import json
import logging
from ipaddress import ip_address
from decimal import Decimal, InvalidOperation

from django.http import HttpResponseNotAllowed
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from . import services
from .models import StockSearchLog
from .models import TradeReference


logger = logging.getLogger(__name__)


def index(request):
    return render(request, "market/index.html")


def search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 1:
        return JsonResponse({"results": []})

    try:
        return JsonResponse({"results": services.search_symbols(query)})
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def search_assets(request):
    query = request.GET.get("q", "").strip()
    asset_type = request.GET.get("type", "").strip()
    if len(query) < 1:
        return JsonResponse({"results": []})

    try:
        return JsonResponse({"results": services.search_assets(query, asset_type)})
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def analyze(request):
    raw_input = request.GET.get("symbol", "").strip()
    symbol = ""
    status_code = 500
    error_message = ""
    try:
        symbol = services.resolve_symbol_input(raw_input)
        if not symbol:
            status_code = 400
            error_message = "Enter a valid ticker symbol or stock name."
            return JsonResponse({"error": error_message}, status=status_code)
        payload = services.analyze_symbol(symbol)
        status_code = 200
        return JsonResponse(payload)
    except Exception as error:
        error_message = str(error)
        status_code = 500
        return JsonResponse({"error": error_message}, status=status_code)
    finally:
        record_stock_search(request, raw_input, symbol, status_code, error_message)


def analyze_asset(request):
    raw_input = request.GET.get("symbol", "").strip()
    asset_type = request.GET.get("type", "").strip()
    symbol = ""
    status_code = 500
    error_message = ""
    try:
        asset_type = services.normalize_asset_type(asset_type)
        symbol = services.resolve_asset_input(raw_input, asset_type)
        if not symbol:
            status_code = 400
            error_message = "Enter a valid ETF or mutual fund symbol/name."
            return JsonResponse({"error": error_message}, status=status_code)
        payload = services.analyze_asset(symbol, asset_type)
        status_code = 200
        return JsonResponse(payload)
    except Exception as error:
        error_message = str(error)
        status_code = 500
        return JsonResponse({"error": error_message}, status=status_code)
    finally:
        record_stock_search(request, raw_input, symbol, status_code, error_message)


def search_logs(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    try:
        limit = max(1, min(int(request.GET.get("limit", "100")), 250))
    except ValueError:
        limit = 100

    logs = StockSearchLog.objects.all()[:limit]
    return JsonResponse({
        "count": StockSearchLog.objects.count(),
        "results": [item.as_dict() for item in logs],
    })


def market_monitor(request):
    try:
        if request.GET.get("refresh") == "1":
            services.clear_cache("market-monitor")
        return JsonResponse(services.cached("market-monitor", services.build_market_monitor, 10 * 60))
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def record_stock_search(request, raw_input, symbol, status_code, error_message):
    if not raw_input and not symbol:
        return

    try:
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        device_type, device_label = describe_device(user_agent)
        StockSearchLog.objects.create(
            raw_input=raw_input[:160],
            symbol=(symbol or "").upper()[:32],
            ip_address=client_ip(request),
            device_type=device_type,
            device_label=device_label,
            user_agent=user_agent[:1000],
            status_code=status_code,
            success=200 <= status_code < 400,
            error_message=(error_message or "")[:280],
        )
    except Exception:
        logger.exception("Could not record stock search log.")


def client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    raw_ip = forwarded_for.split(",", 1)[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR", "").strip()
    if not raw_ip:
        return None

    try:
        return str(ip_address(raw_ip))
    except ValueError:
        return None


def describe_device(user_agent):
    agent = (user_agent or "").lower()
    if not agent:
        return "unknown", "Unknown device"
    if any(marker in agent for marker in ["bot", "crawler", "spider", "slurp"]):
        return "bot", "Bot / crawler"
    if "ipad" in agent or "tablet" in agent or ("android" in agent and "mobile" not in agent):
        return "tablet", "Tablet"
    if any(marker in agent for marker in ["mobi", "iphone", "android", "phone"]):
        return "mobile", "Mobile"
    if "windows" in agent:
        return "desktop", "Windows desktop"
    if "macintosh" in agent or "mac os x" in agent:
        return "desktop", "Mac desktop"
    if "linux" in agent or "x11" in agent:
        return "desktop", "Linux desktop"
    return "unknown", "Unknown device"


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
