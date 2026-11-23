import hashlib
import json
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import DatabaseError, connection, transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import exceptions, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from system.authentication import SessionAuthentication
from system.constants import (
    SESSION_LAST_ACTIVITY_KEY,
    SESSION_STARTED_AT_KEY,
)
from system.models import (
    RevocationIdempotency,
    SessionRevocationAudit,
    SystemInstance,
)
from system.permissions import (
    ADMIN_ROLE,
    AuthenticatedApplicationUser,
    HasApplicationRole,
    get_user_role,
)
from system.serializers import LoginSerializer, SessionRevocationSerializer


class ServiceUnavailable(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "El servicio no está disponible temporalmente."
    api_code = "SERVICE_UNAVAILABLE"
    api_message = "El servicio no está disponible temporalmente."


class AuthenticationRequired(exceptions.APIException):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Se requiere una sesión autenticada."
    api_code = "AUTHENTICATION_REQUIRED"
    api_message = "Se requiere una sesión autenticada."


class IdempotencyConflict(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "La clave de idempotencia ya se usó con otro contenido."
    api_code = "IDEMPOTENCY_KEY_REUSED"
    api_message = "La clave de idempotencia ya se usó con otro contenido."


def _display_name(user: Any) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.get_username()


def _session_expires_at(session: Any, current_time: float) -> str:
    started_at = float(session.get(SESSION_STARTED_AT_KEY, current_time))
    last_activity_at = float(session.get(SESSION_LAST_ACTIVITY_KEY, current_time))
    expiry_timestamp = min(
        last_activity_at + settings.SESSION_IDLE_TIMEOUT_SECONDS,
        started_at + settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
    )
    return datetime.fromtimestamp(
        expiry_timestamp,
        tz=timezone.get_current_timezone(),
    ).isoformat()


class ApiRootView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response(
            {
                "name": "Valhalla Inventory & Sales API",
                "api_version": settings.API_VERSION,
                "status": "online",
                "health_check": "/api/v1/health",
                "client_app": "/",
            }
        )


class WebAppView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Any:
        from django.http import HttpResponse

        index_file = settings.BASE_DIR.parent / "clients" / "web_app" / "index.html"
        if not index_file.exists():
            return HttpResponse("Web App not found.", status=404)
        with open(index_file, "r", encoding="utf-8") as f:
            content = f.read()
        return HttpResponse(content, content_type="text/html; charset=utf-8")


class WebAppChartJsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Any:
        from django.http import HttpResponse

        chart_file = settings.BASE_DIR.parent / "clients" / "web_app" / "chart.umd.min.js"
        if not chart_file.exists():
            return HttpResponse("Chart.js not found.", status=404)
        with open(chart_file, "r", encoding="utf-8") as f:
            content = f.read()
        return HttpResponse(content, content_type="application/javascript; charset=utf-8")


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        try:
            connection.ensure_connection()
            instance_id = SystemInstance.get_instance().instance_id
        except DatabaseError as exc:
            raise ServiceUnavailable() from exc

        return Response(
            {
                "status": "ok",
                "api_version": settings.API_VERSION,
                "instance_id": str(instance_id),
            }
        )


class CsrfView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        return Response({"csrf_token": get_token(request._request)})


@method_decorator(csrf_protect, name="dispatch")
class SessionView(APIView):
    def get_authenticators(self) -> list[BaseAuthentication]:
        if self.request.method == "POST":
            return []
        return [SessionAuthentication()]

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "POST":
            return [AllowAny()]
        return [AuthenticatedApplicationUser()]

    def get(self, request: Request) -> Response:
        role = get_user_role(request.user)
        if role is None:
            raise exceptions.PermissionDenied()
        return Response(
            {
                "user_id": request.user.pk,
                "display_name": _display_name(request.user),
                "role": role,
            }
        )

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request=request._request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            raise AuthenticationRequired()

        role = get_user_role(user)
        if role is None:
            raise exceptions.PermissionDenied()

        django_request = request._request
        login(django_request, user)
        current_time = time.time()
        django_request.session[SESSION_STARTED_AT_KEY] = current_time
        django_request.session[SESSION_LAST_ACTIVITY_KEY] = current_time
        django_request.session.set_expiry(settings.SESSION_IDLE_TIMEOUT_SECONDS)
        get_token(django_request)

        return Response(
            {
                "user_id": user.pk,
                "display_name": _display_name(user),
                "role": role,
                "expires_at": _session_expires_at(
                    django_request.session,
                    current_time,
                ),
            }
        )

    def delete(self, request: Request) -> Response:
        logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionRevocationsView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser, HasApplicationRole]
    required_role = ADMIN_ROLE

    def post(self, request: Request) -> Response:
        serializer = SessionRevocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        raw_key = request.headers.get("Idempotency-Key", "")
        try:
            idempotency_key = UUID(raw_key)
        except (TypeError, ValueError, AttributeError) as exc:
            raise exceptions.ValidationError(
                {"Idempotency-Key": "Debe ser un UUID válido."}
            ) from exc

        canonical_request = json.dumps(
            {"reason": reason},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        actor_user_id = str(request.user.pk)

        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=request.user.pk)
            previous = RevocationIdempotency.objects.filter(
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
            ).first()
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise IdempotencyConflict()
                return Response(previous.response_body)

            active_sessions = Session.objects.filter(
                expire_date__gt=timezone.now()
            ).count()
            audit = SessionRevocationAudit.objects.create(
                actor_user_id=actor_user_id,
                actor_username=request.user.get_username(),
                reason=reason,
                revoked_sessions=active_sessions,
            )
            response_body = {
                "revoked_sessions": active_sessions,
                "audit_id": str(audit.audit_id),
            }
            RevocationIdempotency.objects.create(
                actor_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_body=response_body,
            )
            Session.objects.all().delete()
            logout(request._request)

        return Response(response_body)


# ---------------------------------------------------------------------------
# Vistas de Respaldo Cifrado y Restauración Aislada (P-10, CA-23, CA-33)
# ---------------------------------------------------------------------------

class BackupStatusView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser, HasApplicationRole]
    required_role = ADMIN_ROLE

    def get(self, request: Request) -> Response:
        from system.models import BackupRecord, BackupRecordStatus, DriveSyncStatus

        last_attempt = BackupRecord.objects.order_by("-created_at").first()
        last_valid = (
            BackupRecord.objects.filter(status__in=[BackupRecordStatus.SUCCESS, BackupRecordStatus.VERIFIED])
            .order_by("-created_at")
            .first()
        )
        local_valid_copies = BackupRecord.objects.filter(
            status__in=[BackupRecordStatus.SUCCESS, BackupRecordStatus.VERIFIED]
        ).count()

        # Próxima ejecución programada a las 22:00 UTC-06:00
        now = timezone.now()
        next_run = now.replace(hour=22, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run = next_run + timezone.timedelta(days=1)

        payload = {
            "last_attempt_at": last_attempt.created_at.isoformat() if last_attempt else None,
            "last_valid_backup": {
                "backup_id": str(last_valid.backup_id),
                "created_at": last_valid.created_at.isoformat(),
                "size_bytes": last_valid.size_bytes,
                "sha256": last_valid.ciphertext_sha256,
                "status": last_valid.status,
            } if last_valid else None,
            "local_valid_copies": local_valid_copies,
            "drive_status": last_valid.drive_status if last_valid else "PENDING",
            "next_retry_at": next_run.isoformat(),
            "error": last_attempt.error_message if (last_attempt and last_attempt.status == BackupRecordStatus.FAILED) else None,
        }
        return Response(payload)


class BackupExecuteView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser, HasApplicationRole]
    required_role = ADMIN_ROLE

    @method_decorator(csrf_protect)
    def post(self, request: Request) -> Response:
        from system.backup import execute_backup
        from system.serializers import BackupExecuteSerializer

        serializer = BackupExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        simulate_drive = serializer.validated_data.get("simulate_drive", True)

        record = execute_backup(actor=request.user, simulate_drive_success=simulate_drive)
        return Response(
            {
                "backup_id": str(record.backup_id),
                "status": record.status,
                "size_bytes": record.size_bytes,
                "dump_sha256": record.dump_sha256,
                "ciphertext_sha256": record.ciphertext_sha256,
                "drive_status": record.drive_status,
                "created_at": record.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class RestorationRequestView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser, HasApplicationRole]
    required_role = ADMIN_ROLE

    @method_decorator(csrf_protect)
    def post(self, request: Request) -> Response:
        from system.backup import request_restoration
        from system.models import BackupRecord
        from system.serializers import RestorationRequestSerializer

        serializer = RestorationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        backup_id = serializer.validated_data["backup_id"]

        backup = BackupRecord.objects.filter(backup_id=backup_id).first()
        if not backup:
            raise exceptions.NotFound("El respaldo especificado no existe.")

        session_key = request.session.session_key
        if not session_key:
            request.session.save()
            session_key = request.session.session_key

        restoration = request_restoration(backup_id=backup_id, actor=request.user, session_key=session_key)

        return Response(
            {
                "restoration_id": str(restoration.restoration_id),
                "status": restoration.status,
                "warning": restoration.warning,
                "backup_created_at": restoration.backup.created_at.isoformat(),
                "restore_cutoff": restoration.backup.created_at.isoformat(),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class RestorationConfirmationView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser, HasApplicationRole]
    required_role = ADMIN_ROLE

    @method_decorator(csrf_protect)
    def post(self, request: Request, restoration_id: str) -> Response:
        from system.backup import confirm_restoration
        from system.models import RestorationRequest
        from system.serializers import RestorationConfirmationSerializer

        try:
            r_uuid = UUID(restoration_id)
        except ValueError:
            raise exceptions.ValidationError("El identificador de restauración debe ser un UUID válido.")

        req = RestorationRequest.objects.filter(restoration_id=r_uuid).first()
        if not req:
            raise exceptions.NotFound("La solicitud de restauración especificada no existe.")

        serializer = RestorationConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        confirmation_id = serializer.validated_data["confirmation_id"]
        acknowledged = serializer.validated_data.get("acknowledged", True)
        session_key = request.session.session_key

        try:
            result = confirm_restoration(
                restoration_id=r_uuid,
                confirmation_id=confirmation_id,
                actor=request.user,
                session_key=session_key,
                acknowledged=acknowledged,
            )
        except PermissionError as e:
            raise exceptions.PermissionDenied(str(e))
        except ValueError as e:
            raise exceptions.ValidationError(str(e))

        return Response(result, status=status.HTTP_200_OK)

