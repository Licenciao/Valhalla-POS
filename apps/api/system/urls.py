from django.urls import path

from system.views import (
    BackupExecuteView,
    BackupStatusView,
    CsrfView,
    HealthView,
    RestorationConfirmationView,
    RestorationRequestView,
    SessionRevocationsView,
    SessionView,
)

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("auth/csrf", CsrfView.as_view(), name="csrf"),
    path("auth/session", SessionView.as_view(), name="session"),
    path(
        "auth/sessions/revocations",
        SessionRevocationsView.as_view(),
        name="session-revocations",
    ),
    path("backups/status", BackupStatusView.as_view(), name="backup-status"),
    path("backups/execute", BackupExecuteView.as_view(), name="backup-execute"),
    path("restorations", RestorationRequestView.as_view(), name="restorations"),
    path(
        "restorations/<str:restoration_id>/confirmations",
        RestorationConfirmationView.as_view(),
        name="restoration-confirmations",
    ),
]
