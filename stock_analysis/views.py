"""Views for the Stock Analysis tab.

- ``search``: ticker/name search suggestions (``/api/search``).
- ``analyze``: build the full stock research report (``/api/analyze``). The
  heavy lifting lives in ``core.services``.
"""
from django.http import JsonResponse

from core import services


def search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 1:
        return JsonResponse({"results": []})

    try:
        return JsonResponse({"results": services.search_symbols(query)})
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def analyze(request):
    raw_input = request.GET.get("symbol", "").strip()
    # Bound before the try so the error path can still name it when resolution
    # itself is what raised.
    symbol = ""
    try:
        symbol = services.resolve_symbol_input(raw_input)
        if not symbol:
            return JsonResponse(services.invalid_instrument_payload(raw_input), status=400)
        return JsonResponse(services.analyze_symbol(symbol))
    except Exception as error:
        error_message = str(error)
        if services.is_invalid_instrument_error(error_message):
            # The symbol parsed but the provider has no data for it, so report it
            # as expired rather than mistyped and keep it out of the suggestions.
            return JsonResponse(services.invalid_instrument_payload(raw_input, symbol), status=400)
        return JsonResponse({"error": error_message}, status=500)
