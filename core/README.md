# core - shared app

`core` is the general/shared Django app. Everything reused across tabs lives
here: the analysis engine, the database models, request helpers, the page
shells, and the cross-cutting JSON APIs. The five per-tab apps
(`stock_analysis`, `recommendations`, `market_monitor`, `etf_analysis`,
`mutual_funds`) all import from `core`.

## Files

| File | What it is for |
|------|----------------|
| `services.py` | The analysis engine (large). All market-data fetching and report building: data providers (Yahoo, SEC, Screener, NSE, Moneycontrol), technical indicators, fundamentals/ownership, stock + asset reports, recommendations, market monitor, caching. No Django request code. |
| `models.py` | Database models: `TradeReference`, `WatchlistItem`, `StockSearchLog`. Each has `as_dict()` for JSON output. |
| `view_helpers.py` | Request/form helpers: search logging, client IP, device detection, JSON body parsing, and save/validate helpers for the CRUD APIs. |
| `asset_api.py` | Shared `search_asset` / `analyze_asset` used by the ETF and mutual-fund tabs (the type is passed in). |
| `views.py` | The `home` landing page and `index` workspace shell, plus the cross-cutting APIs: search logs, watchlist CRUD, trade-reference CRUD. |
| `urls.py` | Routes for `/` (home), `/app` (workspace), and the cross-cutting `/api/...` endpoints. |
| `tests.py` | Unit + integration tests for the engine and APIs. Run `python manage.py test`. |
| `apps.py` | Django app config (`name = "core"`). |
| `migrations/` | Auto-generated schema migrations for the three models. |
| `templates/core/base.html` | The workspace shell (sidebar, hero search, includes each tab partial). |
| `templates/core/home.html` | The landing page (background image, single search box, top picks). |

## Endpoints owned here

- `GET /` - landing page
- `GET /app` - workspace shell
- `GET /api/search-logs` - recent analyze/search audit log
- `GET|POST /api/watchlist`, `PATCH|DELETE /api/watchlist/<id>`
- `GET|POST /api/trade-references`, `PATCH|DELETE /api/trade-references/<id>`

## How a request flows

1. A tab app view (e.g. `stock_analysis.views.analyze`) receives the request.
2. It calls into `core.services` to fetch data and build a result dict.
3. It returns `JsonResponse(...)`; `core.view_helpers.record_stock_search`
   logs the request.
4. The browser (`public/app.js`) renders the JSON into the matching tab.
