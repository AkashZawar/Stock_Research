import html
import json
import math
import os
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi


CACHE_TTL_SECONDS = 5 * 60
SEC_CACHE_TTL_SECONDS = 24 * 60 * 60
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "StockResearchDesk/0.1 contact@example.com")
_cache = {}
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

MODULES = ",".join([
    "assetProfile",
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "price",
    "calendarEvents",
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

BREAKOUT_WATCHLIST = [
    ("RELIANCE.NS", "Reliance Industries", ["Oil", "Petrochemicals"]),
    ("ONGC.NS", "ONGC", ["Oil"]),
    ("OIL.NS", "Oil India", ["Oil"]),
    ("IOC.NS", "Indian Oil", ["Oil"]),
    ("BPCL.NS", "BPCL", ["Oil"]),
    ("HPCL.NS", "HPCL", ["Oil"]),
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
    ("TATAMOTORS.NS", "Tata Motors", ["Metals", "Auto"]),
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
        "pressuredWhenUp": ["INDIGO.NS", "ASIANPAINT.NS", "BERGEPAINT.NS", "IOC.NS", "BPCL.NS", "HPCL.NS"],
    },
    {
        "commodity": "Industrial Metals",
        "whenUp": "May support metal producers and indicate stronger industrial demand.",
        "whenDown": "May pressure metal producers and help metal-consuming manufacturers.",
        "beneficiariesWhenUp": ["HINDALCO.NS", "TATASTEEL.NS", "JSWSTEEL.NS", "VEDL.NS", "HINDCOPPER.NS"],
        "pressuredWhenUp": ["LT.NS", "TATAMOTORS.NS", "MARUTI.NS"],
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


def analyze_symbol(symbol):
    return cached(f"analysis:{symbol}", lambda: _analyze_symbol(symbol), CACHE_TTL_SECONDS)


def _analyze_symbol(symbol):
    loaders = {
        "chart": lambda: get_chart(symbol),
        "quote": lambda: get_quote(symbol),
        "summary": lambda: get_summary(symbol),
        "sec": lambda: get_sec_fundamentals(symbol),
        "screener": lambda: get_screener_fundamentals(symbol),
    }
    results = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
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
    if len(candles) < 80:
        raise RuntimeError("Not enough one-year daily data was returned for this symbol.")

    quote_data = results.get("quote", (False, {}))[1] if results.get("quote", (False,))[0] else {}
    summary = results.get("summary", (False, {}))[1] if results.get("summary", (False,))[0] else {}
    sec = results.get("sec", (False, {}))[1] if results.get("sec", (False,))[0] else {}
    screener = results.get("screener", (False, {}))[1] if results.get("screener", (False,))[0] else {}
    return build_report(symbol, chart.get("meta", {}), candles, quote_data, summary, sec, screener)


def build_market_monitor():
    breakout_universe = market_activity_universe()
    commodity_results = settle_map(COMMODITIES, get_commodity_snapshot, concurrency=3)
    scan_results = settle_map(breakout_universe, scan_watchlist_stock, concurrency=4)
    activity_results = settle_map(market_activity_universe(), scan_high_activity_stock, concurrency=4)
    catalyst_results = settle_map(ORDER_CATALYST_WATCHLIST, scan_order_catalyst_stock, concurrency=2)
    commodity_snapshots = [value for ok, value in commodity_results if ok]
    scanned_stocks = [value for ok, value in scan_results if ok]
    activity_stocks = [value for ok, value in activity_results if ok]
    candidates = sorted(
        [
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
            )
        ],
        key=lambda item: item["score"],
        reverse=True,
    )[:18]
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
        "source": "Yahoo Finance public endpoints and headline scan",
        "note": "52-week and 2-year scan highs use the recent daily history returned by the provider. NR4/NR7 breakout means price is breaking above a tight prior 4-day or 7-day range near the 52-week high. Confirm liquidity, headlines, and levels on your broker or exchange feed before acting.",
        "commodities": commodity_snapshots,
        "breakoutCandidates": candidates,
        "highVolumeCandidates": high_volume_candidates,
        "orderCatalysts": order_catalysts,
        "impacted": impacted,
        "scannedCount": len(scanned_stocks),
        "activityScannedCount": len(activity_stocks),
        "catalystScannedCount": len([value for ok, value in catalyst_results if ok]),
    }


def market_activity_universe():
    return merge_watchlists(BREAKOUT_WATCHLIST, HIGH_ACTIVITY_WATCHLIST, ORDER_CATALYST_WATCHLIST)


def merge_watchlists(*watchlists):
    merged = {}
    for watchlist in watchlists:
        for stock in watchlist:
            symbol = stock.get("symbol")
            if not symbol:
                continue
            if symbol not in merged:
                merged[symbol] = {
                    "symbol": symbol,
                    "name": stock.get("name") or symbol,
                    "tags": list(stock.get("tags") or []),
                }
                continue
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
    volumes = [candle.get("volume") or 0 for candle in candles]
    latest = candles[-1]
    previous = candles[-2]
    available_high = max(highs)
    high_index = highs.index(available_high)
    high_52 = max(candle["high"] for candle in candles[-252:])
    prior_20_high = max(candle["high"] for candle in candles[-21:-1])
    prior_55_high = max(candle["high"] for candle in candles[-56:-1])
    atr_value = last(atr(candles[-80:], 14)) or latest["close"] * 0.02
    avg_volume_20 = last(sma(volumes, 20))
    volume_ratio = latest.get("volume", 0) / avg_volume_20 if avg_volume_20 else None
    pct_below_available_high = safe_divide(available_high - latest["close"], available_high) * 100
    pct_below_52_high = safe_divide(high_52 - latest["close"], high_52) * 100
    breakout = latest["close"] > prior_55_high and previous["close"] <= prior_55_high
    breakout_watch = latest["close"] >= prior_55_high * 0.98 or latest["close"] >= prior_20_high * 0.99
    near_available_high = 0 <= pct_below_available_high <= 3
    near_52_week_high = 0 <= pct_below_52_high <= 3
    near_52_week_setup = 0 <= pct_below_52_high <= 5
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
        "prior20High": round2(prior_20_high),
        "prior55High": round2(prior_55_high),
        "breakout": breakout,
        "breakoutWatch": breakout_watch,
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
    if data.get("narrowRange7Breakout"):
        return "52-week NR7 range breakout"
    if data.get("narrowRange4Breakout"):
        return "52-week NR4 range breakout"
    if data.get("narrowRange7Watch"):
        return "52-week NR7 breakout watch"
    if data.get("narrowRange4Watch"):
        return "52-week NR4 breakout watch"
    if data["breakout"] and data["nearAvailableHigh"]:
        return "Breakout near 2-year scan high"
    if data["breakout"]:
        return "Fresh breakout"
    if data["nearAvailableHigh"]:
        return "Near 2-year scan high"
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
        return normalized

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
                if item.get("symbol") and item.get("quoteType") != "CRYPTOCURRENCY"
            ]
        except Exception:
            yahoo_results = []
        return sort_search_results(merge_search_results(local_results + yahoo_results), normalized_query)[:24]

    return cached(f"search:{normalized_query.lower()}", loader, CACHE_TTL_SECONDS)


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
        if lower_query not in searchable and compact_query not in compact_searchable:
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
    return score


def get_chart(symbol):
    return get_chart_range(symbol, "1y", "1d")


def get_chart_range(symbol, range_value="1y", interval="1d"):
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


def get_quote(symbol):
    endpoint = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote(symbol)}"
    payload = fetch_json(endpoint)
    return ((payload.get("quoteResponse") or {}).get("result") or [{}])[0] or {}


def get_quotes(symbols):
    unique_symbols = list(dict.fromkeys([symbol for symbol in symbols if symbol]))
    if not unique_symbols:
        return {}
    endpoint = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={quote(','.join(unique_symbols))}"
    payload = fetch_json(endpoint)
    quotes = (payload.get("quoteResponse") or {}).get("result") or []
    return {quote_item.get("symbol"): quote_item for quote_item in quotes if quote_item.get("symbol")}


def get_summary(symbol):
    endpoint = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(symbol)}?modules={MODULES}"
    payload = fetch_json(endpoint)
    summary = payload.get("quoteSummary") or {}
    if summary.get("error"):
        raise RuntimeError(summary["error"].get("description") or "Fundamental data was not available.")
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
    return extract_screener_metrics(page)


def build_report(symbol, meta, candles, quote_data, summary, sec, screener):
    closes = [item["close"] for item in candles]
    volumes = [item.get("volume") or 0 for item in candles]
    current = closes[-1]
    previous = closes[-2]
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

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    rsi14 = rsi(closes, 14)
    macd_data = macd(closes)
    atr14 = atr(candles, 14)
    bands = bollinger(closes, 20, 2)
    avg_volume_20 = last(sma(volumes, 20))
    volume_ratio = (volumes[-1] or 0) / avg_volume_20 if avg_volume_20 else None
    year_high = max(item["high"] for item in candles)
    year_low = min(item["low"] for item in candles)
    support_resistance = find_levels(candles, current, last(atr14))
    fundamentals = extract_fundamentals(summary, quote_data, meta, sec, screener, current)
    events = extract_events(summary, screener)
    growth_drivers = build_growth_drivers(symbol, long_name, fundamentals, screener)
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
    quality = build_quality_report({
        "candles": candles,
        "quote": quote_data,
        "fundamentals": fundamentals,
        "technical": technical,
        "fundamental": fundamental,
        "eventRisk": event_risk,
        "supportResistance": support_resistance,
        "source": source,
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
        for index, item in enumerate(candles)
    ]

    return {
        "symbol": symbol,
        "longName": long_name,
        "currency": currency,
        "source": source,
        "generatedAt": iso_now(),
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
            "levels": support_resistance,
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
        "researchLevels": research_levels,
        "series": series,
    }


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
        "events": extract_screener_events(page),
    }


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


def build_growth_drivers(symbol, long_name, fundamentals, screener):
    ownership = build_ownership_trend(screener.get("shareholding"), fundamentals)
    catalysts = safe_growth_catalyst_headlines(symbol, long_name)
    budget_impacts = build_budget_impacts(symbol, long_name, fundamentals)
    data_notes = []

    if not ownership["rows"]:
        data_notes.append("Promoter/FII/DII trend was not available from the current data source.")
    if not catalysts:
        data_notes.append("No recent order, contract, budget, or policy headline matched the current scan.")
    if not budget_impacts:
        data_notes.append("No clear government budget allocation theme matched the available sector or watchlist tags.")

    return {
        "lookback": "Last 3-4 quarters / about 12 months where source data is available",
        "summary": summarize_growth_drivers(ownership, catalysts, budget_impacts),
        "ownership": ownership,
        "catalysts": catalysts,
        "budgetImpacts": budget_impacts,
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
        rows.append({
            "name": row["name"],
            "latest": round_ratio_or_none(latest),
            "previous": round_ratio_or_none(previous),
            "changePoints": round_or_none(change_points),
            "trend": holding_trend(change_points),
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
            "trend": "Latest only",
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
    }


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


def summarize_growth_drivers(ownership, catalysts, budget_impacts):
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
        "periods": list(dict.fromkeys([
            quarter["period"]
            for row in rows
            for quarter in row["quarters"]
        ])),
        "rows": rows,
    }


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
    warnings = []
    strengths = []
    score = 55

    if chart_points >= 200:
        score += 15
        strengths.append(f"One-year chart has {chart_points} daily candles.")
    else:
        score -= 18
        warnings.append(f"Only {chart_points} chart candles were available.")

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

    if not data["quote"].get("regularMarketTime"):
        score -= 4
        warnings.append("Provider did not return an official market timestamp for the quote.")

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
        "dataSources": data["source"],
        "strengths": strengths,
        "warnings": warnings,
    }


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


def fetch_json(endpoint, sec=False):
    text = fetch_text(endpoint, json_request=True, sec=sec)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Data provider did not return JSON: {text[:80]}") from error


def fetch_text(endpoint, json_request=False, sec=False):
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
            if error.code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"Data provider returned {error.code}.") from error
            last_error = error
        except (TimeoutError, URLError, OSError) as error:
            last_error = error

        if attempt < 2:
            time.sleep(0.35 * (attempt + 1))

    raise RuntimeError("Market data provider is temporarily unavailable. Please retry in a moment.") from last_error


def cached(key, loader, ttl=CACHE_TTL_SECONDS):
    cached_value = _cache.get(key)
    if cached_value and cached_value["expiresAt"] > time.time():
        return cached_value["data"]
    try:
        data = loader()
    except Exception:
        if cached_value:
            cached_value["expiresAt"] = time.time() + min(ttl, 60)
            return cached_value["data"]
        raise
    _cache[key] = {"data": data, "expiresAt": time.time() + ttl}
    return data


def clear_cache(key):
    _cache.pop(key, None)


def normalize_symbol(value):
    symbol = str(value or "").strip().upper()
    return symbol if re.fullmatch(r"[A-Z0-9.^=_-]{1,25}", symbol) else ""


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


def iso_from_epoch(epoch_seconds):
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).isoformat().replace("+00:00", "Z")
