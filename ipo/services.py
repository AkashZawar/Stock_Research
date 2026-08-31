"""Data layer for the IPO Radar tab.

Three questions this answers, and where each answer comes from:

- **What listed in the last 7 days, and how did it do?** NSE's public past-issues
  feed supplies the final issue price and listing date; Yahoo supplies the
  listing-day open (the actual listing price) and the current price.
- **What is open now or opening in the next 7 days?** NSE's upcoming-issues feed
  supplies the price band and dates, NSE's current-issue and detail feeds supply
  live subscription (overall plus QIB/NII/Retail).
- **What is the grey market saying?** GMP has no official source, so three
  independent aggregators are scraped and averaged. The spread between them is
  reported as an agreement level, because a single source quoting an outlier is
  the normal failure mode here and silently averaging it away would hide that.

Everything scraped here is untrusted third-party HTML. Parsed values are coerced
to floats or length-capped strings before they leave this module, and no
provider-supplied URL is ever passed through to the client.
"""
import json
import re
import time
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from core.services import (
    SSL_CONTEXT,
    cached,
    clear_cache_prefix,
    fetch_chart_range,
    fetch_nse_json_with_session,
    get_cached,
    is_finite,
    iso_now,
    round2,
    set_cached,
    settle_map,
    settle_named_loaders,
)


IPO_CACHE_SECONDS = 10 * 60
IST = ZoneInfo("Asia/Kolkata")

RECENT_LISTING_WINDOW_DAYS = 7
UPCOMING_WINDOW_DAYS = 7

# Aggregators are ranked here only for display order; the consensus itself is an
# unweighted mean, since there is no basis for trusting one grey-market quote
# over another.
GMP_SOURCES = (
    ("IPO Ji", "https://ipoji.com/ipo-gmp"),
    ("IPO Watch", "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"),
    ("IPO Premium", "https://www.ipopremium.in/"),
    ("IPO Central", "https://ipocentral.in/ipo-discussion/"),
    ("IPO360", "https://www.ipo360.in/gmp"),
)

# A grey-market tracker is a nice-to-have layered on top of the exchange data,
# so it gets a short leash. These are third-party WordPress sites that go down
# without warning - IPO Watch has sat behind a Cloudflare 522 for hours - and a
# generous timeout there stalls the whole dashboard for every visitor.
GMP_FETCH_TIMEOUT = 8

# How long a scraped source is reused, and how long a failed one is left alone.
# Without a cooldown, every cache rebuild pays the full timeout again to
# rediscover that a site is still down.
GMP_SOURCE_CACHE_SECONDS = 10 * 60

# The cooldown doubles per consecutive failure, from one minute up to ten,
# because these sources fail in two different ways. IPO Watch flaps - measured
# over 22 requests it answered 8 of them - so a flat multi-minute ban would sit
# out recoveries that were one retry away. A genuinely dead host, by contrast,
# should be backed off hard rather than retried every minute. Growth separates
# the two without needing to know which is which.
GMP_SOURCE_COOLDOWN_SECONDS = 60
GMP_SOURCE_COOLDOWN_MAX_SECONDS = 10 * 60

# Per-category bids only break down a total the pipeline already has, so the
# request that fetches them is capped well below the NSE default.
BID_DETAIL_TIMEOUT = 8

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

MAX_TEXT_LENGTH = 160

# Words that differ between how NSE, the aggregators, and the exchanges each
# write the same company's name. Stripped before matching rows across sources.
NAME_NOISE = (
    "limited", "ltd", "ipo", "mainboard", "mainline", "sme", "nse", "bse",
    "private", "pvt", "india", "the", "open", "closed", "upcoming", "listed",
    "company", "corporation", "corp", "enterprises", "industries",
)


def get_ipo_dashboard():
    return cached(ipo_cache_key(), build_ipo_dashboard, IPO_CACHE_SECONDS)


def ipo_cache_key(now=None):
    value = now or datetime.now(IST)
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return f"ipo:{value.astimezone(IST).date().isoformat()}"


def clear_ipo_cache():
    clear_cache_prefix("ipo")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def clean_text(value, limit=MAX_TEXT_LENGTH):
    """Collapse whitespace, drop tags/entities, and cap length.

    Applied to every string taken from a scraped page so that a provider cannot
    push unbounded or markup-bearing text into the payload.
    """
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = (
        text.replace("&nbsp;", " ")
        .replace("&ndash;", "-")
        .replace("&mdash;", "-")
        .replace("&amp;", "&")
        .replace("&#8377;", "\u20b9")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def to_number(value):
    """First signed decimal in a string, or None.

    Handles the many shapes these feeds use for one number: ``"Rs.546"``,
    ``"+\u20b954 (+66%)"``, ``"   788"``, ``"1.555635E7"``, ``"1,23,456"``,
    ``"       .08"``.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if is_finite(float(value)) else None
    text = clean_text(value).replace(",", "")
    if not text or text in {"-", "--", "\u2014", "n/a", "NA"}:
        return None
    # Currency prefixes are stripped before matching. Left in place, the "." of
    # "Rs.546" reads as a decimal point and the value parses as 0.546.
    text = re.sub(r"(?i)\brs\.?", " ", text).replace("\u20b9", " ")
    # The sign is allowed to sit apart from the digits so that a stripped prefix
    # ("-\u20b95" -> "- 5") keeps its negative, which matters because a grey
    # market premium can be a discount.
    # The bare-decimal alternative is required by NSE's OFS feed, which reports
    # subscription as a right-aligned "       .08" with no leading zero.
    match = re.search(r"[-+]?\s*(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0).replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return number if is_finite(number) else None


def parse_price_band(value):
    """``"Rs.546 to Rs.575"`` / ``"\u20b978-82"`` / ``"168-177"`` -> (low, high)."""
    text = clean_text(value).replace(",", "")
    numbers = []
    for match in re.finditer(r"\d+(?:\.\d+)?", text):
        try:
            numbers.append(float(match.group(0)))
        except (TypeError, ValueError):
            continue
    if not numbers:
        return None, None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d")


def parse_date(value):
    """Parse the several date spellings these feeds mix, to a ``date``."""
    text = clean_text(value)
    if not text or text in {"-", "--", "\u2014"}:
        return None
    normalized = text.replace("/", "-").title()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    return None


def iso_date(value):
    return value.isoformat() if value else None


def normalize_name(value):
    """Reduce a company name to a comparable key across sources.

    NSE says "ESDS Software Solution Limited", one aggregator says "ESDS Software
    Solution IPO Mainboard Open", another says "ESDS Software Solution Ltd
    (MAINBOARD)". All three have to collapse to the same key or the GMP will not
    attach to the issue.
    """
    text = clean_text(value).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token and token not in NAME_NOISE]
    return " ".join(tokens)


def name_key(value):
    """Short prefix key, so "ESDS Software Solution" matches "ESDS Software"."""
    tokens = normalize_name(value).split()
    return " ".join(tokens[:3])


def names_match(left, right):
    left_tokens = normalize_name(left).split()
    right_tokens = normalize_name(right).split()
    if not left_tokens or not right_tokens:
        return False
    if left_tokens[0] != right_tokens[0]:
        return False
    # First token agreeing is a weak signal on its own ("Priority Jewels" vs
    # "Priority Technologies"), so require a second token to line up whenever
    # both sides actually have one.
    if len(left_tokens) > 1 and len(right_tokens) > 1:
        return left_tokens[1] == right_tokens[1]
    return True


def parse_html_tables(html):
    """Extract every table as a list of rows of cell text.

    A regex parser rather than a DOM one because the project deliberately ships
    with no HTML-parsing dependency (Django + certifi only).
    """
    tables = []
    for table_html in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I):
        rows = []
        for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S | re.I)
            if cells:
                rows.append([clean_text(cell) for cell in cells])
        if rows:
            tables.append(rows)
    return tables


def fetch_html(url, timeout=GMP_FETCH_TIMEOUT):
    request = Request(url, headers=BROWSER_HEADERS)
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        return response.read().decode("utf-8", errors="replace")


def header_index(header_row, *needles):
    for index, cell in enumerate(header_row):
        lowered = cell.lower()
        if any(needle in lowered for needle in needles):
            return index
    return None


def cell_at(row, index):
    if index is None or index >= len(row):
        return ""
    return row[index]


# ---------------------------------------------------------------------------
# GMP sources
# ---------------------------------------------------------------------------

def scrape_gmp_ipoji():
    """IPO Ji: one table, GMP in rupees plus an indicative listing price."""
    rows = []
    for table in parse_html_tables(fetch_html("https://ipoji.com/ipo-gmp")):
        header = table[0]
        name_col = header_index(header, "ipo")
        gmp_col = header_index(header, "gmp (")
        if name_col is None or gmp_col is None:
            continue
        band_col = header_index(header, "price band")
        listing_col = header_index(header, "indicative listing")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            rows.append(
                {
                    "company": name,
                    "gmp": gmp,
                    "bandHigh": band_high,
                    "expectedListing": to_number(cell_at(row, listing_col)),
                }
            )
    return rows


def scrape_gmp_ipowatch():
    """IPO Watch: separate mainboard and SME tables, plus a history table.

    The history table is skipped - it carries a "Listing Price" column of past
    outcomes, which would otherwise be mistaken for live estimates.
    """
    rows = []
    for table in parse_html_tables(fetch_html("https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/")):
        header = table[0]
        if header_index(header, "listing price") is not None:
            continue
        name_col = header_index(header, "ipo name")
        gmp_col = header_index(header, "gmp")
        if name_col is None or gmp_col is None:
            continue
        band_col = header_index(header, "price band")
        listing_col = header_index(header, "est. listing", "est listing")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            rows.append(
                {
                    "company": name,
                    "gmp": gmp,
                    "bandHigh": band_high,
                    "expectedListing": to_number(cell_at(row, listing_col)),
                }
            )
    return rows


def scrape_gmp_ipopremium():
    """IPO Premium: single table, GMP in rupees, no listing estimate column."""
    rows = []
    for table in parse_html_tables(fetch_html("https://www.ipopremium.in/")):
        header = table[0]
        name_col = header_index(header, "company name")
        gmp_col = header_index(header, "gmp")
        if name_col is None or gmp_col is None:
            continue
        band_col = header_index(header, "price band")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            rows.append({"company": name, "gmp": gmp, "bandHigh": band_high, "expectedListing": None})
    return rows


def scrape_gmp_ipocentral():
    """IPO Central, read through its WordPress REST endpoint rather than the page.

    Same content as ``/ipo-discussion/`` but 31 KB instead of 488 KB, because the
    endpoint returns the post body without the site chrome. The body is ordinary
    table markup, so it goes through the same parser as every other source.
    Mainboard and SME arrive as two tables, distinguished only by their first
    header cell.
    """
    payload = json.loads(fetch_html("https://ipocentral.in/wp-json/wp/v2/pages?slug=ipo-discussion"))
    if not isinstance(payload, list) or not payload:
        return []
    body = ((payload[0] or {}).get("content") or {}).get("rendered") or ""

    rows = []
    for table in parse_html_tables(body):
        header = table[0]
        name_col = header_index(header, "mainboard ipo", "sme ipo")
        gmp_col = header_index(header, "ipo gmp")
        if name_col is None or gmp_col is None:
            continue
        # "Price*" is already the cap, not a range.
        band_col = header_index(header, "price")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            rows.append({"company": name, "gmp": gmp, "bandHigh": band_high, "expectedListing": None})
    return rows


def scrape_gmp_ipo360():
    """IPO360: one server-rendered table covering mainboard and SME together."""
    rows = []
    for table in parse_html_tables(fetch_html("https://www.ipo360.in/gmp")):
        header = table[0]
        name_col = header_index(header, "company")
        gmp_col = header_index(header, "gmp")
        if name_col is None or gmp_col is None:
            continue
        band_col = header_index(header, "issue price")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            rows.append({"company": name, "gmp": gmp, "bandHigh": band_high, "expectedListing": None})
    return rows


GMP_SCRAPERS = {
    "IPO Ji": scrape_gmp_ipoji,
    "IPO Watch": scrape_gmp_ipowatch,
    "IPO Premium": scrape_gmp_ipopremium,
    "IPO Central": scrape_gmp_ipocentral,
    "IPO360": scrape_gmp_ipo360,
}


# Sources that failed recently, mapped to the time they may be retried, and the
# run of consecutive failures behind each. Held outside the shared cache because
# they record the absence of a value rather than a value.
_gmp_cooldowns = {}
_gmp_failures = {}


def gmp_source_cooldowns():
    return dict(_gmp_cooldowns)


def gmp_cooldown_for(failures):
    """Backoff after ``failures`` consecutive failures, capped."""
    return min(GMP_SOURCE_COOLDOWN_SECONDS * (2 ** max(0, failures - 1)), GMP_SOURCE_COOLDOWN_MAX_SECONDS)


def scrape_gmp_source(name):
    """One aggregator, reusing a recent scrape and backing off after a failure.

    A source in cooldown raises without touching the network, so a site that is
    down costs one timeout per cooldown window rather than one per request. The
    cooldown deliberately survives ``?refresh=1``: a Cloudflare 522 does not
    clear in the time it takes a user to click refresh again, and making them
    wait out the timeout to rediscover that helps nobody.
    """
    cache_key = f"ipo:gmp:{name}"
    rows = get_cached(cache_key)
    if rows is not None:
        return rows
    retry_at = _gmp_cooldowns.get(name)
    if retry_at and retry_at > time.time():
        raise RuntimeError(f"{name} failed recently; retrying after cooldown")
    try:
        rows = GMP_SCRAPERS[name]()
    except Exception:
        failures = _gmp_failures.get(name, 0) + 1
        _gmp_failures[name] = failures
        _gmp_cooldowns[name] = time.time() + gmp_cooldown_for(failures)
        raise
    _gmp_failures.pop(name, None)
    _gmp_cooldowns.pop(name, None)
    set_cached(cache_key, rows, GMP_SOURCE_CACHE_SECONDS)
    return rows


def collect_gmp_quotes():
    """Fetch every aggregator concurrently; a source that fails is just absent."""
    results = settle_named_loaders(
        {name: (lambda source=name: scrape_gmp_source(source)) for name in GMP_SCRAPERS},
        concurrency=len(GMP_SCRAPERS),
    )
    quotes = {}
    failed = []
    for name in GMP_SCRAPERS:
        rows = results.get(name)
        if not rows or not isinstance(rows, list):
            failed.append(name)
            continue
        quotes[name] = rows
    return quotes, failed


def gmp_band_high(company, quotes):
    """Cap price for an issue, taken from whichever aggregator reports one.

    NSE omits the price band for SME issues in its live feeds, and without a cap
    price there is nothing to express GMP as a percentage of, or to add GMP to
    for an expected listing price. The aggregators all carry the band, so it is
    borrowed from them when NSE has not supplied it.
    """
    for rows in quotes.values():
        for row in rows:
            if names_match(company, row["company"]) and is_finite(row.get("bandHigh")):
                return row["bandHigh"]
    return None


def gmp_consensus(company, quotes, band_high):
    """Average the per-source GMP quotes that match this company.

    The spread across sources is reported alongside the mean. Grey-market quotes
    routinely disagree by 20%+, and a mean with no dispersion attached would
    present a guess as a measurement.
    """
    matches = []
    for source_name, rows in quotes.items():
        for row in rows:
            if not names_match(company, row["company"]):
                continue
            gmp = row["gmp"]
            if gmp is None:
                continue
            cap = band_high if is_finite(band_high) else row.get("bandHigh")
            expected = row.get("expectedListing")
            if not is_finite(expected) and is_finite(cap):
                expected = cap + gmp
            matches.append(
                {
                    "source": source_name,
                    "gmp": round2(gmp),
                    "expectedListingPrice": round2(expected) if is_finite(expected) else None,
                }
            )
            break

    if not matches:
        return None

    values = [item["gmp"] for item in matches]
    average = sum(values) / len(values)
    low = min(values)
    high = max(values)
    percent = (average / band_high * 100) if is_finite(band_high) and band_high else None
    expected_price = (band_high + average) if is_finite(band_high) else None

    # Dispersion relative to the mean, so a 5-rupee gap on a 50-rupee GMP counts
    # as noisy while the same gap on a 350-rupee GMP does not.
    spread_percent = ((high - low) / average * 100) if average else 0.0
    if len(matches) < 2:
        agreement = "single source"
    elif spread_percent <= 15:
        agreement = "high"
    elif spread_percent <= 40:
        agreement = "moderate"
    else:
        agreement = "low"

    return {
        "value": round2(average),
        "percent": round2(percent) if is_finite(percent) else None,
        "expectedListingPrice": round2(expected_price) if is_finite(expected_price) else None,
        "low": round2(low),
        "high": round2(high),
        "spreadPercent": round2(spread_percent),
        "agreement": agreement,
        "sourceCount": len(matches),
        "sources": sorted(matches, key=lambda item: item["source"]),
    }


# ---------------------------------------------------------------------------
# NSE feeds
# ---------------------------------------------------------------------------

def fetch_upcoming_issues():
    payload = fetch_nse_json_with_session("/api/all-upcoming-issues?category=ipo")
    return payload if isinstance(payload, list) else []


def fetch_current_issues():
    payload = fetch_nse_json_with_session("/api/ipo-current-issue")
    return payload if isinstance(payload, list) else []


def fetch_past_issues():
    payload = fetch_nse_json_with_session("/api/public-past-issues")
    return payload if isinstance(payload, list) else []


def fetch_bid_details(symbol, series):
    """Per-category subscription (QIB / NII / Retail) for one live issue.

    This only splits a number the current-issue feed already supplies, so it is
    fetched on a tight budget: one extra request per open issue, and a stalling
    NSE would otherwise multiply across them and hold up the whole dashboard.
    """
    if not re.fullmatch(r"[A-Z0-9&_-]{1,20}", str(symbol or "")):
        return {}
    series_value = "SME" if str(series or "").upper() == "SME" else "EQ"
    payload = fetch_nse_json_with_session(
        f"/api/ipo-detail?symbol={symbol}&series={series_value}",
        timeout=BID_DETAIL_TIMEOUT,
        attempts=2,
    )
    if not isinstance(payload, dict):
        return {}
    details = {}
    for row in payload.get("bidDetails") or []:
        if not isinstance(row, dict):
            continue
        category = clean_text(row.get("category")).lower()
        times = to_number(row.get("noOfTime"))
        if times is None:
            continue
        if "qualified institutional" in category:
            details["qib"] = round2(times)
        elif "non institutional" in category and "2.1" not in str(row.get("srNo") or ""):
            details.setdefault("nii", round2(times))
        elif "retail" in category:
            details["retail"] = round2(times)
        elif "employee" in category:
            details["employee"] = round2(times)
        elif category.startswith("total"):
            details["total"] = round2(times)
    return details


def board_of(series_or_type):
    return "SME" if str(series_or_type or "").strip().upper() == "SME" else "Mainboard"


# NSE's past-issues feed mixes equity with bonds and NCDs (securityType "DEBT",
# "N2", "NC", ...). Those carry a 1000-rupee face value and no tradable equity
# chart, so they would show up as rows with a price and no outcome.
EQUITY_SECURITY_TYPES = {"EQ", "SME", "BE"}


def is_equity_issue(security_type):
    return str(security_type or "").strip().upper() in EQUITY_SECURITY_TYPES


# ---------------------------------------------------------------------------
# Price enrichment
# ---------------------------------------------------------------------------

def listing_and_current_price(symbol, board):
    """Listing-day open and latest price for a freshly listed issue.

    For a stock that listed days ago, a one-month chart contains only its post
    listing bars, so the first bar's open is the listing price. NSE is tried
    first and BSE second, because BSE-platform SME issues are absent from NSE.
    """
    candidates = [f"{symbol}.NS", f"{symbol}.BO"]
    if board == "SME":
        candidates.reverse()
    for candidate in candidates:
        try:
            chart = fetch_chart_range(candidate, "1mo", "1d")
        except Exception:
            continue
        candles = chart.get("candles") or []
        if not candles:
            continue
        meta = chart.get("meta") or {}
        current = meta.get("regularMarketPrice")
        if not is_finite(current):
            current = candles[-1].get("close")
        return {
            "listingPrice": round2(candles[0]["open"]) if is_finite(candles[0].get("open")) else None,
            "currentPrice": round2(current) if is_finite(current) else None,
            "analysisSymbol": candidate,
        }
    return {"listingPrice": None, "currentPrice": None, "analysisSymbol": None}


def percent_change(new_value, base_value):
    if not is_finite(new_value) or not is_finite(base_value) or not base_value:
        return None
    return round2((new_value - base_value) / base_value * 100)


# ---------------------------------------------------------------------------
# Recommendation flag
# ---------------------------------------------------------------------------

def upcoming_flag(gmp, subscription, is_open):
    """Score an open/upcoming issue green, amber, or red.

    Only components that actually have data contribute, and the total is
    renormalised over the weights present. An issue that has not opened yet has
    no subscription figures, and treating those as zero would flag every
    forthcoming issue red.
    """
    earned = 0.0
    available = 0.0
    reasons = []

    gmp_percent = (gmp or {}).get("percent")
    if is_finite(gmp_percent):
        available += 55
        # 30%+ premium is where listing gains have historically been reliable;
        # a negative premium is a discount and earns nothing.
        earned += 55 * max(0.0, min(1.0, gmp_percent / 30))
        reasons.append(f"GMP {gmp_percent:+.0f}% across {gmp.get('sourceCount')} source(s)")
        if gmp.get("agreement") == "low":
            earned -= 6
            reasons.append("sources disagree widely")

    total = (subscription or {}).get("total")
    if is_finite(total):
        available += 30
        earned += 30 * max(0.0, min(1.0, total / 10))
        reasons.append(f"subscribed {total:.2f}x overall")

    qib = (subscription or {}).get("qib")
    if is_finite(qib):
        available += 15
        earned += 15 * max(0.0, min(1.0, qib / 5))
        reasons.append(f"QIB {qib:.2f}x")

    if available <= 0:
        return {
            "flag": "grey",
            "score": None,
            "label": "No signal",
            "reason": "No GMP or subscription data published yet.",
        }

    score = max(0.0, min(100.0, earned / available * 100))

    if score >= 60:
        flag, label = "green", "Positive"
    elif score >= 35:
        flag, label = "amber", "Mixed"
    else:
        flag, label = "red", "Weak"

    if not is_open and not is_finite(total):
        reasons.append("bidding not open yet")

    return {
        "flag": flag,
        "score": round2(score),
        "label": label,
        "reason": "; ".join(reasons) if reasons else "Limited data.",
    }


def listed_flag(listing_gain, since_listing, vs_issue):
    """Flag a listed issue on how it has actually traded, not on expectation."""
    if not is_finite(vs_issue):
        return {"flag": "grey", "score": None, "label": "No signal", "reason": "Price data unavailable."}

    reasons = [f"{vs_issue:+.1f}% vs issue price"]
    if is_finite(listing_gain):
        reasons.append(f"listed {listing_gain:+.1f}%")
    if is_finite(since_listing):
        reasons.append(f"{since_listing:+.1f}% since listing")

    if vs_issue >= 10 and (not is_finite(since_listing) or since_listing > -10):
        flag, label = "green", "Holding gains"
    elif vs_issue <= 0:
        flag, label = "red", "Below issue"
    else:
        flag, label = "amber", "Fading"

    return {"flag": flag, "score": None, "label": label, "reason": "; ".join(reasons)}


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_recently_listed(past_issues, today):
    """Issues whose listing date falls inside the trailing 7-day window."""
    window_start = today - timedelta(days=RECENT_LISTING_WINDOW_DAYS)
    selected = []
    for row in past_issues:
        if not isinstance(row, dict):
            continue
        if not is_equity_issue(row.get("securityType")):
            continue
        listing_date = parse_date(row.get("listingDate"))
        if not listing_date or not (window_start <= listing_date <= today):
            continue
        symbol = clean_text(row.get("symbol"), 20).upper()
        if not symbol:
            continue
        band_low, band_high = parse_price_band(row.get("priceRange"))
        issue_price = to_number(row.get("issuePrice"))
        if not is_finite(issue_price):
            issue_price = band_high
        selected.append(
            {
                "symbol": symbol,
                "company": clean_text(row.get("company") or row.get("companyName")),
                "board": board_of(row.get("securityType")),
                "listingDate": iso_date(listing_date),
                "issuePrice": round2(issue_price) if is_finite(issue_price) else None,
                "priceBandLow": round2(band_low) if is_finite(band_low) else None,
                "priceBandHigh": round2(band_high) if is_finite(band_high) else None,
                "_listingDate": listing_date,
            }
        )

    selected.sort(key=lambda item: item["_listingDate"], reverse=True)

    prices = settle_map(selected, lambda item: listing_and_current_price(item["symbol"], item["board"]), 4)
    rows = []
    for item, (ok, price) in zip(selected, prices):
        item.pop("_listingDate", None)
        price = price if ok and isinstance(price, dict) else {}
        listing_price = price.get("listingPrice")
        current_price = price.get("currentPrice")
        issue_price = item["issuePrice"]

        listing_gain = percent_change(listing_price, issue_price)
        since_listing = percent_change(current_price, listing_price)
        vs_issue = percent_change(current_price, issue_price)

        rows.append(
            {
                **item,
                "listingPrice": listing_price,
                "currentPrice": current_price,
                "analysisSymbol": price.get("analysisSymbol"),
                "listingGainPercent": listing_gain,
                "sinceListingPercent": since_listing,
                "vsIssuePercent": vs_issue,
                "recommendation": listed_flag(listing_gain, since_listing, vs_issue),
            }
        )
    return rows


def build_pipeline(upcoming_issues, current_issues, quotes, today):
    """Issues open now, or opening within the next 7 days.

    The two NSE feeds overlap but neither is complete: the upcoming feed carries
    price bands and issue sizes but no bids, while the current feed carries live
    subscription and is the only one that lists SME issues. They are merged on
    symbol.
    """
    horizon = today + timedelta(days=UPCOMING_WINDOW_DAYS)
    merged = {}

    for row in upcoming_issues:
        if not isinstance(row, dict):
            continue
        symbol = clean_text(row.get("symbol"), 20).upper()
        if not symbol:
            continue
        merged[symbol] = dict(row)

    for row in current_issues:
        if not isinstance(row, dict):
            continue
        symbol = clean_text(row.get("symbol"), 20).upper()
        if not symbol:
            continue
        merged.setdefault(symbol, {}).update(
            {key: value for key, value in row.items() if value not in (None, "")}
        )

    candidates = []
    for symbol, row in merged.items():
        open_date = parse_date(row.get("issueStartDate"))
        close_date = parse_date(row.get("issueEndDate"))
        status_raw = clean_text(row.get("status"), 30).lower()

        is_open = status_raw == "active"
        if not is_open and open_date and close_date:
            is_open = open_date <= today <= close_date

        # Keep anything live today, plus anything opening inside the horizon.
        # Issues that already closed drop out; their outcome shows up in the
        # recently-listed table once they list.
        if not is_open:
            if not open_date or not (today <= open_date <= horizon):
                continue

        band_low, band_high = parse_price_band(row.get("issuePrice"))
        candidates.append(
            {
                "symbol": symbol,
                "company": clean_text(row.get("companyName") or row.get("company")),
                "board": board_of(row.get("series")),
                "series": "SME" if board_of(row.get("series")) == "SME" else "EQ",
                "status": "Open" if is_open else "Upcoming",
                "openDate": iso_date(open_date),
                "closeDate": iso_date(close_date),
                "priceBandLow": round2(band_low) if is_finite(band_low) else None,
                "priceBandHigh": round2(band_high) if is_finite(band_high) else None,
                "issueSize": to_number(row.get("issueSize")),
                "overallSubscription": to_number(row.get("noOfTime")),
                "_isOpen": is_open,
                "_openDate": open_date,
            }
        )

    # Per-category bids only exist once bidding is live, so only open issues are
    # asked for - one extra NSE round trip each, and forthcoming issues would
    # return nothing anyway.
    open_rows = [item for item in candidates if item["_isOpen"]]
    bid_results = settle_map(open_rows, lambda item: fetch_bid_details(item["symbol"], item["series"]), 6)
    bids_by_symbol = {}
    for item, (ok, value) in zip(open_rows, bid_results):
        bids_by_symbol[item["symbol"]] = value if ok and isinstance(value, dict) else {}

    rows = []
    for item in candidates:
        is_open = item.pop("_isOpen")
        open_date = item.pop("_openDate")

        subscription = dict(bids_by_symbol.get(item["symbol"]) or {})
        if not is_finite(subscription.get("total")) and is_finite(item.get("overallSubscription")):
            subscription["total"] = round2(item["overallSubscription"])
        subscription = subscription or None

        band_high = item.get("priceBandHigh")
        if not is_finite(band_high):
            band_high = gmp_band_high(item["company"], quotes)
            if is_finite(band_high):
                item["priceBandHigh"] = round2(band_high)
        gmp = gmp_consensus(item["company"], quotes, band_high)

        rows.append(
            {
                **{key: value for key, value in item.items() if key not in {"series", "overallSubscription"}},
                "subscription": subscription,
                "gmp": gmp,
                "recommendation": upcoming_flag(gmp, subscription, is_open),
                "analysisSymbol": f"{item['symbol']}.NS",
                "_sortKey": (0 if is_open else 1, open_date or today),
            }
        )

    rows.sort(key=lambda item: item.pop("_sortKey"))
    return rows


# ---------------------------------------------------------------------------
# Offer For Sale (OFS)
# ---------------------------------------------------------------------------

# An OFS runs over two sessions - non-retail on day one, retail on day two -
# and NSE reports each as its own series row rather than as one window.
OFS_SERIES = {"IS": "nonRetail", "RS": "retail", "ES": "employee"}

# How far back a completed OFS stays on the board, matching the listings table.
RECENT_OFS_WINDOW_DAYS = 7


def ofs_base_symbol(value):
    """Trading symbol behind an OFS archive symbol.

    The archive tags rows with the offer series rather than the scrip - Hindustan
    Copper's cumulative row is "HINDCOPPERCUMU" - and those suffixed forms are
    not tradable symbols, so they would send the analysis button to a dead quote.
    """
    symbol = clean_text(value).upper()
    for suffix in ("CUMU", "CUM"):
        if symbol.endswith(suffix) and len(symbol) > len(suffix) + 2:
            return symbol[: -len(suffix)]
    return symbol


def fetch_ofs_active():
    payload = fetch_nse_json_with_session("/api/live-ofs-active-issues")
    return (payload.get("data") or []) if isinstance(payload, dict) else []


def fetch_ofs_forthcoming():
    """Announced-but-not-open OFS issues.

    The category is "forthcoming", not "ofs". The latter is a dead parameter
    that answers with an empty *object*; a live category answers with a list,
    which is how the two are told apart.
    """
    payload = fetch_nse_json_with_session("/api/all-upcoming-issues?category=forthcoming")
    return payload if isinstance(payload, list) else []


def fetch_ofs_past():
    payload = fetch_nse_json_with_session("/api/live-ofs-past-issues")
    return (payload.get("data") or []) if isinstance(payload, dict) else []


def ofs_flag(discount_percent, subscription):
    """Score an OFS on the two things a retail bidder can actually see.

    The floor price is the whole point of an OFS: shares are offered at a
    discount to the market, so how far the market trades above the floor is the
    headline. Demand is the confirming signal - an OFS that clears many times
    over usually prices well above the floor, which erodes that discount.
    """
    components = []
    if is_finite(discount_percent):
        if discount_percent >= 10:
            score = 100
        elif discount_percent >= 5:
            score = 80
        elif discount_percent >= 2:
            score = 60
        elif discount_percent >= 0:
            score = 40
        else:
            score = 10
        components.append((score, 65))

    times = subscription.get("total")
    if not is_finite(times):
        times = max(
            (value for value in subscription.values() if is_finite(value)),
            default=None,
        )
    if is_finite(times):
        if times >= 3:
            score = 100
        elif times >= 1.5:
            score = 80
        elif times >= 1:
            score = 60
        elif times >= 0.5:
            score = 40
        else:
            score = 20
        components.append((score, 35))

    if not components:
        return {"flag": "grey", "label": "No signal", "score": None}

    weight = sum(item[1] for item in components)
    score = round(sum(value * item_weight for value, item_weight in components) / weight)
    if score >= 70:
        return {"flag": "green", "label": "Attractive", "score": score}
    if score >= 45:
        return {"flag": "amber", "label": "Fair", "score": score}
    return {"flag": "red", "label": "Thin discount", "score": score}


def build_active_ofs(groups):
    """Collapse NSE's per-series active-OFS rows into one row per company."""
    merged = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        symbol = clean_text(group.get("symbol")).upper()
        if not symbol:
            continue
        # The row list arrives under "data" for the forward session and "rows"
        # for the current one. Both shapes appear in the same response.
        series_rows = group.get("data") or group.get("rows") or []
        entry = merged.setdefault(
            symbol,
            {
                "company": clean_text(group.get("company")) or symbol,
                "symbol": symbol,
                "dates": [],
                "floorPrice": None,
                "cutOffPrice": None,
                "currentPrice": None,
                "issueSize": None,
                "subscription": {},
                "active": False,
            },
        )
        for row in series_rows:
            if not isinstance(row, dict):
                continue
            offer_date = parse_date(row.get("offerDate"))
            if offer_date:
                entry["dates"].append(offer_date)
            if str(clean_text(row.get("status"))).lower() == "active":
                entry["active"] = True
            for field, key in (("floorPrice", "floorPrice"), ("cutOffPrice", "cutOffPrice"), ("ltp", "currentPrice")):
                if entry[key] is None:
                    value = to_number(row.get(field))
                    if is_finite(value):
                        entry[key] = round2(value)
            size = to_number(row.get("issueSize"))
            if is_finite(size) and (entry["issueSize"] is None or size > entry["issueSize"]):
                entry["issueSize"] = size
            times = to_number(row.get("noOfTimes"))
            category = OFS_SERIES.get(clean_text(row.get("series")).upper())
            if category and is_finite(times):
                entry["subscription"][category] = round2(times)

    rows = []
    for entry in merged.values():
        dates = sorted(entry.pop("dates"))
        entry["openDate"] = iso_date(dates[0]) if dates else None
        entry["closeDate"] = iso_date(dates[-1]) if dates else None
        entry["status"] = "Active" if entry.pop("active") else "Scheduled"
        entry["discountPercent"] = percent_change(entry["currentPrice"], entry["floorPrice"])
        entry["recommendation"] = ofs_flag(entry["discountPercent"], entry["subscription"])
        entry["analysisSymbol"] = f"{entry['symbol']}.NS"
        rows.append(entry)
    rows.sort(key=lambda item: (item["openDate"] or "", item["company"]))
    return rows


def build_forthcoming_ofs(issues, today):
    rows = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        symbol = clean_text(item.get("symbol")).upper()
        company = clean_text(item.get("company") or item.get("companyName"))
        if not symbol and not company:
            continue
        # The feed spells the window both ways depending on the issue.
        open_date = parse_date(item.get("ofsStartDate") or item.get("startDate"))
        close_date = parse_date(item.get("ofsEndDate") or item.get("endDate"))
        if close_date and close_date < today:
            continue
        if open_date and open_date > today + timedelta(days=UPCOMING_WINDOW_DAYS):
            continue
        floor_price = to_number(item.get("floorPrice"))
        rows.append(
            {
                "company": company or symbol,
                "symbol": symbol,
                "status": "Upcoming",
                "openDate": iso_date(open_date),
                "closeDate": iso_date(close_date),
                "floorPrice": round2(floor_price) if is_finite(floor_price) else None,
                "cutOffPrice": None,
                "currentPrice": None,
                "discountPercent": None,
                "issueSize": to_number(item.get("issueSize")),
                "subscription": {},
                "recommendation": {"flag": "grey", "label": "Not open", "score": None},
                "analysisSymbol": f"{symbol}.NS" if symbol else None,
            }
        )
    rows.sort(key=lambda item: (item["openDate"] or "", item["company"]))
    return rows


def build_recent_ofs(past_rows, today, exclude_symbols):
    """Completed OFS issues from the last week, one row per company.

    The archive carries a row per category per session, so one offer appears
    several times across its two days. They are merged into a single window,
    keeping the clearing price and the strongest subscription reported.
    """
    cutoff = today - timedelta(days=RECENT_OFS_WINDOW_DAYS)
    merged = {}
    for item in past_rows:
        if not isinstance(item, dict):
            continue
        offer_date = parse_date(item.get("offerDate"))
        if not offer_date or offer_date < cutoff or offer_date > today:
            continue
        symbol = ofs_base_symbol(item.get("symbol"))
        company = clean_text(item.get("companyName"))
        key = symbol or company.upper()
        if not key or key in exclude_symbols:
            continue
        entry = merged.setdefault(
            key,
            {
                "company": company or symbol,
                "symbol": symbol,
                "status": "Completed",
                "dates": [],
                "floorPrice": None,
                "cutOffPrice": None,
                "currentPrice": None,
                "issueSize": None,
                "subscription": {},
                "recommendation": {"flag": "grey", "label": "Closed", "score": None},
                "analysisSymbol": f"{symbol}.NS" if symbol else None,
            },
        )
        entry["dates"].append(offer_date)
        for field, target in (("floorPrice", "floorPrice"), ("allocatePrice", "cutOffPrice")):
            if entry[target] is None:
                value = to_number(item.get(field))
                if is_finite(value):
                    entry[target] = round2(value)
        size = to_number(item.get("noOfshareOffered"))
        if is_finite(size) and (entry["issueSize"] is None or size > entry["issueSize"]):
            entry["issueSize"] = size
        times = to_number(item.get("noOfTimes"))
        if is_finite(times) and times > entry["subscription"].get("total", -1):
            entry["subscription"]["total"] = round2(times)

    rows = []
    for entry in merged.values():
        dates = sorted(entry.pop("dates"))
        entry["openDate"] = iso_date(dates[0])
        entry["closeDate"] = iso_date(dates[-1])
        entry["discountPercent"] = percent_change(entry["cutOffPrice"], entry["floorPrice"])
        rows.append(entry)
    rows.sort(key=lambda item: item["openDate"] or "", reverse=True)
    return rows


def build_ofs(today, active_feed, forthcoming_feed, past_feed):
    """Live, scheduled, and just-completed OFS issues from NSE.

    Three feeds back this: the active board, the forthcoming list (category
    "forthcoming"), and the archive going back to 2012, which is trimmed to the
    same 7-day window the listings table uses. They are fetched by the caller so
    they can share one thread pool with everything else the dashboard needs.
    """
    active = build_active_ofs(active_feed or [])
    upcoming = build_forthcoming_ofs(forthcoming_feed or [], today)
    live_symbols = {row["symbol"] for row in active + upcoming if row["symbol"]}
    recent = build_recent_ofs(past_feed or [], today, live_symbols)

    rows = active + upcoming + recent
    if not rows:
        return {
            "available": True,
            "rows": [],
            "note": "No OFS is open, scheduled, or completed in the last 7 days.",
        }
    return {
        "available": True,
        "rows": rows,
        "note": (
            "Floor price is the OFS bid floor; discount is how far the market trades "
            "above it. Completed offers show the allotment price instead."
        ),
    }


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

def build_ipo_dashboard():
    today = datetime.now(IST).date()

    # Every upstream this tab needs is independent, so they all go out at once.
    # Run in sequence, the slowest grey-market source would be added to the NSE
    # and OFS time rather than overlapped with it.
    feeds = settle_named_loaders(
        {
            "upcoming": fetch_upcoming_issues,
            "current": fetch_current_issues,
            "past": fetch_past_issues,
            "gmp": collect_gmp_quotes,
            "ofsActive": fetch_ofs_active,
            "ofsForthcoming": fetch_ofs_forthcoming,
            "ofsPast": fetch_ofs_past,
        },
        concurrency=7,
    )
    upcoming_issues = feeds.get("upcoming") or []
    current_issues = feeds.get("current") or []
    past_issues = feeds.get("past") or []

    if not upcoming_issues and not current_issues and not past_issues:
        raise RuntimeError("NSE India IPO feeds are temporarily unavailable. Please retry in a moment.")

    # settle_named_loaders reports a crashed loader as {}, which unpacks to no
    # quotes and every source marked failed - the same state as all three being
    # down, which is what it means.
    gmp_result = feeds.get("gmp")
    quotes, failed_sources = gmp_result if isinstance(gmp_result, tuple) else ({}, [name for name, _ in GMP_SOURCES])

    recently_listed = build_recently_listed(past_issues, today)
    pipeline = build_pipeline(upcoming_issues, current_issues, quotes, today)
    ofs = build_ofs(today, feeds.get("ofsActive"), feeds.get("ofsForthcoming"), feeds.get("ofsPast"))

    notes = []
    if failed_sources:
        notes.append(
            f"GMP source(s) not responding: {', '.join(failed_sources)}. "
            f"The average uses the {len(quotes)} that did."
            if quotes
            else f"GMP source(s) not responding: {', '.join(failed_sources)}."
        )
    if not quotes:
        notes.append("No GMP source responded, so grey-market columns are empty.")
    elif len(quotes) == 1:
        notes.append("Only one GMP source responded, so there is no cross-check on that premium.")

    return {
        "generatedAt": iso_now(),
        "asOfDate": today.isoformat(),
        "recentlyListed": recently_listed,
        "pipeline": pipeline,
        "ofs": ofs,
        "gmpSources": [
            {"name": name, "url": url, "ok": name in quotes}
            for name, url in GMP_SOURCES
        ],
        "counts": {
            "recentlyListed": len(recently_listed),
            "open": len([row for row in pipeline if row["status"] == "Open"]),
            "upcoming": len([row for row in pipeline if row["status"] == "Upcoming"]),
            "ofs": len(ofs["rows"]),
        },
        "notes": notes,
        "source": (
            "NSE India (issues, subscription, OFS) - Yahoo Finance (listing/current price) - "
            f"{', '.join(name for name, _ in GMP_SOURCES)} (GMP consensus)"
        ),
    }
