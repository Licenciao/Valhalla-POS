"""
devices/models.py

Modelos de datos para emparejamiento, dispositivos, cuotas offline y sincronización.
Especificaciones de 09-pseudocodigo.md §§7, 11 y 11-borrador-contrato-api.md §§28-36.
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model


def default_device_id() -> str:
    return f"dev_{uuid.uuid4().hex[:16]}"


class DeviceStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Activo"
    REVOKED = "REVOKED", "Revocado"


class Device(models.Model):
    """
    Dispositivo móvil Android emparejado para operar offline con cuotas.
    Identificado por su clave exclusiva ECDSA P-256 en Android Keystore.
    """
    id = models.CharField(max_length=64, primary_key=True, default=default_device_id)
    device_label = models.CharField(max_length=150)
    status = models.CharField(
        max_length=20,
        choices=DeviceStatus.choices,
        default=DeviceStatus.ACTIVE,
    )
    # SPKI DER en Base64URL sin padding
    public_key_spki = models.TextField()
    # SHA-256 de los bytes DER en Base64URL sin padding
    key_id = models.CharField(max_length=64, unique=True)
    # Última secuencia ACK confirmada centralmente
    last_sequence = models.PositiveIntegerField(default=0)
    # Versión de cuota vigente
    quota_version = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "dispositivo"
        verbose_name_plural = "dispositivos"

    def __str__(self) -> str:
        return f"{self.device_label} ({self.id}) [{self.status}]"


class DeviceQuota(models.Model):
    """
    Cuota asignada por componente a un dispositivo Android.
    El dispositivo solo puede vender dentro de su saldo de cuota offline.
    """
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="quotas")
    component = models.ForeignKey(
        "catalog.Component",
        on_delete=models.PROTECT,
        related_name="device_quotas",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0.000000"))

    class Meta:
        unique_together = [("device", "component")]
        verbose_name = "cuota de dispositivo"
        verbose_name_plural = "cuotas de dispositivo"

    def __str__(self) -> str:
        return f"{self.device.device_label} → {self.component.name}: {self.quantity}"


class PairingRequest(models.Model):
    """
    Solicitud de emparejamiento iniciada por ADMIN en Windows.
    Genera un QR de un solo uso válido por 5 minutos.
    Almacena el hash SHA-256 del token aleatorio de 256 bits.
    """
    pairing_request_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    device_label = models.CharField(max_length=150)
    qr_token_hash = models.CharField(max_length=64)
    consumed = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    device = models.ForeignKey(
        Device,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "solicitud de emparejamiento"
        verbose_name_plural = "solicitudes de emparejamiento"

    def is_valid(self) -> bool:
        return not self.consumed and timezone.now() <= self.expires_at


class DeviceNonce(models.Model):
    """
    Registro atómico de nonces para prevención de replay en firmas Android.
    Retenido por 15 minutos; timestamp verificado dentro de ±5 minutos.
    """
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="nonces")
    nonce = models.CharField(max_length=64)
    timestamp = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("device", "nonce")]
        indexes = [
            models.Index(fields=["created_at"]),
        ]


class SyncEventStatus(models.TextChoices):
    APPLIED = "APPLIED", "Aplicado"
    CONFLICT = "CONFLICT", "Conflicto"
    REJECTED = "REJECTED", "Rechazado"


class SyncEvent(models.Model):
    """
    Evento offline recibido desde un dispositivo Android.
    Conserva el payload original y audita su aplicación o resolución.
    """
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="sync_events")
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=50)
    payload = models.JSONField()
    status = models.CharField(
        max_length=20,
        choices=SyncEventStatus.choices,
        default=SyncEventStatus.APPLIED,
    )
    conflict_reason = models.TextField(blank=True, default="")
    terminal_result = models.BooleanField(default=False)
    ack_response = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    resolution_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["device", "sequence"]
        unique_together = [("device", "sequence")]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["device", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.device.device_label} seq={self.sequence} [{self.status}]"
