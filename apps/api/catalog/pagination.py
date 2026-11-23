"""
catalog/pagination.py

Paginación por cursor opaco para endpoints /pages/{cursor}.
Cumple con las especificaciones de 10-plan-de-implementacion-y-gates.md §Q-22
y 11-borrador-contrato-api.md:
  • Cursor ligado a recurso e identidad del usuario.
  • Vigencia de 24 horas (86,400 segundos).
  • Cursors alterados o ajenos devuelven VALIDATION_ERROR (400).
  • Cursors expirados devuelven CURSOR_EXPIRED (410).
  • El valor literal "inicio" o vacío inicia desde la primera página.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from django.conf import settings
from rest_framework import exceptions, status

CURSOR_TTL_SECONDS = 24 * 60 * 60  # 24 horas


class CursorExpiredError(exceptions.APIException):
    status_code = status.HTTP_410_GONE
    api_code = "CURSOR_EXPIRED"
    api_message = "El cursor de paginación ha expirado; reinicie con 'inicio'."


class CursorInvalidError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    api_code = "VALIDATION_ERROR"
    api_message = "El cursor proporcionado no es válido o no corresponde a este recurso."


def _sign(payload_str: str) -> str:
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def encode_cursor(resource: str, user_id: str, last_id: int) -> str:
    data = {
        "r": resource,
        "u": str(user_id),
        "id": last_id,
        "t": int(time.time()),
    }
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True)
    sig = _sign(raw)
    combined = f"{raw}|{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(combined).decode("ascii").rstrip("=")


def decode_cursor(cursor_str: str, resource: str, user_id: str) -> int:
    """
    Decodifica el cursor y devuelve el last_id.
    Si cursor_str es 'inicio' o vacío, devuelve 0.
    Lanza CursorExpiredError si pasaron más de 24 horas.
    Lanza CursorInvalidError si la firma, recurso o usuario no coinciden.
    """
    if not cursor_str or cursor_str.strip().lower() == "inicio":
        return 0

    padding = "=" * (-len(cursor_str) % 4)
    try:
        raw_bytes = base64.urlsafe_b64decode(cursor_str + padding)
        decoded = raw_bytes.decode("utf-8")
        raw, sig = decoded.rsplit("|", 1)
    except Exception as exc:
        raise CursorInvalidError() from exc

    expected_sig = _sign(raw)
    if not hmac.compare_digest(sig, expected_sig):
        raise CursorInvalidError()

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise CursorInvalidError() from exc

    if data.get("r") != resource or str(data.get("u")) != str(user_id):
        raise CursorInvalidError()

    created_at = data.get("t", 0)
    if time.time() - created_at > CURSOR_TTL_SECONDS:
        raise CursorExpiredError()

    return int(data.get("id", 0))
