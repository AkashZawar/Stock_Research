# core - shared app

`core` is the general/shared Django app. Everything reused across tabs lives
here: the analysis engine, the database models, request helpers, the page
shells, and the cross-cutting JSON APIs. The six per-tab apps
(`stock_analysis`, `agent_desk`, `recommendations`, `market_monitor`,
`etf_analysis`, `mutual_funds`) all import from `core`.

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

## Fetching and caching

Every report needs several independent upstream calls, so they are issued
together rather than one after another - a report is only as slow as its slowest
provider. `_analyze_symbol` and `_analyze_asset` fan out with a
`ThreadPoolExecutor`; `settle_map` does the same for a list and preserves input
order, so callers can merge results deterministically.

Notes on the cost of each provider, which is why the code is shaped this way:

- **NSE option chain** is the slowest leg of a stock report. It needs one
  contract-info call plus one call per expiry, and NSE rejects requests without
  the cookies its homepage sets. The cookie jar is therefore shared through
  `nse_session()` instead of being rebuilt per call, and the expiry legs run
  concurrently. Together those took the open-interest leg from about 3.2s to
  about 0.9s, and a cold stock report from 4-6s to under 2s. A single failed
  expiry is skipped rather than failing the chain.
- **Yahoo `quote` and `quoteSummary`** answer 401 to anonymous callers. They are
  still attempted, because access may come back, but a 401 is remembered per
  endpoint (`note_unauthorized`) and short-circuited for
  `UNAUTHORIZED_ENDPOINT_TTL_SECONDS` so the app stops spending request budget
  on a result that cannot change. This matters because exhausting that budget
  earns a 429 on the `chart` endpoint, which *does* work and which every report
  depends on. `endpoint_family` strips the symbol so the memo is per route, not
  per symbol.
- **A remembered 401 stops the waste but does not fill the gap**, so `get_quote`
  and `get_quotes` fall back to `quote_from_chart_meta`, which reads the same
  last price, day move and range, 52-week range, volume and names out of the
  `chart` endpoint's `meta` block under different key names. Nothing there is
  estimated. Without it the commodity-impact table published `price: null` for
  every stock outside the breakout watchlist - Hindalco, Tata Steel and Indigo
  among them - and the UI rendered that as a loading spinner. The crumb handshake
  (`/v1/test/getcrumb`) is not used: it is itself rate limited and answered 429
  on repeat attempts, which is the same fragility that broke the quote endpoint.
- **A retired ticker does not always 404.** `HPCL.NS` answers 200 with the symbol
  echoed back, `instrumentType: MUTUALFUND`, and every price null, last traded in
  2019 - Hindustan Petroleum is `HINDPETRO.NS`. `quote_from_chart_meta` therefore
  requires a finite `regularMarketPrice` before it will call something a quote,
  and `WatchlistSymbolTests` pins the two symbols that had already drifted
  (`TATAMOTORS.NS` split into `TMCV.NS` and `TMPV.NS` at the demerger).
- **The cache** (`cached` / `get_cached` / `set_cached`) is a process-local dict
  with a TTL. Writes take `_cache_lock` because loaders run on the thread pool,
  and `evict_cache_entries` bounds it at `MAX_CACHE_ENTRIES` (expired keys first,
  then oldest) so a long-running server does not grow for every symbol anyone
  looks up. When a loader raises and a stale entry exists, the stale value is
  served briefly rather than failing the whole report.
