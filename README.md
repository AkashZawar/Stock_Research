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

- `stockdesk/` contains Django settings, ASGI/WSGI, and URL routing.
- `market/views.py` exposes the page and JSON API endpoints.
- `market/services.py` contains the stock analysis, technical indicators, support/resistance, fundamentals, and monitor logic.
- `market/templates/market/index.html` is the Django template.
- `public/` contains static CSS and JavaScript served by Django in development.

## API

- `/api/search?q=ABBOT%20india`
- `/api/analyze?symbol=ABBOTINDIA.NS`
- `/api/search-logs?limit=100`
- `/api/market-monitor?refresh=1`

The app uses public Yahoo Finance, SEC, and Screener.in endpoints where available. For production use, replace these providers with a licensed market data API.

## Notes

- The report is for research and education only.
- Research levels are generated from support, resistance, and ATR.
- The app does not provide personalized financial advice.
