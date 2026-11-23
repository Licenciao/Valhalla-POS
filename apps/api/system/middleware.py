import time
import uuid
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpRequest, HttpResponse, JsonResponse

from system.constants import SESSION_LAST_ACTIVITY_KEY, SESSION_STARTED_AT_KEY
from system.errors import error_body


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.request_id = str(uuid.uuid4())
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


def session_is_expired(
    started_at: float,
    last_activity_at: float,
    current_time: float,
    idle_seconds: int,
    absolute_seconds: int,
) -> bool:
    return (
        current_time - last_activity_at >= idle_seconds
        or current_time - started_at >= absolute_seconds
    )


def session_expiry_seconds(
    started_at: float,
    current_time: float,
    idle_seconds: int,
    absolute_seconds: int,
) -> int:
    remaining_absolute = absolute_seconds - (current_time - started_at)
    return max(1, min(idle_seconds, int(remaining_absolute)))


def _session_timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class SessionTimeoutMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.user.is_authenticated:
            current_time = time.time()
            started_at = _session_timestamp(request.session.get(SESSION_STARTED_AT_KEY))
            last_activity_at = _session_timestamp(
                request.session.get(SESSION_LAST_ACTIVITY_KEY)
            )

            if started_at is None or last_activity_at is None:
                started_at = current_time
                last_activity_at = current_time

            if session_is_expired(
                started_at,
                last_activity_at,
                current_time,
                settings.SESSION_IDLE_TIMEOUT_SECONDS,
                settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
            ):
                logout(request)
                return JsonResponse(
                    error_body(
                        request,
                        "AUTHENTICATION_REQUIRED",
                        "La sesión expiró; inicia sesión nuevamente.",
                    ),
                    status=401,
                )

            request.session[SESSION_STARTED_AT_KEY] = started_at
            request.session[SESSION_LAST_ACTIVITY_KEY] = current_time
            request.session.set_expiry(
                session_expiry_seconds(
                    started_at,
                    current_time,
                    settings.SESSION_IDLE_TIMEOUT_SECONDS,
                    settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
                )
            )

        return self.get_response(request)
