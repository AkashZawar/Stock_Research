import json
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseNotAllowed
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from . import services
from .models import TradeReference


def index(request):
    return render(request, "market/index.html")


def search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    try:
        return JsonResponse({"results": services.search_symbols(query)})
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def analyze(request):
    raw_input = request.GET.get("symbol", "").strip()
    try:
        symbol = services.resolve_symbol_input(raw_input)
        if not symbol:
            return JsonResponse({"error": "Enter a valid ticker symbol or stock name."}, status=400)
        return JsonResponse(services.analyze_symbol(symbol))
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def market_monitor(request):
    try:
        if request.GET.get("refresh") == "1":
            services.clear_cache("market-monitor")
        return JsonResponse(services.cached("market-monitor", services.build_market_monitor, 10 * 60))
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


@csrf_exempt
def trade_references(request):
    if request.method == "GET":
        items = [trade.as_dict() for trade in TradeReference.objects.all()]
        return JsonResponse({"results": items})

    if request.method == "POST":
        try:
            trade = save_trade_reference(TradeReference(), read_json_body(request))
            return JsonResponse(trade.as_dict(), status=201)
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)

    return HttpResponseNotAllowed(["GET", "POST"])


@csrf_exempt
def trade_reference_detail(request, reference_id):
    try:
        trade = TradeReference.objects.get(pk=reference_id)
    except TradeReference.DoesNotExist:
        return JsonResponse({"error": "Trade reference was not found."}, status=404)

    if request.method == "PATCH":
        try:
            trade = save_trade_reference(trade, read_json_body(request), partial=True)
            return JsonResponse(trade.as_dict())
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)

    if request.method == "DELETE":
        trade.delete()
        return JsonResponse({"deleted": True})

    return HttpResponseNotAllowed(["PATCH", "DELETE"])


def read_json_body(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("Request body must be valid JSON.") from error


def save_trade_reference(trade, payload, partial=False):
    symbol = str(payload.get("symbol", trade.symbol if partial else "")).strip().upper()
    stock_name = str(payload.get("stockName", trade.stock_name if partial else "")).strip()
    status = str(payload.get("status", trade.status if partial else "watch")).strip().lower()
    note = str(payload.get("note", trade.note if partial else "")).strip()

    if not symbol:
        raise ValueError("Symbol is required.")
    if status not in {"watch", "active", "closed"}:
        raise ValueError("Status must be watch, active, or closed.")

    buy_price = decimal_from_payload(payload, "buyPrice", trade.buy_price if partial else None, required=True)
    sell_price = decimal_from_payload(payload, "sellPrice", trade.sell_price if partial else None, required=True)
    stop_loss = decimal_from_payload(payload, "stopLoss", trade.stop_loss if partial else None, required=False)

    if buy_price <= 0 or sell_price <= 0:
        raise ValueError("Buy price and sell price must be greater than zero.")
    if stop_loss is not None and stop_loss < 0:
        raise ValueError("Stop loss cannot be negative.")

    trade.symbol = symbol
    trade.stock_name = stock_name
    trade.buy_price = buy_price
    trade.sell_price = sell_price
    trade.stop_loss = stop_loss
    trade.status = status
    trade.note = note
    trade.save()
    return trade


def decimal_from_payload(payload, key, current_value, required):
    if key not in payload:
        if required and current_value is None:
            raise ValueError(f"{key} is required.")
        return current_value

    value = payload.get(key)
    if value in ("", None):
        if required:
            raise ValueError(f"{key} is required.")
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{key} must be a valid number.") from error
