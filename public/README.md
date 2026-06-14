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
