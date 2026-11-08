from django.http import JsonResponse
from django.views.defaults import page_not_found


def api_not_found(request, exception=None):
    if request.path.startswith("/api/"):
        return JsonResponse(
            {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Not found.",
                    "details": {},
                    "request_id": getattr(request, "request_id", ""),
                }
            },
            status=404,
        )
    return page_not_found(request, exception)
