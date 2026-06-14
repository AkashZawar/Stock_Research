# recommendations - Recommendations tab

Owns the stock recommendations tab. The idea list is built and cached in
`core.services.get_recommendations`.

## Files

| File | What it is for |
|------|----------------|
| `views.py` | `recommendations` endpoint; `?refresh=1` clears the cache first. |
| `urls.py` | Route `/api/recommendations`. |
| `apps.py` | Django app config. |
| `templates/recommendations/tab.html` | The recommendation table (buy/sell/stop, hold duration, upside). Included by `core/base.html`. |

## Endpoints

- `GET /api/recommendations` - analyst / FII-DII backed idea list
- `GET /api/recommendations?refresh=1` - rebuild and refresh the list
