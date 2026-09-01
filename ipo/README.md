# ipo - IPO Radar tab

Owns the IPO tab: issues that listed in the last 7 days with their actual
outcome, issues open now or opening within 7 days with grey-market premium and
live subscription, and OFS issues that are live, scheduled, or completed in the
last 7 days.

Unlike the other tabs, the data layer lives here in `services.py` rather than in
`core.services`, following the `agent_desk/agents.py` precedent - the scraping
and consensus logic is specific to this tab. Shared helpers (`cached`,
`fetch_nse_json_with_session`, `fetch_chart_range`, `settle_map`) are imported
from `core.services`.

## Files

| File | What it is for |
|------|----------------|
| `services.py` | All data collection: NSE issue/subscription feeds, the three-source GMP consensus, listing/current price enrichment, and flag scoring. |
| `views.py` | `ipo` endpoint. Supports `?refresh=1`. |
| `urls.py` | Route `/api/ipo`. |
| `apps.py` | Django app config. |
| `tests.py` | Parsing, name matching, GMP consensus, flag scoring, section assembly, endpoint. |
| `templates/ipo/tab.html` | The three tables. Included by `core/base.html`. |

## Endpoints

- `GET /api/ipo` - cached dashboard (10 minute TTL, keyed by IST date)
- `GET /api/ipo?refresh=1` - clear the cache and rebuild

## Data sources

| Field | Source |
|-------|--------|
| Issue price, price band, dates, issue size | NSE `/api/all-upcoming-issues?category=ipo` |
| Live subscription (overall) | NSE `/api/ipo-current-issue` |
| Subscription by QIB / NII / Retail | NSE `/api/ipo-detail?symbol=&series=` |
| Subscription when NSE is unreachable | Chittorgarh report 21 JSON, then IPO Central `wp-json` (fallbacks, attributed in the UI) |
| Issue profile (issue size text, type, face value, bid lot, lead managers, registrar) | NSE `/api/ipo-detail`, `issueInfo.dataList` |
| FII / DII / mutual-fund split of the QIB book | NSE `/api/ipo-detail`, `bidDetails` rows `1(a)`-`1(d)` |
| Listing date, final issue price | NSE `/api/public-past-issues` |
| Listing price, current price | Yahoo Finance chart (first bar's open, latest close) |
| GMP, expected listing price | IPO Ji, IPO Watch, IPO Premium, IPO Central, IPO360 (scraped; median across them) |
| Live OFS floor price, LTP, subscription by category | NSE `/api/live-ofs-active-issues` |
| Scheduled OFS | NSE `/api/all-upcoming-issues?category=forthcoming` |
| Completed OFS, allotment price | NSE `/api/live-ofs-past-issues` |

## Things worth knowing before changing this

- **Subscription has two fallbacks, and neither is a peer of the exchange.** When
  NSE is unreachable the subscription column has no source at all, which is what
  the live deployment sees. `fetch_subscription_fallback` tries Chittorgarh's
  report 21 first and IPO Central's `wp-json` page second, and both are consulted
  only when NSE gave nothing. Any row that uses one carries `subscription.source`
  and the UI prints "via <source> · <reading time>". Unlike NSE, both publish a
  category split for SME issues.
- **The fallback order was measured, not assumed.** On Lumino Industries: NSE
  104.69x overall and 211.25x QIB, Chittorgarh 124.02x / 232.79x (a later
  reading, consolidated across both exchanges), IPO Central 7.91x / 33.66x -
  understated more than tenfold. Do not reorder these without re-measuring
  against NSE on a live issue.
- **Chittorgarh's URL shape is the whole trick.** The path segments are
  `report/page/month/year/financialYear/sort/segment/subSegment`, and `segment`
  must be the literal `mainboard` or `sme`. Passing `0` there returns
  `{"msg": -1, "error": "No params data found."}`, which reads like a dead
  endpoint. Mainboard and SME are separate calls. The company cell is a link
  wrapped in status badges, so only the anchor text is the name - taking the
  whole cell yields "Lumino Industries Ltd. CT", which matches nothing.
- **Chittorgarh splits NII into small and big.** The combined `NII (x)` column is
  the one used, because that is the figure NSE reports and the two must be
  comparable when the source switches.
- **One request per open issue serves two purposes.** `fetch_issue_detail` parses
  the subscription split and the expandable profile out of the same
  `/api/ipo-detail` response. Splitting them into two functions would double the
  round trips against the slowest upstream in the tab.
- **The QIB sub-categories have no times-subscribed figure.** NSE reports only
  shares bid for FIIs, domestic institutions and mutual funds, so they are shown
  as a share of the QIB book. Presenting them as a multiple would be inventing a
  denominator.
- **Sector is not available before listing.** It appears in neither the issue
  feeds nor `issueInfo`; only the RHP has it. The detail panel says so rather
  than omitting the field.
- **GMP has no official source.** Five aggregators are scraped from raw HTML. If
  a scraper's table layout changes, `collect_gmp_quotes` drops that source and
  reports it in `notes` rather than failing the request.
- **Five sources, because they disagree and they go down.** Two sources that
  disagree give you no way to tell which is the outlier. Rejected after testing:
  Chittorgarh, InvestorGain, Finology and IPO Hub render their tables
  client-side, so nothing usable arrives in the response body; IPO Track serves a
  table but leaves GMP empty for live issues; IPO Market emits duplicate
  mobile/desktop tables; Chanakya NiPothi mis-encodes company names and quotes
  floor rather than cap prices.
- **IPO Central is read through `wp-json`, not the page.** The REST endpoint
  returns the same post body in 31 KB instead of 488 KB, since it omits the site
  chrome. The body is ordinary table markup, so it reuses `parse_html_tables`.
- **A grey-market tracker going down must not slow the tab.** These are
  third-party WordPress sites that fail without warning; IPO Watch has sat behind
  a Cloudflare 522 for hours. Three things bound the damage: `GMP_FETCH_TIMEOUT`
  is 8s rather than the 20s the exchange feeds get, `scrape_gmp_source` caches a
  success for 10 minutes, and a failure puts that source in a cooldown so a
  rebuild does not pay the timeout again to rediscover it is still down. The
  cooldown deliberately survives `?refresh=1` - a 522 does not clear in the time
  it takes to click refresh, and making the user wait out the timeout to find
  that out helps nobody.
- **The cooldown doubles per consecutive failure, 1 minute to 10.** These sources
  fail two different ways. IPO Watch flaps - measured over 22 requests it
  answered 8, and a healthy response arrives in under a second - so a flat
  multi-minute ban would sit out recoveries that were one retry away. A dead host
  should be backed off hard instead. Growth separates the two without needing to
  know which is which, and a success resets the count.
- **NSE latency is erratic, not slow.** The same endpoint answers in 0.2s or
  stalls for 20, and it is not a concurrency effect - measured back to back, the
  same call pattern ran 1.7s once and 20s the next time. So the defence is a
  bounded budget, not a tuned pool size. `fetch_bid_details` takes
  `timeout=BID_DETAIL_TIMEOUT, attempts=2` because it only splits a total the
  pipeline already has; the primary feeds keep the full retry budget.
- **All upstreams are fetched in one pool.** `build_ipo_dashboard` issues the
  three NSE issue feeds, the three OFS feeds, and the GMP scrape together. Run in
  sequence these summed to ~28s in practice; overlapped they cost roughly the
  slowest one.
- **Sources disagree, sometimes a lot, so the consensus is a median.** Not a
  mean: measured across one session, IPO Ji sat below the other four on every
  single issue, and on Rays of Belief quoted 14 where the rest said 48, 48 and
  50 - a mean published 40, a premium no source quoted. `low`, `high`,
  `spreadPercent` and an `agreement` grade ride alongside, so the outlier stays
  visible rather than being smoothed away.
- **`agreement` is judged against the median's magnitude, never a mean.**
  Dividing by a mean broke on two real shapes: quotes straddling zero averaged to
  zero and reported as `high` (the most contradictory case possible), and an
  all-negative set gave a negative denominator that inverted the scale. Sources
  that disagree on the sign are graded `low` before any arithmetic runs.
- **Company names differ across all four feeds.** `normalize_name` strips
  suffixes, board tags and punctuation; `names_match` requires the first two
  significant tokens to agree so that "Priority Jewels" does not match
  "Priority Technologies".
- **NSE omits the price band for SME issues** in its live feeds, so it is
  borrowed from whichever aggregator reports one (`gmp_band_high`). Without a
  cap price there is no GMP percentage and no expected listing price.
- **NSE's past-issues feed mixes bonds and NCDs in with equity, and
  `securityType` alone does not catch them.** NSE files Vision Infra's 11.50%
  2030 NCD as `SME`, so it reached the listings table as an issue priced at
  1,00,000 rupees next to a price band of 155-163. `is_equity_row` also rejects
  debt-series symbols - coupon, issuer, maturity year, as in `1150VIES30` - and
  face-value issue prices far above the stated band. Equity tickers that open
  with a digit (`5PAISA`, `63MOONS`, `20MICRONS`) do not also close with one,
  which is what keeps them.
- **Scraped HTML is untrusted.** `clean_text` strips markup and caps length on
  every string, numbers are coerced through `to_number`, and no provider URL is
  passed to the client. The frontend escapes everything again before it reaches
  `innerHTML`.
- **The OFS category is `forthcoming`, not `ofs`.** `all-upcoming-issues?category=ofs`
  is a dead parameter that answers with an empty *object*; a live category answers
  with a list, which is how the two are told apart. The OFS board is assembled
  from three feeds: `/api/live-ofs-active-issues`, `/api/all-upcoming-issues?category=forthcoming`,
  and `/api/live-ofs-past-issues` (an archive back to 2012, trimmed to 7 days).
- **One OFS spans two sessions.** Non-retail bids on day one and retail on day
  two, and NSE reports each as its own series row. `build_active_ofs` and
  `build_recent_ofs` merge them into a single window per company; the active feed
  keys its row list under `data` on one group and `rows` on another, so both are
  read.
- **Archive symbols are not tradable.** The OFS archive tags rows with the offer
  series (`HINDCOPPERCUMU`), so `ofs_base_symbol` strips the suffix before the
  symbol reaches the analysis button.

## The flag

`upcoming_flag` scores GMP percentage (55), overall subscription (30), and QIB
participation (15) over whichever components have data, then pulls the result
toward 50 by however much evidence is missing. Green is 60+, amber 35-59, red
below 35, and grey means nothing has been published yet.

The shrink matters, because renormalising alone conflated two different things:
how good the signals look, and how much signal there is. A single unverified
grey-market quote scored a perfect 100 - identical to an issue whose premium was
confirmed by 25x subscription and 6x QIB demand. Certainty about a fifth of the
evidence was published as certainty about all of it.

Three inputs set that confidence, and it is deliberately **not** derived from the
scoring weights above, which measure prediction rather than reliability:

- **How many trackers quote the premium.** One is a rumour, four agreeing is
  closer to a measurement (`GMP_CORROBORATION`).
- **How far through its bidding window the issue is** (`bidding_progress`).
  Indian books fill on the last day, so an early figure is incomplete, not weak.
  Rays of Belief was flagged red at 0.02x within an hour of opening, on the same
  footing as an issue that had failed to fill over three days.
- **Whether the figures come from the exchange at all.** Subscription is real
  money bid; GMP is an unregulated market with no published trades. Deriving
  confidence from the scoring weights made 25x subscribed with 6x QIB count for
  less than four websites agreeing on a number.

Below `MIN_CONFIDENCE_FOR_VERDICT` the flag reads **Indicative** and states no
verdict in either direction - one source quoting a steep discount is no more
conclusive than one quoting a steep premium. A full grey-market consensus and a
full set of exchange bidding figures each clear that bar alone, so a forthcoming
issue can still be called; it just needs corroboration rather than one site's
say-so.

`listed_flag` scores an already-listed issue on how it has actually traded:
green if it holds 10%+ above its issue price, red if it is at or below issue,
amber in between.

`ofs_flag` scores discount (65) and demand (35). The discount is the point of an
OFS - shares are offered below market - so how far the market trades above the
floor is the headline, and subscription is the confirming signal, because an
offer that clears many times over usually prices well above the floor and erodes
that discount. Completed and not-yet-open offers are grey rather than scored.
