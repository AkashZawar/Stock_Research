from django.http import JsonResponse

from core import services


def recommendations(request):
    try:
        if request.GET.get("refresh") == "1":
            services.clear_cache("recommendations")
        return JsonResponse(services.get_recommendations())
    except Exception as error:
        return JsonResponse({"error": str(error)}, status=500)
