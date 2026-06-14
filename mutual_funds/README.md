# mutual_funds - Mutual Funds tab

Owns the mutual-fund research tab. Views are thin wrappers over
`core.asset_api` (asset type "mutual-fund"); all logic lives in
`core.services`.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `search` / `analyze` delegating to `core.asset_api` with type "mutual-fund". |
| `urls.py` | Routes `/api/mutual-funds/search` and `/api/mutual-funds/analyze`. |
| `apps.py` | Django app config. |
| `templates/mutual_funds/tab.html` | Fund search form, default shortlist, report and annual-returns panels. Included by `core/base.html`. |

## Endpoints

- `GET /api/mutual-funds/search?q=<text>` - scheme suggestions
- `GET /api/mutual-funds/analyze?symbol=<symbol>` - full fund report
