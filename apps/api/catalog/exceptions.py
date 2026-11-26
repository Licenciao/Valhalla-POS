"""
catalog/exceptions.py

Excepciones de dominio para el módulo de catálogo e inventario.
Heredan de rest_framework.exceptions.APIException y definen api_code y api_message
para ser capturadas por el exception_handler común de system/errors.py.
"""

from rest_framework import exceptions, status


class DomainValidationError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    api_code = "VALIDATION_ERROR"
    api_message = "La operación contiene parámetros inválidos."

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(detail=message)
        self.api_message = message
        self.details = details or {}


class InsufficientStockError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    api_code = "INSUFFICIENT_STOCK"
    api_message = "No hay stock central no asignado suficiente para surtir la solicitud."

    def __init__(self, message: str | None = None, details: dict | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = details or {}


class CostRequiredError(exceptions.APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    api_code = "COST_REQUIRED"
    api_message = "El componente carece de costo promedio conocido; se requiere valoración administrativa."

    def __init__(self, message: str | None = None, details: dict | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = details or {}


class BusinessConflictError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    api_code = "BUSINESS_CONFLICT"
    api_message = "La operación entra en conflicto con las reglas del estado actual."

    def __init__(self, message: str | None = None, details: dict | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = details or {}


class OpeningExpenseProtectedError(BusinessConflictError):
    status_code = status.HTTP_403_FORBIDDEN
    api_code = "PROTECTED_OPENING_EXPENSE"
    api_message = "No se puede modificar ni eliminar un gasto de apertura inicial."


class IdempotencyKeyReusedError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    api_code = "IDEMPOTENCY_KEY_REUSED"
    api_message = "Misma Idempotency-Key con payload distinto."

    def __init__(self, message: str | None = None, details: dict | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = details or {}


class PermissionDeniedError(exceptions.APIException):
    status_code = status.HTTP_403_FORBIDDEN
    api_code = "PERMISSION_DENIED"
    api_message = "No tienes permiso para realizar esta operación."

    def __init__(self, message: str | None = None, details: dict | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = details or {}


class ResourceNotFoundError(exceptions.APIException):
    status_code = status.HTTP_404_NOT_FOUND
    api_code = "RESOURCE_NOT_FOUND"
    api_message = "El recurso solicitado no fue encontrado."

    def __init__(self, message: str | None = None):
        msg = message or self.api_message
        super().__init__(detail=msg)
        self.api_message = msg
        self.details = {}
