"""Data layer for the IPO Radar tab.

Three questions this answers, and where each answer comes from:

- **What listed in the last 7 days, and how did it do?** NSE's public past-issues
  feed supplies the final issue price and listing date; Yahoo supplies the
  listing-day open (the actual listing price) and the current price.
- **What is open now or opening in the next 7 days?** NSE's upcoming-issues feed
  supplies the price band and dates, NSE's current-issue and detail feeds supply
  live subscription (overall plus QIB/NII/Retail).
- **What is the grey market saying?** GMP has no official source, so five
  independent aggregators are scraped and their median taken. The spread between
  them is reported as an agreement level, because a single source quoting an
  outlier is the normal case here rather than the exception, and a mean would let
  it move the headline premium without ever showing up.

Everything scraped here is untrusted third-party HTML. Parsed values are coerced
to floats or length-capped strings before they leave this module, and no
provider-supplied URL is ever passed through to the client.
"""
import json
import re
import statistics
import time
from collections import Counter
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

# Aggregators are ranked here only for display order. No source is trusted above
# another, so the consensus is their median: it needs no ranking to be robust,
# and one site quoting an outlier cannot move it.
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


def fetch_html(url, timeout=GMP_FETCH_TIMEOUT, headers=None):
    request = Request(url, headers=headers or BROWSER_HEADERS)
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


def gmp_status(value):
    """Normalise an aggregator's status word to the states this tab shows.

    Returns ``None`` when the source says nothing recognisable, so that "no
    status published" stays distinct from "closed" - the fallback pipeline drops
    closed issues, and guessing here would drop live ones.
    """
    text = clean_text(value).lower()
    if not text:
        return None
    if "upcoming" in text or "soon" in text:
        return "Upcoming"
    if "open" in text or "live" in text or "current" in text:
        return "Open"
    if "close" in text or "list" in text or "allot" in text:
        return "Closed"
    return None


def gmp_board(*values):
    """Board from a type column, or from an "(SME)"/"MB" tag the name carries.

    Matched on word boundaries: the abbreviations are short enough that a
    substring test would read the "MB" out of an unrelated word.
    """
    for value in values:
        text = clean_text(value).upper()
        if re.search(r"\bSME\b", text):
            return "SME"
        if re.search(r"\b(?:MAINBOARD|MAIN BOARD|MAINLINE|MB)\b", text):
            return "Mainboard"
    return None


# Tags an aggregator appends after the company name - board, exchange, status.
# None of them are part of the name.
GMP_NAME_STOP_WORDS = {"ipo", "bse", "nse", "sme", "mainboard", "mainline", "mb"}


def gmp_company_name(value):
    """Company name with the aggregator's trailing tags stripped.

    Sources append the board, exchange, status and sometimes the price band and
    dates: "Phychem IPO SME Rs.51 - Rs.54 31 Aug-2 Sep". Everything from the
    first such tag onward is dropped, which makes the name readable and, more
    importantly, lets two sources' spellings of one issue match each other -
    otherwise the same company lands in the table twice.

    A name that is entirely tags or begins with a number is left alone, since
    cutting it would leave nothing to match on.
    """
    text = re.sub(r"\([^)]*\)", " ", clean_text(value))
    words = []
    for word in text.split():
        key = re.sub(r"[^a-z]", "", word.lower())
        if key in GMP_NAME_STOP_WORDS or re.search(r"[\d\u20b9]", word):
            break
        words.append(word)
    return " ".join(words).strip(" .,-") or clean_text(value)


def parse_gmp_dates(value):
    """Split a combined "open - close" cell into two dates.

    Only fully spelled dates are taken. Some sources write the window as
    "1-3 September", which needs a year guessed and a leading day inferred from
    the trailing month; a wrong date here would be worse than a blank one, and
    another source usually supplies the same row properly.
    """
    text = clean_text(value)
    if not text:
        return None, None
    parts = [part for part in re.split(r"\s*(?:\u2013|\u2014|to|-)\s*", text) if part.strip()]
    if len(parts) < 2:
        single = parse_date(text)
        return single, single
    return parse_date(parts[0]), parse_date(parts[-1])


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
        type_col = header_index(header, "type")
        status_col = header_index(header, "status")
        window_col = header_index(header, "open")
        for row in table[1:]:
            name = cell_at(row, name_col)
            gmp = to_number(cell_at(row, gmp_col))
            if not name or gmp is None:
                continue
            _, band_high = parse_price_band(cell_at(row, band_col))
            open_date, close_date = parse_gmp_dates(cell_at(row, window_col))
            rows.append(
                {
                    "company": name,
                    "gmp": gmp,
                    "bandHigh": band_high,
                    "expectedListing": to_number(cell_at(row, listing_col)),
                    "status": gmp_status(cell_at(row, status_col)),
                    "board": gmp_board(cell_at(row, type_col), name),
                    "openDate": open_date,
                    "closeDate": close_date,
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
        status_col = header_index(header, "status")
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
                    "status": gmp_status(cell_at(row, status_col)),
                    "board": gmp_board(name),
                    # The date column here is written "1-3 September", which
                    # parse_gmp_dates deliberately declines to guess at.
                    "openDate": None,
                    "closeDate": None,
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
        band_col = header_index(header, "price band", "price")
        type_col = header_index(header, "type")
        open_col = header_index(header, "open")
        close_col = header_index(header, "close")
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
                    "expectedListing": None,
                    # No status column, but the window implies it.
                    "status": None,
                    "board": gmp_board(cell_at(row, type_col), name),
                    "openDate": parse_date(cell_at(row, open_col)),
                    "closeDate": parse_date(cell_at(row, close_col)),
                }
            )
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
        # Mainboard and SME arrive as separate tables, so the header names the
        # board for every row beneath it.
        board = "SME" if header_index(header, "sme ipo") is not None else "Mainboard"
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
                    "expectedListing": None,
                    "status": None,
                    "board": board,
                    "openDate": None,
                    "closeDate": None,
                }
            )
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
            rows.append(
                {
                    "company": name,
                    "gmp": gmp,
                    "bandHigh": band_high,
                    "expectedListing": None,
                    "status": None,
                    "board": gmp_board(name),
                    # Dates here are written "1 Sep" with no year.
                    "openDate": None,
                    "closeDate": None,
                }
            )
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
        rows = [dict(row, company=gmp_company_name(row["company"])) for row in GMP_SCRAPERS[name]()]
    except Exception:
        failures = _gmp_failures.get(name, 0) + 1
        _gmp_failures[name] = failures
        _gmp_cooldowns[name] = time.time() + gmp_cooldown_for(failures)
        raise
    _gmp_failures.pop(name, None)
    _gmp_cooldowns.pop(name, None)
    set_cached(cache_key, rows, GMP_SOURCE_CACHE_SECONDS)
    return rows


# NSE is the only exchange-grade source for subscription, and when it is
# unreachable the column has nothing behind it. Two republishers fill the gap,
# tried in order of measured accuracy.
#
# Chittorgarh is first because it is the closest to the exchange and says how
# old it is. Measured against NSE on Lumino Industries: NSE 104.69x overall /
# 211.25x QIB, Chittorgarh 124.02x / 232.79x (a later timestamp, consolidated
# across both exchanges), IPO Central 7.91x / 33.66x - understated more than
# tenfold. IPO Central is kept only as a second chance if Chittorgarh is down.
#
# Neither is a peer of the exchange, so both are consulted only when NSE gave
# nothing and every row sourced from them is attributed in the UI.
SUBSCRIPTION_FALLBACK_URL = (
    "https://ipocentral.in/wp-json/wp/v2/pages?slug=ipo-subscription-status-live-latest-data-nse-bse"
)

# Report 21 is the live subscription table. The path segments are
# report/page/month/year/financialYear/sort/segment/subSegment; the segment must
# be the literal "mainboard" or "sme" - passing 0 there returns "No params data
# found", which is what makes this endpoint look broken from the outside.
CHITTORGARH_SUBSCRIPTION_URL = (
    "https://webnodejs.chittorgarh.com/cloud/report/data-read/21/1/{month}/{year}/0/0/{segment}/0?search="
)


def scrape_subscription_chittorgarh():
    """Times-subscribed per category, from Chittorgarh's live subscription report.

    Mainboard and SME are separate calls. The company cell is a link wrapped in
    status badges, so only the anchor text is the name. Chittorgarh splits the
    non-institutional book into small and big NII; the combined "NII" column is
    used, to stay comparable with what NSE reports.
    """
    today = datetime.now(IST).date()
    rows = []
    for segment, board in (("mainboard", "Mainboard"), ("sme", "SME")):
        url = CHITTORGARH_SUBSCRIPTION_URL.format(month=today.month, year=today.year, segment=segment)
        payload = json.loads(fetch_html(url))
        if not isinstance(payload, dict) or payload.get("msg") != 1:
            continue
        for row in payload.get("reportTableData") or []:
            if not isinstance(row, dict):
                continue
            name = gmp_company_name(anchor_text(row.get("Company")))
            total = to_number(row.get("Total (x)"))
            if not name or total is None:
                continue
            entry = {"company": name, "board": board, "total": round2(total)}
            for key, column in (("qib", "QIB (x)"), ("nii", "NII (x)"), ("retail", "Retail (x)")):
                value = to_number(row.get(column))
                if value is not None:
                    entry[key] = round2(value)
            as_of = clean_text(row.get("Subscription as on"))
            if as_of:
                entry["asOf"] = as_of
            rows.append(entry)
    return rows


def anchor_text(value):
    """The link text of a cell, ignoring the status badges rendered beside it.

    Without this the name picks up the trailing badge - "Lumino Industries Ltd.
    CT" - which no other source spells that way, so nothing would match.
    """
    match = re.search(r"<a[^>]*>(.*?)</a>", str(value or ""), re.S | re.I)
    return clean_text(match.group(1) if match else value)


def scrape_subscription_ipocentral():
    """Times-subscribed per category, as republished by IPO Central.

    Mainboard and SME arrive as two tables that differ only in one header cell:
    the retail column is "Retail" on mainboard and "Individual" on SME. Unlike
    NSE, this source does publish a category split for SME issues.
    """
    payload = json.loads(fetch_html(SUBSCRIPTION_FALLBACK_URL))
    if not isinstance(payload, list) or not payload:
        return []
    body = ((payload[0] or {}).get("content") or {}).get("rendered") or ""

    rows = []
    for table in parse_html_tables(body):
        header = table[0]
        name_col = header_index(header, "ipo name")
        total_col = header_index(header, "total")
        if name_col is None or total_col is None:
            continue
        qib_col = header_index(header, "qib")
        nii_col = header_index(header, "nii")
        retail_col = header_index(header, "retail", "individual")
        board = "SME" if header_index(header, "individual") is not None else "Mainboard"

        for row in table[1:]:
            name = gmp_company_name(cell_at(row, name_col))
            total = to_number(cell_at(row, total_col))
            if not name or total is None:
                continue
            entry = {"company": name, "board": board, "total": round2(total)}
            for key, column in (("qib", qib_col), ("nii", nii_col), ("retail", retail_col)):
                value = to_number(cell_at(row, column)) if column is not None else None
                if value is not None:
                    entry[key] = round2(value)
            rows.append(entry)
    return rows


# Ordered by measured closeness to the exchange; the first one that answers with
# rows wins, so the weaker source is only reached if the better one is down.
SUBSCRIPTION_FALLBACKS = (
    ("Chittorgarh", lambda: scrape_subscription_chittorgarh()),
    ("IPO Central", lambda: scrape_subscription_ipocentral()),
)


def fetch_subscription_fallback():
    """Cached fallback subscription table, with the same backoff as a GMP source."""
    cache_key = "ipo:subscription:fallback"
    rows = get_cached(cache_key)
    if rows is not None:
        return rows

    for name, scraper in SUBSCRIPTION_FALLBACKS:
        retry_at = _subscription_cooldown.get(name)
        if retry_at and retry_at > time.time():
            continue
        try:
            rows = scraper()
        except Exception:
            failures = _subscription_failures.get(name, 0) + 1
            _subscription_failures[name] = failures
            _subscription_cooldown[name] = time.time() + gmp_cooldown_for(failures)
            continue
        if not rows:
            continue
        _subscription_failures.pop(name, None)
        _subscription_cooldown.pop(name, None)
        rows = [dict(row, source=name) for row in rows]
        set_cached(cache_key, rows, GMP_SOURCE_CACHE_SECONDS)
        return rows
    return []


_subscription_cooldown = {}
_subscription_failures = {}


# Chittorgarh publishes the rest of what NSE carries, under the same report API
# as the subscription table. The path is
# report/data-read/<id>/<page>/<month>/<year>/<financialYear>/<sort>/<segment>,
# and the segment has to be the literal "mainboard" or "sme": passing 0 answers
# msg=-1 with no rows, which is why these look dead until you get it right.
CHITTORGARH_REPORT_URL = (
    "https://webnodejs.chittorgarh.com/cloud/report/data-read/{report}/1/{month}/{year}/0/0/{segment}/0?search="
)
# 25 is listing performance, 184 is issue price band and size, 157 is OFS.
CHITTORGARH_LISTED_REPORT = 25
CHITTORGARH_ISSUE_REPORT = 184
CHITTORGARH_OFS_REPORT = 157


def fetch_chittorgarh_report(report, segment="mainboard"):
    today = datetime.now(IST).date()
    payload = json.loads(
        fetch_html(
            CHITTORGARH_REPORT_URL.format(
                report=report, month=today.month, year=today.year, segment=segment
            )
        )
    )
    if not isinstance(payload, dict) or payload.get("msg") != 1:
        return []
    return [row for row in payload.get("reportTableData") or [] if isinstance(row, dict)]


def scrape_recently_listed_chittorgarh():
    """Recently listed issues, shaped like a row of NSE's past-issues feed.

    Returning NSE's shape rather than the finished table means
    ``build_recently_listed`` keeps doing the date windowing, equity filtering
    and price enrichment, so the fallback cannot drift away from the primary
    path. Chittorgarh files the board under "Issue Category", which maps onto
    the ``securityType`` codes that ``is_equity_row`` and ``board_of`` expect.
    """
    rows = []
    for segment, security_type in (("mainboard", "EQ"), ("sme", "SME")):
        for row in fetch_chittorgarh_report(CHITTORGARH_LISTED_REPORT, segment):
            symbol = clean_text(row.get("NSE Symbol"), 20).upper()
            listing_date = clean_text(row.get("Listing Date"))
            if not symbol or not listing_date:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "company": clean_text(row.get("Company")),
                    "securityType": security_type,
                    "listingDate": listing_date,
                    "issuePrice": to_number(row.get("Issue Price (Rs.)")),
                    "priceRange": "",
                }
            )
    return rows


def scrape_issue_terms_chittorgarh():
    """Price band and issue size per company, keyed for name matching.

    The grey-market trackers quote a premium but never the band it applies to,
    so a GMP-only pipeline had no cap price - and without a cap price there is
    no expected listing price to compute.
    """
    terms = {}
    for segment, board in (("mainboard", "Mainboard"), ("sme", "SME")):
        for row in fetch_chittorgarh_report(CHITTORGARH_ISSUE_REPORT, segment):
            name = gmp_company_name(clean_text(row.get("Company")))
            if not name:
                continue
            low = to_number(row.get("Price Band Min."))
            high = to_number(row.get("Price Band Max."))
            issue_price = to_number(row.get("Issue Price (Rs.)"))
            if is_finite(issue_price) and not is_finite(high):
                low = high = issue_price
            size_crore = to_number(row.get("Issue Amount (Rs.cr.)"))
            if not is_finite(low) and not is_finite(high) and not is_finite(size_crore):
                continue
            terms[normalize_name(name)] = {
                "company": name,
                "board": board,
                # Only allocated once an issue nears listing, so the newest rows
                # carry none and the column stays honestly empty for them.
                "symbol": clean_text(row.get("~nse_symbol"), 20).upper(),
                "priceBandLow": round2(low) if is_finite(low) else None,
                "priceBandHigh": round2(high) if is_finite(high) else None,
                "issueSizeCrore": round2(size_crore) if is_finite(size_crore) else None,
                "openDate": clean_text(row.get("Opening Date")),
            }
    return terms


# Only NSE splits the QIB book into FII, DII and mutual funds, so when NSE is
# unreachable that section of the expanded row had nothing to draw. BSE runs the
# same book and publishes the same 1(a)-1(d) rows, which makes it the one
# fallback that answers the question rather than approximating it.
BSE_API_BASE = "https://api.bseindia.com/BseIndiaAPI/api"
# Both headers are load-bearing: without the Referer BSE answers 403, and
# without a browser User-Agent it redirects to an error page.
BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Accept": "application/json, text/plain, */*",
}
BSE_FETCH_TIMEOUT = 8
BSE_INDEX_CACHE_SECONDS = 30 * 60
BSE_SPLIT_CONCURRENCY = 5

# BSE is inconsistent about the case of the last serial - "1(d)" on some issues
# and "1(D)" on others - so serials are matched lowered.
BSE_QIB_SUBCATEGORIES = (
    ("1(a)", "fii", "Foreign institutional"),
    ("1(b)", "dii", "Domestic institutions"),
    ("1(c)", "mutualFunds", "Mutual funds"),
    ("1(d)", "otherQib", "Other QIB"),
)


def fetch_bse_json(path):
    payload = json.loads(fetch_html(f"{BSE_API_BASE}{path}", BSE_FETCH_TIMEOUT, BSE_HEADERS))
    return payload if isinstance(payload, (dict, list)) else {}


def bse_ipo_index():
    """Company name to BSE's internal issue number.

    Flag 1 is the open book and flag 2 the closed one; an issue that has closed
    but not listed sits in the second, which is exactly the window this tab
    covers, so both are read.
    """

    def load():
        index = {}
        for flag in (1, 2):
            payload = fetch_bse_json(f"/GetPublicIssue_par_updated/w?flag={flag}")
            rows = payload if isinstance(payload, list) else (payload.get("Table") or [])
            for row in rows:
                if not isinstance(row, dict) or clean_text(row.get("IR_flag")).upper() != "IPO":
                    continue
                name = gmp_company_name(clean_text(row.get("Scrip_Name")))
                issue_no = clean_text(row.get("IPO_NO"), 12)
                if name and issue_no:
                    index.setdefault(normalize_name(name), issue_no)
        return index

    return cached("ipo-bse-index", load, BSE_INDEX_CACHE_SECONDS) or {}


def parse_bse_institutional_split(payload):
    """The QIB sub-categories, in the shape NSE's own split is parsed into.

    BSE leaves the sub-category cells empty until institutions actually bid, so
    an empty split means "nobody has bid yet", not a parse failure.
    """
    by_serial = {}
    for row in payload.get("table2") or []:
        if isinstance(row, dict):
            by_serial[clean_text(row.get("SRNo")).lower()] = row

    split = {}
    for serial, key, label in BSE_QIB_SUBCATEGORIES:
        shares = to_number((by_serial.get(serial) or {}).get("col4"))
        if is_finite(shares) and shares > 0:
            split[key] = {"label": label, "sharesBid": shares}

    total = sum(entry["sharesBid"] for entry in split.values())
    if total > 0:
        for entry in split.values():
            entry["shareOfQib"] = round2(entry["sharesBid"] / total * 100)
    return split, to_number((by_serial.get("1") or {}).get("col5"))


def scrape_institutional_bse(companies):
    """QIB split per company, keyed for name matching."""
    wanted = {normalize_name(name): name for name in companies if name}
    if not wanted:
        return {}

    index = bse_ipo_index()
    keys = [key for key in wanted if key in index]
    if not keys:
        return {}

    found = {}
    for key, (ok, result) in zip(
        keys,
        settle_map(
            [index[key] for key in keys],
            lambda issue_no: parse_bse_institutional_split(
                fetch_bse_json(f"/Pubissues_GetBkbldgCatdem_ng/w?IPO_NO={issue_no}")
            ),
            concurrency=BSE_SPLIT_CONCURRENCY,
        ),
    ):
        if ok and result and result[0]:
            found[key] = {"institutional": result[0], "qibTimes": result[1]}
    return found


# Neither NSE feed carries an industry, so before this the tab said every issue
# was unclassified until listing - in every environment, not just when NSE was
# down. Chittorgarh files one, but indirectly: the per-issue page embeds a
# numeric industry id, and the id-to-name table is the sector dropdown on the
# sector-wise report. Two cheap lookups plus one page per issue.
CHITTORGARH_SECTOR_URL = "https://www.chittorgarh.com/report/sector-wise-ipo-list-in-india/96/all/77/"
CHITTORGARH_IPO_PAGE_URL = "https://www.chittorgarh.com/ipo/{slug}/{id}/"
SECTOR_CACHE_SECONDS = 24 * 60 * 60
SECTOR_LOOKUP_CONCURRENCY = 6

# The payload is embedded in a Next.js script block, so the JSON arrives
# double-escaped and the quotes may or may not carry backslashes.
INDUSTRY_ID_PATTERN = re.compile(r'ipo_industry\\*"\s*:\s*\\*"(\d+)')
SECTOR_OPTION_PATTERN = re.compile(r'<option[^>]*value="(\d+)"[^>]*>([^<]{2,60})</option>')


def chittorgarh_sector_map():
    """Industry id to industry name.

    Cached for a day: this is a reference table that changes when a new industry
    is coined, not when an issue opens.
    """

    def load():
        html = fetch_html(CHITTORGARH_SECTOR_URL).replace("\\u003c", "<").replace("\\u003e", ">")
        return {
            key: clean_text(value)
            for key, value in SECTOR_OPTION_PATTERN.findall(html)
            if clean_text(value)
        }

    return cached("ipo-sector-map", load, SECTOR_CACHE_SECONDS) or {}


def chittorgarh_ipo_page_index():
    """Company name to the slug and id its Chittorgarh page lives at."""
    index = {}
    for segment in ("mainboard", "sme"):
        for row in fetch_chittorgarh_report(CHITTORGARH_ISSUE_REPORT, segment):
            name = gmp_company_name(clean_text(row.get("Company")))
            slug = clean_text(row.get("~URLRewrite_Folder_Name"), 120)
            issue_id = clean_text(row.get("~id"), 12)
            if name and slug and issue_id:
                index.setdefault(normalize_name(name), (slug, issue_id))
    return index


def fetch_chittorgarh_sector(entry, sectors):
    slug, issue_id = entry
    match = INDUSTRY_ID_PATTERN.search(
        fetch_html(CHITTORGARH_IPO_PAGE_URL.format(slug=slug, id=issue_id))
    )
    return sectors.get(match.group(1)) if match else None


def scrape_sectors_chittorgarh(companies):
    """Industry per company, for the names the tab is about to show.

    One page request per issue, so it is scoped to the pipeline rather than run
    across every issue of the year, and failures are per-company: a page that
    does not load costs that row its sector and nothing else.
    """
    wanted = {normalize_name(name): name for name in companies if name}
    if not wanted:
        return {}

    sectors = chittorgarh_sector_map()
    index = chittorgarh_ipo_page_index()
    keys = [key for key in wanted if key in index]
    if not sectors or not keys:
        return {}

    found = {}
    for key, (ok, sector) in zip(
        keys,
        settle_map(
            [index[key] for key in keys],
            lambda entry: fetch_chittorgarh_sector(entry, sectors),
            concurrency=SECTOR_LOOKUP_CONCURRENCY,
        ),
    ):
        if ok and sector:
            found[key] = sector
    return found


def scrape_ofs_chittorgarh(today):
    """OFS offers, trimmed to the same window the NSE-backed table uses.

    Chittorgarh reports one offer date rather than NSE's open/close pair, so an
    offer is treated as open on its date and recent for the trailing window.
    """
    rows = []
    for row in fetch_chittorgarh_report(CHITTORGARH_OFS_REPORT):
        company = clean_text(row.get("Company Name"))
        offer_date = parse_date(row.get("Offer date") or row.get("~offer_open_date"))
        if not company or not offer_date:
            continue
        if not (today - timedelta(days=RECENT_LISTING_WINDOW_DAYS)
                <= offer_date
                <= today + timedelta(days=UPCOMING_WINDOW_DAYS)):
            continue
        floor_price = to_number(row.get("Floor price (Rs.)"))
        cut_off = to_number(row.get("Cut-off Price (Rs.)"))
        status = "Upcoming" if offer_date > today else "Open" if offer_date == today else "Completed"
        rows.append(
            {
                "company": company,
                "symbol": "",
                "status": status,
                "openDate": iso_date(offer_date),
                "closeDate": iso_date(offer_date),
                "floorPrice": round2(floor_price) if is_finite(floor_price) else None,
                "cutOffPrice": round2(cut_off) if is_finite(cut_off) else None,
                "currentPrice": None,
                "discountPercent": None,
                "issueSize": to_number(row.get("Total Shares offered")),
                "subscription": {},
                "recommendation": {"flag": "grey", "label": "Not rated", "score": None},
                "analysisSymbol": None,
                "source": "Chittorgarh",
            }
        )
    rows.sort(key=lambda item: (item["openDate"] or "", item["company"]))
    return rows


def subscription_from_fallback(company, board, rows):
    """The fallback's figures for one company, or ``None`` if it does not list it."""
    for row in rows or []:
        if not names_match(company, row["company"]):
            continue
        # Board is advisory: the aggregator occasionally files an SME issue under
        # the mainboard table, and a name match is the stronger signal.
        if board and row.get("board") and row["board"] != board:
            continue
        return {
            **{key: row[key] for key in ("qib", "nii", "retail", "total", "asOf") if key in row},
            "source": row.get("source") or "aggregator",
        }
    return None


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


def gmp_agreement(values):
    """How far apart the sources are: a label and the spread behind it.

    Dispersion is measured against the median's magnitude, so a 5-rupee gap on a
    50-rupee premium counts as noisy while the same gap on a 350-rupee one does
    not.

    Two cases have to be caught before the arithmetic, because both used to
    divide by a mean of zero and report the result as perfect agreement. Sources
    that disagree about the sign do not agree at all - quotes of +10 and -10 were
    read as "high" - and a centre of zero has no magnitude to take a ratio
    against. A negative centre inverted the scale outright, so wider spreads
    scored as closer ones.
    """
    if len(values) < 2:
        return "single source", 0.0
    low, high = min(values), max(values)
    spread = high - low
    if low < 0 < high:
        return "low", round2(spread)
    centre = abs(statistics.median(values))
    if not centre:
        return ("high", 0.0) if not spread else ("low", round2(spread))
    spread_percent = spread / centre * 100
    if spread_percent <= 15:
        agreement = "high"
    elif spread_percent <= 40:
        agreement = "moderate"
    else:
        agreement = "low"
    return agreement, round2(spread_percent)


def gmp_consensus(company, quotes, band_high):
    """Combine the per-source GMP quotes that match this company.

    The headline is the median, not the mean. These sources disagree by more than
    noise: measured across one session's seven issues, IPO Ji sat below the others
    on every single row, by an average of 11 points of the price band, and on Rays
    of Belief it quoted 14 where the other three said 48, 48 and 50. A mean lets
    that one dissenter drag the published premium down by a fifth. The median
    reports what the sources actually agree on, and the outlier stays visible in
    the spread and in the per-source list the row expands to show.
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
    consensus = statistics.median(values)
    low = min(values)
    high = max(values)
    percent = (consensus / band_high * 100) if is_finite(band_high) and band_high else None
    expected_price = (band_high + consensus) if is_finite(band_high) else None
    agreement, spread_percent = gmp_agreement(values)

    return {
        "value": round2(consensus),
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


def fetch_issue_detail(symbol, series):
    """Subscription split and issue profile for one live issue.

    Both come from the same ``/api/ipo-detail`` response, so they are parsed
    from a single request rather than fetched twice. The budget stays tight:
    one extra request per open issue, and a stalling NSE would otherwise
    multiply across them and hold up the whole dashboard.
    """
    if not re.fullmatch(r"[A-Z0-9&_-]{1,20}", str(symbol or "")):
        return {"subscription": {}, "profile": {}}
    series_value = "SME" if str(series or "").upper() == "SME" else "EQ"
    payload = fetch_nse_json_with_session(
        f"/api/ipo-detail?symbol={symbol}&series={series_value}",
        timeout=BID_DETAIL_TIMEOUT,
        attempts=2,
    )
    if not isinstance(payload, dict):
        return {"subscription": {}, "profile": {}}
    return {
        "subscription": parse_bid_details(payload),
        "profile": parse_issue_profile(payload),
    }


def parse_bid_details(payload):
    """Times-subscribed per headline category."""
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


# Within the QIB block NSE reports who actually bid, keyed by sub-serial rather
# than by a stable name. Only shares bid are given here (no times-subscribed),
# so these are shown as a share of the QIB book rather than as a multiple.
QIB_SUBCATEGORIES = (
    ("1(a)", "fii", "Foreign institutional"),
    ("1(b)", "dii", "Domestic institutions"),
    ("1(c)", "mutualFunds", "Mutual funds"),
    ("1(d)", "otherQib", "Other QIB"),
)


def parse_institutional_split(payload):
    """FII / DII / mutual-fund participation inside the QIB category."""
    by_serial = {}
    for row in payload.get("bidDetails") or []:
        if isinstance(row, dict):
            by_serial[str(row.get("srNo") or "").strip()] = row

    split = {}
    for serial, key, label in QIB_SUBCATEGORIES:
        shares = to_number((by_serial.get(serial) or {}).get("noOfsharesBid"))
        if shares is not None and shares > 0:
            split[key] = {"label": label, "sharesBid": shares}

    total = sum(entry["sharesBid"] for entry in split.values())
    if total > 0:
        for entry in split.values():
            entry["shareOfQib"] = round2(entry["sharesBid"] / total * 100)
    return split


# The issue-information block is a flat title/value list holding roughly forty
# entries, most of them procedural boilerplate (UPI cut-off times, ASBA notes,
# circular links). Only these carry information about the offer itself.
PROFILE_FIELDS = (
    ("issueSizeText", "issue size"),
    ("issueType", "issue type"),
    ("faceValue", "face value"),
    ("bidLot", "bid lot"),
    ("priceRange", "price range"),
    ("leadManagers", "book running lead managers"),
    ("registrar", "name of the registrar"),
)


def parse_issue_profile(payload):
    """Company and offer particulars for the expandable row."""
    entries = {}
    for item in (payload.get("issueInfo") or {}).get("dataList") or []:
        if not isinstance(item, dict):
            continue
        title = clean_text(item.get("title")).lower().rstrip(":")
        value = clean_text(item.get("value")).strip('"').strip()
        if title and value:
            entries[title] = value

    profile = {}
    for key, title in PROFILE_FIELDS:
        if entries.get(title):
            profile[key] = entries[title]

    company = clean_text(payload.get("companyName"))
    if company:
        profile["companyName"] = company

    institutional = parse_institutional_split(payload)
    if institutional:
        profile["institutional"] = institutional
    return profile


def board_of(series_or_type):
    return "SME" if str(series_or_type or "").strip().upper() == "SME" else "Mainboard"


# NSE's past-issues feed mixes equity with bonds and NCDs (securityType "DEBT",
# "N2", "NC", ...). Those carry a 1000-rupee face value and no tradable equity
# chart, so they would show up as rows with a price and no outcome.
EQUITY_SECURITY_TYPES = {"EQ", "SME", "BE"}

# Some bond series are filed under an equity securityType, so the type alone
# does not catch them: NSE tags Vision Infra's 11.50% 2030 NCD as "SME", and it
# reached the listings table as a real issue priced at 1,00,000 rupees.
#
# Their symbol still follows the debt convention of coupon, issuer, maturity
# year - "1150VIES30", "920ISF28", "10MWL29" - which no equity ticker does. The
# tickers that open with a digit ("5PAISA", "63MOONS", "20MICRONS", "3IINFOLTD")
# never also close with one, so requiring digits at both ends separates them.
DEBT_SERIES_SYMBOL = re.compile(r"^\d+[A-Z]+\d+[A-Z]?$")

# A debt series is sold at face value, which leaves its issue price orders of
# magnitude above the price band NSE files on the same row. Tested alongside the
# symbol because either signal on its own is circumstantial; across the full
# feed the two agree on exactly the same rows and neither rejects an equity.
DEBT_FACE_VALUES = {10000.0, 100000.0}


def is_equity_issue(security_type):
    return str(security_type or "").strip().upper() in EQUITY_SECURITY_TYPES


def is_equity_row(row):
    """Whether a past-issues row is a tradable equity issue rather than a bond."""
    if not is_equity_issue(row.get("securityType")):
        return False
    if DEBT_SERIES_SYMBOL.match(clean_text(row.get("symbol"), 20).upper()):
        return False
    issue_price = to_number(row.get("issuePrice"))
    _, band_high = parse_price_band(row.get("priceRange"))
    if issue_price in DEBT_FACE_VALUES and is_finite(band_high) and band_high and issue_price > band_high * 5:
        return False
    return True


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

# What each signal is worth to the score: how strongly it points at a good
# outcome. GMP leads because a premium the grey market will actually pay is the
# best available predictor of a listing pop.
GMP_WEIGHT = 55.0
SUBSCRIPTION_WEIGHT = 30.0
QIB_WEIGHT = 15.0

# What each signal is worth to confidence: how far it can be relied on. These are
# deliberately not the scoring weights, because the two answer different
# questions. GMP predicts well, but it is an unregulated market of rumour with no
# published trades; subscription is real money bid on the exchange. Deriving
# confidence from the scoring weights made an issue with 25x subscription and 6x
# QIB demand - about as hard as evidence gets here - come out less certain than
# four websites agreeing on a number, which is backwards.
#
# They are set so that a full grey-market consensus and a full set of exchange
# bidding figures each clear the bar on their own, and a thin version of either
# does not.
GMP_CONFIDENCE = 0.5
SUBSCRIPTION_CONFIDENCE = 0.35
QIB_CONFIDENCE = 0.15

# A premium quoted by one site is a rumour; the same premium on four sites that
# agree is closer to a measurement. GMP therefore carries less weight the fewer
# sources stand behind it, rather than one quote counting as a full reading.
GMP_CORROBORATION = {1: 0.55, 2: 0.75, 3: 0.9}

# Sources that disagree widely are still evidence, just weaker evidence, so the
# penalty scales the GMP component rather than subtracting a flat number of
# points from a total whose size now varies.
GMP_DISAGREEMENT_FACTOR = 0.85

# A score of 50 means "no view". Scores are pulled toward it in proportion to
# how much of the evidence is missing.
NEUTRAL_SCORE = 50.0

# Below this much of the evidence, no verdict is stated either way. Shrinking the
# score alone still let a lone quote out at 65, over the green line, and
# "Positive" is a stronger word than one unconfirmed grey-market number has
# earned. An issue that has not opened yet can still be called - it just needs
# several sources to agree first, rather than one site's say-so.
MIN_CONFIDENCE_FOR_VERDICT = 0.5


def gmp_corroboration(source_count):
    return GMP_CORROBORATION.get(source_count, 1.0) if source_count else 1.0


def bidding_progress(open_date, close_date, today):
    """How far through its bidding window an issue is, from 0 to 1.

    Indian IPO books fill back-loaded: retail trickles in from day one while QIB
    and NII bid in the closing hours, so a book routinely goes from under 1x to
    tens of times over on its final day. An early reading is therefore not a weak
    result, it is an incomplete one, and the two were being scored the same.

    Measured live: Rays of Belief opened this morning at 0.02x and was flagged
    red, on the same footing as Purple Style Labs at 0.10x two thirds of the way
    through its window - and against a +20% premium four sources agreed on.

    A window that cannot be dated returns 1, which keeps the previous behaviour
    of taking whatever figure is published at face value.
    """
    if not open_date or not close_date or close_date < open_date:
        return 1.0
    if today < open_date:
        return 0.0
    total_days = (close_date - open_date).days + 1
    elapsed_days = (today - open_date).days + 1
    return max(0.0, min(1.0, elapsed_days / total_days))


def upcoming_flag(gmp, subscription, is_open, progress=1.0):
    """Score an open/upcoming issue green, amber, or red.

    Two things are being measured and they must not be confused: how good the
    signals look, and how much signal there is. Renormalising over the weights
    present answers only the first, which let a single unverified grey-market
    quote score a perfect 100 - the identical score to an issue whose premium was
    confirmed by 25x subscription and 6x QIB demand. Certainty about a fifth of
    the evidence was being reported as certainty about all of it.

    So the strength of what is present is scored first, then pulled toward
    neutral in proportion to what is missing. Direction is preserved - a thin
    signal stays on the side it points to - but neither extreme is reachable
    without the evidence to support it, in either direction: one source quoting a
    steep discount is no more conclusive than one quoting a steep premium.
    """
    earned = 0.0
    available = 0.0
    confidence = 0.0
    reasons = []

    gmp_percent = (gmp or {}).get("percent")
    if is_finite(gmp_percent):
        source_count = (gmp or {}).get("sourceCount") or 0
        corroboration = gmp_corroboration(source_count)
        available += GMP_WEIGHT
        confidence += GMP_CONFIDENCE * corroboration
        # 30%+ premium is where listing gains have historically been reliable;
        # a negative premium is a discount and earns nothing.
        share = max(0.0, min(1.0, gmp_percent / 30))
        if (gmp or {}).get("agreement") == "low":
            share *= GMP_DISAGREEMENT_FACTOR
            reasons.append(f"GMP {gmp_percent:+.0f}% across {source_count} source(s), which disagree widely")
        else:
            reasons.append(f"GMP {gmp_percent:+.0f}% across {source_count} source(s)")
        earned += GMP_WEIGHT * share

    # A part-filled book is discounted rather than read as a final result, so an
    # issue in its first hours is not marked down for demand that has not been
    # placed yet. Both the weight and the confidence scale, which keeps an early
    # reading from dominating the premium it is meant to corroborate.
    settled = max(0.0, min(1.0, progress))

    total = (subscription or {}).get("total")
    if is_finite(total):
        available += SUBSCRIPTION_WEIGHT * settled
        confidence += SUBSCRIPTION_CONFIDENCE * settled
        earned += SUBSCRIPTION_WEIGHT * settled * max(0.0, min(1.0, total / 10))
        if settled < 1.0:
            reasons.append(f"subscribed {total:.2f}x so far, book still open")
        else:
            reasons.append(f"subscribed {total:.2f}x overall")

    qib = (subscription or {}).get("qib")
    if is_finite(qib):
        available += QIB_WEIGHT * settled
        confidence += QIB_CONFIDENCE * settled
        earned += QIB_WEIGHT * settled * max(0.0, min(1.0, qib / 5))
        reasons.append(f"QIB {qib:.2f}x")

    if available <= 0:
        return {
            "flag": "grey",
            "score": None,
            "label": "No signal",
            "reason": "No GMP or subscription data published yet.",
        }

    strength = max(0.0, min(100.0, earned / available * 100))
    confidence = max(0.0, min(1.0, confidence))
    score = NEUTRAL_SCORE + (strength - NEUTRAL_SCORE) * confidence

    if score >= 60:
        flag, label = "green", "Positive"
    elif score >= 35:
        flag, label = "amber", "Mixed"
    else:
        flag, label = "red", "Weak"

    if confidence < MIN_CONFIDENCE_FOR_VERDICT and flag != "amber":
        direction = "leaning positive" if score >= NEUTRAL_SCORE else "leaning weak"
        flag, label = "amber", "Indicative"
        reasons.append(f"too little corroboration to call, {direction}")

    if not is_open and not is_finite(total):
        reasons.append("bidding not open yet")

    return {
        "flag": flag,
        "score": round2(score),
        "confidence": round2(confidence * 100),
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
        if not is_equity_row(row):
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
    fallback_subscriptions = fetch_subscription_fallback() if open_rows else []
    detail_results = settle_map(open_rows, lambda item: fetch_issue_detail(item["symbol"], item["series"]), 6)
    bids_by_symbol = {}
    profiles_by_symbol = {}
    for item, (ok, value) in zip(open_rows, detail_results):
        detail = value if ok and isinstance(value, dict) else {}
        bids_by_symbol[item["symbol"]] = detail.get("subscription") or {}
        profiles_by_symbol[item["symbol"]] = detail.get("profile") or {}

    rows = []
    for item in candidates:
        is_open = item.pop("_isOpen")
        open_date = item.pop("_openDate")
        close_date = parse_date(item.get("closeDate"))

        subscription = dict(bids_by_symbol.get(item["symbol"]) or {})
        if not is_finite(subscription.get("total")) and is_finite(item.get("overallSubscription")):
            subscription["total"] = round2(item["overallSubscription"])
        # Only consulted once the exchange has come up empty, and only for an
        # issue that is actually taking bids.
        if not is_finite(subscription.get("total")) and is_open:
            subscription = subscription_from_fallback(item["company"], item.get("board"), fallback_subscriptions) or subscription
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
                "profile": profiles_by_symbol.get(item["symbol"]) or None,
                "recommendation": upcoming_flag(
                    gmp, subscription, is_open, bidding_progress(open_date, close_date, today)
                ),
                "analysisSymbol": f"{item['symbol']}.NS",
                "_sortKey": (0 if is_open else 1, open_date or today),
            }
        )

    rows.sort(key=lambda item: item.pop("_sortKey"))
    return rows


def gmp_field_votes(company, quotes, key):
    """Every value the aggregators publish for one company's ``key``.

    At most one row per source votes, so a source that lists an issue twice
    cannot outweigh the others.
    """
    votes = []
    for rows in quotes.values():
        for row in rows:
            if names_match(company, row["company"]) and row.get(key) is not None:
                votes.append(row[key])
                break
    return votes


def gmp_field_consensus(company, quotes, key):
    """Majority value for ``key``, or ``None`` if no source published one."""
    votes = gmp_field_votes(company, quotes, key)
    if not votes:
        return None
    return Counter(votes).most_common(1)[0][0]


def build_pipeline_from_gmp(quotes, today, issue_terms=None):
    """Pipeline rows assembled from the grey-market aggregators alone.

    The aggregators publish a premium and little else, so ``issue_terms`` - the
    price band and issue size republished by Chittorgarh - is merged in where it
    matches by name. It matters beyond filling two columns: the trackers quote a
    premium in rupees, and turning that into a percentage or an expected listing
    price needs the cap price, which only the terms carry.

    Status, board and dates are recovered from the aggregators themselves,
    which publish all three. Status matters most: their pages keep listing an
    issue after it closes, and without it a company that has already listed
    would appear here as an investable pipeline row carrying the stale premium
    it had on listing day.
    """
    companies = []
    for rows in quotes.values():
        for row in rows:
            company = clean_text(row.get("company"))
            # Sources spell the same issue differently, so a name is new only
            # when it matches nothing already collected.
            if company and not any(names_match(company, seen) for seen in companies):
                companies.append(company)

    # With NSE gone this is the only subscription there is, so unlike the
    # exchange path it is consulted for every row rather than as a last resort.
    fallback_subscriptions = fetch_subscription_fallback()

    pipeline = []
    for company in companies:
        status = gmp_field_consensus(company, quotes, "status")
        open_date = gmp_field_consensus(company, quotes, "openDate")
        close_date = gmp_field_consensus(company, quotes, "closeDate")

        # An explicit "Closed" is trusted; otherwise a window that has already
        # ended says the same thing. A row with neither is kept, because the
        # sources that publish no status at all are the majority.
        if status == "Closed" or (close_date and close_date < today):
            continue
        if not status and open_date:
            status = "Open" if open_date <= today else "Upcoming"

        terms = (issue_terms or {}).get(normalize_name(company)) or {}
        # The aggregators' own band is preferred where they publish one, because
        # it is the band their premium was quoted against.
        band_high = gmp_band_high(company, quotes)
        if not is_finite(band_high):
            band_high = terms.get("priceBandHigh")
        gmp = gmp_consensus(company, quotes, band_high)
        if not gmp:
            continue

        board = gmp_field_consensus(company, quotes, "board") or terms.get("board")
        subscription = subscription_from_fallback(company, board, fallback_subscriptions)

        pipeline.append(
            {
                "symbol": terms.get("symbol") or "",
                "company": company,
                "board": board,
                "status": status,
                "openDate": iso_date(open_date),
                "closeDate": iso_date(close_date),
                "priceBandLow": terms.get("priceBandLow"),
                "priceBandHigh": round2(band_high) if is_finite(band_high) else terms.get("priceBandHigh"),
                # ``issueSize`` is a share count everywhere else, which is what
                # NSE's offer feed reports and what the UI labels it. Chittorgarh
                # publishes the rupee value instead, so it gets its own field:
                # put in the shared one it rendered "44.87 shares" for an issue
                # of 44.87 crore.
                "issueSize": None,
                "issueSizeCrore": terms.get("issueSizeCrore"),
                "termsSource": "Chittorgarh" if terms else None,
                "subscription": subscription,
                "gmp": gmp,
                "recommendation": upcoming_flag(
                    gmp, subscription, status == "Open", bidding_progress(open_date, close_date, today)
                ),
                "analysisSymbol": f"{terms['symbol']}.NS" if terms.get("symbol") else None,
                "source": "gmp",
            }
        )

    # Open issues first, then by soonest open date, then by premium - the same
    # priority build_pipeline uses, degraded to the keys that survive here.
    pipeline.sort(
        key=lambda row: (
            0 if row["status"] == "Open" else 1,
            row["openDate"] or "9999",
            -(row["gmp"].get("percent") or row["gmp"].get("value") or 0),
        )
    )
    return pipeline


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

NSE_FEED_KEYS = ("upcoming", "current", "past")


def nse_failure_reason(feed_errors):
    """What NSE actually said, collapsed to one line.

    The three feeds nearly always fail the same way, so the distinct reasons are
    reported rather than one per feed. An empty feed raises nothing, hence the
    fallback: it is a real state and reads differently from a refused request.
    """
    reasons = []
    for key in NSE_FEED_KEYS:
        reason = clean_text(feed_errors.get(key), 160)
        if reason and reason not in reasons:
            reasons.append(reason)
    return "; ".join(reasons) if reasons else "the feeds returned no issues"


def nse_failure_message(feed_errors):
    return (
        f"NSE India IPO feeds are unavailable ({nse_failure_reason(feed_errors)}), "
        "and no grey-market source responded either. Please retry in a moment."
    )


# BSE's absolute bid quantities reconcile with the other sources on some issues
# and come in far below them on others, which suggests it publishes its own book
# rather than the combined one for part of the market. The proportions within
# QIB survive that either way, so the split is trusted for shape and checked for
# scale: where BSE's own QIB multiple disagrees with the subscription figure the
# tab already shows, the row says so instead of quietly presenting both.
BSE_QIB_AGREEMENT_TOLERANCE = 0.25


def bse_split_disagrees(bse_times, reported_times):
    if not is_finite(bse_times) or not is_finite(reported_times) or reported_times <= 0:
        return False
    return abs(bse_times - reported_times) / reported_times > BSE_QIB_AGREEMENT_TOLERANCE


def enrich_pipeline_details(pipeline):
    """Fill the two fields the NSE issue feeds never carry between them.

    Sector is absent from every NSE feed, so it was missing in every environment
    rather than only when NSE was down. The QIB split is NSE-only, so it goes
    missing exactly when NSE does. They are fetched together because both are
    per-company lookups over the same handful of rows.
    """
    if not pipeline:
        return []

    companies = [row.get("company") for row in pipeline]
    # An issue that has not opened has no book, so asking BSE about it would
    # spend a request to be told what the row already knows.
    needs_split = [
        row.get("company")
        for row in pipeline
        if row.get("status") != "Upcoming" and not (row.get("profile") or {}).get("institutional")
    ]

    extra = settle_named_loaders(
        {
            "sectors": lambda: scrape_sectors_chittorgarh(companies),
            "institutional": lambda: scrape_institutional_bse(needs_split),
        },
        concurrency=2,
    )
    sectors = extra.get("sectors") or {}
    splits = extra.get("institutional") or {}

    for row in pipeline:
        key = normalize_name(row.get("company"))
        if sectors.get(key):
            row["sector"] = sectors[key]
            row["sectorSource"] = "Chittorgarh"

        found = splits.get(key)
        if not found:
            continue
        profile = dict(row.get("profile") or {})
        profile["institutional"] = found["institutional"]
        profile["institutionalSource"] = "BSE"
        if bse_split_disagrees(found.get("qibTimes"), (row.get("subscription") or {}).get("qib")):
            profile["institutionalNote"] = (
                "BSE's own QIB multiple differs from the subscription figure above, so treat "
                "the shares bid as BSE's book and the proportions as the reliable part."
            )
        row["profile"] = profile

    filled = []
    if sectors:
        filled.append("sector (via Chittorgarh)")
    if splits:
        filled.append("institutional demand (via BSE)")
    return filled


def build_ipo_dashboard():
    today = datetime.now(IST).date()

    # Every upstream this tab needs is independent, so they all go out at once.
    # Run in sequence, the slowest grey-market source would be added to the NSE
    # and OFS time rather than overlapped with it.
    feed_errors = {}
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
        errors=feed_errors,
    )
    upcoming_issues = feeds.get("upcoming") or []
    current_issues = feeds.get("current") or []
    past_issues = feeds.get("past") or []

    # settle_named_loaders reports a crashed loader as {}, which unpacks to no
    # quotes and every source marked failed - the same state as all three being
    # down, which is what it means.
    gmp_result = feeds.get("gmp")
    quotes, failed_sources = gmp_result if isinstance(gmp_result, tuple) else ({}, [name for name, _ in GMP_SOURCES])

    nse_down = not upcoming_issues and not current_issues and not past_issues
    if nse_down and not quotes:
        raise RuntimeError(nse_failure_message(feed_errors))

    notes = []
    if nse_down:
        # The grey-market trackers carry a premium and nothing else - no symbol,
        # no listing history, no OFS, no price band. Chittorgarh republishes all
        # of those from the same exchange filings, so it stands in for NSE rather
        # than the tab losing whole tables. Every row it supplies is attributed.
        fallbacks = settle_named_loaders(
            {
                "listed": scrape_recently_listed_chittorgarh,
                "terms": scrape_issue_terms_chittorgarh,
                "ofs": lambda: scrape_ofs_chittorgarh(today),
            },
            concurrency=3,
        )
        recently_listed = [
            dict(row, source="Chittorgarh")
            for row in build_recently_listed(fallbacks.get("listed") or [], today)
        ]
        pipeline = build_pipeline_from_gmp(quotes, today, fallbacks.get("terms") or {})
        ofs_rows = fallbacks.get("ofs") or []
        ofs = {
            "available": bool(ofs_rows),
            "rows": ofs_rows,
            "source": "Chittorgarh" if ofs_rows else None,
            "note": (
                "NSE is not responding; these offers come from Chittorgarh, which "
                "reports one offer date rather than an open and close pair."
                if ofs_rows
                else "NSE is not responding and no fallback listed an OFS in this window."
            ),
        }

        # Name the columns actually lost, rather than declaring whole tables
        # unavailable while a fallback is visibly filling them in.
        subscription_source = next(
            (row["subscription"]["source"] for row in pipeline
             if (row.get("subscription") or {}).get("source")),
            None,
        )
        filled = [
            label for label, present in (
                ("listing history", bool(recently_listed)),
                ("OFS", bool(ofs_rows)),
                ("price bands and issue size", bool(fallbacks.get("terms"))),
                (f"subscription (via {subscription_source})", bool(subscription_source)),
            ) if present
        ]
        notes.append(
            "NSE India is not responding, so "
            + (
                f"{', '.join(filled)} are coming from Chittorgarh instead. "
                if filled
                else "listing history, issue dates and OFS are unavailable. "
            )
            + f"Issue dates and symbols may be incomplete. ({nse_failure_reason(feed_errors)})"
        )
    else:
        recently_listed = build_recently_listed(past_issues, today)
        pipeline = build_pipeline(upcoming_issues, current_issues, quotes, today)
        ofs = build_ofs(today, feeds.get("ofsActive"), feeds.get("ofsForthcoming"), feeds.get("ofsPast"))

    # Runs on both paths: sector is missing from NSE's feeds whether or not NSE
    # is answering, so this is not part of the outage branch.
    enriched = enrich_pipeline_details(pipeline)
    if enriched:
        notes.append(f"NSE publishes neither field, so {' and '.join(enriched)} are filled from elsewhere.")

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
        "nseAvailable": not nse_down,
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
            f"{', '.join(name for name, _ in GMP_SOURCES)} (GMP consensus)"
            if nse_down
            else (
                "NSE India (issues, subscription, OFS) - Yahoo Finance (listing/current price) - "
                f"{', '.join(name for name, _ in GMP_SOURCES)} (GMP consensus)"
            )
        ),
    }
