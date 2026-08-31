"""Views for the IPO Radar tab.

- ``ipo`` (``/api/ipo``): returns the recently-listed table, the open/upcoming
  pipeline with GMP consensus and live subscription, and the OFS section, from
  ``ipo.services.get_ipo_dashboard``. ``?refresh=1`` clears the cache first.
"""
from django.http import JsonResponse

from . import services


def ipo(request):
    try:
        if request.GET.get("refresh") == "1":
            services.clear_ipo_cache()
        return JsonResponse(services.get_ipo_dashboard())
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)
