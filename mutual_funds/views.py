from core.asset_api import analyze_asset
from core.asset_api import search_asset


def search(request):
    return search_asset(request, "mutual-fund")


def analyze(request):
    return analyze_asset(request, "mutual-fund")
