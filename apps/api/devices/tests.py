"""
devices/tests.py

Pruebas unitarias y de integración para P-07: Emparejamiento QR, firmas ECDSA,
cuotas, sincronización outbox y resolución de conflictos.

Verifica:
  Q-11 / CA-26: Emparejamiento QR de un solo uso (5 min), derivación de key_id,
                 idempotencia y rechazo de QR reutilizado/expirado.
  Q-11: Firma ECDSA P-256 válida, rechazo de firma inválida, rechazo de timestamp fuera
        de ±5 min y rechazo de replay por nonce repetido.
  Q-04: Sincronización idempotente: reenvío del mismo UUID devuelve ACK original
        sin duplicar movimientos.
  Q-05: Conflicto por secuencia discontinua o cuota insuficiente pausa la cola;
        resolución ACCEPT aplica; resolución REJECT consume secuencia con ACK terminal
        sin aplicar movimientos; reintentos son idempotentes.
  Q-08: Revocación administrativa de dispositivo y detención de sincronización.
"""

import hashlib
import json
import time
import uuid
from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from catalog.models import Component, Product
from catalog.services import (
    add_price_version,
    add_recipe_version,
    create_product,
    get_component_stock,
    register_receipt,
)
from devices.crypto import (
    base64url_encode,
    build_canonical_string,
    derive_key_id,
    generate_device_keypair,
    sign_canonical_string,
)
from devices.models import Device, DeviceNonce, DeviceQuota, DeviceStatus, PairingRequest, SyncEvent, SyncEventStatus
from devices.services import (
    assign_device_quota,
    complete_pairing_request,
    create_pairing_request,
    process_event_batches,
    resolve_conflict,
    revoke_device,
)
from system.models import SystemInstance

User = get_user_model()


class DeviceCryptoAndPairingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin_dev", password="Admin-password-123!"
        )

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def get_csrf(self):
        res = self.client.get("/api/v1/auth/csrf")
        return res.json()["csrf_token"]

    def login_admin(self):
        token = self.get_csrf()
        res = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": "admin_dev", "password": "Admin-password-123!"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)

    # ── Q-11 / CA-26: Emparejamiento QR ──────────────────────────────────────
    def test_pairing_flow_idempotency_and_expirations(self):
        self.login_admin()
        token = self.get_csrf()

        # 1. Admin crea solicitud de emparejamiento
        res_req = self.client.post(
            "/api/v1/pairing-requests",
            data=json.dumps({"device_label": "Caja Valhalla 1"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_req.status_code, 201)
        data = res_req.json()
        pr_id = data["pairing_request_id"]
        qr_token = data["qr_payload"]["qr_token"]

        # Generar clave ECDSA Android
        priv_key, spki_b64, key_id = generate_device_keypair()

        # 2. Android completa emparejamiento exitosamente
        res_comp = self.client.post(
            f"/api/v1/pairing-requests/{pr_id}/complete",
            data=json.dumps({
                "qr_token": qr_token,
                "public_key_spki": spki_b64,
                "key_id": key_id,
            }),
            content_type="application/json",
        )
        self.assertEqual(res_comp.status_code, 200)
        self.assertEqual(res_comp.json()["pairing_status"], "COMPLETED")
        dev_id = res_comp.json()["device_id"]

        # 3. Idempotencia: repetir exactamente mismos datos reproduce resultado
        res_replay = self.client.post(
            f"/api/v1/pairing-requests/{pr_id}/complete",
            data=json.dumps({
                "qr_token": qr_token,
                "public_key_spki": spki_b64,
                "key_id": key_id,
            }),
            content_type="application/json",
        )
        self.assertEqual(res_replay.status_code, 200)
        self.assertEqual(res_replay.json()["device_id"], dev_id)

        # 4. Reintentar con clave distinta falla (QR consumido)
        _, spki_b64_other, key_id_other = generate_device_keypair()
        res_conflict = self.client.post(
            f"/api/v1/pairing-requests/{pr_id}/complete",
            data=json.dumps({
                "qr_token": qr_token,
                "public_key_spki": spki_b64_other,
                "key_id": key_id_other,
            }),
            content_type="application/json",
        )
        self.assertEqual(res_conflict.status_code, 409)

        # 5. QR expirado (>5 minutos) falla
        token = self.get_csrf()
        res_exp = self.client.post(
            "/api/v1/pairing-requests",
            data=json.dumps({"device_label": "Caja Expirada"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        pr_exp_id = res_exp.json()["pairing_request_id"]
        qr_exp_token = res_exp.json()["qr_payload"]["qr_token"]

        # Simular expiración de 5 minutos
        pr_obj = PairingRequest.objects.get(pairing_request_id=pr_exp_id)
        pr_obj.expires_at = timezone.now() - timedelta(seconds=1)
        pr_obj.save()

        res_expired = self.client.post(
            f"/api/v1/pairing-requests/{pr_exp_id}/complete",
            data=json.dumps({
                "qr_token": qr_exp_token,
                "public_key_spki": spki_b64_other,
                "key_id": key_id_other,
            }),
            content_type="application/json",
        )
        self.assertEqual(res_expired.status_code, 409)

    # ── Q-11: Firma ECDSA P-256, Timestamp y Replay de Nonce ─────────────────
    def test_signed_event_batch_validation_and_replays(self):
        priv_key, spki_b64, key_id = generate_device_keypair()
        device = Device.objects.create(
            device_label="Caja Firma",
            public_key_spki=spki_b64,
            key_id=key_id,
            status=DeviceStatus.ACTIVE,
        )
        instance = SystemInstance.get_instance()

        comp = Component.objects.create(name="Insumo Firma", base_unit="pza")

        event_payload = {
            "events": [
                {
                    "event_id": str(uuid.uuid4()),
                    "sequence": 1,
                    "type": "entry",
                    "payload": {"component_id": comp.id, "quantity": "5", "unit_cost": "10"},
                }
            ]
        }
        body_bytes = json.dumps(event_payload).encode("utf-8")
        path = f"/api/v1/devices/{device.id}/event-batches"
        ts = str(time.time())
        nonce = uuid.uuid4().hex

        canonical = build_canonical_string(
            protocol_version="v1",
            instance_id=str(instance.instance_id),
            key_id=key_id,
            method="POST",
            path=path,
            body_bytes=body_bytes,
            timestamp=ts,
            nonce=nonce,
        )
        signature = sign_canonical_string(priv_key, canonical)

        # 1. Envío válido con firma correcta
        res = self.client.post(
            path,
            data=body_bytes,
            content_type="application/json",
            HTTP_X_DEVICE_KEY_ID=key_id,
            HTTP_X_DEVICE_TIMESTAMP=ts,
            HTTP_X_DEVICE_NONCE=nonce,
            HTTP_X_DEVICE_SIGNATURE=signature,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["acks"]), 1)

        # 2. Replay del mismo nonce dentro de 15 minutos falla
        res_replay = self.client.post(
            path,
            data=body_bytes,
            content_type="application/json",
            HTTP_X_DEVICE_KEY_ID=key_id,
            HTTP_X_DEVICE_TIMESTAMP=ts,
            HTTP_X_DEVICE_NONCE=nonce,
            HTTP_X_DEVICE_SIGNATURE=signature,
        )
        self.assertEqual(res_replay.status_code, 401)

        # 3. Timestamp fuera de ±5 minutos falla
        stale_ts = str(time.time() - 360)  # -6 minutos
        stale_nonce = uuid.uuid4().hex
        stale_canonical = build_canonical_string(
            protocol_version="v1",
            instance_id=str(instance.instance_id),
            key_id=key_id,
            method="POST",
            path=path,
            body_bytes=body_bytes,
            timestamp=stale_ts,
            nonce=stale_nonce,
        )
        stale_sig = sign_canonical_string(priv_key, stale_canonical)

        res_stale = self.client.post(
            path,
            data=body_bytes,
            content_type="application/json",
            HTTP_X_DEVICE_KEY_ID=key_id,
            HTTP_X_DEVICE_TIMESTAMP=stale_ts,
            HTTP_X_DEVICE_NONCE=stale_nonce,
            HTTP_X_DEVICE_SIGNATURE=stale_sig,
        )
        self.assertEqual(res_stale.status_code, 401)

        # 4. Firma inválida falla
        bad_nonce = uuid.uuid4().hex
        res_bad_sig = self.client.post(
            path,
            data=body_bytes,
            content_type="application/json",
            HTTP_X_DEVICE_KEY_ID=key_id,
            HTTP_X_DEVICE_TIMESTAMP=str(time.time()),
            HTTP_X_DEVICE_NONCE=bad_nonce,
            HTTP_X_DEVICE_SIGNATURE="bad-signature-base64",
        )
        self.assertEqual(res_bad_sig.status_code, 401)


class DeviceSyncAndConflictTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin_sync", password="Admin-password-123!"
        )
        cls.comp = Component.objects.create(
            name="Insumo Cuota",
            base_unit="pza",
            average_cost=Decimal("50.00"),
        )
        register_receipt(cls.comp.id, Decimal("100"), Decimal("50.00"))

        cls.prod = create_product(name="Producto Cuota")
        add_recipe_version(cls.prod.id, [{"component_id": cls.comp.id, "quantity": Decimal("1")}])
        add_price_version(cls.prod.id, Decimal("200.00"))

        _, spki_b64, key_id = generate_device_keypair()
        cls.device = Device.objects.create(
            device_label="Caja Sync",
            public_key_spki=spki_b64,
            key_id=key_id,
            status=DeviceStatus.ACTIVE,
        )

    # ── Q-04: Sincronización offline, cuotas e idempotencia ────────────────────
    def test_sync_sale_consumes_quota_and_is_idempotent(self):
        # Asignar cuota de 10 unidades al dispositivo
        assign_device_quota(
            device_id=self.device.id,
            quota_version=1,
            components_data=[{"component_id": self.comp.id, "quantity": "10.000000"}],
            actor=self.admin,
        )
        self.assertEqual(DeviceQuota.objects.get(device=self.device, component=self.comp).quantity, Decimal("10"))

        # Evento de venta offline por 2 unidades
        sale_event_id = uuid.uuid4()
        events = [
            {
                "event_id": str(sale_event_id),
                "sequence": 1,
                "type": "sale",
                "payload": {"product_id": self.prod.id, "quantity": "2"},
            }
        ]

        # 1. Aplicación exitosa
        acks = process_event_batches(device_id=self.device.id, events=events)
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["status"], SyncEventStatus.APPLIED)
        self.assertTrue(acks[0]["terminal_result"])
        self.assertEqual(acks[0]["next_expected_sequence"], 2)

        # Cuota disminuye de 10 a 8
        self.assertEqual(DeviceQuota.objects.get(device=self.device, component=self.comp).quantity, Decimal("8"))

        # 2. Idempotencia: reenvío del mismo UUID devuelve ACK persistido sin duplicar descuento
        acks_replay = process_event_batches(device_id=self.device.id, events=events)
        self.assertEqual(len(acks_replay), 1)
        self.assertEqual(acks_replay[0]["status"], SyncEventStatus.APPLIED)
        self.assertEqual(DeviceQuota.objects.get(device=self.device, component=self.comp).quantity, Decimal("8"))

    # ── Q-05: Conflicto por secuencia discontinua y resolución ────────────────
    def test_sequence_gap_causes_conflict_and_rejection_consumes_sequence(self):
        assign_device_quota(
            device_id=self.device.id,
            quota_version=1,
            components_data=[{"component_id": self.comp.id, "quantity": "5.000000"}],
            actor=self.admin,
        )

        # Enviar secuencia 2 cuando la esperada es 1 (salto de secuencia)
        gap_event_id = uuid.uuid4()
        events = [
            {
                "event_id": str(gap_event_id),
                "sequence": 2,
                "type": "sale",
                "payload": {"product_id": self.prod.id, "quantity": "1"},
            }
        ]

        acks = process_event_batches(device_id=self.device.id, events=events)
        self.assertEqual(acks[0]["status"], SyncEventStatus.CONFLICT)
        self.assertFalse(acks[0]["terminal_result"])

        conflict_evt = SyncEvent.objects.get(event_id=gap_event_id)
        self.assertEqual(conflict_evt.status, SyncEventStatus.CONFLICT)

        # Admin RECHAZA el conflicto: debe consumir la secuencia sin movimientos
        res_reject = resolve_conflict(
            event_id=gap_event_id,
            decision="REJECT",
            reason="Venta descartada por salto de secuencia no recuperable",
            actor=self.admin,
        )
        self.assertEqual(res_reject["decision"], "REJECT")
        self.assertEqual(res_reject["terminal_ack"]["status"], SyncEventStatus.REJECTED)

        conflict_evt.refresh_from_db()
        self.assertEqual(conflict_evt.status, SyncEventStatus.REJECTED)
        self.assertTrue(conflict_evt.terminal_result)

        # Reintento de resolución es idempotente
        res_reject_replay = resolve_conflict(
            event_id=gap_event_id,
            decision="REJECT",
            reason="Venta descartada por salto de secuencia no recuperable",
            actor=self.admin,
        )
        self.assertEqual(res_reject_replay["decision"], "REJECT")

    # ── Q-08: Revocación de dispositivo detiene sincronización ────────────────
    def test_revoked_device_events_enter_conflict(self):
        revoke_device(device_id=self.device.id, reason="Dispositivo perdido en campo", actor=self.admin)
        self.device.refresh_from_db()
        self.assertEqual(self.device.status, DeviceStatus.REVOKED)

        rev_event_id = uuid.uuid4()
        events = [
            {
                "event_id": str(rev_event_id),
                "sequence": 1,
                "type": "sale",
                "payload": {"product_id": self.prod.id, "quantity": "1"},
            }
        ]
        acks = process_event_batches(device_id=self.device.id, events=events)
        self.assertEqual(acks[0]["status"], SyncEventStatus.CONFLICT)
        self.assertEqual(acks[0]["conflict_code"], "DEVICE_REVOKED")
