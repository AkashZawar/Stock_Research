# market_monitor - Market Monitor tab

Owns the market-wide monitor tab. The expensive scan is built in
`core.services`; this app serves it from cache and triggers background
refreshes.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `market_monitor` endpoint plus `cache_live_market_monitor` / `mark_monitor_refreshing` helpers. Supports `?live=1`, `?refresh=1` and `?detail=1`. |
| `urls.py` | Route `/api/market-monitor`. |
| `apps.py` | Django app config. |
| `templates/market_monitor/tab.html` | Commodities, heatmaps, NSE snapshot, sector OI, gainers/losers, breakouts and momentum tables. Included by `core/base.html`. |

## Endpoints

- `GET /api/market-monitor` - cached monitor (auto-refreshes when stale)
- `GET /api/market-monitor?live=1` - latest live payload
- `GET /api/market-monitor?refresh=1` - force a refresh
- `GET /api/market-monitor?detail=1` - build the detailed scan in the request and return it

## Why the detail is its own request

The snapshot answers in about a second; the detailed scan takes much longer, so
the two cannot share a request without the dashboard waiting on the slower one.
That part was always true. What was not true was the way the scan got built: a
daemon thread filled a process-local cache, and the tab told the user the
sections were "loading in the background and will appear".

A serverless host freezes that thread the moment the response is sent and routes
the next request to a different instance with its own empty cache, so on the
live deployment the promise was frequently never kept - the dashboard sat at
"0 scanned" with "detailed scan refreshing" until an instance happened to stay
warm long enough. `?detail=1` builds the scan inside the request instead, and
the browser asks for it once the snapshot is on screen. Measured from a cold
start: snapshot at ~8s, full detail at ~25s, no background work involved.

This only works because the scan was brought inside a request budget first - see
the concurrency notes in `core/README.md`. Before that it took ~64s, which no
serverless request survives.
