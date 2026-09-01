# public - shared static assets

Front-end assets served by Django in development (configured via
`STATICFILES_DIRS` in `stockdesk/settings.py`). They are referenced from the
templates with `{% static '...' %}`.

## Files

| File | What it is for |
|------|----------------|
| `app.js` | The single-page frontend for the workspace shell (`/app`): tab switching, calling every JSON API, rendering reports/tables, drawing the price chart, search suggestions, and reading `?symbol=` to auto-analyze. |
| `styles.css` | The shared stylesheet for the whole app: design tokens, layout, components, the Bootstrap refinement block, and responsive/mobile rules. |
| `cursor.js` | The animated custom cursor for laptop/desktop (fine-pointer) devices; skips touch and reduced-motion users. Used by both `base.html` and `home.html`. |
| `home-bg.png` | Background image for the landing page (`core/home.html`). |

## Notes

- The landing page (`core/home.html`) is self-contained: it has its own inline
  styles and a small inline suggestion script, and does not load `app.js` or
  `styles.css`. It does use `cursor.js`.
- Asset versions are cache-busted via a `?v=...` query string in the template
  `<link>`/`<script>` tags; bump it when you change `app.js`/`styles.css`.
- **Absent is not the same as pending.** `validateRenderedData` sweeps rendered
  markup and restyles anything reading `n/a`, `null`, `--` or `... unavailable`
  as a loading placeholder. That is right while a payload is in flight and wrong
  once it has landed empty, and the second case was the common one: the market
  monitor carried about 110 spinners that could never resolve, several of them
  over fields whose own payload said the data was unavailable. A renderer that
  knows a value will not arrive marks it with `SETTLED_VALUE_CLASS`
  (`settledText`, `settledNumber`, `reportedText`, `reportedNumber`, or
  `emptyTable(..., settled=true)`), and the sweep leaves those alone.
- `settleStalePlaceholders` is armed once per root, not restarted per render.
  Clearing it first meant the monitor's one-second live repaint reset the timer
  before it could fire, so the one tab where placeholders piled up was the one
  tab they could never settle on.
