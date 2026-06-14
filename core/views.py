from django.http import HttpResponseNotAllowed
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from . import services
from .models import StockSearchLog
from .models import TradeReference
from .models import WatchlistItem
from .view_helpers import read_json_body
from .view_helpers import save_trade_reference
from .view_helpers import save_watchlist_item


DEFAULT_HOME_SUGGESTIONS = [
    {"symbol": "RELIANCE", "label": "RELIANCE"},
    {"symbol": "TCS", "label": "TCS"},
    {"symbol": "INFY", "label": "INFY"},
    {"symbol": "HDFCBANK", "label": "HDFCBANK"},
]


def recommended_home_suggestions(limit=5):
    """Top stocks from the cached recommendations, falling back to defaults.

    Uses only the cached payload so the landing page stays instant and never
    triggers the slow recommendations build on load.
    """
    try:
        payload = services.get_cached("recommendations")
    except Exception:
        payload = None

    suggestions = []
    for row in (payload or {}).get("results", [])[:limit]:
        symbol = str(row.get("analysisSymbol") or row.get("symbol") or "").strip()
        if not symbol:
            continue
        label = symbol.split(".")[0] or symbol
        suggestions.append({"symbol": symbol, "label": label})

    return suggestions if len(suggestions) >= 3 else DEFAULT_HOME_SUGGESTIONS


def home(request):
    return render(request, "core/home.html", {"suggestions": recommended_home_suggestions()})


def index(request):
    return render(request, "core/base.html")


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


@csrf_exempt
def watchlist_items(request):
    if request.method == "GET":
        items = [item.as_dict() for item in WatchlistItem.objects.all()]
        return JsonResponse({"results": items})

    if request.method == "POST":
        try:
            payload = read_json_body(request)
            symbol = str(payload.get("symbol", "")).strip().upper()
            item = WatchlistItem.objects.filter(symbol=symbol).first() if symbol else WatchlistItem()
            item = save_watchlist_item(item or WatchlistItem(), payload)
            return JsonResponse(item.as_dict(), status=201)
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)

    return HttpResponseNotAllowed(["GET", "POST"])


@csrf_exempt
def watchlist_item_detail(request, item_id):
    try:
        item = WatchlistItem.objects.get(pk=item_id)
    except WatchlistItem.DoesNotExist:
        return JsonResponse({"error": "Watchlist item was not found."}, status=404)

    if request.method == "PATCH":
        try:
            item = save_watchlist_item(item, read_json_body(request), partial=True)
            return JsonResponse(item.as_dict())
        except ValueError as error:
            return JsonResponse({"error": str(error)}, status=400)

    if request.method == "DELETE":
        item.delete()
        return JsonResponse({"deleted": True})

    return HttpResponseNotAllowed(["PATCH", "DELETE"])


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
