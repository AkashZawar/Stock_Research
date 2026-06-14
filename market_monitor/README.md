# market_monitor - Market Monitor tab

Owns the market-wide monitor tab. The expensive scan is built in
`core.services`; this app serves it from cache and triggers background
refreshes.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `market_monitor` endpoint plus `cache_live_market_monitor` / `mark_monitor_refreshing` helpers. Supports `?live=1` and `?refresh=1`. |
| `urls.py` | Route `/api/market-monitor`. |
| `apps.py` | Django app config. |
| `templates/market_monitor/tab.html` | Commodities, heatmaps, NSE snapshot, sector OI, gainers/losers, breakouts and momentum tables. Included by `core/base.html`. |

## Endpoints

- `GET /api/market-monitor` - cached monitor (auto-refreshes when stale)
- `GET /api/market-monitor?live=1` - latest live payload
- `GET /api/market-monitor?refresh=1` - force a refresh
