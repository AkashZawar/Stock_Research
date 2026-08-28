# mutual_funds - Mutual Funds tab

Owns the mutual-fund research tab. Views are thin wrappers over
`core.asset_api` (asset type "mutual-fund"); all logic lives in
`core.services`.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `search` / `analyze` delegating to `core.asset_api` with type "mutual-fund". |
| `urls.py` | Routes `/api/mutual-funds/search` and `/api/mutual-funds/analyze`. |
| `apps.py` | Django app config. |
| `templates/mutual_funds/tab.html` | Fund search form, default shortlist, report and annual-returns panels. Included by `core/base.html`. |

## Endpoints

- `GET /api/mutual-funds/search?q=<text>` - scheme suggestions
- `GET /api/mutual-funds/analyze?symbol=<symbol>` - full fund report
- Deep link: `/app?tab=fund&symbol=Parag%20Parikh%20Flexi%20Cap`

## Notes on Indian scheme data

The data provider is awkward for Indian mutual funds, and the tab works around
it in `core.services`:

- Schemes are keyed by opaque ids (`0P0000YWL1.BO`) and the provider mirrors
  that id into `shortName`, so a name equal to the symbol is rejected
  (`display_name`) and `longName` from the chart endpoint is used instead.
- The `quote` and `quoteSummary` endpoints answer 401 for anonymous callers, so
  expense ratio, AUM, yield, and inception date are frequently unavailable. The
  payload flags this (`profile.detailsAvailable`) so the tab reports "Not
  published" once rather than showing a permanent loading spinner with an ETA for
  data that will never arrive. The NAV row falls back to the latest close, which
  always loads. Everything in the return, risk, and benchmark panels is computed
  from price history and is unaffected.
- Every scheme lists one entry per plan, so search ranks Direct Growth plans
  above Regular and IDCW variants (`fund_plan_rank`).
- Resolution requires each significant word of the query to appear in the
  matched fund (`asset_match_is_plausible`). A scheme the provider does not
  carry reports as not found with suggestions rather than quietly resolving to
  a different fund.
- `MUTUAL_FUND_UNIVERSE` seeds popular Direct Growth plans so common searches
  work without depending on the provider's search index. Every entry is
  verified to return a usable daily history.

## How the numbers are measured

Shared with the ETF tab. History is `ASSET_HISTORY_RANGE` (10 years), long enough
for a 5-year CAGR, a rolling-return distribution, and a full market cycle
including the 2020 drawdown.

**Price basis.** Returns, risk, and the benchmark comparison run on a
corporate-action-safe series (`build_asset_return_series`); price, moving
averages, and support/resistance stay on the traded close, with levels limited to
the last `ASSET_LEVEL_WINDOW_SESSIONS` so a decade-old level is not quoted as
current. The adjusted close is used when the provider actually adjusted (for
Indian ETFs it is often just a copy of the close, and the payload says which).
Two provider faults are then separated, because the right repair differs:

- **Glitch spikes** - a few sessions on a different divisor that revert.
  NIFTYBEES has two such rows in December 2019 (129 -> 13 -> 129). The bad rows
  are dropped and the series stitched. Truncating at the return leg instead
  discarded 815 earlier sessions for the sake of two rows.
- **Level shifts** - a step that does not revert, i.e. a real unadjusted split.
  The scale cannot be recovered, so history before it is discarded and the
  payload reports the cut date.

Without this a decade of NIFTYBEES showed a 90% "drawdown" and a 1013% best
rolling year that were purely a split divisor.

**Rolling returns.** A single 1Y figure is one sample dominated by its start date.
The distribution of every rolling one-year window (median, quartiles, worst, best,
share finishing positive) is what says whether a fund is consistent.

**Risk-adjusted.** Sharpe and Sortino over the trailing
`ASSET_RISK_WINDOW_SESSIONS`, against a currency-specific risk-free rate
(`ASSET_RISK_FREE_RATES`). These are stable reference levels rather than live
quotes, and every payload states the rate it used.

**Versus the market reference.** Excess CAGR, beta, tracking error (or active
risk, when the fund does not track the reference), and up/down capture. Two
corrections matter here:

- The reference series are **price** indices while a Growth-plan NAV accumulates
  dividends, so the reference is grossed up by `INDEX_DIVIDEND_YIELDS` before the
  excess is taken. Without it every equity fund clears its benchmark by roughly
  the index yield for free - NIFTYBEES, a plain Nifty 50 tracker charging a fee,
  showed +1.27pp a year, which is impossible. It now reads +0.01pp.
- Beta and tracking error use **weekly** returns, and fund and index closes are
  matched by date rather than by position, so a missing NAV day cannot offset the
  two series.

**Suitability.** Weighs risk-adjusted return, rolling-return consistency, risk,
cost, and momentum. Data confidence and freshness are reported alongside it
rather than averaged into it, because a poorly documented fund is not the same
thing as a poor fund; stale data marks the score indicative without changing it.
