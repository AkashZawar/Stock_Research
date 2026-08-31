# The Big Picture Investment

A Django app for stock research reports, chart analysis, support/resistance levels, commodity monitoring, breakout scans, fundamentals, and event-risk checks — plus an **Agent Desk** that runs a multi-agent debate over the same data and returns a single, grounded verdict.

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
- `agent_desk/` owns the multi-agent research desk (`/api/agent-desk/analyze`).
- `recommendations/` owns the recommendations feed (`/api/recommendations`).
- `market_monitor/` owns the market monitor (`/api/market-monitor`).
- `etf_analysis/` owns ETF research (`/api/etf/search`, `/api/etf/analyze`).
- `mutual_funds/` owns mutual fund research (`/api/mutual-funds/search`, `/api/mutual-funds/analyze`).
- `ipo/` owns the IPO radar (`/api/ipo`), including its own `services.py` data layer.
- `public/` contains the shared static CSS and JavaScript served by Django in development.

`core/templates/core/base.html` is the page shell; it includes each tab app's
`tab.html`, so editing a tab only touches that app's folder.

## Which tab answers which question

Every tab opens with a collapsed **"What is this tab for?"** panel listing its
purpose and the details it reports, so the app explains itself in place. In
short:

| Tab | Use it to | Scope |
|-----|-----------|-------|
| Stock Analysis | Research one stock end to end and get entry/stop/target levels | A single symbol |
| Agent Desk | See the argument behind the call, and where the desks disagree | A single symbol |
| Recommendations | Start from a ready-made shortlist of analyst/FII-DII backed ideas | Many symbols |
| Market Monitor | Find what is moving today and pick candidates to research | Whole market |
| ETF Analysis | Judge an ETF's risk-adjusted return and how closely it tracks | A single fund |
| Mutual Funds | Assess a scheme before an SIP or lump sum, using rolling returns | A single scheme |
| IPO Radar | Judge an IPO before applying, and see how recent listings actually did | Current issues |

The usual path is Market Monitor or Recommendations to find a name, then Stock
Analysis to verify it, then Agent Desk to stress-test the reasoning.

## Folder guide (for new contributors)

Every folder has its own `README.md` describing each file in it, and every
source file starts with a short header comment explaining its purpose. Start
here:

- [`core/`](core/README.md) - shared analysis engine, models, helpers, page shells, cross-cutting APIs
- [`stock_analysis/`](stock_analysis/README.md) - Stock Analysis tab
- [`agent_desk/`](agent_desk/README.md) - Agent Desk tab (multi-agent debate)
- [`recommendations/`](recommendations/README.md) - Recommendations tab
- [`market_monitor/`](market_monitor/README.md) - Market Monitor tab
- [`etf_analysis/`](etf_analysis/README.md) - ETF Analysis tab
- [`mutual_funds/`](mutual_funds/README.md) - Mutual Funds tab
- [`ipo/`](ipo/README.md) - IPO Radar tab (GMP consensus, subscription, listings)
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
- `/api/agent-desk/analyze?symbol=ABBOTINDIA.NS&rounds=2`
- `/api/etf/analyze?symbol=NIFTYBEES.NS`
- `/api/mutual-funds/analyze?symbol=0P0000YWL1.BO`
- `/api/recommendations?refresh=1`
- `/api/search-logs?limit=100`
- `/api/market-monitor?refresh=1`
- `/api/ipo?refresh=1`

The app uses public Yahoo Finance, SEC, and Screener.in endpoints where available. For production use, replace these providers with a licensed market data API.

## Agent Desk

The Agent Desk tab is modelled on the
[TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent
trading framework, but it runs on the app's own data with no LLM and no API key.

Four analyst desks (technical, fundamentals, positioning, macro/news) score the
setup and grade their own data coverage. A bull and a bear researcher then debate
over one to three rounds, a trader converts the result into an action with levels
from the existing research-levels engine, a three-member risk team sizes it, and
a portfolio manager approves, adds conditions, or blocks it.

Two things make the verdict trustworthy rather than just confident-sounding:

- **Grounding.** Eight input checks are reported as verified, partial, or
  missing. Thin coverage lowers the desk confidence and shrinks the size, and a
  symbol with too little data is rejected outright.
- **Conflicts.** When two desks read the same data differently, the split is
  shown with a suggested resolution instead of being averaged away.

The technical and fundamental scores are reused from `core/services.py`, so this
tab can never disagree with the Stock Analysis tab. See
[`agent_desk/README.md`](agent_desk/README.md) for the full pipeline and payload.

## ETF and mutual fund data

TradingAgents covers equities and crypto only, so it offers no ETF or fund
logic to borrow. What did transfer is its data-verification habit: it computes a
verified snapshot for the analyst to cite and refuses OHLCV frames older than a
cutoff. Both ideas are applied here.

- **Freshness.** Each asset report carries a `freshness` block. A close or NAV
  older than `MAX_ASSET_STALE_DAYS` (5 days for ETFs, 8 for funds, which publish
  once daily and often a day late) is flagged above the scores and lowers the
  confidence score, so stale levels cannot read as current.
- **Honest resolution.** A name lookup must match every significant word of the
  query. Funds the provider does not carry now report as not found with
  suggestions instead of silently resolving to a different fund.
- **Verified universes.** Every symbol in the curated ETF and fund lists is
  checked to return a usable daily history, so the picker never offers something
  `analyze` will reject.

See [`etf_analysis/README.md`](etf_analysis/README.md) and
[`mutual_funds/README.md`](mutual_funds/README.md) for the provider quirks each
tab works around.

## Test

```sh
python3 manage.py test
```

## Notes

- The report is for research and education only.
- Research levels are generated from support, resistance, and ATR.
- The app does not provide personalized financial advice.
