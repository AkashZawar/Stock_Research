"""The analysis engine: all market-data fetching and report building.

This is the shared "brain" of the app. It contains no Django request/response
code - views call into it and serialise the returned dicts to JSON. It is a
large module, so locate functions by the area they belong to rather than
reading top to bottom:

- Data providers: Yahoo Finance, SEC, Screener.in, Groww/Upstox, NSE and
  Moneycontrol fetch/parse helpers (e.g. ``get_quote``, ``get_chart``,
  ``get_screener_fundamentals``, ``fetch_nse_json``).
- Technical analysis: indicators and patterns (SMA/EMA, RSI, ATR,
  support/resistance, candlestick and relative-strength helpers).
- Fundamentals & ownership: financials, quarterly results, promoter/FII/DII
  shareholding trends and growth catalysts.
- Stock report: ``resolve_symbol_input`` + ``analyze_symbol`` build the full
  Stock Analysis payload, plus the swing-trade plan and quality checks.
- Asset report: ``analyze_asset`` for ETFs and mutual funds.
- Recommendations: ``get_recommendations`` / ``build_recommendations``.
- Market monitor: ``build_live_market_monitor`` plus NSE/Moneycontrol
  snapshots, sector OI, gainers/losers and heatmaps.
- Caching & concurrency: ``cached`` / ``get_cached`` / ``set_cached`` /
  ``clear_cache`` and the threaded ``settle_map`` / ``settle_named_loaders``.
- Symbol validation: ``INVALID_INSTRUMENT_MESSAGE``,
  ``invalid_instrument_payload`` and ``instrument_suggestions``.

Data comes from public endpoints; replace with a licensed feed for production.
"""
import csv
import html
import json
import math
import os
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from io import StringIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

import certifi


CACHE_TTL_SECONDS = 5 * 60
SEC_CACHE_TTL_SECONDS = 24 * 60 * 60
MARKET_MONITOR_CACHE_SECONDS = 10 * 60
FAST_MARKET_MONITOR_CACHE_SECONDS = 15
NSE_MARKET_SNAPSHOT_CACHE_SECONDS = 60
LIVE_MARKET_MONITOR_CACHE_SECONDS = 1
LIVE_NSE_MARKET_SNAPSHOT_CACHE_SECONDS = 1
RECOMMENDATION_CACHE_SECONDS = 30 * 60
RECOMMENDATION_SCAN_LIMIT = 18
RECOMMENDATION_RESULT_LIMIT = 14
RECOMMENDATION_MIN_UPSIDE_PERCENT = 4
RECOMMENDATION_PROVIDER_CONCURRENCY = 4
DAILY_RECOMMENDATION_SCAN_LIMIT = 40
DAILY_RECOMMENDATION_RESULT_LIMIT = 8
INTRADAY_RECOMMENDATION_SCAN_LIMIT = 36
INTRADAY_RECOMMENDATION_RESULT_LIMIT = 10
INTRADAY_MIN_EXPECTED_MOVE_PERCENT = 3
INTRADAY_MAX_EXPECTED_MOVE_PERCENT = 5
STOCK_REPORT_MAX_SESSIONS = 252
STOCK_ANALYSIS_BUFFER_SESSIONS = 63
STOCK_ANALYSIS_MAX_SESSIONS = STOCK_REPORT_MAX_SESSIONS + STOCK_ANALYSIS_BUFFER_SESSIONS
STOCK_MIN_ANALYSIS_CANDLES = 2
ADVISORKHOJ_ANNUAL_RETURNS_CACHE_SECONDS = 24 * 60 * 60
ADVISORKHOJ_ANNUAL_RETURNS_URL = "https://www.advisorkhoj.com/mutual-funds-research/mutual-fund-annual-returns"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "StockResearchDesk/0.1 contact@example.com")
OWNERSHIP_ROW_NAMES = ("Promoters", "FIIs", "DIIs", "Public")
INVALID_INSTRUMENT_MESSAGE = "Invalid stock/MF name, do you mean anything from below?"
# Used when the input parsed to a real-looking ticker but the provider has no
# data for it. A mistyped ticker ("RELANCE.NS") and one that has expired
# ("TATAMOTORS.NS", post-demerger) are indistinguishable from the outside, so the
# message names the symbol that failed and both possible causes rather than
# guessing at one.
MISSING_INSTRUMENT_DATA_MESSAGE = (
    "{symbol} returned no data from the provider. Check the spelling, or the symbol may "
    "have been delisted, renamed, or demerged. Try one of these instead."
)
# Marks the "instrument is real but has no usable history" case so the views can
# answer 400 with the explanation instead of a bare 500.
INSUFFICIENT_HISTORY_PREFIX = "Not enough price history"
_cache = {}
_cache_lock = threading.Lock()
_market_monitor_refresh_lock = threading.Lock()
_market_monitor_refreshing = False
_nse_session_state = None
_nse_session_lock = threading.Lock()
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# How long an NSE cookie jar is reused before it is rebuilt. Long enough that a
# burst of option-chain calls shares one handshake, short enough that an expired
# jar is not held onto.
NSE_SESSION_TTL_SECONDS = 5 * 60

# The cache is a process-local dict with no eviction, so a long-running server
# that is asked about thousands of symbols would grow without bound. Expired
# entries are dropped first, then the oldest, once this many keys are held.
MAX_CACHE_ENTRIES = 2000

# How long an endpoint that answered 401 is skipped for. Long enough to stop
# spending request budget on it across a browsing session, short enough that
# restored access is picked up without a restart.
UNAUTHORIZED_ENDPOINT_TTL_SECONDS = 15 * 60
_unauthorized_endpoints = {}

MODULES = ",".join([
    "assetProfile",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "price",
    "calendarEvents",
    "earnings",
    "earningsTrend",
    "recommendationTrend",
    "upgradeDowngradeHistory",
])

COMMODITIES = [
    {"symbol": "CL=F", "name": "WTI Crude Oil", "category": "Oil", "unit": "USD/barrel"},
    {"symbol": "BZ=F", "name": "Brent Crude Oil", "category": "Oil", "unit": "USD/barrel"},
    {"symbol": "NG=F", "name": "Natural Gas", "category": "Energy", "unit": "USD/MMBtu"},
    {"symbol": "GC=F", "name": "Gold", "category": "Precious Metals", "unit": "USD/oz"},
    {"symbol": "SI=F", "name": "Silver", "category": "Precious Metals", "unit": "USD/oz"},
    {"symbol": "HG=F", "name": "Copper", "category": "Industrial Metals", "unit": "USD/lb"},
]

NSE_BASE_URL = "https://www.nseindia.com"
NSE_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "User-Agent": "Mozilla/5.0 StockResearchDesk/0.1",
}
NSE_KEY_INDICES = (
    "NIFTY 50",
    "NIFTY NEXT 50",
    "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY MIDCAP 100",
    "NIFTY SMALLCAP 100",
    "INDIA VIX",
)
NSE_SECTOR_INDICES = (
    "NIFTY AUTO",
    "NIFTY IT",
    "NIFTY FMCG",
    "NIFTY PHARMA",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY OIL & GAS",
    "NIFTY HEALTHCARE INDEX",
    "NIFTY CONSUMER DURABLES",
)
NSE_OI_SECTOR_INDICES = (
    "NIFTY BANK",
    "NIFTY FINANCIAL SERVICES",
    "NIFTY FINANCIAL SERVICES EX-BANK",
    "NIFTY MIDSMALL FINANCIAL SERVICES",
    "NIFTY AUTO",
    "NIFTY IT",
    "NIFTY MIDSMALL IT & TELECOM",
    "NIFTY FMCG",
    "NIFTY PHARMA",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY OIL & GAS",
    "NIFTY HEALTHCARE INDEX",
    "NIFTY MIDSMALL HEALTHCARE",
    "NIFTY CONSUMER DURABLES",
    "NIFTY CHEMICALS",
    "NIFTY CEMENT",
    "NIFTY MEDIA",
    "NIFTY ENERGY",
    "NIFTY INFRASTRUCTURE",
)
NIFTY_500_INDEX_NAME = "NIFTY 500"
NIFTY_500_CONSTITUENTS_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
NIFTY_500_PRIMARY_CACHE_SECONDS = 6 * 60 * 60
NIFTY_500_PRIMARY_SCAN_LIMIT = 120
NIFTY_500_CONSTITUENTS_CSV_URL = NIFTY_500_CONSTITUENTS_URL
# These scans are what decide whether the detailed monitor can be built inside a
# single request. At 6/3/2 the three of them took roughly 26s of the ~64s build,
# which no serverless request survives, so the dashboard depended on a background
# thread that a serverless host kills the moment the response is sent. Measured
# against Yahoo's chart endpoint over the full 120/69/25 symbol universes, every
# level up to 32 returned zero failures, and the wall time fell 10.3s -> 3.4s,
# 9.7s -> 1.8s and 6.6s -> 1.8s. These sit below the level that was tested clean,
# to leave headroom for a shared deployment IP that is not the only caller.
MARKET_MONITOR_PRIMARY_CONCURRENCY = 20
MARKET_MONITOR_ACTIVITY_CONCURRENCY = 16
MARKET_MONITOR_CATALYST_CONCURRENCY = 10
# Yahoo's batch quote endpoint answers 401 to anonymous callers, so a batch of 80
# degrades into 80 single chart calls. These two multiply - at most four batches
# in flight, eight symbols each - and the product stays inside the range that
# scanned the full universe without a single failure.
UNIVERSE_QUOTE_BATCH_CONCURRENCY = 4
QUOTE_FALLBACK_CONCURRENCY = 8
NIFTY_50_INDEX_NAME = "NIFTY 50"
NSE_SNAPSHOT_ENDPOINTS = {
    "marketStatus": "/api/marketStatus",
    "allIndices": "/api/allIndices",
    "nifty50": f"/api/equity-stockIndices?index={quote(NIFTY_50_INDEX_NAME)}",
    "gainers": "/api/live-analysis-variations?index=gainers",
    "losers": "/api/live-analysis-variations?index=loosers",
    "mostActive": "/api/live-analysis-most-active-securities?index=volume",
    "weekHighs": "/api/live-analysis-52Week?index=high",
    "priceBands": "/api/live-analysis-price-band-hitter?index=upper",
}
MONEYCONTROL_SECTOR_URL = "https://www.moneycontrol.com/markets/sector-analysis/"
SECTOR_ALIASES = {
    "Technology": "Software & IT Services",
    "Information Technology": "Software & IT Services",
    "Software": "Software & IT Services",
    "Financial Services": "Finance",
    "Financials": "Finance",
    "Banks": "Banks",
    "Banking": "Banks",
    "Consumer Defensive": "FMCG",
    "Consumer Staples": "FMCG",
    "Healthcare": "Healthcare",
    "Health Care": "Healthcare",
    "Industrials": "Capital Goods",
    "Capital Goods": "Capital Goods",
    "Basic Materials": "Metals & Mining",
    "Materials": "Metals & Mining",
    "Energy": "Oil & Gas",
    "Oil": "Oil & Gas",
    "Oil & Gas": "Oil & Gas",
    "Utilities": "Power",
    "Power": "Power",
    "Real Estate": "Real Estate",
    "Communication Services": "Telecom",
    "Telecom": "Telecom",
    "Consumer Cyclical": "Automobile & Ancillaries",
    "Auto": "Automobile & Ancillaries",
    "Automobile": "Automobile & Ancillaries",
}
MONEYCONTROL_NSE_SECTOR_MAP = {
    "Automobile & Ancillaries": "NIFTY AUTO",
    "Auto": "NIFTY AUTO",
    "Banks": "NIFTY BANK",
    "Banking": "NIFTY BANK",
    "Capital Goods": "NIFTY INFRASTRUCTURE",
    "Cement": "NIFTY CEMENT",
    "Chemicals": "NIFTY CHEMICALS",
    "Consumer Durables": "NIFTY CONSUMER DURABLES",
    "Finance": "NIFTY FINANCIAL SERVICES",
    "Financial Services": "NIFTY FINANCIAL SERVICES",
    "FMCG": "NIFTY FMCG",
    "Healthcare": "NIFTY HEALTHCARE INDEX",
    "Media": "NIFTY MEDIA",
    "Metals & Mining": "NIFTY METAL",
    "Oil & Gas": "NIFTY OIL & GAS",
    "Power": "NIFTY ENERGY",
    "Real Estate": "NIFTY REALTY",
    "Realty": "NIFTY REALTY",
    "Software & IT Services": "NIFTY IT",
    "Technology": "NIFTY IT",
    "Telecom": "NIFTY MIDSMALL IT & TELECOM",
}

# Holiday calendars are fetched, never written down here. A hardcoded table is
# wrong twice over: it knows nothing about the next year, and it cannot learn
# about holidays declared mid-year. The table this replaced was already missing
# 15-Jan-2026, an election holiday NSE announced after the fact, so the clock
# called a closed Thursday a trading day.
NSE_HOLIDAY_PATH = "/api/holiday-master?type=trading"

# Cash-market segment. NSE keys its holiday master by segment and "CM" is the
# equities one; FO and CD keep their own, occasionally differing, calendars.
NSE_HOLIDAY_SEGMENT = "CM"

# Index history is used to infer holidays when no calendar is reachable, and it
# needs corroboration: Yahoo's index series have occasional single-day holes
# that look exactly like a holiday. 28-Aug-2026 is missing from both ^NSEI and
# ^BSESN yet RELIANCE.NS traded that day, so an index pair alone is not enough -
# a liquid stock is the tie-breaker.
HOLIDAY_PROBE_SYMBOLS = {
    "india": ("^NSEI", "^BSESN", "RELIANCE.NS"),
    "us": ("^GSPC", "^DJI", "AAPL"),
}

HOLIDAY_CACHE_SECONDS = 12 * 60 * 60

MARKET_CLOCK_CONFIGS = {
    "india": {
        "market": "india",
        "label": "NSE/BSE India",
        "timezone": "Asia/Kolkata",
        "timezoneLabel": "IST",
        "regularOpen": datetime_time(9, 15),
        "regularClose": datetime_time(15, 30),
        "weekdays": {0, 1, 2, 3, 4},
        "holidayMarket": "india",
        "source": "NSE India cash-market calendar and live market status",
    },
    "us": {
        "market": "us",
        "label": "US equities",
        "timezone": "America/New_York",
        "timezoneLabel": "ET",
        "regularOpen": datetime_time(9, 30),
        "regularClose": datetime_time(16, 0),
        "weekdays": {0, 1, 2, 3, 4},
        "holidayMarket": "us",
        # Half-day closes are deliberately absent. The only way to state them
        # was a hardcoded pair of dates that expires with the year, and showing
        # the regular close on a half-day is a smaller error than asserting a
        # half-day on a date that has moved.
        "source": "NYSE/Nasdaq regular-session calendar",
    },
    "generic": {
        "market": "generic",
        "label": "Exchange session",
        "timezone": "UTC",
        "timezoneLabel": "UTC",
        "regularOpen": datetime_time(9, 30),
        "regularClose": datetime_time(16, 0),
        "weekdays": {0, 1, 2, 3, 4},
        # An unrecognised exchange has no calendar we could name, so weekends
        # are the only closures claimed.
        "holidayMarket": None,
        "source": "Quote-provider exchange timezone with regular-session fallback",
    },
}

BREAKOUT_WATCHLIST = [
    ("RELIANCE.NS", "Reliance Industries", ["Oil", "Petrochemicals"]),
    ("ONGC.NS", "ONGC", ["Oil"]),
    ("OIL.NS", "Oil India", ["Oil"]),
    ("IOC.NS", "Indian Oil", ["Oil"]),
    ("BPCL.NS", "BPCL", ["Oil"]),
    # Hindustan Petroleum trades as HINDPETRO on the NSE. HPCL.NS resolves to an
    # unrelated mutual-fund stub last priced in 2019, which answers 200 with the
    # symbol echoed back and every price field null - so it read as a working
    # ticker that simply had nothing to report.
    ("HINDPETRO.NS", "HPCL", ["Oil"]),
    ("INDIGO.NS", "InterGlobe Aviation", ["Oil", "Aviation"]),
    ("ASIANPAINT.NS", "Asian Paints", ["Oil", "Chemicals"]),
    ("BERGEPAINT.NS", "Berger Paints", ["Oil", "Chemicals"]),
    ("TATASTEEL.NS", "Tata Steel", ["Metals"]),
    ("JSWSTEEL.NS", "JSW Steel", ["Metals"]),
    ("HINDALCO.NS", "Hindalco", ["Metals", "Copper"]),
    ("VEDL.NS", "Vedanta", ["Metals", "Oil"]),
    ("HINDCOPPER.NS", "Hindustan Copper", ["Copper"]),
    ("COALINDIA.NS", "Coal India", ["Energy"]),
    ("TITAN.NS", "Titan", ["Gold"]),
    ("KALYANKJIL.NS", "Kalyan Jewellers", ["Gold"]),
    ("MUTHOOTFIN.NS", "Muthoot Finance", ["Gold"]),
    ("MANAPPURAM.NS", "Manappuram Finance", ["Gold"]),
    ("TCS.NS", "TCS", ["Index"]),
    ("INFY.NS", "Infosys", ["Index"]),
    ("HDFCBANK.NS", "HDFC Bank", ["Index"]),
    ("ICICIBANK.NS", "ICICI Bank", ["Index"]),
    ("LT.NS", "Larsen & Toubro", ["Index"]),
    ("SBIN.NS", "State Bank of India", ["Index"]),
    ("BHARTIARTL.NS", "Bharti Airtel", ["Index"]),
    ("SUNPHARMA.NS", "Sun Pharma", ["Index"]),
    ("CIPLA.NS", "Cipla", ["Index"]),
    ("ABBOTINDIA.NS", "Abbott India", ["Pharma"]),
    # Tata Motors demerged into a commercial-vehicle and a passenger-vehicle
    # company, and TATAMOTORS.NS now 404s. Both successors buy the same steel and
    # aluminium, so both belong under Metals where the single parent used to sit.
    ("TMCV.NS", "Tata Motors (CV)", ["Metals", "Auto"]),
    ("TMPV.NS", "Tata Motors Passenger Vehicles", ["Metals", "Auto"]),
    ("MARUTI.NS", "Maruti Suzuki", ["Metals", "Auto"]),
    ("ULTRACEMCO.NS", "UltraTech Cement", ["Energy", "Cement"]),
]
BREAKOUT_WATCHLIST = [
    {"symbol": symbol, "name": name, "tags": tags}
    for symbol, name, tags in BREAKOUT_WATCHLIST
]

HIGH_ACTIVITY_WATCHLIST = [
    ("NUVAMA.NS", "Nuvama Wealth Management", ["Financials", "Volume"]),
    ("BSE.NS", "BSE", ["Exchange", "Volume"]),
    ("CDSL.NS", "CDSL", ["Exchange", "Volume"]),
    ("CAMS.NS", "CAMS", ["Financials", "Volume"]),
    ("MCX.NS", "MCX", ["Exchange", "Volume"]),
    ("BEL.NS", "Bharat Electronics", ["Defence", "Orders"]),
    ("BHEL.NS", "BHEL", ["Capital Goods", "Orders"]),
    ("HAL.NS", "Hindustan Aeronautics", ["Defence", "Orders"]),
    ("BDL.NS", "Bharat Dynamics", ["Defence", "Orders"]),
    ("COCHINSHIP.NS", "Cochin Shipyard", ["Shipbuilding", "Orders"]),
    ("MAZDOCK.NS", "Mazagon Dock Shipbuilders", ["Shipbuilding", "Orders"]),
    ("GRSE.NS", "Garden Reach Shipbuilders", ["Shipbuilding", "Orders"]),
    ("RVNL.NS", "RVNL", ["Railways", "Orders"]),
    ("IRFC.NS", "IRFC", ["Railways", "Volume"]),
    ("IRCON.NS", "IRCON International", ["Railways", "Orders"]),
    ("RITES.NS", "RITES", ["Railways", "Orders"]),
    ("RAILTEL.NS", "RailTel", ["Railways", "Orders"]),
    ("TITAGARH.NS", "Titagarh Rail Systems", ["Railways", "Orders"]),
    ("NBCC.NS", "NBCC", ["Construction", "Orders"]),
    ("KEC.NS", "KEC International", ["Infrastructure", "Orders"]),
    ("KPIL.NS", "Kalpataru Projects", ["Infrastructure", "Orders"]),
    ("KAYNES.NS", "Kaynes Technology", ["Electronics", "Orders"]),
    ("DIXON.NS", "Dixon Technologies", ["Electronics", "Orders"]),
    ("PARAS.NS", "Paras Defence", ["Defence", "Orders"]),
    ("ASTRAMICRO.NS", "Astra Microwave", ["Defence", "Orders"]),
    ("SIEMENS.NS", "Siemens", ["Capital Goods", "Orders"]),
    ("ABB.NS", "ABB India", ["Capital Goods", "Orders"]),
    ("CGPOWER.NS", "CG Power", ["Capital Goods", "Volume"]),
    ("PFC.NS", "Power Finance Corp", ["Power", "Volume"]),
    ("RECLTD.NS", "REC", ["Power", "Volume"]),
    ("IREDA.NS", "IREDA", ["Renewables", "Volume"]),
    ("ADANIPOWER.NS", "Adani Power", ["Power", "Volume"]),
    ("TATAPOWER.NS", "Tata Power", ["Power", "Orders"]),
    ("JSWENERGY.NS", "JSW Energy", ["Power", "Volume"]),
    ("NHPC.NS", "NHPC", ["Power", "Orders"]),
    ("SJVN.NS", "SJVN", ["Power", "Orders"]),
]
HIGH_ACTIVITY_WATCHLIST = [
    {"symbol": symbol, "name": name, "tags": tags}
    for symbol, name, tags in HIGH_ACTIVITY_WATCHLIST
]

ORDER_CATALYST_WATCHLIST = [
    stock for stock in HIGH_ACTIVITY_WATCHLIST
    if "Orders" in stock["tags"] or "Defence" in stock["tags"] or "Railways" in stock["tags"]
]

# Trading days in a year, used to annualise returns, volatility, and to size the
# rolling-return window.
TRADING_SESSIONS_PER_YEAR = 252
WEEKS_PER_YEAR = 52

# ETF and mutual fund history window. Two years was too short to build
# rolling one-year returns (which need more than a year of history before the
# first window even closes) or to say anything about a full market cycle. Ten
# years spans the 2020 drawdown, so the worst-case figures mean something.
ASSET_HISTORY_RANGE = "10y"

# Support/resistance detection still looks at the recent two years. Feeding it a
# decade of candles would surface levels from a completely different price regime.
ASSET_LEVEL_WINDOW_SESSIONS = 504

# A one-session move beyond this is read as an unadjusted split or bonus rather
# than a price move, and history before it is discarded for return maths.
MAX_SESSION_MOVE_PERCENT = 40.0

# Fund risk is conventionally quoted on trailing three years, so volatility,
# Sharpe, and Sortino use that window even though more history is on file.
ASSET_RISK_WINDOW_SESSIONS = 756

# Standing risk-free assumptions by currency, used for Sharpe and Sortino. These
# are not live quotes - they are stable reference levels so the ratios are
# comparable across funds within a currency, and every payload states the rate it
# used so the reader can adjust.
ASSET_RISK_FREE_RATES = {
    "INR": 6.5,
    "USD": 4.3,
    "EUR": 2.5,
    "GBP": 4.0,
    "JPY": 1.0,
}
DEFAULT_RISK_FREE_RATE = 4.0

# Long-run gross dividend yields for the reference indices, used to put the
# benchmark on the same footing as the fund.
#
# This closes a bias that made every equity fund look better than it was. A
# Growth-plan NAV accumulates dividends, and an accumulating ETF's price does the
# same, so the fund side of the comparison is a total return. The reference
# series (^NSEI, ^GSPC and the rest) are price indices that drop dividends on the
# floor. Comparing the two showed NIFTYBEES - a plain Nifty 50 tracker charging
# a fee - beating its own index by 1.27pp a year, which is impossible: the gap
# was the Nifty's own 1.3% dividend yield.
#
# These are stable long-run averages, not live figures, and every payload states
# the number it used so the reader can adjust it.
INDEX_DIVIDEND_YIELDS = {
    "^NSEI": 1.3,
    "^BSESN": 1.2,
    "^GSPC": 1.6,
    "^IXIC": 0.8,
    "^DJI": 1.9,
    "^FTSE": 3.6,
    "^N225": 2.0,
}
DEFAULT_INDEX_DIVIDEND_YIELD = 1.5

# When a fund's name or tags contain one of these markers, the mapped market
# reference is genuinely the index it tracks, so dispersion against it is
# tracking error rather than active risk.
ASSET_BENCHMARK_TRACKING_MARKERS = {
    "^NSEI": ("nifty 50", "nifty50", "nifty index"),
    "^GSPC": ("s&p 500", "sp 500", "500 index"),
}
# Names that contain a tracking marker but follow a different index.
ASSET_BENCHMARK_TRACKING_EXCLUSIONS = ("next 50", "junior", "bank", "midcap", "mid cap", "smallcap", "small cap")

ETF_UNIVERSE = [
    ("NIFTYBEES.NS", "Nippon India ETF Nifty 50 BeES", "NSE", ["India", "Nifty 50", "Large cap"]),
    ("JUNIORBEES.NS", "Nippon India ETF Junior BeES", "NSE", ["India", "Nifty Next 50"]),
    ("BANKBEES.NS", "Nippon India ETF Bank BeES", "NSE", ["India", "Banking"]),
    ("GOLDBEES.NS", "Nippon India ETF Gold BeES", "NSE", ["India", "Gold"]),
    ("ITBEES.NS", "Nippon India ETF IT BeES", "NSE", ["India", "Information technology"]),
    ("SETFNIF50.NS", "SBI ETF Nifty 50", "NSE", ["India", "Nifty 50"]),
    ("SETFNIFBK.NS", "SBI ETF Nifty Bank", "NSE", ["India", "Banking"]),
    # ICICINIFTY.NS was delisted and 404s on the data provider. Its live
    # replacement (NIFTYIETF.NS) and AXISNIFTY.NS both return only a handful of
    # daily candles, so ICICI is represented here by its Nifty 100 ETF instead -
    # every symbol in this list is verified to return a usable 2y history.
    ("HDFCNIFTY.NS", "HDFC Nifty 50 ETF", "NSE", ["India", "Nifty 50", "Large cap"]),
    ("IVZINNIFTY.NS", "Invesco India Nifty 50 ETF", "NSE", ["India", "Nifty 50", "Large cap"]),
    ("NIF100IETF.NS", "ICICI Prudential Nifty 100 ETF", "NSE", ["India", "Nifty 100", "Large cap"]),
    ("MID150BEES.NS", "Nippon India ETF Nifty Midcap 150", "NSE", ["India", "Midcap", "Nifty Midcap 150"]),
    ("MOM100.NS", "Motilal Oswal Nifty Midcap 100 ETF", "NSE", ["India", "Midcap"]),
    ("MOM50.NS", "Motilal Oswal M50 ETF", "NSE", ["India", "Nifty 50"]),
    ("SILVERBEES.NS", "Nippon India Silver ETF", "NSE", ["India", "Silver", "Commodity"]),
    ("SETFGOLD.NS", "SBI Gold ETF", "NSE", ["India", "Gold", "Commodity"]),
    ("HDFCGOLD.NS", "HDFC Gold ETF", "NSE", ["India", "Gold", "Commodity"]),
    ("CPSEETF.NS", "CPSE ETF", "NSE", ["India", "PSU", "Thematic"]),
    ("ICICIB22.NS", "Bharat 22 ETF", "NSE", ["India", "PSU", "Thematic"]),
    ("PSUBNKBEES.NS", "Nippon India ETF Nifty PSU Bank BeES", "NSE", ["India", "Banking", "PSU"]),
    ("AUTOBEES.NS", "Nippon India Nifty Auto ETF", "NSE", ["India", "Auto", "Sectoral"]),
    ("PHARMABEES.NS", "Nippon India Nifty Pharma ETF", "NSE", ["India", "Pharma", "Sectoral"]),
    ("LIQUIDBEES.NS", "Nippon India ETF Nifty 1D Rate Liquid BeES", "NSE", ["India", "Liquid", "Cash"]),
    ("MON100.NS", "Motilal Oswal Nasdaq 100 ETF", "NSE", ["India", "Nasdaq 100", "Global", "Technology"]),
    ("MAFANG.NS", "Mirae Asset NYSE FANG+ ETF", "NSE", ["India", "Global", "Technology"]),
    ("HNGSNGBEES.NS", "Nippon India ETF Hang Seng BeES", "NSE", ["India", "Global", "Hong Kong"]),
    ("SPY", "SPDR S&P 500 ETF Trust", "NYSE Arca", ["US", "S&P 500", "Large cap"]),
    ("QQQ", "Invesco QQQ Trust", "Nasdaq", ["US", "Nasdaq 100", "Growth"]),
    ("VOO", "Vanguard S&P 500 ETF", "NYSE Arca", ["US", "S&P 500", "Large cap"]),
    ("VTI", "Vanguard Total Stock Market ETF", "NYSE Arca", ["US", "Total market"]),
    ("GLD", "SPDR Gold Shares", "NYSE Arca", ["Gold", "Commodity"]),
]

MUTUAL_FUND_UNIVERSE = [
    # Indian schemes are keyed by opaque Yahoo ids, so searching "parag parikh"
    # only worked when Yahoo's own search happened to answer. Seeding the
    # popular Direct Growth plans keeps the picker useful and deterministic.
    ("0P0000YWL1.BO", "Parag Parikh Flexi Cap Fund Direct Growth", "BSE", ["India", "Flexi cap", "Equity"]),
    ("0P0000XW77.BO", "HDFC Flexi Cap Fund Direct Growth", "BSE", ["India", "Flexi cap", "Equity"]),
    ("0P0000XVA0.BO", "Mirae Asset Large Cap Fund Direct Growth", "BSE", ["India", "Large cap", "Equity"]),
    ("0P0000XVG6.BO", "Nippon India Large Cap Fund Direct Growth", "BSE", ["India", "Large cap", "Equity"]),
    ("0P00012ALS.BO", "Motilal Oswal Midcap Fund Direct Growth", "BSE", ["India", "Mid cap", "Equity"]),
    ("0P0000XW1B.BO", "SBI Small Cap Fund Direct Growth", "BSE", ["India", "Small cap", "Equity"]),
    ("0P0000XVFY.BO", "Nippon India Small Cap Fund Direct Growth", "BSE", ["India", "Small cap", "Equity"]),
    ("0P0000XW4J.BO", "Quant Small Cap Fund Direct Growth", "BSE", ["India", "Small cap", "Equity"]),
    ("0P0000XVU2.BO", "UTI Nifty 50 Index Fund Direct Growth", "BSE", ["India", "Index fund", "Nifty 50"]),
    ("VFIAX", "Vanguard 500 Index Fund Admiral Shares", "Nasdaq", ["US", "S&P 500", "Index fund"]),
    ("FXAIX", "Fidelity 500 Index Fund", "Nasdaq", ["US", "S&P 500", "Index fund"]),
    ("SWPPX", "Schwab S&P 500 Index Fund", "Nasdaq", ["US", "S&P 500", "Index fund"]),
    ("VTSAX", "Vanguard Total Stock Market Index Fund Admiral Shares", "Nasdaq", ["US", "Total market"]),
    ("VBTLX", "Vanguard Total Bond Market Index Fund Admiral Shares", "Nasdaq", ["US", "Bond fund"]),
    ("VBIAX", "Vanguard Balanced Index Fund Admiral Shares", "Nasdaq", ["US", "Balanced fund"]),
]

ASSET_TYPE_CONFIG = {
    "etf": {
        "label": "ETF",
        "quoteTypes": {"ETF"},
        "universe": ETF_UNIVERSE,
    },
    "mutual-fund": {
        "label": "Mutual Fund",
        "quoteTypes": {"MUTUALFUND", "MUTUAL_FUND"},
        "universe": MUTUAL_FUND_UNIVERSE,
    },
}

ORDER_CATALYST_KEYWORDS = [
    "order",
    "orders",
    "contract",
    "contracts",
    "project",
    "projects",
    "tender",
    "letter of award",
    "work order",
    "loa",
    "bagged",
    "bags",
    "wins",
    "won",
    "award",
    "awarded",
    "supply",
    "procurement",
    "epc",
]

UPSIDE_CATALYST_KEYWORDS = [
    "target",
    "upgrade",
    "buy",
    "upside",
    "rally",
    "surge",
    "jumps",
    "gains",
    "record high",
]

GROWTH_CATALYST_KEYWORDS = [
    *ORDER_CATALYST_KEYWORDS,
    "budget",
    "allocation",
    "allocated",
    "government",
    "govt",
    "ministry",
    "cabinet",
    "policy",
    "capex",
    "capital expenditure",
    "procurement",
    "production-linked incentive",
    "pli",
    "subsidy",
    "defence",
    "defense",
    "railway",
    "railways",
    "renewable",
    "solar",
    "transmission",
    "infrastructure",
]

COMMODITY_IMPACT_GROUPS = [
    {
        "commodity": "Oil",
        "whenUp": "May benefit upstream producers, but can pressure fuel users, paints, chemicals, and airlines.",
        "whenDown": "May ease input-cost pressure for airlines, paints, chemicals, and OMC margins.",
        "beneficiariesWhenUp": ["ONGC.NS", "OIL.NS", "VEDL.NS"],
        "pressuredWhenUp": ["INDIGO.NS", "ASIANPAINT.NS", "BERGEPAINT.NS", "IOC.NS", "BPCL.NS", "HINDPETRO.NS"],
    },
    {
        "commodity": "Industrial Metals",
        "whenUp": "May support metal producers and indicate stronger industrial demand.",
        "whenDown": "May pressure metal producers and help metal-consuming manufacturers.",
        "beneficiariesWhenUp": ["HINDALCO.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "VEDL.NS", "HINDCOPPER.NS"],
        "pressuredWhenUp": ["LT.NS", "TMCV.NS", "TMPV.NS", "MARUTI.NS"],
    },
    {
        "commodity": "Precious Metals",
        "whenUp": "Can lift attention on gold lenders and jewellers, while high prices may affect jewellery demand.",
        "whenDown": "Can ease jewellery inventory cost but may pressure gold-lender collateral sentiment.",
        "beneficiariesWhenUp": ["MUTHOOTFIN.NS", "MANAPPURAM.NS", "KALYANKJIL.NS"],
        "pressuredWhenUp": ["TITAN.NS"],
    },
    {
        "commodity": "Energy",
        "whenUp": "Can pressure energy-intensive industries and support energy producers.",
        "whenDown": "Can ease cost pressure for power-intensive manufacturers.",
        "beneficiariesWhenUp": ["ONGC.NS", "OIL.NS", "COALINDIA.NS"],
        "pressuredWhenUp": ["TATASTEEL.NS", "HINDALCO.NS", "ULTRACEMCO.NS"],
    },
]

RECOMMENDATION_FALLBACK_UNIVERSE = (
    ("RELIANCE.NS", "Reliance Industries", ("Large cap", "Index leader")),
    ("TCS.NS", "Tata Consultancy Services", ("Large cap", "IT")),
    ("INFY.NS", "Infosys", ("Large cap", "IT")),
    ("HDFCBANK.NS", "HDFC Bank", ("Large cap", "Banking")),
    ("ICICIBANK.NS", "ICICI Bank", ("Large cap", "Banking")),
    ("LT.NS", "Larsen & Toubro", ("Infrastructure", "Capital goods")),
    ("SBIN.NS", "State Bank of India", ("Banking", "PSU")),
    ("BHARTIARTL.NS", "Bharti Airtel", ("Telecom", "Large cap")),
    ("AXISBANK.NS", "Axis Bank", ("Banking", "Large cap")),
    ("MARUTI.NS", "Maruti Suzuki India", ("Auto", "Large cap")),
    ("SUNPHARMA.NS", "Sun Pharmaceutical", ("Pharma", "Large cap")),
    ("HAL.NS", "Hindustan Aeronautics", ("Defence", "PSU")),
)

BULLISH_RECOMMENDATION_KEYS = {"buy", "strong_buy", "strongbuy", "outperform", "overweight", "add", "accumulate"}
RECOMMENDATION_GROUP_LABELS = {
    "FIIs": "Foreign institutional investor group",
    "DIIs": "Domestic institutional investor group",
}
RECOMMENDATION_GROUP_DETAILS = {
    "FIIs": "Foreign portfolio investors, overseas institutions, and related public shareholding categories.",
    "DIIs": "Domestic institutions such as mutual funds, insurers, banks, and other Indian institutional holders.",
}


def analyze_symbol(symbol):
    return cached(f"analysis:{symbol}", lambda: _analyze_symbol(symbol), CACHE_TTL_SECONDS)


def analyze_asset(symbol, asset_type):
    normalized_asset_type = normalize_asset_type(asset_type)
    return cached(
        f"asset-analysis:{normalized_asset_type}:{symbol}",
        lambda: _analyze_asset(symbol, normalized_asset_type),
        CACHE_TTL_SECONDS,
    )


def _analyze_symbol(symbol):
    benchmark_symbol = benchmark_symbol_for(symbol)
    loaders = {
        "chart": lambda: get_chart(symbol),
        "quote": lambda: get_quote(symbol),
        "summary": lambda: get_summary(symbol),
        "sec": lambda: get_sec_fundamentals(symbol),
        "screener": lambda: get_screener_fundamentals(symbol),
        "openInterest": lambda: get_stock_open_interest(symbol),
    }
    if benchmark_symbol:
        loaders["benchmark"] = lambda: get_benchmark_chart(benchmark_symbol)

    results = {}
    with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
        futures = {executor.submit(loader): key for key, loader in loaders.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = (True, future.result())
            except Exception as error:
                results[key] = (False, error)

    chart_ok, chart = results.get("chart", (False, RuntimeError("Could not load chart data.")))
    if not chart_ok:
        raise RuntimeError(str(chart))

    candles = chart["candles"]
    if len(candles) < STOCK_MIN_ANALYSIS_CANDLES:
        raise RuntimeError("Not enough daily data was returned for this symbol.")

    quote_data = results.get("quote", (False, {}))[1] if results.get("quote", (False,))[0] else {}
    summary = results.get("summary", (False, {}))[1] if results.get("summary", (False,))[0] else {}
    sec = results.get("sec", (False, {}))[1] if results.get("sec", (False,))[0] else {}
    screener = results.get("screener", (False, {}))[1] if results.get("screener", (False,))[0] else {}
    benchmark = results.get("benchmark", (False, {}))[1] if results.get("benchmark", (False,))[0] else {}
    open_interest = (
        results.get("openInterest", (False, {}))[1]
        if results.get("openInterest", (False,))[0]
        else open_interest_unavailable(symbol, str(results.get("openInterest", (False, ""))[1]))
    )
    return build_report(symbol, chart.get("meta", {}), candles, quote_data, summary, sec, screener, benchmark, open_interest)


def _analyze_asset(symbol, asset_type):
    benchmark_symbol = benchmark_symbol_for(symbol)
    loaders = {
        "chart": lambda: get_chart_range(symbol, ASSET_HISTORY_RANGE, "1d"),
        "quote": lambda: get_quote(symbol),
        "summary": lambda: get_asset_summary(symbol),
    }
    if benchmark_symbol:
        loaders["benchmark"] = lambda: get_asset_benchmark_chart(benchmark_symbol)
    results = {}
    with ThreadPoolExecutor(max_workers=len(loaders)) as executor:
        futures = {executor.submit(loader): key for key, loader in loaders.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = (True, future.result())
            except Exception as error:
                results[key] = (False, error)

    chart_ok, chart = results.get("chart", (False, RuntimeError("Could not load chart data.")))
    if not chart_ok:
        raise RuntimeError(str(chart))

    candles = chart.get("candles") or []
    if len(candles) < 30:
        # The instrument exists but the provider only has a stub history, so no
        # level, return, or risk figure below would mean anything.
        raise RuntimeError(
            f"{INSUFFICIENT_HISTORY_PREFIX}: the data provider returned only "
            f"{len(candles)} daily rows for {symbol}, so it cannot be analysed. "
            "Try a more actively traded ETF or fund."
        )

    quote_data = results.get("quote", (False, {}))[1] if results.get("quote", (False,))[0] else {}
    summary = results.get("summary", (False, {}))[1] if results.get("summary", (False,))[0] else {}
    benchmark = results.get("benchmark", (False, {}))[1] if results.get("benchmark", (False,))[0] else {}
    return build_asset_report(symbol, asset_type, chart.get("meta", {}), candles, quote_data, summary, benchmark)


def build_nse_market_snapshot():
    payloads = {}
    endpoint_errors = {}
    with ThreadPoolExecutor(max_workers=min(6, len(NSE_SNAPSHOT_ENDPOINTS))) as executor:
        futures = {
            executor.submit(fetch_nse_json, path): key
            for key, path in NSE_SNAPSHOT_ENDPOINTS.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                payloads[key] = future.result()
            except Exception as error:
                endpoint_errors[key] = str(error)

    if not payloads:
        raise RuntimeError("NSE India market snapshot is temporarily unavailable.")

    snapshot = build_nse_market_snapshot_from_payloads(payloads, endpoint_errors=endpoint_errors)
    snapshot["endpointErrors"] = endpoint_errors
    if endpoint_errors:
        snapshot["note"] = "Some NSE snapshot blocks could not be refreshed; visible data uses the endpoints that responded."
    return snapshot


def safe_nse_market_snapshot(cache_seconds=NSE_MARKET_SNAPSHOT_CACHE_SECONDS, cache_key="nse-market-snapshot"):
    try:
        return cached(
            cache_key,
            build_nse_market_snapshot,
            cache_seconds,
        )
    except Exception as error:
        return {
            "available": False,
            "source": "NSE India public market APIs",
            "generatedAt": iso_now(),
            "error": str(error),
            "marketStatus": [],
            "indices": [],
            "sectorIndices": [],
            "topGainers": [],
            "topLosers": [],
            "topGainersNote": "NIFTY 50 gainer feed is temporarily unavailable.",
            "topLosersNote": "NIFTY 50 loser feed is temporarily unavailable.",
            "mostActive": [],
            "weekHighs": [],
            "priceBands": {"rows": [], "count": []},
        }


def build_nse_market_snapshot_from_payloads(payloads, endpoint_errors=None):
    endpoint_errors = endpoint_errors or {}
    market_status_payload = payloads.get("marketStatus") or {}
    all_indices_payload = payloads.get("allIndices") or {}
    market_status = normalize_nse_market_status(market_status_payload)
    market_cap = normalize_nse_market_cap(market_status_payload)
    gift_nifty = normalize_nse_gift_nifty(market_status_payload)
    breadth = normalize_nse_breadth(all_indices_payload)
    indices = normalize_nse_indices(all_indices_payload, NSE_KEY_INDICES)
    sector_indices = normalize_nse_sector_indices(all_indices_payload)
    top_gainers = normalize_nse_nifty_movers(payloads, "gainers", limit=10)
    top_losers = normalize_nse_nifty_movers(payloads, "losers", limit=10)

    return {
        "available": True,
        "source": "NSE India public market APIs",
        "generatedAt": iso_now(),
        "timestamp": nse_text(all_indices_payload.get("timestamp"))
            or nse_first_timestamp(market_status),
        "marketStatus": market_status,
        "marketCap": market_cap,
        "giftNifty": gift_nifty,
        "breadth": breadth,
        "indices": indices,
        "sectorIndices": sector_indices,
        "topGainers": top_gainers,
        "topLosers": top_losers,
        "topGainersNote": nse_nifty_mover_note(top_gainers, "gainers", endpoint_errors),
        "topLosersNote": nse_nifty_mover_note(top_losers, "losers", endpoint_errors),
        "mostActive": normalize_nse_most_active(payloads.get("mostActive"), limit=8),
        "weekHighs": normalize_nse_52_week_highs(payloads.get("weekHighs"), limit=8),
        "priceBands": normalize_nse_price_bands(payloads.get("priceBands"), limit=8),
        "note": "Live snapshot pulled from NSE India public website endpoints.",
    }


def normalize_nse_market_status(payload):
    rows = []
    for item in (payload.get("marketState") or []):
        market = nse_text(item.get("market"))
        if not market:
            continue
        rows.append({
            "market": market,
            "status": nse_text(item.get("marketStatus")),
            "message": nse_text(item.get("marketStatusMessage")),
            "tradeDate": nse_text(item.get("tradeDateFormatted"))
                or nse_text(item.get("updated_time"))
                or nse_text(item.get("tradeDate")),
            "index": nse_text(item.get("index")) or nse_text(item.get("underlying")),
            "last": nse_round(item.get("last")),
            "change": nse_round(item.get("variation")),
            "changePercent": nse_round(item.get("percentChange")),
            "expiryDate": nse_text(item.get("expiryDate")),
        })
    return rows


def normalize_nse_market_cap(payload):
    market_cap = payload.get("marketcap") or {}
    return {
        "timestamp": nse_text(market_cap.get("timeStamp")),
        "trillionDollars": nse_round(market_cap.get("marketCapinTRDollars")),
        "lakhCroreRupees": nse_round(market_cap.get("marketCapinLACCRRupees")),
        "croreRupees": nse_round(market_cap.get("marketCapinCRRupees")),
        "croreRupeesFormatted": nse_text(market_cap.get("marketCapinCRRupeesFormatted")),
    }


def normalize_nse_gift_nifty(payload):
    gift = payload.get("giftnifty") or {}
    return {
        "symbol": nse_text(gift.get("SYMBOL")),
        "expiryDate": nse_text(gift.get("EXPIRYDATE")),
        "last": nse_round(gift.get("LASTPRICE")),
        "change": nse_round(gift.get("DAYCHANGE")),
        "changePercent": nse_round(gift.get("PERCHANGE")),
        "contractsTraded": nse_int(gift.get("CONTRACTSTRADED")),
        "timestamp": nse_text(gift.get("TIMESTMP")),
    }


def normalize_nse_breadth(payload):
    advances = nse_int(payload.get("advances")) or 0
    declines = nse_int(payload.get("declines")) or 0
    unchanged = nse_int(payload.get("unchanged")) or 0
    total = advances + declines + unchanged
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "advanceDeclineRatio": round(advances / declines, 2) if declines else None,
        "advancePercent": round(advances / total * 100, 2) if total else None,
        "declinePercent": round(declines / total * 100, 2) if total else None,
    }


def normalize_nse_indices(payload, preferred_names):
    rows = payload.get("data") or []
    by_name = {
        nse_text(item.get("index")).upper(): item
        for item in rows
        if nse_text(item.get("index"))
    }
    normalized = []
    for name in preferred_names:
        item = by_name.get(name.upper())
        if item:
            normalized.append(normalize_nse_index(item))
    return normalized


def normalize_nse_sector_indices(payload):
    preferred = normalize_nse_indices(payload, NSE_SECTOR_INDICES)
    if preferred:
        return preferred
    sector_rows = [
        normalize_nse_index(item)
        for item in payload.get("data", [])
        if nse_text(item.get("key")).upper() == "SECTORAL INDICES"
    ]
    return [item for item in sector_rows if item.get("name")][:12]


def normalize_nse_index(item):
    return {
        "name": nse_text(item.get("index")),
        "symbol": nse_text(item.get("indexSymbol")),
        "group": nse_text(item.get("key")),
        "last": nse_round(item.get("last")),
        "change": nse_round(item.get("variation")),
        "changePercent": nse_round(item.get("percentChange")),
        "open": nse_round(item.get("open")),
        "high": nse_round(item.get("high")),
        "low": nse_round(item.get("low")),
        "previousClose": nse_round(item.get("previousClose")),
        "yearHigh": nse_round(item.get("yearHigh")),
        "yearLow": nse_round(item.get("yearLow")),
        "pe": nse_round(item.get("pe")),
        "pb": nse_round(item.get("pb")),
        "dividendYield": nse_round(item.get("dy")),
        "advances": nse_int(item.get("advances")),
        "declines": nse_int(item.get("declines")),
        "unchanged": nse_int(item.get("unchanged")),
        "oneMonthChange": nse_round(item.get("perChange30d")),
        "oneYearChange": nse_round(item.get("perChange365d")),
    }


def normalize_nse_variation(payload, limit=8, group_key="allSec", direction=None):
    rows = nse_group_data(payload, group_key)
    normalized = []
    for item in rows:
        symbol = nse_text(item.get("symbol")).upper()
        if not symbol:
            continue
        corporate_action = nse_text(item.get("ca_purpose"))
        change_percent = nse_round(item.get("perChange"))
        if direction == "gainers" and (change_percent is None or change_percent <= 0):
            continue
        if direction == "losers" and (change_percent is None or change_percent >= 0):
            continue
        normalized.append({
            "symbol": symbol,
            "series": nse_text(item.get("series")),
            "price": nse_round(item.get("ltp")),
            "change": nse_round(item.get("net_price")),
            "changePercent": change_percent,
            "open": nse_round(item.get("open_price")),
            "high": nse_round(item.get("high_price")),
            "low": nse_round(item.get("low_price")),
            "previousClose": nse_round(item.get("prev_price")),
            "volume": nse_int(item.get("trade_quantity")),
            "turnoverLakhs": nse_round(item.get("turnover")),
            "corporateAction": "" if corporate_action in {"-", "--"} else corporate_action,
        })
    if direction in {"gainers", "losers"}:
        normalized = sorted(
            normalized,
            key=lambda item: item["changePercent"] if item["changePercent"] is not None else 0,
            reverse=(direction == "gainers"),
        )
    return normalized[:limit]


def normalize_nse_nifty_movers(payloads, direction, limit=8):
    variation_rows = normalize_nse_variation(
        payloads.get(direction),
        limit=limit,
        group_key="NIFTY",
        direction=direction,
    )
    if variation_rows:
        return variation_rows
    return normalize_nse_index_movers(payloads.get("nifty50"), direction, limit=limit)


def nse_nifty_mover_note(rows, direction, endpoint_errors=None):
    endpoint_errors = endpoint_errors or {}
    if len(rows) >= 5:
        return ""
    noun = "gainer" if direction == "gainers" else "loser"
    plural = "gainers" if direction == "gainers" else "losers"
    movement = "positive" if direction == "gainers" else "negative"
    if not rows:
        if endpoint_errors.get(direction) and endpoint_errors.get("nifty50"):
            return f"NIFTY 50 {noun} feed is temporarily unavailable."
        return f"NIFTY 50 is not showing {movement} movers in the live feed right now."
    unit = noun if len(rows) == 1 else plural
    verb = "is" if len(rows) == 1 else "are"
    return f"Only {len(rows)} NIFTY 50 {unit} {verb} available in the live feed right now."


def normalize_nse_index_movers(payload, direction, limit=8):
    rows = []
    index_key = sector_match_key(NIFTY_50_INDEX_NAME)
    for item in nse_group_data(payload, "data"):
        symbol = nse_text(item.get("symbol")).upper()
        if not symbol or sector_match_key(symbol) == index_key:
            continue
        change_percent = nse_round(item.get("pChange"))
        if direction == "gainers" and (change_percent is None or change_percent <= 0):
            continue
        if direction == "losers" and (change_percent is None or change_percent >= 0):
            continue
        rows.append({
            "symbol": symbol,
            "series": nse_text(item.get("series")),
            "price": nse_round(item.get("lastPrice")),
            "change": nse_round(item.get("change")),
            "changePercent": change_percent,
            "open": nse_round(item.get("open")),
            "high": nse_round(item.get("dayHigh")) or nse_round(item.get("high")),
            "low": nse_round(item.get("dayLow")) or nse_round(item.get("low")),
            "previousClose": nse_round(item.get("previousClose")),
            "volume": nse_int(item.get("totalTradedVolume")) or nse_int(item.get("quantityTraded")),
            "turnoverLakhs": nse_round(item.get("totalTradedValue")),
            "corporateAction": "",
        })

    return sorted(
        rows,
        key=lambda item: item["changePercent"],
        reverse=(direction == "gainers"),
    )[:limit]


def normalize_nse_most_active(payload, limit=8):
    normalized = []
    for item in (payload or {}).get("data", [])[:limit]:
        symbol = nse_text(item.get("symbol"))
        if not symbol:
            continue
        normalized.append({
            "symbol": symbol,
            "price": nse_round(item.get("lastPrice")),
            "change": nse_round(item.get("change")),
            "changePercent": nse_round(item.get("pChange")),
            "volume": nse_int(item.get("totalTradedVolume")) or nse_int(item.get("quantityTraded")),
            "value": nse_round(item.get("totalTradedValue")),
            "open": nse_round(item.get("open")),
            "high": nse_round(item.get("dayHigh")),
            "low": nse_round(item.get("dayLow")),
            "previousClose": nse_round(item.get("previousClose")),
            "yearHigh": nse_round(item.get("yearHigh")),
            "yearLow": nse_round(item.get("yearLow")),
            "lastUpdateTime": nse_text(item.get("lastUpdateTime")),
        })
    return normalized


def normalize_nse_52_week_highs(payload, limit=8):
    rows = []
    for bucket, label in (
        ("dataLtpGreater20", "LTP above Rs 20"),
        ("dataLtpLess20", "LTP below Rs 20"),
    ):
        for item in (payload or {}).get(bucket, []):
            symbol = nse_text(item.get("symbol"))
            if not symbol:
                continue
            rows.append({
                "symbol": symbol,
                "name": nse_text(item.get("comapnyName")) or nse_text(item.get("companyName")),
                "priceBucket": label,
                "newHigh": nse_round(item.get("new52WHL")),
                "previousHigh": nse_round(item.get("prev52WHL")),
                "previousHighDate": nse_text(item.get("prevHLDate")),
                "price": nse_round(item.get("ltp")),
                "previousClose": nse_round(item.get("prevClose")),
                "change": nse_round(item.get("change")),
                "changePercent": nse_round(item.get("pChange")),
            })
    return rows[:limit]


def normalize_nse_price_bands(payload, limit=8):
    group = nse_group(payload, "AllSec")
    rows = []
    for item in group.get("data", [])[:limit]:
        symbol = nse_text(item.get("symbol"))
        if not symbol:
            continue
        rows.append({
            "symbol": symbol,
            "series": nse_text(item.get("series")),
            "price": nse_round(item.get("ltp")),
            "change": nse_round(item.get("change")),
            "changePercent": nse_round(item.get("pChange")),
            "priceBand": nse_round(item.get("priceBand")),
            "high": nse_round(item.get("highPrice")),
            "low": nse_round(item.get("lowPrice")),
            "yearHigh": nse_round(item.get("yearHigh")),
            "yearLow": nse_round(item.get("yearLow")),
            "volume": nse_int(item.get("totalTradedVol")),
            "turnover": nse_round(item.get("turnover")),
        })
    return {"count": normalize_nse_price_band_count(group.get("count")), "rows": rows}


def normalize_nse_price_band_count(counts):
    normalized = []
    for item in counts or []:
        label = nse_text(item.get("key") if isinstance(item, dict) else "")
        if label:
            normalized.append({"label": label.title(), "value": nse_int(item.get("value"))})
    return normalized


def nse_group(payload, preferred_key):
    if not isinstance(payload, dict):
        return {}
    for key in (preferred_key, preferred_key.lower(), preferred_key.upper(), preferred_key.capitalize()):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    preferred_lower = preferred_key.lower()
    for key, value in payload.items():
        if key.lower() == preferred_lower and isinstance(value, dict):
            return value
    return {}


def nse_group_data(payload, preferred_key):
    group = nse_group(payload, preferred_key)
    if isinstance(group.get("data"), list):
        return group["data"]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return []


def nse_first_timestamp(rows):
    for row in rows:
        if row.get("tradeDate"):
            return row["tradeDate"]
    return ""


def nse_number(value):
    if value in (None, "", "-", "--", "NA", "N/A"):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def nse_round(value, digits=2):
    number = nse_number(value)
    return round(number, digits) if number is not None else None


def nse_int(value):
    number = nse_number(value)
    return int(round(number)) if number is not None else None


def nse_text(value):
    text = str(value or "").strip()
    return "" if text in {"-", "--", "None", "null"} else text


def build_market_clock(symbol, quote_data=None, meta=None, now=None, market_status=None):
    quote_data = quote_data or {}
    meta = meta or {}
    config = market_clock_config(symbol, quote_data, meta)
    exchange_tz = ZoneInfo(config["timezone"])
    local_now = now.astimezone(exchange_tz) if now else datetime.now(exchange_tz)
    day_status = market_day_status(config, local_now.date())
    session_open_at, session_close_at = market_session_window(config, local_now.date())
    next_open_at = next_market_open(config, local_now)

    if market_status is None and config["market"] == "india" and now is None:
        market_status = safe_india_capital_market_status()

    provider_state = str(
        quote_data.get("marketState")
        or meta.get("marketState")
        or ""
    ).upper()
    schedule_open = (
        day_status["tradingDay"]
        and session_open_at
        and session_close_at
        and session_open_at <= local_now < session_close_at
    )
    live_status = nse_text((market_status or {}).get("status"))
    live_message = nse_text((market_status or {}).get("message"))
    live_status_key = live_status.lower()
    live_open = live_status_key in {"open", "regular"}

    is_holiday = day_status["isHoliday"]
    holiday_name = day_status.get("holidayName") or ""
    special_closure = False
    if (
        config["market"] == "india"
        and market_status
        and schedule_open
        and not live_open
    ):
        special_closure = True
        is_holiday = True
        holiday_name = live_message or live_status or "Exchange closure"

    if config["market"] == "india" and market_status and live_open:
        is_holiday = False
        holiday_name = ""
        status = "open"
        status_label = "Market Open"
        message = live_message or "NSE reports the capital market is open."
    elif is_holiday:
        status = "holiday"
        status_label = "Market Holiday"
        message = f"Closed for {holiday_name}" if holiday_name else "Market holiday"
    elif schedule_open and (not live_status or live_open or config["market"] != "india"):
        status = "open"
        status_label = "Market Open"
        message = live_message or "Regular trading session is open."
    elif day_status["isWeekend"]:
        status = "closed"
        status_label = "Market Closed"
        message = "Weekend closure"
    elif session_open_at and local_now < session_open_at:
        status = "closed"
        status_label = "Market Closed"
        message = "Pre-open; regular trading has not started."
    elif provider_state in {"PRE", "PREPRE"}:
        status = "closed"
        status_label = "Market Closed"
        message = "Pre-market; regular trading has not started."
    else:
        status = "closed"
        status_label = "Market Closed"
        message = live_message or "Regular trading session is closed."

    return {
        "exchange": config["label"],
        "market": config["market"],
        "timezone": config["timezone"],
        "timezoneLabel": config["timezoneLabel"],
        "source": config["source"],
        "generatedAt": iso_now(),
        "localDate": local_now.date().isoformat(),
        "regularOpenTime": config["regularOpen"].strftime("%H:%M"),
        "regularCloseTime": config["regularClose"].strftime("%H:%M"),
        "sessionOpenAt": iso_from_datetime(session_open_at),
        "sessionCloseAt": iso_from_datetime(session_close_at),
        "nextOpenAt": iso_from_datetime(next_open_at),
        "status": status,
        "statusLabel": status_label,
        "isOpen": status == "open",
        "isHoliday": is_holiday,
        "isWeekend": day_status["isWeekend"],
        "holidayName": holiday_name,
        "specialClosure": special_closure,
        "providerState": provider_state,
        "liveStatus": live_status,
        "message": message,
    }


def market_clock_config(symbol, quote_data, meta):
    normalized_symbol = str(symbol or "").upper()
    exchange_text = " ".join([
        str(quote_data.get("exchange") or ""),
        str(quote_data.get("fullExchangeName") or ""),
        str(meta.get("exchangeName") or ""),
        str(meta.get("fullExchangeName") or ""),
    ]).upper()

    if (
        normalized_symbol.endswith((".NS", ".BO"))
        or any(token in exchange_text for token in ("NSI", "NSE", "BSE", "BOMBAY", "INDIA"))
    ):
        return MARKET_CLOCK_CONFIGS["india"]
    if (
        any(token in exchange_text for token in ("NMS", "NGM", "NCM", "NYQ", "NYSE", "NASDAQ", "AMEX", "ASE", "PCX", "ARCA"))
        or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", normalized_symbol or "")
    ):
        return MARKET_CLOCK_CONFIGS["us"]

    timezone_name = meta.get("exchangeTimezoneName") or quote_data.get("exchangeTimezoneName")
    config = dict(MARKET_CLOCK_CONFIGS["generic"])
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
            config["timezone"] = timezone_name
            config["timezoneLabel"] = timezone_name.split("/")[-1].replace("_", " ")
        except Exception:
            pass
    exchange_label = (
        quote_data.get("fullExchangeName")
        or quote_data.get("exchange")
        or meta.get("exchangeName")
        or config["label"]
    )
    config["label"] = exchange_label
    return config


def fetch_india_trading_holidays():
    """The exchange's own cash-market calendar, keyed by date.

    This is the only forward-looking source: it lists holidays that have not
    happened yet, including ones declared partway through the year, which is
    precisely what an inferred calendar can never know about.
    """
    payload = fetch_nse_json_with_session(NSE_HOLIDAY_PATH, timeout=12, attempts=2)
    holidays = {}
    for row in (payload or {}).get(NSE_HOLIDAY_SEGMENT) or []:
        if not isinstance(row, dict):
            continue
        parsed = parse_nse_holiday_date(row.get("tradingDate"))
        if parsed:
            holidays[parsed] = nse_text(row.get("description")) or "Trading holiday"
    return holidays


def parse_nse_holiday_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def derive_trading_holidays(symbols):
    """Weekdays with no trade, inferred from price history.

    A weekday absent from a symbol's daily series is either a holiday or a hole
    in the provider's data, and the two are indistinguishable from one series
    alone. Requiring every symbol to agree resolves it: a real closure stops all
    of them, while a data hole affects one. This can only describe days that
    have already passed, so it is a fallback and never the primary.
    """
    seen = []
    for symbol in symbols:
        try:
            candles = (get_chart_range(symbol, "1y", "1d") or {}).get("candles") or []
        except Exception:
            continue
        days = {
            date.fromisoformat(candle["date"])
            for candle in candles
            if isinstance(candle, dict) and candle.get("date")
        }
        if days:
            seen.append(days)
    if not seen:
        return {}

    # Only the window every symbol covers can be reasoned about; outside it, an
    # absence means "not reported" rather than "closed".
    start = max(min(days) for days in seen)
    end = min(max(days) for days in seen)
    holidays = {}
    current = start
    while current <= end:
        if current.weekday() < 5 and all(current not in days for days in seen):
            holidays[current] = "Trading holiday"
        current += timedelta(days=1)
    return holidays


def market_holidays(market):
    """Holiday calendar for a market, exchange-first and inferred second."""
    if not market:
        return {}
    return cached(
        f"market:holidays:{market}",
        lambda: resolve_market_holidays(market),
        HOLIDAY_CACHE_SECONDS,
    )


def resolve_market_holidays(market):
    if market == "india":
        try:
            holidays = fetch_india_trading_holidays()
            if holidays:
                return holidays
        except Exception:
            pass
    try:
        return derive_trading_holidays(HOLIDAY_PROBE_SYMBOLS.get(market) or ())
    except Exception:
        return {}


def market_day_status(config, local_date):
    is_weekend = local_date.weekday() not in config["weekdays"]
    holiday_name = market_holidays(config.get("holidayMarket")).get(local_date, "")
    return {
        "tradingDay": not is_weekend and not holiday_name,
        "isWeekend": is_weekend,
        "isHoliday": bool(holiday_name),
        "holidayName": holiday_name,
    }


def market_session_window(config, local_date):
    day_status = market_day_status(config, local_date)
    if not day_status["tradingDay"]:
        return None, None
    close_time = config["regularClose"]
    session_open_at = datetime.combine(local_date, config["regularOpen"], tzinfo=ZoneInfo(config["timezone"]))
    session_close_at = datetime.combine(local_date, close_time, tzinfo=ZoneInfo(config["timezone"]))
    return session_open_at, session_close_at


def next_market_open(config, local_now):
    for offset in range(0, 370):
        candidate_date = local_now.date() + timedelta(days=offset)
        open_at, close_at = market_session_window(config, candidate_date)
        if not open_at or not close_at:
            continue
        if candidate_date == local_now.date() and local_now >= close_at:
            continue
        return open_at
    return None


def safe_india_capital_market_status():
    try:
        payload = cached("nse-market-status-clock", lambda: fetch_nse_json("/api/marketStatus"), 60)
        for row in normalize_nse_market_status(payload):
            market = (row.get("market") or "").lower()
            if "capital" in market or "equities" in market:
                return row
    except Exception:
        return {}
    return {}


def get_stock_open_interest(symbol):
    nse_symbol = nse_derivative_symbol(symbol)
    if not nse_symbol:
        return open_interest_unavailable(
            symbol,
            "Open interest is available here for NSE/BSE symbols that map to NSE F&O contracts.",
        )

    try:
        payload = get_nse_option_chain_payload(nse_symbol)
        option_chain_report = build_open_interest_report(nse_symbol, payload)
    except Exception as error:
        option_chain_report = open_interest_unavailable(nse_symbol, str(error))
    if option_chain_report.get("available"):
        return option_chain_report
    try:
        oi_spurt_report = get_oi_spurt_open_interest(nse_symbol)
    except Exception:
        oi_spurt_report = open_interest_unavailable(nse_symbol, option_chain_report.get("summary"))
    if oi_spurt_report.get("available"):
        oi_spurt_report["note"] = option_chain_report.get("summary")
        return oi_spurt_report
    return option_chain_report


def get_nse_option_chain_payload(nse_symbol):
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    contract_info = cached(
        f"nse-option-chain-contract-info:{nse_symbol}",
        lambda: fetch_nse_json_with_session(f"/api/option-chain-contract-info?symbol={quote(nse_symbol)}"),
        60,
    )
    expiry_texts = [nse_text(value) for value in contract_info.get("expiryDates") or []]
    expiry_texts = [value for value in expiry_texts if value]
    selected_expiries = []
    for expiry_text in expiry_texts:
        expiry_date = parse_nse_expiry_date(expiry_text)
        if expiry_date and expiry_date >= today and (expiry_date - today).days <= 92:
            selected_expiries.append(expiry_text)
    if not selected_expiries and expiry_texts:
        selected_expiries = expiry_texts[:1]
    selected_expiries = selected_expiries[:4]
    if not selected_expiries:
        return {"records": {"timestamp": "", "underlyingValue": None, "expiryDates": [], "data": []}}

    records = {
        "timestamp": "",
        "underlyingValue": None,
        "expiryDates": expiry_texts,
        "data": [],
    }
    # One request per expiry, run together. Sequentially this was the single
    # slowest step in a stock analysis - four expiries at roughly half a second
    # each, on the critical path of every uncached report.
    def load_expiry(expiry_text):
        return cached(
            f"nse-option-chain-v3:{nse_symbol}:{expiry_text}",
            lambda: fetch_nse_json_with_session(
                f"/api/option-chain-v3?type=Equity&symbol={quote(nse_symbol)}&expiry={quote(expiry_text)}"
            ),
            60,
        )

    # settle_map preserves input order, so the strike rows stay grouped by expiry
    # rather than arriving in completion order. A failed expiry is skipped: a
    # partial chain is still a usable read, and the caller falls back to the OI
    # spurt feed if nothing lands at all.
    settled = settle_map(selected_expiries, load_expiry, concurrency=len(selected_expiries))
    for ok, payload in settled:
        if not ok:
            continue
        expiry_records = (payload or {}).get("records") or {}
        if expiry_records.get("timestamp"):
            records["timestamp"] = expiry_records.get("timestamp")
        if expiry_records.get("underlyingValue") is not None:
            records["underlyingValue"] = expiry_records.get("underlyingValue")
        records["data"].extend(expiry_records.get("data") or [])
    return {"records": records}


def nse_derivative_symbol(symbol):
    root = str(symbol or "").upper().split(".")[0].strip()
    if re.fullmatch(r"[A-Z0-9&-]{1,30}", root):
        return root
    return ""


def open_interest_unavailable(symbol, reason="NSE option-chain open interest is not available for this stock."):
    return {
        "available": False,
        "symbol": str(symbol or ""),
        "source": "NSE India option-chain equities",
        "generatedAt": iso_now(),
        "summary": reason,
        "periods": {},
        "expiryDates": [],
    }


def get_oi_spurt_open_interest(nse_symbol):
    payload = cached(
        "nse-oi-spurts-underlyings",
        lambda: fetch_nse_json("/api/live-analysis-oi-spurts-underlyings"),
        60,
    )
    match = None
    for row in payload.get("data") or []:
        if nse_text(row.get("symbol")).upper() == nse_symbol:
            match = row
            break
    if not match:
        return open_interest_unavailable(
            nse_symbol,
            "NSE option-chain OI was unavailable and the stock was not present in NSE Change in Open Interest.",
        )

    latest_oi = nse_int(match.get("latestOI")) or 0
    previous_oi = nse_int(match.get("prevOI")) or 0
    change_oi = nse_int(match.get("changeInOI")) or 0
    change_percent = nse_round(match.get("avgInOI"))
    bias = "OI increasing" if change_oi > 0 else "OI reducing" if change_oi < 0 else "OI stable"
    volume = nse_int(match.get("volume"))
    day_period = {
        "available": True,
        "aggregateOnly": True,
        "label": "Day",
        "summary": (
            f"Day OI from NSE Change in Open Interest: latest OI {latest_oi:,}, "
            f"change {change_oi:+,} ({change_percent if change_percent is not None else 'n/a'}%). "
            "NSE aggregate feed does not split call volume versus put volume."
        ),
        "volumeSummary": (
            f"NSE reports aggregate derivative volume of {volume:,} contracts, but not call/put split."
            if volume is not None
            else "NSE aggregate feed did not return call/put volume split."
        ),
        "callPutVolumeSplitAvailable": False,
        "expiryDates": [],
        "totalOi": latest_oi,
        "previousOi": previous_oi,
        "changeOi": change_oi,
        "changePercent": change_percent,
        "volume": volume,
        "futuresValue": nse_round(match.get("futValue")),
        "optionsValue": nse_round(match.get("optValue")),
        "premiumValue": nse_round(match.get("premValue")),
        "underlyingValue": nse_round(match.get("underlyingValue")),
        "bias": bias,
        "rows": [],
    }

    unavailable_periods = {
        key: {
            "available": False,
            "label": label,
            "summary": f"{label} OI requires full NSE option-chain data, which was not returned.",
            "rows": [],
        }
        for key, label in (("week", "Week"), ("month", "Month"), ("quarter", "Quarter"))
    }
    return {
        "available": True,
        "symbol": nse_symbol,
        "source": "NSE India Change in Open Interest",
        "generatedAt": iso_now(),
        "timestamp": nse_text(payload.get("timestamp")),
        "underlyingValue": day_period["underlyingValue"],
        "summary": day_period["summary"],
        "periods": {"day": day_period, **unavailable_periods},
        "expiryDates": [],
    }


def build_open_interest_report(nse_symbol, payload, today=None):
    records = (payload or {}).get("records") or {}
    raw_rows = records.get("data") or []
    today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    expiry_map = {}
    for value in records.get("expiryDates") or []:
        expiry_date = parse_nse_expiry_date(value)
        if expiry_date:
            expiry_map[expiry_date] = nse_text(value)
    for item in raw_rows:
        expiry_date = parse_option_row_expiry_date(item)
        if expiry_date and expiry_date not in expiry_map:
            expiry_map[expiry_date] = option_row_expiry_label(item, expiry_date)

    expiry_dates = sorted(expiry_map)
    if not raw_rows or not expiry_dates:
        return open_interest_unavailable(
            nse_symbol,
            "NSE did not return option-chain open interest rows for this stock.",
        )

    periods = {}
    for key, label, max_days in (
        ("day", "Day", 0),
        ("week", "Week", 7),
        ("month", "Month", 31),
        ("quarter", "Quarter", 92),
    ):
        selected_expiries = select_oi_expiries(expiry_dates, today, max_days)
        periods[key] = summarize_open_interest_period(
            raw_rows,
            selected_expiries,
            expiry_map,
            label,
            records.get("underlyingValue"),
        )

    available_periods = [period for period in periods.values() if period.get("available")]
    if not available_periods:
        return open_interest_unavailable(
            nse_symbol,
            "NSE option-chain rows were returned, but OI values were not usable.",
        )

    nearest = periods.get("day") or available_periods[0]
    return {
        "available": True,
        "symbol": nse_symbol,
        "source": "NSE India option-chain equities",
        "generatedAt": iso_now(),
        "timestamp": nse_text(records.get("timestamp")),
        "underlyingValue": nse_round(records.get("underlyingValue")),
        "expiryDates": [
            {"date": expiry_date.isoformat(), "label": expiry_map[expiry_date]}
            for expiry_date in expiry_dates
        ],
        "summary": nearest.get("summary") or "Open interest summary is available.",
        "periods": periods,
    }


def parse_nse_expiry_date(value):
    text = nse_text(value)
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.title(), fmt).date()
        except ValueError:
            continue
    return None


def parse_option_row_expiry_date(item):
    return parse_nse_expiry_date(
        item.get("expiryDate")
        or item.get("expiryDates")
        or (item.get("CE") or {}).get("expiryDate")
        or (item.get("PE") or {}).get("expiryDate")
    )


def option_row_expiry_label(item, expiry_date):
    return (
        nse_text(item.get("expiryDates"))
        or nse_text(item.get("expiryDate"))
        or nse_text((item.get("CE") or {}).get("expiryDate"))
        or nse_text((item.get("PE") or {}).get("expiryDate"))
        or expiry_date.isoformat()
    )


def select_oi_expiries(expiry_dates, today, max_days):
    future_expiries = [expiry for expiry in expiry_dates if expiry >= today]
    if not future_expiries:
        future_expiries = expiry_dates[:]
    if not future_expiries:
        return []
    nearest = future_expiries[0]
    if max_days == 0:
        return [nearest]
    selected = [
        expiry
        for expiry in future_expiries
        if 0 <= (expiry - today).days <= max_days
    ]
    return selected or [nearest]


def summarize_open_interest_period(raw_rows, selected_expiries, expiry_map, label, underlying_value):
    selected_set = set(selected_expiries)
    rows_by_strike = {}
    for item in raw_rows:
        expiry_date = parse_option_row_expiry_date(item)
        if expiry_date not in selected_set:
            continue
        strike = nse_round(item.get("strikePrice"))
        if strike is None:
            continue
        row = rows_by_strike.setdefault(strike, {
            "strike": strike,
            "callOi": 0,
            "putOi": 0,
            "callChangeOi": 0,
            "putChangeOi": 0,
            "callVolume": 0,
            "putVolume": 0,
            "callIv": None,
            "putIv": None,
        })
        merge_option_side(row, item.get("CE") or {}, "call")
        merge_option_side(row, item.get("PE") or {}, "put")

    rows = list(rows_by_strike.values())
    if not rows:
        return {
            "available": False,
            "label": label,
            "summary": f"No OI contracts found for {label.lower()} view.",
            "expiryDates": [],
            "rows": [],
        }

    for row in rows:
        row["totalOi"] = row["callOi"] + row["putOi"]
        row["netChangeOi"] = row["putChangeOi"] - row["callChangeOi"]
        row["totalVolume"] = row["callVolume"] + row["putVolume"]
        row["volumePcrAtStrike"] = round_or_none(row["putVolume"] / row["callVolume"]) if row["callVolume"] else None
        row["pcrAtStrike"] = round_or_none(row["putOi"] / row["callOi"]) if row["callOi"] else None
        row["bias"] = open_interest_row_bias(row)
        row["volumeBias"] = open_interest_volume_bias(row["callVolume"], row["putVolume"])

    total_call_oi = sum(row["callOi"] for row in rows)
    total_put_oi = sum(row["putOi"] for row in rows)
    total_call_change = sum(row["callChangeOi"] for row in rows)
    total_put_change = sum(row["putChangeOi"] for row in rows)
    total_call_volume = sum(row["callVolume"] for row in rows)
    total_put_volume = sum(row["putVolume"] for row in rows)
    total_volume = total_call_volume + total_put_volume
    max_call = max(rows, key=lambda row: row["callOi"]) if total_call_oi else None
    max_put = max(rows, key=lambda row: row["putOi"]) if total_put_oi else None
    max_pain = calculate_max_pain(rows)
    pcr = round_or_none(total_put_oi / total_call_oi) if total_call_oi else None
    volume_pcr = round_or_none(total_put_volume / total_call_volume) if total_call_volume else None
    bias = open_interest_period_bias(pcr, total_call_change, total_put_change)
    volume_bias = open_interest_volume_bias(total_call_volume, total_put_volume)
    volume_summary = open_interest_volume_summary(
        total_call_volume,
        total_put_volume,
        total_call_oi,
        total_put_oi,
    )
    top_rows = sorted(
        rows,
        key=lambda row: (
            row["totalOi"],
            row["totalVolume"],
            abs(row["callChangeOi"]) + abs(row["putChangeOi"]),
        ),
        reverse=True,
    )[:12]

    expiry_labels = [expiry_map.get(expiry, expiry.isoformat()) for expiry in selected_expiries]
    summary = (
        f"{label} OI across {', '.join(expiry_labels)}: PCR {pcr if pcr is not None else 'n/a'}, "
        f"{bias.lower()}. {volume_summary}"
    )
    return {
        "available": True,
        "label": label,
        "summary": summary,
        "volumeSummary": volume_summary,
        "callPutVolumeSplitAvailable": True,
        "expiryDates": [
            {"date": expiry.isoformat(), "label": expiry_map.get(expiry, expiry.isoformat())}
            for expiry in selected_expiries
        ],
        "totalCallOi": total_call_oi,
        "totalPutOi": total_put_oi,
        "totalCallChangeOi": total_call_change,
        "totalPutChangeOi": total_put_change,
        "totalCallVolume": total_call_volume,
        "totalPutVolume": total_put_volume,
        "totalVolume": total_volume,
        "pcr": pcr,
        "volumePcr": volume_pcr,
        "maxCallOiStrike": max_call["strike"] if max_call else None,
        "maxPutOiStrike": max_put["strike"] if max_put else None,
        "maxPain": max_pain,
        "underlyingValue": nse_round(underlying_value),
        "bias": bias,
        "volumeBias": volume_bias,
        "rows": top_rows,
    }


def merge_option_side(row, option_data, side):
    prefix = "call" if side == "call" else "put"
    row[f"{prefix}Oi"] += nse_int(option_data.get("openInterest")) or 0
    row[f"{prefix}ChangeOi"] += nse_int(option_data.get("changeinOpenInterest")) or 0
    row[f"{prefix}Volume"] += nse_int(option_data.get("totalTradedVolume")) or 0
    iv = nse_round(option_data.get("impliedVolatility"))
    if iv is not None:
        row[f"{prefix}Iv"] = iv


def calculate_max_pain(rows):
    best_strike = None
    best_pain = None
    for candidate in [row["strike"] for row in rows]:
        pain = sum(
            row["callOi"] * max(0, candidate - row["strike"])
            + row["putOi"] * max(0, row["strike"] - candidate)
            for row in rows
        )
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_strike = candidate
    return best_strike


def open_interest_period_bias(pcr, call_change, put_change):
    if is_finite(pcr) and pcr >= 1.1 and put_change >= call_change:
        return "Bullish put-writing support"
    if is_finite(pcr) and pcr <= 0.85 and call_change >= put_change:
        return "Bearish call-writing pressure"
    if put_change > call_change:
        return "Put OI building faster"
    if call_change > put_change:
        return "Call OI building faster"
    return "Balanced OI"


def open_interest_volume_bias(call_volume, put_volume):
    call_volume = call_volume or 0
    put_volume = put_volume or 0
    total = call_volume + put_volume
    if not total:
        return "Volume split unavailable"
    call_share = call_volume / total
    put_share = put_volume / total
    if put_share >= 0.55:
        return "Put volume dominates"
    if call_share >= 0.55:
        return "Call volume dominates"
    return "Balanced call/put volume"


def open_interest_volume_summary(call_volume, put_volume, call_oi, put_oi):
    call_volume = call_volume or 0
    put_volume = put_volume or 0
    total_volume = call_volume + put_volume
    if not total_volume:
        return "Call/put volume split was not available."

    call_share = call_volume / total_volume * 100
    put_share = put_volume / total_volume * 100
    volume_side = "put" if put_volume > call_volume else "call" if call_volume > put_volume else "call and put"
    oi_side = "put OI" if put_oi > call_oi else "call OI" if call_oi > put_oi else "balanced OI"
    return (
        f"Volume-led view: {volume_side} positions are more active "
        f"(calls {call_share:.1f}%, puts {put_share:.1f}%); {oi_side} is higher by open interest."
    )


def open_interest_row_bias(row):
    if row["putChangeOi"] > row["callChangeOi"] and row["putOi"] >= row["callOi"]:
        return "Put support"
    if row["callChangeOi"] > row["putChangeOi"] and row["callOi"] >= row["putOi"]:
        return "Call resistance"
    if row["putOi"] > row["callOi"]:
        return "Put-heavy"
    if row["callOi"] > row["putOi"]:
        return "Call-heavy"
    return "Balanced"


def safe_moneycontrol_sector_snapshot():
    try:
        return get_moneycontrol_sector_snapshot()
    except Exception as error:
        return {
            "available": False,
            "source": "Moneycontrol sector analysis",
            "url": MONEYCONTROL_SECTOR_URL,
            "generatedAt": iso_now(),
            "error": str(error),
            "sectors": [],
            "topPerforming": [],
            "underPerforming": [],
            "sectorIndices": [],
        }


def safe_sector_open_interest():
    try:
        return get_sector_open_interest()
    except Exception as error:
        return {
            "available": False,
            "source": "NSE India Change in Open Interest + sectoral indices",
            "generatedAt": iso_now(),
            "error": str(error),
            "summary": "Sector-wise open interest could not be loaded.",
            "rows": [],
            "totals": {},
            "coverage": {},
        }


def get_sector_open_interest():
    return cached(
        "nse-sector-open-interest",
        build_sector_open_interest,
        60,
    )


def build_sector_open_interest():
    oi_payload = cached(
        "nse-oi-spurts-underlyings",
        lambda: fetch_nse_json("/api/live-analysis-oi-spurts-underlyings"),
        60,
    )
    sector_map = get_nse_sector_constituent_map()
    return build_sector_open_interest_from_payloads(oi_payload, sector_map)


def get_nse_sector_constituent_map():
    return cached(
        "nse-sector-constituent-map",
        build_nse_sector_constituent_map,
        15 * 60,
    )


def build_nse_sector_constituent_map():
    results = settle_map(NSE_OI_SECTOR_INDICES, fetch_nse_sector_constituents, concurrency=4)
    symbol_to_sector = {}
    sector_counts = {}
    stock_movers = {}
    stock_performance = {}
    errors = {}
    for ok, value in results:
        if not ok:
            errors[str(value)] = "NSE sector constituent request failed."
            continue
        sector = value.get("sector")
        symbols = value.get("symbols") or []
        sector_counts[sector] = len(symbols)
        stock_movers[sector] = value.get("topStocks") or []
        stock_performance[sector] = {
            "sector": sector,
            "stockCount": len(symbols),
            "advance": value.get("advance") or 0,
            "decline": value.get("decline") or 0,
            "unchanged": value.get("unchanged") or 0,
            "bestStock": value.get("bestStock") or {},
            "worstStock": value.get("worstStock") or {},
            "timestamp": value.get("timestamp") or "",
        }
        for symbol in symbols:
            symbol_to_sector.setdefault(symbol, sector)
    if not symbol_to_sector:
        raise RuntimeError("NSE sector constituents were unavailable.")
    return {
        "symbols": symbol_to_sector,
        "sectors": sector_counts,
        "stockMovers": stock_movers,
        "stockPerformance": stock_performance,
        "errors": errors,
        "sourceIndices": list(NSE_OI_SECTOR_INDICES),
    }


def fetch_nse_sector_constituents(index_name):
    payload = fetch_nse_json(f"/api/equity-stockIndices?index={quote(index_name)}")
    symbols = []
    stocks = []
    index_key = sector_match_key(index_name)
    for item in payload.get("data") or []:
        symbol = nse_text(item.get("symbol")).upper()
        if not symbol or sector_match_key(symbol) == index_key:
            continue
        symbols.append(symbol)
        stocks.append({
            "symbol": symbol,
            "name": nse_text((item.get("meta") or {}).get("companyName")),
            "price": nse_round(item.get("lastPrice")),
            "change": nse_round(item.get("change")),
            "changePercent": nse_round(item.get("pChange")),
            "volume": nse_int(item.get("totalTradedVolume")),
        })
    ordered_by_move = sorted(
        [stock for stock in stocks if is_finite(stock.get("changePercent"))],
        key=lambda stock: stock["changePercent"],
    )
    return {
        "sector": index_name,
        "symbols": list(dict.fromkeys(symbols)),
        "advance": len([stock for stock in stocks if is_finite(stock.get("changePercent")) and stock["changePercent"] > 0]),
        "decline": len([stock for stock in stocks if is_finite(stock.get("changePercent")) and stock["changePercent"] < 0]),
        "unchanged": len([stock for stock in stocks if stock.get("changePercent") == 0]),
        "bestStock": ordered_by_move[-1] if ordered_by_move else {},
        "worstStock": ordered_by_move[0] if ordered_by_move else {},
        "topStocks": sorted(
            stocks,
            key=lambda stock: abs(stock.get("changePercent") or 0),
            reverse=True,
        )[:5],
        "timestamp": nse_text(payload.get("timestamp"))
            or nse_text((payload.get("metadata") or {}).get("timeVal")),
    }


def build_sector_open_interest_from_payloads(oi_payload, sector_map):
    symbol_to_sector = (sector_map or {}).get("symbols") or {}
    sector_stock_movers = (sector_map or {}).get("stockMovers") or {}
    sector_stock_performance = (sector_map or {}).get("stockPerformance") or {}
    buckets = {}
    mapped_count = 0
    unmapped_count = 0
    source_rows = oi_payload.get("data") or []
    for item in source_rows:
        symbol = nse_text(item.get("symbol")).upper()
        if not symbol:
            continue
        sector = symbol_to_sector.get(symbol)
        if not sector:
            unmapped_count += 1
            continue
        mapped_count += 1
        latest_oi = nse_int(item.get("latestOI")) or 0
        previous_oi = nse_int(item.get("prevOI")) or 0
        change_oi = nse_int(item.get("changeInOI")) or 0
        volume = nse_int(item.get("volume")) or 0
        bucket = buckets.setdefault(sector, {
            "sector": sector,
            "latestOi": 0,
            "previousOi": 0,
            "changeOi": 0,
            "volume": 0,
            "optionsValue": 0,
            "futuresValue": 0,
            "premiumValue": 0,
            "underlyingValue": 0,
            "stockCount": 0,
            "stocks": [],
        })
        bucket["latestOi"] += latest_oi
        bucket["previousOi"] += previous_oi
        bucket["changeOi"] += change_oi
        bucket["volume"] += volume
        bucket["optionsValue"] += nse_round(item.get("optValue")) or 0
        bucket["futuresValue"] += nse_round(item.get("futValue")) or 0
        bucket["premiumValue"] += nse_round(item.get("premValue")) or 0
        bucket["underlyingValue"] += nse_round(item.get("underlyingValue")) or 0
        bucket["stockCount"] += 1
        bucket["stocks"].append({
            "symbol": symbol,
            "latestOi": latest_oi,
            "previousOi": previous_oi,
            "changeOi": change_oi,
            "changePercent": round_or_none(change_oi / previous_oi * 100) if previous_oi else None,
            "volume": volume,
            "underlyingValue": nse_round(item.get("underlyingValue")),
        })

    rows = []
    for bucket in buckets.values():
        change_percent = (
            round_or_none(bucket["changeOi"] / bucket["previousOi"] * 100)
            if bucket["previousOi"]
            else None
        )
        top_stocks = sorted(
            bucket["stocks"],
            key=lambda stock: (stock.get("latestOi") or 0, abs(stock.get("changeOi") or 0)),
            reverse=True,
        )[:4]
        bucket["changePercent"] = change_percent
        bucket["volumeToOi"] = round_or_none(bucket["volume"] / bucket["latestOi"]) if bucket["latestOi"] else None
        bucket["bias"] = sector_open_interest_bias(bucket["changeOi"], change_percent)
        bucket["topStocks"] = top_stocks
        bucket["topMovingStocks"] = sector_stock_movers.get(bucket["sector"], [])
        bucket["summary"] = summarize_sector_open_interest_row(bucket)
        del bucket["stocks"]
        rows.append(bucket)

    rows = sorted(rows, key=lambda row: row.get("latestOi") or 0, reverse=True)
    if not rows:
        return {
            "available": False,
            "source": "NSE India Change in Open Interest + sectoral indices",
            "generatedAt": iso_now(),
            "timestamp": nse_text(oi_payload.get("timestamp")),
            "summary": "No NSE OI symbols could be mapped to sectoral indices.",
            "rows": [],
            "totals": {},
            "coverage": {
                "sourceRows": len(source_rows),
                "mappedStocks": mapped_count,
                "unmappedStocks": unmapped_count,
            },
            "sectorPerformance": sector_stock_performance,
        }

    total_latest = sum(row["latestOi"] for row in rows)
    total_previous = sum(row["previousOi"] for row in rows)
    total_change = sum(row["changeOi"] for row in rows)
    total_volume = sum(row["volume"] for row in rows)
    top_build_up = max(rows, key=lambda row: row.get("changePercent") if is_finite(row.get("changePercent")) else -999)
    highest_oi = rows[0]
    summary = (
        f"Sector OI maps {mapped_count} F&O stocks from NSE Change in OI into {len(rows)} sectoral buckets. "
        f"Highest OI: {highest_oi['sector']}; strongest OI build-up: {top_build_up['sector']}."
    )
    return {
        "available": True,
        "source": "NSE India Change in Open Interest + sectoral indices",
        "generatedAt": iso_now(),
        "timestamp": nse_text(oi_payload.get("timestamp")),
        "summary": summary,
        "rows": rows[:14],
        "totals": {
            "latestOi": total_latest,
            "previousOi": total_previous,
            "changeOi": total_change,
            "changePercent": round_or_none(total_change / total_previous * 100) if total_previous else None,
            "volume": total_volume,
        },
        "highestOi": highest_oi,
        "topBuildUp": top_build_up,
        "stockMoversBySector": sector_stock_movers,
        "sectorPerformance": sector_stock_performance,
        "coverage": {
            "sourceRows": len(source_rows),
            "mappedStocks": mapped_count,
            "unmappedStocks": unmapped_count,
            "sectorIndices": len((sector_map or {}).get("sectors") or {}),
        },
        "note": "A symbol is mapped to the first matching NSE sectoral index in the configured priority list, so overlapping sector indices are not double-counted.",
    }


def sector_open_interest_bias(change_oi, change_percent):
    if change_oi > 0 and is_finite(change_percent) and change_percent >= 5:
        return "Strong OI build-up"
    if change_oi > 0:
        return "OI build-up"
    if change_oi < 0 and is_finite(change_percent) and change_percent <= -5:
        return "Strong OI unwinding"
    if change_oi < 0:
        return "OI unwinding"
    return "OI stable"


def summarize_sector_open_interest_row(row):
    top_symbols = ", ".join(stock["symbol"] for stock in row.get("topStocks") or [] if stock.get("symbol"))
    change_text = (
        f"{row['changePercent']:+.2f}%"
        if is_finite(row.get("changePercent"))
        else "n/a"
    )
    return (
        f"{row.get('sector')} has {row.get('stockCount', 0)} mapped F&O stocks, "
        f"OI change {change_text}; top OI names: {top_symbols or 'n/a'}."
    )


def build_sector_stock_performance(nse_snapshot, sector_performance):
    sector_performance = sector_performance or {}
    index_rows = {
        row.get("name"): row
        for row in (nse_snapshot or {}).get("sectorIndices") or []
        if row.get("name")
    }
    rows = []
    for sector_name in NSE_SECTOR_INDICES:
        performance = sector_performance.get(sector_name) or {}
        index = index_rows.get(sector_name) or {}
        if not performance and not index:
            continue
        row = {
            "sector": sector_name,
            "stockCount": performance.get("stockCount"),
            "advance": first_present(performance.get("advance"), index.get("advances")),
            "decline": first_present(performance.get("decline"), index.get("declines")),
            "unchanged": first_present(performance.get("unchanged"), index.get("unchanged")),
            "sectorLast": index.get("last"),
            "sectorChange": index.get("change"),
            "sectorChangePercent": index.get("changePercent"),
            "sectorHigh": index.get("high"),
            "sectorLow": index.get("low"),
            "sectorPe": index.get("pe"),
            "oneMonthChange": index.get("oneMonthChange"),
            "oneYearChange": index.get("oneYearChange"),
            "bestStock": performance.get("bestStock") or {},
            "worstStock": performance.get("worstStock") or {},
        }
        row["summary"] = summarize_sector_stock_performance(row)
        rows.append(row)

    available_rows = [
        row for row in rows
        if row.get("bestStock") or row.get("worstStock") or is_finite(row.get("sectorChangePercent"))
    ]
    return {
        "available": bool(available_rows),
        "source": "NSE India sectoral index constituents",
        "generatedAt": iso_now(),
        "summary": summarize_sector_stock_performance_report(available_rows),
        "rows": available_rows,
    }


def summarize_sector_stock_performance(row):
    sector = row.get("sector") or "Sector"
    sector_change = row.get("sectorChangePercent")
    best = row.get("bestStock") or {}
    worst = row.get("worstStock") or {}
    best_change = best.get("changePercent")
    worst_change = worst.get("changePercent")

    if is_finite(sector_change) and best.get("symbol") and worst.get("symbol"):
        best_spread = best_change - sector_change if is_finite(best_change) else None
        worst_spread = worst_change - sector_change if is_finite(worst_change) else None
        best_text = describe_stock_sector_spread(best["symbol"], best_spread, "led the sector")
        worst_text = describe_stock_sector_spread(worst["symbol"], worst_spread, "was the weakest constituent")
        direction = "up" if sector_change >= 0 else "down"
        return f"{sector} is {direction} {abs(sector_change):.2f}%; {best_text}, while {worst_text}."

    if best.get("symbol") and worst.get("symbol"):
        return f"{sector}: {best['symbol']} is the strongest constituent and {worst['symbol']} is the weakest in the current NSE snapshot."
    if is_finite(sector_change):
        direction = "up" if sector_change >= 0 else "down"
        return f"{sector} is {direction} {abs(sector_change):.2f}%; constituent best/worst data is not available."
    return f"{sector} performance is not available in the current NSE snapshot."


def describe_stock_sector_spread(symbol, spread, fallback):
    if not is_finite(spread):
        return f"{symbol} {fallback}"
    if spread >= 0:
        return f"{symbol} outperformed the sector by {spread:+.2f} pp"
    return f"{symbol} lagged the sector by {abs(spread):.2f} pp"


def summarize_sector_stock_performance_report(rows):
    if not rows:
        return "Sector stock performance is unavailable from NSE right now."
    positive = len([row for row in rows if is_finite(row.get("sectorChangePercent")) and row["sectorChangePercent"] > 0])
    negative = len([row for row in rows if is_finite(row.get("sectorChangePercent")) and row["sectorChangePercent"] < 0])
    strongest = max(
        rows,
        key=lambda row: row.get("sectorChangePercent") if is_finite(row.get("sectorChangePercent")) else -999,
    )
    weakest = min(
        rows,
        key=lambda row: row.get("sectorChangePercent") if is_finite(row.get("sectorChangePercent")) else 999,
    )
    return (
        f"{len(rows)} major NSE sectors tracked. "
        f"{positive} sectors are positive and {negative} are negative. "
        f"Strongest sector: {strongest.get('sector', 'n/a')} ({format_signed_percent(strongest.get('sectorChangePercent'))}); "
        f"weakest sector: {weakest.get('sector', 'n/a')} ({format_signed_percent(weakest.get('sectorChangePercent'))})."
    )


def format_signed_percent(value):
    return f"{value:+.2f}%" if is_finite(value) else "n/a"


def get_moneycontrol_sector_snapshot():
    return cached(
        "moneycontrol-sector-analysis",
        fetch_moneycontrol_sector_snapshot,
        15 * 60,
    )


def fetch_moneycontrol_sector_snapshot():
    page = fetch_text(MONEYCONTROL_SECTOR_URL)
    payload = extract_moneycontrol_sector_payload(page)
    return build_moneycontrol_sector_snapshot_from_payload(payload)


def build_moneycontrol_sector_snapshot_from_payload(payload):
    sectors = [
        row for row in (
            normalize_moneycontrol_sector(item)
            for item in payload.get("allSectors") or []
        )
        if row.get("sector")
    ]
    sector_indices = [
        row for row in (
            normalize_moneycontrol_sector_index(item)
            for item in payload.get("sectorIndices") or []
        )
        if row.get("name")
    ]
    sorted_by_move = sorted(
        sectors,
        key=lambda row: row.get("marketCapChangePercent")
            if is_finite(row.get("marketCapChangePercent"))
            else -999,
        reverse=True,
    )
    sorted_by_weakness = sorted(
        sectors,
        key=lambda row: row.get("marketCapChangePercent")
            if is_finite(row.get("marketCapChangePercent"))
            else 999,
    )
    breadth = summarize_moneycontrol_sector_breadth(sectors)

    return {
        "available": True,
        "source": "Moneycontrol sector analysis",
        "url": MONEYCONTROL_SECTOR_URL,
        "generatedAt": iso_now(),
        "sectors": sectors,
        "topPerforming": sorted_by_move[:5],
        "underPerforming": sorted_by_weakness[:3],
        "sectorIndices": sector_indices[:16],
        "breadth": breadth,
        "note": "Sector classification, trend, market-cap move, advance/decline, PE, and earnings YoY are parsed from Moneycontrol sector analysis.",
    }


def extract_moneycontrol_sector_payload(page):
    script = match_first(
        page,
        r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>([\s\S]*?)</script>",
    )
    if not script:
        raise RuntimeError("Moneycontrol sector payload was not found.")
    try:
        data = json.loads(html.unescape(script))
    except json.JSONDecodeError as error:
        raise RuntimeError("Moneycontrol sector payload could not be parsed.") from error
    return {
        "allSectors": find_nested_key(data, "allSectors") or [],
        "sectorIndices": find_nested_key(data, "sectorIndices") or [],
    }


def find_nested_key(value, target_key):
    if isinstance(value, dict):
        if target_key in value:
            return value[target_key]
        for child in value.values():
            found = find_nested_key(child, target_key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_nested_key(child, target_key)
            if found is not None:
                return found
    return None


def normalize_moneycontrol_sector(item):
    advance = nse_int(item.get("advance")) or 0
    decline = nse_int(item.get("decline")) or 0
    total = advance + decline
    sector = nse_text(item.get("sector"))
    trend = nse_text(item.get("trend"))
    market_cap_change_percent = nse_round(item.get("mCapPerChange"))
    earnings_yoy_change = nse_round(item.get("sectorNpYoyChange"))
    row = {
        "sector": sector,
        "trend": trend,
        "slug": nse_text(item.get("slug")),
        "stocks": nse_int(item.get("stockCnt")),
        "industries": nse_int(item.get("industryCnt")),
        "advance": advance,
        "decline": decline,
        "advanceDeclineRatio": round(advance / decline, 2) if decline else None,
        "advancePercent": round(advance / total * 100, 2) if total else None,
        "marketCapCrore": nse_round(item.get("currentMcap")),
        "marketCapChangeCrore": nse_round(item.get("mCapChange")),
        "marketCapChangePercent": market_cap_change_percent,
        "sectorPe": nse_round(item.get("sectorPe")),
        "earningsYoyCrore": nse_round(item.get("sectorNpYoy")),
        "earningsYoyChange": earnings_yoy_change,
        "url": f"{MONEYCONTROL_SECTOR_URL}{nse_text(item.get('slug'))}/" if nse_text(item.get("slug")) else MONEYCONTROL_SECTOR_URL,
    }
    row["score"] = score_moneycontrol_sector(row)
    row["summary"] = summarize_moneycontrol_sector(row)
    return row


def normalize_moneycontrol_sector_index(item):
    return {
        "name": nse_text(item.get("indexName")),
        "price": nse_round(item.get("ltp")),
        "change": nse_round(item.get("change")),
        "changePercent": nse_round(item.get("changePer")),
        "advance": nse_int(item.get("advance")),
        "decline": nse_int(item.get("decline")),
        "lastUpdated": moneycontrol_timestamp(item.get("lastUpdated")),
    }


def summarize_moneycontrol_sector_breadth(sectors):
    advance = sum(row.get("advance") or 0 for row in sectors)
    decline = sum(row.get("decline") or 0 for row in sectors)
    stocks = sum(row.get("stocks") or 0 for row in sectors)
    bullish = len([row for row in sectors if "BULLISH" in row.get("trend", "").upper()])
    bearish = len([row for row in sectors if "BEARISH" in row.get("trend", "").upper()])
    total = advance + decline
    return {
        "advance": advance,
        "decline": decline,
        "stocks": stocks,
        "bullishSectors": bullish,
        "bearishSectors": bearish,
        "advanceDeclineRatio": round(advance / decline, 2) if decline else None,
        "advancePercent": round(advance / total * 100, 2) if total else None,
    }


def score_moneycontrol_sector(row):
    trend_score = {
        "VERY BULLISH": 80,
        "BULLISH": 65,
        "NEUTRAL": 50,
        "BEARISH": 35,
        "VERY BEARISH": 20,
    }.get(row.get("trend", "").upper(), 45)
    market_move = row.get("marketCapChangePercent")
    earnings = row.get("earningsYoyChange")
    advance_percent = row.get("advancePercent")
    score = trend_score
    if is_finite(market_move):
        score += clamp(market_move, -4, 4) * 4
    if is_finite(earnings):
        score += clamp(earnings / 10, -5, 5) * 2
    if is_finite(advance_percent):
        score += (advance_percent - 50) * 0.18
    return round(clamp(score, 0, 100))


def summarize_moneycontrol_sector(row):
    parts = []
    if row.get("trend"):
        parts.append(f"{row['trend']} trend")
    if is_finite(row.get("marketCapChangePercent")):
        direction = "up" if row["marketCapChangePercent"] >= 0 else "down"
        parts.append(f"market cap {direction} {abs(row['marketCapChangePercent']):.2f}%")
    if row.get("advance") is not None and row.get("decline") is not None:
        parts.append(f"A/D {row['advance']}/{row['decline']}")
    if is_finite(row.get("earningsYoyChange")):
        parts.append(f"earnings YoY {row['earningsYoyChange']:.2f}%")
    return ", ".join(parts) + "." if parts else "Sector data available from Moneycontrol."


def moneycontrol_timestamp(value):
    text = nse_text(value)
    if re.fullmatch(r"\d{14}", text):
        return f"{text[6:8]}-{text[4:6]}-{text[:4]} {text[8:10]}:{text[10:12]} IST"
    return text


def stock_sector_analysis(fundamentals):
    snapshot = safe_moneycontrol_sector_snapshot()
    if not snapshot.get("available"):
        return {
            "available": False,
            "source": snapshot.get("source") or "Moneycontrol sector analysis",
            "url": MONEYCONTROL_SECTOR_URL,
            "error": snapshot.get("error") or "Moneycontrol sector data unavailable.",
        }
    match = match_moneycontrol_sector(fundamentals, snapshot.get("sectors") or [])
    if not match:
        return {
            "available": False,
            "source": snapshot.get("source"),
            "url": snapshot.get("url") or MONEYCONTROL_SECTOR_URL,
            "error": "No Moneycontrol sector match was found for this stock sector.",
        }
    return {
        "available": True,
        "source": snapshot.get("source"),
        "url": match.get("url") or snapshot.get("url") or MONEYCONTROL_SECTOR_URL,
        "stockSector": fundamentals.get("sector") or "",
        "stockIndustry": fundamentals.get("industry") or "",
        "matchedSector": match,
        "breadth": snapshot.get("breadth") or {},
        "rank": sector_rank(match, snapshot.get("sectors") or []),
        "summary": f"Matched {match['sector']} from Moneycontrol: {match.get('summary')}",
    }


def match_moneycontrol_sector(fundamentals, sectors):
    candidates = [
        fundamentals.get("sector") or "",
        fundamentals.get("industry") or "",
    ]
    aliases = [
        SECTOR_ALIASES.get(candidate)
        for candidate in candidates
        if SECTOR_ALIASES.get(candidate)
    ]
    keys = [sector_match_key(value) for value in [*aliases, *candidates] if value]
    for row in sectors:
        sector_key = sector_match_key(row.get("sector"))
        if sector_key in keys:
            return row
    for key in keys:
        for row in sectors:
            sector_key = sector_match_key(row.get("sector"))
            if key and (key in sector_key or sector_key in key):
                return row
    return None


def sector_match_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def sector_rank(match, sectors):
    ranked = sorted(
        sectors,
        key=lambda row: row.get("score") if is_finite(row.get("score")) else -1,
        reverse=True,
    )
    for index, row in enumerate(ranked, start=1):
        if row.get("sector") == match.get("sector"):
            return index
    return None


def build_market_monitor():
    with ThreadPoolExecutor(max_workers=6) as executor:
        nse_snapshot_future = executor.submit(safe_nse_market_snapshot)
        moneycontrol_sectors_future = executor.submit(safe_moneycontrol_sector_snapshot)
        sector_open_interest_future = executor.submit(safe_sector_open_interest)
        breakout_universe_future = executor.submit(safe_nifty500_primary_universe)
        usd_inr_future = executor.submit(safe_usd_inr_snapshot)
        commodity_results_future = executor.submit(
            settle_map,
            COMMODITIES,
            get_commodity_snapshot,
            3,
        )

        nse_snapshot = nse_snapshot_future.result()
        moneycontrol_sectors = moneycontrol_sectors_future.result()
        sector_open_interest = sector_open_interest_future.result()
        breakout_universe = breakout_universe_future.result()
        usd_inr = usd_inr_future.result()
        commodity_results = commodity_results_future.result()

    moneycontrol_sectors = attach_sector_top_stocks(
        moneycontrol_sectors,
        sector_open_interest.get("stockMoversBySector") or {},
    )
    moneycontrol_sectors = {
        **moneycontrol_sectors,
        "sectorOpenInterest": sector_open_interest,
    }
    sector_stock_performance = build_sector_stock_performance(
        nse_snapshot,
        sector_open_interest.get("sectorPerformance") or {},
    )

    primary_scan_universe = primary_scan_candidates_from_nifty500(breakout_universe)
    activity_universe = market_activity_universe()
    with ThreadPoolExecutor(max_workers=3) as executor:
        scan_results_future = executor.submit(
            settle_map,
            primary_scan_universe,
            scan_watchlist_stock,
            MARKET_MONITOR_PRIMARY_CONCURRENCY,
        )
        activity_results_future = executor.submit(
            settle_map,
            activity_universe,
            scan_high_activity_stock,
            MARKET_MONITOR_ACTIVITY_CONCURRENCY,
        )
        catalyst_results_future = executor.submit(
            settle_map,
            ORDER_CATALYST_WATCHLIST,
            scan_order_catalyst_stock,
            MARKET_MONITOR_CATALYST_CONCURRENCY,
        )

        scan_results = scan_results_future.result()
        activity_results = activity_results_future.result()
        catalyst_results = catalyst_results_future.result()

    commodity_snapshots = [
        commodity_with_inr(value, usd_inr)
        for ok, value in commodity_results
        if ok
    ]
    scanned_stocks = [value for ok, value in scan_results if ok]
    activity_stocks = [value for ok, value in activity_results if ok]
    candidates = primary_opportunity_candidates(scanned_stocks)
    high_volume_candidates = sorted(
        [stock for stock in activity_stocks if is_high_volume_candidate(stock)],
        key=lambda item: item["score"],
        reverse=True,
    )[:14]
    activity_by_symbol = {stock["symbol"]: stock for stock in activity_stocks}
    order_catalysts = build_order_catalysts(
        [value for ok, value in catalyst_results if ok],
        activity_by_symbol,
    )
    impacted = build_commodity_impact(commodity_snapshots, scanned_stocks)

    return {
        "generatedAt": iso_now(),
        "source": "NSE India public market APIs, Moneycontrol sector analysis, Yahoo Finance public endpoints, and headline scan",
        "note": "NSE snapshot uses public NSE website endpoints; sector analysis uses Moneycontrol's sector-analysis page, and sector-wise OI maps NSE Change in OI symbols into NSE sectoral indices. Primary 50 stays Nifty 500-only: it ranks current Nifty 500 constituents with NSE price, volume, and 52-week range data, then chart-scans the strongest candidates. NR4/NR7 breakout means price is breaking above a tight prior 4-day or 7-day range near the 52-week high. Confirm liquidity, headlines, and levels on your broker or exchange feed before acting.",
        "nseSnapshot": nse_snapshot,
        "moneycontrolSectorAnalysis": moneycontrol_sectors,
        "sectorStockPerformance": sector_stock_performance,
        "commodities": commodity_snapshots,
        "usdInr": usd_inr,
        "breakoutCandidates": candidates,
        "highVolumeCandidates": high_volume_candidates,
        "orderCatalysts": order_catalysts,
        "impacted": impacted,
        "scannedCount": len(scanned_stocks),
        "primaryScanUniverse": {
            "label": "Nifty 500 only",
            "count": len(breakout_universe),
            "scanned": len(primary_scan_universe),
            "scanLimit": NIFTY_500_PRIMARY_SCAN_LIMIT,
            "available": bool(breakout_universe),
        },
        "activityScannedCount": len(activity_stocks),
        "catalystScannedCount": len([value for ok, value in catalyst_results if ok]),
    }


def build_live_market_monitor(base_payload=None, refreshing=False):
    nse_snapshot = safe_nse_market_snapshot(
        cache_seconds=LIVE_NSE_MARKET_SNAPSHOT_CACHE_SECONDS,
        cache_key="nse-market-snapshot-live",
    )
    detail_payload = base_payload if isinstance(base_payload, dict) else {}
    detail_generated_at = detail_payload.get("detailGeneratedAt") or detail_payload.get("generatedAt") or ""

    payload = {
        **empty_market_monitor_details(refreshing),
        **detail_payload,
        "generatedAt": iso_now(),
        "source": "Live NSE snapshot with cached detailed monitor scan",
        "note": live_market_monitor_note(detail_payload, refreshing),
        "refreshing": refreshing,
        "liveMode": True,
        "detailGeneratedAt": detail_generated_at,
        "nseSnapshot": nse_snapshot,
    }
    if not detail_payload:
        payload["fastMode"] = True
    return payload


def empty_market_monitor_details(refreshing=False):
    loading_text = "Detailed monitor scan is running; this section will fill as provider data returns."
    unavailable_text = "Detailed monitor data has not loaded yet."
    summary = loading_text if refreshing else unavailable_text
    return {
        "fastMode": True,
        "moneycontrolSectorAnalysis": {
            "available": False,
            "loading": refreshing,
            "summary": summary,
            "sectors": [],
            "topPerforming": [],
            "underPerforming": [],
            "sectorIndices": [],
            "sectorOpenInterest": {
                "available": False,
                "loading": refreshing,
                "summary": summary,
                "stockMoversBySector": {},
            },
        },
        "sectorStockPerformance": {
            "available": False,
            "loading": refreshing,
            "source": "NSE India sectoral index constituents",
            "summary": summary,
            "rows": [],
        },
        "commodities": [],
        "usdInr": {"available": False, "loading": refreshing},
        "breakoutCandidates": [],
        "highVolumeCandidates": [],
        "orderCatalysts": [],
        "impacted": [],
        "scannedCount": 0,
        "primaryScanUniverse": {
            "label": "Nifty 500 only",
            "count": 0,
            "scanned": 0,
            "scanLimit": NIFTY_500_PRIMARY_SCAN_LIMIT,
            "available": False,
        },
        "activityScannedCount": 0,
        "catalystScannedCount": 0,
    }


def live_market_monitor_note(detail_payload, refreshing):
    if detail_payload and refreshing:
        detail_time = detail_payload.get("generatedAt")
        detail_suffix = f" from {detail_time}" if detail_time else ""
        return (
            "Live NSE snapshot refreshes every second in the browser. "
            f"Showing the last detailed scan{detail_suffix} "
            "while a fresh full scan runs in the background."
        )
    if detail_payload:
        return (
            "Live NSE snapshot refreshes every second in the browser. "
            "Detailed scan sections are cached because they use slower public provider endpoints."
        )
    return (
        "Live NSE snapshot is available now. Detailed sections are loading in the background and will appear "
        "without clearing the dashboard."
    )


def build_fast_market_monitor(refreshing=True):
    with ThreadPoolExecutor(max_workers=2) as executor:
        nse_snapshot_future = executor.submit(safe_nse_market_snapshot)
        universe_future = executor.submit(safe_nifty500_primary_universe)
        nse_snapshot = nse_snapshot_future.result()
        breakout_universe = universe_future.result()

    primary_scan_universe = primary_scan_candidates_from_nifty500(breakout_universe)
    candidates = [
        fast_primary_candidate(stock)
        for stock in primary_scan_universe[:50]
    ]

    return {
        "generatedAt": iso_now(),
        "source": "NSE India public market APIs with background chart scan",
        "note": "Fast mode ranks Nifty 500 constituents from NSE price, volume, and 52-week range data while the full chart scan refreshes in the background.",
        "refreshing": refreshing,
        "fastMode": True,
        "nseSnapshot": nse_snapshot,
        "moneycontrolSectorAnalysis": {
            "available": False,
            "sectors": [],
            "topPerforming": [],
            "underPerforming": [],
            "sectorOpenInterest": {"available": False, "stockMoversBySector": {}},
        },
        "sectorStockPerformance": {
            "available": False,
            "source": "NSE India sectoral index constituents",
            "summary": "Sector stock performance will load after the full monitor refresh completes.",
            "rows": [],
        },
        "commodities": [],
        "usdInr": {"available": False},
        "breakoutCandidates": candidates,
        "highVolumeCandidates": [],
        "orderCatalysts": [],
        "impacted": [],
        "scannedCount": len(candidates),
        "primaryScanUniverse": {
            "label": "Nifty 500 only",
            "count": len(breakout_universe),
            "scanned": len(primary_scan_universe),
            "scanLimit": NIFTY_500_PRIMARY_SCAN_LIMIT,
            "available": bool(breakout_universe),
        },
        "activityScannedCount": 0,
        "catalystScannedCount": 0,
    }


def fast_primary_candidate(stock):
    price = stock.get("nsePrice")
    year_high = stock.get("nseYearHigh")
    year_low = stock.get("nseYearLow")
    change_percent = stock.get("nseChangePercent")
    pct_below_high = (
        safe_divide(year_high - price, year_high) * 100
        if is_finite(price) and is_finite(year_high) and year_high > 0
        else None
    )
    pct_above_low = (
        safe_divide(price - year_low, year_low) * 100
        if is_finite(price) and is_finite(year_low) and year_low > 0
        else None
    )
    score = round(clamp(primary_scan_prefilter_score(stock), 0, 100))

    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "tags": [*stock.get("tags", []), "Fast rank"],
        "price": round_or_none(price),
        "changePercent": round_or_none(change_percent),
        "availableHigh": round_or_none(year_high),
        "availableHighDate": "",
        "high52Week": round_or_none(year_high),
        "pctBelowAvailableHigh": round_or_none(pct_below_high),
        "pctBelow52WeekHigh": round_or_none(pct_below_high),
        "pctAbovePrior20Low": None,
        "drawdownFromRecentHigh": None,
        "prior5High": None,
        "prior20High": None,
        "prior55High": None,
        "prior20Low": round_or_none(year_low),
        "sma20": None,
        "sma50": None,
        "rsi14": None,
        "breakout": False,
        "breakoutWatch": is_finite(pct_below_high) and pct_below_high <= 5,
        "bullishReversal": False,
        "trendReversal": False,
        "reversalWatch": is_finite(pct_above_low) and pct_above_low <= 20 and (change_percent or 0) > 0,
        "nearAvailableHigh": is_finite(pct_below_high) and pct_below_high <= 3,
        "near52WeekHigh": is_finite(pct_below_high) and pct_below_high <= 3,
        "near52WeekSetup": is_finite(pct_below_high) and pct_below_high <= 5,
        "narrowRange4": {},
        "narrowRange7": {},
        "narrowRange4Breakout": False,
        "narrowRange7Breakout": False,
        "narrowRange4Watch": False,
        "narrowRange7Watch": False,
        "atr": None,
        "volumeRatio": None,
        "oneMonth": None,
        "score": score,
        "signal": describe_fast_primary_signal(pct_below_high, change_percent),
    }


def describe_fast_primary_signal(pct_below_high, change_percent):
    if is_finite(pct_below_high) and pct_below_high <= 3:
        return "Near 52-week high; chart scan pending"
    if is_finite(change_percent) and change_percent > 2:
        return "Nifty 500 momentum candidate; chart scan pending"
    return "Nifty 500 ranked candidate; chart scan pending"


def start_market_monitor_refresh():
    global _market_monitor_refreshing
    with _market_monitor_refresh_lock:
        if _market_monitor_refreshing:
            return False
        _market_monitor_refreshing = True

    thread = threading.Thread(target=refresh_market_monitor_cache, daemon=True)
    thread.start()
    return True


def is_market_monitor_refreshing():
    return _market_monitor_refreshing


def refresh_market_monitor_cache():
    global _market_monitor_refreshing
    try:
        set_cached("market-monitor", build_market_monitor(), MARKET_MONITOR_CACHE_SECONDS)
    finally:
        with _market_monitor_refresh_lock:
            _market_monitor_refreshing = False


def get_recommendations():
    return cached(recommendation_cache_key(), build_recommendations, RECOMMENDATION_CACHE_SECONDS)


def recommendation_cache_key(now=None):
    value = now or datetime.now(ZoneInfo("Asia/Kolkata"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return f"recommendations:{value.astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()}"


def clear_recommendations_cache():
    clear_cache_prefix("recommendations")


def build_recommendations():
    universe = recommendation_universe()
    analyst_results = settle_map(universe, build_recommendation_candidate, 4)
    analyst_rows = [
        value
        for ok, value in analyst_results
        if ok and value
    ]
    daily = safe_daily_recommendations()
    daily_rows = daily.get("results") or []
    rows = merge_recommendation_rows([*analyst_rows, *daily_rows])
    rows = sorted(
        rows,
        key=lambda item: (
            item.get("score") or 0,
            item.get("upsidePercent") if is_finite(item.get("upsidePercent")) else -1000,
        ),
        reverse=True,
    )[:RECOMMENDATION_RESULT_LIMIT]
    intraday = safe_intraday_recommendations()

    return {
        "generatedAt": iso_now(),
        "source": "Yahoo Finance analyst consensus/actions, public shareholding data, NSE Nifty 500 price/volume scan, and Yahoo/NSE prices",
        "note": (
            "Rows qualify when public analyst data is constructive, FIIs/DIIs have accumulated meaningfully, "
            "or the daily Nifty 500 scan finds a price/volume technical setup. Buy, sell, and stop levels are "
            "generated from the latest public price/chart data and must be verified before trading."
        ),
        "summary": summarize_recommendations(rows),
        "scannedCount": len(universe) + (daily.get("scannedCount") or 0),
        "results": rows,
        "daily": daily,
        "intraday": intraday,
    }


def build_intraday_recommendations():
    universe = intraday_recommendation_universe()
    results = settle_map(
        universe,
        build_intraday_recommendation_candidate,
        RECOMMENDATION_PROVIDER_CONCURRENCY,
    )
    failed_count = sum(1 for ok, _value in results if not ok)
    rows = [
        value
        for ok, value in results
        if ok and value
    ]
    rows = sorted(
        rows,
        key=lambda item: (
            item.get("score") or 0,
            item.get("volumeRatio") if is_finite(item.get("volumeRatio")) else 0,
            abs(item.get("changePercent") or 0),
        ),
        reverse=True,
    )[:INTRADAY_RECOMMENDATION_RESULT_LIMIT]

    return {
        "source": "NSE Nifty 500 snapshot fields and Yahoo Finance daily candles",
        "note": intraday_recommendation_note(failed_count),
        "summary": summarize_intraday_recommendations(rows, failed_count),
        "scannedCount": len(universe),
        "failedCount": failed_count,
        "results": rows,
    }


def safe_intraday_recommendations():
    try:
        return build_intraday_recommendations()
    except Exception as error:
        return empty_intraday_recommendations(str(error))


def empty_intraday_recommendations(error=""):
    return {
        "source": "NSE Nifty 500 snapshot fields and Yahoo Finance daily candles",
        "note": (
            "Intraday scan could not be completed from public provider data. "
            "The long-horizon recommendation list remains available."
        ),
        "summary": "Intraday candidates are temporarily unavailable; refresh after provider data returns.",
        "scannedCount": 0,
        "failedCount": 0,
        "results": [],
        "error": error,
    }


def intraday_recommendation_note(failed_count=0):
    skipped = f" {failed_count} symbols could not be checked from public data." if failed_count else ""
    return (
        "Intraday rows are day-trade watchlist candidates, not guaranteed trades. "
        "The app uses NSE snapshot fields plus daily candles, so confirm live minute-chart structure, "
        "news, F&O/OI, actual delivery data, and liquidity in your broker terminal before acting."
        f"{skipped}"
    )


def intraday_recommendation_universe():
    stocks = []
    nse_universe = safe_nifty500_primary_universe()
    if nse_universe:
        stocks.extend(
            sorted(
                nse_universe,
                key=intraday_scan_prefilter_score,
                reverse=True,
            )[:INTRADAY_RECOMMENDATION_SCAN_LIMIT]
        )

    stocks.extend([
        {"symbol": symbol, "name": name, "tags": list(tags)}
        for symbol, name, tags in RECOMMENDATION_FALLBACK_UNIVERSE
    ])

    by_symbol = {}
    for stock in stocks:
        symbol = normalize_symbol(stock.get("symbol"))
        if not symbol:
            continue
        by_symbol.setdefault(symbol, {**stock, "symbol": symbol})
    return list(by_symbol.values())[:INTRADAY_RECOMMENDATION_SCAN_LIMIT]


def intraday_scan_prefilter_score(stock):
    price = stock.get("nsePrice")
    year_high = stock.get("nseYearHigh")
    year_low = stock.get("nseYearLow")
    change_percent = stock.get("nseChangePercent")
    volume = stock.get("nseVolume")
    traded_value = stock.get("nseValue")
    range_position = (
        safe_divide(price - year_low, year_high - year_low) * 100
        if is_finite(price)
        and is_finite(year_high)
        and is_finite(year_low)
        and year_high > year_low
        else None
    )
    pct_below_high = (
        safe_divide(year_high - price, year_high) * 100
        if is_finite(price) and is_finite(year_high) and year_high > 0
        else None
    )
    pct_above_low = (
        safe_divide(price - year_low, year_low) * 100
        if is_finite(price) and is_finite(year_low) and year_low > 0
        else None
    )

    score = 0
    if is_finite(change_percent):
        score += clamp(abs(change_percent) * 5, 0, 30)
        if change_percent > 0 and is_finite(pct_below_high) and pct_below_high <= 8:
            score += 16
        if change_percent < 0 and (
            (is_finite(range_position) and range_position <= 35)
            or (is_finite(pct_above_low) and pct_above_low <= 18)
        ):
            score += 16
    if is_finite(volume) and volume > 0:
        score += min(math.log10(volume + 1) * 2.2, 16)
    if is_finite(traded_value) and traded_value > 0:
        score += min(math.log10(traded_value + 1), 10)
    if is_finite(range_position) and (range_position >= 78 or range_position <= 28):
        score += 10
    return score


def build_intraday_recommendation_candidate(stock):
    symbol = normalize_symbol(stock.get("symbol"))
    if not symbol:
        return None
    chart = get_chart_range(symbol, "6mo", "1d")
    return build_intraday_recommendation_from_inputs(stock, chart.get("candles") or [])


def build_intraday_recommendation_from_inputs(stock, candles):
    symbol = normalize_symbol(stock.get("symbol"))
    if not symbol:
        return None

    candles = usable_intraday_candles(candles)
    if len(candles) < 40:
        return None

    candles = candles[-126:]
    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    lows = [candle["low"] for candle in candles]
    volumes = [candle.get("volume") or 0 for candle in candles]
    latest = candles[-1]
    previous = candles[-2]
    current = finite_or(stock.get("nsePrice"), latest["close"])
    previous_close = previous["close"]
    if not is_finite(current) or current <= 0 or not is_finite(previous_close) or previous_close <= 0:
        return None

    change_percent = finite_or(
        stock.get("nseChangePercent"),
        safe_divide(current - previous_close, previous_close) * 100,
        0,
    )
    volume = finite_or(stock.get("nseVolume"), latest.get("volume"), 0) or 0
    prior_volumes = volumes[:-1]
    avg_volume_20 = average(prior_volumes[-20:]) or last(sma(volumes, 20))
    avg_volume_50 = average(prior_volumes[-50:]) or last(sma(volumes, 50)) or avg_volume_20
    volume_ratio = safe_divide(volume, avg_volume_20) if avg_volume_20 else None
    delivery_proxy = safe_divide(volume, avg_volume_50) if avg_volume_50 else None
    liquidity_value = current * volume
    atr_value = last(atr(candles, 14)) or current * 0.02
    atr_percent = safe_divide(atr_value, current) * 100
    sma20_value = last(sma(closes, 20))
    sma50_value = last(sma(closes, 50))
    rsi_value = last(rsi(closes, 14))
    bands = bollinger(closes, 20, 2)
    bollinger_widths = [
        safe_divide(upper - lower, middle) * 100
        for upper, lower, middle in zip(bands["upper"], bands["lower"], bands["middle"])
        if is_finite(upper) and is_finite(lower) and is_finite(middle) and middle
    ]
    bollinger_width = bollinger_widths[-1] if bollinger_widths else None
    average_bollinger_width = average(bollinger_widths[-60:]) if bollinger_widths else None
    volatility_tight = (
        is_finite(bollinger_width)
        and (
            bollinger_width <= 6
            or (
                is_finite(average_bollinger_width)
                and average_bollinger_width > 0
                and bollinger_width <= average_bollinger_width * 0.78
            )
        )
    )

    prior_5_high = max(highs[-6:-1])
    prior_5_low = min(lows[-6:-1])
    prior_20_high = max(highs[-21:-1])
    prior_20_low = min(lows[-21:-1])
    high_52 = finite_or(stock.get("nseYearHigh"), max(highs))
    low_52 = finite_or(stock.get("nseYearLow"), min(lows))
    pct_below_52_high = safe_divide(high_52 - current, high_52) * 100 if high_52 else None
    pct_above_52_low = safe_divide(current - low_52, low_52) * 100 if low_52 else None
    above_sma20 = is_finite(sma20_value) and current > sma20_value
    below_sma20 = is_finite(sma20_value) and current < sma20_value
    above_sma50 = is_finite(sma50_value) and current > sma50_value
    below_sma50 = is_finite(sma50_value) and current < sma50_value
    volume_spike = is_finite(volume_ratio) and volume_ratio >= 1.8
    participation_spike = is_finite(delivery_proxy) and delivery_proxy >= 1.35
    near_high = is_finite(pct_below_52_high) and 0 <= pct_below_52_high <= 6
    near_low = is_finite(pct_above_52_low) and 0 <= pct_above_52_low <= 12
    long_breakout = current > prior_20_high and previous_close <= prior_20_high
    short_breakdown = current < prior_20_low and previous_close >= prior_20_low
    long_watch = current >= prior_5_high * 0.995 or current >= prior_20_high * 0.99
    short_watch = current <= prior_5_low * 1.005 or current <= prior_20_low * 1.01

    long_score = intraday_direction_score(
        direction="Long",
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        delivery_proxy=delivery_proxy,
        liquidity_value=liquidity_value,
        near_extreme=near_high,
        breakout=long_breakout,
        watch=long_watch,
        trend_ok=above_sma20 or above_sma50,
        volatility_tight=volatility_tight,
        rsi_value=rsi_value,
    )
    short_score = intraday_direction_score(
        direction="Short",
        change_percent=change_percent,
        volume_ratio=volume_ratio,
        delivery_proxy=delivery_proxy,
        liquidity_value=liquidity_value,
        near_extreme=near_low,
        breakout=short_breakdown,
        watch=short_watch,
        trend_ok=below_sma20 or below_sma50,
        volatility_tight=volatility_tight,
        rsi_value=rsi_value,
    )

    direction = "Long" if long_score >= short_score else "Short"
    score = max(long_score, short_score)
    if score < 58:
        return None
    if not volume_spike and not participation_spike:
        return None
    if direction == "Long" and not (change_percent > 0 and (long_breakout or long_watch or near_high or volatility_tight)):
        return None
    if direction == "Short" and not (change_percent < 0 and (short_breakdown or short_watch or near_low or volatility_tight)):
        return None

    expected_move = estimate_intraday_move_percent(atr_percent, volume_ratio, change_percent, score)
    if expected_move < INTRADAY_MIN_EXPECTED_MOVE_PERCENT:
        return None

    if direction == "Long":
        trigger_price = max(current, prior_5_high if long_watch and not long_breakout else current)
        target_near = current * (1 + INTRADAY_MIN_EXPECTED_MOVE_PERCENT / 100)
        target_far = current * (1 + expected_move / 100)
        stop_loss = current - max(atr_value * 0.65, current * 0.012)
        setup = intraday_setup_label("Long", long_breakout, near_high, volatility_tight, volume_spike)
    else:
        trigger_price = min(current, prior_5_low if short_watch and not short_breakdown else current)
        target_near = current * (1 - INTRADAY_MIN_EXPECTED_MOVE_PERCENT / 100)
        target_far = current * (1 - expected_move / 100)
        stop_loss = current + max(atr_value * 0.65, current * 0.012)
        setup = intraday_setup_label("Short", short_breakdown, near_low, volatility_tight, volume_spike)

    risk_percent = abs(safe_divide(current - stop_loss, current) * 100)
    reasons = intraday_reasons(
        direction,
        volume_ratio,
        delivery_proxy,
        change_percent,
        long_breakout if direction == "Long" else short_breakdown,
        near_high if direction == "Long" else near_low,
        volatility_tight,
        rsi_value,
        atr_percent,
    )

    return {
        "symbol": symbol,
        "analysisSymbol": symbol,
        "name": first_present(stock.get("name"), symbol),
        "direction": direction,
        "bias": "Up" if direction == "Long" else "Down",
        "setup": setup,
        "score": round(clamp(score, 0, 100)),
        "price": round_or_none(current),
        "changePercent": round_or_none(change_percent),
        "triggerPrice": round_or_none(trigger_price),
        "targetNear": round_or_none(target_near),
        "targetFar": round_or_none(target_far),
        "stopLoss": round_or_none(stop_loss),
        "riskPercent": round_or_none(risk_percent),
        "expectedMovePercent": round_or_none(expected_move),
        "expectedMoveRange": {
            "low": INTRADAY_MIN_EXPECTED_MOVE_PERCENT,
            "high": round_or_none(expected_move),
        },
        "volume": round(volume),
        "avgVolume20": round(avg_volume_20 or 0),
        "volumeRatio": round_or_none(volume_ratio),
        "deliveryProxy": round_or_none(delivery_proxy),
        "liquidityValue": round_or_none(liquidity_value),
        "atrPercent": round_or_none(atr_percent),
        "rsi14": round_or_none(rsi_value),
        "bollingerWidthPercent": round_or_none(bollinger_width),
        "levels": {
            "prior5High": round_or_none(prior_5_high),
            "prior5Low": round_or_none(prior_5_low),
            "prior20High": round_or_none(prior_20_high),
            "prior20Low": round_or_none(prior_20_low),
            "sma20": round_or_none(sma20_value),
            "sma50": round_or_none(sma50_value),
            "pctBelow52WeekHigh": round_or_none(pct_below_52_high),
            "pctAbove52WeekLow": round_or_none(pct_above_52_low),
        },
        "tags": stock.get("tags") or [],
        "reason": " ".join(reasons),
    }


def intraday_direction_score(
    direction,
    change_percent,
    volume_ratio,
    delivery_proxy,
    liquidity_value,
    near_extreme,
    breakout,
    watch,
    trend_ok,
    volatility_tight,
    rsi_value,
):
    signed_change = finite_or(change_percent, 0)
    if direction == "Short":
        signed_change = -signed_change
    rsi_ok = (
        not is_finite(rsi_value)
        or (direction == "Long" and 48 <= rsi_value <= 76)
        or (direction == "Short" and 24 <= rsi_value <= 52)
    )
    score = 22
    score += clamp(signed_change * 7, -22, 28)
    score += clamp(((volume_ratio or 0) - 1) * 18, 0, 34)
    score += clamp(((delivery_proxy or 0) - 1) * 9, 0, 16)
    score += 14 if breakout else 0
    score += 9 if watch else 0
    score += 8 if near_extreme else 0
    score += 8 if trend_ok else 0
    score += 6 if volatility_tight else 0
    score += 5 if rsi_ok else -6
    score += 8 if is_finite(liquidity_value) and liquidity_value >= 20_00_00_000 else 0
    return round(clamp(score, 0, 100))


def usable_intraday_candles(candles):
    usable = []
    for candle in candles or []:
        if not isinstance(candle, dict):
            continue
        open_price = finite_or(candle.get("open"))
        high = finite_or(candle.get("high"))
        low = finite_or(candle.get("low"))
        close = finite_or(candle.get("close"))
        if not all(is_finite(value) for value in (open_price, high, low, close)):
            continue
        if high < low:
            continue
        volume = finite_or(candle.get("volume"), 0)
        usable.append({
            **candle,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return usable


def estimate_intraday_move_percent(atr_percent, volume_ratio, change_percent, score):
    base = max(atr_percent or 0, abs(change_percent or 0) * 0.55)
    volume_boost = clamp((volume_ratio or 0) - 1, 0, 3) * 0.45
    score_boost = clamp(score - 58, 0, 30) * 0.035
    return round2(clamp(base + volume_boost + score_boost, INTRADAY_MIN_EXPECTED_MOVE_PERCENT, INTRADAY_MAX_EXPECTED_MOVE_PERCENT))


def intraday_setup_label(direction, breakout, near_extreme, volatility_tight, volume_spike):
    if direction == "Long":
        if breakout and volume_spike:
            return "Volume breakout long"
        if near_extreme and volume_spike:
            return "52-week high momentum long"
        if volatility_tight:
            return "Coiled range long watch"
        return "Upside momentum long"
    if breakout and volume_spike:
        return "Volume breakdown short"
    if near_extreme and volume_spike:
        return "52-week low pressure short"
    if volatility_tight:
        return "Coiled range short watch"
    return "Downside momentum short"


def intraday_reasons(direction, volume_ratio, delivery_proxy, change_percent, breakout, near_extreme, volatility_tight, rsi_value, atr_percent):
    reasons = []
    if is_finite(volume_ratio):
        reasons.append(f"Volume is {volume_ratio:.2f}x the 20-day average.")
    if is_finite(delivery_proxy):
        reasons.append(f"Participation proxy is {delivery_proxy:.2f}x the 50-day average.")
    if is_finite(change_percent):
        direction_word = "up" if direction == "Long" else "down"
        reasons.append(f"Price is {direction_word} {abs(change_percent):.2f}% on the latest snapshot.")
    if breakout:
        reasons.append("Price has cleared the key short-term range." if direction == "Long" else "Price has broken below the key short-term range.")
    elif near_extreme:
        reasons.append("Price is close to a 52-week high/low zone.")
    if volatility_tight:
        reasons.append("Bollinger bandwidth shows a compressed range before expansion.")
    if is_finite(rsi_value):
        reasons.append(f"RSI is {rsi_value:.1f}.")
    if is_finite(atr_percent):
        reasons.append(f"ATR implies roughly {atr_percent:.2f}% daily movement.")
    return reasons


def summarize_intraday_recommendations(rows, failed_count=0):
    skipped = f" {failed_count} symbols could not be checked." if failed_count else ""
    if not rows:
        return f"No intraday candidates passed the 3-5% move, volume, momentum, and structure filters.{skipped}"
    long_count = sum(1 for row in rows if row.get("direction") == "Long")
    short_count = sum(1 for row in rows if row.get("direction") == "Short")
    top = rows[0]
    return (
        f"{len(rows)} intraday watchlist candidates: {long_count} long-biased and {short_count} short-biased. "
        f"Top setup: {top.get('symbol')} {top.get('direction')} at {top.get('score')}/100."
        f"{skipped}"
    )


def safe_daily_recommendations():
    try:
        return build_daily_recommendations()
    except Exception as error:
        return empty_daily_recommendations(str(error))


def empty_daily_recommendations(error=""):
    return {
        "source": "NSE Nifty 500 snapshot fields and Yahoo Finance daily candles",
        "note": "Daily Nifty 500 short-term scan could not be completed from public provider data.",
        "summary": "Daily short-term candidates are temporarily unavailable; refresh after provider data returns.",
        "scannedCount": 0,
        "failedCount": 0,
        "results": [],
        "error": error,
    }


def build_daily_recommendations():
    universe = daily_recommendation_universe()
    results = settle_map(
        universe,
        build_daily_recommendation_candidate,
        RECOMMENDATION_PROVIDER_CONCURRENCY,
    )
    failed_count = sum(1 for ok, _value in results if not ok)
    rows = [
        value
        for ok, value in results
        if ok and value
    ]
    rows = sorted(
        rows,
        key=lambda item: (
            item.get("score") or 0,
            item.get("upsidePercent") if is_finite(item.get("upsidePercent")) else 0,
            item.get("volumeRatio") if is_finite(item.get("volumeRatio")) else 0,
        ),
        reverse=True,
    )[:DAILY_RECOMMENDATION_RESULT_LIMIT]

    return {
        "source": "NSE Nifty 500 snapshot fields and Yahoo Finance daily candles",
        "note": daily_recommendation_note(failed_count),
        "summary": summarize_daily_recommendations(rows, failed_count),
        "scannedCount": len(universe),
        "failedCount": failed_count,
        "results": rows,
    }


def daily_recommendation_note(failed_count=0):
    skipped = f" {failed_count} symbols could not be checked from public data." if failed_count else ""
    return (
        "Daily rows are short-term technical watchlist candidates from Nifty 500 price/volume action, "
        "not analyst recommendations. Confirm live chart, liquidity, news, and broad-market context before trading."
        f"{skipped}"
    )


def daily_recommendation_universe():
    stocks = []
    nse_universe = safe_nifty500_primary_universe()
    if nse_universe:
        stocks.extend(primary_scan_candidates_from_nifty500(nse_universe)[:DAILY_RECOMMENDATION_SCAN_LIMIT])
    stocks.extend([
        {"symbol": symbol, "name": name, "tags": list(tags)}
        for symbol, name, tags in RECOMMENDATION_FALLBACK_UNIVERSE
    ])

    by_symbol = {}
    for stock in stocks:
        symbol = normalize_symbol(stock.get("symbol"))
        if not symbol:
            continue
        by_symbol.setdefault(symbol, {**stock, "symbol": symbol})
    return list(by_symbol.values())[:DAILY_RECOMMENDATION_SCAN_LIMIT]


def build_daily_recommendation_candidate(stock):
    scanned = scan_watchlist_stock(stock)
    return build_daily_recommendation_from_scan(scanned)


def build_daily_recommendation_from_scan(stock):
    if not is_daily_recommendation_candidate(stock):
        return None
    symbol = normalize_symbol(stock.get("symbol"))
    current = stock.get("price")
    if not symbol or not is_finite(current) or current <= 0:
        return None

    atr_value = stock.get("atr") if is_finite(stock.get("atr")) and stock.get("atr") > 0 else current * 0.025
    entry_high = round2(current)
    entry_low = round2(max(current - atr_value * 0.45, current * 0.97))
    target_percent = daily_target_percent(stock)
    sell_price = round2(current * (1 + target_percent / 100))
    stop_loss = round2(current - max(atr_value * 1.15, current * 0.035))
    risk_percent = safe_divide(entry_high - stop_loss, entry_high) * 100
    score = daily_recommendation_score(
        stock.get("changePercent") or 0,
        stock.get("volumeRatio"),
        stock.get("oneWeek"),
        stock.get("oneMonth"),
        stock.get("pctBelow52WeekHigh"),
        bool(stock.get("breakout") or stock.get("narrowRange4Breakout") or stock.get("narrowRange7Breakout")),
        bool(stock.get("breakoutWatch") or stock.get("narrowRange4Watch") or stock.get("narrowRange7Watch")),
        bool(stock.get("bullishReversal") or stock.get("trendReversal") or stock.get("reversalWatch")),
        stock.get("rsi14"),
    )

    return {
        "symbol": symbol,
        "analysisSymbol": symbol,
        "name": first_present(stock.get("name"), symbol),
        "recommendedBy": "Nifty 500 daily technical scan",
        "recommenderDetails": [{
            "type": "Daily technical scan",
            "name": "Nifty 500",
            "group": "Price/volume/52-week structure",
            "detail": daily_recommendation_detail(stock),
        }],
        "fundGroup": "Price/volume/52-week structure",
        "sourceType": "Daily Nifty 500",
        "sourceDetail": "NSE Nifty 500 snapshot and daily candle scan",
        "buyPrice": entry_high,
        "buyRange": {"low": entry_low, "high": entry_high},
        "sellPrice": sell_price,
        "stopLoss": stop_loss,
        "duration": "3-15 sessions",
        "currentPrice": round_or_none(current),
        "upsidePercent": round_or_none(target_percent),
        "riskPercent": round_or_none(risk_percent),
        "score": score,
        "analyst": {
            "recommendationMean": None,
            "recommendationKey": "",
            "targetMeanPrice": None,
            "analystCount": None,
        },
        "ownershipSignals": [],
        "quarterlyResults": {
            "available": True,
            "period": "Daily scan",
            "source": "NSE Nifty 500 / Yahoo Finance daily candles",
            "summary": "Tactical daily technical candidate; quarterly result data is not part of this signal.",
        },
        "reason": daily_recommendation_reason_from_scan(stock),
    }


def is_daily_recommendation_candidate(stock):
    score = stock.get("score") or 0
    volume_ratio = stock.get("volumeRatio")
    change_percent = stock.get("changePercent")
    constructive_structure = any([
        stock.get("breakout"),
        stock.get("breakoutWatch"),
        stock.get("narrowRange4Breakout"),
        stock.get("narrowRange7Breakout"),
        stock.get("narrowRange4Watch"),
        stock.get("narrowRange7Watch"),
        stock.get("bullishReversal"),
        stock.get("trendReversal"),
        stock.get("reversalWatch"),
        stock.get("near52WeekHigh"),
    ])
    return (
        score >= 48
        and constructive_structure
        and (not is_finite(change_percent) or change_percent >= -1.5)
        and (
            (is_finite(volume_ratio) and volume_ratio >= 1.05)
            or score >= 65
            or stock.get("breakout")
            or stock.get("narrowRange7Breakout")
        )
    )


def daily_target_percent(stock):
    atr_value = stock.get("atr")
    price = stock.get("price")
    atr_percent = safe_divide(atr_value, price) * 100 if is_finite(atr_value) and is_finite(price) and price > 0 else 2.5
    structure_bonus = 1.5 if stock.get("breakout") or stock.get("narrowRange7Breakout") else 0.75
    volume_bonus = clamp((stock.get("volumeRatio") or 1) - 1, 0, 2) * 0.6
    return clamp(max(4.0, atr_percent * 1.8 + structure_bonus + volume_bonus), 4.0, 12.0)


def daily_recommendation_detail(stock):
    parts = [
        stock.get("signal") or "Daily technical setup",
        f"score {stock.get('score')}/100" if is_finite(stock.get("score")) else "",
        f"volume {stock.get('volumeRatio'):.2f}x 20D avg" if is_finite(stock.get("volumeRatio")) else "",
        f"52W high gap {stock.get('pctBelow52WeekHigh'):.2f}%" if is_finite(stock.get("pctBelow52WeekHigh")) else "",
    ]
    return "; ".join(part for part in parts if part)


def daily_recommendation_reason_from_scan(stock):
    parts = [f"Daily Nifty 500 scan: {stock.get('signal') or 'technical setup'}."]
    if is_finite(stock.get("volumeRatio")):
        parts.append(f"Volume {stock['volumeRatio']:.2f}x 20-day average.")
    if is_finite(stock.get("oneMonth")):
        parts.append(f"One-month momentum {stock['oneMonth']:.2f}%.")
    if stock.get("breakout") or stock.get("narrowRange4Breakout") or stock.get("narrowRange7Breakout"):
        parts.append("Breakout structure is active.")
    elif stock.get("breakoutWatch") or stock.get("narrowRange4Watch") or stock.get("narrowRange7Watch"):
        parts.append("Price is close to a breakout trigger.")
    elif stock.get("bullishReversal") or stock.get("trendReversal") or stock.get("reversalWatch"):
        parts.append("Reversal structure is improving.")
    return " ".join(parts)


def build_daily_recommendation_from_inputs(stock, candles):
    symbol = normalize_symbol(stock.get("symbol"))
    if not symbol:
        return None
    candles = usable_intraday_candles(candles)
    if len(candles) < 55:
        return None

    candles = candles[-126:]
    latest = candles[-1]
    previous = candles[-2]
    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    lows = [candle["low"] for candle in candles]
    volumes = [candle.get("volume") or 0 for candle in candles]
    current = finite_or(stock.get("nsePrice"), latest["close"])
    previous_close = previous["close"]
    if not is_finite(current) or current <= 0 or not is_finite(previous_close) or previous_close <= 0:
        return None

    change_percent = finite_or(
        stock.get("nseChangePercent"),
        safe_divide(current - previous_close, previous_close) * 100,
        0,
    )
    volume = finite_or(stock.get("nseVolume"), latest.get("volume"), 0) or 0
    prior_volumes = volumes[:-1]
    avg_volume_20 = average(prior_volumes[-20:])
    volume_ratio = safe_divide(volume, avg_volume_20) if avg_volume_20 else None
    atr_value = last(atr(candles, 14)) or current * 0.025
    atr_percent = safe_divide(atr_value, current) * 100
    sma20_value = last(sma(closes, 20))
    sma50_value = last(sma(closes, 50))
    rsi_value = last(rsi(closes, 14))
    prior_20_high = max(highs[-21:-1])
    prior_20_low = min(lows[-21:-1])
    prior_55_high = max(highs[-56:-1])
    high_52 = finite_or(stock.get("nseYearHigh"), max(highs))
    near_high = safe_divide(high_52 - current, high_52) * 100 if high_52 else None
    one_week = period_return(closes, 5)
    one_month = period_return(closes, 21)
    trend_ok = (
        is_finite(sma20_value)
        and is_finite(sma50_value)
        and current >= sma20_value
        and current >= sma50_value * 0.98
    )
    breakout = current > prior_20_high and previous_close <= prior_20_high
    breakout_watch = current >= prior_20_high * 0.99 or current >= prior_55_high * 0.985
    pullback_reclaim = (
        is_finite(sma20_value)
        and previous_close < sma20_value
        and current >= sma20_value
        and change_percent > 0
    )
    volume_ok = (is_finite(volume_ratio) and volume_ratio >= 1.15) or change_percent >= 1.5
    rsi_ok = not is_finite(rsi_value) or 45 <= rsi_value <= 72
    if not trend_ok or not volume_ok or not rsi_ok or not (breakout or breakout_watch or pullback_reclaim):
        return None

    target_percent = clamp(
        max(atr_percent * 1.8, 4) + clamp(change_percent, 0, 4) * 0.35 + clamp((volume_ratio or 1) - 1, 0, 2) * 0.7,
        4,
        12,
    )
    entry_high = round2(current)
    entry_low = round2(max(current - max(atr_value * 0.45, current * 0.012), prior_20_low))
    sell_price = round2(current * (1 + target_percent / 100))
    stop_loss = round2(max(min(prior_20_low * 0.995, current - atr_value * 1.05), current * 0.9))
    risk_percent = safe_divide(entry_high - stop_loss, entry_high) * 100
    score = daily_recommendation_score(
        change_percent,
        volume_ratio,
        one_week,
        one_month,
        near_high,
        breakout,
        breakout_watch,
        pullback_reclaim,
        rsi_value,
    )
    setup = daily_recommendation_setup(breakout, breakout_watch, pullback_reclaim)

    return {
        "symbol": symbol,
        "analysisSymbol": symbol,
        "name": first_present(stock.get("name"), symbol),
        "recommendedBy": "Nifty 500 daily technical scan",
        "recommenderDetails": [{
            "type": "Technical scan",
            "name": "Nifty 500 daily scan",
            "group": "Short-term price/volume setup",
            "detail": (
                f"{setup}; price change {round2(change_percent)}%, "
                f"volume {round_or_none(volume_ratio) if is_finite(volume_ratio) else 'n/a'}x 20-day average."
            ),
        }],
        "fundGroup": "Short-term price/volume setup",
        "sourceType": "Daily Nifty 500",
        "sourceDetail": "NSE Nifty 500 snapshot + Yahoo Finance daily candles",
        "buyPrice": entry_high,
        "buyRange": {"low": entry_low, "high": entry_high},
        "sellPrice": sell_price,
        "stopLoss": stop_loss,
        "duration": "3-15 sessions",
        "currentPrice": round_or_none(current),
        "upsidePercent": round_or_none(target_percent),
        "riskPercent": round_or_none(risk_percent),
        "score": score,
        "analyst": {
            "recommendationMean": None,
            "recommendationKey": "",
            "targetMeanPrice": None,
            "analystCount": None,
        },
        "ownershipSignals": [],
        "quarterlyResults": {
            "available": True,
            "period": "Daily scan",
            "source": "NSE/Yahoo daily technical scan",
            "summary": (
                f"{setup}. RSI {round_or_none(rsi_value) if is_finite(rsi_value) else 'n/a'}; "
                f"ATR {round_or_none(atr_percent) if is_finite(atr_percent) else 'n/a'}%; "
                f"20D volume ratio {round_or_none(volume_ratio) if is_finite(volume_ratio) else 'n/a'}x."
            ),
        },
        "reason": daily_recommendation_reason(setup, change_percent, volume_ratio, one_week, one_month),
        "technicalSetup": {
            "setup": setup,
            "changePercent": round_or_none(change_percent),
            "volumeRatio": round_or_none(volume_ratio),
            "rsi14": round_or_none(rsi_value),
            "atrPercent": round_or_none(atr_percent),
            "oneWeek": round_or_none(one_week),
            "oneMonth": round_or_none(one_month),
            "pctBelow52WeekHigh": round_or_none(near_high),
        },
    }


def daily_recommendation_score(change_percent, volume_ratio, one_week, one_month, near_high, breakout, breakout_watch, pullback_reclaim, rsi_value):
    score = 48
    score += clamp(change_percent * 4, -10, 20)
    score += clamp(((volume_ratio or 0) - 1) * 14, 0, 24)
    score += clamp(one_week or 0, -8, 12)
    score += clamp((one_month or 0) * 0.6, -8, 12)
    score += 12 if breakout else 0
    score += 7 if breakout_watch else 0
    score += 8 if pullback_reclaim else 0
    if is_finite(near_high) and 0 <= near_high <= 8:
        score += 6
    if is_finite(rsi_value) and 52 <= rsi_value <= 68:
        score += 5
    return round(clamp(score, 0, 100))


def daily_recommendation_setup(breakout, breakout_watch, pullback_reclaim):
    if breakout:
        return "20-day breakout with volume confirmation"
    if pullback_reclaim:
        return "20-day average reclaim after pullback"
    if breakout_watch:
        return "Breakout watch near recent high"
    return "Short-term momentum watch"


def daily_recommendation_reason(setup, change_percent, volume_ratio, one_week, one_month):
    parts = [setup]
    if is_finite(change_percent):
        parts.append(f"latest move {change_percent:.2f}%.")
    if is_finite(volume_ratio):
        parts.append(f"volume is {volume_ratio:.2f}x the 20-day average.")
    if is_finite(one_week):
        parts.append(f"1W return {one_week:.2f}%.")
    if is_finite(one_month):
        parts.append(f"1M return {one_month:.2f}%.")
    return " ".join(parts)


def summarize_daily_recommendations(rows, failed_count=0):
    skipped = f" {failed_count} symbols could not be checked." if failed_count else ""
    if not rows:
        return f"No daily Nifty 500 short-term candidates passed the current price/volume filters.{skipped}"
    top = rows[0]
    return (
        f"{len(rows)} daily short-term candidates from the Nifty 500 scan. "
        f"Top setup: {top.get('symbol')} at {top.get('score')}/100."
        f"{skipped}"
    )


def merge_recommendation_rows(rows):
    merged = {}
    for row in rows:
        symbol = normalize_symbol(row.get("symbol"))
        if not symbol:
            continue
        existing = merged.get(symbol)
        if not existing:
            merged[symbol] = row
            continue
        primary, secondary = (
            (row, existing)
            if (row.get("score") or 0) > (existing.get("score") or 0)
            else (existing, row)
        )
        merged[symbol] = {
            **secondary,
            **primary,
            "symbol": symbol,
            "analysisSymbol": first_present(primary.get("analysisSymbol"), secondary.get("analysisSymbol"), symbol),
            "recommendedBy": join_unique_text(existing.get("recommendedBy"), row.get("recommendedBy")),
            "fundGroup": join_unique_text(existing.get("fundGroup"), row.get("fundGroup")),
            "sourceType": join_unique_text(existing.get("sourceType"), row.get("sourceType")),
            "sourceDetail": join_unique_text(existing.get("sourceDetail"), row.get("sourceDetail"), separator=" | "),
            "reason": join_unique_text(existing.get("reason"), row.get("reason"), separator=" "),
            "recommenderDetails": merge_recommender_details(existing.get("recommenderDetails"), row.get("recommenderDetails")),
        }
    return list(merged.values())


def join_unique_text(*values, separator=" + "):
    parts = []
    for value in values:
        split_values = (
            str(value or "").split(separator)
            if separator != " " and separator in str(value or "")
            else [str(value or "")]
        )
        for part in split_values:
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return separator.join(parts)


def merge_recommender_details(*groups):
    details = []
    seen = set()
    for group in groups:
        for item in group or []:
            key = (item.get("type"), item.get("name"), item.get("detail"))
            if key in seen:
                continue
            seen.add(key)
            details.append(item)
    return details


def recommendation_universe():
    stocks = []
    nse_universe = safe_nifty500_primary_universe()
    if nse_universe:
        stocks.extend(primary_scan_candidates_from_nifty500(nse_universe)[:12])

    stocks.extend([
        {"symbol": symbol, "name": name, "tags": list(tags)}
        for symbol, name, tags in RECOMMENDATION_FALLBACK_UNIVERSE
    ])

    by_symbol = {}
    for stock in stocks:
        symbol = normalize_symbol(stock.get("symbol"))
        if not symbol:
            continue
        by_symbol.setdefault(symbol, {**stock, "symbol": symbol})
    return list(by_symbol.values())[:RECOMMENDATION_SCAN_LIMIT]


def build_recommendation_candidate(stock):
    symbol = normalize_symbol(stock.get("symbol"))
    if not symbol:
        return None

    loaders = {
        "quote": lambda: get_quote(symbol),
        "summary": lambda: get_summary(symbol),
        "chart": lambda: get_chart_range(symbol, "6mo", "1d"),
    }
    if symbol.endswith((".NS", ".BO")):
        loaders["screener"] = lambda: get_screener_fundamentals(symbol)

    results = settle_named_loaders(loaders, RECOMMENDATION_PROVIDER_CONCURRENCY)
    quote_data = results.get("quote") or {}
    summary = results.get("summary") or {}
    chart = results.get("chart") or {}
    candles = chart.get("candles") or []

    long_name = first_present(
        stock.get("name"),
        quote_data.get("longName"),
        quote_data.get("shortName"),
        raw((summary.get("price") or {}).get("longName")),
        raw((summary.get("price") or {}).get("shortName")),
        symbol,
    )
    ownership = {}
    quarterly_results = {}
    if symbol.endswith((".NS", ".BO")):
        screener = results.get("screener") or {}
        screener = ensure_shareholding_data(symbol, long_name, screener)
        ownership = build_ownership_trend(screener.get("shareholding"), {})
        quarterly_results = screener.get("quarterlyResults") or {}

    return build_recommendation_from_inputs(stock, quote_data, summary, candles, ownership, quarterly_results)


def build_recommendation_from_inputs(stock, quote_data=None, summary=None, candles=None, ownership=None, quarterly_results=None):
    quote_data = quote_data or {}
    summary = summary or {}
    candles = candles or []
    ownership = ownership or {}
    quarterly_results = quarterly_results or latest_quarterly_results_summary(summary)
    symbol = normalize_symbol(stock.get("symbol"))
    if not symbol:
        return None

    financial = summary.get("financialData") or {}
    price_module = summary.get("price") or {}
    current = finite_or(
        raw(financial.get("currentPrice")),
        raw(price_module.get("regularMarketPrice")),
        quote_data.get("regularMarketPrice"),
        stock.get("nsePrice"),
        candles[-1]["close"] if candles else None,
    )
    if not is_finite(current) or current <= 0:
        return None

    target_mean = finite_or(raw(financial.get("targetMeanPrice")), quote_data.get("targetMeanPrice"))
    target_high = finite_or(raw(financial.get("targetHighPrice")), quote_data.get("targetHighPrice"))
    recommendation_mean = finite_or(raw(financial.get("recommendationMean")), quote_data.get("recommendationMean"))
    analyst_count = finite_or(raw(financial.get("numberOfAnalystOpinions")), quote_data.get("numberOfAnalystOpinions"))
    recommendation_key = normalize_recommendation_grade(financial.get("recommendationKey") or quote_data.get("recommendationKey"))
    latest_action = latest_analyst_action(summary)
    ownership_sources = recommendation_ownership_sources(ownership)

    analyst_upside = (
        safe_divide(target_mean - current, current) * 100
        if is_finite(target_mean)
        else None
    )
    analyst_signal = (
        (is_finite(recommendation_mean) and recommendation_mean <= 2.6)
        or recommendation_key in BULLISH_RECOMMENDATION_KEYS
        or (is_finite(analyst_upside) and analyst_upside >= RECOMMENDATION_MIN_UPSIDE_PERCENT)
        or is_bullish_analyst_action(latest_action)
    )
    if not analyst_signal and not ownership_sources:
        return None

    atr_value = last(atr(candles, 14)) if len(candles) >= 15 else None
    entry_low, entry_high = recommendation_entry_range(current, candles, atr_value)
    sell_price = recommendation_sell_price(current, entry_high, target_mean, target_high, candles, atr_value)
    if not is_finite(sell_price) or sell_price <= entry_high:
        return None

    upside_percent = safe_divide(sell_price - entry_high, entry_high) * 100
    if not ownership_sources and upside_percent < RECOMMENDATION_MIN_UPSIDE_PERCENT:
        return None

    stop_loss = recommendation_stop_loss(entry_high, candles, atr_value)
    risk_percent = safe_divide(entry_high - stop_loss, entry_high) * 100 if is_finite(stop_loss) else None
    recommended_by = recommendation_recommender(latest_action, analyst_signal, ownership_sources)
    recommender_details = recommendation_recommender_details(latest_action, analyst_signal, ownership_sources, ownership)
    source_type = recommendation_source_type(analyst_signal, ownership_sources)
    reason = recommendation_reason(
        recommendation_mean,
        recommendation_key,
        analyst_upside,
        analyst_count,
        ownership_sources,
    )

    return {
        "symbol": symbol,
        "analysisSymbol": symbol,
        "name": first_present(stock.get("name"), quote_data.get("longName"), quote_data.get("shortName"), symbol),
        "recommendedBy": recommended_by,
        "recommenderDetails": recommender_details,
        "fundGroup": recommendation_group_summary(recommender_details),
        "sourceType": source_type,
        "sourceDetail": recommendation_source_detail(latest_action, ownership),
        "buyPrice": round_or_none(entry_high),
        "buyRange": {"low": round_or_none(entry_low), "high": round_or_none(entry_high)},
        "sellPrice": round_or_none(sell_price),
        "stopLoss": round_or_none(stop_loss),
        "duration": recommendation_duration(analyst_signal, ownership_sources),
        "currentPrice": round_or_none(current),
        "upsidePercent": round_or_none(upside_percent),
        "riskPercent": round_or_none(risk_percent),
        "score": recommendation_score(recommendation_mean, analyst_upside, analyst_count, ownership_sources),
        "analyst": {
            "recommendationMean": round_or_none(recommendation_mean),
            "recommendationKey": recommendation_key,
            "targetMeanPrice": round_or_none(target_mean),
            "analystCount": round(analyst_count) if is_finite(analyst_count) else None,
        },
        "ownershipSignals": ownership_sources,
        "quarterlyResults": quarterly_results,
        "reason": reason,
    }


def settle_named_loaders(loaders, concurrency=4, errors=None):
    """Run loaders concurrently; a loader that raises yields ``{}``.

    Pass a dict as ``errors`` to also receive the reason each failed loader
    gave. Without it a raised exception is indistinguishable from an upstream
    that legitimately returned nothing, so a caller wanting to tell the user
    which of the two happened has no way to find out.
    """
    results = {}
    if not loaders:
        return results
    with ThreadPoolExecutor(max_workers=min(concurrency, len(loaders))) as executor:
        futures = {executor.submit(loader): key for key, loader in loaders.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as error:
                results[key] = {}
                if errors is not None:
                    errors[key] = str(error).strip() or type(error).__name__
    return results


def latest_analyst_action(summary):
    history = arrayify((summary.get("upgradeDowngradeHistory") or {}).get("history"))
    if not history:
        return {}
    return sorted(history, key=lambda item: item.get("epochGradeDate") or 0, reverse=True)[0] or {}


def is_bullish_analyst_action(action):
    grade = normalize_recommendation_grade(action.get("toGrade"))
    action_name = normalize_recommendation_grade(action.get("action"))
    return grade in BULLISH_RECOMMENDATION_KEYS or action_name in {"up", "upgrade", "initiated"}


def normalize_recommendation_grade(value):
    return re.sub(r"[^a-z_]+", "_", str(value or "").strip().lower()).strip("_")


def recommendation_ownership_sources(ownership):
    rows = ownership.get("rows") or []
    signals = []
    for name in ["FIIs", "DIIs"]:
        row = next((item for item in rows if item.get("name") == name), None)
        if not row:
            continue
        quarter_change = row.get("quarterChangePoints")
        available_change = row.get("changePoints")
        qualifies_quarter = is_finite(quarter_change) and quarter_change >= 0.5
        qualifies_available = is_finite(available_change) and available_change >= 1.0
        if not qualifies_quarter and not qualifies_available:
            continue
        signals.append({
            "name": name,
            "groupLabel": RECOMMENDATION_GROUP_LABELS.get(name, name),
            "groupDetail": RECOMMENDATION_GROUP_DETAILS.get(name, ""),
            "changePoints": round_or_none(quarter_change if qualifies_quarter else available_change),
            "period": row.get("latestPeriod") or "latest period",
            "basis": "latest quarter" if qualifies_quarter else "available quarters",
            "latestHolding": round_ratio_or_none(row.get("latest")),
        })
    return signals


def recommendation_entry_range(current, candles, atr_value):
    entry_high = current
    fallback_width = current * 0.025
    width = atr_value * 0.45 if is_finite(atr_value) and atr_value > 0 else fallback_width
    recent_low = min([candle["low"] for candle in candles[-20:]], default=None)
    entry_low = current - width
    if is_finite(recent_low) and current * 0.9 <= recent_low <= current:
        entry_low = max(entry_low, recent_low)
    entry_low = min(entry_low, entry_high)
    return round2(max(entry_low, current * 0.82)), round2(entry_high)


def recommendation_sell_price(current, entry_high, target_mean, target_high, candles, atr_value):
    if is_finite(target_mean) and target_mean > entry_high * 1.02:
        return target_mean
    if is_finite(target_high) and target_high > entry_high * 1.04:
        return target_high

    recent_high = max([candle["high"] for candle in candles[-60:]], default=None)
    atr_target = current + (atr_value * 2.2 if is_finite(atr_value) and atr_value > 0 else current * 0.08)
    if is_finite(recent_high) and recent_high > entry_high * 1.03:
        return max(recent_high, atr_target)
    return atr_target


def recommendation_stop_loss(entry_high, candles, atr_value):
    recent_low = min([candle["low"] for candle in candles[-20:]], default=None)
    atr_stop = entry_high - (atr_value * 1.4 if is_finite(atr_value) and atr_value > 0 else entry_high * 0.07)
    if is_finite(recent_low) and recent_low < entry_high:
        return round2(max(min(atr_stop, recent_low * 0.99), entry_high * 0.78))
    return round2(max(atr_stop, entry_high * 0.78))


def recommendation_recommender(action, analyst_signal, ownership_sources):
    parts = []
    if analyst_signal:
        firm = str(action.get("firm") or "").strip()
        if firm and is_bullish_analyst_action(action):
            grade = str(action.get("toGrade") or "").strip()
            parts.append(f"{firm}{f' ({grade})' if grade else ''}")
        else:
            parts.append("Analyst consensus")
    if ownership_sources:
        parts.append(" / ".join(item["name"] for item in ownership_sources))
    return " + ".join(parts) or "Public data signal"


def recommendation_recommender_details(action, analyst_signal, ownership_sources, ownership):
    details = []
    if analyst_signal:
        firm = str(action.get("firm") or "").strip()
        grade = str(action.get("toGrade") or "").strip()
        action_date = to_date(action.get("epochGradeDate")) if action.get("epochGradeDate") else ""
        details.append({
            "type": "Analyst",
            "name": firm or "Analyst consensus",
            "group": grade or "Broker / analyst coverage",
            "detail": " ".join([str(action.get("action") or "").strip(), action_date]).strip(),
        })

    source = ownership.get("source") or ""
    for signal in ownership_sources:
        latest_holding = signal.get("latestHolding")
        details.append({
            "type": "Institutional ownership",
            "name": signal.get("name") or "",
            "group": signal.get("groupLabel") or signal.get("name") or "",
            "detail": (
                f"{signal.get('groupDetail') or ''} Added {abs(signal.get('changePoints') or 0):.2f} pp "
                f"over the {signal.get('basis') or 'available period'}"
                f"{f'; latest holding {latest_holding * 100:.2f}%' if is_finite(latest_holding) else ''}."
                f"{f' Source: {source}.' if source else ''}"
            ).strip(),
        })
    return details


def recommendation_group_summary(details):
    groups = [item.get("group") for item in details or [] if item.get("group")]
    return " / ".join(dict.fromkeys(groups))


def recommendation_source_type(analyst_signal, ownership_sources):
    parts = []
    if analyst_signal:
        parts.append("Analyst")
    if ownership_sources:
        parts.append("FII/DII")
    return " + ".join(parts)


def recommendation_source_detail(action, ownership):
    details = []
    if action.get("firm"):
        action_date = to_date(action.get("epochGradeDate")) if action.get("epochGradeDate") else ""
        details.append(" ".join([str(action.get("firm") or ""), str(action.get("action") or ""), str(action.get("toGrade") or ""), action_date]).strip())
    source = ownership.get("source")
    if source:
        details.append(source)
    return " | ".join(details) or "Public market data"


def recommendation_reason(recommendation_mean, recommendation_key, analyst_upside, analyst_count, ownership_sources):
    parts = []
    if is_finite(recommendation_mean):
        label = recommendation_key.replace("_", " ") if recommendation_key else "consensus"
        parts.append(f"Analyst mean {recommendation_mean:.2f} ({label}).")
    if is_finite(analyst_upside):
        parts.append(f"Target upside {analyst_upside:.2f}%.")
    if is_finite(analyst_count):
        parts.append(f"{round(analyst_count)} analyst opinions.")
    for signal in ownership_sources:
        parts.append(
            f"{signal['name']} added {abs(signal['changePoints']):.2f} pp over the {signal['basis']}."
        )
    return " ".join(parts) or "Constructive public recommendation signal."


def recommendation_duration(analyst_signal, ownership_sources):
    if analyst_signal and ownership_sources:
        return "6-12 months"
    if analyst_signal:
        return "3-6 months"
    return "1-2 quarters"


def recommendation_score(recommendation_mean, analyst_upside, analyst_count, ownership_sources):
    score = 45
    if is_finite(recommendation_mean):
        score += clamp((3.2 - recommendation_mean) * 15, -12, 28)
    if is_finite(analyst_upside):
        score += clamp(analyst_upside * 0.9, 0, 24)
    if is_finite(analyst_count):
        score += clamp(analyst_count, 0, 20) * 0.4
    for signal in ownership_sources:
        score += clamp(signal.get("changePoints") or 0, 0, 4) * 4
    return round(clamp(score, 0, 100))


def summarize_recommendations(rows):
    if not rows:
        return "No analyst, FII/DII, or daily Nifty 500 recommendation candidates passed the current filters."
    analyst_count = sum(1 for row in rows if "Analyst" in row.get("sourceType", ""))
    ownership_count = sum(1 for row in rows if "FII/DII" in row.get("sourceType", ""))
    daily_count = sum(1 for row in rows if "Daily" in row.get("sourceType", ""))
    top = rows[0]
    return (
        f"{len(rows)} ideas from {analyst_count} analyst-backed, {ownership_count} FII/DII-backed, "
        f"and {daily_count} daily Nifty 500 technical signals. "
        f"Top score: {top.get('symbol')} at {top.get('score')}/100."
    )


def safe_usd_inr_snapshot():
    try:
        return get_usd_inr_snapshot()
    except Exception as error:
        return {
            "available": False,
            "symbol": "USDINR=X",
            "price": None,
            "change": None,
            "changePercent": None,
            "generatedAt": iso_now(),
            "error": str(error),
        }


def get_usd_inr_snapshot():
    chart = get_chart_range("USDINR=X", "1mo", "1d")
    candles = chart["candles"]
    latest = candles[-1]
    previous = candles[-2] if len(candles) > 1 else latest
    change = latest["close"] - previous["close"]
    return {
        "available": True,
        "symbol": "USDINR=X",
        "price": round2(latest["close"]),
        "change": round2(change),
        "changePercent": round2(safe_divide(change, previous["close"]) * 100),
        "lastDate": latest.get("date"),
        "generatedAt": iso_now(),
    }


def commodity_with_inr(commodity, usd_inr):
    rate = usd_inr.get("price") if usd_inr and usd_inr.get("available") else None
    if not is_finite(rate) or not is_finite(commodity.get("price")):
        return commodity
    return {
        **commodity,
        "usdInr": rate,
        "inrPrice": round2(commodity["price"] * rate),
    }


def attach_sector_top_stocks(snapshot, stock_movers_by_sector):
    if not snapshot.get("available"):
        return snapshot

    sectors = [
        attach_moneycontrol_sector_top_stocks(row, stock_movers_by_sector)
        for row in snapshot.get("sectors") or []
    ]
    by_sector = {row.get("sector"): row for row in sectors}

    def replace_rows(rows):
        return [
            by_sector.get(row.get("sector"), attach_moneycontrol_sector_top_stocks(row, stock_movers_by_sector))
            for row in rows or []
        ]

    return {
        **snapshot,
        "sectors": sectors,
        "topPerforming": replace_rows(snapshot.get("topPerforming"))[:5],
        "underPerforming": replace_rows(snapshot.get("underPerforming"))[:3],
    }


def attach_moneycontrol_sector_top_stocks(row, stock_movers_by_sector):
    nse_sector = nse_sector_for_moneycontrol(row.get("sector"))
    top_stocks = stock_movers_by_sector.get(nse_sector, []) if nse_sector else []
    return {
        **row,
        "nseSector": nse_sector or "",
        "topStocks": top_stocks[:5],
    }


def nse_sector_for_moneycontrol(sector_name):
    if not sector_name:
        return ""
    mapped = MONEYCONTROL_NSE_SECTOR_MAP.get(sector_name)
    if mapped:
        return mapped
    key = sector_match_key(sector_name)
    for moneycontrol_name, nse_sector in MONEYCONTROL_NSE_SECTOR_MAP.items():
        map_key = sector_match_key(moneycontrol_name)
        if key == map_key or (key and (key in map_key or map_key in key)):
            return nse_sector
    return ""


def primary_opportunity_candidates(scanned_stocks):
    qualifying = [
        stock for stock in scanned_stocks
        if (
            stock["nearAvailableHigh"]
            or stock["near52WeekHigh"]
            or stock["breakout"]
            or stock["breakoutWatch"]
            or stock["narrowRange4Breakout"]
            or stock["narrowRange7Breakout"]
            or stock["narrowRange4Watch"]
            or stock["narrowRange7Watch"]
            or stock["reversalWatch"]
            or stock["bullishReversal"]
            or stock["trendReversal"]
        )
    ]
    sorted_qualifying = sorted(qualifying, key=lambda item: item["score"], reverse=True)
    if len(sorted_qualifying) >= 50:
        return sorted_qualifying[:50]

    selected_symbols = {stock["symbol"] for stock in sorted_qualifying}
    fillers = [
        stock for stock in sorted(scanned_stocks, key=lambda item: item["score"], reverse=True)
        if stock["symbol"] not in selected_symbols
    ]
    return [*sorted_qualifying, *fillers][:50]


def safe_nifty500_primary_universe():
    try:
        return cached(
            "nifty-500-primary-universe",
            build_nifty500_primary_universe,
            NIFTY_500_PRIMARY_CACHE_SECONDS,
        )
    except Exception:
        return []


def build_nifty500_primary_universe():
    errors = []
    try:
        # NSE is the better source here because it carries prices, which is what
        # ranks the 500 down to the scan list, but it is not the only source: the
        # constituent CSV answers in under a second with the same names. On the
        # default 20s x 3 budget a sulking NSE cost 33s of a ~64s build, so this
        # gives it one short attempt and then stops waiting.
        rows = build_nifty500_primary_universe_from_index_payload(
            fetch_nse_stock_index_payload(NIFTY_500_INDEX_NAME, timeout=6, attempts=1)
        )
    except RuntimeError as error:
        errors.append(str(error))
        rows = []
    if rows:
        return merge_watchlists(rows)

    try:
        rows = build_nifty500_primary_universe_from_csv()
    except RuntimeError as error:
        errors.append(str(error))
        rows = []

    universe = merge_watchlists(rows)
    if not universe:
        detail = f" {' '.join(errors)}" if errors else ""
        raise RuntimeError(f"NSE Nifty 500 constituents were unavailable.{detail}".strip())
    return enrich_nifty500_universe_with_yahoo_quotes(universe)


def build_nifty500_primary_universe_from_index_payload(payload):
    index_key = sector_match_key(NIFTY_500_INDEX_NAME)
    rows = []
    for item in payload.get("data") or []:
        symbol = nse_text(item.get("symbol")).upper()
        if not symbol or sector_match_key(symbol) == index_key:
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        rows.append({
            "symbol": f"{symbol}.NS",
            "name": nse_text(meta.get("companyName"))
                or nse_text(item.get("companyName"))
                or nse_text(item.get("name"))
                or symbol,
            "tags": ["Nifty 500"],
            "nsePrice": nse_round(item.get("lastPrice")),
            "nseChangePercent": nse_round(item.get("pChange")),
            "nseVolume": nse_int(item.get("totalTradedVolume")),
            "nseValue": nse_round(item.get("totalTradedValue")),
            "nseYearHigh": nse_round(item.get("yearHigh")),
            "nseYearLow": nse_round(item.get("yearLow")),
        })
    return rows


def build_nifty500_primary_universe_from_csv():
    text = fetch_text_once(NIFTY_500_CONSTITUENTS_CSV_URL, timeout=8)
    rows = []
    for item in csv.DictReader(StringIO((text or "").lstrip("\ufeff"))):
        symbol = nse_text(
            item.get("Symbol")
            or item.get("SYMBOL")
            or item.get("symbol")
        ).upper()
        if not symbol:
            continue
        industry = nse_text(item.get("Industry"))
        tags = ["Nifty 500"]
        if industry:
            tags.append(industry)
        rows.append({
            "symbol": f"{symbol}.NS",
            "name": nse_text(
                item.get("Company Name")
                or item.get("Company")
                or item.get("Security Name")
                or item.get("Name")
            ) or symbol,
            "tags": tags,
            "niftyIndexSource": "Nifty Indices constituent CSV",
            "nsePrice": None,
            "nseChangePercent": None,
            "nseVolume": None,
            "nseValue": None,
            "nseYearHigh": None,
            "nseYearLow": None,
        })
    return rows


def enrich_nifty500_universe_with_yahoo_quotes(universe):
    """Fill prices onto a universe that came from the CSV, which carries none.

    This is the branch a deployment takes whenever NSE declines, and it was the
    single slowest thing the monitor did: 500 symbols in seven sequential
    batches, each of which now degrades to per-symbol chart calls while Yahoo's
    batch quote endpoint answers 401. That was ~34s of a ~64s build. The batches
    are independent, so they go out together; the pool sizes multiply, and the
    product is held at the level measured clean against Yahoo.
    """
    quotes = {}
    symbol_batches = list(batches([stock.get("symbol") for stock in universe], 80))
    for ok, batch_quotes in settle_map(
        symbol_batches, get_quotes, concurrency=UNIVERSE_QUOTE_BATCH_CONCURRENCY
    ):
        if ok and batch_quotes:
            quotes.update(batch_quotes)

    if not quotes:
        return universe

    enriched = []
    for stock in universe:
        quote_data = quotes.get(stock.get("symbol")) or {}
        price = nse_round(raw(quote_data.get("regularMarketPrice")))
        volume = nse_int(raw(quote_data.get("regularMarketVolume")))
        value = price * volume if is_finite(price) and is_finite(volume) else None
        enriched.append({
            **stock,
            "name": first_present(
                stock.get("name"),
                quote_data.get("shortName"),
                quote_data.get("longName"),
                stock.get("symbol"),
            ),
            "nsePrice": first_present(stock.get("nsePrice"), price),
            "nseChangePercent": first_present(
                stock.get("nseChangePercent"),
                nse_round(raw(quote_data.get("regularMarketChangePercent"))),
            ),
            "nseVolume": first_present(stock.get("nseVolume"), volume),
            "nseValue": first_present(stock.get("nseValue"), value),
            "nseYearHigh": first_present(
                stock.get("nseYearHigh"),
                nse_round(raw(quote_data.get("fiftyTwoWeekHigh"))),
            ),
            "nseYearLow": first_present(
                stock.get("nseYearLow"),
                nse_round(raw(quote_data.get("fiftyTwoWeekLow"))),
            ),
        })
    return enriched


def batches(values, size):
    clean_values = [value for value in values if value]
    for index in range(0, len(clean_values), size):
        yield clean_values[index:index + size]


def primary_scan_candidates_from_nifty500(universe):
    if len(universe) <= NIFTY_500_PRIMARY_SCAN_LIMIT:
        return universe
    return sorted(
        universe,
        key=primary_scan_prefilter_score,
        reverse=True,
    )[:NIFTY_500_PRIMARY_SCAN_LIMIT]


def primary_scan_prefilter_score(stock):
    price = stock.get("nsePrice")
    year_high = stock.get("nseYearHigh")
    year_low = stock.get("nseYearLow")
    change_percent = stock.get("nseChangePercent")
    volume = stock.get("nseVolume")
    traded_value = stock.get("nseValue")

    score = 0
    pct_below_high = (
        safe_divide(year_high - price, year_high) * 100
        if is_finite(price) and is_finite(year_high) and year_high > 0
        else None
    )
    range_position = (
        safe_divide(price - year_low, year_high - year_low) * 100
        if is_finite(price)
        and is_finite(year_high)
        and is_finite(year_low)
        and year_high > year_low
        else None
    )

    if is_finite(pct_below_high):
        score += clamp(36 - pct_below_high * 3, 0, 36)
        if 0 <= pct_below_high <= 3:
            score += 18
        elif 3 < pct_below_high <= 8:
            score += 8

    if is_finite(range_position):
        if range_position >= 78:
            score += 12
        elif 20 <= range_position <= 55:
            score += 7

    if is_finite(change_percent):
        score += clamp(change_percent * 3, -8, 20)
        if is_finite(range_position) and change_percent > 0 and range_position <= 55:
            score += 8

    if is_finite(volume) and volume > 0:
        score += min(math.log10(volume + 1) * 2, 14)
    if is_finite(traded_value) and traded_value > 0:
        score += min(math.log10(traded_value + 1), 10)

    return score


def fetch_nse_stock_index_payload(index_name, timeout=20, attempts=3):
    path = f"/api/equity-stockIndices?index={quote(index_name)}"
    try:
        return fetch_nse_json(path)
    except RuntimeError:
        return fetch_nse_json_with_session(path, timeout=timeout, attempts=attempts)


def market_activity_universe(nse_snapshot=None):
    return merge_watchlists(
        BREAKOUT_WATCHLIST,
        HIGH_ACTIVITY_WATCHLIST,
        ORDER_CATALYST_WATCHLIST,
        nse_snapshot_watchlist(nse_snapshot),
    )


def nse_snapshot_watchlist(nse_snapshot):
    if not nse_snapshot or not nse_snapshot.get("available"):
        return []
    rows = []
    for key, label in (
        ("topGainers", "Top gainer"),
        ("topLosers", "Top loser"),
        ("mostActive", "Most active"),
        ("weekHighs", "52W high"),
    ):
        for item in nse_snapshot.get(key) or []:
            symbol = nse_text(item.get("symbol")).upper()
            if symbol:
                rows.append({
                    "symbol": f"{symbol}.NS",
                    "name": item.get("name") or symbol,
                    "tags": [label, "NSE live"],
                })
    for item in ((nse_snapshot.get("priceBands") or {}).get("rows") or []):
        symbol = nse_text(item.get("symbol")).upper()
        if symbol:
            rows.append({
                "symbol": f"{symbol}.NS",
                "name": item.get("name") or symbol,
                "tags": ["Price band", "NSE live"],
            })
    return rows


def merge_watchlists(*watchlists):
    merged = {}
    for watchlist in watchlists:
        for stock in watchlist:
            symbol = stock.get("symbol")
            if not symbol:
                continue
            if symbol not in merged:
                merged[symbol] = {
                    **stock,
                    "symbol": symbol,
                    "name": stock.get("name") or symbol,
                    "tags": list(stock.get("tags") or []),
                }
                continue
            for key, value in stock.items():
                if key not in {"symbol", "name", "tags"} and key not in merged[symbol]:
                    merged[symbol][key] = value
            merged[symbol]["tags"] = list(dict.fromkeys([
                *merged[symbol].get("tags", []),
                *(stock.get("tags") or []),
            ]))
    return list(merged.values())


def get_commodity_snapshot(commodity):
    chart = get_chart_range(commodity["symbol"], "6mo", "1d")
    candles = chart["candles"]
    closes = [candle["close"] for candle in candles]
    latest = closes[-1]
    previous = closes[-2]

    return {
        **commodity,
        "price": round2(latest),
        "change": round2(latest - previous),
        "changePercent": round2(safe_divide(latest - previous, previous) * 100),
        "oneWeek": round_or_none(period_return(closes, 5)),
        "oneMonth": round_or_none(period_return(closes, 21)),
        "trend": commodity_trend(closes),
        "lastDate": candles[-1].get("date"),
    }


def settle_map(items, mapper, concurrency=4):
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(items) or 1)) as executor:
        futures = {executor.submit(mapper, item): index for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = (True, future.result())
            except Exception as error:
                results[index] = (False, error)
    return results


def scan_watchlist_stock(stock):
    chart = get_chart_range(stock["symbol"], "2y", "1d")
    candles = chart["candles"]
    if len(candles) < 80:
        raise RuntimeError(f"Not enough data for {stock['symbol']}")

    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    lows = [candle["low"] for candle in candles]
    volumes = [candle.get("volume") or 0 for candle in candles]
    latest = candles[-1]
    previous = candles[-2]
    available_high = max(highs)
    high_index = highs.index(available_high)
    high_52 = max(candle["high"] for candle in candles[-252:])
    recent_55_high = max(candle["high"] for candle in candles[-56:])
    prior_5_high = max(candle["high"] for candle in candles[-6:-1])
    prior_20_high = max(candle["high"] for candle in candles[-21:-1])
    prior_55_high = max(candle["high"] for candle in candles[-56:-1])
    prior_20_low = min(candle["low"] for candle in candles[-21:-1])
    atr_value = last(atr(candles[-80:], 14)) or latest["close"] * 0.02
    sma20_values = sma(closes, 20)
    sma50_values = sma(closes, 50)
    rsi_values = rsi(closes, 14)
    sma20_value = last(sma20_values)
    sma50_value = last(sma50_values)
    rsi_value = last(rsi_values)
    sma20_slope = (
        sma20_values[-1] - sma20_values[-6]
        if len(sma20_values) >= 6 and is_finite(sma20_values[-1]) and is_finite(sma20_values[-6])
        else None
    )
    avg_volume_20 = last(sma(volumes, 20))
    volume_ratio = latest.get("volume", 0) / avg_volume_20 if avg_volume_20 else None
    pct_below_available_high = safe_divide(available_high - latest["close"], available_high) * 100
    pct_below_52_high = safe_divide(high_52 - latest["close"], high_52) * 100
    pct_above_prior_20_low = safe_divide(latest["close"] - prior_20_low, prior_20_low) * 100
    drawdown_from_recent_high = safe_divide(recent_55_high - latest["close"], recent_55_high) * 100
    breakout = latest["close"] > prior_55_high and previous["close"] <= prior_55_high
    breakout_watch = latest["close"] >= prior_55_high * 0.98 or latest["close"] >= prior_20_high * 0.99
    near_available_high = 0 <= pct_below_available_high <= 3
    near_52_week_high = 0 <= pct_below_52_high <= 3
    near_52_week_setup = 0 <= pct_below_52_high <= 5
    bullish_reversal = (
        drawdown_from_recent_high >= 5
        and latest["close"] > prior_5_high
        and previous["close"] <= prior_5_high
        and latest["close"] > (latest.get("open") or latest["close"])
    )
    trend_reversal = (
        is_finite(sma20_value)
        and previous["close"] <= sma20_value
        and latest["close"] > sma20_value
        and (not is_finite(sma50_value) or latest["close"] >= sma50_value * 0.96)
        and ((sma20_slope or 0) >= 0 or latest["close"] > previous["close"])
    )
    reversal_watch = (
        0 <= pct_above_prior_20_low <= 8
        and latest["close"] > previous["close"]
        and (not is_finite(rsi_value) or rsi_value <= 48)
    )
    narrow_range_4 = narrow_range_breakout(candles, 4, atr_value)
    narrow_range_7 = narrow_range_breakout(candles, 7, atr_value)
    narrow_range_4_breakout = near_52_week_setup and narrow_range_4["breakout"]
    narrow_range_7_breakout = near_52_week_setup and narrow_range_7["breakout"]
    narrow_range_4_watch = near_52_week_setup and narrow_range_4["watch"] and not narrow_range_4_breakout
    narrow_range_7_watch = near_52_week_setup and narrow_range_7["watch"] and not narrow_range_7_breakout
    momentum = period_return(closes, 21)
    score = round(clamp(
        (35 if breakout else 0)
        + (18 if breakout_watch else 0)
        + (25 if near_available_high else 0)
        + (18 if near_52_week_high else 0)
        + (24 if narrow_range_4_breakout else 0)
        + (30 if narrow_range_7_breakout else 0)
        + (10 if narrow_range_4_watch else 0)
        + (12 if narrow_range_7_watch else 0)
        + (26 if bullish_reversal else 0)
        + (22 if trend_reversal else 0)
        + (14 if reversal_watch else 0)
        + (8 if volume_ratio and volume_ratio > 1.2 else 0)
        + clamp(momentum or 0, -10, 12),
        0,
        100,
    ))

    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "tags": stock["tags"],
        "price": round2(latest["close"]),
        "changePercent": round2(safe_divide(latest["close"] - previous["close"], previous["close"]) * 100),
        "availableHigh": round2(available_high),
        "availableHighDate": candles[high_index].get("date"),
        "high52Week": round2(high_52),
        "pctBelowAvailableHigh": round2(pct_below_available_high),
        "pctBelow52WeekHigh": round2(pct_below_52_high),
        "pctAbovePrior20Low": round2(pct_above_prior_20_low),
        "drawdownFromRecentHigh": round2(drawdown_from_recent_high),
        "prior5High": round2(prior_5_high),
        "prior20High": round2(prior_20_high),
        "prior55High": round2(prior_55_high),
        "prior20Low": round2(prior_20_low),
        "sma20": round_or_none(sma20_value),
        "sma50": round_or_none(sma50_value),
        "rsi14": round_or_none(rsi_value),
        "breakout": breakout,
        "breakoutWatch": breakout_watch,
        "bullishReversal": bullish_reversal,
        "trendReversal": trend_reversal,
        "reversalWatch": reversal_watch,
        "nearAvailableHigh": near_available_high,
        "near52WeekHigh": near_52_week_high,
        "near52WeekSetup": near_52_week_setup,
        "narrowRange4": narrow_range_4,
        "narrowRange7": narrow_range_7,
        "narrowRange4Breakout": narrow_range_4_breakout,
        "narrowRange7Breakout": narrow_range_7_breakout,
        "narrowRange4Watch": narrow_range_4_watch,
        "narrowRange7Watch": narrow_range_7_watch,
        "atr": round2(atr_value),
        "volumeRatio": round_or_none(volume_ratio),
        "oneMonth": round_or_none(momentum),
        "score": score,
        "signal": describe_breakout_signal({
            "breakout": breakout,
            "breakoutWatch": breakout_watch,
            "nearAvailableHigh": near_available_high,
            "near52WeekHigh": near_52_week_high,
            "narrowRange4Breakout": narrow_range_4_breakout,
            "narrowRange7Breakout": narrow_range_7_breakout,
            "narrowRange4Watch": narrow_range_4_watch,
            "narrowRange7Watch": narrow_range_7_watch,
            "bullishReversal": bullish_reversal,
            "trendReversal": trend_reversal,
            "reversalWatch": reversal_watch,
        }),
    }


def narrow_range_breakout(candles, sessions, atr_value):
    if len(candles) < sessions + 1:
        return {
            "period": sessions,
            "high": None,
            "low": None,
            "widthPercent": None,
            "maxWidthPercent": None,
            "tight": False,
            "watch": False,
            "breakout": False,
        }

    latest = candles[-1]
    prior_window = candles[-sessions - 1:-1]
    range_high = max(candle["high"] for candle in prior_window)
    range_low = min(candle["low"] for candle in prior_window)
    width_percent = safe_divide(range_high - range_low, latest["close"]) * 100
    atr_percent = safe_divide(atr_value, latest["close"]) * 100
    base_width = 3 if sessions == 4 else 4.5
    atr_multiplier = 1.4 if sessions == 4 else 2.1
    max_width_percent = max(base_width, atr_percent * atr_multiplier)
    tight = width_percent <= max_width_percent
    watch = tight and latest["close"] >= range_high * 0.995
    breakout = tight and latest["close"] > range_high

    return {
        "period": sessions,
        "high": round2(range_high),
        "low": round2(range_low),
        "widthPercent": round_or_none(width_percent),
        "maxWidthPercent": round_or_none(max_width_percent),
        "tight": tight,
        "watch": watch,
        "breakout": breakout,
    }


def scan_high_activity_stock(stock):
    chart = get_chart_range(stock["symbol"], "6mo", "1d")
    candles = chart["candles"]
    if len(candles) < 40:
        raise RuntimeError(f"Not enough data for {stock['symbol']}")

    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    volumes = [candle.get("volume") or 0 for candle in candles]
    latest = candles[-1]
    previous = candles[-2]
    sma20_value = last(sma(closes, 20))
    avg_volume_20 = last(sma(volumes, 20))
    avg_volume_50 = last(sma(volumes, 50)) or avg_volume_20
    volume = latest.get("volume") or 0
    volume_ratio = volume / avg_volume_20 if avg_volume_20 else None
    delivery_proxy = volume / avg_volume_50 if avg_volume_50 else None
    change_percent = safe_divide(latest["close"] - previous["close"], previous["close"]) * 100
    one_week = period_return(closes, 5)
    one_month = period_return(closes, 21)
    recent_high = max(highs[-126:])
    upside_to_recent_high = safe_divide(recent_high - latest["close"], latest["close"]) * 100
    value_traded = latest["close"] * volume
    above_sma20 = is_finite(sma20_value) and latest["close"] > sma20_value
    score = round(clamp(
        (min(volume_ratio or 0, 6) * 14)
        + (min(delivery_proxy or 0, 4) * 5)
        + (max(change_percent, 0) * 4)
        + (max(one_week or 0, 0) * 1.4)
        + (max(one_month or 0, 0) * 0.45)
        + (min(max(upside_to_recent_high, 0), 25) * 0.55)
        + (10 if above_sma20 else 0),
        0,
        100,
    ))

    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "tags": stock["tags"],
        "price": round2(latest["close"]),
        "changePercent": round2(change_percent),
        "volume": volume,
        "avgVolume20": round(avg_volume_20 or 0),
        "volumeRatio": round_or_none(volume_ratio),
        "liquidityValue": round_or_none(value_traded),
        "oneWeek": round_or_none(one_week),
        "oneMonth": round_or_none(one_month),
        "recentHigh": round2(recent_high),
        "upsideToRecentHigh": round_or_none(upside_to_recent_high),
        "aboveSma20": above_sma20,
        "score": score,
        "signal": describe_high_activity_signal(volume_ratio, change_percent, one_week, above_sma20),
    }


def is_high_volume_candidate(stock):
    volume_ratio = stock.get("volumeRatio") or 0
    change_percent = stock.get("changePercent") or 0
    one_week = stock.get("oneWeek") or 0
    liquidity_value = stock.get("liquidityValue") or 0
    return (
        volume_ratio >= 1.8
        and change_percent > 0.3
        and stock.get("score", 0) >= 35
        and (liquidity_value >= 20_00_00_000 or one_week > 1 or volume_ratio >= 3)
    )


def describe_high_activity_signal(volume_ratio, change_percent, one_week, above_sma20):
    if volume_ratio and volume_ratio >= 3 and change_percent >= 5:
        return "Volume thrust with strong price expansion"
    if volume_ratio and volume_ratio >= 2.5 and above_sma20:
        return "High-volume accumulation above short-term trend"
    if volume_ratio and volume_ratio >= 2 and one_week and one_week > 5:
        return "Volume spike backing weekly momentum"
    if volume_ratio and volume_ratio >= 1.8:
        return "Above-average volume with positive close"
    return "Watch for volume confirmation"


def scan_order_catalyst_stock(stock):
    query = f"{stock['name']} order contract"
    endpoint = f"https://query2.finance.yahoo.com/v1/finance/search?q={quote(query)}&quotesCount=0&newsCount=5"
    payload = fetch_json(endpoint)
    matches = []
    for item in payload.get("news", [])[:5]:
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not title:
            continue
        text = f"{title} {summary}"
        if not has_order_catalyst(text):
            continue
        published_at = iso_from_epoch(item["providerPublishTime"]) if item.get("providerPublishTime") else None
        matches.append({
            "title": title,
            "publisher": item.get("publisher") or "",
            "publishedAt": published_at,
            "url": item.get("link") or "",
            "orderValue": extract_order_value(text),
            "score": score_order_headline(text, item.get("providerPublishTime")),
        })

    if not matches:
        return {"symbol": stock["symbol"], "name": stock["name"], "tags": stock["tags"], "matches": []}

    matches = sorted(matches, key=lambda item: item["score"], reverse=True)
    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "tags": stock["tags"],
        "matches": matches[:3],
    }


def build_order_catalysts(catalyst_items, activity_by_symbol):
    rows = []
    for item in catalyst_items:
        matches = item.get("matches") or []
        if not matches:
            continue
        activity = activity_by_symbol.get(item["symbol"], {})
        best = matches[0]
        score = round(clamp(
            best.get("score", 0)
            + min(activity.get("volumeRatio") or 0, 4) * 6
            + max(activity.get("changePercent") or 0, 0) * 2
            + max(activity.get("oneWeek") or 0, 0) * 0.8,
            0,
            100,
        ))
        rows.append({
            "symbol": item["symbol"],
            "name": item["name"],
            "tags": item.get("tags") or [],
            "price": activity.get("price"),
            "changePercent": activity.get("changePercent"),
            "volumeRatio": activity.get("volumeRatio"),
            "upsideToRecentHigh": activity.get("upsideToRecentHigh"),
            "headline": best.get("title"),
            "publisher": best.get("publisher"),
            "publishedAt": best.get("publishedAt"),
            "url": best.get("url"),
            "orderValue": best.get("orderValue"),
            "signal": describe_order_signal(best, activity),
            "score": score,
        })
    return sorted(rows, key=lambda row: row["score"], reverse=True)[:10]


def has_order_catalyst(text):
    lower_text = text.lower()
    return any(keyword in lower_text for keyword in ORDER_CATALYST_KEYWORDS)


def score_order_headline(text, published_epoch):
    lower_text = text.lower()
    order_score = sum(10 for keyword in ORDER_CATALYST_KEYWORDS if keyword in lower_text)
    upside_score = sum(4 for keyword in UPSIDE_CATALYST_KEYWORDS if keyword in lower_text)
    value_score = 12 if extract_order_value(text) else 0
    recency_score = 0
    if published_epoch:
        age_days = max(0, (time.time() - float(published_epoch)) / 86400)
        recency_score = clamp(22 - age_days * 2, 0, 22)
    return round(clamp(order_score + upside_score + value_score + recency_score, 0, 70))


def extract_order_value(text):
    match = re.search(
        r"(?:rs\.?|inr|₹)\s*([\d,.]+)\s*(crore|cr|lakh|million|billion)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    amount = match.group(1).replace(",", "")
    unit = match.group(2).lower()
    label = "crore" if unit in {"crore", "cr"} else unit
    return f"Rs {amount} {label}"


def describe_order_signal(headline, activity):
    order_value = headline.get("orderValue")
    volume_ratio = activity.get("volumeRatio") or 0
    change_percent = activity.get("changePercent") or 0
    if order_value and volume_ratio >= 1.8 and change_percent > 0:
        return "Order headline plus volume confirmation"
    if order_value:
        return "Order-value headline; wait for price confirmation"
    if volume_ratio >= 2 and change_percent > 0:
        return "Catalyst headline with volume pickup"
    return "Catalyst headline; verify exchange filing"


def build_commodity_impact(commodity_snapshots, scanned_stocks):
    quotes = {
        stock["symbol"]: {
            "regularMarketPrice": stock["price"],
            "regularMarketChangePercent": stock["changePercent"],
        }
        for stock in scanned_stocks
    }

    try:
        quotes.update(get_quotes(unique_impact_symbols()))
    except Exception:
        pass

    commodity_by_category = {}
    for commodity in commodity_snapshots:
        existing = commodity_by_category.get(commodity["category"])
        if not existing or abs(commodity["changePercent"]) > abs(existing["changePercent"]):
            commodity_by_category[commodity["category"]] = commodity

    groups = []
    for group in COMMODITY_IMPACT_GROUPS:
        commodity = commodity_by_category.get(group["commodity"])
        move = commodity.get("changePercent", 0) if commodity else 0
        direction = "up" if move > 0.25 else "down" if move < -0.25 else "flat"
        focus_symbols = (
            group["pressuredWhenUp"]
            if direction == "down"
            else [*group["beneficiariesWhenUp"], *group["pressuredWhenUp"]]
        )
        stocks = [
            stock_impact_snapshot(symbol, quotes.get(symbol), group, direction)
            for symbol in focus_symbols
        ]
        if stocks:
            groups.append({
                "commodity": group["commodity"],
                "reference": commodity["name"] if commodity else group["commodity"],
                "changePercent": round2(move),
                "direction": direction,
                "implication": (
                    group["whenUp"]
                    if direction == "up"
                    else group["whenDown"]
                    if direction == "down"
                    else "Move is small today; watch for follow-through."
                ),
                "stocks": stocks,
            })
    return groups


def stock_impact_snapshot(symbol, quote_data, group, direction):
    is_beneficiary_when_up = symbol in group["beneficiariesWhenUp"]
    quote_data = quote_data or {}
    return {
        "symbol": symbol,
        "name": watchlist_name(symbol),
        "role": impact_role(is_beneficiary_when_up, direction),
        "price": round_or_none(quote_data.get("regularMarketPrice")),
        "changePercent": round_or_none(quote_data.get("regularMarketChangePercent")),
    }


def impact_role(is_beneficiary_when_up, direction):
    if direction == "up":
        return "Potential beneficiary as commodity rises" if is_beneficiary_when_up else "Potential pressure as commodity rises"
    if direction == "down":
        return "May cool off as commodity falls" if is_beneficiary_when_up else "May benefit as input cost falls"
    return "Watch for positive commodity sensitivity" if is_beneficiary_when_up else "Watch for input-cost sensitivity"


def commodity_trend(closes):
    one_week = period_return(closes, 5)
    one_month = period_return(closes, 21)
    if one_week and one_month and one_week > 1 and one_month > 2:
        return "Rising"
    if one_week and one_month and one_week < -1 and one_month < -2:
        return "Falling"
    return "Mixed"


def describe_breakout_signal(data):
    if data.get("bullishReversal"):
        return "Bullish reversal confirmed"
    if data.get("trendReversal"):
        return "Trend reversal above SMA20"
    if data.get("reversalWatch"):
        return "Reversal watch near recent low"
    if data.get("narrowRange7Breakout"):
        return "52-week NR7 range breakout"
    if data.get("narrowRange4Breakout"):
        return "52-week NR4 range breakout"
    if data.get("narrowRange7Watch"):
        return "52-week NR7 breakout watch"
    if data.get("narrowRange4Watch"):
        return "52-week NR4 breakout watch"
    if data["breakout"] and data["nearAvailableHigh"]:
        return "Breakout near multi-year scan high"
    if data["breakout"]:
        return "Fresh breakout"
    if data["nearAvailableHigh"]:
        return "Near multi-year scan high"
    if data["near52WeekHigh"]:
        return "Near 52-week high"
    return "Close to breakout level"


def unique_impact_symbols():
    symbols = []
    for group in COMMODITY_IMPACT_GROUPS:
        symbols.extend(group["beneficiariesWhenUp"])
        symbols.extend(group["pressuredWhenUp"])
    return list(dict.fromkeys(symbols))


def watchlist_name(symbol):
    match = next((stock for stock in BREAKOUT_WATCHLIST if stock["symbol"] == symbol), None)
    return match["name"] if match else symbol


def resolve_symbol_input(value):
    normalized = normalize_symbol(value)
    if normalized:
        local_symbol = local_symbol_match(normalized)
        return local_symbol or normalized

    results = search_symbols(value)
    best = choose_search_result(results, value)
    return best["symbol"] if best else ""


def search_symbols(query):
    normalized_query = str(query or "").strip()

    def loader():
        endpoint = f"https://query2.finance.yahoo.com/v1/finance/search?q={quote(normalized_query)}&quotesCount=24&newsCount=0"
        local_results = local_search_symbols(normalized_query)
        try:
            payload = fetch_json(endpoint)
            yahoo_results = [
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("shortname") or item.get("longname") or item.get("symbol"),
                    "exchange": item.get("exchDisp") or item.get("exchange") or "",
                    "type": item.get("quoteType") or "",
                }
                for item in payload.get("quotes", [])
                if item.get("symbol") and normalized_quote_type(item.get("quoteType")) == "EQUITY"
            ]
        except Exception:
            yahoo_results = []
        return sort_search_results(merge_search_results(local_results + yahoo_results), normalized_query)[:24]

    return cached(f"search:{normalized_query.lower()}", loader, CACHE_TTL_SECONDS)


def instrument_suggestions(query):
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {"stocks": [], "etfs": [], "mutualFunds": []}

    return {
        "stocks": annotate_suggestions(safe_search_symbols(normalized_query), "stock", "Stock")[:8],
        "etfs": annotate_suggestions(asset_suggestions(normalized_query, "etf"), "etf", "ETF")[:6],
        "mutualFunds": annotate_suggestions(asset_suggestions(normalized_query, "mutual-fund"), "mutual-fund", "Mutual Fund")[:6],
    }


def asset_suggestions(query, asset_type):
    """Search results for the picker, widened so the panel is never empty.

    Resolution is deliberately strict (see ``asset_match_is_plausible``), which
    means a fund the provider does not carry - "SBI Bluechip Fund" - resolves to
    nothing. Suggestions are for browsing rather than acting, so fall back to
    any curated fund sharing a word with the query, then to the curated list, so
    "do you mean anything from below?" always has something below it.
    """
    results = safe_search_assets(query, asset_type)
    if results:
        return results

    tokens = [
        token for token in re.split(r"[^a-z0-9]+", str(query or "").lower())
        if len(token) > 2 and token not in FUND_NAME_STOPWORDS
    ]
    universe = ASSET_TYPE_CONFIG[normalize_asset_type(asset_type)]["universe"]
    type_label = "ETF" if asset_type == "etf" else "MUTUALFUND"

    def entry(symbol, name, exchange, tags):
        return {"symbol": symbol, "name": name, "exchange": exchange, "type": type_label, "tags": list(tags)}

    if tokens:
        matches = [
            entry(symbol, name, exchange, tags)
            for symbol, name, exchange, tags in universe
            if any(token in " ".join([symbol, name, *tags]).lower() for token in tokens)
        ]
        if matches:
            return matches

    return [entry(symbol, name, exchange, tags) for symbol, name, exchange, tags in universe[:6]]


def invalid_instrument_payload(query, resolved_symbol=""):
    """Error body for an instrument the tab cannot analyse.

    ``resolved_symbol`` is set when the input parsed to a valid-looking ticker but
    the provider had no data for it. TATAMOTORS.NS is the live example: it parses,
    it is in the curated universe, and it has 404ed since the demerger.

    That case needs its own answer for two reasons. The old message said the name
    was invalid when the name was fine and the listing had expired, and the
    suggestion list offered the failed symbol straight back as the top fix, so the
    only obvious next click reproduced the same error. The symbol is dropped from
    the suggestions here, because a symbol that just failed is not a fix for
    itself.
    """
    query_text = str(query or "").strip()
    symbol_text = str(resolved_symbol or "").strip()
    suggestions = instrument_suggestions(query)
    if symbol_text:
        suggestions = drop_symbol_from_suggestions(suggestions, symbol_text)

    return {
        "error": MISSING_INSTRUMENT_DATA_MESSAGE.format(symbol=symbol_text) if symbol_text else INVALID_INSTRUMENT_MESSAGE,
        "invalidInput": query_text,
        "resolvedSymbol": symbol_text,
        "suggestions": suggestions,
    }


def drop_symbol_from_suggestions(suggestions, symbol):
    target = str(symbol or "").strip().upper()
    if not target:
        return suggestions
    return {
        group: [item for item in rows if str(item.get("symbol") or "").strip().upper() != target]
        for group, rows in (suggestions or {}).items()
    }


def is_insufficient_history_error(message):
    return INSUFFICIENT_HISTORY_PREFIX.lower() in str(message or "").lower()


def is_invalid_instrument_error(message):
    text = str(message or "").lower()
    return any(pattern in text for pattern in [
        "data provider returned 404",
        "no data found",
        "symbol may be delisted",
        "chart data was not available",
    ])


def safe_search_symbols(query):
    try:
        return search_symbols(query)
    except Exception:
        return sort_search_results(local_search_symbols(query), query)[:24]


def safe_search_assets(query, asset_type):
    try:
        return search_assets(query, asset_type)
    except Exception:
        return sort_asset_search_results(local_asset_search_symbols(query, asset_type), query, asset_type)[:24]


def annotate_suggestions(results, kind, label):
    annotated = []
    for item in results:
        symbol = item.get("symbol")
        if not symbol:
            continue
        annotated.append({
            "symbol": symbol,
            "name": item.get("name") or symbol,
            "exchange": item.get("exchange") or exchange_label(symbol),
            "type": item.get("type") or label,
            "kind": kind,
            "label": label,
        })
    return annotated


def resolve_asset_input(value, asset_type):
    normalized_asset_type = normalize_asset_type(asset_type)
    normalized = normalize_symbol(value)
    if normalized:
        local_symbol = local_asset_symbol_match(normalized, normalized_asset_type)
        if local_symbol:
            return local_symbol
        if looks_like_asset_ticker(normalized):
            return normalized
        # A bare word such as "gold" or "nifty" also matches the ticker regex.
        # Treating it as a ticker used to hand the ETF/mutual-fund tabs a plain
        # equity (the MF tab resolved "gold" to the US ticker GOLD), so fall
        # through to search where the asset-type filter applies.

    results = search_assets(value, normalized_asset_type)
    best = choose_asset_search_result(results, value, normalized_asset_type)
    return best["symbol"] if best else ""


def looks_like_asset_ticker(symbol):
    """True when the input is specific enough to use without a type check.

    Qualified tickers ("NIFTYBEES.NS"), Yahoo fund ids ("0P0000YWL1.BO"), and
    index/currency forms are unambiguous. A bare alphabetic word is not - it
    could equally be a search term - so those are routed through search.
    """
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    return bool(re.search(r"[.\^=0-9]", normalized))


def local_symbol_match(symbol):
    normalized = str(symbol or "").strip().upper()
    if not normalized or "." in normalized or normalized.startswith("^") or "=" in normalized:
        return ""

    for stock in local_search_universe():
        local_symbol = stock["symbol"].upper()
        if local_symbol.split(".", 1)[0] == normalized:
            return stock["symbol"]
    return ""


def local_asset_symbol_match(symbol, asset_type):
    normalized = str(symbol or "").strip().upper()
    if not normalized or "." in normalized or normalized.startswith("^") or "=" in normalized:
        return ""

    for item_symbol, _name, _exchange, _tags in ASSET_TYPE_CONFIG[asset_type]["universe"]:
        if item_symbol.upper().split(".", 1)[0] == normalized:
            return item_symbol
    return ""


def search_assets(query, asset_type):
    normalized_query = str(query or "").strip()
    normalized_asset_type = normalize_asset_type(asset_type)
    config = ASSET_TYPE_CONFIG[normalized_asset_type]

    def loader():
        endpoint = f"https://query2.finance.yahoo.com/v1/finance/search?q={quote(normalized_query)}&quotesCount=24&newsCount=0"
        local_results = local_asset_search_symbols(normalized_query, normalized_asset_type)
        try:
            payload = fetch_json(endpoint)
            yahoo_results = [
                {
                    "symbol": item.get("symbol"),
                    # ``longname`` first: Yahoo sets ``shortname`` to the scheme
                    # id for Indian funds, which is useless in a picker.
                    "name": display_name(
                        [item.get("longname"), item.get("shortname")],
                        item.get("symbol"),
                    ),
                    "exchange": item.get("exchDisp") or item.get("exchange") or "",
                    "type": item.get("quoteType") or "",
                }
                for item in payload.get("quotes", [])
                if item.get("symbol") and normalized_quote_type(item.get("quoteType")) in config["quoteTypes"]
            ]
        except Exception:
            yahoo_results = []
        return sort_asset_search_results(
            merge_search_results(local_results + yahoo_results),
            normalized_query,
            normalized_asset_type,
        )[:24]

    return cached(f"asset-search:{normalized_asset_type}:{normalized_query.lower()}", loader, CACHE_TTL_SECONDS)


def local_asset_search_symbols(query, asset_type):
    lower_query = str(query or "").strip().lower()
    compact_query = re.sub(r"[^a-z0-9]", "", lower_query)
    if not compact_query:
        return []

    results = []
    for symbol, name, exchange, tags in ASSET_TYPE_CONFIG[asset_type]["universe"]:
        searchable = " ".join([symbol, name, exchange, *tags]).lower()
        compact_searchable = re.sub(r"[^a-z0-9]", "", searchable)
        fuzzy_score = fuzzy_candidate_score(compact_query, [symbol, name, exchange, *tags])
        if (
            lower_query not in searchable
            and compact_query not in compact_searchable
            and fuzzy_score < 62
        ):
            continue
        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "type": "ETF" if asset_type == "etf" else "MUTUALFUND",
            # Carried through to scoring so a thematic query ("nifty next 50")
            # ranks the fund tagged with it above an unrelated name match.
            "tags": list(tags),
        })

    return results


def display_name(candidates, symbol):
    """First candidate that is a real name rather than an echo of the symbol.

    Yahoo returns ``shortname``/``shortName`` equal to the symbol itself for
    Indian mutual funds (and some ETFs), which would otherwise surface as a
    fund called "0P0000YWL1.BO". Falls back to the symbol only if nothing
    usable is left.
    """
    target = str(symbol or "").strip().upper()
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or text.upper() == target:
            continue
        return text
    return str(symbol or "")


def local_asset_metadata(symbol, asset_type):
    target = str(symbol or "").upper()
    for item_symbol, name, exchange, tags in ASSET_TYPE_CONFIG[asset_type]["universe"]:
        if item_symbol.upper() == target:
            return {"name": name, "exchange": exchange, "tags": tags}
    return {}


def choose_asset_search_result(results, query, asset_type):
    """Best result that is actually the requested asset type, else nothing.

    Falling back to ``sorted_results[0]`` regardless of type is what let an
    equity answer an ETF/mutual-fund lookup. Returning None instead makes the
    caller emit the "invalid instrument" payload with suggestions.
    """
    sorted_results = sort_asset_search_results(results, query, asset_type)
    quote_types = ASSET_TYPE_CONFIG[asset_type]["quoteTypes"]
    return next(
        (
            item
            for item in sorted_results
            if normalized_quote_type(item.get("type")) in quote_types
            and asset_match_is_plausible(item, query)
        ),
        None,
    )


# Words that appear in almost every scheme name and so carry no signal about
# which fund the user meant.
FUND_NAME_STOPWORDS = frozenset({
    "fund", "funds", "scheme", "plan", "direct", "regular", "growth", "dir", "gr",
    "reg", "idcw", "dividend", "payout", "reinvestment", "option", "etf", "the",
    "of", "and", "index",
})


def asset_match_is_plausible(item, query):
    """Reject a match that misses a distinctive word from the query.

    The fuzzy search is deliberately loose so the suggestion list is useful
    while typing, but resolution has to be strict: "ICICI Prudential Value Fund
    Direct Growth" used to resolve to "Nippon India Small Cap Fund", quietly
    analysing a completely different fund. Requiring every significant query
    word to appear in the candidate makes an unavailable fund report as not
    found instead.
    """
    tokens = [
        token for token in re.split(r"[^a-z0-9]+", str(query or "").lower())
        if token and token not in FUND_NAME_STOPWORDS
    ]
    if not tokens:
        return True

    haystack = re.sub(
        r"[^a-z0-9]",
        "",
        " ".join([
            str(item.get("name") or ""),
            str(item.get("symbol") or ""),
            *[str(tag or "") for tag in (item.get("tags") or [])],
        ]).lower(),
    )
    # Substring rather than word match so "flexicap" still matches "Flexi Cap".
    return all(token in haystack for token in tokens)


def sort_asset_search_results(results, query, asset_type):
    quote_types = ASSET_TYPE_CONFIG[asset_type]["quoteTypes"]
    lower_query = str(query or "").lower()
    return sorted(
        results,
        key=lambda item: (
            0 if normalized_quote_type(item.get("type")) in quote_types else 1,
            exchange_priority(item),
            -search_score(item, lower_query),
            fund_plan_rank(item, asset_type),
            item.get("symbol", ""),
        ),
    )


# Indian schemes list one entry per plan/option, so a search for a fund house
# returns a dozen near-identical rows. Growth plans are the ones people analyse
# (IDCW variants pay out and distort NAV history), and Direct plans carry the
# lower expense ratio, so surface those first on equal name relevance.
FUND_PLAN_PREFERENCE = (
    ("dir gr", 0),
    ("direct gr", 0),
    ("reg gr", 1),
    ("regular gr", 1),
)


def fund_plan_rank(item, asset_type):
    if asset_type != "mutual-fund":
        return 0

    name = str(item.get("name") or "").lower()
    for marker, rank in FUND_PLAN_PREFERENCE:
        if marker in name:
            return rank
    if "idcw" in name or "dividend" in name:
        return 3
    return 2


def normalize_asset_type(asset_type):
    normalized = str(asset_type or "").strip().lower().replace("_", "-")
    if normalized not in ASSET_TYPE_CONFIG:
        raise ValueError("Asset type must be etf or mutual-fund.")
    return normalized


def normalized_quote_type(value):
    return str(value or "").strip().upper().replace("-", "_")


def local_search_symbols(query):
    lower_query = str(query or "").strip().lower()
    compact_query = re.sub(r"[^a-z0-9]", "", lower_query)
    if not compact_query:
        return []

    results = []
    for stock in local_search_universe():
        symbol = stock["symbol"]
        base_symbol = symbol.removesuffix(".NS")
        name = stock["name"]
        searchable = " ".join([symbol, base_symbol, name, *stock.get("tags", [])]).lower()
        compact_searchable = re.sub(r"[^a-z0-9]", "", searchable)
        fuzzy_score = fuzzy_candidate_score(compact_query, [symbol, base_symbol, name, *stock.get("tags", [])])
        if (
            lower_query not in searchable
            and compact_query not in compact_searchable
            and fuzzy_score < 62
        ):
            continue

        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": "NSE",
            "type": "EQUITY",
        })
        if symbol.endswith(".NS"):
            results.append({
                "symbol": f"{base_symbol}.BO",
                "name": name,
                "exchange": "BSE",
                "type": "EQUITY",
            })

    return results


def local_search_universe():
    stocks = {}
    for stock in [*BREAKOUT_WATCHLIST, *HIGH_ACTIVITY_WATCHLIST]:
        stocks.setdefault(stock["symbol"], stock)
    return stocks.values()


def merge_search_results(results):
    merged = {}
    for item in results:
        symbol = (item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        existing = merged.get(symbol, {})
        merged[symbol] = {
            "symbol": symbol,
            "name": item.get("name") or existing.get("name") or symbol,
            "exchange": item.get("exchange") or existing.get("exchange") or exchange_label(symbol),
            "type": item.get("type") or existing.get("type") or "",
            "tags": item.get("tags") or existing.get("tags") or [],
        }
    return list(merged.values())


def choose_search_result(results, query):
    sorted_results = sort_search_results(results, query)
    return next((item for item in sorted_results if item.get("type") == "EQUITY"), None) or (results[0] if results else None)


def sort_search_results(results, query):
    lower_query = str(query or "").lower()
    return sorted(
        results,
        key=lambda item: (exchange_priority(item), -search_score(item, lower_query), item.get("symbol", "")),
    )


def exchange_priority(item):
    symbol = item.get("symbol", "").upper()
    exchange = item.get("exchange", "").upper()
    if symbol.endswith(".NS") or exchange in {"NSE", "NSI"}:
        return 0
    if symbol.endswith(".BO") or exchange in {"BSE", "BOM"}:
        return 1
    return 2


def exchange_label(symbol):
    if symbol.endswith(".NS"):
        return "NSE"
    if symbol.endswith(".BO"):
        return "BSE"
    return ""


def search_score(item, lower_query):
    score = 20 if item.get("type") == "EQUITY" else 0
    symbol = item.get("symbol", "").upper()
    compact_query = re.sub(r"[^a-z0-9]", "", lower_query)
    compact_symbol = re.sub(r"[^a-z0-9]", "", symbol.lower())
    name = item.get("name", "").lower()
    compact_name = re.sub(r"[^a-z0-9]", "", name)
    if compact_query and compact_symbol.startswith(compact_query):
        score += 35
    elif compact_query and compact_query in compact_symbol:
        score += 24
    if lower_query and name.startswith(lower_query):
        score += 28
    elif lower_query and lower_query in name:
        score += 16
    if compact_query and compact_query in compact_name:
        score += 10
    # Tags describe what a fund tracks ("Nifty Next 50", "Gold"), which is often
    # what the query says even when the fund name never spells it out.
    tags = [str(tag or "").lower() for tag in (item.get("tags") or [])]
    if lower_query and any(lower_query == tag for tag in tags):
        score += 30
    elif lower_query and any(lower_query in tag for tag in tags):
        score += 14
    fuzzy_score = fuzzy_candidate_score(compact_query, [symbol, name, *tags])
    if fuzzy_score >= 82:
        score += 26
    elif fuzzy_score >= 70:
        score += 18
    elif fuzzy_score >= 62:
        score += 10
    return score


def fuzzy_candidate_score(compact_query, values):
    if not compact_query or len(compact_query) < 2:
        return 0

    best = 0
    for value in values:
        compact_value = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
        if not compact_value:
            continue
        if compact_value.startswith(compact_query):
            best = max(best, 96)
            continue
        if compact_query in compact_value:
            best = max(best, 88)
            continue
        prefix_length = min(len(compact_value), max(len(compact_query) + 4, 5))
        prefix_value = compact_value[:prefix_length]
        best = max(
            best,
            int(SequenceMatcher(None, compact_query, prefix_value).ratio() * 100),
            int(SequenceMatcher(None, compact_query, compact_value).ratio() * 100),
        )
    return best


def get_chart(symbol):
    chart = get_chart_range(symbol, "2y", "1d")
    return {
        **chart,
        "candles": stock_analysis_window(chart.get("candles") or []),
    }


def stock_analysis_window(candles):
    return (candles or [])[-STOCK_ANALYSIS_MAX_SESSIONS:]


def stock_report_window(candles):
    return (candles or [])[-STOCK_REPORT_MAX_SESSIONS:]


def benchmark_symbol_for(symbol):
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return "^NSEI"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol or ""):
        return "^GSPC"
    return None


def get_benchmark_chart(benchmark_symbol):
    chart = get_chart(benchmark_symbol)
    return {
        "symbol": benchmark_symbol,
        "name": benchmark_name_for(benchmark_symbol),
        "candles": chart.get("candles") or [],
    }


def get_asset_benchmark_chart(benchmark_symbol):
    """Benchmark history over the same window the fund/ETF is analysed on.

    ``get_benchmark_chart`` uses the shorter stock window, which would leave the
    rolling-return and capture-ratio comparisons with nothing to line up against.
    """
    chart = get_chart_range(benchmark_symbol, ASSET_HISTORY_RANGE, "1d")
    return {
        "symbol": benchmark_symbol,
        "name": benchmark_name_for(benchmark_symbol),
        "candles": chart.get("candles") or [],
    }


def benchmark_name_for(benchmark_symbol):
    names = {
        "^NSEI": "Nifty 50",
        "^GSPC": "S&P 500",
    }
    return names.get(benchmark_symbol, benchmark_symbol or "Benchmark")


def get_chart_range(symbol, range_value="1y", interval="1d"):
    return cached(
        f"chart:{symbol}:{range_value}:{interval}",
        lambda: fetch_chart_range(symbol, range_value, interval),
        5 * 60,
    )


def fetch_chart_range(symbol, range_value="1y", interval="1d"):
    endpoint = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol)}?range={quote(range_value)}&interval={quote(interval)}&includePrePost=false&events=div%2Csplits"
    )
    payload = fetch_json(endpoint)
    chart = payload.get("chart") or {}
    result = (chart.get("result") or [None])[0]
    yahoo_error = chart.get("error")
    if not result:
        detail = yahoo_error.get("description") if isinstance(yahoo_error, dict) else None
        raise RuntimeError(detail or "Chart data was not available.")

    quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    adjclose = ((result.get("indicators") or {}).get("adjclose") or [{}])[0] or {}
    timestamps = result.get("timestamp") or []
    candles = []
    for index, timestamp in enumerate(timestamps):
        candle = {
            "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat(),
            "open": clean_number(array_get(quote_data.get("open"), index)),
            "high": clean_number(array_get(quote_data.get("high"), index)),
            "low": clean_number(array_get(quote_data.get("low"), index)),
            "close": clean_number(array_get(quote_data.get("close"), index)),
            "adjClose": clean_number(array_get(adjclose.get("adjclose"), index)),
            "volume": clean_number(array_get(quote_data.get("volume"), index)),
        }
        if all(is_finite(candle[key]) for key in ["open", "high", "low", "close"]):
            candles.append(candle)
    candles.sort(key=lambda candle: candle["date"])
    return {"meta": result.get("meta") or {}, "candles": candles}


EXCHANGE_CODE_TO_NAME = {
    "NSI": "NSE",
    "BSE": "BSE",
    "NMS": "NasdaqGS",
    "NGM": "NasdaqGM",
    "NCM": "NasdaqCM",
    "NYQ": "NYSE",
    "PCX": "NYSEArca",
    "ASE": "NYSEAmerican",
    "LSE": "LSE",
}


def quote_from_chart_meta(symbol):
    """A quote assembled from the chart endpoint's ``meta`` block.

    Yahoo's ``/v7/finance/quote`` now answers 401 to anonymous callers and its
    crumb handshake is itself rate limited, so the batch quote is not something
    to depend on. ``/v8/finance/chart`` still answers unauthenticated and its
    meta carries the same last price, day move, day range, 52-week range, volume
    and names, keyed slightly differently. Every field below is a rename of one
    already present, so nothing here is estimated.
    """
    payload = fetch_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range=5d&interval=1d"
    )
    meta = (((payload.get("chart") or {}).get("result") or [{}])[0] or {}).get("meta") or {}
    # A retired symbol is not always a 404. Yahoo answers HPCL.NS with 200, the
    # symbol echoed back, and every price field null - a shape that passes a
    # symbol check and then supplies nothing. Requiring a price is what actually
    # separates a quote from an acknowledgement.
    if not meta.get("symbol") or not is_finite(meta.get("regularMarketPrice")):
        return {}
    exchange_code = meta.get("exchangeName") or ""
    return {
        "symbol": meta.get("symbol"),
        "shortName": meta.get("shortName"),
        "longName": meta.get("longName") or meta.get("shortName"),
        "currency": meta.get("currency"),
        "exchange": exchange_code,
        "fullExchangeName": meta.get("fullExchangeName")
        or EXCHANGE_CODE_TO_NAME.get(exchange_code, exchange_code),
        "exchangeTimezoneName": meta.get("exchangeTimezoneName"),
        "quoteType": meta.get("instrumentType"),
        "regularMarketPrice": clean_number(meta.get("regularMarketPrice")),
        "regularMarketChangePercent": clean_number(meta.get("regularMarketChangePercent")),
        "regularMarketVolume": clean_number(meta.get("regularMarketVolume")),
        "regularMarketTime": meta.get("regularMarketTime"),
        "regularMarketDayHigh": clean_number(meta.get("regularMarketDayHigh")),
        "regularMarketDayLow": clean_number(meta.get("regularMarketDayLow")),
        "previousClose": clean_number(meta.get("chartPreviousClose")),
        "fiftyTwoWeekHigh": clean_number(meta.get("fiftyTwoWeekHigh")),
        "fiftyTwoWeekLow": clean_number(meta.get("fiftyTwoWeekLow")),
    }


def get_quote(symbol):
    endpoint = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote(symbol)}"
    try:
        payload = fetch_json(endpoint)
        quote_item = ((payload.get("quoteResponse") or {}).get("result") or [{}])[0] or {}
        if quote_item.get("symbol"):
            return quote_item
    except Exception:
        pass
    return quote_from_chart_meta(symbol)


def get_quotes(symbols):
    unique_symbols = list(dict.fromkeys([symbol for symbol in symbols if symbol]))
    if not unique_symbols:
        return {}

    quotes = {}
    endpoint = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote(','.join(unique_symbols))}"
    try:
        payload = fetch_json(endpoint)
        for quote_item in (payload.get("quoteResponse") or {}).get("result") or []:
            if quote_item.get("symbol"):
                quotes[quote_item["symbol"]] = quote_item
    except Exception:
        pass

    # The batch call covers every symbol in one request when it works, so fall
    # back only for what is still missing. Whole-batch failure is the common case
    # while the 401 stands, and a partial answer is also possible: a delisted or
    # renamed ticker is simply absent from the result array.
    missing = [symbol for symbol in unique_symbols if symbol not in quotes]
    if not missing:
        return quotes
    for symbol, (ok, value) in zip(
        missing, settle_map(missing, quote_from_chart_meta, concurrency=QUOTE_FALLBACK_CONCURRENCY)
    ):
        if ok and value.get("symbol"):
            quotes[symbol] = value
    return quotes


def get_summary(symbol):
    endpoint = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}?modules={MODULES}"
    payload = fetch_json(endpoint)
    summary = payload.get("quoteSummary") or {}
    if summary.get("error"):
        raise RuntimeError(summary["error"].get("description") or "Fundamental data was not available.")
    return (summary.get("result") or [{}])[0] or {}


def get_asset_summary(symbol):
    modules = ",".join([
        "price",
        "summaryDetail",
        "defaultKeyStatistics",
        "fundProfile",
        "topHoldings",
        "fundPerformance",
    ])
    endpoint = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}?modules={modules}"
    payload = fetch_json(endpoint)
    summary = payload.get("quoteSummary") or {}
    if summary.get("error"):
        raise RuntimeError(summary["error"].get("description") or "Fund data was not available.")
    return (summary.get("result") or [{}])[0] or {}


def get_sec_fundamentals(symbol):
    if not re.fullmatch(r"[A-Z]{1,5}", symbol or ""):
        return {}

    ticker_map = cached(
        "sec:company-tickers",
        lambda: fetch_json("https://www.sec.gov/files/company_tickers.json", sec=True),
        SEC_CACHE_TTL_SECONDS,
    )
    company = next(
        (item for item in ticker_map.values() if item.get("ticker", "").upper() == symbol),
        None,
    )
    if not company:
        return {}

    cik = str(company["cik_str"]).zfill(10)
    facts = cached(
        f"sec:companyfacts:{cik}",
        lambda: fetch_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", sec=True),
        SEC_CACHE_TTL_SECONDS,
    )
    return extract_sec_metrics(facts, company.get("title", ""))


def get_screener_fundamentals(symbol):
    if not (symbol.endswith(".NS") or symbol.endswith(".BO")):
        return {}
    screener_symbol = re.sub(r"\.(NS|BO)$", "", symbol, flags=re.IGNORECASE)
    page = cached(
        f"screener:{screener_symbol}",
        lambda: fetch_text(f"https://www.screener.in/company/{quote(screener_symbol)}/"),
        SEC_CACHE_TTL_SECONDS,
    )
    screener = extract_screener_metrics(page)

    if not has_shareholding_categories(screener.get("shareholding"), ("Promoters", "FIIs", "DIIs")):
        consolidated_page = cached(
            f"screener-consolidated:{screener_symbol}",
            lambda: fetch_text(f"https://www.screener.in/company/{quote(screener_symbol)}/consolidated/"),
            SEC_CACHE_TTL_SECONDS,
        )
        consolidated = extract_screener_metrics(consolidated_page)
        screener["shareholding"] = merge_shareholding(screener.get("shareholding"), consolidated.get("shareholding"))
        screener["metrics"]["promoterHolding"] = first_present(
            screener["metrics"].get("promoterHolding"),
            consolidated.get("metrics", {}).get("promoterHolding"),
            latest_shareholding_value(screener.get("shareholding"), "Promoters"),
        )

    return screener


def build_report(symbol, meta, candles, quote_data, summary, sec, screener, benchmark=None, open_interest=None):
    analysis_candles = stock_analysis_window(candles)
    report_candles = stock_report_window(analysis_candles)
    closes = [item["close"] for item in report_candles]
    analysis_closes = [item["close"] for item in analysis_candles]
    analysis_volumes = [item.get("volume") or 0 for item in analysis_candles]
    current = closes[-1]
    previous = closes[-2] if len(closes) > 1 else current
    currency = first_present(
        quote_data.get("currency"),
        meta.get("currency"),
        raw((summary.get("price") or {}).get("currency")),
        "",
    )
    long_name = first_present(
        quote_data.get("longName"),
        quote_data.get("shortName"),
        (summary.get("price") or {}).get("longName"),
        (summary.get("price") or {}).get("shortName"),
        sec.get("companyName"),
        screener.get("companyName"),
        symbol,
    )
    screener = ensure_shareholding_data(symbol, long_name, screener)

    sma20 = sma(analysis_closes, 20)
    sma50 = sma(analysis_closes, 50)
    sma200 = sma(analysis_closes, 200)
    ema12 = ema(analysis_closes, 12)
    ema26 = ema(analysis_closes, 26)
    rsi14 = rsi(analysis_closes, 14)
    macd_data = macd(analysis_closes)
    atr14 = atr(analysis_candles, 14)
    bands = bollinger(analysis_closes, 20, 2)
    avg_volume_20 = last(sma(analysis_volumes, 20))
    volume_ratio = (analysis_volumes[-1] or 0) / avg_volume_20 if avg_volume_20 else None
    year_high = max(item["high"] for item in report_candles)
    year_low = min(item["low"] for item in report_candles)
    support_resistance = find_levels(report_candles, current, last(atr14))
    fundamentals = extract_fundamentals(summary, quote_data, meta, sec, screener, current)
    events = extract_events(summary, screener)
    sector_analysis = stock_sector_analysis(fundamentals)
    growth_drivers = build_growth_drivers(symbol, long_name, fundamentals, screener, sector_analysis)
    relative_strength = build_relative_strength(report_candles, benchmark)
    latest_candle = analyze_latest_candle(report_candles)
    technical = score_technical({
        "current": current,
        "yearHigh": year_high,
        "yearLow": year_low,
        "sma20": last(sma20),
        "sma50": last(sma50),
        "sma200": last(sma200),
        "rsi14": last(rsi14),
        "macdLine": last(macd_data["line"]),
        "macdSignal": last(macd_data["signal"]),
        "macdHist": last(macd_data["histogram"]),
        "volumeRatio": volume_ratio,
        "atrPercent": safe_divide(last(atr14), current) * 100,
    })
    fundamental = score_fundamental(fundamentals)
    event_risk = score_event_risk(events)
    research_levels = build_research_levels({
        "current": current,
        "support": array_get(support_resistance["supports"], 0),
        "resistance": array_get(support_resistance["resistances"], 0),
        "atrValue": last(atr14),
        "yearHigh": year_high,
        "yearLow": year_low,
        "technicalScore": technical["score"],
    })
    overall_score = weighted_average([
        (technical["score"], 0.45),
        (fundamental["score"], 0.4),
        (100 - event_risk["score"], 0.15),
    ])
    outlook = build_outlook(overall_score, technical["score"], fundamental["score"], event_risk)
    source = build_source_label(sec, screener)
    market_clock = build_market_clock(symbol, quote_data, meta)
    quality = build_quality_report({
        "candles": report_candles,
        "quote": quote_data,
        "fundamentals": fundamentals,
        "technical": technical,
        "fundamental": fundamental,
        "eventRisk": event_risk,
        "supportResistance": support_resistance,
        "source": source,
        "symbol": symbol,
        "ownership": growth_drivers.get("ownership"),
        "relativeStrength": relative_strength,
    })
    scenarios = build_scenarios({
        "current": current,
        "technical": technical,
        "fundamental": fundamental,
        "eventRisk": event_risk,
        "researchLevels": research_levels,
        "supportResistance": support_resistance,
        "avgVolume20": avg_volume_20,
        "volumeRatio": volume_ratio,
    })
    swing_trade_plan = build_swing_trade_plan({
        "current": current,
        "yearHigh": year_high,
        "yearLow": year_low,
        "technical": technical,
        "fundamental": fundamental,
        "eventRisk": event_risk,
        "researchLevels": research_levels,
        "supportResistance": support_resistance,
        "avgVolume20": avg_volume_20,
        "volumeRatio": volume_ratio,
        "atrValue": last(atr14),
        "atrPercent": safe_divide(last(atr14), current) * 100,
        "sma20": last(sma20),
        "sma50": last(sma50),
        "sma200": last(sma200),
        "rsi14": last(rsi14),
        "performance": {
            "oneWeek": period_return(closes, 5),
            "oneMonth": period_return(closes, 21),
            "threeMonth": period_return(closes, 63),
            "sixMonth": period_return(closes, 126),
        },
    })
    references = build_references(symbol, long_name, quote_data, meta)
    series = [
        {
            "date": item["date"],
            "open": round2(item["open"]),
            "high": round2(item["high"]),
            "low": round2(item["low"]),
            "close": round2(item["close"]),
            "volume": item.get("volume") or 0,
            "sma20": round_or_none(sma20[index]),
            "sma50": round_or_none(sma50[index]),
            "sma200": round_or_none(sma200[index]),
        }
        for index, item in enumerate(report_candles, start=len(analysis_candles) - len(report_candles))
    ]

    return {
        "symbol": symbol,
        "longName": long_name,
        "currency": currency,
        "source": source,
        "generatedAt": iso_now(),
        "marketClock": market_clock,
        "history": {
            "chartCandles": len(report_candles),
            "analysisCandles": len(analysis_candles),
            "maxChartSessions": STOCK_REPORT_MAX_SESSIONS,
            "analysisBufferSessions": max(0, len(analysis_candles) - len(report_candles)),
        },
        "quote": {
            "price": round2(current),
            "previousClose": round2(previous),
            "change": round2(current - previous),
            "changePercent": round2(safe_divide(current - previous, previous) * 100),
            "marketTime": iso_from_epoch(quote_data.get("regularMarketTime")) if quote_data.get("regularMarketTime") else None,
            "exchange": quote_data.get("fullExchangeName") or quote_data.get("exchange") or meta.get("exchangeName") or "",
            "range": {
                "high52w": round2(year_high),
                "low52w": round2(year_low),
            },
        },
        "scores": {
            "overall": round(overall_score),
            "technical": technical["score"],
            "fundamental": fundamental["score"],
            "eventRisk": event_risk["score"],
            "confidence": quality["score"],
        },
        "outlook": outlook,
        "quality": quality,
        "scenarios": scenarios,
        "swingTradePlan": swing_trade_plan,
        "references": references,
        "technical": {
            "score": technical["score"],
            "summary": technical["summary"],
            "signals": technical["signals"],
            "indicators": {
                "sma20": round_or_none(last(sma20)),
                "sma50": round_or_none(last(sma50)),
                "sma200": round_or_none(last(sma200)),
                "ema12": round_or_none(last(ema12)),
                "ema26": round_or_none(last(ema26)),
                "rsi14": round_or_none(last(rsi14)),
                "macd": round_or_none(last(macd_data["line"])),
                "macdSignal": round_or_none(last(macd_data["signal"])),
                "macdHistogram": round_or_none(last(macd_data["histogram"])),
                "atr14": round_or_none(last(atr14)),
                "atrPercent": round_or_none(safe_divide(last(atr14), current) * 100),
                "bollingerUpper": round_or_none(last(bands["upper"])),
                "bollingerMiddle": round_or_none(last(bands["middle"])),
                "bollingerLower": round_or_none(last(bands["lower"])),
                "avgVolume20": round(avg_volume_20 or 0),
                "volumeRatio": round_or_none(volume_ratio),
            },
            "performance": {
                "oneMonth": round_or_none(period_return(closes, 21)),
                "threeMonth": round_or_none(period_return(closes, 63)),
                "sixMonth": round_or_none(period_return(closes, 126)),
                "oneYear": round_or_none(period_return(closes, len(closes) - 1)),
            },
            "relativeStrength": relative_strength,
            "levels": support_resistance,
            "latestCandle": latest_candle,
        },
        "fundamentals": {
            "score": fundamental["score"],
            "summary": fundamental["summary"],
            "signals": fundamental["signals"],
            "metrics": fundamentals,
        },
        "events": {
            "risk": event_risk,
            "items": events,
        },
        "growthDrivers": growth_drivers,
        "openInterest": open_interest or open_interest_unavailable(symbol),
        "researchLevels": research_levels,
        "series": series,
    }


def build_asset_report(symbol, asset_type, meta, candles, quote_data, summary, benchmark=None):
    closes = [item["close"] for item in candles]
    volumes = [item.get("volume") or 0 for item in candles]
    current = closes[-1]
    previous = closes[-2] if len(closes) > 1 else current
    local_meta = local_asset_metadata(symbol, asset_type)
    currency = first_present(
        quote_data.get("currency"),
        meta.get("currency"),
        raw((summary.get("price") or {}).get("currency")),
        "",
    )
    asset_label = ASSET_TYPE_CONFIG[asset_type]["label"]
    price = summary.get("price") or {}
    # Yahoo's quote/quoteSummary endpoints now answer 401 for anonymous callers,
    # so ``quote_data``/``summary`` are usually empty and the chart ``meta`` is
    # the only name source. For Indian mutual funds Yahoo also mirrors the
    # opaque scheme id ("0P0000YWL1.BO") into shortName, so a name equal to the
    # symbol has to be rejected instead of shown as the fund name.
    long_name = display_name(
        [
            # Curated name first: the provider often returns an internal code
            # ("HDFCAMC - HDFCNIFTY") or a triple-prefixed legal name for Indian
            # funds. Anything outside the curated list still falls back to the
            # provider, so a renamed fund only reads stale if we listed it.
            local_meta.get("name"),
            quote_data.get("longName"),
            quote_data.get("shortName"),
            price.get("longName"),
            price.get("shortName"),
            meta.get("longName"),
            meta.get("shortName"),
        ],
        symbol,
    )
    sma50_value = last(sma(closes, 50))
    sma200_value = last(sma(closes, 200))
    atr14 = atr(candles, 14)
    levels = find_levels(candles[-ASSET_LEVEL_WINDOW_SESSIONS:], current, last(atr14))
    profile = extract_asset_profile(
        summary, quote_data, meta, asset_type, {**local_meta, "latestClose": current},
    )
    top_holdings = extract_asset_holdings(summary)
    sectors = extract_sector_weightings(summary)
    # Returns, risk, and the benchmark comparison run on the adjusted series;
    # price, moving averages, and levels stay on the traded close.
    return_candles, return_basis = build_asset_return_series(candles)
    return_closes = [item["close"] for item in return_candles]
    performance = build_asset_performance(return_closes)
    rolling_returns = build_asset_rolling_returns(return_closes)
    annual_returns = build_mutual_fund_annual_return_report(long_name, profile) if asset_type == "mutual-fund" else unavailable_annual_returns(asset_type)
    risk = build_asset_risk_report(return_closes)
    risk_adjusted = build_asset_risk_adjusted_report(return_closes, currency)
    benchmark_report = build_asset_benchmark_report(
        return_candles, benchmark, asset_type, long_name, local_meta.get("tags") or [],
    )
    momentum = build_asset_momentum_report(current, sma50_value, sma200_value, performance)
    freshness = build_asset_freshness(candles, asset_type)
    confidence = build_asset_confidence_report(candles, profile, top_holdings, summary, annual_returns, freshness)
    plan = build_asset_plan(asset_type, current, currency, levels, performance, risk, profile)
    suitability_score = score_asset_suitability(
        momentum, risk, confidence, profile, risk_adjusted, rolling_returns, benchmark_report,
    )
    suitability = build_asset_suitability_report(
        suitability_score, confidence, freshness, risk_adjusted, rolling_returns, benchmark_report,
    )
    references = build_asset_references(symbol, long_name, quote_data, meta, asset_type)

    series = [
        {
            "date": item["date"],
            "open": round2(item["open"]),
            "high": round2(item["high"]),
            "low": round2(item["low"]),
            "close": round2(item["close"]),
            "volume": item.get("volume") or 0,
        }
        for item in candles[-260:]
    ]

    return {
        "symbol": symbol,
        "longName": long_name,
        "assetType": asset_type,
        "assetLabel": asset_label,
        "currency": currency,
        "source": "Yahoo Finance public endpoints",
        "generatedAt": iso_now(),
        "quote": {
            "price": round2(current),
            "previousClose": round2(previous),
            "change": round2(current - previous),
            "changePercent": round2(safe_divide(current - previous, previous) * 100),
            "marketTime": iso_from_epoch(quote_data.get("regularMarketTime")) if quote_data.get("regularMarketTime") else None,
            "exchange": quote_data.get("fullExchangeName") or quote_data.get("exchange") or meta.get("exchangeName") or local_meta.get("exchange") or "",
            "quoteType": quote_data.get("quoteType") or price.get("quoteType") or "",
        },
        "scores": {
            "suitability": suitability_score,
            "momentum": momentum["score"],
            "risk": risk["score"],
            "confidence": confidence["score"],
        },
        "summary": summarize_asset_report(asset_label, suitability_score, momentum, risk, profile, risk_adjusted, benchmark_report),
        "freshness": freshness,
        "profile": profile,
        "returnBasis": return_basis,
        "performance": performance,
        "rollingReturns": rolling_returns,
        "annualReturns": annual_returns,
        "risk": risk,
        "riskAdjusted": risk_adjusted,
        "benchmark": benchmark_report,
        "suitability": suitability,
        "momentum": momentum,
        "confidence": confidence,
        "holdings": {
            "top": top_holdings,
            "sectors": sectors,
        },
        "plan": plan,
        "levels": levels,
        "references": references,
        "series": series,
        "volume": {
            "average20": round(last(sma(volumes, 20)) or 0),
            "latest": round(volumes[-1] or 0),
        },
    }


def extract_asset_profile(summary, quote_data, meta, asset_type, local_meta=None):
    local_meta = local_meta or {}
    price = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    key = summary.get("defaultKeyStatistics") or {}
    profile = summary.get("fundProfile") or {}
    return {
        # Yahoo answers 401 to anonymous quoteSummary calls, so for most Indian
        # symbols every field below is empty. The client renders a missing value
        # as "Loading, ETA 10-30s", which told the reader to wait for data that
        # is never going to arrive and made a finished report look half-built.
        # This flag lets it say "not published by the data source" instead.
        "detailsAvailable": bool(price or detail or key or profile),
        "category": first_present(raw(profile.get("categoryName")), raw(detail.get("category")), quote_data.get("market"), ", ".join(local_meta.get("tags") or [])),
        "family": first_present(raw(profile.get("family")), quote_data.get("fundFamily")),
        "legalType": first_present(raw(profile.get("legalType")), ASSET_TYPE_CONFIG[asset_type]["label"]),
        "totalAssets": first_present(raw(detail.get("totalAssets")), raw(key.get("totalAssets")), quote_data.get("totalAssets")),
        # The chart's last close is the same NAV the quote panel shows, so falling
        # back to it means this row is answered from data that did load rather
        # than reported missing alongside the genuinely absent factsheet fields.
        "navPrice": first_present(
            raw(detail.get("navPrice")),
            raw(price.get("regularMarketPrice")),
            quote_data.get("regularMarketPrice"),
            meta.get("regularMarketPrice"),
            (local_meta or {}).get("latestClose"),
        ),
        "yield": first_present(raw(detail.get("yield")), raw(key.get("yield")), quote_data.get("yield")),
        "expenseRatio": first_present(raw(detail.get("annualReportExpenseRatio")), raw(key.get("annualReportExpenseRatio")), raw(profile.get("annualReportExpenseRatio"))),
        "turnover": first_present(raw(profile.get("annualHoldingsTurnover")), raw(key.get("annualHoldingsTurnover"))),
        "beta3Year": first_present(raw(key.get("beta3Year")), raw(detail.get("beta3Year"))),
        "ytdReturn": first_present(raw(key.get("ytdReturn")), raw(detail.get("ytdReturn"))),
        "inceptionDate": iso_from_epoch(raw(key.get("fundInceptionDate"))) if raw(key.get("fundInceptionDate")) else None,
        "exchange": quote_data.get("fullExchangeName") or quote_data.get("exchange") or meta.get("exchangeName") or local_meta.get("exchange") or "",
        "quoteType": quote_data.get("quoteType") or price.get("quoteType") or "",
    }


def extract_asset_holdings(summary):
    top_holdings = summary.get("topHoldings") or {}
    holdings = []
    for item in arrayify(top_holdings.get("holdings"))[:10]:
        name = first_present(item.get("holdingName"), item.get("symbol"), item.get("name"))
        if not name:
            continue
        percent = first_present(raw(item.get("holdingPercent")), raw(item.get("holdingPercentage")))
        holdings.append({
            "symbol": item.get("symbol") or "",
            "name": name,
            "percent": round_ratio_or_none(percent),
        })
    return holdings


def extract_sector_weightings(summary):
    top_holdings = summary.get("topHoldings") or {}
    sectors = []
    raw_sectors = top_holdings.get("sectorWeightings") or []
    for item in arrayify(raw_sectors):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            percent = raw(value)
            if not is_finite(percent):
                continue
            sectors.append({
                "name": humanize_sector_key(key),
                "percent": round_ratio_or_none(percent),
            })
    return sorted(sectors, key=lambda item: item["percent"] or 0, reverse=True)[:8]


def build_asset_return_series(candles):
    """Candles suitable for measuring returns over long horizons.

    The raw ``close`` series steps at every corporate action the provider has not
    adjusted for, so a decade of NIFTYBEES showed a 90% "drawdown" and a 1013%
    best rolling year that were purely a split divisor. Yahoo's ``adjClose`` is
    used when it covers the series, but for Indian ETFs it is frequently just a
    copy of ``close`` and fixes nothing, so a second pass is needed.

    That pass separates two very different faults, because the right repair
    differs:

    Glitch spikes - a handful of sessions priced on a different divisor that then
    revert. NIFTYBEES has exactly two such rows in December 2019 (129 -> 13 ->
    129). Treating the return leg as a permanent break threw away every session
    before it, which is a third of the history for the sake of two bad rows. The
    bad rows are dropped and the series is stitched instead.

    Level shifts - a step that does not revert, i.e. a genuine unadjusted split
    or bonus. There is no way to recover the pre-split scale without the ratio,
    so history before the last such step is discarded: a shorter honest series
    beats a long corrupt one.
    """
    candles = candles or []
    adjusted_count = len([item for item in candles if is_finite(item.get("adjClose"))])
    use_adjusted = adjusted_count >= len(candles) * 0.99 and adjusted_count > 0
    # Yahoo often returns adjClose identical to close for Indian ETFs, in which
    # case calling the basis "adjusted" would overstate what the data supports.
    differs = use_adjusted and any(
        is_finite(item.get("adjClose"))
        and is_finite(item.get("close"))
        and item["close"] > 0
        and abs(item["adjClose"] - item["close"]) / item["close"] > 0.0005
        for item in candles
    )
    rows = [
        {
            "date": item.get("date"),
            "close": item["adjClose"] if use_adjusted else item.get("close"),
        }
        for item in candles
    ]
    rows = [item for item in rows if is_finite(item["close"]) and item["close"] > 0]

    rows, repaired = repair_price_spikes(rows)

    break_index = 0
    break_date = None
    for index in range(1, len(rows)):
        previous = rows[index - 1]["close"]
        move = abs((rows[index]["close"] - previous) / previous) * 100
        if move > MAX_SESSION_MOVE_PERCENT:
            break_index = index
            break_date = rows[index]["date"]

    trimmed = rows[break_index:]
    if differs:
        basis = "Adjusted close (splits and dividends)"
    elif use_adjusted:
        basis = "Close (provider reported no adjustment)"
    else:
        basis = "Unadjusted close"

    notes = []
    if break_date:
        notes.append(
            f"Return, risk, and benchmark figures start at {break_date} because a single-session "
            f"move of more than {MAX_SESSION_MOVE_PERCENT:.0f}% before that date indicates a "
            "corporate action the data provider did not adjust for."
        )
    elif differs:
        notes.append("Return, risk, and benchmark figures use the provider's split- and dividend-adjusted close.")
    else:
        notes.append(
            "The provider returned no usable adjustment, so these figures are measured on the traded "
            "close. That captures dividends the fund reinvests (the norm for growth plans and "
            "accumulating ETFs) but not any it paid out as cash."
        )
    if repaired:
        notes.append(
            f"{repaired} isolated session(s) priced on a different divisor were removed as provider "
            "errors; the surrounding history is retained."
        )

    return trimmed, {
        "basis": basis,
        "adjusted": bool(differs),
        "sessions": len(trimmed),
        "droppedSessions": len(rows) - len(trimmed),
        "repairedSessions": repaired,
        "discontinuityDate": break_date,
        "note": " ".join(notes),
    }


# How many consecutive sessions a bad-divisor run may span before it is read as a
# real level shift rather than a provider error, and how close the price must
# come back to the pre-spike level to count as reverted.
MAX_SPIKE_SESSIONS = 5
SPIKE_REVERSION_TOLERANCE_PERCENT = 15.0


def repair_price_spikes(rows):
    """Drop short runs of sessions that jump away from the series and back.

    NIFTYBEES carries two December 2019 sessions quoted at a tenth of the
    surrounding price. That is a provider error, not a split - the series returns
    to its previous level on the next session. Truncating at the return leg
    (which is what a pure level-shift guard does) discarded 815 earlier sessions
    to work around two bad rows, so those rows are removed instead.

    A run is only treated as an error when it is short and the price comes back
    to where it started; anything that persists is left for the level-shift guard
    to handle.
    """
    if len(rows) < 3:
        return rows, 0

    keep = [True] * len(rows)
    repaired = 0
    index = 1
    while index < len(rows):
        anchor = rows[index - 1]["close"]
        move = abs((rows[index]["close"] - anchor) / anchor) * 100
        if move <= MAX_SESSION_MOVE_PERCENT:
            index += 1
            continue

        # Look for the session that comes back to the pre-spike level.
        limit = min(index + MAX_SPIKE_SESSIONS + 1, len(rows))
        recovery = next(
            (
                candidate
                for candidate in range(index + 1, limit)
                if abs((rows[candidate]["close"] - anchor) / anchor) * 100 <= SPIKE_REVERSION_TOLERANCE_PERCENT
            ),
            None,
        )
        if recovery is None:
            index += 1
            continue

        for bad in range(index, recovery):
            keep[bad] = False
            repaired += 1
        index = recovery + 1

    if not repaired:
        return rows, 0
    return [row for row, wanted in zip(rows, keep) if wanted], repaired


def build_asset_performance(closes):
    rows = [
        {"key": "oneWeek", "label": "1W", "return": round_or_none(period_return(closes, 5))},
        {"key": "oneMonth", "label": "1M", "return": round_or_none(period_return(closes, 21))},
        {"key": "threeMonth", "label": "3M", "return": round_or_none(period_return(closes, 63))},
        {"key": "sixMonth", "label": "6M", "return": round_or_none(period_return(closes, 126))},
        {"key": "oneYear", "label": "1Y", "return": round_or_none(period_return(closes, min(252, len(closes) - 1)))},
        {"key": "threeYear", "label": "3Y CAGR", "return": round_or_none(annualized_return(closes, 756))},
        {"key": "fiveYear", "label": "5Y CAGR", "return": round_or_none(annualized_return(closes, 1260))},
    ]
    return {
        "rows": rows,
        "best": max([row for row in rows if is_finite(row.get("return"))], key=lambda row: row["return"], default=None),
        "worst": min([row for row in rows if is_finite(row.get("return"))], key=lambda row: row["return"], default=None),
        "note": (
            "Point-to-point returns depend on the start date, so read the rolling-return "
            "table below before judging consistency."
        ),
    }


def annualized_return(values, sessions):
    """Compound annual growth rate over ``sessions`` trading days.

    Point-to-point 3Y/5Y totals overstate what an investor experienced per year,
    so multi-year rows are reported as CAGR.
    """
    if not sessions or len(values) <= sessions:
        return None
    start = values[-1 - sessions]
    end = values[-1]
    if not is_finite(start) or not is_finite(end) or start <= 0 or end <= 0:
        return None
    years = sessions / TRADING_SESSIONS_PER_YEAR
    if years <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def build_asset_rolling_returns(closes, window_sessions=TRADING_SESSIONS_PER_YEAR):
    """Distribution of every rolling one-year return in the available history.

    A single 1Y number is one sample and is dominated by its start date - the
    reason point-to-point returns are the wrong tool for judging a fund. Rolling
    windows answer the question an investor actually has: across all the moments
    I could have bought, what range of one-year outcomes did this deliver, and
    how often was it positive?
    """
    usable = [value for value in closes if is_finite(value) and value > 0]
    if len(usable) <= window_sessions + 1:
        return {
            "available": False,
            "windowLabel": "1 year",
            "reason": (
                f"Needs more than {window_sessions + 1} daily rows to build one-year rolling windows; "
                f"only {len(usable)} are available."
            ),
        }

    returns = [
        (usable[index] / usable[index - window_sessions] - 1) * 100
        for index in range(window_sessions, len(usable))
    ]
    positive = len([value for value in returns if value > 0])
    ordered = sorted(returns)

    return {
        "available": True,
        "windowLabel": "1 year",
        "windowSessions": window_sessions,
        "observations": len(returns),
        "average": round_or_none(sum(returns) / len(returns)),
        "median": round_or_none(percentile(ordered, 50)),
        "best": round_or_none(ordered[-1]),
        "worst": round_or_none(ordered[0]),
        "percentile25": round_or_none(percentile(ordered, 25)),
        "percentile75": round_or_none(percentile(ordered, 75)),
        "positiveSharePercent": round_or_none(positive / len(returns) * 100),
        "summary": (
            f"Across {len(returns)} rolling one-year windows the median outcome was "
            f"{percentile(ordered, 50):.1f}%, the range ran {ordered[0]:.1f}% to {ordered[-1]:.1f}%, "
            f"and {positive / len(returns) * 100:.0f}% of windows finished positive."
        ),
    }


def percentile(ordered_values, target):
    """Linear-interpolated percentile of an already-sorted list."""
    if not ordered_values:
        return None
    if len(ordered_values) == 1:
        return ordered_values[0]
    position = (len(ordered_values) - 1) * clamp(target, 0, 100) / 100
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered_values[lower]
    weight = position - lower
    return ordered_values[lower] * (1 - weight) + ordered_values[upper] * weight


class AdvisorkhojAnnualReturnsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.section = ""
        self.in_row = False
        self.in_cell = False
        self.rows = {"thead": [], "tbody": [], "tfoot": []}
        self.current_row = []
        self.current_row_section = ""
        self.current_cell_text = []
        self.current_anchor_text = None
        self.first_anchor_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "tbl_scheme_returns":
            self.in_table = True
            return
        if not self.in_table:
            return
        if tag in {"thead", "tbody", "tfoot"}:
            self.section = tag
        elif tag == "tr":
            self.in_row = True
            self.current_row = []
            self.current_row_section = self.section or "tbody"
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.current_cell_text = []
            self.current_anchor_text = None
            self.first_anchor_text = ""
        elif self.in_cell and tag == "a" and not self.first_anchor_text:
            self.current_anchor_text = []

    def handle_data(self, data):
        if not self.in_cell:
            return
        self.current_cell_text.append(data)
        if self.current_anchor_text is not None:
            self.current_anchor_text.append(data)

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if self.in_cell and tag == "a" and self.current_anchor_text is not None:
            self.first_anchor_text = clean_advisorkhoj_text(" ".join(self.current_anchor_text))
            self.current_anchor_text = None
        elif self.in_cell and tag in {"th", "td"}:
            cell_text = self.first_anchor_text or clean_advisorkhoj_text(" ".join(self.current_cell_text))
            self.current_row.append(cell_text)
            self.in_cell = False
            self.current_cell_text = []
            self.current_anchor_text = None
            self.first_anchor_text = ""
        elif self.in_row and tag == "tr":
            if self.current_row:
                self.rows.setdefault(self.current_row_section, []).append(self.current_row)
            self.in_row = False
            self.current_row = []
            self.current_row_section = ""
        elif tag in {"thead", "tbody", "tfoot"}:
            self.section = ""
        elif tag == "table":
            self.in_table = False


ADVISORKHOJ_CATEGORY_RULES = (
    ("balanced advantage", "Hybrid: Dynamic Asset Allocation"),
    ("dynamic asset allocation", "Hybrid: Dynamic Asset Allocation"),
    ("aggressive hybrid", "Hybrid: Aggressive Hybrid"),
    ("equity savings", "Hybrid: Equity Savings"),
    ("multi asset", "Hybrid: Multi Asset Allocation"),
    ("large and mid cap", "Equity: Large and Mid Cap"),
    ("large mid cap", "Equity: Large and Mid Cap"),
    ("flexi cap", "Equity: Flexi Cap"),
    ("flexicap", "Equity: Flexi Cap"),
    ("multi cap", "Equity: Multi Cap"),
    ("multicap", "Equity: Multi Cap"),
    ("large cap", "Equity: Large Cap"),
    ("bluechip", "Equity: Large Cap"),
    ("mid cap", "Equity: Mid Cap"),
    ("midcap", "Equity: Mid Cap"),
    ("small cap", "Equity: Small Cap"),
    ("smallcap", "Equity: Small Cap"),
    ("value", "Equity: Value"),
    ("contra", "Equity: Contra"),
    ("focused", "Equity: Focused"),
    ("elss", "Equity: ELSS"),
    ("tax saver", "Equity: ELSS"),
    ("dividend yield", "Equity: Dividend Yield"),
    ("banking and financial", "Equity: Sectoral-Banking and Financial Services"),
    ("technology", "Equity: Sectoral-Technology"),
    ("pharma", "Equity: Sectoral-Pharma and Healthcare"),
    ("healthcare", "Equity: Sectoral-Pharma and Healthcare"),
    ("infrastructure", "Equity: Sectoral-Infrastructure"),
    ("consumption", "Equity: Thematic-Consumption"),
)


def build_mutual_fund_annual_return_report(fund_name, profile):
    category = infer_advisorkhoj_category(fund_name, profile)
    plan_type = infer_advisorkhoj_plan_type(fund_name)
    try:
        annual_data = get_advisorkhoj_annual_returns(category, plan_type)
    except Exception as error:
        return unavailable_annual_returns(
            "mutual-fund",
            category=category,
            plan_type=plan_type,
            error=str(error),
        )

    matched = match_advisorkhoj_fund_row(annual_data.get("funds") or [], fund_name)
    comparison_rows = build_annual_comparison_rows(matched, annual_data)
    if not comparison_rows:
        return unavailable_annual_returns(
            "mutual-fund",
            category=category,
            plan_type=plan_type,
            error="AdvisorKhoj annual return table did not return comparable rows.",
        )

    summary = summarize_annual_return_comparison(matched, annual_data)
    return {
        "available": True,
        "source": annual_data.get("source") or "AdvisorKhoj annual returns",
        "sourceUrl": annual_data.get("sourceUrl") or advisorkhoj_annual_returns_url(category, plan_type),
        "category": annual_data.get("category") or category,
        "planType": annual_data.get("planType") or plan_type,
        "returnsAsOn": annual_data.get("returnsAsOn") or "",
        "years": annual_data.get("years") or [],
        "matched": bool(matched),
        "matchedFund": matched,
        "categoryAverage": annual_data.get("categoryAverage"),
        "benchmark": annual_data.get("benchmark"),
        "comparisonRows": comparison_rows,
        "summary": summary,
    }


def unavailable_annual_returns(asset_type, category="", plan_type="", error=""):
    return {
        "available": False,
        "source": "AdvisorKhoj annual returns" if asset_type == "mutual-fund" else "",
        "sourceUrl": advisorkhoj_annual_returns_url(category, plan_type) if category else ADVISORKHOJ_ANNUAL_RETURNS_URL,
        "category": category,
        "planType": plan_type,
        "returnsAsOn": "",
        "years": [],
        "matched": False,
        "matchedFund": None,
        "categoryAverage": None,
        "benchmark": None,
        "comparisonRows": [],
        "summary": error or "AdvisorKhoj annual return comparison is available only for mutual fund reports.",
    }


def get_advisorkhoj_annual_returns(category="Equity: Multi Cap", plan_type="Regular"):
    normalized_category = category or "Equity: Multi Cap"
    normalized_plan = "Direct" if str(plan_type or "").strip().lower() == "direct" else "Regular"
    cache_key = f"advisorkhoj-mf-annual:{normalized_category}:{normalized_plan}"
    url = advisorkhoj_annual_returns_url(normalized_category, normalized_plan)
    return cached(
        cache_key,
        lambda: parse_advisorkhoj_annual_returns(fetch_text(url), normalized_category, normalized_plan, url),
        ADVISORKHOJ_ANNUAL_RETURNS_CACHE_SECONDS,
    )


def advisorkhoj_annual_returns_url(category="", plan_type=""):
    if not category:
        return ADVISORKHOJ_ANNUAL_RETURNS_URL
    plan = plan_type or "Regular"
    return f"{ADVISORKHOJ_ANNUAL_RETURNS_URL}?category={quote(category)}&scheme_plan_type={quote(plan)}"


def parse_advisorkhoj_annual_returns(page, category="Equity: Multi Cap", plan_type="Regular", source_url=""):
    parser = AdvisorkhojAnnualReturnsParser()
    parser.feed(page or "")
    header_text = " ".join(" ".join(row) for row in parser.rows.get("thead", []))
    returns_as_on = parse_advisorkhoj_returns_as_on(header_text)
    years = advisorkhoj_return_years(returns_as_on)
    funds = [
        row
        for row in (parse_advisorkhoj_return_row(cells, years, "Fund") for cells in parser.rows.get("tbody", []))
        if row
    ]
    footer_rows = [
        row
        for row in (parse_advisorkhoj_return_row(cells, years, "Comparator") for cells in parser.rows.get("tfoot", []))
        if row
    ]
    category_average = next((row for row in footer_rows if "category average" in row["label"].lower()), None)
    benchmark = next((row for row in footer_rows if row is not category_average), None)

    ranked_funds = sorted(
        funds,
        key=lambda row: row["averageReturn"] if is_finite(row.get("averageReturn")) else -999,
        reverse=True,
    )
    for index, row in enumerate(ranked_funds, start=1):
        row["rank"] = index

    if not funds and not footer_rows:
        raise RuntimeError("AdvisorKhoj annual return table was not found.")

    return {
        "available": True,
        "source": "AdvisorKhoj annual returns",
        "sourceUrl": source_url or advisorkhoj_annual_returns_url(category, plan_type),
        "category": category,
        "planType": plan_type,
        "returnsAsOn": returns_as_on,
        "years": years,
        "funds": ranked_funds,
        "categoryAverage": category_average,
        "benchmark": benchmark,
    }


def parse_advisorkhoj_return_row(cells, years, row_type):
    if len(cells) < 5:
        return None
    label = clean_advisorkhoj_text(cells[0])
    if not label:
        return None
    returns = []
    for index, year in enumerate(years):
        returns.append({
            "year": year,
            "return": round_or_none(parse_advisorkhoj_number(array_get(cells, 5 + index))),
        })
    usable_returns = [item["return"] for item in returns if is_finite(item.get("return"))]
    average_return = sum(usable_returns) / len(usable_returns) if usable_returns else None
    return {
        "label": label,
        "scheme": label if row_type == "Fund" else "",
        "type": row_type,
        "amc": clean_advisorkhoj_text(array_get(cells, 1)),
        "launchDate": clean_advisorkhoj_text(array_get(cells, 2)),
        "aumCrore": round_or_none(parse_advisorkhoj_number(array_get(cells, 3))),
        "terPercent": round_or_none(parse_advisorkhoj_number(array_get(cells, 4))),
        "returns": returns,
        "latestReturn": returns[0]["return"] if returns else None,
        "averageReturn": round_or_none(average_return),
    }


def parse_advisorkhoj_returns_as_on(header_text):
    match = re.search(r"Returns\s+as\s+on\s*-\s*(\d{2}-\d{2}-\d{4})", header_text or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def advisorkhoj_return_years(returns_as_on):
    try:
        as_on_date = datetime.strptime(returns_as_on, "%d-%m-%Y").date()
    except (TypeError, ValueError):
        as_on_date = date.today()
    anchor_year = as_on_date.year - 1 if as_on_date.month == 1 and as_on_date.day == 1 else as_on_date.year
    return [anchor_year - index for index in range(5)]


def parse_advisorkhoj_number(value):
    text = clean_advisorkhoj_text(value).replace("%", "")
    if text in {"", "-", "--", "n/a", "N/A"}:
        return None
    return parse_loose_number(text)


def clean_advisorkhoj_text(value):
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("|").strip()


def match_advisorkhoj_fund_row(rows, fund_name):
    query = normalize_fund_match_text(fund_name)
    query_tokens = set(query.split())
    if not query_tokens:
        return None

    best_row = None
    best_score = 0
    for row in rows:
        candidate = normalize_fund_match_text(row.get("scheme") or row.get("label"))
        if not candidate:
            continue
        candidate_tokens = set(candidate.split())
        overlap = len(query_tokens & candidate_tokens)
        overlap_score = overlap / max(len(query_tokens), 1) * 100
        fuzzy_score = SequenceMatcher(None, query, candidate).ratio() * 100
        substring_bonus = 20 if query in candidate or candidate in query else 0
        score = max(overlap_score, fuzzy_score) + substring_bonus
        if score > best_score:
            best_score = score
            best_row = row

    return best_row if best_score >= 58 else None


def normalize_fund_match_text(value):
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    replacements = {
        "flexicap": "flexi cap",
        "multicap": "multi cap",
        "bluechip": "blue chip",
        "dir": "direct",
        "reg": "regular",
        "gr": "growth",
        "pru": "prudential",
        "absl": "aditya birla sun life",
    }
    for source, replacement in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", replacement, text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stopwords = {
        "fund",
        "mutual",
        "regular",
        "direct",
        "plan",
        "growth",
        "option",
        "scheme",
        "the",
    }
    tokens = [token for token in text.split() if token not in stopwords]
    return " ".join(tokens)


def infer_advisorkhoj_category(fund_name, profile):
    text = " ".join([
        str(fund_name or ""),
        str((profile or {}).get("category") or ""),
        str((profile or {}).get("legalType") or ""),
    ]).lower()
    for needle, category in ADVISORKHOJ_CATEGORY_RULES:
        if needle in text:
            return category
    return "Equity: Multi Cap"


def infer_advisorkhoj_plan_type(fund_name):
    normalized = normalize_fund_match_text(fund_name)
    raw_text = str(fund_name or "").lower()
    if " direct " in f" {raw_text} " or " dir " in f" {raw_text} " or " direct " in f" {normalized} ":
        return "Direct"
    return "Regular"


def build_annual_comparison_rows(matched, annual_data):
    rows = []
    if matched:
        rows.append({**matched, "type": "Selected fund"})
    for key, label in (("categoryAverage", "Category average"), ("benchmark", "Benchmark")):
        row = annual_data.get(key)
        if row:
            rows.append({**row, "type": label})
    matched_label = (matched or {}).get("label")
    top_peers = [
        {**row, "type": "Top peer"}
        for row in (annual_data.get("funds") or [])[:5]
        if row.get("label") != matched_label
    ]
    rows.extend(top_peers[:5])
    return rows


def summarize_annual_return_comparison(matched, annual_data):
    category = annual_data.get("category") or "selected category"
    plan = annual_data.get("planType") or "plan"
    as_on = annual_data.get("returnsAsOn") or "latest available date"
    category_average = annual_data.get("categoryAverage") or {}
    if not matched:
        return f"No exact AdvisorKhoj scheme match was found; showing {plan} {category} category average, benchmark, and top peer annual returns as on {as_on}."

    latest = matched.get("latestReturn")
    category_latest = category_average.get("latestReturn")
    spread = latest - category_latest if is_finite(latest) and is_finite(category_latest) else None
    spread_text = f" Latest-year spread versus category average is {spread:+.2f} pp." if is_finite(spread) else ""
    rank = f" Rank {matched.get('rank')} by five-year average." if matched.get("rank") else ""
    return f"Matched against AdvisorKhoj {plan} {category} annual returns as on {as_on}.{spread_text}{rank}".strip()


def build_asset_risk_report(closes):
    # Volatility is measured on the trailing three years, the window fund
    # risk is conventionally quoted on, while drawdown uses the full history
    # because the worst peak-to-trough is the number an investor lives through.
    risk_window = closes[-ASSET_RISK_WINDOW_SESSIONS:] if len(closes) > ASSET_RISK_WINDOW_SESSIONS else closes
    volatility = annualized_volatility(risk_window)
    drawdown = max_drawdown_percent(closes)
    downside = annualized_downside_deviation(risk_window)
    score = 72
    if is_finite(volatility):
        score -= max(0, volatility - 12) * 1.1
    if is_finite(drawdown):
        score -= abs(min(drawdown, 0)) * 0.75
    score = round(clamp(score, 0, 100))
    label = "Low risk" if score >= 72 else "Moderate risk" if score >= 48 else "High risk"
    years = len(closes) / TRADING_SESSIONS_PER_YEAR
    return {
        "score": score,
        "label": label,
        "summary": f"{label}. Check volatility, drawdown, expense ratio, and category fit before allocating.",
        "annualizedVolatility": round_or_none(volatility),
        "downsideDeviation": round_or_none(downside),
        "maxDrawdown": round_or_none(drawdown),
        "volatilityWindowLabel": f"{len(risk_window) / TRADING_SESSIONS_PER_YEAR:.1f} years",
        "drawdownWindowLabel": f"{years:.1f} years",
        "windowNote": (
            f"Volatility uses the trailing {len(risk_window) / TRADING_SESSIONS_PER_YEAR:.1f} years; "
            f"maximum drawdown uses the full {years:.1f} years on file. A drawdown is only as deep "
            "as the history shown, so a short history can hide a worse one."
        ),
    }


def annualized_downside_deviation(values, target_daily_return=0.0):
    """Annualised standard deviation of returns below ``target_daily_return``.

    Total volatility punishes upside moves as if they were risk. Downside
    deviation is what the Sortino ratio uses and is the closer match to what an
    investor means by risk.
    """
    returns = daily_return_series(values)
    if len(returns) < 20:
        return None
    shortfalls = [min(0.0, value - target_daily_return) for value in returns]
    variance = sum(value ** 2 for value in shortfalls) / len(shortfalls)
    return math.sqrt(variance) * math.sqrt(TRADING_SESSIONS_PER_YEAR) * 100


def build_asset_risk_adjusted_report(closes, currency):
    """Sharpe and Sortino over the standard three-year fund window.

    The tab previously reported raw returns and raw volatility side by side and
    left the reader to combine them, and the suitability score mixed momentum
    with a data-quality score instead. Return per unit of risk is the measure a
    fund is actually chosen on, so it is computed here with the risk-free rate
    stated rather than silently assumed to be zero.
    """
    window = closes[-ASSET_RISK_WINDOW_SESSIONS:] if len(closes) > ASSET_RISK_WINDOW_SESSIONS else closes
    risk_free = risk_free_rate_for(currency)
    sessions = len(window) - 1
    cagr = annualized_return(window, sessions) if sessions >= 60 else None
    volatility = annualized_volatility(window)
    downside = annualized_downside_deviation(window)

    if not is_finite(cagr):
        return {
            "available": False,
            "riskFreeRatePercent": risk_free,
            "reason": "Not enough history to annualise a return over the risk window.",
        }

    excess = cagr - risk_free
    sharpe = excess / volatility if is_finite(volatility) and volatility > 0 else None
    sortino = excess / downside if is_finite(downside) and downside > 0 else None

    return {
        "available": True,
        "windowLabel": f"{len(window) / TRADING_SESSIONS_PER_YEAR:.1f} years",
        "annualizedReturn": round_or_none(cagr),
        "annualizedVolatility": round_or_none(volatility),
        "downsideDeviation": round_or_none(downside),
        "riskFreeRatePercent": risk_free,
        "excessReturn": round_or_none(excess),
        "sharpe": round_or_none(sharpe),
        "sortino": round_or_none(sortino),
        "label": sharpe_label(sharpe),
        "summary": (
            f"{cagr:.1f}% annualised against {volatility:.1f}% volatility over "
            f"{len(window) / TRADING_SESSIONS_PER_YEAR:.1f} years."
            + (f" Sharpe {sharpe:.2f}" if is_finite(sharpe) else " Sharpe unavailable")
            + (f", Sortino {sortino:.2f}" if is_finite(sortino) else "")
            + f", using a {risk_free:.1f}% risk-free rate."
        ),
        "assumptionNote": (
            f"The {risk_free:.1f}% risk-free rate is a standing assumption for {currency or 'this currency'}, "
            "not a live quote. Sharpe and Sortino move with it, so treat them as comparative rather than absolute."
        ),
    }


def sharpe_label(sharpe):
    if not is_finite(sharpe):
        return "Unavailable"
    if sharpe >= 1.0:
        return "Strong risk-adjusted return"
    if sharpe >= 0.5:
        return "Fair risk-adjusted return"
    if sharpe >= 0:
        return "Weak risk-adjusted return"
    return "Below the risk-free rate"


def risk_free_rate_for(currency):
    return ASSET_RISK_FREE_RATES.get(str(currency or "").strip().upper(), DEFAULT_RISK_FREE_RATE)


def build_asset_benchmark_report(candles, benchmark, asset_type, long_name, tags):
    """Compare the fund against a market reference on the same dates.

    A fund return in isolation says nothing: 14% is excellent against a
    benchmark that returned 8% and poor against one that returned 22%. This is
    the comparison the tab was missing entirely. Where the fund actually tracks
    the reference index the same maths is tracking error, which for an index ETF
    or index fund is the main selection criterion alongside cost.
    """
    benchmark = benchmark or {}
    benchmark_symbol = benchmark.get("symbol") or ""
    benchmark_name = benchmark.get("name") or benchmark_symbol or "Market reference"
    tracking = tracks_benchmark(benchmark_symbol, long_name, tags)

    # The reference goes through the same adjustment so both sides of every
    # comparison are on a total-return basis.
    benchmark_rows, _basis = build_asset_return_series(benchmark.get("candles") or [])
    paired = align_closes_by_date(candles, benchmark_rows)
    if len(paired) < 60:
        return {
            "available": False,
            "expected": bool(benchmark_symbol),
            "benchmarkSymbol": benchmark_symbol,
            "benchmarkName": benchmark_name,
            "isTrackingBenchmark": tracking,
            "summary": (
                f"Not enough overlapping sessions with {benchmark_name} to compare "
                f"({len(paired)} matched dates)."
                if benchmark_symbol
                else "No market reference is mapped for this symbol."
            ),
        }

    fund_closes = [row[1] for row in paired]
    index_closes = [row[2] for row in paired]

    # Beta, tracking error, and capture are computed on weekly rather than daily
    # returns. A thinly traded ETF's daily "close" is often the last trade rather
    # than a simultaneous print, so part of today's measured fund return belongs
    # to yesterday's index move. That non-synchronous pricing decorrelates the
    # two series and biases beta toward zero while inflating tracking error - it
    # showed NIFTYBEES, a plain Nifty 50 ETF, at beta 0.89 and 3.05% tracking
    # error. Weekly sampling absorbs a day of lag and still leaves ~50
    # observations a year.
    fund_weekly = weekly_return_series(paired, price_index=1)
    index_weekly = weekly_return_series(paired, price_index=2)
    span = min(len(fund_weekly), len(index_weekly))
    fund_returns = fund_weekly[-span:]
    index_returns = index_weekly[-span:]

    sessions = len(fund_closes) - 1
    fund_cagr = annualized_return(fund_closes, sessions)
    index_cagr = annualized_return(index_closes, sessions)

    # The fund side accumulates dividends and the reference index does not, so
    # the reference is grossed up by its dividend yield before the two are
    # subtracted. Without this every equity fund clears its benchmark by roughly
    # the index yield for free.
    dividend_yield = index_dividend_yield_for(benchmark_symbol)
    index_total_return = index_cagr + dividend_yield if is_finite(index_cagr) else None
    excess = (
        fund_cagr - index_total_return
        if is_finite(fund_cagr) and is_finite(index_total_return)
        else None
    )

    active_returns = [fund - index for fund, index in zip(fund_returns, index_returns)]
    tracking_error = (
        population_stdev(active_returns) * math.sqrt(WEEKS_PER_YEAR) * 100
        if len(active_returns) >= 20
        else None
    )
    beta = covariance_beta(fund_returns, index_returns)
    up_capture = capture_ratio(fund_returns, index_returns, "up")
    down_capture = capture_ratio(fund_returns, index_returns, "down")
    years = sessions / TRADING_SESSIONS_PER_YEAR

    label = "Tracking error" if tracking else "Active risk"
    return {
        "available": True,
        "expected": True,
        "benchmarkSymbol": benchmark_symbol,
        "benchmarkName": benchmark_name,
        "isTrackingBenchmark": tracking,
        "windowLabel": f"{years:.1f} years",
        "matchedSessions": len(paired),
        "returnFrequency": "weekly",
        "observations": span,
        "fundReturn": round_or_none(fund_cagr),
        "benchmarkPriceReturn": round_or_none(index_cagr),
        "benchmarkDividendYield": dividend_yield,
        "benchmarkReturn": round_or_none(index_total_return),
        "excessReturn": round_or_none(excess),
        "trackingErrorLabel": label,
        "trackingError": round_or_none(tracking_error),
        "beta": round_or_none(beta),
        "upCapture": round_or_none(up_capture),
        "downCapture": round_or_none(down_capture),
        "summary": benchmark_summary(
            benchmark_name, fund_cagr, index_total_return, excess, tracking, tracking_error, years,
        ),
        "note": (
            f"{benchmark_name} is the market reference this tab maps to the symbol's exchange. "
            + (
                "The fund's own name or category matches it, so the dispersion figure reads as tracking error."
                if tracking
                else "This is not necessarily the fund's stated benchmark, so read the dispersion as active "
                     "risk against the broad market rather than as tracking error."
            )
            + (
                f" {benchmark_name} is a price index, so an assumed {dividend_yield:.1f}% dividend yield is "
                "added to it before the excess return is taken; the fund is measured on a basis that keeps "
                "any income it received. Without that step every equity fund appears to beat its index by "
                "roughly the index yield for free."
            )
            + (
                " For an ETF the dispersion is measured on the traded price, which drifts from NAV on premium, "
                "discount, and thin sessions, so it reads higher than the NAV-based tracking error the AMC publishes."
                if asset_type == "etf"
                else ""
            )
        ),
    }


def index_dividend_yield_for(benchmark_symbol):
    return INDEX_DIVIDEND_YIELDS.get(
        str(benchmark_symbol or "").strip().upper(), DEFAULT_INDEX_DIVIDEND_YIELD,
    )


def benchmark_summary(benchmark_name, fund_cagr, index_cagr, excess, tracking, tracking_error, years):
    if not (is_finite(fund_cagr) and is_finite(index_cagr)):
        return f"Comparison against {benchmark_name} could not be annualised."
    verb = "ahead of" if (excess or 0) >= 0 else "behind"
    text = (
        f"{fund_cagr:.1f}% a year versus {index_cagr:.1f}% for {benchmark_name} including dividends "
        f"over {years:.1f} years, {abs(excess):.1f}pp {verb} the reference."
    )
    if is_finite(tracking_error):
        if tracking:
            text += f" Tracking error is {tracking_error:.2f}%, which for an index vehicle should stay low."
        else:
            text += f" Active risk against the reference is {tracking_error:.2f}%."
    return text


def align_closes_by_date(candles, benchmark_candles):
    """Match fund and benchmark closes on shared dates.

    Mutual fund NAVs publish on a different calendar from an index (holidays,
    a day's reporting lag), so comparing the two raw series index-by-index would
    silently offset them and corrupt beta and tracking error.
    """
    index_by_date = {
        item.get("date"): item.get("close")
        for item in (benchmark_candles or [])
        if item.get("date") and is_finite(item.get("close"))
    }
    paired = []
    for item in candles or []:
        date_key = item.get("date")
        close = item.get("close")
        other = index_by_date.get(date_key)
        if date_key and is_finite(close) and is_finite(other) and close > 0 and other > 0:
            paired.append((date_key, close, other))
    return paired


def weekly_return_series(paired_rows, price_index):
    """Week-over-week returns from date-aligned rows.

    Each ISO week contributes its last observation, so a single stale daily
    close no longer offsets the fund against the index.
    """
    last_by_week = {}
    order = []
    for row in paired_rows:
        try:
            week_key = date.fromisoformat(str(row[0])).isocalendar()[:2]
        except (TypeError, ValueError):
            continue
        if week_key not in last_by_week:
            order.append(week_key)
        last_by_week[week_key] = row[price_index]

    closes = [last_by_week[key] for key in order]
    return daily_return_series(closes)


def population_stdev(values):
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def covariance_beta(fund_returns, index_returns):
    """Sensitivity of the fund to the reference index."""
    if len(fund_returns) < 20 or len(fund_returns) != len(index_returns):
        return None
    fund_mean = sum(fund_returns) / len(fund_returns)
    index_mean = sum(index_returns) / len(index_returns)
    covariance = sum(
        (fund - fund_mean) * (index - index_mean)
        for fund, index in zip(fund_returns, index_returns)
    )
    variance = sum((index - index_mean) ** 2 for index in index_returns)
    if not variance:
        return None
    return covariance / variance


def capture_ratio(fund_returns, index_returns, direction):
    """Share of the reference's up (or down) moves the fund captured.

    Above 100 on the up side and below 100 on the down side is the pattern worth
    paying for; the reverse is a fund that lags rallies and leads selloffs.
    """
    pairs = [
        (fund, index)
        for fund, index in zip(fund_returns, index_returns)
        if (index > 0 if direction == "up" else index < 0)
    ]
    if len(pairs) < 20:
        return None
    index_total = sum(index for _fund, index in pairs)
    if not index_total:
        return None
    fund_total = sum(fund for fund, _index in pairs)
    return fund_total / index_total * 100


def tracks_benchmark(benchmark_symbol, long_name, tags):
    """True when the fund's own name or tags say it follows the reference index."""
    markers = ASSET_BENCHMARK_TRACKING_MARKERS.get(benchmark_symbol) or ()
    if not markers:
        return False
    haystack = " ".join([str(long_name or ""), *[str(tag or "") for tag in (tags or [])]]).lower()
    if not any(marker in haystack for marker in markers):
        return False
    # "Nifty Next 50" and "Nifty Bank" contain "nifty" but do not track Nifty 50.
    return not any(marker in haystack for marker in ASSET_BENCHMARK_TRACKING_EXCLUSIONS)


def build_asset_momentum_report(current, sma50_value, sma200_value, performance):
    one_month = next((row.get("return") for row in performance["rows"] if row["key"] == "oneMonth"), None)
    three_month = next((row.get("return") for row in performance["rows"] if row["key"] == "threeMonth"), None)
    six_month = next((row.get("return") for row in performance["rows"] if row["key"] == "sixMonth"), None)
    score = 50
    if is_finite(one_month):
        score += clamp(one_month * 1.4, -15, 18)
    if is_finite(three_month):
        score += clamp(three_month * 0.9, -16, 18)
    if is_finite(six_month):
        score += clamp(six_month * 0.45, -12, 16)
    if is_finite(sma50_value) and current > sma50_value:
        score += 8
    elif is_finite(sma50_value):
        score -= 8
    if is_finite(sma200_value) and current > sma200_value:
        score += 8
    elif is_finite(sma200_value):
        score -= 8
    score = round(clamp(score, 0, 100))
    label = "Strong" if score >= 70 else "Neutral" if score >= 45 else "Weak"
    return {
        "score": score,
        "label": label,
        "summary": f"{label} momentum based on recent returns and 50/200-day trend position.",
        "sma50": round_or_none(sma50_value),
        "sma200": round_or_none(sma200_value),
    }


def build_asset_confidence_report(candles, profile, holdings, summary, annual_returns=None, freshness=None):
    score = 45
    checks = []
    if len(candles) >= 250:
        score += 20
        checks.append("At least one year of daily history is available.")
    else:
        checks.append("Price history is shorter than one year.")
    if freshness:
        checks.append(freshness["detail"])
        if freshness["stale"]:
            # A stale NAV/close makes every level and return in the report a
            # statement about the past, so it should not read as high
            # confidence.
            score -= 20
    if is_finite(profile.get("expenseRatio")):
        score += 12
        checks.append("Expense ratio was available.")
    else:
        checks.append("Expense ratio was not available.")
    if is_finite(profile.get("totalAssets")):
        score += 12
        checks.append("AUM / total assets was available.")
    if holdings:
        score += 10
        checks.append("Top holdings were available.")
    if summary.get("fundProfile"):
        score += 8
        checks.append("Fund profile data was available.")
    if annual_returns and annual_returns.get("available"):
        score += 8
        checks.append("AdvisorKhoj annual return comparison was available.")
    score = round(clamp(score, 0, 100))
    return {
        "score": score,
        "label": "High" if score >= 75 else "Moderate" if score >= 55 else "Low",
        "checks": checks,
    }


# A latest row older than this many calendar days is treated as stale. Wide
# enough to span a long weekend plus a holiday, tight enough to catch the
# months-old NAV the provider sometimes returns for a dormant fund. Mutual funds
# publish one NAV per day (often a day late), so they get more slack than ETFs.
MAX_ASSET_STALE_DAYS = {"etf": 5, "mutual-fund": 8}


def build_asset_freshness(candles, asset_type):
    """Report how old the latest close/NAV is, and whether that is stale.

    Yahoo occasionally serves a long-dormant frame for delisted or suspended
    funds. Without this the report would present months-old levels as current.
    """
    latest_date = (candles[-1] or {}).get("date") if candles else None
    limit = MAX_ASSET_STALE_DAYS.get(asset_type, 5)
    label = "NAV" if asset_type == "mutual-fund" else "Close"
    if not latest_date:
        return {
            "latestDate": None,
            "ageDays": None,
            "stale": False,
            "limitDays": limit,
            "detail": f"Latest {label} date was not returned by the data provider.",
        }

    try:
        age_days = (datetime.now(tz=timezone.utc).date() - date.fromisoformat(str(latest_date))).days
    except (TypeError, ValueError):
        return {
            "latestDate": str(latest_date),
            "ageDays": None,
            "stale": False,
            "limitDays": limit,
            "detail": f"Latest {label} date could not be parsed.",
        }

    stale = age_days > limit
    if stale:
        detail = (
            f"Latest {label} is from {latest_date}, {age_days} days old "
            f"(over the {limit}-day limit) - treat levels and returns as out of date."
        )
    else:
        detail = f"Latest {label} is from {latest_date} ({age_days} days old)."
    return {
        "latestDate": str(latest_date),
        "ageDays": age_days,
        "stale": stale,
        "limitDays": limit,
        "detail": detail,
    }


def build_asset_plan(asset_type, current, currency, levels, performance, risk, profile):
    first_support = array_get(levels.get("supportZones"), 0)
    first_resistance = array_get(levels.get("resistanceZones"), 0)
    support = first_support["price"] if first_support else current * 0.96
    resistance = first_resistance["price"] if first_resistance else current * 1.06
    one_month = next((row.get("return") for row in performance["rows"] if row["key"] == "oneMonth"), None)
    expense = expense_ratio_percent(profile.get("expenseRatio"))

    if asset_type == "etf":
        title = "ETF allocation plan"
        items = [
            {"label": "Entry discipline", "value": f"{format_currency_value(support, currency)} - {format_currency_value(current, currency)}", "detail": "Prefer staggered buying near support or after a close back above short-term trend."},
            {"label": "Exit / trim zone", "value": format_currency_value(resistance, currency), "detail": "Trim tactical ETF exposure near resistance if momentum fades or market breadth weakens."},
            {"label": "Risk control", "value": risk["label"], "detail": "Reassess if price closes below support with rising volatility."},
        ]
    else:
        title = "Mutual fund allocation plan"
        items = [
            {"label": "SIP suitability", "value": "Prefer staggered allocation", "detail": "Use SIP/STP for volatile categories; avoid judging a mutual fund only from one-month returns."},
            {"label": "Lump-sum filter", "value": f"Review near {format_currency_value(support, currency)}", "detail": "For equity funds, deploy lump sum more cautiously after sharp short-term rallies."},
            {"label": "Review trigger", "value": risk["label"], "detail": "Review category, benchmark, manager consistency, expense ratio, and drawdown every quarter."},
        ]

    if is_finite(one_month):
        items.append({
            "label": "Short-term trend",
            "value": f"{one_month:+.2f}%",
            "detail": "Use short-term return as timing context, not as the primary fund selection criterion.",
        })
    if is_finite(expense):
        items.append({
            "label": "Cost check",
            "value": f"{expense:.2f}%",
            "detail": "Lower cost improves long-term compounding when tracking and category quality are similar.",
        })

    return {
        "title": title,
        "items": items,
    }


def score_asset_suitability(momentum, risk, confidence, profile, risk_adjusted=None, rolling=None, benchmark=None):
    """Investment merit of the fund, on the evidence available.

    The previous version was 35% momentum, 28% risk, 22% data confidence, and
    15% cost. Two things were wrong with that. Data confidence is a statement
    about the feed, not about the fund, so a well-documented poor fund scored
    above a thinly-documented good one. And momentum as the largest term made it
    a trend-following score, which is the wrong lens for a buy-and-hold vehicle.

    The weights now lead with return per unit of risk and consistency across
    rolling windows, which is how a fund is actually selected. Data confidence is
    reported separately and gates the score rather than contributing to it.
    """
    expense = expense_ratio_percent(profile.get("expenseRatio"))
    cost_score = 70
    if is_finite(expense):
        cost_score = clamp(92 - expense * 18, 35, 95)

    components = [
        (risk["score"], 0.16, "Risk profile"),
        (cost_score, 0.16, "Cost"),
        (momentum["score"], 0.12, "Momentum"),
    ]

    sharpe = (risk_adjusted or {}).get("sharpe")
    if is_finite(sharpe):
        # Sharpe 0 -> 40, 1.0 -> 80, 1.5 -> 100. Below the risk-free rate scores
        # under 40 and can reach zero.
        components.append((clamp(40 + sharpe * 40, 0, 100), 0.30, "Risk-adjusted return"))

    positive_share = (rolling or {}).get("positiveSharePercent")
    if (rolling or {}).get("available") and is_finite(positive_share):
        components.append((clamp(positive_share, 0, 100), 0.18, "Rolling-return consistency"))

    excess = (benchmark or {}).get("excessReturn")
    if (benchmark or {}).get("available") and is_finite(excess):
        # +/-5pp a year against the reference spans the full range.
        components.append((clamp(50 + excess * 10, 0, 100), 0.08, "Versus market reference"))

    score = round(weighted_average([(value, weight) for value, weight, _label in components]))
    return int(clamp(score, 0, 100))


def build_asset_suitability_report(score, confidence, freshness, risk_adjusted, rolling, benchmark):
    """The suitability score plus what it was and was not able to include."""
    included = ["Risk profile", "Cost", "Momentum"]
    if is_finite((risk_adjusted or {}).get("sharpe")):
        included.append("Risk-adjusted return")
    if (rolling or {}).get("available"):
        included.append("Rolling-return consistency")
    if (benchmark or {}).get("available"):
        included.append("Versus market reference")

    missing = [item for item in (
        "Risk-adjusted return", "Rolling-return consistency", "Versus market reference",
    ) if item not in included]

    # Data quality gates the score instead of being averaged into it: a thin or
    # stale feed makes the number unreliable rather than making the fund bad.
    reliable = confidence["score"] >= 55 and not (freshness or {}).get("stale")
    return {
        "score": score,
        "label": "Strong" if score >= 72 else "Balanced" if score >= 52 else "Weak",
        "included": included,
        "missing": missing,
        "dataConfidence": confidence["score"],
        "reliable": reliable,
        "note": (
            "Data confidence and freshness are reported separately rather than averaged into this score, "
            "because a poorly documented fund is not the same thing as a poor fund."
            + ("" if reliable else " Treat this score as indicative only until the flagged data gaps are checked.")
        ),
    }


def summarize_asset_report(asset_label, suitability_score, momentum, risk, profile, risk_adjusted=None, benchmark=None):
    cost = expense_ratio_percent(profile.get("expenseRatio"))
    cost_text = f" Expense ratio: {cost:.2f}%." if is_finite(cost) else " Expense ratio was not available."
    label = "Strong" if suitability_score >= 72 else "Balanced" if suitability_score >= 52 else "Weak"
    text = f"{label} {asset_label} setup: {momentum['label'].lower()} momentum with {risk['label'].lower()}.{cost_text}"
    # Lead the summary with the two figures a fund is actually judged on rather
    # than momentum alone.
    sharpe = (risk_adjusted or {}).get("sharpe")
    if is_finite(sharpe):
        text += f" Sharpe {sharpe:.2f} over {risk_adjusted.get('windowLabel') or 'the risk window'}."
    excess = (benchmark or {}).get("excessReturn")
    if (benchmark or {}).get("available") and is_finite(excess):
        text += f" {abs(excess):.1f}pp a year {'ahead of' if excess >= 0 else 'behind'} {benchmark.get('benchmarkName')}."
    return text


def build_asset_references(symbol, long_name, quote_data, meta, asset_type):
    tv_symbol = to_trading_view_symbol(symbol, quote_data, meta)
    links = [
        {
            "label": "Yahoo Finance profile",
            "url": f"https://finance.yahoo.com/quote/{quote(symbol)}",
            "note": "Cross-check NAV/price, expense ratio, AUM, holdings, and performance source fields.",
        },
    ]
    if tv_symbol:
        links.append({
            "label": "TradingView chart",
            "url": f"https://www.tradingview.com/chart/?symbol={quote(tv_symbol)}",
            "note": "Review live chart, trend, and support/resistance behavior.",
        })
    if asset_type == "mutual-fund":
        advisorkhoj_category = infer_advisorkhoj_category(long_name, {})
        advisorkhoj_plan = infer_advisorkhoj_plan_type(long_name)
        links.append({
            "label": "AdvisorKhoj annual returns",
            "url": advisorkhoj_annual_returns_url(advisorkhoj_category, advisorkhoj_plan),
            "note": "Compare annual calendar-year returns against category average, benchmark, and peers.",
        })
        links.append({
            "label": "Morningstar search",
            "url": f"https://www.morningstar.com/search?query={quote(long_name or symbol)}",
            "note": "Cross-check fund category, risk, returns, holdings, and expense details.",
        })
    else:
        links.append({
            "label": "ETF.com search",
            "url": f"https://www.etf.com/search?query={quote(long_name or symbol)}",
            "note": "Cross-check ETF holdings, liquidity, expense ratio, and tracking exposure.",
        })
    return {"tradingViewSymbol": tv_symbol, "links": links}


def extract_fundamentals(summary, quote_data, meta, sec, screener, current_price):
    price = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    key = summary.get("defaultKeyStatistics") or {}
    financial = summary.get("financialData") or {}
    profile = summary.get("assetProfile") or {}
    sec_metrics = sec.get("metrics") or {}
    screener_metrics = screener.get("metrics") or {}

    return {
        "sector": profile.get("sector") or "",
        "industry": profile.get("industry") or "",
        "website": profile.get("website") or "",
        "marketCap": first_present(raw(price.get("marketCap")), raw(detail.get("marketCap")), quote_data.get("marketCap"), implied_market_cap(sec_metrics, current_price), screener_metrics.get("marketCap")),
        "beta": first_present(raw(detail.get("beta")), quote_data.get("beta")),
        "trailingPE": first_present(raw(detail.get("trailingPE")), quote_data.get("trailingPE"), implied_trailing_pe(sec_metrics, current_price), screener_metrics.get("trailingPE")),
        "forwardPE": first_present(raw(detail.get("forwardPE")), quote_data.get("forwardPE")),
        "pegRatio": raw(key.get("pegRatio")),
        "priceToBook": first_present(raw(key.get("priceToBook")), quote_data.get("priceToBook"), implied_price_to_book(sec_metrics, current_price), screener_metrics.get("priceToBook")),
        "profitMargins": first_present(raw(financial.get("profitMargins")), sec_metrics.get("profitMargins"), screener_metrics.get("profitMargins")),
        "operatingMargins": first_present(raw(financial.get("operatingMargins")), sec_metrics.get("operatingMargins")),
        "grossMargins": first_present(raw(financial.get("grossMargins")), sec_metrics.get("grossMargins")),
        "returnOnEquity": first_present(raw(financial.get("returnOnEquity")), sec_metrics.get("returnOnEquity"), screener_metrics.get("returnOnEquity")),
        "returnOnCapitalEmployed": screener_metrics.get("returnOnCapitalEmployed"),
        "revenueGrowth": first_present(raw(financial.get("revenueGrowth")), sec_metrics.get("revenueGrowth"), screener_metrics.get("revenueGrowth")),
        "earningsGrowth": first_present(raw(financial.get("earningsGrowth")), sec_metrics.get("earningsGrowth"), screener_metrics.get("earningsGrowth")),
        "salesGrowth5y": screener_metrics.get("salesGrowth5y"),
        "promoterHolding": screener_metrics.get("promoterHolding"),
        "debtToEquity": first_present(raw(financial.get("debtToEquity")), sec_metrics.get("debtToEquity"), screener_metrics.get("debtToEquity")),
        "currentRatio": first_present(raw(financial.get("currentRatio")), sec_metrics.get("currentRatio")),
        "totalCash": first_present(raw(financial.get("totalCash")), sec_metrics.get("totalCash")),
        "totalDebt": first_present(raw(financial.get("totalDebt")), sec_metrics.get("totalDebt")),
        "freeCashflow": first_present(raw(financial.get("freeCashflow")), sec_metrics.get("freeCashflow")),
        "revenue": first_present(sec_metrics.get("revenue"), screener_metrics.get("revenue")),
        "netIncome": first_present(sec_metrics.get("netIncome"), screener_metrics.get("netIncome")),
        "bookValuePerShare": screener_metrics.get("bookValuePerShare"),
        "targetMeanPrice": raw(financial.get("targetMeanPrice")),
        "recommendationMean": raw(financial.get("recommendationMean")),
        "recommendationKey": financial.get("recommendationKey") or "",
        "dividendYield": first_present(raw(detail.get("dividendYield")), quote_data.get("dividendYield"), screener_metrics.get("dividendYield")),
        "currency": first_present(raw(price.get("currency")), quote_data.get("currency"), meta.get("currency"), ""),
        "dataSource": " + ".join([value for value in [sec.get("source"), screener.get("source")] if value]) or "Market data provider",
        "latestFiling": sec.get("latestFiling") or screener.get("latestUpdate"),
    }


def extract_screener_metrics(page):
    ratios = extract_screener_ratios(page)
    description = extract_meta_description(page)
    company_name = clean_html(match_first(page, r"<h1[^>]*>\s*([^<]+?)\s*</h1>"))
    current_price = ratios.get("Current Price")
    market_cap_crore = ratios.get("Market Cap")
    revenue_crore = parse_description_number(description, r"Revenue:\s*([\d,.]+)\s*Cr")
    profit_crore = parse_description_number(description, r"Profit:\s*([\d,.]+)\s*Cr")
    sales_growth_5y = parse_description_percent(description, r"sales growth of\s*([\d.]+)%")
    promoter_holding = parse_description_percent(description, r"Promoter Holding:\s*([\d.]+)%")
    earnings_growth = parse_description_percent(page, r"profit growth of\s*([\d.]+)%\s*CAGR")
    book_value_per_share = ratios.get("Book Value")
    price_to_book = current_price / book_value_per_share if current_price and book_value_per_share else None
    debt_to_equity = 0 if re.search(r"almost debt free", page, flags=re.IGNORECASE) else None

    return {
        "companyName": company_name,
        "source": "Screener.in summary",
        "latestUpdate": None,
        "metrics": {
            "marketCap": crore_to_rupees(market_cap_crore),
            "trailingPE": ratios.get("Stock P/E"),
            "bookValuePerShare": book_value_per_share,
            "priceToBook": price_to_book,
            "dividendYield": ratio_percent(ratios.get("Dividend Yield")),
            "returnOnCapitalEmployed": ratio_percent(ratios.get("ROCE")),
            "returnOnEquity": ratio_percent(ratios.get("ROE")),
            "revenue": crore_to_rupees(revenue_crore),
            "netIncome": crore_to_rupees(profit_crore),
            "profitMargins": profit_crore / revenue_crore if revenue_crore and profit_crore else None,
            "salesGrowth5y": sales_growth_5y,
            "revenueGrowth": sales_growth_5y,
            "earningsGrowth": earnings_growth,
            "promoterHolding": promoter_holding,
            "debtToEquity": debt_to_equity,
        },
        "shareholding": extract_screener_shareholding(page),
        "quarterlyResults": extract_screener_quarterly_results(page),
        "events": extract_screener_events(page),
    }


def ensure_shareholding_data(symbol, long_name, screener):
    if not (symbol.endswith(".NS") or symbol.endswith(".BO")):
        return screener or {}

    screener = dict(screener or {})
    metrics = dict(screener.get("metrics") or {})
    shareholding = screener.get("shareholding") or {"periods": [], "rows": []}
    missing_rows = not has_shareholding_categories(shareholding, ("Promoters", "FIIs", "DIIs"))
    missing_promoter_metric = not is_finite(metrics.get("promoterHolding"))

    if missing_rows:
        fallback = safe_public_shareholding(symbol, long_name)
        if fallback.get("rows"):
            shareholding = merge_shareholding(shareholding, fallback)

    if missing_promoter_metric:
        metrics["promoterHolding"] = latest_shareholding_value(shareholding, "Promoters")

    screener["metrics"] = metrics
    screener["shareholding"] = shareholding
    if shareholding.get("source"):
        screener["source"] = combine_source_labels(screener.get("source"), shareholding.get("source"))
    return screener


def safe_public_shareholding(symbol, long_name):
    try:
        return cached(
            f"public-shareholding:{symbol}:{stock_slug_seed(long_name)}",
            lambda: get_public_shareholding(symbol, long_name),
            SEC_CACHE_TTL_SECONDS,
        )
    except Exception:
        return {"periods": [], "rows": []}


def get_public_shareholding(symbol, long_name):
    if not (symbol.endswith(".NS") or symbol.endswith(".BO")):
        return {"periods": [], "rows": []}

    combined = {"periods": [], "rows": [], "source": ""}
    for loader in (get_groww_shareholding, get_upstox_shareholding):
        source = loader(symbol, long_name)
        if source.get("rows"):
            combined = merge_shareholding(combined, source)
            if has_shareholding_categories(combined, ("Promoters", "FIIs", "DIIs")):
                break
    return combined


def get_groww_shareholding(symbol, long_name):
    for slug in company_slug_candidates(symbol, long_name, groww=True):
        try:
            page = fetch_text(f"https://groww.in/stocks/{quote(slug)}/share-holding")
        except Exception:
            continue
        shareholding = extract_groww_shareholding(page)
        if shareholding.get("rows"):
            return shareholding
    return {"periods": [], "rows": []}


def get_upstox_shareholding(symbol, long_name):
    for slug in company_slug_candidates(symbol, long_name, groww=False):
        try:
            page = fetch_text(f"https://upstox.com/stocks/{quote(slug)}-shareholding/")
        except Exception:
            continue
        shareholding = extract_upstox_shareholding(page)
        if shareholding.get("rows"):
            return shareholding
    return {"periods": [], "rows": []}


def extract_groww_shareholding(page):
    script = match_first(page, r"<script[^>]+id=\"__NEXT_DATA__\"[^>]*>([\s\S]*?)</script>")
    if not script:
        return {"periods": [], "rows": []}

    try:
        data = json.loads(html.unescape(script))
    except json.JSONDecodeError:
        return {"periods": [], "rows": []}

    stock_data = (((data.get("props") or {}).get("pageProps") or {}).get("stockData") or {})
    pattern = stock_data.get("shareHoldingPattern") or {}
    series = {name: [] for name in OWNERSHIP_ROW_NAMES}

    for period, values in pattern.items():
        promoter = nested_percent_total(values.get("promoters"))
        fii = nested_percent_total(values.get("foreignInstitutions"))
        domestic = add_numbers(
            nested_percent_total(values.get("otherDomesticInstitutions")),
            nested_percent_total(values.get("mutualFunds")),
        )
        public = nested_percent_total(values.get("retailAndOthers"))
        add_shareholding_point(series, "Promoters", period, promoter)
        add_shareholding_point(series, "FIIs", period, fii)
        add_shareholding_point(series, "DIIs", period, domestic)
        add_shareholding_point(series, "Public", period, public)

    return build_shareholding_from_series(series, "Groww public shareholding")


def extract_upstox_shareholding(page):
    text = html.unescape(page).replace('\\"', '"')
    series = {name: [] for name in OWNERSHIP_ROW_NAMES}
    for match in re.finditer(r'\{"shareHolderType":"([^"]+)"[\s\S]*?"history":\[(.*?)\]\}', text):
        row_name = normalize_shareholder_name(match.group(1))
        if row_name not in set(OWNERSHIP_ROW_NAMES) | {"Mutual Funds"}:
            continue
        for item in re.finditer(r'\{"period":"([^"]+)","totalPercent":(-?[\d.]+)', match.group(2)):
            period = item.group(1)
            value = parse_loose_number(item.group(2))
            target_name = "DIIs" if row_name == "Mutual Funds" else row_name
            add_shareholding_point(series, target_name, period, value)

    return build_shareholding_from_series(series, "Upstox public shareholding")


def add_shareholding_point(series, name, period, percent_value):
    if not period or not is_finite(percent_value):
        return
    ratio_value = percent_value / 100
    existing = next((item for item in series[name] if item["period"] == period), None)
    if existing:
        existing["value"] = add_numbers(existing["value"], ratio_value)
    else:
        series[name].append({"period": normalize_period_label(period), "value": ratio_value})


def build_shareholding_from_series(series, source):
    rows = []
    for name in OWNERSHIP_ROW_NAMES:
        quarters = [
            quarter
            for quarter in sorted(series.get(name) or [], key=lambda item: shareholding_period_key(item["period"]))
            if is_finite(quarter.get("value"))
        ][-4:]
        if quarters:
            rows.append({"name": name, "quarters": quarters})

    return {
        "periods": shareholding_periods_from_rows(rows),
        "rows": rows,
        "source": source if rows else "",
    }


def nested_percent_total(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        if is_finite(value.get("percent")):
            return value["percent"]
        values = [nested_percent_total(item) for item in value.values()]
        values = [item for item in values if is_finite(item)]
        return sum(values) if values else None
    if isinstance(value, list):
        values = [nested_percent_total(item) for item in value]
        values = [item for item in values if is_finite(item)]
        return sum(values) if values else None
    return None


def merge_shareholding(primary, fallback):
    primary = primary or {"periods": [], "rows": []}
    fallback = fallback or {"periods": [], "rows": []}
    rows_by_name = {
        row.get("name"): {"name": row.get("name"), "quarters": list(row.get("quarters") or [])}
        for row in primary.get("rows") or []
        if row.get("name")
    }

    for row in fallback.get("rows") or []:
        name = row.get("name")
        quarters = row.get("quarters") or []
        if not name or not quarters:
            continue
        if name not in rows_by_name or not rows_by_name[name].get("quarters"):
            rows_by_name[name] = {"name": name, "quarters": list(quarters)}

    rows = [
        {"name": name, "quarters": sorted(row["quarters"], key=lambda item: shareholding_period_key(item["period"]))[-4:]}
        for name, row in rows_by_name.items()
        if row.get("quarters")
    ]

    source = combine_source_labels(primary.get("source"), fallback.get("source"))
    return {
        "periods": shareholding_periods_from_rows(rows),
        "rows": rows,
        "source": source,
    }


def has_shareholding_categories(shareholding, names):
    rows_by_name = {
        row.get("name"): row
        for row in (shareholding or {}).get("rows") or []
    }
    return all(rows_by_name.get(name, {}).get("quarters") for name in names)


def latest_shareholding_value(shareholding, name):
    rows = (shareholding or {}).get("rows") or []
    row = next((item for item in rows if item.get("name") == name), None)
    quarters = row.get("quarters") if row else []
    return quarters[-1]["value"] if quarters and is_finite(quarters[-1].get("value")) else None


def shareholding_periods_from_rows(rows):
    periods = {
        quarter["period"]
        for row in rows
        for quarter in row.get("quarters") or []
        if quarter.get("period")
    }
    return sorted(periods, key=shareholding_period_key)


def normalize_period_label(period):
    text = clean_html(str(period)).replace("Sept", "Sep").strip()
    match = re.search(r"\b(Mar|Jun|Sep|Dec)\s+'?(\d{2}|\d{4})\b", text, flags=re.IGNORECASE)
    if not match:
        return text
    month = match.group(1).title()
    year = int(match.group(2))
    year = 2000 + year if year < 100 else year
    return f"{month} {year}"


def shareholding_period_key(period):
    text = normalize_period_label(period)
    month_order = {"Mar": 3, "Jun": 6, "Sep": 9, "Dec": 12}
    match = re.search(r"\b(Mar|Jun|Sep|Dec)\s+(\d{4})\b", text)
    if not match:
        return (0, 0, text)
    return (int(match.group(2)), month_order.get(match.group(1), 0), text)


def company_slug_candidates(symbol, long_name, groww):
    base_symbol = re.sub(r"\.(NS|BO)$", "", symbol, flags=re.IGNORECASE)
    seeds = [long_name or "", base_symbol]
    candidates = []

    for seed in seeds:
        cleaned = clean_company_slug_seed(seed)
        variants = [cleaned]
        variants.append(re.sub(r"\blimited\b", "ltd", cleaned))
        variants.append(re.sub(r"\bltd\b", "limited", cleaned))
        variants.append(re.sub(r"\b(limited|ltd)\b", "", cleaned))
        for variant in variants:
            slug = slugify_company_name(variant)
            if slug and slug not in candidates:
                candidates.append(slug)

    if groww:
        return candidates[:8]
    return candidates[:10]


def clean_company_slug_seed(value):
    text = html.unescape(str(value or "")).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("&", " and ")
    text = text.replace("'", "")
    text = re.sub(r"\b(company|co)\b", " ", text)
    return text


def slugify_company_name(value):
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text


def stock_slug_seed(value):
    return slugify_company_name(clean_company_slug_seed(value or "stock"))


def combine_source_labels(*sources):
    labels = []
    for source in sources:
        for label in str(source or "").split(" + "):
            clean = label.strip()
            if clean and clean not in labels:
                labels.append(clean)
    return " + ".join(labels)


def extract_sec_metrics(payload, fallback_name):
    facts = payload.get("facts") or {}
    usgaap = facts.get("us-gaap") or {}
    dei = facts.get("dei") or {}
    revenue = latest_annual_pair(usgaap, ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"])
    net_income = latest_annual_pair(usgaap, ["NetIncomeLoss", "ProfitLoss"])
    operating_income = latest_annual_pair(usgaap, ["OperatingIncomeLoss"])
    gross_profit = latest_annual_pair(usgaap, ["GrossProfit"])
    operating_cash_flow = latest_annual_pair(usgaap, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = latest_annual_pair(usgaap, ["PaymentsToAcquirePropertyPlantAndEquipment"])
    eps = latest_annual_pair(usgaap, ["EarningsPerShareDiluted"], "USD/shares")
    assets = latest_instant(usgaap, ["Assets"])
    liabilities = latest_instant(usgaap, ["Liabilities"])
    equity = latest_instant(usgaap, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    current_assets = latest_instant(usgaap, ["AssetsCurrent"])
    current_liabilities = latest_instant(usgaap, ["LiabilitiesCurrent"])
    total_cash = latest_instant(usgaap, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"])
    debt_current = latest_instant(usgaap, ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"])
    debt_non_current = latest_instant(usgaap, ["LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"])
    total_debt = add_numbers(debt_current["value"], debt_non_current["value"]) or latest_instant(usgaap, ["LongTermDebt"])["value"]
    shares_outstanding = latest_instant(dei, ["EntityCommonStockSharesOutstanding"], "shares")
    free_cashflow = (
        operating_cash_flow["current"] - abs(capex["current"])
        if is_finite(operating_cash_flow["current"]) and is_finite(capex["current"])
        else None
    )
    debt_ratio = ratio(total_debt, equity["value"])

    return {
        "companyName": payload.get("entityName") or fallback_name or "",
        "source": "SEC company facts",
        "latestFiling": latest_filing_date([revenue["currentItem"], net_income["currentItem"], assets["item"], shares_outstanding["item"]]),
        "metrics": {
            "revenue": revenue["current"],
            "netIncome": net_income["current"],
            "assets": assets["value"],
            "liabilities": liabilities["value"],
            "equity": equity["value"],
            "sharesOutstanding": shares_outstanding["value"],
            "epsDiluted": eps["current"],
            "profitMargins": ratio(net_income["current"], revenue["current"]),
            "operatingMargins": ratio(operating_income["current"], revenue["current"]),
            "grossMargins": ratio(gross_profit["current"], revenue["current"]),
            "returnOnEquity": ratio(net_income["current"], equity["value"]),
            "revenueGrowth": growth(revenue["current"], revenue["previous"]),
            "earningsGrowth": growth(net_income["current"], net_income["previous"]),
            "debtToEquity": debt_ratio * 100 if debt_ratio is not None else None,
            "currentRatio": ratio(current_assets["value"], current_liabilities["value"]),
            "totalCash": total_cash["value"],
            "totalDebt": total_debt,
            "freeCashflow": free_cashflow,
        },
    }


def latest_annual_pair(facts, concepts, preferred_unit="USD"):
    values = [
        item for item in sec_values(facts, concepts, preferred_unit)
        if item.get("start") and item.get("end") and item.get("form", "").startswith("10-K")
    ]
    values.sort(key=lambda item: (item.get("end") or "", item.get("filed") or ""), reverse=True)
    yearly = []
    seen = set()
    for item in values:
        key = item.get("end")
        if key not in seen and is_finite(item.get("val")):
            yearly.append(item)
            seen.add(key)
        if len(yearly) == 2:
            break
    return {
        "current": yearly[0].get("val") if len(yearly) > 0 else None,
        "previous": yearly[1].get("val") if len(yearly) > 1 else None,
        "currentItem": yearly[0] if len(yearly) > 0 else None,
        "previousItem": yearly[1] if len(yearly) > 1 else None,
    }


def latest_instant(facts, concepts, preferred_unit="USD"):
    values = [
        item for item in sec_values(facts, concepts, preferred_unit)
        if item.get("end") and is_finite(item.get("val"))
    ]
    values.sort(key=lambda item: (item.get("end") or "", item.get("filed") or ""), reverse=True)
    item = values[0] if values else None
    return {"value": item.get("val") if item else None, "item": item}


def sec_values(facts, concepts, preferred_unit):
    for concept in concepts:
        fact = facts.get(concept)
        if not fact or not fact.get("units"):
            continue
        units = fact["units"]
        unit_key = preferred_unit if preferred_unit in units else next(iter(units.keys()))
        values = units.get(unit_key) or []
        if values:
            return values
    return []


def latest_filing_date(items):
    dates = sorted([item.get("filed") for item in items if item and item.get("filed")], reverse=True)
    return dates[0] if dates else None


def implied_market_cap(metrics, current_price):
    if not is_finite(metrics.get("sharesOutstanding")) or not is_finite(current_price):
        return None
    return metrics["sharesOutstanding"] * current_price


def implied_trailing_pe(metrics, current_price):
    if not is_finite(metrics.get("epsDiluted")) or not is_finite(current_price) or metrics["epsDiluted"] <= 0:
        return None
    return current_price / metrics["epsDiluted"]


def implied_price_to_book(metrics, current_price):
    market_cap = implied_market_cap(metrics, current_price)
    if not is_finite(market_cap) or not is_finite(metrics.get("equity")) or metrics["equity"] <= 0:
        return None
    return market_cap / metrics["equity"]


def ratio(numerator, denominator):
    if not is_finite(numerator) or not is_finite(denominator) or denominator == 0:
        return None
    return numerator / denominator


def growth(current, previous):
    if not is_finite(current) or not is_finite(previous) or previous == 0:
        return None
    return (current - previous) / abs(previous)


def add_numbers(*values):
    usable = [value for value in values if is_finite(value)]
    return sum(usable) if usable else None


def extract_events(summary, screener):
    calendar = summary.get("calendarEvents") or {}
    earnings = calendar.get("earnings") or {}
    trend = summary.get("earningsTrend") or {}
    upgrades = summary.get("upgradeDowngradeHistory") or {}
    items = []

    for date in [raw(value) for value in arrayify(earnings.get("earningsDate"))]:
        if date:
            items.append({"type": "Earnings", "date": to_date(date), "detail": "Expected results date"})

    if raw(calendar.get("exDividendDate")):
        items.append({"type": "Ex-dividend", "date": to_date(raw(calendar.get("exDividendDate"))), "detail": "Dividend eligibility date"})

    if raw(calendar.get("dividendDate")):
        items.append({"type": "Dividend payment", "date": to_date(raw(calendar.get("dividendDate"))), "detail": "Expected dividend payment date"})

    next_quarter = next((item for item in arrayify(trend.get("trend")) if item.get("period") and "+1q" in item["period"]), None)
    if next_quarter:
        estimate = raw(((next_quarter.get("earningsEstimate") or {}).get("avg")))
        items.append({"type": "Earnings trend", "date": None, "detail": f"Next quarter EPS estimate: {format_estimate(estimate)}"})

    for item in arrayify(upgrades.get("history"))[:3]:
        items.append({
            "type": "Analyst action",
            "date": to_date(item.get("epochGradeDate")) if item.get("epochGradeDate") else None,
            "detail": " - ".join([value for value in [item.get("firm"), item.get("action"), item.get("toGrade")] if value]),
        })

    items.extend(arrayify(screener.get("events")))
    items = [item for item in items if item.get("date") or item.get("detail")]
    return sorted(items, key=lambda item: item.get("date") or "9999-12-31")


def build_growth_drivers(symbol, long_name, fundamentals, screener, sector_analysis=None):
    ownership = build_ownership_trend(screener.get("shareholding"), fundamentals)
    catalysts = safe_growth_catalyst_headlines(symbol, long_name)
    budget_impacts = build_budget_impacts(symbol, long_name, fundamentals)
    sector_analysis = sector_analysis or stock_sector_analysis(fundamentals)
    data_notes = []

    if not ownership["rows"]:
        data_notes.append("Promoter/FII/DII trend was not available from the current data source.")
    if not catalysts:
        data_notes.append("No recent order, contract, budget, or policy headline matched the current scan.")
    if not budget_impacts:
        data_notes.append("No clear government budget allocation theme matched the available sector or watchlist tags.")

    return {
        "lookback": "Last 3-4 quarters / about 12 months where source data is available",
        "summary": summarize_growth_drivers(ownership, catalysts, budget_impacts, sector_analysis),
        "ownership": ownership,
        "catalysts": catalysts,
        "budgetImpacts": budget_impacts,
        "sectorAnalysis": sector_analysis,
        "dataNotes": data_notes,
    }


def build_ownership_trend(shareholding, fundamentals):
    rows = []
    shareholding = shareholding or {}
    for row in shareholding.get("rows") or []:
        quarters = row.get("quarters") or []
        if not quarters:
            continue
        first = quarters[0]["value"]
        latest = quarters[-1]["value"]
        previous = quarters[-2]["value"] if len(quarters) > 1 else None
        change_points = (latest - first) * 100 if is_finite(latest) and is_finite(first) else None
        quarter_change_points = (latest - previous) * 100 if is_finite(latest) and is_finite(previous) else None
        rows.append({
            "name": row["name"],
            "latest": round_ratio_or_none(latest),
            "previous": round_ratio_or_none(previous),
            "changePoints": round_or_none(change_points),
            "quarterChangePoints": round_or_none(quarter_change_points),
            "trend": holding_trend(change_points),
            "latestPeriod": quarters[-1].get("period"),
            "previousPeriod": quarters[-2].get("period") if len(quarters) > 1 else None,
            "quarters": [
                {"period": quarter["period"], "value": round_ratio_or_none(quarter["value"])}
                for quarter in quarters
            ],
        })

    if not rows and is_finite(fundamentals.get("promoterHolding")):
        rows.append({
            "name": "Promoters",
            "latest": round_ratio_or_none(fundamentals["promoterHolding"]),
            "previous": None,
            "changePoints": None,
            "quarterChangePoints": None,
            "trend": "Latest only",
            "latestPeriod": "Latest",
            "previousPeriod": None,
            "quarters": [{"period": "Latest", "value": round_ratio_or_none(fundamentals["promoterHolding"])}],
        })

    signals = []
    for name in ["Promoters", "FIIs", "DIIs"]:
        row = next((item for item in rows if item["name"] == name), None)
        if row and is_finite(row.get("changePoints")):
            signals.append(f"{name}: {row['trend']} by {abs(row['changePoints']):.2f} pp over available quarters.")
        elif row:
            signals.append(f"{name}: latest holding {row['latest'] * 100:.2f}%.")

    return {
        "periods": shareholding.get("periods") or [],
        "rows": rows,
        "signals": signals,
        "flags": build_ownership_flags(rows, shareholding.get("source") or ""),
        "source": shareholding.get("source") or "",
    }


def build_ownership_flags(rows, source):
    flags = []
    rows_by_name = {row.get("name"): row for row in rows if row.get("name")}

    for name in ["Promoters", "FIIs", "DIIs"]:
        row = rows_by_name.get(name)
        if not row or not is_finite(row.get("latest")):
            flags.append({
                "type": "warning",
                "title": f"{name} holding unavailable",
                "detail": "Cross-check the latest exchange shareholding pattern before relying on ownership signals.",
            })

    promoter_change = (rows_by_name.get("Promoters") or {}).get("quarterChangePoints")
    if is_finite(promoter_change):
        if promoter_change <= -0.5:
            flags.append({
                "type": "warning",
                "title": "Promoter holding reduced",
                "detail": f"Promoters reduced holding by {abs(promoter_change):.2f} pp versus the previous available quarter.",
            })
        elif promoter_change >= 0.5:
            flags.append({
                "type": "positive",
                "title": "Promoter holding improved",
                "detail": f"Promoters increased holding by {promoter_change:.2f} pp versus the previous available quarter.",
            })

    for name in ["FIIs", "DIIs"]:
        change = (rows_by_name.get(name) or {}).get("quarterChangePoints")
        if not is_finite(change) or abs(change) < 0.5:
            continue
        flags.append({
            "type": "positive" if change > 0 else "warning",
            "title": f"{name} {'accumulation' if change > 0 else 'reduction'}",
            "detail": f"{name} {'added' if change > 0 else 'reduced'} {abs(change):.2f} pp versus the previous available quarter.",
        })

    if source:
        flags.append({
            "type": "neutral",
            "title": "Ownership source",
            "detail": source,
        })

    return flags[:6]


def holding_trend(change_points):
    if not is_finite(change_points):
        return "Latest only"
    if change_points > 0.15:
        return "Increasing"
    if change_points < -0.15:
        return "Reducing"
    return "Stable"


def safe_growth_catalyst_headlines(symbol, long_name):
    try:
        return cached(
            f"growth-headlines:{symbol}",
            lambda: get_growth_catalyst_headlines(symbol, long_name),
            30 * 60,
        )
    except Exception:
        return []


def get_growth_catalyst_headlines(symbol, long_name):
    query = f"{long_name or symbol} order contract budget government allocation capex"
    endpoint = f"https://query2.finance.yahoo.com/v1/finance/search?q={quote(query)}&quotesCount=0&newsCount=10"
    payload = fetch_json(endpoint)
    items = []
    for news_item in payload.get("news", [])[:10]:
        title = str(news_item.get("title") or "").strip()
        summary = str(news_item.get("summary") or "").strip()
        if not title:
            continue
        text = f"{title} {summary}"
        if not has_growth_catalyst(text):
            continue
        published_at = iso_from_epoch(news_item["providerPublishTime"]) if news_item.get("providerPublishTime") else None
        age_days = (time.time() - float(news_item.get("providerPublishTime") or time.time())) / 86400
        if age_days > 370:
            continue
        items.append({
            "type": classify_growth_catalyst(text),
            "title": title,
            "publisher": news_item.get("publisher") or "",
            "publishedAt": published_at,
            "url": news_item.get("link") or "",
            "value": extract_order_value(text),
            "detail": catalyst_detail(text),
            "score": score_growth_catalyst(text, news_item.get("providerPublishTime")),
        })
    return sorted(items, key=lambda item: item["score"], reverse=True)[:5]


def has_growth_catalyst(text):
    lower_text = text.lower()
    return any(keyword in lower_text for keyword in GROWTH_CATALYST_KEYWORDS)


def classify_growth_catalyst(text):
    lower_text = text.lower()
    if any(keyword in lower_text for keyword in ORDER_CATALYST_KEYWORDS):
        return "Order / contract"
    if any(keyword in lower_text for keyword in ["budget", "allocation", "government", "govt", "ministry", "cabinet", "policy", "capex"]):
        return "Budget / policy"
    return "Growth catalyst"


def catalyst_detail(text):
    value = extract_order_value(text)
    if value:
        return f"Reported value: {value}"
    catalyst_type = classify_growth_catalyst(text)
    if catalyst_type == "Budget / policy":
        return "Policy or allocation headline; verify direct company impact."
    return "Verify exchange filing, order size, margin profile, and execution timeline."


def score_growth_catalyst(text, published_epoch):
    lower_text = text.lower()
    keyword_score = sum(7 for keyword in GROWTH_CATALYST_KEYWORDS if keyword in lower_text)
    value_score = 14 if extract_order_value(text) else 0
    recency_score = 0
    if published_epoch:
        age_days = max(0, (time.time() - float(published_epoch)) / 86400)
        recency_score = clamp(28 - age_days * 0.12, 0, 28)
    return round(clamp(keyword_score + value_score + recency_score, 0, 100))


def build_relative_strength(candles, benchmark):
    benchmark = benchmark or {}
    stock_closes = [item["close"] for item in candles if is_finite(item.get("close"))]
    benchmark_candles = benchmark.get("candles") or []
    benchmark_closes = [item["close"] for item in benchmark_candles if is_finite(item.get("close"))]
    benchmark_symbol = benchmark.get("symbol") or ""
    benchmark_name = benchmark.get("name") or benchmark_symbol or "Benchmark"
    periods = [
        ("oneWeek", "1W", 5),
        ("oneMonth", "1M", 21),
        ("threeMonth", "3M", 63),
        ("sixMonth", "6M", 126),
    ]
    rows = []

    for key, label, sessions in periods:
        stock_return = period_return(stock_closes, sessions)
        benchmark_return = period_return(benchmark_closes, sessions)
        spread = (
            stock_return - benchmark_return
            if is_finite(stock_return) and is_finite(benchmark_return)
            else None
        )
        rows.append({
            "period": key,
            "label": label,
            "stockReturn": round_or_none(stock_return),
            "benchmarkReturn": round_or_none(benchmark_return),
            "spread": round_or_none(spread),
        })

    spreads = [row["spread"] for row in rows if is_finite(row.get("spread"))]
    average_spread = sum(spreads) / len(spreads) if spreads else None
    label = relative_strength_label(average_spread)

    return {
        "available": bool(spreads),
        "expected": bool(benchmark_symbol),
        "benchmarkSymbol": benchmark_symbol,
        "benchmarkName": benchmark_name,
        "rows": rows,
        "averageSpread": round_or_none(average_spread),
        "label": label,
        "summary": relative_strength_summary(label, average_spread, benchmark_name),
    }


def relative_strength_label(average_spread):
    if not is_finite(average_spread):
        return "Unavailable"
    if average_spread >= 5:
        return "Strong outperformer"
    if average_spread >= 1.5:
        return "Outperforming"
    if average_spread > -1.5:
        return "In line"
    return "Lagging"


def relative_strength_summary(label, average_spread, benchmark_name):
    if not is_finite(average_spread):
        return f"Benchmark comparison with {benchmark_name} could not be calculated from the available data."
    direction = "outperforming" if average_spread >= 0 else "underperforming"
    return f"{label}: average {abs(average_spread):.2f} pp {direction} versus {benchmark_name} across available lookbacks."


def build_budget_impacts(symbol, long_name, fundamentals):
    context = " ".join([
        symbol,
        long_name or "",
        fundamentals.get("sector") or "",
        fundamentals.get("industry") or "",
        " ".join(watchlist_tags(symbol)),
    ]).lower()
    themes = [
        {
            "theme": "Defence procurement",
            "keywords": ["defence", "defense", "aerospace", "aircraft", "shipbuilding", "orders"],
            "impact": "Higher defence procurement or capex allocation can improve order visibility for defence manufacturers and suppliers.",
        },
        {
            "theme": "Railway capex",
            "keywords": ["railway", "railways", "rail", "wagon", "rvnl", "irfc", "ircon", "rites", "railtel"],
            "impact": "Railway capex allocation can support order flow, project execution, and working-capital cycles for rail-linked companies.",
        },
        {
            "theme": "Power and renewable energy",
            "keywords": ["power", "renewable", "solar", "transmission", "energy", "ireda", "nhpc", "sjvn"],
            "impact": "Power, grid, and renewable allocations can support project pipelines, financing demand, and equipment orders.",
        },
        {
            "theme": "Infrastructure and capital goods",
            "keywords": ["infrastructure", "capital goods", "construction", "cement", "engineering", "epc"],
            "impact": "Public capex and infrastructure allocation can lift order books for EPC, capital goods, cement, and construction-linked names.",
        },
        {
            "theme": "Healthcare and pharma",
            "keywords": ["pharma", "healthcare", "hospital", "medicine", "diagnostic"],
            "impact": "Healthcare allocations and policy changes can affect demand, pricing, and institutional procurement.",
        },
        {
            "theme": "Oil, gas, and commodities",
            "keywords": ["oil", "gas", "petrochemical", "metals", "copper", "coal", "energy"],
            "impact": "Energy and commodity-related allocations or duties can change input costs, realizations, and downstream margins.",
        },
        {
            "theme": "Financials and capex cycle",
            "keywords": ["bank", "finance", "financial", "exchange", "volume"],
            "impact": "Budget-driven capex, credit growth, and market-activity shifts can affect lending, fee income, and transaction volumes.",
        },
    ]
    impacts = [
        {
            "theme": theme["theme"],
            "impact": theme["impact"],
            "basis": "Matched sector, industry, or watchlist tags",
        }
        for theme in themes
        if any(keyword in context for keyword in theme["keywords"])
    ]
    return impacts[:3]


def watchlist_tags(symbol):
    tags = []
    for stock in market_activity_universe():
        if stock.get("symbol") == symbol:
            tags.extend(stock.get("tags") or [])
    return list(dict.fromkeys(tags))


def summarize_growth_drivers(ownership, catalysts, budget_impacts, sector_analysis=None):
    positive_holding = [
        row["name"]
        for row in ownership.get("rows", [])
        if is_finite(row.get("changePoints")) and row["changePoints"] > 0.15 and row["name"] in {"Promoters", "FIIs", "DIIs"}
    ]
    reducing_holding = [
        row["name"]
        for row in ownership.get("rows", [])
        if is_finite(row.get("changePoints")) and row["changePoints"] < -0.15 and row["name"] in {"Promoters", "FIIs", "DIIs"}
    ]
    parts = []
    if positive_holding:
        parts.append(f"Accumulation visible in {', '.join(positive_holding)} holdings.")
    if reducing_holding:
        parts.append(f"Reduction visible in {', '.join(reducing_holding)} holdings.")
    if catalysts:
        parts.append(f"{len(catalysts)} recent order/policy catalyst headline(s) matched the scan.")
    if budget_impacts:
        parts.append(f"{len(budget_impacts)} budget/sector theme(s) may affect growth visibility.")
    if sector_analysis and sector_analysis.get("available"):
        matched = sector_analysis.get("matchedSector") or {}
        parts.append(f"Moneycontrol sector context: {matched.get('sector')} is {matched.get('trend', 'tracked')} with score {matched.get('score', 'n/a')}/100.")
    return " ".join(parts) or "Limited ownership or catalyst data was available; use manual filings and exchange announcements for confirmation."


def extract_screener_ratios(page):
    block = match_first(page, r"<ul id=\"top-ratios\">([\s\S]*?)</ul>")
    ratios = {}
    for match in re.finditer(r"<li[\s\S]*?</li>", block, flags=re.IGNORECASE):
        item = match.group(0)
        name = clean_html(match_first(item, r"<span class=\"name\">([\s\S]*?)</span>"))
        numbers = [
            parse_loose_number(clean_html(number_match.group(1)))
            for number_match in re.finditer(r"<span class=\"number\">([\s\S]*?)</span>", item, flags=re.IGNORECASE)
        ]
        numbers = [number for number in numbers if is_finite(number)]
        if name and numbers:
            ratios[name] = numbers[0] if len(numbers) == 1 else numbers
    return ratios


def extract_screener_shareholding(page):
    block = match_first(page, r"<section[^>]*id=\"shareholding\"[^>]*>([\s\S]*?)</section>")
    if not block:
        return {"periods": [], "rows": []}

    table = match_first(block, r"<table[^>]*>([\s\S]*?)</table>")
    if not table:
        return {"periods": [], "rows": []}

    headers = [
        clean_html(match.group(1))
        for match in re.finditer(r"<th[^>]*>([\s\S]*?)</th>", table, flags=re.IGNORECASE)
    ]
    periods = [
        header
        for header in headers
        if re.search(r"\b(?:Mar|Jun|Sep|Dec)\s+\d{4}\b", header, flags=re.IGNORECASE)
    ]
    if not periods:
        return {"periods": [], "rows": []}

    rows = []
    for match in re.finditer(r"<tr[\s\S]*?</tr>", table, flags=re.IGNORECASE):
        cells = [
            clean_html(cell_match.group(1))
            for cell_match in re.finditer(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", match.group(0), flags=re.IGNORECASE)
        ]
        if len(cells) < 2:
            continue

        row_name = normalize_shareholder_name(cells[0])
        if row_name not in {"Promoters", "FIIs", "DIIs", "Public"}:
            continue

        values = [parse_percent_cell(value) for value in cells[1:1 + len(periods)]]
        quarters = [
            {"period": period, "value": value}
            for period, value in zip(periods, values)
            if is_finite(value)
        ][-4:]
        if quarters:
            rows.append({"name": row_name, "quarters": quarters})

    return {
        "periods": shareholding_periods_from_rows(rows),
        "rows": rows,
        "source": "Screener.in shareholding",
    }


def extract_screener_quarterly_results(page):
    block = match_first(page, r"<section[^>]*id=\"quarters\"[^>]*>([\s\S]*?)</section>")
    if not block:
        return {}

    table = match_first(block, r"<table[^>]*>([\s\S]*?)</table>")
    if not table:
        return {}

    headers = [
        clean_html(match.group(1))
        for match in re.finditer(r"<th[^>]*>([\s\S]*?)</th>", table, flags=re.IGNORECASE)
    ]
    periods = [
        header
        for header in headers
        if re.search(r"\b(?:Mar|Jun|Sep|Dec)\s+\d{4}\b", header, flags=re.IGNORECASE)
    ]
    if not periods:
        return {}

    row_values = {}
    for match in re.finditer(r"<tr[\s\S]*?</tr>", table, flags=re.IGNORECASE):
        cells = [
            clean_html(cell_match.group(1))
            for cell_match in re.finditer(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", match.group(0), flags=re.IGNORECASE)
        ]
        if len(cells) < len(periods) + 1:
            continue

        metric = normalize_quarterly_metric(cells[0])
        if not metric:
            continue
        values = [parse_quarterly_number(value) for value in cells[1:1 + len(periods)]]
        row_values[metric] = values

    if not row_values:
        return {}

    latest_index = latest_quarter_index(row_values, periods)
    if latest_index is None:
        return {}

    sales = quarterly_value(row_values, "sales", latest_index)
    net_profit = quarterly_value(row_values, "netProfit", latest_index)
    operating_profit = quarterly_value(row_values, "operatingProfit", latest_index)
    opm_percent = quarterly_value(row_values, "opmPercent", latest_index)
    eps = quarterly_value(row_values, "eps", latest_index)
    summary = quarterly_result_text(
        periods[latest_index],
        sales,
        net_profit,
        operating_profit,
        opm_percent,
        eps,
        quarterly_change(row_values, "sales", latest_index, 1),
        quarterly_change(row_values, "netProfit", latest_index, 1),
        quarterly_change(row_values, "sales", latest_index, 4),
        quarterly_change(row_values, "netProfit", latest_index, 4),
        "INR crore",
    )

    return {
        "available": True,
        "period": periods[latest_index],
        "source": "Screener.in quarterly results",
        "currency": "INR crore",
        "sales": round_or_none(sales),
        "netProfit": round_or_none(net_profit),
        "operatingProfit": round_or_none(operating_profit),
        "opmPercent": round_or_none(opm_percent),
        "eps": round_or_none(eps),
        "salesQoqPercent": round_or_none(quarterly_change(row_values, "sales", latest_index, 1)),
        "netProfitQoqPercent": round_or_none(quarterly_change(row_values, "netProfit", latest_index, 1)),
        "salesYoyPercent": round_or_none(quarterly_change(row_values, "sales", latest_index, 4)),
        "netProfitYoyPercent": round_or_none(quarterly_change(row_values, "netProfit", latest_index, 4)),
        "summary": summary,
    }


def normalize_quarterly_metric(value):
    text = clean_html(value).replace("+", "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    if text in {"sales", "revenue"}:
        return "sales"
    if text == "operating profit":
        return "operatingProfit"
    if text in {"opm %", "operating profit margin %"}:
        return "opmPercent"
    if text == "net profit":
        return "netProfit"
    if text.startswith("eps"):
        return "eps"
    return ""


def parse_quarterly_number(value):
    if str(value or "").strip() in {"", "-", "--"}:
        return None
    return parse_loose_number(value)


def latest_quarter_index(row_values, periods):
    for index in range(len(periods) - 1, -1, -1):
        if is_finite(quarterly_value(row_values, "sales", index)) or is_finite(quarterly_value(row_values, "netProfit", index)):
            return index
    return None


def quarterly_value(row_values, key, index):
    values = row_values.get(key) or []
    return values[index] if 0 <= index < len(values) and is_finite(values[index]) else None


def quarterly_change(row_values, key, latest_index, offset):
    current = quarterly_value(row_values, key, latest_index)
    previous = quarterly_value(row_values, key, latest_index - offset)
    if not is_finite(current) or not is_finite(previous) or previous == 0:
        return None
    return safe_divide(current - previous, abs(previous)) * 100


def latest_quarterly_results_summary(summary):
    earnings = (summary or {}).get("earnings") or {}
    chart = earnings.get("financialsChart") or {}
    rows = arrayify(chart.get("quarterly"))
    if not rows:
        return {}
    latest = rows[-1]
    period = str(latest.get("date") or "Latest quarter")
    sales = raw(latest.get("revenue"))
    net_profit = raw(latest.get("earnings"))
    summary_text = quarterly_result_text(period, sales, net_profit, None, None, None, None, None, None, None, "")
    return {
        "available": True,
        "period": period,
        "source": "Yahoo Finance earnings",
        "currency": "",
        "sales": round_or_none(sales),
        "netProfit": round_or_none(net_profit),
        "summary": summary_text,
    }


def quarterly_result_text(period, sales, net_profit, operating_profit, opm_percent, eps, sales_qoq, profit_qoq, sales_yoy, profit_yoy, currency):
    parts = [f"{period} results"]
    if is_finite(sales):
        parts.append(f"sales {format_quarterly_money(sales, currency)}")
        sales_changes = format_quarterly_changes(sales_qoq, sales_yoy)
        if sales_changes:
            parts[-1] += f" ({sales_changes})"
    if is_finite(net_profit):
        parts.append(f"net profit {format_quarterly_money(net_profit, currency)}")
        profit_changes = format_quarterly_changes(profit_qoq, profit_yoy)
        if profit_changes:
            parts[-1] += f" ({profit_changes})"
    if is_finite(operating_profit):
        parts.append(f"operating profit {format_quarterly_money(operating_profit, currency)}")
    if is_finite(opm_percent):
        parts.append(f"OPM {opm_percent:.2f}%")
    if is_finite(eps):
        parts.append(f"EPS {eps:.2f}")
    return "; ".join(parts) + "."


def format_quarterly_money(value, currency):
    if not is_finite(value):
        return "n/a"
    if currency == "INR crore":
        return f"INR {value:,.0f} cr"
    return f"{value:,.0f}"


def format_quarterly_changes(qoq, yoy):
    changes = []
    if is_finite(qoq):
        changes.append(f"QoQ {qoq:+.2f}%")
    if is_finite(yoy):
        changes.append(f"YoY {yoy:+.2f}%")
    return ", ".join(changes)


def normalize_shareholder_name(value):
    text = clean_html(value).replace("+", "").strip()
    lower_text = text.lower()
    if "promoter" in lower_text:
        return "Promoters"
    if "fii" in lower_text or "foreign" in lower_text:
        return "FIIs"
    if "dii" in lower_text or "domestic" in lower_text:
        return "DIIs"
    if "public" in lower_text:
        return "Public"
    return text


def parse_percent_cell(value):
    number = parse_loose_number(value)
    return number / 100 if is_finite(number) else None


def extract_screener_events(page):
    events = []
    board_meeting_date = match_first(page, r"Board meets ([A-Z][a-z]+ \d{1,2}, \d{4})")
    if board_meeting_date:
        events.append({
            "type": "Board meeting",
            "date": to_iso_date(board_meeting_date),
            "detail": "Expected audited results and final dividend discussion",
        })
    return events


def extract_meta_description(page):
    return html.unescape(match_first(page, r"<meta name=\"description\" content=\"([^\"]*)\""))


def parse_description_number(text, pattern):
    return parse_loose_number(match_first(text, pattern))


def parse_description_percent(text, pattern):
    value = parse_loose_number(match_first(text, pattern))
    return value / 100 if is_finite(value) else None


def clean_html(value):
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return html.unescape(text).replace("\xa0", " ")


def match_first(value, pattern):
    match = re.search(pattern, str(value or ""), flags=re.IGNORECASE)
    return match.group(1) if match else ""


def parse_loose_number(value):
    normalized = str(value or "").replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def crore_to_rupees(value):
    return value * 10_000_000 if is_finite(value) else None


def ratio_percent(value):
    return value / 100 if is_finite(value) else None


def to_iso_date(value):
    text = str(value or "")
    try:
        return datetime.strptime(text, "%B %d, %Y").date().isoformat()
    except ValueError:
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except ValueError:
            return None


def analyze_latest_candle(candles):
    if not candles:
        return {
            "available": False,
            "pattern": "Unavailable",
            "summary": "Latest daily candle could not be analysed.",
        }

    current = candles[-1]
    previous = candles[-2] if len(candles) > 1 else None
    open_price = current.get("open")
    high = current.get("high")
    low = current.get("low")
    close = current.get("close")
    if not all(is_finite(value) for value in [open_price, high, low, close]) or high <= low:
        return {
            "available": False,
            "pattern": "Unavailable",
            "summary": "Latest daily candle has incomplete OHLC data.",
        }

    candle_range = high - low
    body = abs(close - open_price)
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    body_percent = safe_divide(body, candle_range) * 100
    upper_percent = safe_divide(upper_wick, candle_range) * 100
    lower_percent = safe_divide(lower_wick, candle_range) * 100
    close_position = safe_divide(close - low, candle_range) * 100
    midpoint = (open_price + close) / 2
    direction = "bullish" if close > open_price else "bearish" if close < open_price else "neutral"
    previous_open = previous.get("open") if previous else None
    previous_close = previous.get("close") if previous else None

    pattern = "Standard candle"
    meaning = "The latest daily candle is balanced; wait for a break of its high or low before reading direction."
    expectation = "Next candle should prove direction by closing beyond the latest candle high or low."
    confirmation = high if direction != "bearish" else low
    invalidation = low if direction != "bearish" else high
    bias = "Neutral"

    if body_percent <= 10 and upper_percent < 55 and lower_percent < 55:
        pattern = "Doji"
        bias = "Indecision"
        meaning = "The candle shows indecision because open and close are very close."
        expectation = "The next candle should break and close outside the doji range to confirm direction."
        confirmation = high
        invalidation = low
    elif (
        previous
        and all(is_finite(value) for value in [previous_open, previous_close])
        and close > open_price
        and previous_close < previous_open
        and open_price <= previous_close
        and close >= previous_open
    ):
        pattern = "Bullish Engulfing"
        bias = "Bullish reversal"
        meaning = "Buyers fully absorbed the prior bearish body, which can mark a reversal attempt."
        expectation = "The next candle should hold above the engulfing midpoint and ideally close above the latest high."
        confirmation = high
        invalidation = midpoint
    elif (
        previous
        and all(is_finite(value) for value in [previous_open, previous_close])
        and close < open_price
        and previous_close > previous_open
        and open_price >= previous_close
        and close <= previous_open
    ):
        pattern = "Bearish Engulfing"
        bias = "Bearish reversal"
        meaning = "Sellers fully absorbed the prior bullish body, which can warn of a downside reversal."
        expectation = "The next candle should stay below the engulfing midpoint and ideally close below the latest low."
        confirmation = low
        invalidation = midpoint
    elif body_percent >= 80 and upper_percent <= 10 and lower_percent <= 10:
        pattern = "Bullish Marubozu" if direction == "bullish" else "Bearish Marubozu"
        bias = "Bullish continuation" if direction == "bullish" else "Bearish continuation"
        meaning = "The candle has a dominant real body with very small wicks, showing one-sided control."
        expectation = (
            "The next candle should continue upward or at least hold above the marubozu midpoint."
            if direction == "bullish"
            else "The next candle should continue downward or at least stay below the marubozu midpoint."
        )
        confirmation = high if direction == "bullish" else low
        invalidation = midpoint
    elif lower_wick >= body * 2 and upper_wick <= max(body * 0.7, candle_range * 0.08) and close_position >= 55:
        pattern = "Hammer"
        bias = "Bullish reversal watch"
        meaning = "Lower rejection shows buyers defended the low after intraday selling."
        expectation = "The next candle should close above the hammer high to confirm bullish reversal."
        confirmation = high
        invalidation = low
    elif upper_wick >= body * 2 and lower_wick <= max(body * 0.7, candle_range * 0.08) and close_position <= 45:
        pattern = "Shooting Star"
        bias = "Bearish reversal watch"
        meaning = "Upper rejection shows sellers pushed back after intraday buying."
        expectation = "The next candle should close below the shooting-star low to confirm bearish reversal."
        confirmation = low
        invalidation = high
    elif previous and high < previous.get("high", high) and low > previous.get("low", low):
        pattern = "Inside Bar"
        bias = "Compression"
        meaning = "The candle is fully inside the prior day's range, showing temporary compression."
        expectation = "The next candle should break the inside-bar high or low; avoid assuming direction before the break."
        confirmation = high
        invalidation = low
    elif body_percent >= 55 and close_position >= 70:
        pattern = "Strong Bullish Candle"
        bias = "Bullish continuation watch"
        meaning = "The candle closed near its high with a broad body, showing demand into the close."
        expectation = "The next candle should hold above the latest midpoint and attempt a high breakout."
        confirmation = high
        invalidation = midpoint
    elif body_percent >= 55 and close_position <= 30:
        pattern = "Strong Bearish Candle"
        bias = "Bearish continuation watch"
        meaning = "The candle closed near its low with a broad body, showing supply into the close."
        expectation = "The next candle should stay below the latest midpoint and attempt a low breakdown."
        confirmation = low
        invalidation = midpoint

    return {
        "available": True,
        "date": current.get("date"),
        "timeframe": "Daily",
        "pattern": pattern,
        "direction": direction.title(),
        "bias": bias,
        "summary": f"{pattern} on the latest daily candle: {meaning}",
        "meaning": meaning,
        "nextCandleExpectation": expectation,
        "confirmationLevel": round_or_none(confirmation),
        "invalidationLevel": round_or_none(invalidation),
        "open": round_or_none(open_price),
        "high": round_or_none(high),
        "low": round_or_none(low),
        "close": round_or_none(close),
        "midpoint": round_or_none(midpoint),
        "bodyPercent": round_or_none(body_percent),
        "upperWickPercent": round_or_none(upper_percent),
        "lowerWickPercent": round_or_none(lower_percent),
        "closePositionPercent": round_or_none(close_position),
        "changePercent": round_or_none(safe_divide(close - open_price, open_price) * 100),
    }


def score_technical(data):
    score = 50
    signals = []
    current = data["current"]

    if is_finite(data.get("sma20")) and current > data["sma20"]:
        score += 7
        signals.append("Price is above the 20-day average, showing short-term strength.")
    else:
        score -= 7
        signals.append("Price is below the 20-day average, showing short-term weakness.")

    if is_finite(data.get("sma50")) and current > data["sma50"]:
        score += 9
        signals.append("Price is above the 50-day average, which supports the medium-term trend.")
    else:
        score -= 9
        signals.append("Price is below the 50-day average, so trend confirmation is weaker.")

    if is_finite(data.get("sma200")) and current > data["sma200"]:
        score += 12
        signals.append("Price is above the 200-day average, which favors the long-term trend.")
    elif is_finite(data.get("sma200")):
        score -= 12
        signals.append("Price is below the 200-day average, which keeps long-term risk elevated.")

    if is_finite(data.get("sma50")) and is_finite(data.get("sma200")) and data["sma50"] > data["sma200"]:
        score += 8
        signals.append("The 50-day average is above the 200-day average.")
    elif is_finite(data.get("sma50")) and is_finite(data.get("sma200")):
        score -= 8
        signals.append("The 50-day average is below the 200-day average.")

    if is_finite(data.get("rsi14")) and 45 <= data["rsi14"] <= 65:
        score += 7
        signals.append("RSI is in a constructive range, not yet stretched.")
    elif is_finite(data.get("rsi14")) and data["rsi14"] > 70:
        score -= 8
        signals.append("RSI is above 70, so the stock may be overextended.")
    elif is_finite(data.get("rsi14")) and data["rsi14"] < 35:
        score -= 6
        signals.append("RSI is weak and below 35.")

    if is_finite(data.get("macdLine")) and is_finite(data.get("macdSignal")) and is_finite(data.get("macdHist")) and data["macdLine"] > data["macdSignal"] and data["macdHist"] > 0:
        score += 9
        signals.append("MACD is above its signal line.")
    else:
        score -= 6
        signals.append("MACD is not confirming upside momentum.")

    if data.get("volumeRatio") and data["volumeRatio"] > 1.4 and is_finite(data.get("sma20")) and current > data["sma20"]:
        score += 5
        signals.append("Recent volume is above its 20-day average on strength.")

    if is_finite(data.get("atrPercent")) and data["atrPercent"] > 6:
        score -= 5
        signals.append("ATR is high, so position sizing and stops need extra care.")

    distance_from_high = safe_divide(data["yearHigh"] - current, data["yearHigh"]) * 100
    distance_from_low = safe_divide(current - data["yearLow"], data["yearLow"]) * 100
    if distance_from_high < 10:
        score += 4
        signals.append("Price is trading near its one-year high.")
    if distance_from_low < 12:
        score -= 4
        signals.append("Price is close to its one-year low.")

    score = clamp(round(score), 0, 100)
    return {
        "score": score,
        "signals": signals,
        "summary": "Bullish technical structure" if score >= 70 else "Mixed technical structure" if score >= 45 else "Weak technical structure",
    }


def score_fundamental(metrics):
    score = 50
    signals = []

    if is_finite(metrics.get("revenueGrowth")) and metrics["revenueGrowth"] > 0.08:
        score += 10
        signals.append("Revenue growth is positive and above 8%.")
    elif is_finite(metrics.get("revenueGrowth")) and metrics["revenueGrowth"] < 0:
        score -= 10
        signals.append("Revenue growth is negative.")

    if is_finite(metrics.get("earningsGrowth")) and metrics["earningsGrowth"] > 0.08:
        score += 10
        signals.append("Earnings growth is positive and above 8%.")
    elif is_finite(metrics.get("earningsGrowth")) and metrics["earningsGrowth"] < 0:
        score -= 10
        signals.append("Earnings growth is negative.")

    if is_finite(metrics.get("profitMargins")) and metrics["profitMargins"] > 0.12:
        score += 8
        signals.append("Profit margin is healthy.")
    elif is_finite(metrics.get("profitMargins")) and metrics["profitMargins"] < 0.03:
        score -= 8
        signals.append("Profit margin is thin.")

    if is_finite(metrics.get("returnOnEquity")) and metrics["returnOnEquity"] > 0.15:
        score += 8
        signals.append("Return on equity is strong.")
    elif is_finite(metrics.get("returnOnEquity")) and metrics["returnOnEquity"] < 0.05:
        score -= 6
        signals.append("Return on equity is weak.")

    if is_finite(metrics.get("debtToEquity")) and metrics["debtToEquity"] < 80:
        score += 6
        signals.append("Debt-to-equity is manageable.")
    elif is_finite(metrics.get("debtToEquity")) and metrics["debtToEquity"] > 180:
        score -= 10
        signals.append("Debt-to-equity is high.")

    if is_finite(metrics.get("currentRatio")) and metrics["currentRatio"] >= 1.2:
        score += 4
        signals.append("Current ratio suggests acceptable liquidity.")
    elif is_finite(metrics.get("currentRatio")) and metrics["currentRatio"] < 1:
        score -= 5
        signals.append("Current ratio is below 1.")

    if is_finite(metrics.get("trailingPE")) and 0 < metrics["trailingPE"] < 30:
        score += 5
        signals.append("Trailing P/E is below 30.")
    elif is_finite(metrics.get("trailingPE")) and metrics["trailingPE"] > 60:
        score -= 7
        signals.append("Trailing P/E is elevated.")

    if is_finite(metrics.get("recommendationMean")) and metrics["recommendationMean"] <= 2.5:
        score += 4
        signals.append("Analyst recommendation mean is constructive.")
    elif is_finite(metrics.get("recommendationMean")) and metrics["recommendationMean"] >= 4:
        score -= 4
        signals.append("Analyst recommendation mean is weak.")

    if not signals:
        signals.append("Limited fundamental data was available for this symbol.")

    score = clamp(round(score), 0, 100)
    return {
        "score": score,
        "signals": signals,
        "summary": "Strong fundamental profile" if score >= 70 else "Mixed fundamental profile" if score >= 45 else "Weak fundamental profile",
    }


def score_event_risk(events):
    now = datetime.now(timezone.utc).date()
    future_events = []
    for event in events:
        if not event.get("date"):
            continue
        try:
            event_date = datetime.fromisoformat(event["date"]).date()
        except ValueError:
            continue
        days_away = (event_date - now).days
        if days_away >= 0:
            future_events.append({**event, "daysAway": days_away})

    next_results_event = next((event for event in future_events if event.get("type") in ["Earnings", "Board meeting"]), None)
    score = 25
    label = "Normal"
    summary = "No near-term results event was found in the available data."

    if next_results_event and next_results_event["daysAway"] <= 7:
        score = 85
        label = "High"
        summary = "A results-related event appears to be within one week, so gap risk is high."
    elif next_results_event and next_results_event["daysAway"] <= 21:
        score = 65
        label = "Elevated"
        summary = "A results-related event appears to be within three weeks, so volatility may rise."
    elif next_results_event and next_results_event["daysAway"] <= 45:
        score = 45
        label = "Watch"
        summary = "A results-related event appears to be within 45 days."

    return {"score": score, "label": label, "summary": summary}


def build_research_levels(data):
    current = data["current"]
    atr_value = data.get("atrValue") or current * 0.025
    support = data.get("support") or current - atr_value * 2
    resistance = data.get("resistance") or current + atr_value * 2
    pullback_low = max(0.01, support)
    pullback_high = max(pullback_low, min(current, support + atr_value * 0.55))
    breakout_trigger = resistance + atr_value * 0.25
    stop = max(0.01, support - atr_value)
    risk = max(pullback_high - stop, atr_value * 0.5)
    target_one = max(resistance, pullback_high + risk * 1.8)
    target_two = max(data["yearHigh"], pullback_high + risk * 2.6)
    mode = "Trend-following" if data["technicalScore"] >= 65 else "Confirmation needed" if data["technicalScore"] >= 45 else "Waitlist"

    return {
        "mode": mode,
        "note": "These are research zones from support, resistance, and ATR. They are not instructions to buy or sell.",
        "pullbackEntry": {"low": round2(pullback_low), "high": round2(pullback_high)},
        "breakoutTrigger": round2(breakout_trigger),
        "invalidation": round2(stop),
        "targets": [round2(target_one), round2(target_two)],
        "support": round2(support),
        "resistance": round2(resistance),
        "riskReward": round2(safe_divide(target_one - pullback_high, pullback_high - stop)),
    }


def build_swing_trade_plan(data):
    current = data["current"]
    atr_value = data.get("atrValue") if is_finite(data.get("atrValue")) else current * 0.025
    atr_value = max(0.01, atr_value, current * 0.008)
    technical_score = data["technical"]["score"]
    fundamental_score = data["fundamental"]["score"]
    event_risk_score = data["eventRisk"]["score"]
    event_safety_score = 100 - event_risk_score
    volume_ratio = data.get("volumeRatio") if is_finite(data.get("volumeRatio")) else None
    avg_volume_20 = data.get("avgVolume20")
    rsi14 = data.get("rsi14")
    research_levels = data["researchLevels"]
    support_resistance = data["supportResistance"]
    support_zones = support_resistance.get("supportZones") or []
    resistance_zones = support_resistance.get("resistanceZones") or []
    first_support = array_get(support_zones, 0) or {}
    second_support = array_get(support_zones, 1) or {}
    first_resistance = array_get(resistance_zones, 0) or {}
    second_resistance = array_get(resistance_zones, 1) or {}

    support = finite_or(first_support.get("price"), research_levels.get("support"), current - atr_value * 2)
    if support >= current:
        support = current - atr_value * 1.2
    second_support_price = finite_or(second_support.get("price"), support - atr_value * 2)
    resistance = finite_or(first_resistance.get("price"), research_levels.get("resistance"), current + atr_value * 2)
    if resistance <= current:
        resistance = current + atr_value * 1.4
    second_resistance_price = finite_or(second_resistance.get("price"), research_levels.get("targets", [None, None])[1], resistance + atr_value * 2)
    if second_resistance_price <= resistance:
        second_resistance_price = resistance + atr_value * 1.4

    short_entry_low = max(0.01, max(support, current - atr_value * 0.85))
    short_entry_high = max(short_entry_low, min(current, current - atr_value * 0.2))
    short_stop = max(0.01, short_entry_low - atr_value * 0.75)
    short_trigger = max(short_entry_high, min(resistance, current + atr_value * 0.65))
    short_target_one = max(resistance, current + atr_value * 1.15)
    short_target_two = max(short_target_one + atr_value * 0.7, current + atr_value * 2.1)

    mid_anchor = finite_or(data.get("sma50"), support, current - atr_value * 1.6)
    if mid_anchor >= current:
        mid_anchor = finite_or(support, current - atr_value * 1.4)
    mid_entry_low = max(0.01, min(mid_anchor - atr_value * 0.45, current - atr_value * 1.45))
    mid_entry_high = max(mid_entry_low, min(current, max(mid_anchor + atr_value * 0.45, current - atr_value * 0.35)))
    mid_stop = max(0.01, min(mid_entry_low, support, second_support_price) - atr_value * 1.05)
    mid_trigger = max(mid_entry_high, min(second_resistance_price, resistance + atr_value * 0.35))
    mid_target_one = max(second_resistance_price, resistance + atr_value * 0.8, current + atr_value * 2.4)
    mid_target_two = max(data["yearHigh"], mid_target_one + atr_value * 1.4, current + atr_value * 4.0)

    long_anchor = finite_or(data.get("sma200"), data.get("sma50"), support, current - atr_value * 2.5)
    if long_anchor >= current:
        long_anchor = finite_or(data.get("sma50"), support, current - atr_value * 2.0)
    long_entry_low = max(0.01, min(long_anchor - atr_value * 0.75, current - atr_value * 2.4))
    long_entry_high = max(long_entry_low, min(current, max(long_anchor + atr_value * 0.75, current - atr_value * 0.8)))
    long_stop = max(0.01, min(long_entry_low, long_anchor, second_support_price) - atr_value * 1.6)
    long_trigger = max(long_entry_high, research_levels.get("breakoutTrigger") or resistance)
    long_target_one = max(data["yearHigh"], current + atr_value * 5.0)
    long_target_two = max(long_target_one + atr_value * 3.0, current * 1.18, current + atr_value * 7.5)

    volume_text = (
        f"volume above {round(avg_volume_20 * 1.2):,}"
        if is_finite(avg_volume_20) and avg_volume_20 > 0
        else "above-average volume"
    )
    event_condition = (
        "Near-term event risk is high; wait for the event reaction before using the plan."
        if event_risk_score >= 65
        else "Recheck the plan after fresh results, large news, or a broad-market gap."
    )

    short_score = swing_score([
        (technical_score, 0.62),
        (event_safety_score, 0.24),
        (rsi_score(rsi14), 0.08),
        (volume_score(volume_ratio), 0.06),
    ])
    mid_score = swing_score([
        (technical_score, 0.44),
        (fundamental_score, 0.24),
        (event_safety_score, 0.16),
        (trend_score(data.get("performance", {}).get("threeMonth")), 0.10),
        (volume_score(volume_ratio), 0.06),
    ])
    long_score = swing_score([
        (fundamental_score, 0.38),
        (technical_score, 0.28),
        (event_safety_score, 0.12),
        (trend_score(data.get("performance", {}).get("sixMonth")), 0.14),
        (long_trend_score(current, data.get("sma200")), 0.08),
    ])

    plans = [
        build_swing_horizon(
            "Short term",
            "1 week",
            short_score,
            "Momentum swing",
            short_entry_low,
            short_entry_high,
            short_trigger,
            short_stop,
            short_target_one,
            short_target_two,
            "Exit or tighten risk if there is no follow-through within 5 sessions.",
            [
                f"Use the entry zone only if price holds above support; breakout entries need a daily close above the trigger with {volume_text}.",
                "Take the first exit into nearby resistance, then trail the rest with a prior-day-low or ATR stop.",
                event_condition,
            ],
        ),
        build_swing_horizon(
            "Mid term",
            "1 quarter",
            mid_score,
            "Trend pullback or base breakout",
            mid_entry_low,
            mid_entry_high,
            mid_trigger,
            mid_stop,
            mid_target_one,
            mid_target_two,
            "Reassess after roughly 63 trading sessions or after the next results cycle.",
            [
                "Prefer a pullback that holds the 50-day area or a close above the trigger after consolidation.",
                "Scale out near the first target and keep the second target only while price holds above the 20-day or 50-day average.",
                event_condition,
            ],
        ),
        build_swing_horizon(
            "Long term",
            "6+ months",
            long_score,
            "Position swing",
            long_entry_low,
            long_entry_high,
            long_trigger,
            long_stop,
            long_target_one,
            long_target_two,
            "Review after 6 months, trend break, or a close below the 200-day area.",
            [
                "Build only on deeper pullbacks or after a major resistance breakout; avoid chasing extended moves.",
                "Book partial profits near the first objective and trail the balance with the 50-day or 200-day trend.",
                event_condition,
            ],
        ),
    ]

    best_plan = max(plans, key=lambda plan: plan["score"])
    suitability_score = round(clamp(best_plan["score"] - (8 if event_risk_score >= 65 else 0), 0, 100))
    suitability_label = (
        "High-quality swing setup"
        if suitability_score >= 75
        else "Good setup with confirmation"
        if suitability_score >= 62
        else "Watchlist, not clean yet"
        if suitability_score >= 45
        else "Avoid fresh swing entry"
    )

    return {
        "suitability": {
            "score": suitability_score,
            "label": suitability_label,
            "bestHorizon": best_plan["horizon"],
            "summary": (
                f"{suitability_label}. Best fit: {best_plan['horizon']} ({best_plan['timeframe']}). "
                "No swing setup is perfect; use these as research levels and confirm live price action."
            ),
        },
        "plans": plans,
        "note": "Plans are generated from support, resistance, ATR, trend, volume, fundamentals, and event risk. They are not personalized financial advice.",
    }


def build_swing_horizon(
    horizon,
    timeframe,
    score,
    setup,
    entry_low,
    entry_high,
    trigger,
    stop_loss,
    target_one,
    target_two,
    time_exit,
    conditions,
):
    entry_high = max(entry_high, entry_low)
    stop_loss = max(0.01, min(stop_loss, entry_low * 0.995))
    target_one = max(target_one, entry_high * 1.01)
    target_two = max(target_two, target_one * 1.01)
    return {
        "horizon": horizon,
        "timeframe": timeframe,
        "score": round(clamp(score, 0, 100)),
        "setup": setup,
        "entry": {
            "low": round2(entry_low),
            "high": round2(entry_high),
            "trigger": round2(trigger),
        },
        "stopLoss": round2(stop_loss),
        "targets": [
            {"label": "Target 1", "price": round2(target_one)},
            {"label": "Target 2", "price": round2(target_two)},
        ],
        "riskReward": round2(safe_divide(target_one - entry_high, entry_high - stop_loss)),
        "exitPlan": {
            "partial": "Book partial profits at Target 1.",
            "final": "Exit the balance at Target 2 or on a close back below the active trailing average.",
            "time": time_exit,
        },
        "conditions": conditions,
    }


def swing_score(items):
    total_weight = sum(weight for _, weight in items)
    if not total_weight:
        return 0
    return round(clamp(sum(value * weight for value, weight in items) / total_weight, 0, 100))


def volume_score(volume_ratio):
    if not is_finite(volume_ratio):
        return 50
    if volume_ratio >= 2:
        return 82
    if volume_ratio >= 1.2:
        return 68
    if volume_ratio >= 0.8:
        return 52
    return 38


def rsi_score(value):
    if not is_finite(value):
        return 50
    if 45 <= value <= 65:
        return 78
    if 38 <= value < 45 or 65 < value <= 72:
        return 58
    if value > 78 or value < 32:
        return 30
    return 45


def trend_score(value):
    if not is_finite(value):
        return 50
    return round(clamp(50 + value * 2.2, 20, 85))


def long_trend_score(current, sma200_value):
    if not is_finite(sma200_value):
        return 50
    distance = safe_divide(current - sma200_value, sma200_value) * 100
    if distance >= 0:
        return round(clamp(62 + min(distance, 18), 62, 82))
    return round(clamp(48 + distance, 25, 48))


def finite_or(*values):
    for value in values:
        if is_finite(value):
            return value
    return None


def build_outlook(overall_score, technical_score, fundamental_score, event_risk):
    label = "Neutral"
    summary = "The stock has mixed evidence. Wait for price confirmation near the listed levels."
    if overall_score >= 72 and technical_score >= 65 and fundamental_score >= 55:
        label = "Constructive"
        summary = "The setup is constructive if price holds above support and volume confirms strength."
    elif overall_score <= 42 or technical_score < 40:
        label = "Cautious"
        summary = "Risk is elevated. The cleaner setup is to wait for a close back above key averages."

    if event_risk["score"] >= 65:
        summary += " Upcoming event risk can override the chart, so avoid treating levels as fixed."
    return {"label": label, "summary": summary}


def market_data_freshness(candles, quote_data, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    latest_candle_date = parse_candle_date((candles or [{}])[-1].get("date") if candles else None)
    quote_time = parse_provider_datetime(
        (quote_data or {}).get("regularMarketTime")
        or (quote_data or {}).get("marketTime")
    )
    chart_age_days = (now.date() - latest_candle_date).days if latest_candle_date else None
    quote_age_minutes = (
        round((now - quote_time).total_seconds() / 60)
        if quote_time and quote_time <= now
        else None
    )
    return {
        "latestCandleDate": latest_candle_date.isoformat() if latest_candle_date else "",
        "chartAgeDays": chart_age_days,
        "quoteTime": iso_from_datetime(quote_time) if quote_time else None,
        "quoteAgeMinutes": quote_age_minutes,
    }


def parse_candle_date(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.title(), fmt).date()
        except ValueError:
            continue
    return None


def parse_provider_datetime(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_quality_report(data):
    metric_names = [
        "marketCap",
        "trailingPE",
        "priceToBook",
        "profitMargins",
        "returnOnEquity",
        "returnOnCapitalEmployed",
        "revenueGrowth",
        "earningsGrowth",
        "debtToEquity",
        "dividendYield",
    ]
    available_metrics = len([name for name in metric_names if is_finite(data["fundamentals"].get(name))])
    chart_points = len(data["candles"])
    level_count = len(data["supportResistance"].get("supportZones") or []) + len(data["supportResistance"].get("resistanceZones") or [])
    symbol = data.get("symbol") or ""
    is_indian_symbol = symbol.endswith(".NS") or symbol.endswith(".BO")
    ownership = data.get("ownership") or {}
    relative_strength = data.get("relativeStrength") or {}
    freshness = market_data_freshness(data["candles"], data.get("quote") or {}, data.get("now"))
    warnings = []
    strengths = []
    score = 55

    if chart_points >= 200:
        score += 15
        strengths.append(f"Price chart has {chart_points} daily candles in the one-year display window.")
    else:
        score -= 18
        warnings.append(f"Only {chart_points} chart candles were available; analysis is based on the available history.")

    if level_count >= 3:
        score += 8
        strengths.append(f"{level_count} support/resistance zones were detected.")
    else:
        score -= 8
        warnings.append("Few support/resistance zones were detected.")

    if available_metrics >= 7:
        score += 16
        strengths.append(f"{available_metrics} key fundamental metrics were available.")
    elif available_metrics >= 4:
        score += 5
        warnings.append("Fundamental coverage is partial.")
    else:
        score -= 18
        warnings.append("Fundamental coverage is weak for this symbol.")

    if data["fundamentals"].get("dataSource") and "Screener.in" in data["fundamentals"]["dataSource"]:
        score -= 4
        warnings.append("Indian fundamentals use Screener.in summary parsing, so the fields should be cross-checked.")

    if has_shareholding_categories(ownership, ("Promoters", "FIIs", "DIIs")):
        score += 8
        strengths.append("Promoter, FII, and DII holdings were available for the latest ownership view.")
    elif is_indian_symbol:
        score -= 10
        warnings.append("Promoter/FII/DII holding coverage is incomplete for this symbol.")

    if ownership.get("source"):
        strengths.append(f"Ownership source: {ownership['source']}.")

    if relative_strength.get("available"):
        score += 7
        strengths.append(f"Relative strength versus {relative_strength.get('benchmarkName') or 'benchmark'} was calculated.")
    elif relative_strength.get("expected"):
        score -= 6
        warnings.append("Benchmark-relative strength could not be calculated from the available data.")

    if freshness["chartAgeDays"] is None:
        score -= 6
        warnings.append("Latest chart candle date was not available from the provider.")
    elif freshness["chartAgeDays"] <= 4:
        score += 5
        strengths.append(f"Latest chart candle is recent: {freshness['latestCandleDate']}.")
    elif freshness["chartAgeDays"] <= 10:
        score -= 4
        warnings.append(f"Latest chart candle is {freshness['chartAgeDays']} days old; check a live chart before acting.")
    else:
        score -= 12
        warnings.append(f"Latest chart candle appears stale ({freshness['latestCandleDate']}).")

    if freshness["quoteAgeMinutes"] is None:
        score -= 4
        warnings.append("Provider did not return an official market timestamp for the quote.")
    elif freshness["quoteAgeMinutes"] <= 90:
        score += 4
        strengths.append("Quote timestamp is recent.")
    elif freshness["quoteAgeMinutes"] > 3 * 24 * 60:
        score -= 8
        warnings.append("Quote timestamp is more than three days old.")
    else:
        warnings.append("Quote timestamp is older than a normal intraday quote; verify live price.")

    if data["eventRisk"]["score"] >= 65:
        score -= 12
        warnings.append("Near-term results/event risk can override technical levels.")

    if abs(data["technical"]["score"] - data["fundamental"]["score"]) >= 35:
        score -= 10
        warnings.append("Technical and fundamental scores strongly disagree.")

    if is_finite(data["fundamentals"].get("targetMeanPrice")):
        strengths.append("Analyst target data was available.")
    else:
        warnings.append("Analyst target data was not available.")

    if is_indian_symbol:
        warnings.append("Use the NSE/BSE filing links to verify shareholding, board changes, and order announcements before trading.")

    score = clamp(round(score), 0, 100)
    return {
        "score": score,
        "label": "High" if score >= 75 else "Moderate" if score >= 55 else "Low",
        "summary": (
            "Enough data is available for a useful research view."
            if score >= 75
            else "Use this as a research starting point and cross-check important fields."
            if score >= 55
            else "The report has meaningful data gaps and needs manual verification."
        ),
        "chartPoints": chart_points,
        "availableFundamentalMetrics": available_metrics,
        "freshness": freshness,
        "dataSources": data["source"],
        "checks": build_accuracy_checks(data, ownership, relative_strength, freshness),
        "strengths": strengths,
        "warnings": warnings,
    }


def build_accuracy_checks(data, ownership, relative_strength, freshness=None):
    symbol = data.get("symbol") or ""
    is_indian_symbol = symbol.endswith(".NS") or symbol.endswith(".BO")
    freshness = freshness or market_data_freshness(data["candles"], data.get("quote") or {}, data.get("now"))
    checks = []

    checks.append({
        "label": "Price and chart history",
        "status": "Available" if len(data["candles"]) >= 200 else "Partial",
        "tone": "positive" if len(data["candles"]) >= 200 else "warning",
        "detail": f"{len(data['candles'])} daily candles loaded; chart display is capped at one year when more data exists.",
    })

    chart_age = freshness.get("chartAgeDays")
    quote_age = freshness.get("quoteAgeMinutes")
    checks.append({
        "label": "Data freshness",
        "status": "Fresh" if chart_age is not None and chart_age <= 4 and quote_age is not None and quote_age <= 90 else "Verify",
        "tone": "positive" if chart_age is not None and chart_age <= 4 else "warning",
        "detail": (
            f"Latest candle {freshness.get('latestCandleDate') or 'n/a'}; "
            f"quote age {quote_age} minutes." if quote_age is not None
            else f"Latest candle {freshness.get('latestCandleDate') or 'n/a'}; quote timestamp unavailable."
        ),
    })

    checks.append({
        "label": "Promoter/FII/DII holdings",
        "status": "Available" if has_shareholding_categories(ownership, ("Promoters", "FIIs", "DIIs")) else "Needs verification",
        "tone": "positive" if has_shareholding_categories(ownership, ("Promoters", "FIIs", "DIIs")) else "warning",
        "detail": ownership.get("source") or "Latest ownership rows were not available from public sources.",
    })

    checks.append({
        "label": "Relative strength",
        "status": relative_strength.get("label") or "Unavailable",
        "tone": "positive" if relative_strength.get("available") and (relative_strength.get("averageSpread") or 0) >= 0 else "warning" if relative_strength.get("expected") else "neutral",
        "detail": relative_strength.get("summary") or "Benchmark comparison was not available.",
    })

    if is_indian_symbol:
        checks.append({
            "label": "Official exchange filings",
            "status": "Manual cross-check",
            "tone": "neutral",
            "detail": "NSE/BSE links are provided to verify shareholding pattern, announcements, results, and board changes.",
        })

    checks.append({
        "label": "Fundamental coverage",
        "status": "Strong" if data["fundamentals"].get("dataSource") and data["fundamental"]["score"] >= 60 else "Review",
        "tone": "positive" if data["fundamental"]["score"] >= 60 else "warning",
        "detail": f"{data['fundamental']['score']}/100 fundamental score with {data['source'] or 'available public'} sources.",
    })

    return checks


def build_scenarios(data):
    first_resistance = array_get(data["supportResistance"].get("resistanceZones"), 0)
    first_support = array_get(data["supportResistance"].get("supportZones"), 0)
    second_support = array_get(data["supportResistance"].get("supportZones"), 1)
    breakout = data["researchLevels"]["breakoutTrigger"]
    invalidation = data["researchLevels"]["invalidation"]
    volume_needed = round(data["avgVolume20"] * 1.2) if data.get("avgVolume20") else None
    event_warning = (
        "Results/event risk is high, so wait for the event reaction before trusting levels."
        if data["eventRisk"]["score"] >= 65
        else "No high near-term event risk was detected."
    )

    return {
        "stance": "Trend continuation" if data["technical"]["score"] >= 65 and data["eventRisk"]["score"] < 65 else "Confirmation needed" if data["technical"]["score"] >= 45 else "Wait for repair",
        "bull": {
            "title": "Bull case",
            "trigger": breakout,
            "confirmation": f"Daily close above breakout with volume above {volume_needed:,}." if volume_needed else "Daily close above breakout with above-average volume.",
            "expectedMove": data["researchLevels"]["targets"],
            "reason": f"Price must reclaim resistance near {round2(first_resistance['price'])}." if first_resistance else "Price must clear the nearest resistance zone.",
        },
        "base": {
            "title": "Base case",
            "rangeLow": first_support["price"] if first_support else data["researchLevels"]["support"],
            "rangeHigh": first_resistance["price"] if first_resistance else data["researchLevels"]["resistance"],
            "action": "Treat the stock as range-bound until it closes outside the nearest zone.",
            "reason": event_warning,
        },
        "bear": {
            "title": "Bear case",
            "trigger": invalidation,
            "nextSupport": second_support["price"] if second_support else None,
            "action": "A close below invalidation weakens the setup and calls for reassessment.",
            "reason": f"Nearest support is near {round2(first_support['price'])}." if first_support else "Nearest support was not strong enough to rank.",
        },
        "safeguards": [
            "Do not use intraday spikes alone as confirmation.",
            "Re-check the setup after results, large news, or a broad-market gap.",
            "Position sizing and stop placement should be decided outside this app.",
        ],
    }


def build_references(symbol, long_name, quote_data, meta):
    tv_symbol = to_trading_view_symbol(symbol, quote_data, meta)
    base_symbol = re.sub(r"\.(NS|BO)$", "", symbol, flags=re.IGNORECASE)
    links = []

    if tv_symbol:
        links.append({
            "label": "TradingView chart",
            "url": f"https://www.tradingview.com/chart/?symbol={quote(tv_symbol)}",
            "note": "Cross-check live chart, indicators, and exchange symbol.",
        })
        links.append({
            "label": "TradingView technicals",
            "url": f"https://www.tradingview.com/symbols/{tv_symbol.replace(':', '-')}/technicals/",
            "note": "Compare independent technical ratings.",
        })

    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        links.append({
            "label": "Screener company page",
            "url": f"https://www.screener.in/company/{quote(base_symbol)}/",
            "note": "Cross-check Indian fundamentals and announcements.",
        })
        links.append({
            "label": "NSE shareholding pattern",
            "url": f"https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern?symbol={quote(base_symbol)}&tabIndex=equity",
            "note": "Official promoter/public shareholding filings; verify latest quarter and XBRL details.",
        })
        links.append({
            "label": "NSE announcements",
            "url": f"https://www.nseindia.com/companies-listing/corporate-filings-announcements?symbol={quote(base_symbol)}&tabIndex=equity",
            "note": "Official filings for orders, board changes, results, and investor updates.",
        })
        links.append({
            "label": "BSE shareholding pattern",
            "url": "https://www.bseindia.com/corporates/shpSecurities.aspx?lang=en-gb",
            "note": "Official BSE shareholding page; enter the company or scrip code if the page does not auto-load.",
        })
        links.append({
            "label": "BSE announcements",
            "url": "https://www.bseindia.com/corporates/ann.html",
            "note": "Official BSE announcements for orders, promoter changes, results, and disclosures.",
        })
        links.append({
            "label": "Moneycontrol search",
            "url": f"https://www.moneycontrol.com/search/?search_str={quote(long_name or base_symbol)}",
            "note": "Cross-check news, results, and market commentary manually.",
        })

    return {"tradingViewSymbol": tv_symbol, "links": links}


def to_trading_view_symbol(symbol, quote_data, meta):
    base = re.sub(r"\.(NS|BO)$", "", symbol, flags=re.IGNORECASE)
    if symbol.endswith(".NS"):
        return f"NSE:{base}"
    if symbol.endswith(".BO"):
        return f"BSE:{base}"

    exchange = quote_data.get("exchange") or meta.get("exchangeName") or ""
    exchange_map = {"NMS": "NASDAQ", "NAS": "NASDAQ", "NYQ": "NYSE", "ASE": "AMEX"}
    tv_exchange = exchange_map.get(exchange, exchange)
    return f"{tv_exchange}:{symbol}" if tv_exchange else symbol


def build_source_label(sec, screener):
    return " and ".join([value for value in ["Yahoo Finance public endpoints", sec.get("source"), screener.get("source")] if value])


def find_levels(candles, current, atr_value):
    lookback = candles[-252:]
    radius = 4
    effective_atr = atr_value or current * 0.025
    zone_width = max(current * 0.006, effective_atr * 0.65)
    cluster_threshold = max(current * 0.01, effective_atr * 0.9)
    candidates = []

    for index in range(radius, len(lookback) - radius):
        window = lookback[index - radius:index + radius + 1]
        candle = lookback[index]
        min_low = min(item["low"] for item in window)
        max_high = max(item["high"] for item in window)
        if candle["low"] <= min_low:
            candidates.append({"type": "support", "price": candle["low"], "date": candle["date"], "index": index, "source": "swing low"})
        if candle["high"] >= max_high:
            candidates.append({"type": "resistance", "price": candle["high"], "date": candle["date"], "index": index, "source": "swing high"})

    add_range_levels(candidates, lookback, [20, 50, 100, len(lookback)])
    support_zones = [
        level for level in rank_levels(candidates, lookback, current, zone_width, cluster_threshold, "support")
        if level["price"] < current
    ]
    resistance_zones = [
        level for level in rank_levels(candidates, lookback, current, zone_width, cluster_threshold, "resistance")
        if level["price"] > current
    ]

    if not support_zones:
        fallback = min(item["low"] for item in lookback[-60:])
        support_zones.append(describe_level([{"price": fallback, "source": "60-day low"}], "support", lookback, current, zone_width))
    if not resistance_zones:
        fallback = max(item["high"] for item in lookback[-60:])
        resistance_zones.append(describe_level([{"price": fallback, "source": "60-day high"}], "resistance", lookback, current, zone_width))

    support_zones = pick_relevant_levels(support_zones, "support")
    resistance_zones = pick_relevant_levels(resistance_zones, "resistance")
    return {
        "supports": [level["price"] for level in support_zones],
        "resistances": [level["price"] for level in resistance_zones],
        "supportZones": support_zones,
        "resistanceZones": resistance_zones,
    }


def add_range_levels(candidates, candles, periods):
    for period in periods:
        slice_ = candles[-period:]
        if not slice_:
            continue
        low_candle = min(slice_, key=lambda candle: candle["low"])
        high_candle = max(slice_, key=lambda candle: candle["high"])
        candidates.append({"type": "support", "price": low_candle["low"], "date": low_candle["date"], "index": candles.index(low_candle), "source": f"{period}-session low"})
        candidates.append({"type": "resistance", "price": high_candle["high"], "date": high_candle["date"], "index": candles.index(high_candle), "source": f"{period}-session high"})


def rank_levels(candidates, candles, current, zone_width, cluster_threshold, type_):
    clusters = cluster_level_candidates([candidate for candidate in candidates if candidate["type"] == type_], cluster_threshold)
    levels = [describe_level(cluster, type_, candles, current, zone_width) for cluster in clusters]
    return sorted(levels, key=lambda item: item["rank"], reverse=True)


def cluster_level_candidates(candidates, threshold):
    sorted_candidates = sorted(
        [candidate for candidate in candidates if is_finite(candidate.get("price"))],
        key=lambda item: item["price"],
    )
    clusters = []
    for candidate in sorted_candidates:
        current = clusters[-1] if clusters else None
        if not current or abs(current["average"] - candidate["price"]) > threshold:
            clusters.append({"values": [candidate], "average": candidate["price"]})
        else:
            current["values"].append(candidate)
            current["average"] = sum(item["price"] for item in current["values"]) / len(current["values"])
    return [cluster["values"] for cluster in clusters]


def describe_level(cluster, type_, candles, current, zone_width):
    price = sum(item["price"] for item in cluster) / len(cluster)
    touches = [candle for candle in candles if touches_level(candle, price, zone_width, type_)]
    last_touch_index = candles.index(touches[-1]) if touches else 0
    recent_touches = len([candle for candle in touches if candles.index(candle) >= len(candles) - 60])
    average_volume = average([candle.get("volume") or 0 for candle in candles])
    touch_volume = average([candle.get("volume") or 0 for candle in touches])
    volume_ratio = touch_volume / average_volume if average_volume else 1
    rejection = average([rejection_score(candle, type_) for candle in touches])
    recency = last_touch_index / max(1, len(candles) - 1) if candles else 0
    sources = list(dict.fromkeys([item.get("source") for item in cluster if item.get("source")]))
    distance_percent = safe_divide(price - current, current) * 100
    strength = clamp(round(
        len(touches) * 6
        + recent_touches * 5
        + rejection * 18
        + min(volume_ratio, 2) * 7
        + len(sources) * 5
        + recency * 14
    ), 20, 95)
    proximity_penalty = abs(distance_percent) * 0.7

    return {
        "price": round2(price),
        "zoneLow": round2(max(0.01, price - zone_width)),
        "zoneHigh": round2(price + zone_width),
        "strength": strength,
        "label": "Major" if strength >= 78 else "Strong" if strength >= 62 else "Valid" if strength >= 45 else "Minor",
        "touches": len(touches),
        "lastTouched": touches[-1]["date"] if touches else cluster[-1].get("date"),
        "distancePercent": round2(distance_percent),
        "sources": sources[:3],
        "rank": strength - proximity_penalty,
    }


def touches_level(candle, price, zone_width, type_):
    if type_ == "support":
        return candle["low"] <= price + zone_width and candle["close"] >= price - zone_width
    return candle["high"] >= price - zone_width and candle["close"] <= price + zone_width


def rejection_score(candle, type_):
    range_ = candle["high"] - candle["low"]
    if not range_:
        return 0
    return (candle["close"] - candle["low"]) / range_ if type_ == "support" else (candle["high"] - candle["close"]) / range_


def pick_relevant_levels(levels, type_):
    selected = sorted(levels, key=lambda item: item["rank"], reverse=True)[:4]
    selected = sorted(selected, key=lambda item: item["price"], reverse=(type_ == "support"))
    return [{key: value for key, value in level.items() if key != "rank"} for level in selected]


def average(values):
    usable = [value for value in values if is_finite(value)]
    return sum(usable) / len(usable) if usable else 0


def sma(values, period):
    output = [None] * len(values)
    total = 0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        if index >= period - 1:
            output[index] = total / period
    return output


def ema(values, period):
    output = [None] * len(values)
    multiplier = 2 / (period + 1)
    previous = None
    for index, value in enumerate(values):
        if index == period - 1:
            previous = sum(values[:period]) / period
            output[index] = previous
        elif index >= period:
            previous = value * multiplier + previous * (1 - multiplier)
            output[index] = previous
    return output


def rsi(values, period):
    output = [None] * len(values)
    if len(values) <= period:
        return output
    gain = 0
    loss = 0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gain += max(change, 0)
        loss += max(-change, 0)

    average_gain = gain / period
    average_loss = loss / period
    output[period] = 100 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0)) / period
        output[index] = 100 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    return output


def macd(values):
    fast = ema(values, 12)
    slow = ema(values, 26)
    line = [
        value - slow[index] if value is not None and slow[index] is not None else None
        for index, value in enumerate(fast)
    ]
    compact_line = [value for value in line if value is not None]
    compact_signal = ema(compact_line, 9)
    signal = [None] * len(values)
    signal_index = 0
    for index, value in enumerate(line):
        if value is not None:
            signal[index] = compact_signal[signal_index]
            signal_index += 1
    return {
        "line": line,
        "signal": signal,
        "histogram": [
            value - signal[index] if value is not None and signal[index] is not None else None
            for index, value in enumerate(line)
        ],
    }


def bollinger(values, period, multiplier):
    middle = sma(values, period)
    upper = [None] * len(values)
    lower = [None] * len(values)
    for index in range(period - 1, len(values)):
        slice_ = values[index - period + 1:index + 1]
        mean = middle[index]
        variance = sum((value - mean) ** 2 for value in slice_) / period
        deviation = math.sqrt(variance)
        upper[index] = mean + deviation * multiplier
        lower[index] = mean - deviation * multiplier
    return {"upper": upper, "middle": middle, "lower": lower}


def atr(candles, period):
    true_ranges = []
    for index, candle in enumerate(candles):
        if index == 0:
            true_ranges.append(candle["high"] - candle["low"])
            continue
        previous_close = candles[index - 1]["close"]
        true_ranges.append(max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        ))
    return sma(true_ranges, period)


def period_return(values, sessions):
    if not sessions or len(values) <= sessions:
        return None
    start = values[-1 - sessions]
    end = values[-1]
    return safe_divide(end - start, start) * 100


def daily_return_series(values):
    returns = []
    for index in range(1, len(values)):
        previous = values[index - 1]
        current = values[index]
        if is_finite(previous) and previous and is_finite(current):
            returns.append((current - previous) / previous)
    return returns


def annualized_volatility(values):
    returns = daily_return_series(values)
    if len(returns) < 20:
        return None
    average = sum(returns) / len(returns)
    variance = sum((item - average) ** 2 for item in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252) * 100


def max_drawdown_percent(values):
    peak = None
    max_drawdown = 0
    for value in values:
        if not is_finite(value):
            continue
        peak = value if peak is None else max(peak, value)
        if peak:
            drawdown = (value - peak) / peak * 100
            max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def expense_ratio_percent(value):
    if not is_finite(value):
        return None
    return value * 100 if value <= 1 else value


def humanize_sector_key(value):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    text = text.replace("_", " ").replace("-", " ").strip()
    return text.title() if text else "Other"


def format_currency_value(value, currency):
    if not is_finite(value):
        return "n/a"
    return f"{currency + ' ' if currency else ''}{round2(value)}"


def fetch_json(endpoint, sec=False):
    text = fetch_text(endpoint, json_request=True, sec=sec)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Data provider did not return JSON: {text[:80]}") from error


def fetch_nse_json(path):
    endpoint = path if str(path).startswith("http") else f"{NSE_BASE_URL}{path}"
    request = Request(endpoint, headers=NSE_HEADERS)
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
                text = response.read().decode("utf-8", errors="replace")
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"NSE India did not return JSON: {text[:80]}") from error
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"NSE India returned {error.code}.") from error
            last_error = error
        except (TimeoutError, URLError, OSError) as error:
            last_error = error
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError("NSE India is temporarily unavailable. Please retry in a moment.") from last_error


def nse_session(force_new=False):
    """Cookie-bearing opener for NSE, reused across calls.

    NSE rejects requests without the cookies its homepage sets, so every call
    used to build a fresh jar and fetch the homepage first - doubling the round
    trips. A single stock's option chain needs one contract-info call plus one
    per expiry, which meant ten round trips where five would do. The jar is kept
    until it ages out or the server rejects it.
    """
    global _nse_session_state
    with _nse_session_lock:
        state = _nse_session_state
        fresh = (
            state
            and not force_new
            and time.time() - state["createdAt"] < NSE_SESSION_TTL_SECONDS
        )
        if fresh:
            return state["opener"]

        cookie_jar = CookieJar()
        opener = build_opener(HTTPSHandler(context=SSL_CONTEXT), HTTPCookieProcessor(cookie_jar))
        with opener.open(Request(NSE_BASE_URL, headers=NSE_HEADERS), timeout=20) as response:
            response.read()
        _nse_session_state = {"opener": opener, "createdAt": time.time()}
        return opener


def fetch_nse_json_with_session(path, timeout=20, attempts=3):
    """NSE JSON over the shared cookie jar.

    ``timeout`` and ``attempts`` are exposed because NSE's latency is erratic -
    the same endpoint answers in 0.2s or stalls for 20 - and the defaults give a
    worst case near a minute per call. A caller fetching data that merely
    enriches a response, rather than data the response cannot be built without,
    should shorten the budget so one slow endpoint cannot hold up the page.
    """
    endpoint = path if str(path).startswith("http") else f"{NSE_BASE_URL}{path}"
    attempts = max(1, attempts)
    last_error = None
    for attempt in range(attempts):
        try:
            # A rejected cookie is the one failure a retry cannot fix on its own,
            # so the second attempt onwards starts from a new jar.
            opener = nse_session(force_new=attempt > 0)
            with opener.open(Request(endpoint, headers=NSE_HEADERS), timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"NSE India did not return JSON: {text[:80]}") from error
        except HTTPError as error:
            if error.code not in {401, 403, 429, 500, 502, 503, 504}:
                raise RuntimeError(f"NSE India returned {error.code}.") from error
            last_error = error
        except (TimeoutError, URLError, OSError) as error:
            last_error = error
        if attempt < attempts - 1:
            time.sleep(0.4 * (attempt + 1))
    # What NSE said is carried into the message. Without it every deployment
    # failure read "temporarily unavailable", which cannot distinguish a refused
    # request from a slow one, and the note the tab shows the user inherits that
    # message - so the page could not say why either.
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "no response"
    if isinstance(last_error, HTTPError):
        detail = f"HTTP {last_error.code}"
    raise RuntimeError(
        f"NSE India endpoint is temporarily unavailable: {path} ({detail})"
    ) from last_error


def endpoint_family(endpoint):
    """Host plus route, with the instrument and query string stripped.

    Used to remember that a whole endpoint is refusing anonymous callers rather
    than rediscovering it per symbol. Some providers put the symbol in the path
    (``/v10/finance/quoteSummary/INFY.NS``), so a segment that looks like an
    instrument is dropped - otherwise the memo would hold one useless entry per
    symbol and never suppress anything.
    """
    parsed = urlparse(str(endpoint or ""))
    segments = [
        segment
        for segment in parsed.path.split("/")
        if segment and "." not in segment and not segment.startswith("^") and segment != segment.upper()
    ]
    return f"{parsed.netloc}/{'/'.join(segments[:4])}"


def note_unauthorized(endpoint):
    with _cache_lock:
        _unauthorized_endpoints[endpoint_family(endpoint)] = time.time() + UNAUTHORIZED_ENDPOINT_TTL_SECONDS


def is_unauthorized_endpoint(endpoint):
    expires_at = _unauthorized_endpoints.get(endpoint_family(endpoint))
    return bool(expires_at and expires_at > time.time())


def fetch_text(endpoint, json_request=False, sec=False):
    # Yahoo's quote and quoteSummary endpoints answer 401 to anonymous callers,
    # and they are called on every single report. Retrying them per symbol spends
    # request budget on a result that cannot change, and the provider answers 429
    # once that budget runs out - which then breaks the chart call the report
    # actually depends on. So a 401 is remembered per endpoint and short-circuited
    # until the TTL lapses, in case access is restored.
    if is_unauthorized_endpoint(endpoint):
        raise RuntimeError("Data provider returned 401.")

    headers = {
        "Accept": "application/json,text/plain,*/*" if json_request else "text/html,application/xhtml+xml",
        "User-Agent": SEC_USER_AGENT if sec else "Mozilla/5.0 StockResearchDesk/0.1",
    }
    request = Request(endpoint, headers=headers)
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            if error.code == 401:
                note_unauthorized(endpoint)
            if error.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"Data provider returned {error.code}.") from error
            last_error = error
        except (TimeoutError, URLError, OSError) as error:
            last_error = error

        if attempt < 2:
            time.sleep(0.35 * (attempt + 1))

    raise RuntimeError("Market data provider is temporarily unavailable. Please retry in a moment.") from last_error


def fetch_text_once(endpoint, timeout=8):
    headers = {
        "Accept": "text/csv,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 StockResearchDesk/0.1",
    }
    request = Request(endpoint, headers=headers)
    try:
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        raise RuntimeError(f"Data provider returned {error.code}.") from error
    except (TimeoutError, URLError, OSError) as error:
        raise RuntimeError("Market data provider is temporarily unavailable. Please retry in a moment.") from error


def cached(key, loader, ttl=CACHE_TTL_SECONDS):
    cached_value = _cache.get(key)
    if cached_value and cached_value["expiresAt"] > time.time():
        return cached_value["data"]
    try:
        data = loader()
    except Exception:
        # Serving slightly stale data beats failing the whole report when one
        # upstream endpoint is briefly down.
        if cached_value:
            cached_value["expiresAt"] = time.time() + min(ttl, 60)
            return cached_value["data"]
        raise
    set_cached(key, data, ttl)
    return data


def get_cached(key, include_expired=False):
    cached_value = _cache.get(key)
    if cached_value and (include_expired or cached_value["expiresAt"] > time.time()):
        return cached_value["data"]
    return None


def set_cached(key, data, ttl=CACHE_TTL_SECONDS):
    # Loaders run on a thread pool, so writes are serialised. Reads stay lock-free:
    # dict lookups are atomic under the GIL and a racing read at worst misses.
    with _cache_lock:
        _cache[key] = {"data": data, "expiresAt": time.time() + ttl}
        if len(_cache) > MAX_CACHE_ENTRIES:
            evict_cache_entries()
    return data


def evict_cache_entries():
    """Drop expired keys, then oldest, back to the entry ceiling.

    Called with ``_cache_lock`` held. Without this the dict grows for the life of
    the process: every symbol anyone looks up leaves a report behind.
    """
    now = time.time()
    for key, value in list(_cache.items()):
        if value["expiresAt"] <= now:
            _cache.pop(key, None)
    if len(_cache) <= MAX_CACHE_ENTRIES:
        return
    for key, _ in sorted(_cache.items(), key=lambda item: item[1]["expiresAt"])[
        : len(_cache) - MAX_CACHE_ENTRIES
    ]:
        _cache.pop(key, None)


def clear_cache(key):
    _cache.pop(key, None)


def clear_cache_prefix(prefix):
    for key in list(_cache.keys()):
        if key == prefix or key.startswith(f"{prefix}:"):
            _cache.pop(key, None)


def normalize_symbol(value):
    symbol = str(value or "").strip().upper()
    return symbol if re.fullmatch(r"[A-Z0-9.&^=_-]{1,25}", symbol) else ""


def raw(value):
    if isinstance(value, dict) and "raw" in value:
        return value.get("raw")
    return value


def arrayify(value):
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def to_date(epoch_seconds):
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).date().isoformat()


def clean_number(value):
    return value if is_finite(value) else None


def last(values):
    for value in reversed(values):
        if is_finite(value):
            return value
    return None


def safe_divide(numerator, denominator):
    return numerator / denominator if denominator else 0


def round2(value):
    return round(float(value) + 1e-12, 2)


def round_or_none(value):
    return round2(value) if is_finite(value) else None


def round_ratio_or_none(value):
    return round(float(value), 4) if is_finite(value) else None


def clamp(value, min_value, max_value):
    return min(max_value, max(min_value, value))


def weighted_average(items):
    total_weight = sum(weight for _, weight in items)
    return sum(value * weight for value, weight in items) / total_weight


def format_estimate(value):
    return "not available" if value is None else round2(value)


def first_present(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def array_get(values, index):
    if not values or index < 0 or index >= len(values):
        return None
    return values[index]


def is_finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_datetime(value):
    if not value:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_epoch(epoch_seconds):
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).isoformat().replace("+00:00", "Z")
