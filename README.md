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

## Folder guide (for new contributors)

Every folder has its own `README.md` describing each file in it, and every
source file starts with a short header comment explaining its purpose. Start
here:

- [`core/`](core/README.md) - shared analysis engine, models, helpers, page shells, cross-cutting APIs
- [`stock_analysis/`](stock_analysis/README.md) - Stock Analysis tab
- [`recommendations/`](recommendations/README.md) - Recommendations tab
- [`market_monitor/`](market_monitor/README.md) - Market Monitor tab
- [`etf_analysis/`](etf_analysis/README.md) - ETF Analysis tab
- [`mutual_funds/`](mutual_funds/README.md) - Mutual Funds tab
- [`stockdesk/`](stockdesk/README.md) - Django project config (settings, URLs, WSGI/ASGI)
- [`public/`](public/README.md) - shared static assets (`app.js`, `styles.css`, `cursor.js`)

### Request flow

1. The browser loads `/` (landing page) or `/app` (workspace shell).
2. `public/app.js` calls a JSON API (e.g. `/api/analyze`).
3. The matching tab app view calls into `core/services.py` to build the data.
4. The view returns JSON; `app.js` renders it into that tab.

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
