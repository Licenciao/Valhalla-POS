"""
devices/views.py

Vistas API DRF para emparejamiento, dispositivos, cuotas, lotes de eventos y resolución de conflictos.
Cumple con las rutas, permisos, firmas y payloads de 11-borrador-contrato-api.md §§28-36.
"""

import time
import uuid
from typing import Any

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from rest_framework import exceptions, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.pagination import decode_cursor, encode_cursor
from catalog.views import IsAdminUserRole
from devices.crypto import (
    build_canonical_string,
    validate_timestamp,
    verify_device_signature,
)
from devices.models import Device, DeviceNonce, DeviceStatus, SyncEvent, SyncEventStatus
from devices.serializers import (
    ConflictResolutionSerializer,
    DeviceQuotaUpdateSerializer,
    DeviceRevocationSerializer,
    EventBatchSerializer,
    PairingCompleteSerializer,
    PairingRequestCreateSerializer,
)
from devices.services import (
    assign_device_quota,
    complete_pairing_request,
    create_pairing_request,
    process_event_batches,
    resolve_conflict,
    revoke_device,
)
from system.authentication import SessionAuthentication
from system.models import SystemInstance

PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Emparejamiento
# ---------------------------------------------------------------------------

@method_decorator(csrf_protect, name="dispatch")
class PairingRequestCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request) -> Response:
        serializer = PairingRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = create_pairing_request(
            device_label=data["device_label"],
            actor=request.user,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class PairingCompleteView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request, pairing_request_id: uuid.UUID) -> Response:
        serializer = PairingCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = complete_pairing_request(
            pairing_request_id=pairing_request_id,
            qr_token=data["qr_token"],
            public_key_spki=data["public_key_spki"],
            key_id=data["key_id"],
            protocol_version=data.get("protocol_version", "v1"),
        )
        return Response(result, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Dispositivos y Cuotas
# ---------------------------------------------------------------------------

class DevicesPageView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def get(self, request: Request, cursor: str = "inicio") -> Response:
        last_id_str = str(decode_cursor(cursor, "devices", str(request.user.pk)))
        qs = Device.objects.all().order_by("created_at")
        devices_list = list(qs[: PAGE_SIZE + 1])
        has_more = len(devices_list) > PAGE_SIZE
        items_slice = devices_list[:PAGE_SIZE]

        items = [
            {
                "device_id": d.id,
                "label": d.device_label,
                "status": d.status,
                "key_id": d.key_id,
                "last_sequence": d.last_sequence,
                "quota_version": d.quota_version,
            }
            for d in items_slice
        ]

        next_cursor = (
            encode_cursor("devices", str(request.user.pk), len(items_slice))
            if has_more and items_slice
            else None
        )
        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


@method_decorator(csrf_protect, name="dispatch")
class DeviceRevocationView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, device_id: str) -> Response:
        serializer = DeviceRevocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = revoke_device(
            device_id=device_id,
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(result, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class DeviceQuotaView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def put(self, request: Request, device_id: str) -> Response:
        serializer = DeviceQuotaUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = assign_device_quota(
            device_id=device_id,
            quota_version=data["quota_version"],
            components_data=data["components"],
            actor=request.user,
        )
        return Response(result, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Lotes de Eventos Sincronizados (Firma ECDSA)
# ---------------------------------------------------------------------------

class DeviceEventBatchView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request, device_id: str) -> Response:
        device = Device.objects.filter(id=device_id).first()
        if device is None:
            raise exceptions.NotFound("Dispositivo no encontrado.")

        # 1. Extracción de encabezados de firma
        key_id = request.headers.get("X-Device-Key-Id", "").strip()
        timestamp = request.headers.get("X-Device-Timestamp", "").strip()
        nonce = request.headers.get("X-Device-Nonce", "").strip()
        signature = request.headers.get("X-Device-Signature", "").strip()

        if not key_id or not timestamp or not nonce or not signature:
            raise exceptions.AuthenticationFailed(
                "Faltan encabezados de autenticación firmada (Key-Id, Timestamp, Nonce, Signature)."
            )

        if key_id != device.key_id:
            raise exceptions.AuthenticationFailed("key_id no corresponde a la clave registrada del dispositivo.")

        # 2. Validación de timestamp (±5 minutos)
        if not validate_timestamp(timestamp):
            raise exceptions.AuthenticationFailed("Timestamp fuera de tolerancia permitida (±5 minutos).")

        # 3. Validación de nonce (prevención de replay)
        if DeviceNonce.objects.filter(device=device, nonce=nonce).exists():
            raise exceptions.AuthenticationFailed("Replay detectado: el nonce ya fue consumido.")

        # Registrar nonce atómicamente
        DeviceNonce.objects.create(
            device=device,
            nonce=nonce,
            timestamp=float(timestamp),
        )

        # 4. Verificación de firma criptográfica
        raw_body = request.body
        instance = SystemInstance.get_instance()
        canonical = build_canonical_string(
            protocol_version="v1",
            instance_id=str(instance.instance_id),
            key_id=device.key_id,
            method="POST",
            path=request.path,
            body_bytes=raw_body,
            timestamp=timestamp,
            nonce=nonce,
        )

        if not verify_device_signature(device.public_key_spki, signature, canonical):
            raise exceptions.AuthenticationFailed("Firma criptográfica ECDSA inválida.")

        # 5. Procesamiento del lote
        serializer = EventBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        acks = process_event_batches(
            device_id=device.id,
            events=serializer.validated_data["events"],
        )
        return Response({"acks": acks}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Conflictos
# ---------------------------------------------------------------------------

class ConflictsPageView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def get(self, request: Request, cursor: str = "inicio") -> Response:
        qs = SyncEvent.objects.filter(status=SyncEventStatus.CONFLICT).order_by("-received_at")
        events_list = list(qs[: PAGE_SIZE + 1])
        has_more = len(events_list) > PAGE_SIZE
        items_slice = events_list[:PAGE_SIZE]

        items = [
            {
                "event_id": str(e.event_id),
                "device_id": e.device_id,
                "sequence": e.sequence,
                "event_type": e.event_type,
                "payload": e.payload,
                "conflict_code": "CONFLICT",
                "status": e.status,
                "conflict_reason": e.conflict_reason,
            }
            for e in items_slice
        ]

        next_cursor = (
            encode_cursor("conflicts", str(request.user.pk), len(items_slice))
            if has_more and items_slice
            else None
        )
        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


@method_decorator(csrf_protect, name="dispatch")
class ConflictResolutionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, event_id: uuid.UUID) -> Response:
        serializer = ConflictResolutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = resolve_conflict(
            event_id=event_id,
            decision=data["decision"],
            reason=data["reason"],
            actor=request.user,
        )
        return Response(result, status=status.HTTP_200_OK)
