import json
import uuid
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import OperationalError
from django.test import Client, TestCase
from django.utils import timezone

from system.constants import OPERATOR_GROUP_NAME, SESSION_STARTED_AT_KEY
from system.models import SessionRevocationAudit, SystemInstance
from system.permissions import get_user_role


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.invalid",
            password="Admin-password-123!",
        )
        cls.operator = user_model.objects.create_user(
            username="operator",
            password="Operator-password-123!",
        )
        operator_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(operator_group)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def csrf_token(self, client=None):
        client = client or self.client
        response = client.get("/api/v1/auth/csrf")
        self.assertEqual(response.status_code, 200)
        self.assertIn(settings.SESSION_COOKIE_NAME, client.cookies)
        return response.json()["csrf_token"]

    def login(self, username, password, client=None):
        client = client or self.client
        token = self.csrf_token(client)
        response = client.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        return response

    def test_health_is_ready_and_instance_identity_is_stable(self):
        first = self.client.get("/api/v1/health")
        second = self.client.get("/api/v1/health")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "ok")
        self.assertEqual(first.json()["api_version"], "v1")
        self.assertEqual(first.json()["instance_id"], second.json()["instance_id"])
        self.assertEqual(SystemInstance.objects.count(), 1)
        self.assertEqual(
            str(SystemInstance.get_instance().instance_id),
            first.json()["instance_id"],
        )

    def test_health_returns_service_unavailable_if_database_is_down(self):
        with patch(
            "system.views.connection.ensure_connection",
            side_effect=OperationalError("database unavailable"),
        ):
            response = self.client.get("/api/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "SERVICE_UNAVAILABLE")
        self.assertTrue(response.json()["retryable"])

    def test_csrf_token_is_returned_and_required_for_login(self):
        token = self.csrf_token()
        self.assertTrue(token)
        self.assertNotIn("csrftoken", self.client.cookies)

        missing_token_response = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps(
                {"username": "admin", "password": "Admin-password-123!"}
            ),
            content_type="application/json",
        )

        self.assertEqual(missing_token_response.status_code, 403)
        self.assertEqual(missing_token_response.json()["code"], "CSRF_FAILED")
        self.assertFalse(missing_token_response.json()["retryable"])

    def test_login_session_and_logout_for_admin_and_operator(self):
        initial_token = self.csrf_token()
        admin_response = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps(
                {"username": "admin", "password": "Admin-password-123!"}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=initial_token,
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["role"], "ADMIN")
        self.assertIn("expires_at", admin_response.json())
        uuid.UUID(admin_response["X-Request-ID"])
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Strict")
        self.assertEqual(settings.SESSION_IDLE_TIMEOUT_SECONDS, 30 * 60)
        self.assertEqual(settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS, 8 * 60 * 60)

        session_response = self.client.get("/api/v1/auth/session")
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(session_response.json()["role"], "ADMIN")

        stale_csrf_response = self.client.delete(
            "/api/v1/auth/session",
            HTTP_X_CSRFTOKEN=initial_token,
        )
        self.assertEqual(stale_csrf_response.status_code, 403)
        self.assertEqual(stale_csrf_response.json()["code"], "CSRF_FAILED")

        operator_client = Client(enforce_csrf_checks=True)
        operator_response = self.login(
            "operator",
            "Operator-password-123!",
            client=operator_client,
        )
        self.assertEqual(operator_response.status_code, 200)
        self.assertEqual(operator_response.json()["role"], "OPERADOR")

        logout_token = self.csrf_token()
        logout_response = self.client.delete(
            "/api/v1/auth/session",
            HTTP_X_CSRFTOKEN=logout_token,
        )
        self.assertEqual(logout_response.status_code, 204)
        self.assertEqual(
            self.client.get("/api/v1/auth/session").json()["code"],
            "AUTHENTICATION_REQUIRED",
        )

    def test_login_errors_use_the_shared_envelope(self):
        token = self.csrf_token()
        response = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": "admin", "password": "wrong"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_REQUIRED")
        self.assertFalse(response.json()["retryable"])
        self.assertTrue(response.json()["request_id"])
        self.assertEqual(
            set(response.json()),
            {"code", "message", "request_id", "retryable", "details"},
        )

    def test_operator_cannot_revoke_sessions(self):
        self.assertEqual(
            self.login("operator", "Operator-password-123!").status_code,
            200,
        )
        token = self.csrf_token()
        response = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "Prueba de permisos"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "PERMISSION_DENIED")

    def test_operator_role_is_provisioned_by_local_command(self):
        user = get_user_model().objects.create_user(
            username="command_operator",
            password="Command-password-123!",
        )

        call_command(
            "provision_operator",
            "--username",
            user.username,
            stdout=StringIO(),
        )

        self.assertEqual(get_user_role(user), "OPERADOR")

    def test_admin_revocation_invalidates_all_sessions_and_is_audited(self):
        self.assertEqual(
            self.login("admin", "Admin-password-123!").status_code,
            200,
        )
        operator_client = Client(enforce_csrf_checks=True)
        self.assertEqual(
            self.login(
                "operator",
                "Operator-password-123!",
                client=operator_client,
            ).status_code,
            200,
        )

        token = self.csrf_token()
        idempotency_key = str(uuid.uuid4())
        response = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "Rotación administrativa"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revoked_sessions"], 2)
        self.assertEqual(
            Session.objects.filter(expire_date__gt=timezone.now()).count(),
            0,
        )
        self.assertEqual(SessionRevocationAudit.objects.count(), 1)
        self.assertEqual(
            self.client.get("/api/v1/auth/session").status_code,
            401,
        )
        self.assertEqual(
            operator_client.get("/api/v1/auth/session").status_code,
            401,
        )

    def test_revocation_idempotency_replays_result_and_rejects_changed_payload(self):
        self.assertEqual(
            self.login("admin", "Admin-password-123!").status_code,
            200,
        )
        idempotency_key = str(uuid.uuid4())
        first_token = self.csrf_token()
        original = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "Revisión programada"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=first_token,
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(original.status_code, 200)

        self.assertEqual(
            self.login("admin", "Admin-password-123!").status_code,
            200,
        )
        replay = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "Revisión programada"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token(),
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), original.json())
        self.assertEqual(SessionRevocationAudit.objects.count(), 1)
        self.assertEqual(self.client.get("/api/v1/auth/session").status_code, 200)

        conflict = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "Motivo diferente"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.csrf_token(),
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_validation_errors_use_the_common_envelope(self):
        self.assertEqual(
            self.login("admin", "Admin-password-123!").status_code,
            200,
        )
        token = self.csrf_token()
        response = self.client.post(
            "/api/v1/auth/sessions/revocations",
            data=json.dumps({"reason": "   "}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        self.assertIsInstance(response.json()["details"], dict)


class SessionExpiryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.invalid",
            password="Admin-password-123!",
        )

    def test_authenticated_session_expires_on_idle_and_absolute_limits(self):
        client = Client(enforce_csrf_checks=True)
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        login_response = client.post(
            "/api/v1/auth/session",
            data=json.dumps(
                {"username": "admin", "password": "Admin-password-123!"}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login_response.status_code, 200)
        started_at = float(client.session[SESSION_STARTED_AT_KEY])

        with patch(
            "system.middleware.time.time",
            return_value=started_at + settings.SESSION_IDLE_TIMEOUT_SECONDS,
        ):
            idle_expired = client.get("/api/v1/auth/session")
        self.assertEqual(idle_expired.status_code, 401)
        self.assertEqual(idle_expired.json()["code"], "AUTHENTICATION_REQUIRED")

        absolute_client = Client(enforce_csrf_checks=True)
        csrf = absolute_client.get("/api/v1/auth/csrf").json()["csrf_token"]
        login_response = absolute_client.post(
            "/api/v1/auth/session",
            data=json.dumps(
                {"username": "admin", "password": "Admin-password-123!"}
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(login_response.status_code, 200)
        started_at = float(absolute_client.session[SESSION_STARTED_AT_KEY])

        with patch(
            "system.middleware.time.time",
            return_value=started_at + settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
        ):
            absolute_expired = absolute_client.get("/api/v1/auth/session")
        self.assertEqual(absolute_expired.status_code, 401)
        self.assertEqual(
            absolute_expired.json()["code"],
            "AUTHENTICATION_REQUIRED",
        )

    def test_idle_and_absolute_session_expiration(self):
        idle = settings.SESSION_IDLE_TIMEOUT_SECONDS
        absolute = settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS

        from system.middleware import session_expiry_seconds, session_is_expired

        self.assertEqual(session_expiry_seconds(100, 100, idle, absolute), idle)
        self.assertEqual(
            session_expiry_seconds(100, 100 + absolute - 60, idle, absolute),
            60,
        )
        self.assertTrue(session_is_expired(100, 100, 100 + idle, idle, absolute))
        self.assertTrue(
            session_is_expired(100, 100 + idle - 1, 100 + absolute, idle, absolute)
        )
