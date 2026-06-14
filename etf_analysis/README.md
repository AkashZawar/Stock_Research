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
