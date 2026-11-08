"""Single error envelope for all API errors: {"error": {code, message, details, request_id}}."""
import logging

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class APIError(exceptions.APIException):
    error_code = "ERROR"


ERROR_CODES = {
    exceptions.ValidationError: "VALIDATION_ERROR",
    exceptions.ParseError: "VALIDATION_ERROR",
    exceptions.NotAuthenticated: "UNAUTHENTICATED",
    exceptions.AuthenticationFailed: "INVALID_CREDENTIALS",
    exceptions.PermissionDenied: "FORBIDDEN",
    exceptions.NotFound: "NOT_FOUND",
    Http404: "NOT_FOUND",
    exceptions.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    exceptions.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    exceptions.Throttled: "RATE_LIMITED",
}


def _error_body(request, code, message, details=None):
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request, "request_id", ""),
        }
    }


def api_exception_handler(exc, context):
    request = context.get("request")
    response = exception_handler(exc, context)

    if response is None:  # unhandled exception: log it, never leak internals
        logger.exception("Unhandled API exception")
        return Response(
            _error_body(request, "INTERNAL_ERROR", "Internal server error."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = getattr(exc, "error_code", None) or next(
        (c for cls, c in ERROR_CODES.items() if isinstance(exc, cls)), "ERROR"
    )
    if isinstance(exc, exceptions.ValidationError):
        message = "Invalid input."
        details = {"field_errors": response.data} if isinstance(response.data, dict) else {"errors": response.data}
    else:
        message = str(getattr(exc, "detail", "Request failed."))
        details = {}
    response.data = _error_body(request, code, message, details)
    return response