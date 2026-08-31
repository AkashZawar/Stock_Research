"""Core views: the page shells.

Views:
- ``home``: the Google-style landing page (``core/home.html``) showing the
  top recommendation picks as quick analyze links.
- ``index``: the main single-page workspace shell (``core/base.html``).

Helper:
- ``recommended_home_suggestions``: top picks pulled from the cached
  recommendations payload (falls back to defaults) for the landing page.

The app stores nothing: every tab fetches live from its upstream on request,
so there are no CRUD endpoints here and no database behind them.
"""
from django.shortcuts import render

from . import services


def recommended_home_suggestions(limit=5):
    """Top picks from the cached recommendations (empty list if not built yet).

    Uses only the cache so the landing page stays instant; the page's script
    fetches /api/recommendations to fill or refresh the picks client-side. When
    the cache is warm this returns the real picks so they render immediately
    with no flash of placeholder content.
    """
    try:
        payload = services.get_cached("recommendations")
    except Exception:
        payload = None

    suggestions = []
    for row in (payload or {}).get("results", [])[:limit]:
        symbol = str(row.get("analysisSymbol") or row.get("symbol") or "").strip()
        if not symbol:
            continue
        label = symbol.split(".")[0] or symbol
        suggestions.append({"symbol": symbol, "label": label})

    return suggestions


def home(request):
    return render(request, "core/home.html", {"suggestions": recommended_home_suggestions()})


def index(request):
    return render(request, "core/base.html")
