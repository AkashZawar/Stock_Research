"""Views for the Mutual Funds tab.

Thin wrappers over ``core.asset_api`` with the asset type fixed to
"mutual-fund":
- ``search`` (``/api/mutual-funds/search``) and ``analyze``
  (``/api/mutual-funds/analyze``).
"""
from core.asset_api import analyze_asset
from core.asset_api import search_asset


def search(request):
    return search_asset(request, "mutual-fund")


def analyze(request):
    return analyze_asset(request, "mutual-fund")
