"""Shared view logic for the ETF and mutual-fund tabs.

The ``etf_analysis`` and ``mutual_funds`` apps both reuse these helpers,
passing their asset type ("etf" or "mutual-fund"):
- ``search_asset(request, asset_type)``: symbol/scheme search suggestions.
- ``analyze_asset(request, asset_type)``: build the full asset report.
"""
from django.http import JsonResponse

from . import services


def search_asset(request, asset_type):
    query = request.GET.get("q", "").strip()
    if len(query) < 1:
        return JsonResponse({"results": []})

    try:
        return JsonResponse({"results": services.search_assets(query, asset_type)})
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def analyze_asset(request, asset_type):
    raw_input = request.GET.get("symbol", "").strip()
    # Bound before the try so the error path can still name it when resolution
    # itself is what raised.
    symbol = ""
    try:
        asset_type = services.normalize_asset_type(asset_type)
        symbol = services.resolve_asset_input(raw_input, asset_type)
        if not symbol:
            return JsonResponse(services.invalid_instrument_payload(raw_input), status=400)
        return JsonResponse(services.analyze_asset(symbol, asset_type))
    except Exception as error:
        error_message = str(error)
        if services.is_invalid_instrument_error(error_message):
            # The symbol parsed but the provider has no data for it, so report it
            # as expired rather than mistyped and keep it out of the suggestions.
            return JsonResponse(services.invalid_instrument_payload(raw_input, symbol), status=400)
        if services.is_insufficient_history_error(error_message):
            # A real instrument with a stub history is a data-availability
            # answer, not a server fault - keep the explanation and use 400.
            return JsonResponse({"error": error_message}, status=400)
        return JsonResponse({"error": error_message}, status=500)
