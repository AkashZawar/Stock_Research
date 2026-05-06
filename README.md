# Stock Research Desk

A no-dependency Node app for one-year stock research reports.

## Run

```sh
node server.js
```

Then open:

```text
http://localhost:3000
```

The app uses public Yahoo Finance endpoints for chart, quote, fundamentals, and events. For production use, replace this provider with a licensed market data API.

## Notes

- The report is for research and education only.
- Research levels are generated from support, resistance, and ATR.
- The app does not provide personalized financial advice.
