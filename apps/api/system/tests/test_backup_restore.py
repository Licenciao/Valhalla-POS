import json
import os
import shutil
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone

from catalog.models import (
    Component,
    Expense,
    ExpenseType,
    ImportBatch,
    InventoryMovement,
    MovementStatus,
    MovementType,
    Product,
    PriceVersion,
    RecipeVersion,
    RecipeComponent,
    Sale,
    SaleLine,
    SaleStatus,
)
from catalog.services import confirm_initial_import, create_initial_import, create_sale_request, fulfill_sale
from devices.models import Device, DeviceQuota, DeviceStatus, SyncEvent, SyncEventStatus
from system.backup import (
    DEFAULT_BACKUP_KEY,
    apply_retention_policy,
    check_storage_space,
    decrypt_payload,
    execute_backup,
    verify_isolated_restore,
)
from system.constants import OPERATOR_GROUP_NAME
from system.errors import InsufficientStorageError
from system.models import (
    BackupRecord,
    BackupRecordStatus,
    DriveSyncStatus,
    RestorationRequest,
    RestorationStatus,
)


class BackupAndRestorationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_p10",
            email="admin_p10@example.invalid",
            password="Admin-password-123!",
        )
        cls.operator = user_model.objects.create_user(
            username="operator_p10",
            password="Operator-password-123!",
        )
        operator_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(operator_group)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.temp_backup_dir = Path(tempfile.mkdtemp(prefix="test_backups_"))

    def tearDown(self):
        shutil.rmtree(self.temp_backup_dir, ignore_errors=True)

    def csrf_token(self, client=None):
        c = client or self.client
        res = c.get("/api/v1/auth/csrf")
        self.assertEqual(res.status_code, 200)
        return res.json()["csrf_token"]

    def login_admin(self, client=None):
        c = client or self.client
        token = self.csrf_token(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": "admin_p10", "password": "Admin-password-123!"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)

    def login_operator(self, client=None):
        c = client or self.client
        token = self.csrf_token(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": "operator_p10", "password": "Operator-password-123!"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)

    def test_backup_status_permissions_and_response(self):
        # 1. Anónimo -> 401
        res = self.client.get("/api/v1/backups/status")
        self.assertEqual(res.status_code, 401)

        # 2. Operador -> 403
        self.login_operator()
        res = self.client.get("/api/v1/backups/status")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "PERMISSION_DENIED")

        # 3. Administrador -> 200 inicial
        self.client.post("/api/v1/auth/session", HTTP_X_CSRFTOKEN=self.csrf_token())  # logout
        self.login_admin()
        res = self.client.get("/api/v1/backups/status")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("last_attempt_at", data)
        self.assertIn("local_valid_copies", data)
        self.assertIn("drive_status", data)
        self.assertIn("next_retry_at", data)

    def test_backup_insufficient_space_alert_raises_507(self):
        # Q-09a: espacio libre < 2x tamaño exige alerta y no promueve incompleto
        with self.assertRaises(InsufficientStorageError):
            execute_backup(
                target_dir=self.temp_backup_dir,
                actor=self.admin,
                mock_free_bytes=100,  # Insuficiente (< 20MB)
            )

        self.assertEqual(BackupRecord.objects.count(), 0)
        enc_files = list(self.temp_backup_dir.glob("*.enc"))
        self.assertEqual(len(enc_files), 0)

    def test_execute_backup_creates_aes256_gcm_file_and_verifies_integrity(self):
        # Q-09b: Respaldo consistente cifrado con AES-256-GCM y verificación hash
        record = execute_backup(
            target_dir=self.temp_backup_dir,
            actor=self.admin,
            simulate_drive_success=True,
        )

        self.assertEqual(record.status, BackupRecordStatus.SUCCESS)
        self.assertEqual(record.drive_status, DriveSyncStatus.SYNCED)
        self.assertTrue(Path(record.file_path).exists())
        self.assertGreater(record.size_bytes, 0)
        self.assertEqual(len(record.ciphertext_sha256), 64)
        self.assertEqual(len(record.dump_sha256), 64)

        # Verificar que el contenido cifrado puede descifrarse con la clave
        with open(record.file_path, "rb") as f:
            encrypted_data = f.read()

        decrypted_bytes = decrypt_payload(encrypted_data, DEFAULT_BACKUP_KEY)
        payload = json.loads(decrypted_bytes.decode("utf-8"))

        self.assertIn("manifest", payload)
        self.assertIn("data", payload)
        self.assertEqual(payload["manifest"]["instance_id"], str(record.manifest["instance_id"]))

    def test_retention_policy_maintains_latest_valid_and_seven_previous(self):
        # Q-09c: Conservar último válido + 7 copias diarias anteriores (total 8)
        for i in range(10):
            execute_backup(
                target_dir=self.temp_backup_dir,
                actor=self.admin,
            )

        enc_files = list(self.temp_backup_dir.glob("backup_*.enc"))
        self.assertEqual(len(enc_files), 8)

    def test_isolated_restore_verification(self):
        # Q-09c / Q-15: Cotejo en entorno aislado de esquemas, integridad y conteos
        comp = Component.objects.create(
            name="Insumo P10 Test",
            sku="P10-01",
            base_unit="kg",
            average_cost=Decimal("50.0000"),
            active=True,
        )

        record = execute_backup(
            target_dir=self.temp_backup_dir,
            actor=self.admin,
        )

        verification = verify_isolated_restore(record)
        self.assertTrue(verification["isolated_restore_verified"])
        self.assertEqual(verification["integrity_check"], "ok")
        self.assertIn("components", verification["counts_matched"])
        self.assertTrue(verification["counts_matched"]["components"]["matched"])

        record.refresh_from_db()
        self.assertEqual(record.status, BackupRecordStatus.VERIFIED)

    def test_restoration_lifecycle_with_double_confirmation_in_same_session(self):
        # CA-33 / Q-19: Doble confirmación administrativa en la misma sesión
        record = execute_backup(
            target_dir=self.temp_backup_dir,
            actor=self.admin,
        )

        self.login_admin()
        csrf = self.csrf_token()

        # 1. Solicitar restauración (POST /api/v1/restorations)
        res = self.client.post(
            "/api/v1/restorations",
            data=json.dumps({"backup_id": str(record.backup_id)}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res.status_code, 202)
        body = res.json()
        restoration_id = body["restoration_id"]
        self.assertEqual(body["status"], "PENDING_CONFIRMATION")
        self.assertIn("ADVERTENCIA", body["warning"])

        # 2. Primera confirmación
        c1 = str(uuid.uuid4())
        res_c1 = self.client.post(
            f"/api/v1/restorations/{restoration_id}/confirmations",
            data=json.dumps({"confirmation_id": c1, "acknowledged": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_c1.status_code, 200)
        self.assertEqual(res_c1.json()["confirmations_received"], 1)
        self.assertEqual(res_c1.json()["status"], "PENDING_CONFIRMATION")

        # 3. Intentar repetir el mismo confirmation_id -> 400
        res_dup = self.client.post(
            f"/api/v1/restorations/{restoration_id}/confirmations",
            data=json.dumps({"confirmation_id": c1, "acknowledged": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_dup.status_code, 400)

        # 4. Segunda confirmación válida -> 200 COMPLETED
        c2 = str(uuid.uuid4())
        res_c2 = self.client.post(
            f"/api/v1/restorations/{restoration_id}/confirmations",
            data=json.dumps({"confirmation_id": c2, "acknowledged": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_c2.status_code, 200)
        self.assertEqual(res_c2.json()["confirmations_received"], 2)
        self.assertEqual(res_c2.json()["status"], "COMPLETED")
        self.assertIn("isolated_verification", res_c2.json())

    def test_e2e_full_business_lifecycle_with_backup_and_isolated_restore(self):
        """
        Prueba E2E Integral:
        1. Apertura e Importación Inicial Doble Confirmación (P-09).
        2. Configuración de Producto, Receta y Precio Activo (P-06).
        3. Venta y Surtido Atómico con Desglose IVA 16/116 (P-06/P-08).
        4. Alta de Dispositivo y Sincronización de Evento Offline (P-07).
        5. Ejecución de Respaldo Cifrado AES-256-GCM a las 22:00 (P-10).
        6. Validación de Restauración Aislada y Manifiesto (P-10).
        """
        # 1. Importación Inicial Conciliada
        comp = Component.objects.create(
            name="Agave Azul Premium",
            sku="AGV-E2E",
            base_unit="kg",
            average_cost=Decimal("40.0000"),
            active=True,
        )

        import_id = uuid.uuid4()
        create_initial_import(
            import_id=import_id,
            quantities=[{"component_id": comp.id, "quantity": "100.000000", "unit_cost": "40.0000"}],
            opening_expenses=[{"name": "Flete inicial", "amount": "500.00"}],
            actor=self.admin,
        )
        # Doble confirmación en la misma sesión
        session_key = "e2e_admin_session_key"
        confirm_initial_import(
            import_id=import_id,
            confirmation_id=uuid.uuid4(),
            preview_version=1,
            session_key=session_key,
            actor=self.admin,
        )
        confirm_initial_import(
            import_id=import_id,
            confirmation_id=uuid.uuid4(),
            preview_version=1,
            session_key=session_key,
            actor=self.admin,
        )

        # 2. Producto, Receta y Precio
        prod = Product.objects.create(name="Tequila Artesanal E2E", sku="TEQ-E2E", active=True)
        rec = RecipeVersion.objects.create(product=prod, version_number=1, active=True, created_by=self.admin)
        RecipeComponent.objects.create(recipe_version=rec, component=comp, quantity=Decimal("2.000000"))
        PriceVersion.objects.create(product=prod, version_number=1, price_with_tax=Decimal("600.00"), active=True, created_by=self.admin)

        # 3. Surtido Atómico de Venta (Windows Online)
        sale = create_sale_request(
            product_id=prod.id,
            quantity=Decimal("5.000000"),  # consume 10 kg
            unit_price=Decimal("600.00"),
            discount=Decimal("200.00"),  # Total = 2800.00
            actor=self.admin,
            device_id="windows_pos",
        )
        sale_fulfilled = fulfill_sale(
            sale_id=sale.id,
            actor=self.admin,
            device_id="windows_pos",
        )
        self.assertEqual(sale_fulfilled.status, SaleStatus.FULFILLED)
        self.assertEqual(sale_fulfilled.line.final_amount, Decimal("2800.00"))
        # IVA 16/116: 2800 * 16 / 116 = 386.21
        self.assertEqual(sale_fulfilled.line.tax_amount, Decimal("386.21"))

        # 4. Dispositivo Móvil y Sincronización de Evento
        dev = Device.objects.create(
            id="android_operator_1",
            device_label="Tablet Barra 1",
            status=DeviceStatus.ACTIVE,
            public_key_spki="MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEe2e_fake_pub_key",
            key_id="e2e_key_id",
            last_sequence=1,
        )
        SyncEvent.objects.create(
            event_id=uuid.uuid4(),
            device=dev,
            sequence=1,
            status=SyncEventStatus.APPLIED,
            event_type="sale",
            payload={"product_id": prod.id, "quantity": 1},
        )

        # 5. Ejecución del Respaldo Cifrado
        record = execute_backup(
            target_dir=self.temp_backup_dir,
            actor=self.admin,
            simulate_drive_success=True,
        )
        self.assertEqual(record.status, BackupRecordStatus.SUCCESS)
        self.assertGreater(record.manifest["table_counts"]["components"], 0)
        self.assertGreater(record.manifest["table_counts"]["sales"], 0)
        self.assertGreater(record.manifest["table_counts"]["devices"], 0)

        # 6. Verificación en Entorno Aislado
        verification = verify_isolated_restore(record)
        self.assertTrue(verification["isolated_restore_verified"])
        self.assertEqual(verification["integrity_check"], "ok")
        self.assertEqual(
            verification["counts_matched"]["sales"]["actual"],
            verification["counts_matched"]["sales"]["expected"],
        )
