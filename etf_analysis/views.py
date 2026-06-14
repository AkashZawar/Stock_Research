"""Views for the ETF Analysis tab.

Thin wrappers over ``core.asset_api`` with the asset type fixed to "etf":
- ``search`` (``/api/etf/search``) and ``analyze`` (``/api/etf/analyze``).
"""
from core.asset_api import analyze_asset
from core.asset_api import search_asset


def search(request):
    return search_asset(request, "etf")


def analyze(request):
    return analyze_asset(request, "etf")
