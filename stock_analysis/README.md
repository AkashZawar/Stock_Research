# stock_analysis - Stock Analysis tab

Owns the main stock research tab. All analysis logic lives in `core.services`;
this app only exposes the endpoints and the tab UI.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `search` (suggestions) and `analyze` (full report). Calls `core.services`; logs requests via `core.view_helpers.record_stock_search`. |
| `urls.py` | Routes `/api/search` and `/api/analyze`. |
| `apps.py` | Django app config. |
| `templates/stock_analysis/tab.html` | The tab markup (quote, scores, chart, levels, swing plan, detail panels). Included by `core/base.html`. |

## Endpoints

- `GET /api/search?q=<text>` - ticker/name suggestions
- `GET /api/analyze?symbol=<symbol>` - full research report (also writes a search log)
