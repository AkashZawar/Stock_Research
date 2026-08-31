# stockdesk - project configuration

The Django project package. It wires the apps together but contains no
business logic.

## Files

| File | What it is for |
|------|----------------|
| `settings.py` | Project settings: installed apps (core + the seven tab apps), templates, SQLite database, static files (`public/`), timezone, and env-driven secret/debug/hosts. |
| `urls.py` | Root URL config; includes each app's `urls.py` at the root so paths come from the apps. |
| `wsgi.py` | WSGI entry point for synchronous servers. |
| `asgi.py` | ASGI entry point for asynchronous servers. |
| `__init__.py` | Package marker. |

## Adding a new tab

1. Create a Django app (e.g. `python manage.py startapp my_tab`).
2. Add its `views.py`, `urls.py` (with an `app_name`), and
   `templates/my_tab/tab.html`.
3. Register the app in `settings.py` `INSTALLED_APPS`.
4. Add `path("", include("my_tab.urls"))` in `urls.py`.
5. Include `{% include "my_tab/tab.html" %}` in `core/templates/core/base.html`.
6. Add a `<button class="tab-button" id="myTabTab">` to the `<nav class="tabs">`
   block in the same file.
7. Wire `public/app.js`: element lookups, a `latest*` state variable, a click
   listener on the button, and the `is*` / `classList.toggle` lines in
   `setActiveTab`. Bump the `?v=` on `app.js` and `styles.css`.
