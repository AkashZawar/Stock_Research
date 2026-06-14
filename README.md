# The Big Picture Investment

A Django app for stock research reports, chart analysis, support/resistance levels, commodity monitoring, breakout scans, fundamentals, and event-risk checks.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 manage.py runserver
```

Then open:

```text
http://127.0.0.1:8000
```

## Django Structure

The project is split into one shared app plus one app per workspace tab. Each tab app
owns its own `views.py`, `urls.py`, and `templates/<app>/tab.html`.

- `stockdesk/` contains Django settings, ASGI/WSGI, and URL routing (`stockdesk/urls.py` includes every app's URLs).
- `core/` is the shared app: `services.py` (stock/asset analysis, indicators, support/resistance, fundamentals, monitor logic), `models.py`, `view_helpers.py`, `asset_api.py`, `templates/core/base.html` (the page shell), and the cross-cutting JSON APIs (watchlist, trade references, search logs).
- `stock_analysis/` owns stock search + analysis (`/api/search`, `/api/analyze`).
- `recommendations/` owns the recommendations feed (`/api/recommendations`).
- `market_monitor/` owns the market monitor (`/api/market-monitor`).
- `etf_analysis/` owns ETF research (`/api/etf/search`, `/api/etf/analyze`).
- `mutual_funds/` owns mutual fund research (`/api/mutual-funds/search`, `/api/mutual-funds/analyze`).
- `public/` contains the shared static CSS and JavaScript served by Django in development.

`core/templates/core/base.html` is the page shell; it includes each tab app's
`tab.html`, so editing a tab only touches that app's folder.

## API

- `/api/search?q=ABBOT%20india`
- `/api/analyze?symbol=ABBOTINDIA.NS`
- `/api/etf/analyze?symbol=NIFTYBEES.NS`
- `/api/mutual-funds/analyze?symbol=VFIAX`
- `/api/recommendations?refresh=1`
- `/api/search-logs?limit=100`
- `/api/market-monitor?refresh=1`

The app uses public Yahoo Finance, SEC, and Screener.in endpoints where available. For production use, replace these providers with a licensed market data API.

## Notes

- The report is for research and education only.
- Research levels are generated from support, resistance, and ATR.
- The app does not provide personalized financial advice.
