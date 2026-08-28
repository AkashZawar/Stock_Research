# etf_analysis - ETF Analysis tab

Owns the ETF research tab. Views are thin wrappers over `core.asset_api`
(asset type "etf"); all logic lives in `core.services`.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `search` / `analyze` delegating to `core.asset_api` with type "etf". |
| `urls.py` | Routes `/api/etf/search` and `/api/etf/analyze`. |
| `apps.py` | Django app config. |
| `templates/etf_analysis/tab.html` | ETF search form, default shortlist, and report panels. Included by `core/base.html`. |

## Endpoints

- `GET /api/etf/search?q=<text>` - ETF suggestions
- `GET /api/etf/analyze?symbol=<symbol>` - full ETF report
- Deep link: `/app?tab=etf&symbol=GOLDBEES.NS`

## Notes

- `ETF_UNIVERSE` in `core.services` is the curated fallback list. Every symbol
  is verified to return a usable 2y daily history, because a symbol the picker
  offers but `analyze` rejects is worse than one it never offered. Delisted
  (`ICICINIFTY.NS`) and stub-history (`NIFTYIETF.NS`, `AXISNIFTY.NS`) tickers
  were removed for that reason.
- Curated names are preferred over provider names, which for Indian ETFs are
  often internal codes (`HDFCAMC - HDFCNIFTY`) or long legal names.
- Tags ("Nifty Next 50", "Gold") are scored during search, so a thematic query
  finds the fund that tracks it even when the name never says so.
- A real ETF with too little history returns 400 with an explanation
  (`INSUFFICIENT_HISTORY_PREFIX`) rather than a generic 500.
- A close older than `MAX_ASSET_STALE_DAYS` is flagged in the report
  (`freshness`) and lowers the confidence score.
- A symbol that parses but returns no data (`TATAMOTORS.NS` since the demerger)
  is reported as such and is removed from its own suggestion list, because the
  obvious next click used to reproduce the same error.

See [`mutual_funds/README.md`](../mutual_funds/README.md#how-the-numbers-are-measured)
for the return, risk, and benchmark methodology, which is shared with this tab.
Two points are ETF-specific:

- Tracking error and beta are measured on the **traded price**, which drifts from
  NAV on premium, discount, and thin sessions, so they read higher than the
  NAV-based figures an AMC publishes. The payload note says so.
- Thin ETFs price non-synchronously with the index (today's close may be an older
  last trade), which biases daily beta toward zero and inflates tracking error -
  NIFTYBEES showed beta 0.89 and 3.05% that way. Weekly sampling absorbs a day of
  lag and gives 0.95 and 1.67%.
