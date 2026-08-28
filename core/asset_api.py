"""Shared view logic for the ETF and mutual-fund tabs.

The ``etf_analysis`` and ``mutual_funds`` apps both reuse these helpers,
passing their asset type ("etf" or "mutual-fund"):
- ``search_asset(request, asset_type)``: symbol/scheme search suggestions.
- ``analyze_asset(request, asset_type)``: build the full asset report and
  log the request.
"""
from django.http import JsonResponse

from . import services
from .view_helpers import record_stock_search


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
    symbol = ""
    status_code = 500
    error_message = ""
    try:
        asset_type = services.normalize_asset_type(asset_type)
        symbol = services.resolve_asset_input(raw_input, asset_type)
        if not symbol:
            status_code = 400
            error_message = services.INVALID_INSTRUMENT_MESSAGE
            return JsonResponse(services.invalid_instrument_payload(raw_input), status=status_code)
        payload = services.analyze_asset(symbol, asset_type)
        status_code = 200
        return JsonResponse(payload)
    except Exception as error:
        error_message = str(error)
        if services.is_invalid_instrument_error(error_message):
            status_code = 400
            # The symbol parsed but the provider has no data for it, so report it
            # as expired rather than mistyped and keep it out of the suggestions.
            payload = services.invalid_instrument_payload(raw_input, symbol)
            error_message = payload["error"]
            return JsonResponse(payload, status=status_code)
        if services.is_insufficient_history_error(error_message):
            # A real instrument with a stub history is a data-availability
            # answer, not a server fault - keep the explanation and use 400.
            status_code = 400
            return JsonResponse({"error": error_message}, status=status_code)
        status_code = 500
        return JsonResponse({"error": error_message}, status=status_code)
    finally:
        record_stock_search(request, raw_input, symbol, status_code, error_message)
