"""Views for the Agent Desk tab.

``analyze``: run the multi-agent desk for one symbol
(``/api/agent-desk/analyze?symbol=...&rounds=2``). Data loading is delegated to
``core.services`` (which caches the report, so opening this tab right after the
Stock Analysis tab costs no extra network calls) and the reasoning lives in
``agent_desk.agents``.
"""
from django.http import JsonResponse

from core import services

from . import agents


def analyze(request):
    raw_input = request.GET.get("symbol", "").strip()
    rounds = agents.normalize_rounds(request.GET.get("rounds", agents.DEFAULT_DEBATE_ROUNDS))
    # Bound before the try so the error path can still name it when resolution
    # itself is what raised.
    symbol = ""
    try:
        symbol = services.resolve_symbol_input(raw_input)
        if not symbol:
            return JsonResponse(services.invalid_instrument_payload(raw_input), status=400)
        return JsonResponse(agents.build_agent_report(symbol, rounds))
    except Exception as error:
        error_message = str(error)
        if services.is_invalid_instrument_error(error_message):
            # The symbol parsed but the provider has no data for it, so report it
            # as expired rather than mistyped and keep it out of the suggestions.
            return JsonResponse(services.invalid_instrument_payload(raw_input, symbol), status=400)
        return JsonResponse({"error": error_message}, status=500)
