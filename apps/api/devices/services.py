"""
devices/services.py

Capa de servicios para emparejamiento, autenticación y sincronización offline de dispositivos.
Implementa las especificaciones de 09-pseudocodigo.md §§7, 11 y 11-borrador-contrato-api.md §§28-36.
"""

import hashlib
import os
import secrets
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions, status

from catalog.exceptions import (
    BusinessConflictError,
    CostRequiredError,
    DomainValidationError,
    InsufficientStockError,
    ResourceNotFoundError,
)
from catalog.models import Component, InventoryMovement, MovementStatus, MovementType, RecipeVersion
from catalog.services import get_component_stock, register_receipt, round_currency, round_quantity
from devices.crypto import (
    base64url_decode,
    base64url_encode,
    derive_key_id,
    validate_timestamp,
    verify_device_signature,
)
from devices.models import (
    Device,
    DeviceNonce,
    DeviceQuota,
    DeviceStatus,
    PairingRequest,
    SyncEvent,
    SyncEventStatus,
)
from system.models import SystemInstance

QR_EXPIRY_MINUTES = 5


# ---------------------------------------------------------------------------
# Emparejamiento QR
# ---------------------------------------------------------------------------

def create_pairing_request(device_label: str, actor: Any) -> dict[str, Any]:
    """
    POST /api/v1/pairing-requests
    Inicia el alta de un dispositivo Android emitiendo un QR de un solo uso válido por 5 minutos.
    Windows almacena únicamente el hash SHA-256 del token aleatorio de 256 bits.
    """
    if not device_label or not device_label.strip():
        raise DomainValidationError("La etiqueta del dispositivo es obligatoria.")

    raw_token_bytes = secrets.token_bytes(32)  # 256 bits
    qr_token = base64url_encode(raw_token_bytes)
    qr_token_hash = hashlib.sha256(qr_token.encode("utf-8")).hexdigest()

    now = timezone.now()
    expires_at = now + timedelta(minutes=QR_EXPIRY_MINUTES)
    instance = SystemInstance.get_instance()

    pr = PairingRequest.objects.create(
        device_label=device_label.strip(),
        qr_token_hash=qr_token_hash,
        expires_at=expires_at,
        created_by=actor,
    )

    qr_payload = {
        "pairing_request_id": str(pr.pairing_request_id),
        "instance_id": str(instance.instance_id),
        "qr_token": qr_token,
        "expires_at": expires_at.isoformat(),
    }

    return {
        "pairing_request_id": str(pr.pairing_request_id),
        "qr_payload": qr_payload,
        "expires_at": expires_at.isoformat(),
    }


def complete_pairing_request(
    pairing_request_id: uuid.UUID,
    qr_token: str,
    public_key_spki: str,
    key_id: str,
    protocol_version: str = "v1",
) -> dict[str, Any]:
    """
    POST /api/v1/pairing-requests/{id}/complete
    Completa el alta del dispositivo Android con su clave pública.
    Idempotente: repetir exactamente el mismo ID/QR/clave pública reproduce
    el resultado persistido sin duplicar registros.
    """
    qr_hash = hashlib.sha256(qr_token.encode("utf-8")).hexdigest()

    with transaction.atomic():
        pr = (
            PairingRequest.objects.select_for_update()
            .filter(pairing_request_id=pairing_request_id)
            .first()
        )
        if pr is None:
            raise ResourceNotFoundError("Solicitud de emparejamiento no encontrada.")

        # Idempotencia: si ya fue consumido, verificar si coincide exactamente
        if pr.consumed:
            if pr.device is not None and pr.device.public_key_spki == public_key_spki and pr.qr_token_hash == qr_hash:
                return {
                    "device_id": pr.device.id,
                    "key_id": pr.device.key_id,
                    "initial_sequence": pr.device.last_sequence,
                    "pairing_status": "COMPLETED",
                }
            raise BusinessConflictError("Este código QR ya fue consumido con otra clave o dispositivo.")

        if timezone.now() > pr.expires_at:
            raise BusinessConflictError("El código QR ha expirado (validez de 5 minutos).")

        if pr.qr_token_hash != qr_hash:
            raise DomainValidationError("El token QR no coincide con la solicitud de emparejamiento.")

        # Validar derivación de key_id
        try:
            spki_der = base64url_decode(public_key_spki)
            expected_key_id = derive_key_id(spki_der)
        except Exception as exc:
            raise DomainValidationError("La clave pública SPKI no tiene un formato Base64URL válido.") from exc

        if key_id != expected_key_id:
            raise DomainValidationError(
                f"key_id no coincide con SHA-256 de SPKI: recibido {key_id}, esperado {expected_key_id}."
            )

        # Si ya existe un dispositivo con ese key_id activo, rechazar duplicación
        existing_device = Device.objects.filter(key_id=key_id).first()
        if existing_device is not None:
            raise BusinessConflictError("Ya existe un dispositivo registrado con esa clave pública.")

        device = Device.objects.create(
            device_label=pr.device_label,
            public_key_spki=public_key_spki,
            key_id=key_id,
            status=DeviceStatus.ACTIVE,
            last_sequence=0,
            quota_version=0,
        )

        pr.consumed = True
        pr.device = device
        pr.save(update_fields=["consumed", "device"])

        return {
            "device_id": device.id,
            "key_id": device.key_id,
            "initial_sequence": 0,
            "pairing_status": "COMPLETED",
        }


# ---------------------------------------------------------------------------
# Gestión de Dispositivos y Cuotas
# ---------------------------------------------------------------------------

def revoke_device(device_id: str, reason: str, actor: Any) -> dict[str, Any]:
    """
    POST /api/v1/devices/{id}/revocations
    Revoca un dispositivo conservando auditoría y su outbox remota pendiente.
    """
    if not reason or not reason.strip():
        raise DomainValidationError("El motivo de revocación es obligatorio.")

    with transaction.atomic():
        device = Device.objects.select_for_update().filter(id=device_id).first()
        if device is None:
            raise ResourceNotFoundError(f"Dispositivo {device_id} no encontrado.")

        now = timezone.now()
        device.status = DeviceStatus.REVOKED
        device.revoked_at = now
        device.revocation_reason = reason.strip()
        device.save(update_fields=["status", "revoked_at", "revocation_reason", "updated_at"])

        return {
            "device_id": device.id,
            "status": device.status,
            "revoked_at": now.isoformat(),
        }


def assign_device_quota(
    device_id: str,
    quota_version: int,
    components_data: list[dict[str, Any]],
    actor: Any,
) -> dict[str, Any]:
    """
    PUT /api/v1/devices/{id}/quota
    Asigna cantidades por componente dentro del stock central disponible.
    Exige dispositivo activo y costo promedio conocido para cantidades mayores a cero.
    """
    with transaction.atomic():
        device = Device.objects.select_for_update().filter(id=device_id).first()
        if device is None:
            raise ResourceNotFoundError(f"Dispositivo {device_id} no encontrado.")

        if device.status != DeviceStatus.ACTIVE:
            raise BusinessConflictError("No se puede asignar cuota a un dispositivo revocado.")

        result_components = []
        for item in components_data:
            c_id = item["component_id"]
            qty = Decimal(str(item["quantity"]))
            if qty < Decimal("0"):
                raise DomainValidationError("La cantidad de cuota no puede ser negativa.")

            comp = Component.objects.select_for_update().filter(id=c_id).first()
            if comp is None:
                raise ResourceNotFoundError(f"Componente {c_id} no encontrado.")

            stock_central = get_component_stock(comp.id)
            if qty > stock_central:
                raise InsufficientStockError(
                    f"Stock central insuficiente para asignar cuota de {comp.name}: requerido {qty}, disponible {stock_central}."
                )

            if qty > Decimal("0") and comp.average_cost is None:
                raise CostRequiredError(
                    f"El componente {comp.name} carece de costo promedio conocido; no puede asignarse como cuota."
                )

            quota_obj, _ = DeviceQuota.objects.get_or_create(device=device, component=comp)
            quota_obj.quantity = qty
            quota_obj.save(update_fields=["quantity"])

            result_components.append({
                "component_id": comp.id,
                "quantity": str(qty),
                "unit": comp.base_unit,
            })

        device.quota_version = quota_version
        device.save(update_fields=["quota_version", "updated_at"])

        return {
            "device_id": device.id,
            "quota_version": device.quota_version,
            "components": result_components,
        }


# ---------------------------------------------------------------------------
# Sincronización de Lotes de Eventos (Event Batches)
# ---------------------------------------------------------------------------

def process_event_batches(
    device_id: str,
    events: list[dict[str, Any]],
    actor: Any | None = None,
) -> list[dict[str, Any]]:
    """
    POST /api/v1/devices/{id}/event-batches
    Procesa eventos offline ordenados por secuencia.
    Idempotente por UUID; si hay conflicto o dispositivo revocado, pausa la cola.
    """
    acks = []

    with transaction.atomic():
        device = Device.objects.select_for_update().filter(id=device_id).first()
        if device is None:
            raise ResourceNotFoundError(f"Dispositivo {device_id} no encontrado.")

        # Ordenar eventos por secuencia recibida
        sorted_events = sorted(events, key=lambda e: int(e["sequence"]))

        for ev_data in sorted_events:
            ev_id = uuid.UUID(str(ev_data["event_id"]))
            seq = int(ev_data["sequence"])
            ev_type = str(ev_data["type"]).lower()
            payload = ev_data.get("payload", {})

            # 1. Idempotencia: si el UUID ya tiene resultado terminal registrado
            existing = SyncEvent.objects.filter(event_id=ev_id).first()
            if existing is not None:
                acks.append(existing.ack_response or {
                    "event_id": str(ev_id),
                    "status": existing.status,
                    "next_expected_sequence": device.last_sequence + 1,
                    "terminal_result": existing.terminal_result,
                })
                continue

            # 2. Si el dispositivo está revocado: registrar como conflicto y detener cola
            if device.status == DeviceStatus.REVOKED:
                ack_resp = {
                    "event_id": str(ev_id),
                    "status": SyncEventStatus.CONFLICT,
                    "next_expected_sequence": device.last_sequence + 1,
                    "terminal_result": False,
                    "conflict_code": "DEVICE_REVOKED",
                }
                SyncEvent.objects.create(
                    event_id=ev_id,
                    device=device,
                    sequence=seq,
                    event_type=ev_type,
                    payload=payload,
                    status=SyncEventStatus.CONFLICT,
                    conflict_reason="El dispositivo ha sido revocado administrativamente.",
                    terminal_result=False,
                    ack_response=ack_resp,
                )
                acks.append(ack_resp)
                break  # Cola detenida hasta resolución

            # 3. Verificación de secuencia estricta
            expected_seq = device.last_sequence + 1
            if seq != expected_seq:
                ack_resp = {
                    "event_id": str(ev_id),
                    "status": SyncEventStatus.CONFLICT,
                    "next_expected_sequence": expected_seq,
                    "terminal_result": False,
                    "conflict_code": "SEQUENCE_GAP",
                }
                SyncEvent.objects.create(
                    event_id=ev_id,
                    device=device,
                    sequence=seq,
                    event_type=ev_type,
                    payload=payload,
                    status=SyncEventStatus.CONFLICT,
                    conflict_reason=f"Secuencia fuera de orden: esperado {expected_seq}, recibido {seq}.",
                    terminal_result=False,
                    ack_response=ack_resp,
                )
                acks.append(ack_resp)
                break  # Cola detenida hasta resolución

            # 4. Procesar según tipo de evento
            conflict_detected = False
            conflict_msg = ""

            if ev_type == "sale":
                # Validar cuota del dispositivo para los componentes de la venta
                prod_id = payload.get("product_id")
                qty_sold = Decimal(str(payload.get("quantity", "1")))
                recipe = RecipeVersion.objects.filter(product_id=prod_id, active=True).prefetch_related("components__component").first()
                if recipe is None:
                    conflict_detected = True
                    conflict_msg = "Receta no configurada para el producto vendido."
                else:
                    for rc in recipe.components.all():
                        req_qty = rc.quantity * qty_sold
                        quota_obj = DeviceQuota.objects.filter(device=device, component=rc.component).first()
                        if quota_obj is None or quota_obj.quantity < req_qty:
                            conflict_detected = True
                            conflict_msg = f"Cuota insuficiente para {rc.component.name}: requerido {req_qty}."
                            break

                if not conflict_detected:
                    # Descontar cuota y registrar movimiento central
                    for rc in recipe.components.all():
                        req_qty = rc.quantity * qty_sold
                        quota_obj = DeviceQuota.objects.get(device=device, component=rc.component)
                        quota_obj.quantity -= req_qty
                        quota_obj.save(update_fields=["quantity"])

                        InventoryMovement.objects.create(
                            event_id=uuid.uuid4(),
                            component=rc.component,
                            movement_type=MovementType.SALE_CONSUME,
                            quantity_delta=-req_qty,
                            unit_cost=rc.component.average_cost,
                            status=MovementStatus.CONFIRMED,
                            occurred_at=timezone.now(),
                            reference_type="device_sale",
                            reference_id=str(ev_id),
                            device_id=device.id,
                        )

            elif ev_type == "return":
                # Aumenta cuota del dispositivo con los componentes devueltos
                prod_id = payload.get("product_id")
                qty_ret = Decimal(str(payload.get("quantity", "1")))
                recipe = RecipeVersion.objects.filter(product_id=prod_id, active=True).prefetch_related("components__component").first()
                if recipe is not None:
                    for rc in recipe.components.all():
                        reenter_qty = rc.quantity * qty_ret
                        quota_obj, _ = DeviceQuota.objects.get_or_create(device=device, component=rc.component)
                        quota_obj.quantity += reenter_qty
                        quota_obj.save(update_fields=["quantity"])

                        InventoryMovement.objects.create(
                            event_id=uuid.uuid4(),
                            component=rc.component,
                            movement_type=MovementType.RETURN_REENTRY,
                            quantity_delta=reenter_qty,
                            unit_cost=rc.component.average_cost or Decimal("0.000000"),
                            status=MovementStatus.CONFIRMED,
                            occurred_at=timezone.now(),
                            reference_type="device_return",
                            reference_id=str(ev_id),
                            device_id=device.id,
                        )

            elif ev_type == "entry":
                # Aumenta stock central no asignado (nunca la cuota Android automáticamente)
                comp_id = payload.get("component_id")
                entry_qty = Decimal(str(payload.get("quantity", "0")))
                entry_cost = Decimal(str(payload.get("unit_cost", "0")))
                register_receipt(
                    component_id=comp_id,
                    quantity=entry_qty,
                    unit_cost=entry_cost,
                    reference_type="device_entry",
                    reference_id=str(ev_id),
                    actor=actor,
                )

            if conflict_detected:
                ack_resp = {
                    "event_id": str(ev_id),
                    "status": SyncEventStatus.CONFLICT,
                    "next_expected_sequence": expected_seq,
                    "terminal_result": False,
                    "conflict_code": "INSUFFICIENT_QUOTA",
                    "details": conflict_msg,
                }
                SyncEvent.objects.create(
                    event_id=ev_id,
                    device=device,
                    sequence=seq,
                    event_type=ev_type,
                    payload=payload,
                    status=SyncEventStatus.CONFLICT,
                    conflict_reason=conflict_msg,
                    terminal_result=False,
                    ack_response=ack_resp,
                )
                acks.append(ack_resp)
                break  # Pausar cola

            # Éxito: avanzar secuencia y emitir ACK
            device.last_sequence = seq
            device.save(update_fields=["last_sequence", "updated_at"])

            ack_resp = {
                "event_id": str(ev_id),
                "status": SyncEventStatus.APPLIED,
                "next_expected_sequence": seq + 1,
                "terminal_result": True,
            }
            SyncEvent.objects.create(
                event_id=ev_id,
                device=device,
                sequence=seq,
                event_type=ev_type,
                payload=payload,
                status=SyncEventStatus.APPLIED,
                terminal_result=True,
                ack_response=ack_resp,
            )
            acks.append(ack_resp)

        return acks


# ---------------------------------------------------------------------------
# Resolución de Conflictos
# ---------------------------------------------------------------------------

def resolve_conflict(
    event_id: uuid.UUID,
    decision: str,
    reason: str,
    actor: Any,
) -> dict[str, Any]:
    """
    POST /api/v1/conflicts/{event_id}/resolutions
    Resuelve administrativamente un conflicto de sincronización.
    Aceptar: aplica operación y consume secuencia.
    Rechazar: consume secuencia mediante ACK terminal sin aplicar movimientos.
    """
    dec = decision.upper().strip()
    if dec not in {"ACCEPT", "REJECT"}:
        raise DomainValidationError("La decisión debe ser 'ACCEPT' o 'REJECT'.")
    if not reason or not reason.strip():
        raise DomainValidationError("El motivo de resolución es obligatorio.")

    with transaction.atomic():
        event = SyncEvent.objects.select_for_update().filter(event_id=event_id).first()
        if event is None:
            raise ResourceNotFoundError(f"Evento {event_id} no encontrado.")

        device = Device.objects.select_for_update().filter(id=event.device_id).first()
        if device is None:
            raise ResourceNotFoundError("Dispositivo asociado no encontrado.")

        # Si ya fue resuelto con resultado terminal
        if event.terminal_result:
            persisted_status = event.status
            if (dec == "ACCEPT" and persisted_status == SyncEventStatus.APPLIED) or (dec == "REJECT" and persisted_status == SyncEventStatus.REJECTED):
                return {
                    "event_id": str(event.event_id),
                    "decision": dec,
                    "terminal_ack": event.ack_response,
                    "next_expected_sequence": device.last_sequence + 1,
                    "audit_id": str(uuid.uuid4()),
                }
            raise BusinessConflictError("El conflicto ya fue resuelto con otra decisión terminal.")

        now = timezone.now()
        event.resolved_at = now
        event.resolved_by = actor
        event.resolution_reason = reason.strip()

        if dec == "ACCEPT":
            event.status = SyncEventStatus.APPLIED
            event.terminal_result = True
            # Avanzar secuencia del dispositivo si era la siguiente
            if event.sequence == device.last_sequence + 1:
                device.last_sequence = event.sequence
                device.save(update_fields=["last_sequence", "updated_at"])

            ack = {
                "event_id": str(event.event_id),
                "status": SyncEventStatus.APPLIED,
                "next_expected_sequence": device.last_sequence + 1,
                "terminal_result": True,
            }
            event.ack_response = ack
            event.save()

        elif dec == "REJECT":
            event.status = SyncEventStatus.REJECTED
            event.terminal_result = True
            # El rechazo consume la secuencia del evento sin aplicar movimientos
            if event.sequence == device.last_sequence + 1:
                device.last_sequence = event.sequence
                device.save(update_fields=["last_sequence", "updated_at"])

            ack = {
                "event_id": str(event.event_id),
                "status": SyncEventStatus.REJECTED,
                "next_expected_sequence": device.last_sequence + 1,
                "terminal_result": True,
            }
            event.ack_response = ack
            event.save()

        return {
            "event_id": str(event.event_id),
            "decision": dec,
            "terminal_ack": event.ack_response,
            "next_expected_sequence": device.last_sequence + 1,
            "audit_id": str(uuid.uuid4()),
        }
