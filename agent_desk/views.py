"""Views for the Agent Desk tab.

``analyze``: run the multi-agent desk for one symbol
(``/api/agent-desk/analyze?symbol=...&rounds=2``). Data loading is delegated to
``core.services`` (which caches the report, so opening this tab right after the
Stock Analysis tab costs no extra network calls) and the reasoning lives in
``agent_desk.agents``.
"""
from django.http import JsonResponse

from core import services
from core.view_helpers import record_stock_search

from . import agents


def analyze(request):
    raw_input = request.GET.get("symbol", "").strip()
    rounds = agents.normalize_rounds(request.GET.get("rounds", agents.DEFAULT_DEBATE_ROUNDS))
    symbol = ""
    status_code = 500
    error_message = ""
    try:
        symbol = services.resolve_symbol_input(raw_input)
        if not symbol:
            status_code = 400
            error_message = services.INVALID_INSTRUMENT_MESSAGE
            return JsonResponse(services.invalid_instrument_payload(raw_input), status=status_code)
        payload = agents.build_agent_report(symbol, rounds)
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
        status_code = 500
        return JsonResponse({"error": error_message}, status=status_code)
    finally:
        record_stock_search(request, raw_input, symbol, status_code, error_message)
