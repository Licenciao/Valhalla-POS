import uuid
from typing import Self

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone


class SystemInstance(models.Model):
    singleton_key = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton_key=1),
                name="system_instance_singleton_key_is_one",
            )
        ]

    @classmethod
    def get_instance(cls) -> Self:
        instance, _ = cls.objects.get_or_create(singleton_key=1)
        return instance


class SessionRevocationAudit(models.Model):
    audit_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor_user_id = models.CharField(max_length=64)
    actor_username = models.CharField(max_length=150)
    reason = models.TextField()
    revoked_sessions = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class RevocationIdempotency(models.Model):
    actor_user_id = models.CharField(max_length=64)
    idempotency_key = models.UUIDField()
    request_hash = models.CharField(max_length=64)
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor_user_id", "idempotency_key"],
                name="unique_session_revocation_idempotency",
            )
        ]


# ---------------------------------------------------------------------------
# Respaldo Cifrado y Restauración Aislada (P-10, CA-23, CA-33)
# ---------------------------------------------------------------------------

class BackupRecordStatus(models.TextChoices):
    SUCCESS = "SUCCESS", "Exitoso"
    FAILED = "FAILED", "Fallido"
    VERIFIED = "VERIFIED", "Verificado en entorno aislado"


class DriveSyncStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente de subida"
    SYNCED = "SYNCED", "Sincronizado con Drive (30 días retención)"
    FAILED = "FAILED", "Fallo de conexión Drive"


class BackupRecord(models.Model):
    """
    Registro histórico de respaldos cifrados generados diariamente a las 22:00 UTC-06:00
    o al siguiente inicio si la aplicación Windows estaba cerrada.
    """
    backup_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=BackupRecordStatus.choices,
        default=BackupRecordStatus.SUCCESS,
    )
    dump_sha256 = models.CharField(max_length=64)
    ciphertext_sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField(default=0)
    file_path = models.CharField(max_length=500)
    drive_status = models.CharField(
        max_length=20,
        choices=DriveSyncStatus.choices,
        default=DriveSyncStatus.PENDING,
    )
    drive_synced_at = models.DateTimeField(null=True, blank=True)
    manifest = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    error_message = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "registro de respaldo"
        verbose_name_plural = "registros de respaldos"

    def __str__(self) -> str:
        return f"Backup {self.backup_id} [{self.created_at.isoformat()}] - {self.status}"


class RestorationStatus(models.TextChoices):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION", "Pendiente de doble confirmación ADMIN"
    COMPLETED = "COMPLETED", "Completada"
    REJECTED = "REJECTED", "Rechazada"


class RestorationRequest(models.Model):
    """
    Solicitud de restauración controlada. Exige doble confirmación de ADMIN en la misma sesión.
    No modifica la base operativa hasta que se reciben ambas confirmaciones.
    """
    restoration_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    backup = models.ForeignKey(
        BackupRecord,
        on_delete=models.PROTECT,
        related_name="restoration_requests",
    )
    status = models.CharField(
        max_length=25,
        choices=RestorationStatus.choices,
        default=RestorationStatus.PENDING_CONFIRMATION,
    )
    actor = models.ForeignKey(
        get_user_model(),
        on_delete=models.PROTECT,
        related_name="+",
    )
    session_key = models.CharField(max_length=40)
    warning = models.TextField(
        default="ADVERTENCIA CRÍTICA: Restaurar este respaldo sobreescribirá la base operativa actual "
                "y causará la pérdida permanente de transacciones posteriores a la fecha del respaldo."
    )
    first_confirmation_id = models.UUIDField(null=True, blank=True)
    first_confirmation_at = models.DateTimeField(null=True, blank=True)
    second_confirmation_id = models.UUIDField(null=True, blank=True)
    second_confirmation_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    audit_log = models.JSONField(default=list, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "solicitud de restauración"
        verbose_name_plural = "solicitudes de restauración"

