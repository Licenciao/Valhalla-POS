import uuid
from typing import Any

from django.http import JsonResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as default_exception_handler


class RestoreConfirmationRequiredError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "RESTORE_CONFIRMATION_REQUIRED"
    api_code = "RESTORE_CONFIRMATION_REQUIRED"
    api_message = "Se requiere una segunda confirmación explícita para iniciar la restauración."

    def __init__(self, message: str | None = None):
        if message:
            self.api_message = message
        super().__init__(self.api_message)


class InsufficientStorageError(exceptions.APIException):
    status_code = 507
    default_code = "INSUFFICIENT_STORAGE"
    api_code = "INSUFFICIENT_STORAGE"
    api_message = "Espacio libre en disco insuficiente para generar o restaurar el respaldo."

    def __init__(self, message: str | None = None):
        if message:
            self.api_message = message
        super().__init__(self.api_message)


def error_body(
    request: Any,
    code: str,
    message: str,
    retryable: bool = False,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "request_id": getattr(request, "request_id", str(uuid.uuid4())),
        "retryable": retryable,
        "details": details if details is not None else {},
    }


def api_exception_handler(
    exc: Exception,
    context: dict[str, Any],
) -> Response | None:
    response = default_exception_handler(exc, context)
    if response is None:
        return None

    request = context.get("request")
    status_code = response.status_code
    details = {}

    explicit_code = getattr(exc, "api_code", None)
    if explicit_code:
        code = explicit_code
        message = getattr(exc, "api_message", str(exc))
        details = getattr(exc, "details", None) or {}
    elif isinstance(exc, exceptions.ValidationError):
        code = "VALIDATION_ERROR"
        message = "La solicitud contiene datos inválidos."
        details = response.data
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        code = "AUTHENTICATION_REQUIRED"
        message = "Se requiere una sesión autenticada."
        status_code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(exc, exceptions.PermissionDenied):
        detail = str(getattr(exc, "detail", "")).lower()
        if "csrf" in detail:
            code = "CSRF_FAILED"
            message = "El token CSRF falta o no es válido."
        else:
            code = "PERMISSION_DENIED"
            message = "El rol autenticado no tiene permiso para esta operación."
    elif isinstance(exc, exceptions.NotFound):
        code = "RESOURCE_NOT_FOUND"
        message = "El recurso solicitado no existe."
    elif status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        code = "SERVICE_UNAVAILABLE"
        message = "El servicio no está disponible temporalmente."
    elif status_code == status.HTTP_400_BAD_REQUEST:
        code = "VALIDATION_ERROR"
        message = "La solicitud contiene datos inválidos."
        details = response.data
    elif status_code == status.HTTP_401_UNAUTHORIZED:
        code = "AUTHENTICATION_REQUIRED"
        message = "Se requiere una sesión autenticada."
    elif status_code == status.HTTP_409_CONFLICT:
        code = "BUSINESS_CONFLICT"
        message = "La solicitud entra en conflicto con el estado actual."
    elif status_code == status.HTTP_410_GONE:
        code = "CURSOR_EXPIRED"
        message = "El recurso solicitado expiró."
    elif status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        code = "COST_REQUIRED"
        message = "Falta información de costo requerida."
    elif status_code == status.HTTP_426_UPGRADE_REQUIRED:
        code = "CLIENT_VERSION_UNSUPPORTED"
        message = "La versión del cliente ya no es compatible."
    elif status_code == 507:
        code = "INSUFFICIENT_STORAGE"
        message = "No hay espacio suficiente para completar la operación."
    elif status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        code = "INTERNAL_ERROR"
        message = "Ocurrió un error interno."
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        code = "PERMISSION_DENIED"
        message = "La solicitud no está permitida."

    return Response(
        error_body(
            request,
            code,
            message,
            retryable=code in {"SERVICE_UNAVAILABLE", "INTERNAL_ERROR"},
            details=details,
        ),
        status=status_code,
        headers=response.headers,
    )


def csrf_failure(request: Any, reason: str = "") -> JsonResponse:
    return JsonResponse(
        error_body(
            request,
            "CSRF_FAILED",
            "El token CSRF falta o no es válido.",
        ),
        status=403,
    )


def not_found(request: Any, exception: Exception) -> JsonResponse:
    return JsonResponse(
        error_body(request, "RESOURCE_NOT_FOUND", "El recurso solicitado no existe."),
        status=404,
    )


def internal_error(request: Any) -> JsonResponse:
    return JsonResponse(
        error_body(
            request,
            "INTERNAL_ERROR",
            "Ocurrió un error interno.",
            retryable=True,
        ),
        status=500,
    )
