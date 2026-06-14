from django.http import JsonResponse

from core import services


def market_monitor(request):
    try:
        if request.GET.get("live") == "1":
            fresh_payload = services.get_cached("market-monitor")
            cached_payload = fresh_payload or services.get_cached("market-monitor", include_expired=True)
            refreshing = services.is_market_monitor_refreshing()
            if not fresh_payload and not refreshing:
                services.start_market_monitor_refresh()
                refreshing = True
            return JsonResponse(cache_live_market_monitor(cached_payload, refreshing))

        if request.GET.get("refresh") == "1":
            services.start_market_monitor_refresh()
            cached_payload = services.get_cached("market-monitor", include_expired=True)
            return JsonResponse(cache_live_market_monitor(cached_payload, True, stale=True))

        cached_payload = services.get_cached("market-monitor")
        if cached_payload:
            return JsonResponse(cache_live_market_monitor(cached_payload, services.is_market_monitor_refreshing()))

        cached_payload = services.get_cached("market-monitor", include_expired=True)
        services.start_market_monitor_refresh()
        return JsonResponse(cache_live_market_monitor(cached_payload, True))
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)


def cache_live_market_monitor(cached_payload=None, refreshing=False, stale=False):
    live_payload = services.get_cached("market-monitor-live")
    if live_payload and live_payload.get("refreshing") == refreshing:
        return mark_monitor_refreshing(live_payload, stale=stale) if refreshing else live_payload

    payload = services.build_live_market_monitor(cached_payload, refreshing=refreshing)
    services.set_cached("market-monitor-live", payload, services.LIVE_MARKET_MONITOR_CACHE_SECONDS)
    return mark_monitor_refreshing(payload, stale=stale) if refreshing else payload


def mark_monitor_refreshing(payload, stale=False):
    marked = {**payload, "refreshing": True}
    if stale:
        marked["staleWhileRefreshing"] = True
        marked["note"] = (
            f"{payload.get('note', '').rstrip()} Showing the last detailed monitor while a fresh scan runs."
        ).strip()
    return marked
