"""
catalog/tests.py

P-06 — Pruebas unitarias de dominio y servicios para catálogo, recetas, inventario y ventas.

Verifica:
  CA-cat-01: Component se crea con unidad base y precisión; average_cost opcional.
  CA-cat-02: Product se crea inactivo por defecto (CA-35/Q-20).
  CA-cat-03: PriceVersion calcula el impuesto incluido (16/116) correctamente.
  CA-cat-04: RecipeVersion y RecipeComponent vinculan producto con sus insumos.
  CA-cat-05: InventoryMovement guarda event_id UUID único e inmutable.
  CA-cat-06: Sale y SaleLine congelan receta, precio y costo al surtir.
  CA-cat-07: Return y ReturnLine preservan el costo original del componente.
  CA-cat-08: ExpenseTemplate y Expense admiten tipos únicos y recurrentes.
  CA-srv-01: register_receipt calcula nuevo promedio ponderado móvil (Q-01: 10 a $10 + 5 a $16 = $12).
  CA-srv-02: register_receipt es idempotente ante el mismo event_id.
  CA-srv-03: add_recipe_version y add_price_version activan producto automáticamente al tener ambas (CA-35/Q-20).
  CA-srv-04: calculate_availability calcula unidades completas posibles a partir de sus componentes.
  CA-srv-05: fulfill_sale descuenta componentes, congela costos y calcula IVA (16/116).
  CA-srv-06: fulfill_sale falla atómicamente si el stock es insuficiente (Q-02).
  CA-srv-07: fulfill_sale falla si algún componente carece de costo conocido.
  CA-srv-08: register_return reingresa componentes completos con costo original congelado (Q-03).
"""

import importlib
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.exceptions import (
    BusinessConflictError,
    CostRequiredError,
    DomainValidationError,
    IdempotencyKeyReusedError,
    InsufficientStockError,
    OpeningExpenseProtectedError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from catalog.models import (
    CatalogIdempotency,
    Component,
    ComponentType,
    CustomTaxType,
    Expense,
    ExpenseTemplate,
    ExpenseType,
    ImportBatch,
    ImportBatchStatus,
    InventoryMovement,
    MovementStatus,
    MovementType,
    PriceVersion,
    Product,
    ProductCustomTax,
    RecipeComponent,
    RecipeVersion,
    Return,
    ReturnLine,
    ReturnSource,
    ReturnStatus,
    Sale,
    SaleLine,
    SaleStatus,
)
from catalog.services import (
    add_price_version,
    add_recipe_version,
    adjust_sale_quantity_from_excel,
    calculate_availability,
    compute_sale_allows_fractional,
    compute_sale_return_summary,
    create_product,
    create_product_custom_tax,
    create_sale_request,
    delete_product_custom_tax,
    fulfill_sale,
    get_component_stock,
    get_product_contador_summary,
    infer_return_quantity,
    normalize_tax_name,
    register_receipt,
    register_return,
    update_product_custom_tax,
    validate_tax_type_and_rate,
)

User = get_user_model()


class CatalogDomainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin_cat", password="Admin-password-123!"
        )

        cls.ensamble = Component.objects.create(
            name="Ensamble",
            component_type=ComponentType.LIQUID,
            base_unit="L",
            unit_precision=3,
            average_cost=Decimal("80.000000"),
        )
        cls.botella_750 = Component.objects.create(
            name="Botella Valhalla 750 ml",
            component_type=ComponentType.CONTAINER,
            base_unit="pza",
            unit_precision=0,
            average_cost=Decimal("55.190000"),
        )
        cls.corcho_sint = Component.objects.create(
            name="Corcho sintético ficha ancha",
            component_type=ComponentType.CLOSURE,
            base_unit="pza",
            unit_precision=0,
            average_cost=Decimal("12.500000"),
        )

    # ── CA-cat-01: Component ──────────────────────────────────────────────────
    def test_component_creation_and_fields(self):
        self.assertEqual(self.ensamble.base_unit, "L")
        self.assertEqual(self.ensamble.unit_precision, 3)
        self.assertEqual(self.ensamble.average_cost, Decimal("80.000000"))
        self.assertTrue(self.ensamble.active)

    # ── CA-cat-02: Product inactivo por defecto ──────────────────────────────
    def test_product_inactive_by_default(self):
        valhalla = Product.objects.create(name="Valhalla 750")
        self.assertFalse(valhalla.active)

    # ── CA-cat-03: PriceVersion y desglose de IVA (16/116) ───────────────────
    def test_price_version_tax_breakdown(self):
        valhalla = Product.objects.create(name="Valhalla 750 Test")
        price = PriceVersion.objects.create(
            product=valhalla,
            version_number=1,
            price_with_tax=Decimal("500.00"),
            active=True,
            created_by=self.admin,
        )
        self.assertEqual(price.price_with_tax, Decimal("500.00"))
        expected_tax = round(Decimal("500.00") * 16 / 116, 2)
        self.assertEqual(expected_tax, Decimal("68.97"))

    # ── CA-cat-04: RecipeVersion y componentes ────────────────────────────────
    def test_recipe_version_and_components(self):
        valhalla = Product.objects.create(name="Valhalla 750 Receta")
        recipe = RecipeVersion.objects.create(
            product=valhalla,
            version_number=1,
            active=True,
            created_by=self.admin,
        )
        RecipeComponent.objects.create(
            recipe_version=recipe,
            component=self.ensamble,
            quantity=Decimal("0.750000"),
        )
        RecipeComponent.objects.create(
            recipe_version=recipe,
            component=self.botella_750,
            quantity=Decimal("1.000000"),
        )
        RecipeComponent.objects.create(
            recipe_version=recipe,
            component=self.corcho_sint,
            quantity=Decimal("1.000000"),
        )

        self.assertEqual(recipe.components.count(), 3)
        ensamble_rc = recipe.components.get(component=self.ensamble)
        self.assertEqual(ensamble_rc.quantity, Decimal("0.750000"))

    # ── CA-cat-05: InventoryMovement UUID único e inmutable ───────────────────
    def test_inventory_movement_event_id_unique(self):
        evt_id = uuid.uuid4()
        now = timezone.now()
        InventoryMovement.objects.create(
            event_id=evt_id,
            component=self.ensamble,
            movement_type=MovementType.ENTRY,
            quantity_delta=Decimal("40.000000"),
            unit_cost=Decimal("80.000000"),
            average_cost_before=None,
            average_cost_after=Decimal("80.000000"),
            occurred_at=now,
            status=MovementStatus.CONFIRMED,
        )
        with self.assertRaises(IntegrityError):
            InventoryMovement.objects.create(
                event_id=evt_id,
                component=self.ensamble,
                movement_type=MovementType.ENTRY,
                quantity_delta=Decimal("40.000000"),
                unit_cost=Decimal("80.000000"),
                occurred_at=now,
                status=MovementStatus.CONFIRMED,
            )

    # ── CA-cat-06: Sale y SaleLine con congelación al surtir ─────────────────
    def test_sale_and_sale_line_lifecycle(self):
        valhalla = Product.objects.create(name="Valhalla 750 Venta")
        recipe = RecipeVersion.objects.create(
            product=valhalla,
            version_number=1,
            active=True,
            created_by=self.admin,
        )
        price = PriceVersion.objects.create(
            product=valhalla,
            version_number=1,
            price_with_tax=Decimal("500.00"),
            active=True,
            created_by=self.admin,
        )

        sale = Sale.objects.create(status=SaleStatus.PENDING)
        line = SaleLine.objects.create(
            sale=sale,
            product=valhalla,
            quantity=Decimal("2.000000"),
        )
        self.assertIsNone(line.recipe_version)
        self.assertIsNone(line.price_version)
        self.assertIsNone(line.final_amount)

        now = timezone.now()
        line.recipe_version = recipe
        line.price_version = price
        line.unit_price_with_tax = price.price_with_tax
        line.final_amount = Decimal("1000.00")
        line.tax_amount = round(Decimal("1000.00") * 16 / 116, 2)
        line.cost_applied = Decimal("295.38")
        line.save()

        sale.status = SaleStatus.FULFILLED
        sale.fulfilled_at = now
        sale.save()

        line.refresh_from_db()
        self.assertEqual(line.recipe_version, recipe)
        self.assertEqual(line.price_version, price)
        self.assertEqual(line.tax_amount, Decimal("137.93"))

    # ── CA-cat-07: Return y ReturnLine con costo original ────────────────────
    def test_return_preserves_original_cost(self):
        valhalla = Product.objects.create(name="Valhalla 750 Dev")
        sale = Sale.objects.create(status=SaleStatus.FULFILLED)
        line = SaleLine.objects.create(
            sale=sale,
            product=valhalla,
            quantity=Decimal("1.000000"),
            final_amount=Decimal("500.00"),
        )
        ret = Return.objects.create(
            sale_line=line,
            status=ReturnStatus.COMPLETED,
            returned_at=timezone.now(),
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )
        rl = ReturnLine.objects.create(
            ret=ret,
            component=self.ensamble,
            quantity_returned=Decimal("0.750000"),
            unit_cost_original=Decimal("80.000000"),
        )
        self.assertEqual(rl.unit_cost_original, Decimal("80.000000"))

    # ── CA-cat-08: Gastos únicos y plantillas recurrentes ───────────────────
    def test_expense_types_and_templates(self):
        etiquetas = Expense.objects.create(
            name="Etiquetas apertura",
            amount=Decimal("3250.00"),
            expense_type=ExpenseType.ONE_TIME,
            occurred_at=timezone.now(),
        )
        self.assertEqual(etiquetas.amount, Decimal("3250.00"))
        self.assertIsNone(etiquetas.template)

        template = ExpenseTemplate.objects.create(
            name="Renta local",
            amount=Decimal("5000.00"),
            expense_type=ExpenseType.RECURRING_MONTHLY,
            created_by=self.admin,
        )
        self.assertTrue(template.active)
        self.assertEqual(template.occurrences.count(), 0)


class CatalogServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="admin_srv", password="Admin-password-123!"
        )

    # ── CA-srv-01: Q-01 Entrada y promedio ponderado móvil ────────────────────
    def test_register_receipt_computes_moving_average_q01(self):
        """
        Caso de referencia Q-01 (10-plan-de-implementacion-y-gates.md):
        Existencia 10 a $10 -> Entrada de 5 a $16 -> Promedio resultante $12.
        """
        comp = Component.objects.create(
            name="Insumo Q01",
            base_unit="pza",
            unit_precision=0,
            average_cost=None,
        )
        # 1. Entrada inicial: 10 pzas a $10.00
        register_receipt(
            component_id=comp.id,
            quantity=Decimal("10.000000"),
            unit_cost=Decimal("10.000000"),
            actor=self.admin,
        )
        comp.refresh_from_db()
        self.assertEqual(get_component_stock(comp.id), Decimal("10.000000"))
        self.assertEqual(comp.average_cost, Decimal("10.000000"))

        # 2. Segunda entrada: 5 pzas a $16.00
        # (10*10 + 5*16) / 15 = (100 + 80) / 15 = 180 / 15 = 12.000000
        register_receipt(
            component_id=comp.id,
            quantity=Decimal("5.000000"),
            unit_cost=Decimal("16.000000"),
            actor=self.admin,
        )
        comp.refresh_from_db()
        self.assertEqual(get_component_stock(comp.id), Decimal("15.000000"))
        self.assertEqual(comp.average_cost, Decimal("12.000000"))

    # ── CA-srv-02: Idempotencia en entradas ──────────────────────────────────
    def test_register_receipt_idempotency(self):
        comp = Component.objects.create(
            name="Insumo Idempotente",
            base_unit="pza",
            unit_precision=0,
        )
        evt_id = uuid.uuid4()
        m1 = register_receipt(
            component_id=comp.id,
            quantity=Decimal("20.000000"),
            unit_cost=Decimal("5.000000"),
            event_id=evt_id,
            actor=self.admin,
        )
        # Reintento con mismo event_id
        m2 = register_receipt(
            component_id=comp.id,
            quantity=Decimal("20.000000"),
            unit_cost=Decimal("5.000000"),
            event_id=evt_id,
            actor=self.admin,
        )
        self.assertEqual(m1.id, m2.id)
        self.assertEqual(get_component_stock(comp.id), Decimal("20.000000"))
        self.assertEqual(InventoryMovement.objects.filter(component=comp).count(), 1)

    # ── CA-srv-03: Activación escalonada de producto (CA-35 / Q-20) ──────────
    def test_product_stepwise_activation(self):
        c1 = Component.objects.create(name="C1", base_unit="pza")
        prod = create_product(name="Licor Especial", actor=self.admin)
        self.assertFalse(prod.active)

        # 1. Agregar receta -> sigue inactivo
        add_recipe_version(
            product_id=prod.id,
            components_data=[{"component_id": c1.id, "quantity": Decimal("1")}],
            actor=self.admin,
        )
        prod.refresh_from_db()
        self.assertFalse(prod.active)

        # 2. Agregar precio -> se activa automáticamente
        add_price_version(
            product_id=prod.id,
            price_with_tax=Decimal("350.00"),
            actor=self.admin,
        )
        prod.refresh_from_db()
        self.assertTrue(prod.active)

    # ── CA-srv-04: Cálculo de disponibilidad de producto ─────────────────────
    def test_calculate_availability_bottleneck(self):
        liquid = Component.objects.create(
            name="Destilado", base_unit="L", unit_precision=3, average_cost=Decimal("80")
        )
        bottle = Component.objects.create(
            name="Botella 750", base_unit="pza", unit_precision=0, average_cost=Decimal("50")
        )
        cork = Component.objects.create(
            name="Corcho", base_unit="pza", unit_precision=0, average_cost=Decimal("10")
        )

        prod = create_product(name="Botella Final")
        add_recipe_version(
            product_id=prod.id,
            components_data=[
                {"component_id": liquid.id, "quantity": Decimal("0.750")},
                {"component_id": bottle.id, "quantity": Decimal("1")},
                {"component_id": cork.id, "quantity": Decimal("1")},
            ],
        )
        add_price_version(product_id=prod.id, price_with_tax=Decimal("500.00"))

        # Cargar stock:
        # Líquido: 3.0 L -> da para 4 botellas
        register_receipt(liquid.id, Decimal("3.000"), Decimal("80.00"))
        # Botellas: 10 pzas -> da para 10 botellas
        register_receipt(bottle.id, Decimal("10"), Decimal("50.00"))
        # Corchos: 2 pzas -> cuello de botella: solo 2 botellas!
        register_receipt(cork.id, Decimal("2"), Decimal("10.00"))

        avail = calculate_availability(prod.id)
        self.assertEqual(avail, 2)

    # ── CA-srv-05 / CA-srv-06: Surtido atómico de venta y falta de stock ─────
    def test_fulfill_sale_success_and_insufficient_stock(self):
        c_liq = Component.objects.create(
            name="Ensamble Venta", base_unit="L", unit_precision=3
        )
        c_bot = Component.objects.create(
            name="Botella Venta", base_unit="pza", unit_precision=0
        )
        register_receipt(c_liq.id, Decimal("1.500"), Decimal("80.00"))
        register_receipt(c_bot.id, Decimal("2"), Decimal("55.00"))

        prod = create_product(name="Valhalla Venta")
        add_recipe_version(
            product_id=prod.id,
            components_data=[
                {"component_id": c_liq.id, "quantity": Decimal("0.750")},
                {"component_id": c_bot.id, "quantity": Decimal("1")},
            ],
        )
        add_price_version(product_id=prod.id, price_with_tax=Decimal("500.00"))

        # 1. Crear solicitud de venta por 2 botellas
        sale = create_sale_request(
            product_id=prod.id,
            quantity=Decimal("2"),
            actor=self.admin,
        )
        self.assertEqual(sale.status, SaleStatus.PENDING)

        # 2. Surtir exitosamente
        fulfill_sale(sale.id, actor=self.admin)
        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleStatus.FULFILLED)
        self.assertEqual(get_component_stock(c_liq.id), Decimal("0.000000"))
        self.assertEqual(get_component_stock(c_bot.id), Decimal("0.000000"))
        # IVA desglosado: 1000 * 16 / 116 = 137.93
        self.assertEqual(sale.line.tax_amount, Decimal("137.93"))

        # 3. Intentar otra venta sin stock -> falla atómicamente
        sale2 = create_sale_request(
            product_id=prod.id,
            quantity=Decimal("1"),
            actor=self.admin,
        )
        with self.assertRaises(InsufficientStockError):
            fulfill_sale(sale2.id, actor=self.admin)

        sale2.refresh_from_db()
        self.assertEqual(sale2.status, SaleStatus.PENDING)

    # ── CA-srv-07: Surtido bloqueado si falta costo promedio ─────────────────
    def test_fulfill_sale_fails_if_cost_unknown(self):
        comp = Component.objects.create(
            name="Insumo Sin Costo", base_unit="pza", average_cost=None
        )
        # Movimiento sin costo promedio
        InventoryMovement.objects.create(
            component=comp,
            movement_type=MovementType.OPENING,
            quantity_delta=Decimal("10"),
            status=MovementStatus.CONFIRMED,
            occurred_at=timezone.now(),
        )
        prod = create_product(name="Prod Sin Costo")
        add_recipe_version(
            product_id=prod.id,
            components_data=[{"component_id": comp.id, "quantity": Decimal("1")}],
        )
        add_price_version(product_id=prod.id, price_with_tax=Decimal("100.00"))

        sale = create_sale_request(product_id=prod.id, quantity=Decimal("1"))
        with self.assertRaises(CostRequiredError):
            fulfill_sale(sale.id)

    # ── CA-srv-08: Devolución reingresa componentes con costo original ──────
    def test_return_reenters_components_at_original_cost(self):
        c_bot = Component.objects.create(name="Botella Dev", base_unit="pza")
        register_receipt(c_bot.id, Decimal("5"), Decimal("50.00"))

        prod = create_product(name="Valhalla Dev")
        add_recipe_version(
            prod.id, [{"component_id": c_bot.id, "quantity": Decimal("1")}]
        )
        add_price_version(prod.id, Decimal("500.00"))

        sale = create_sale_request(prod.id, Decimal("2"))
        fulfill_sale(sale.id)
        self.assertEqual(get_component_stock(c_bot.id), Decimal("3"))

        # Devolver 1 botella (precio unitario $500.00)
        ret = register_return(
            sale_id=sale.id,
            quantity_to_return=Decimal("1"),
            actor=self.admin,
        )
        self.assertEqual(ret.status, ReturnStatus.COMPLETED)
        self.assertEqual(ret.refund_amount, Decimal("500.00"))
        # Stock de botellas vuelve a subir a 4
        self.assertEqual(get_component_stock(c_bot.id), Decimal("4"))

        # Intentar devolver más de lo restante (solo queda 1)
        with self.assertRaises(BusinessConflictError):
            register_return(sale_id=sale.id, quantity_to_return=Decimal("2"))


class CatalogApiIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_api", password="Admin-password-123!"
        )
        cls.operator = user_model.objects.create_user(
            username="op_api", password="Op-password-123!"
        )
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

    def setUp(self):
        from django.test import Client
        self.client = Client(enforce_csrf_checks=True)

    def get_csrf(self, client=None):
        c = client or self.client
        res = c.get("/api/v1/auth/csrf")
        return res.json()["csrf_token"]

    def login(self, username, password, client=None):
        import json
        c = client or self.client
        token = self.get_csrf(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self.get_csrf(c)

    def test_admin_can_create_product_and_operator_is_forbidden(self):
        import json
        # 1. Operador intenta crear producto -> 403
        self.login("op_api", "Op-password-123!")
        token = self.get_csrf()
        res_op = self.client.post(
            "/api/v1/products",
            data=json.dumps({"name": "Whisky Prohibido"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_op.status_code, 403)
        self.assertEqual(res_op.json()["code"], "PERMISSION_DENIED")

        # 2. Admin crea producto exitosamente -> 201 (inactivo)
        admin_client = self.client
        self.login("admin_api", "Admin-password-123!", client=admin_client)
        token_admin = self.get_csrf(admin_client)
        res_admin = admin_client.post(
            "/api/v1/products",
            data=json.dumps({"name": "Valhalla 750 ML"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token_admin,
        )
        self.assertEqual(res_admin.status_code, 201)
        self.assertFalse(res_admin.json()["active"])
        prod_id = res_admin.json()["product_id"]

        # 3. Consultar productos mediante cursor
        res_list = admin_client.get("/api/v1/products/pages/inicio")
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(len(res_list.json()["items"]), 1)
        self.assertEqual(res_list.json()["items"][0]["name"], "Valhalla 750 ML")

    def test_full_catalog_inventory_and_sale_flow_via_api(self):
        import json
        admin_c = self.client
        self.login("admin_api", "Admin-password-123!", client=admin_c)
        token = self.get_csrf(admin_c)

        # 1. Crear componente líquido
        res_comp_liq = admin_c.post(
            "/api/v1/components",
            data=json.dumps({"name": "Ensamble API", "base_unit": "L", "unit_precision": 3}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_comp_liq.status_code, 201)
        liq_id = res_comp_liq.json()["component_id"]

        # 2. Crear componente envase
        token = self.get_csrf(admin_c)
        res_comp_bot = admin_c.post(
            "/api/v1/components",
            data=json.dumps({"name": "Botella API", "base_unit": "pza", "unit_precision": 0}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_comp_bot.status_code, 201)
        bot_id = res_comp_bot.json()["component_id"]

        # 3. Crear producto
        token = self.get_csrf(admin_c)
        res_prod = admin_c.post(
            "/api/v1/products",
            data=json.dumps({"name": "Mezcal Valhalla API"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        prod_id = res_prod.json()["product_id"]

        # 4. Registrar receta (0.750 L + 1 pza)
        token = self.get_csrf(admin_c)
        res_recipe = admin_c.post(
            f"/api/v1/products/{prod_id}/recipe-versions",
            data=json.dumps({
                "components": [
                    {"component_id": liq_id, "quantity": "0.750000"},
                    {"component_id": bot_id, "quantity": "1.000000"},
                ]
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_recipe.status_code, 201)
        self.assertFalse(res_recipe.json()["product_active"])

        # 5. Registrar precio ($500.00 con IVA) -> activa producto
        token = self.get_csrf(admin_c)
        res_price = admin_c.post(
            f"/api/v1/products/{prod_id}/price-versions",
            data=json.dumps({"amount": "500.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_price.status_code, 201)
        self.assertTrue(res_price.json()["product_active"])

        # 6. Registrar entrada de inventario (10 L de líquido a $80 y 10 botellas a $55)
        token = self.get_csrf(admin_c)
        res_receipt = admin_c.post(
            "/api/v1/inventory/receipts",
            data=json.dumps({
                "reference": "FACT-001",
                "lines": [
                    {"component_id": liq_id, "quantity": "10.000000", "unit_cost": "80.000000"},
                    {"component_id": bot_id, "quantity": "10.000000", "unit_cost": "55.000000"},
                ]
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_receipt.status_code, 201)

        # 7. Consultar existencias mediante cursor
        res_stock = admin_c.get("/api/v1/inventory/stock/pages/inicio")
        self.assertEqual(res_stock.status_code, 200)
        stock_items = {item["component_id"]: item for item in res_stock.json()["items"]}
        self.assertEqual(stock_items[liq_id]["central_unassigned"], "10.000000")
        self.assertEqual(stock_items[bot_id]["central_unassigned"], "10.000000")

        # 8. Realizar una venta de 2 botellas ($1000.00 total)
        token = self.get_csrf(admin_c)
        res_sale = admin_c.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": prod_id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_sale.status_code, 201)
        sale_data = res_sale.json()
        self.assertEqual(sale_data["status"], "FULFILLED")
        self.assertEqual(sale_data["amount_paid"], "1000.00")
        self.assertEqual(sale_data["tax"], "137.93")
        sale_id = sale_data["sale_id"]

        # Verificar descuento de inventario (10 - 1.5 = 8.5 L de líquido, 10 - 2 = 8 botellas)
        self.assertEqual(get_component_stock(liq_id), Decimal("8.500000"))
        self.assertEqual(get_component_stock(bot_id), Decimal("8.000000"))

        # 9. Realizar devolución de 1 botella -> reembolso $500.00
        token = self.get_csrf(admin_c)
        res_return = admin_c.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_return.status_code, 201)
        self.assertEqual(res_return.json()["refund"], "500.00")
        self.assertEqual(res_return.json()["tax_reversed"], "68.97")

        # Verificar reingreso al stock (8.5 + 0.75 = 9.25 L, 8 + 1 = 9 botellas)
        self.assertEqual(get_component_stock(liq_id), Decimal("9.250000"))
        self.assertEqual(get_component_stock(bot_id), Decimal("9.000000"))

    def test_create_custom_raw_material_components_pieces_and_liters(self):
        import json
        admin_c = self.client
        self.login("admin_api", "Admin-password-123!", client=admin_c)
        token = self.get_csrf(admin_c)

        # 1. Crear insumo en Litros con existencia inicial y costo
        res_liq = admin_c.post(
            "/api/v1/components",
            data=json.dumps({
                "name": "Destilado Añejo Roble",
                "sku": "LIQ-ROB-01",
                "component_type": "LIQUID",
                "base_unit": "litros",
                "initial_stock": "50.000000",
                "unit_cost": "120.000000",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_liq.status_code, 201)
        data_liq = res_liq.json()
        self.assertEqual(data_liq["base_unit"], "L")
        self.assertEqual(data_liq["precision"], 3)
        self.assertEqual(data_liq["stock"], "50.000000")
        self.assertEqual(data_liq["average_cost"], "120.000000")

        # 2. Crear insumo en Piezas con existencia inicial y costo
        token = self.get_csrf(admin_c)
        res_bot = admin_c.post(
            "/api/v1/components",
            data=json.dumps({
                "name": "Botella Cilíndrica 500ml",
                "sku": "BOT-CIL-500",
                "component_type": "CONTAINER",
                "base_unit": "piezas",
                "initial_stock": "100.000000",
                "unit_cost": "18.500000",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_bot.status_code, 201)
        data_bot = res_bot.json()
        self.assertEqual(data_bot["base_unit"], "pza")
        self.assertEqual(data_bot["precision"], 0)
        self.assertEqual(data_bot["stock"], "100.000000")
        self.assertEqual(data_bot["average_cost"], "18.500000")

        # 3. Validar existencia en el endpoint de inventario
        res_stock = admin_c.get("/api/v1/inventory/stock/pages/inicio")
        self.assertEqual(res_stock.status_code, 200)
        stock_items = {i["component_name"]: i for i in res_stock.json()["items"]}
        self.assertIn("Destilado Añejo Roble", stock_items)
        self.assertEqual(stock_items["Destilado Añejo Roble"]["central_unassigned"], "50.000000")
        self.assertEqual(stock_items["Destilado Añejo Roble"]["unit"], "L")
        self.assertIn("Botella Cilíndrica 500ml", stock_items)
        self.assertEqual(stock_items["Botella Cilíndrica 500ml"]["central_unassigned"], "100.000000")
        self.assertEqual(stock_items["Botella Cilíndrica 500ml"]["unit"], "pza")

    def test_create_new_bottle_with_dynamic_recipe_and_add_bottles_flow(self):
        import json
        admin_c = self.client
        self.login("admin_api", "Admin-password-123!", client=admin_c)

        # 1. Crear insumos personalizados
        token = self.get_csrf(admin_c)
        res_c1 = admin_c.post(
            "/api/v1/components",
            data=json.dumps({
                "name": "Líquido Especial V500",
                "sku": "LIQ-ESP-500",
                "component_type": "LIQUID",
                "base_unit": "L",
                "initial_stock": "20.000000",
                "unit_cost": "100.000000",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        c1_id = res_c1.json()["component_id"]

        token = self.get_csrf(admin_c)
        res_c2 = admin_c.post(
            "/api/v1/components",
            data=json.dumps({
                "name": "Botella Especial 500ml",
                "sku": "BOT-ESP-500",
                "component_type": "CONTAINER",
                "base_unit": "pza",
                "initial_stock": "30.000000",
                "unit_cost": "25.000000",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        c2_id = res_c2.json()["component_id"]

        # 2. Dar de alta la nueva botella con receta y precio en un solo llamado
        token = self.get_csrf(admin_c)
        res_prod = admin_c.post(
            "/api/v1/products",
            data=json.dumps({
                "name": "Valhalla Especial 500",
                "sku": "PROD-VAL-500",
                "description": "Edición limitada 500ml",
                "price_with_tax": "450.00",
                "components": [
                    {"component_id": c1_id, "quantity": "0.500"},
                    {"component_id": c2_id, "quantity": "1"},
                ],
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_prod.status_code, 201)
        prod_data = res_prod.json()
        self.assertTrue(prod_data["active"])
        self.assertEqual(prod_data["bottle_stock"], "0")
        prod_id = prod_data["product_id"]

        # 3. Embotellar 10 unidades de la nueva botella
        token = self.get_csrf(admin_c)
        res_emb = admin_c.post(
            f"/api/v1/products/{prod_id}/add-bottles",
            data=json.dumps({"quantity": "10"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_emb.status_code, 200)
        self.assertEqual(Decimal(res_emb.json()["bottles_added"]), Decimal("10"))
        self.assertEqual(Decimal(res_emb.json()["new_bottle_stock"]), Decimal("10"))

        # Verificar descuento exacto de insumos de su receta:
        # 10 * 0.500 L = 5.0 L descontados -> 20 - 5 = 15 L
        # 10 * 1 pza = 10 pzas descontadas -> 30 - 10 = 20 pzas
        self.assertEqual(get_component_stock(c1_id), Decimal("15.000000"))
        self.assertEqual(get_component_stock(c2_id), Decimal("20.000000"))

        # 4. Vender 2 botellas de la nueva botella
        token = self.get_csrf(admin_c)
        res_sale = admin_c.post(
            "/api/v1/sales",
            data=json.dumps({
                "product_id": prod_id,
                "quantity": "2.000000",
                "idempotency_key": "custom-bottle-sale-01",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_sale.status_code, 201)
        sale_id = res_sale.json()["sale_id"]
        self.assertEqual(res_sale.json()["amount_paid"], "900.00")

        # Validar que subinventario decrementó a 8
        prod_obj = Product.objects.get(id=prod_id)
        self.assertEqual(prod_obj.bottle_stock, Decimal("8.000000"))
        # Y las materias primas NO se tocaron adicionalmente
        self.assertEqual(get_component_stock(c1_id), Decimal("15.000000"))
        self.assertEqual(get_component_stock(c2_id), Decimal("20.000000"))

        # 5. Devolver 1 botella -> debe reingresar a botellas terminadas (8 + 1 = 9)
        token = self.get_csrf(admin_c)
        res_ret = admin_c.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_ret.status_code, 201)
        prod_obj.refresh_from_db()
        self.assertEqual(prod_obj.bottle_stock, Decimal("9.000000"))

    def test_mobile_pending_sale_and_approval_or_cancellation_flow(self):
        import json
        admin_c = self.client
        self.login("admin_api", "Admin-password-123!", client=admin_c)
        token = self.get_csrf(admin_c)

        # 1. Crear producto con stock de botellas terminadas
        c_liq = Component.objects.create(name="Base Test", base_unit="L", unit_precision=3, average_cost=Decimal("100.00"))
        c_bot = Component.objects.create(name="Botella Test", base_unit="pza", unit_precision=0, average_cost=Decimal("20.00"))
        prod = Product.objects.create(name="Valhalla Mobile", sku="PROD-VAL-MOB", active=True, bottle_stock=Decimal("10.000000"))
        pv = PriceVersion.objects.create(product=prod, version_number=1, price_with_tax=Decimal("600.00"), active=True)
        rv = RecipeVersion.objects.create(product=prod, version_number=1, active=True)
        RecipeComponent.objects.create(recipe_version=rv, component=c_liq, quantity=Decimal("0.750"))
        RecipeComponent.objects.create(recipe_version=rv, component=c_bot, quantity=Decimal("1.000"))

        # 2. Registrar venta multi-producto desde Android con status PENDING
        res_mob = admin_c.post(
            "/api/v1/sales",
            data=json.dumps({
                "status": "PENDING",
                "device_id": "android_terminal_99",
                "items": [
                    {"product_id": prod.id, "quantity": "2.000000", "discount": {"type": "AMOUNT", "value": "50.00"}}
                ]
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_mob.status_code, 201)
        mob_data = res_mob.json()
        self.assertEqual(mob_data["status"], "PENDING")
        sale_id = mob_data["sale_id"]

        # Validar que al estar PENDING, el stock NO se ha descontado aún (sigue siendo 10)
        prod.refresh_from_db()
        self.assertEqual(prod.bottle_stock, Decimal("10.000000"))

        # 3. Consultar la bandeja de ventas pendientes en Windows
        res_pending = admin_c.get("/api/v1/sales?status=PENDING")
        self.assertEqual(res_pending.status_code, 200)
        pending_sales = res_pending.json()["sales"]
        matching = [s for s in pending_sales if s["sale_id"] == sale_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["device_id"], "android_terminal_99")
        self.assertEqual(matching[0]["status"], "PENDING")

        # 4. Aceptar y surtir la venta desde Windows
        token = self.get_csrf(admin_c)
        res_fulfill = admin_c.post(
            f"/api/v1/sales/{sale_id}/fulfill",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_fulfill.status_code, 200)
        self.assertEqual(res_fulfill.json()["status"], "FULFILLED")

        # Validar que ahora SÍ se descontó el stock (10 - 2 = 8)
        prod.refresh_from_db()
        self.assertEqual(prod.bottle_stock, Decimal("8.000000"))

        # 5. Probar rechazo/cancelación de una segunda venta pendiente
        token = self.get_csrf(admin_c)
        res_mob2 = admin_c.post(
            "/api/v1/sales",
            data=json.dumps({
                "status": "PENDING",
                "device_id": "android_terminal_99",
                "items": [
                    {"product_id": prod.id, "quantity": "1.000000"}
                ]
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        sale2_id = res_mob2.json()["sale_id"]

        token = self.get_csrf(admin_c)
        res_cancel = admin_c.post(
            f"/api/v1/sales/{sale2_id}/cancel",
            data=json.dumps({"reason": "Venta cancelada por error de captura"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_cancel.status_code, 200)
        self.assertEqual(res_cancel.json()["status"], "CANCELLED")

        # El stock se conserva intacto en 8
        prod.refresh_from_db()
        self.assertEqual(prod.bottle_stock, Decimal("8.000000"))


class InitialImportIntegrationTests(TestCase):
    """
    P-09 — Pruebas de integración para Importación Inicial con Doble Confirmación Administrativa.
    Verifica:
      CA-28: Carga provisional, vista previa con advertencias, doble confirmación en la misma sesión,
             idempotencia ante el mismo import_id, corrección con movimientos compensatorios.
      CA-31: Doble confirmación administrativa obligatoria para oficializar inventario inicial.
    """

    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="admin_import",
            password="StrongPassword123!",
            email="admin@test.com",
        )
        self.other_admin = User.objects.create_superuser(
            username="other_admin",
            password="StrongPassword456!",
            email="other@test.com",
        )
        self.comp1 = Component.objects.create(
            name="Tequila Ensamble a Granel",
            sku="TEQ-ENS-GRANEL",
            base_unit="L",
            unit_precision=6,
        )
        self.comp2 = Component.objects.create(
            name="Envases 750ml",
            sku="BOT-VID-750",
            base_unit="pza",
            unit_precision=0,
        )

    def get_authenticated_client(self, user):
        from django.test import Client
        client = Client()
        client.force_login(user)
        # Establecer sesión activa
        session = client.session
        session["auth_role"] = "ADMIN"
        session.save()
        return client

    def get_csrf(self, client):
        res = client.get("/api/v1/auth/csrf")
        return res.json()["csrf_token"]

    def test_create_initial_import_is_provisional_and_excludes_available_stock(self):
        client = self.get_authenticated_client(self.admin)
        token = self.get_csrf(client)
        import_id = str(uuid.uuid4())

        payload = {
            "import_id": import_id,
            "quantities": [
                {
                    "component_id": self.comp1.id,
                    "quantity": "40.000000",
                    "unit": "L",
                    "reference_unit_cost": "80.000000",
                },
                {
                    "component_id": self.comp2.id,
                    "quantity": "72.000000",
                    "unit": "pza",
                    "reference_unit_cost": "25.000000",
                },
            ],
            "opening_expenses": [
                {
                    "name": "Gastos notariales de constitución",
                    "amount": "15000.00",
                    "currency": "MXN",
                }
            ],
        }

        res = client.post(
            "/api/v1/initial-imports",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["import_id"], import_id)
        self.assertEqual(data["status"], "PROVISIONAL")
        self.assertEqual(data["preview_version"], 1)

        # Verificar que el stock disponible oficial permanece en 0 (CA-28: provisional excluido)
        self.assertEqual(get_component_stock(self.comp1.id), Decimal("0.000000"))
        self.assertEqual(get_component_stock(self.comp2.id), Decimal("0.000000"))

        # Reintento idéntico es idempotente (201)
        res_retry = client.post(
            "/api/v1/initial-imports",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_retry.status_code, 201)

        # Mismo import_id con payload distinto se rechaza (409 IDEMPOTENCY_KEY_REUSED)
        different_payload = dict(payload, quantities=[{"component_id": self.comp1.id, "quantity": "99.000000"}])
        res_conflict = client.post(
            "/api/v1/initial-imports",
            data=json.dumps(different_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_conflict.status_code, 409)

    def test_preview_returns_structured_summary_and_warnings(self):
        client = self.get_authenticated_client(self.admin)
        token = self.get_csrf(client)
        import_id = str(uuid.uuid4())

        payload = {
            "import_id": import_id,
            "quantities": [
                {
                    "component_id": self.comp1.id,
                    "quantity": "40.000000",
                    "unit": "L",
                    "reference_unit_cost": "80.000000",
                }
            ],
            "opening_expenses": [],
        }
        client.post(
            "/api/v1/initial-imports",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        res_preview = client.get(f"/api/v1/initial-imports/{import_id}/preview")
        self.assertEqual(res_preview.status_code, 200)
        pdata = res_preview.json()
        self.assertEqual(pdata["import_id"], import_id)
        self.assertEqual(pdata["status"], "PROVISIONAL")
        self.assertEqual(pdata["confirmations_received"], 0)
        self.assertTrue(len(pdata["warnings"]) > 0)
        self.assertEqual(pdata["quantities"][0]["component_name"], "Tequila Ensamble a Granel")

    def test_double_confirmation_lifecycle_in_same_session(self):
        client = self.get_authenticated_client(self.admin)
        token = self.get_csrf(client)
        import_id = str(uuid.uuid4())

        # 1. Crear importación provisional
        payload = {
            "import_id": import_id,
            "quantities": [
                {
                    "component_id": self.comp1.id,
                    "quantity": "50.000000",
                    "unit": "L",
                    "reference_unit_cost": "85.000000",
                }
            ],
            "opening_expenses": [
                {
                    "name": "Gasto Apertura Notarial",
                    "amount": "5000.00",
                    "currency": "MXN",
                }
            ],
        }
        client.post(
            "/api/v1/initial-imports",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        # 2. Primera confirmación
        conf1_id = str(uuid.uuid4())
        res_conf1 = client.post(
            f"/api/v1/initial-imports/{import_id}/confirmations",
            data=json.dumps({
                "confirmation_id": conf1_id,
                "preview_version": 1,
                "physical_count": True,
                "verified_costs": True,
                "acknowledged": True,
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_conf1.status_code, 200)
        self.assertEqual(res_conf1.json()["confirmations_received"], 1)
        self.assertEqual(res_conf1.json()["status"], "PENDING_SECOND_CONFIRMATION")
        # El saldo sigue sin oficializarse
        self.assertEqual(get_component_stock(self.comp1.id), Decimal("0.000000"))

        # 3. Intento de segunda confirmación con OTRO administrador -> rechazado 403
        other_client = self.get_authenticated_client(self.other_admin)
        other_token = self.get_csrf(other_client)
        conf2_id = str(uuid.uuid4())
        res_fail_user = other_client.post(
            f"/api/v1/initial-imports/{import_id}/confirmations",
            data=json.dumps({
                "confirmation_id": conf2_id,
                "acknowledged": True,
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=other_token,
        )
        self.assertEqual(res_fail_user.status_code, 403)

        # 4. Segunda confirmación exitosa con el MISMO administrador y en la MISMA sesión
        res_conf2 = client.post(
            f"/api/v1/initial-imports/{import_id}/confirmations",
            data=json.dumps({
                "confirmation_id": conf2_id,
                "preview_version": 1,
                "physical_count": True,
                "verified_costs": True,
                "acknowledged": True,
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_conf2.status_code, 200)
        c2data = res_conf2.json()
        self.assertEqual(c2data["confirmations_received"], 2)
        self.assertEqual(c2data["status"], "CONFIRMED")
        self.assertEqual(c2data["audit_ids"], [conf1_id, conf2_id])

        # ¡Ahora sí está oficializado y disponible!
        self.assertEqual(get_component_stock(self.comp1.id), Decimal("50.000000"))
        self.comp1.refresh_from_db()
        self.assertEqual(self.comp1.average_cost, Decimal("85.000000"))

        # Gasto de apertura creado
        self.assertTrue(Expense.objects.filter(name="Gasto Apertura Notarial").exists())

    def test_correction_adds_compensating_movements_and_preserves_average_cost(self):
        client = self.get_authenticated_client(self.admin)
        token = self.get_csrf(client)
        import_id = str(uuid.uuid4())

        # 1. Crear e importar oficialmente 40 L a $80/L
        payload = {
            "import_id": import_id,
            "quantities": [
                {
                    "component_id": self.comp1.id,
                    "quantity": "40.000000",
                    "unit": "L",
                    "reference_unit_cost": "80.000000",
                }
            ],
        }
        client.post(
            "/api/v1/initial-imports",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        client.post(
            f"/api/v1/initial-imports/{import_id}/confirmations",
            data=json.dumps({"confirmation_id": str(uuid.uuid4()), "acknowledged": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        client.post(
            f"/api/v1/initial-imports/{import_id}/confirmations",
            data=json.dumps({"confirmation_id": str(uuid.uuid4()), "acknowledged": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(get_component_stock(self.comp1.id), Decimal("40.000000"))

        # 2. Corregir cantidad a 35 L (delta = -5 L)
        correction_id = str(uuid.uuid4())
        res_correct = client.post(
            f"/api/v1/initial-imports/{import_id}/corrections",
            data=json.dumps({
                "correction_id": correction_id,
                "quantities": [
                    {
                        "component_id": self.comp1.id,
                        "quantity": "35.000000",
                        "unit": "L",
                    }
                ],
                "reason": "Ajuste físico en báscula tras verificación de merma",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_correct.status_code, 200)
        self.assertEqual(res_correct.json()["status"], "CORRECTED")

        # 3. El saldo ahora es exactamente 35.000000 L
        self.assertEqual(get_component_stock(self.comp1.id), Decimal("35.000000"))

        # 4. Se conserva el costo promedio original de $80.000000 (CA-28 / §9)
        self.comp1.refresh_from_db()
        self.assertEqual(self.comp1.average_cost, Decimal("80.000000"))

        # 5. Reintento de la misma corrección es idempotente
        res_retry = client.post(
            f"/api/v1/initial-imports/{import_id}/corrections",
            data=json.dumps({
                "correction_id": correction_id,
                "quantities": [{"component_id": self.comp1.id, "quantity": "35.000000", "unit": "L"}],
                "reason": "Ajuste físico en báscula tras verificación de merma",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_retry.status_code, 200)


class ProductAndComponentEditDeleteTests(TestCase):
    """
    Pruebas unitarias y de integración para la edición y eliminación de
    botellas (productos) y materias primas (componentes).
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_edit_test", password="Admin-password-123!"
        )
        cls.operator = user_model.objects.create_user(
            username="op_edit_test", password="Op-password-123!"
        )
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

    def setUp(self):
        from django.test import Client
        self.client = Client(enforce_csrf_checks=True)

    def get_csrf(self, client=None):
        c = client or self.client
        res = c.get("/api/v1/auth/csrf")
        return res.json()["csrf_token"]

    def login(self, username, password, client=None):
        import json
        c = client or self.client
        token = self.get_csrf(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self.get_csrf(c)

    def test_product_edit_and_hard_delete_without_sales(self):
        import json
        admin_token = self.login("admin_edit_test", "Admin-password-123!")

        # 1. Crear producto con precio y receta iniciales
        res_create = self.client.post(
            "/api/v1/products",
            data=json.dumps({
                "name": "Botella Prueba 750",
                "sku": "PRU-750",
                "price_with_tax": "300.00",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_create.status_code, 201)
        prod_id = res_create.json()["product_id"]

        # 2. Operador no puede editar
        self.login("op_edit_test", "Op-password-123!")
        op_token = self.get_csrf()
        res_op_edit = self.client.put(
            f"/api/v1/products/{prod_id}",
            data=json.dumps({"name": "Intento Operador"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=op_token,
        )
        self.assertEqual(res_op_edit.status_code, 403)

        # 3. Admin consulta detalle
        admin_token = self.login("admin_edit_test", "Admin-password-123!")
        res_get = self.client.get(f"/api/v1/products/{prod_id}")
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()["name"], "Botella Prueba 750")

        # 4. Admin edita producto (nombre, sku, precio, botella stock)
        res_edit = self.client.patch(
            f"/api/v1/products/{prod_id}",
            data=json.dumps({
                "name": "Botella Prueba 750 Edición Roble",
                "sku": "PRU-750-ROBLE",
                "description": "Añejada en barrica",
                "price_with_tax": "380.00",
                "bottle_stock": "15",
                "active": True,
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_edit.status_code, 200)
        prod = Product.objects.get(id=prod_id)
        self.assertEqual(prod.name, "Botella Prueba 750 Edición Roble")
        self.assertEqual(prod.sku, "PRU-750-ROBLE")
        self.assertEqual(prod.bottle_stock, Decimal("15"))
        self.assertTrue(prod.active)
        self.assertEqual(prod.price_versions.filter(active=True).first().price_with_tax, Decimal("380.00"))

        # 5. Admin elimina producto sin ventas -> Hard Delete
        res_del = self.client.delete(
            f"/api/v1/products/{prod_id}",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.json()["status"], "DELETED")
        self.assertFalse(Product.objects.filter(id=prod_id).exists())

    def test_product_delete_with_sales_triggers_soft_delete(self):
        import json
        admin_token = self.login("admin_edit_test", "Admin-password-123!")

        # 1. Crear producto e insumo para receta
        comp = Component.objects.create(name="Mosto Especial", base_unit="L", unit_precision=3, average_cost=Decimal("50.000000"))
        # Inyectar stock de mosto
        from catalog.services import register_receipt
        register_receipt(comp.id, Decimal("100.000000"), Decimal("50.000000"), actor=self.admin)

        res_p = self.client.post(
            "/api/v1/products",
            data=json.dumps({
                "name": "Valhalla Reserva 1000",
                "sku": "VAL-RES-1000",
                "price_with_tax": "500.00",
                "components": [{"component_id": comp.id, "quantity": "1.000000"}],
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_p.status_code, 201)
        prod_id = res_p.json()["product_id"]

        # 2. Registrar y surtir una venta del producto
        from catalog.services import create_sale_request, fulfill_sale
        sale = create_sale_request(product_id=prod_id, quantity=Decimal("2.000000"), actor=self.admin)
        fulfill_sale(sale.id, actor=self.admin)

        # 3. Intentar eliminar el producto -> Soft delete con estado DEACTIVATED
        res_del = self.client.delete(
            f"/api/v1/products/{prod_id}",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.json()["status"], "DEACTIVATED")
        prod = Product.objects.get(id=prod_id)
        self.assertFalse(prod.active)
        self.assertIn("ventas históricas", res_del.json()["message"])

    def test_component_edit_and_stock_adjustment(self):
        import json
        admin_token = self.login("admin_edit_test", "Admin-password-123!")

        # 1. Crear componente sin stock inicial
        res_c = self.client.post(
            "/api/v1/components",
            data=json.dumps({
                "name": "Corcho Natural Granulado",
                "sku": "COR-NAT-01",
                "component_type": "CLOSURE",
                "base_unit": "pza",
                "unit_precision": 0,
                "unit_cost": "2.50",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_c.status_code, 201)
        comp_id = res_c.json()["component_id"]

        # 2. Admin edita y realiza un ajuste de stock a 150 piezas
        res_edit = self.client.patch(
            f"/api/v1/components/{comp_id}",
            data=json.dumps({
                "name": "Corcho Natural Premium",
                "sku": "COR-NAT-PREM",
                "stock": "150",
                "unit_cost": "3.00",
                "reason": "Conteo físico en bodega central",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_edit.status_code, 200)
        self.assertEqual(get_component_stock(comp_id), Decimal("150.000000"))
        comp = Component.objects.get(id=comp_id)
        self.assertEqual(comp.name, "Corcho Natural Premium")
        self.assertEqual(comp.average_cost, Decimal("3.000000"))

        # 3. Segundo ajuste: reducir a 140 piezas
        res_edit2 = self.client.patch(
            f"/api/v1/components/{comp_id}",
            data=json.dumps({
                "stock": "140",
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        self.assertEqual(res_edit2.status_code, 200)
        self.assertEqual(get_component_stock(comp_id), Decimal("140.000000"))

    def test_component_delete_protections(self):
        import json
        admin_token = self.login("admin_edit_test", "Admin-password-123!")

        # Caso 1: Componente limpio (sin recetas ni movimientos) -> Hard delete
        comp_clean = Component.objects.create(name="Envase Prueba Limpio", base_unit="pza", unit_precision=0)
        res_del_clean = self.client.delete(f"/api/v1/components/{comp_clean.id}", HTTP_X_CSRFTOKEN=admin_token)
        self.assertEqual(res_del_clean.status_code, 200)
        self.assertEqual(res_del_clean.json()["status"], "DELETED")
        self.assertFalse(Component.objects.filter(id=comp_clean.id).exists())

        # Caso 2: Componente usado en receta activa -> Rechazo 400
        comp_recipe = Component.objects.create(name="Líquido Receta Activa", base_unit="L", unit_precision=3)
        prod = Product.objects.create(name="Producto Con Receta Activa", base_unit="pza", active=True)
        rv = RecipeVersion.objects.create(product=prod, version_number=1, active=True)
        RecipeComponent.objects.create(recipe_version=rv, component=comp_recipe, quantity=Decimal("0.750000"))

        res_del_recipe = self.client.delete(f"/api/v1/components/{comp_recipe.id}", HTTP_X_CSRFTOKEN=admin_token)
        self.assertEqual(res_del_recipe.status_code, 400)
        self.assertIn("utilizada en la receta activa", res_del_recipe.json()["message"])

        # Caso 3: Componente con movimientos pero ya no en receta activa -> Soft delete
        rv.active = False
        rv.save()
        from catalog.services import register_receipt
        register_receipt(comp_recipe.id, Decimal("50.000000"), Decimal("10.000000"), actor=self.admin)

        res_del_soft = self.client.delete(f"/api/v1/components/{comp_recipe.id}", HTTP_X_CSRFTOKEN=admin_token)
        self.assertEqual(res_del_soft.status_code, 200)
        self.assertEqual(res_del_soft.json()["status"], "DEACTIVATED")
        comp_recipe.refresh_from_db()
        self.assertFalse(comp_recipe.active)


class CatalogSalesAndReturnsIdempotencyTests(TestCase):
    """
    MI-1 / MI-2 / MI-3 — Pruebas de idempotencia para POST /api/v1/sales y
    POST /api/v1/sales/<sale_id>/returns mediante header Idempotency-Key.
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_idem", password="Admin-password-123!"
        )
        cls.operator = user_model.objects.create_user(
            username="op_idem", password="Op-password-123!"
        )
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME

        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

    def setUp(self):
        from django.test import Client

        self.client = Client(enforce_csrf_checks=True)
        self.op_client = Client(enforce_csrf_checks=True)

        self.c_liq = Component.objects.create(
            name="Ensamble Idem", base_unit="L", unit_precision=3
        )
        self.c_bot = Component.objects.create(
            name="Botella Idem", base_unit="pza", unit_precision=0
        )
        register_receipt(self.c_liq.id, Decimal("20.000"), Decimal("80.00"))
        register_receipt(self.c_bot.id, Decimal("20.000"), Decimal("50.00"))

        self.prod = create_product(name="Valhalla Idem 750")
        add_recipe_version(
            product_id=self.prod.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.750")},
                {"component_id": self.c_bot.id, "quantity": Decimal("1")},
            ],
        )
        add_price_version(product_id=self.prod.id, price_with_tax=Decimal("500.00"))

        self.prod2 = create_product(name="Valhalla Idem 500")
        add_recipe_version(
            product_id=self.prod2.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.500")},
                {"component_id": self.c_bot.id, "quantity": Decimal("1")},
            ],
        )
        add_price_version(product_id=self.prod2.id, price_with_tax=Decimal("350.00"))

    def get_csrf(self, client=None):
        c = client or self.client
        return c.get("/api/v1/auth/csrf").json()["csrf_token"]

    def login(self, username, password, client=None):
        c = client or self.client
        token = self.get_csrf(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self.get_csrf(c)

    def test_individual_sale_retry_is_idempotent(self):
        token = self.login("admin_idem", "Admin-password-123!")
        idem_key = str(uuid.uuid4())
        payload = {"product_id": self.prod.id, "quantity": "2.000000"}

        res1 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res1.status_code, 201)
        body1 = res1.json()
        self.assertEqual(Sale.objects.count(), 1)
        movements_count = InventoryMovement.objects.count()
        self.assertEqual(get_component_stock(self.c_liq.id), Decimal("18.500000"))
        self.assertEqual(get_component_stock(self.c_bot.id), Decimal("18.000000"))

        # Reintento idéntico
        res2 = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"quantity": "2.000000", "product_id": self.prod.id}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.json(), body1)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), movements_count)
        self.assertEqual(get_component_stock(self.c_liq.id), Decimal("18.500000"))
        self.assertEqual(get_component_stock(self.c_bot.id), Decimal("18.000000"))
        self.assertEqual(
            Sale.objects.get(id=body1["sale_id"]).event_id,
            uuid.UUID(idem_key),
        )

    def test_individual_pending_sale_retry_is_idempotent(self):
        token = self.login("admin_idem", "Admin-password-123!")
        idem_key = str(uuid.uuid4())
        payload = {
            "product_id": self.prod.id,
            "quantity": "1.000000",
            "status": "PENDING",
        }

        res1 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res1.status_code, 201)
        self.assertEqual(res1.json()["status"], "PENDING")

        res2 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.json(), res1.json())
        self.assertEqual(Sale.objects.count(), 1)

    def test_multi_item_and_pending_sale_retry_is_idempotent(self):
        token = self.login("admin_idem", "Admin-password-123!")
        self.prod.bottle_stock = Decimal("10.000000")
        self.prod.save(update_fields=["bottle_stock"])
        self.prod2.bottle_stock = Decimal("10.000000")
        self.prod2.save(update_fields=["bottle_stock"])

        # 1) Multi-item FULFILLED con 2 productos
        key_fulfilled = str(uuid.uuid4())
        payload_fulfilled = {
            "device_id": "win-pos-1",
            "items": [
                {"product_id": self.prod.id, "quantity": "2.000000"},
                {"product_id": self.prod2.id, "quantity": "1.000000"},
            ],
        }
        res1 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload_fulfilled),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key_fulfilled,
        )
        self.assertEqual(res1.status_code, 201)
        body1 = res1.json()
        self.assertEqual(body1["total_items"], 2)
        self.assertEqual(Sale.objects.count(), 2)
        self.prod.refresh_from_db()
        self.prod2.refresh_from_db()
        self.assertEqual(self.prod.bottle_stock, Decimal("8.000000"))
        self.assertEqual(self.prod2.bottle_stock, Decimal("9.000000"))

        res2 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload_fulfilled),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key_fulfilled,
        )
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.json(), body1)
        self.assertEqual(Sale.objects.count(), 2)
        self.prod.refresh_from_db()
        self.prod2.refresh_from_db()
        self.assertEqual(self.prod.bottle_stock, Decimal("8.000000"))
        self.assertEqual(self.prod2.bottle_stock, Decimal("9.000000"))

        # 2) Multi-item PENDING
        key_pending = str(uuid.uuid4())
        payload_pending = {
            "status": "PENDING",
            "device_id": "android_terminal_01",
            "items": [
                {"product_id": self.prod.id, "quantity": "1.000000"},
                {"product_id": self.prod2.id, "quantity": "2.000000"},
            ],
        }
        res_p1 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload_pending),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key_pending,
        )
        self.assertEqual(res_p1.status_code, 201)
        body_p1 = res_p1.json()
        self.assertEqual(body_p1["status"], "PENDING")
        self.assertEqual(Sale.objects.count(), 4)

        res_p2 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload_pending),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key_pending,
        )
        self.assertEqual(res_p2.status_code, 201)
        self.assertEqual(res_p2.json()["purchase_id"], body_p1["purchase_id"])
        self.assertEqual(res_p2.json()["sales"], body_p1["sales"])
        self.assertEqual(Sale.objects.count(), 4)

    def test_return_retry_is_idempotent(self):
        token = self.login("admin_idem", "Admin-password-123!")
        res_sale = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_sale.status_code, 201)
        sale_id = res_sale.json()["sale_id"]

        ret_key = str(uuid.uuid4())
        ret_payload = {"quantity": "1.000000"}
        res_ret1 = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps(ret_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=ret_key,
        )
        self.assertEqual(res_ret1.status_code, 201)
        body1 = res_ret1.json()
        self.assertEqual(Return.objects.count(), 1)
        self.assertEqual(get_component_stock(self.c_liq.id), Decimal("19.250000"))
        self.assertEqual(get_component_stock(self.c_bot.id), Decimal("19.000000"))

        # Reintento idéntico -> mismo return_id, sin doble reembolso ni reingreso
        res_ret2 = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps(ret_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=ret_key,
        )
        self.assertEqual(res_ret2.status_code, 201)
        self.assertEqual(res_ret2.json(), body1)
        self.assertEqual(Return.objects.count(), 1)
        self.assertEqual(get_component_stock(self.c_liq.id), Decimal("19.250000"))
        self.assertEqual(get_component_stock(self.c_bot.id), Decimal("19.000000"))

    def test_reused_idempotency_key_conflicts_across_payload_actor_scope_and_sale(self):
        admin_token = self.login("admin_idem", "Admin-password-123!")
        op_token = self.login("op_idem", "Op-password-123!", client=self.op_client)

        # Crear dos ventas surtidas sin idempotencia para probar devoluciones
        s1_res = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        s2_res = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
        )
        sale1_id = s1_res.json()["sale_id"]
        sale2_id = s2_res.json()["sale_id"]

        # 1. Crear venta con key_sale
        key_sale = str(uuid.uuid4())
        sale_payload = {"product_id": self.prod.id, "quantity": "1.000000"}
        ok_sale = self.client.post(
            "/api/v1/sales",
            data=json.dumps(sale_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_sale,
        )
        self.assertEqual(ok_sale.status_code, 201)

        # 1a. Misma key con payload distinto -> 409 IDEMPOTENCY_KEY_REUSED
        diff_payload = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_sale,
        )
        self.assertEqual(diff_payload.status_code, 409)
        self.assertEqual(diff_payload.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 1b. Misma key y mismo payload, pero distinto actor -> 409 IDEMPOTENCY_KEY_REUSED
        diff_actor = self.op_client.post(
            "/api/v1/sales",
            data=json.dumps(sale_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=op_token,
            HTTP_IDEMPOTENCY_KEY=key_sale,
        )
        self.assertEqual(diff_actor.status_code, 409)
        self.assertEqual(diff_actor.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 1c. Misma key de venta reutilizada en devolución -> 409 IDEMPOTENCY_KEY_REUSED
        sale_to_ret = self.client.post(
            f"/api/v1/sales/{sale1_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_sale,
        )
        self.assertEqual(sale_to_ret.status_code, 409)
        self.assertEqual(sale_to_ret.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 2. Devolución en sale1 con key_ret -> luego reutilizada en sale2 / venta / otro payload
        key_ret = str(uuid.uuid4())
        ok_ret = self.client.post(
            f"/api/v1/sales/{sale1_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_ret,
        )
        self.assertEqual(ok_ret.status_code, 201)

        # 2a. Misma key en otro sale_id -> 409 IDEMPOTENCY_KEY_REUSED
        diff_sale_ret = self.client.post(
            f"/api/v1/sales/{sale2_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_ret,
        )
        self.assertEqual(diff_sale_ret.status_code, 409)
        self.assertEqual(diff_sale_ret.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 2b. Misma key de devolución reutilizada en venta -> 409 IDEMPOTENCY_KEY_REUSED
        ret_to_sale = self.client.post(
            "/api/v1/sales",
            data=json.dumps(sale_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_ret,
        )
        self.assertEqual(ret_to_sale.status_code, 409)
        self.assertEqual(ret_to_sale.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 2c. Misma key en sale1 con payload distinto -> 409 IDEMPOTENCY_KEY_REUSED
        diff_ret_payload = self.client.post(
            f"/api/v1/sales/{sale1_id}/returns",
            data=json.dumps({"quantity": "0.500000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=key_ret,
        )
        self.assertEqual(diff_ret_payload.status_code, 409)
        self.assertEqual(diff_ret_payload.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # 2d. Misma key en sale1 con otro actor -> 409 IDEMPOTENCY_KEY_REUSED
        diff_ret_actor = self.op_client.post(
            f"/api/v1/sales/{sale1_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=op_token,
            HTTP_IDEMPOTENCY_KEY=key_ret,
        )
        self.assertEqual(diff_ret_actor.status_code, 409)
        self.assertEqual(diff_ret_actor.json()["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_invalid_or_empty_idempotency_key_header_returns_400(self):
        token = self.login("admin_idem", "Admin-password-123!")

        for bad_header in ("", "   ", "not-a-uuid"):
            res_sale = self.client.post(
                "/api/v1/sales",
                data=json.dumps({"product_id": self.prod.id, "quantity": "1.000000"}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
                HTTP_IDEMPOTENCY_KEY=bad_header,
            )
            self.assertEqual(res_sale.status_code, 400)
            self.assertEqual(res_sale.json()["code"], "VALIDATION_ERROR")

            res_ret = self.client.post(
                "/api/v1/sales/1/returns",
                data=json.dumps({"quantity": "1.000000"}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
                HTTP_IDEMPOTENCY_KEY=bad_header,
            )
            self.assertEqual(res_ret.status_code, 400)
            self.assertEqual(res_ret.json()["code"], "VALIDATION_ERROR")

        self.assertEqual(CatalogIdempotency.objects.count(), 0)

    def test_insufficient_stock_and_validation_errors_rollback_cleanly_allowing_retry(self):
        token = self.login("admin_idem", "Admin-password-123!")
        idem_key = str(uuid.uuid4())

        # 1. Falla de validación (Decimal inválido en items) -> 400, sin fila huérfana
        res_bad = self.client.post(
            "/api/v1/sales",
            data=json.dumps({
                "items": [{"product_id": self.prod.id, "quantity": "no-decimal"}],
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res_bad.status_code, 400)
        self.assertEqual(CatalogIdempotency.objects.count(), 0)
        self.assertEqual(Sale.objects.count(), 0)

        # 2. Falla por stock insuficiente (pedir 50 botellas teniendo 20) -> 409 INSUFFICIENT_STOCK
        # Rollback limpio: no deja Sale ni CatalogIdempotency huérfano
        payload = {"product_id": self.prod.id, "quantity": "50.000000"}
        res_insuff = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res_insuff.status_code, 409)
        self.assertEqual(res_insuff.json()["code"], "INSUFFICIENT_STOCK")
        self.assertEqual(CatalogIdempotency.objects.count(), 0)
        self.assertEqual(Sale.objects.count(), 0)

        # 3. Surtir stock suficiente y reintentar con la MISMA Idempotency-Key -> 201 OK
        register_receipt(self.c_liq.id, Decimal("30.000"), Decimal("80.00"))
        register_receipt(self.c_bot.id, Decimal("40.000"), Decimal("50.00"))

        res_ok = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res_ok.status_code, 201)
        self.assertEqual(CatalogIdempotency.objects.count(), 1)
        self.assertEqual(Sale.objects.count(), 1)

        # 4. Reintento posterior devuelve el mismo resultado cacheado
        res_ok2 = self.client.post(
            "/api/v1/sales",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=idem_key,
        )
        self.assertEqual(res_ok2.status_code, 201)
        self.assertEqual(res_ok2.json(), res_ok.json())
        self.assertEqual(Sale.objects.count(), 1)

    def test_existing_event_id_and_single_item_array_and_return_rollback(self):
        token = self.login("admin_idem", "Admin-password-123!")

        # 1) Multi-item de 1 solo elemento asigna event_id == idempotency_key directo
        single_item_key = str(uuid.uuid4())
        res_single = self.client.post(
            "/api/v1/sales",
            data=json.dumps({
                "items": [{"product_id": self.prod.id, "quantity": "1.000000", "from_subinventory": False}],
            }),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=single_item_key,
        )
        self.assertEqual(res_single.status_code, 201)
        sale_id = res_single.json()["sale_id"]
        self.assertEqual(Sale.objects.get(id=sale_id).event_id, uuid.UUID(single_item_key))

        # 2) Devolución que falla (reembolso excesivo) hace rollback de CatalogIdempotency y permite reintentar
        ret_key = str(uuid.uuid4())
        res_ret_fail = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000", "refund_amount": "99999.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=ret_key,
        )
        self.assertEqual(res_ret_fail.status_code, 409)
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=ret_key).exists())

        res_ret_ok = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=ret_key,
        )
        self.assertEqual(res_ret_ok.status_code, 201)
        self.assertTrue(CatalogIdempotency.objects.filter(idempotency_key=ret_key).exists())
        self.assertEqual(
            Return.objects.get(id=res_ret_ok.json()["return_id"]).event_id,
            uuid.UUID(ret_key),
        )

        # 3) Si existe una Sale con event_id igual sin CatalogIdempotency, la API devuelve 409 IDEMPOTENCY_KEY_REUSED
        orphan_event = uuid.uuid4()
        create_sale_request(
            product_id=self.prod.id,
            quantity=Decimal("1.000000"),
            actor=self.admin,
            event_id=orphan_event,
        )
        res_conflict = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=str(orphan_event),
        )
        self.assertEqual(res_conflict.status_code, 409)
        self.assertEqual(res_conflict.json()["code"], "IDEMPOTENCY_KEY_REUSED")


class SalesHistoryAndReturnsContractTests(TestCase):
    """
    Fase 5A — Contrato de histórico de ventas y devoluciones acumuladas:
      • Migración 0005 idempotente y caso legacy 1+2=3.
      • Validación de piezas acumuladas y prohibición de exceso.
      • Tolerancia $0.05 en devolución final e IVA exacto sin anular retenciones.
      • Devoluciones $0.00 cuando existen piezas remanentes.
      • Keyset batching con limit, include_returned=1/0, PENDING/CANCELLED, validación 400.
      • Consultas sin N+1 (assertNumQueries constante), SaleDetailView y ReturnCreateView.
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_5a", password="Admin-password-123!"
        )

    def setUp(self):
        from django.test import Client

        self.client = Client(enforce_csrf_checks=True)
        self.c_liq = Component.objects.create(
            name="Ensamble 5A", base_unit="L", unit_precision=3
        )
        self.c_bot = Component.objects.create(
            name="Botella 5A", base_unit="pza", unit_precision=0
        )
        register_receipt(self.c_liq.id, Decimal("500.000"), Decimal("80.00"))
        register_receipt(self.c_bot.id, Decimal("500.000"), Decimal("50.00"))

        self.prod = create_product(name="Valhalla 5A 750")
        self.rv = add_recipe_version(
            product_id=self.prod.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.750000")},
                {"component_id": self.c_bot.id, "quantity": Decimal("1.000000")},
            ],
        )
        self.pv = add_price_version(product_id=self.prod.id, price_with_tax=Decimal("500.00"))

        self.prod_bulk = create_product(name="Granel 5A")
        self.rv_bulk = add_recipe_version(
            product_id=self.prod_bulk.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.750000")},
            ],
        )
        self.pv_bulk = add_price_version(
            product_id=self.prod_bulk.id, price_with_tax=Decimal("300.00")
        )

    def login_admin(self):
        token = self.client.get("/api/v1/auth/csrf").json()["csrf_token"]
        res = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": "admin_5a", "password": "Admin-password-123!"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self.client.get("/api/v1/auth/csrf").json()["csrf_token"]

    def _create_fulfilled_sale_orm(
        self,
        *,
        product=None,
        recipe_version=None,
        price_version=None,
        quantity=Decimal("1.000000"),
        unit_price=Decimal("500.00"),
        discount=Decimal("0.00"),
        final_amount=None,
        tax_amount=None,
        device_id="win-pos",
    ) -> Sale:
        prod = product or self.prod
        rv = recipe_version if recipe_version is not None else self.rv
        pv = price_version if price_version is not None else self.pv
        if final_amount is None:
            final_amount = (quantity * unit_price) - discount
        if tax_amount is None:
            tax_amount = (final_amount * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))
        now = timezone.now()
        sale = Sale.objects.create(
            status=SaleStatus.FULFILLED,
            requested_at=now,
            fulfilled_at=now,
            created_by=self.admin,
            device_id=device_id,
        )
        SaleLine.objects.create(
            sale=sale,
            product=prod,
            quantity=quantity,
            recipe_version=rv,
            price_version=pv,
            unit_price_with_tax=unit_price,
            discount=discount,
            final_amount=final_amount,
            tax_amount=tax_amount,
            cost_applied=Decimal("100.00"),
        )
        return sale

    def test_migration_0005_backfill_is_idempotent_and_handles_legacy_overreturn(self):
        mig = importlib.import_module("catalog.migrations.0005_return_quantity")
        now = timezone.now()

        # Venta legacy de 2 pzas ($900 netos, $100 descuento) con 2 devoluciones 1 + 2 = 3 (ambas $450)
        sale_legacy = self._create_fulfilled_sale_orm(
            quantity=Decimal("2.000000"),
            unit_price=Decimal("500.00"),
            discount=Decimal("100.00"),
            final_amount=Decimal("900.00"),
            tax_amount=Decimal("124.14"),
        )
        ret1 = Return.objects.create(
            sale_line=sale_legacy.line,
            status=ReturnStatus.COMPLETED,
            quantity=None,
            returned_at=now,
            refund_amount=Decimal("450.00"),
            tax_reversed=Decimal("62.07"),
        )
        ReturnLine.objects.create(
            ret=ret1, component=self.c_liq, quantity_returned=Decimal("0.750000"), unit_cost_original=Decimal("80.00")
        )
        ReturnLine.objects.create(
            ret=ret1, component=self.c_bot, quantity_returned=Decimal("1.000000"), unit_cost_original=Decimal("50.00")
        )

        ret2 = Return.objects.create(
            sale_line=sale_legacy.line,
            status=ReturnStatus.COMPLETED,
            quantity=None,
            returned_at=now,
            refund_amount=Decimal("450.00"),
            tax_reversed=Decimal("62.07"),
        )
        ReturnLine.objects.create(
            ret=ret2, component=self.c_liq, quantity_returned=Decimal("1.500000"), unit_cost_original=Decimal("80.00")
        )
        ReturnLine.objects.create(
            ret=ret2, component=self.c_bot, quantity_returned=Decimal("2.000000"), unit_cost_original=Decimal("50.00")
        )

        # Caso sin componente == 1.000000 (solo 0.750 L) -> 1.500 / 0.750 = 2.000000
        sale_bulk = self._create_fulfilled_sale_orm(
            product=self.prod_bulk,
            recipe_version=self.rv_bulk,
            price_version=self.pv_bulk,
            quantity=Decimal("3.000000"),
            unit_price=Decimal("300.00"),
            final_amount=Decimal("900.00"),
        )
        ret_bulk = Return.objects.create(
            sale_line=sale_bulk.line,
            status=ReturnStatus.COMPLETED,
            quantity=None,
            returned_at=now,
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )
        ReturnLine.objects.create(
            ret=ret_bulk, component=self.c_liq, quantity_returned=Decimal("1.500000"), unit_cost_original=Decimal("80.00")
        )

        # Caso Estrategia 2 (sin ReturnLine): (250 / 500) * 2 = 1.000000
        sale_fallback = self._create_fulfilled_sale_orm(
            quantity=Decimal("2.000000"),
            unit_price=Decimal("250.00"),
            final_amount=Decimal("500.00"),
        )
        ret_fallback = Return.objects.create(
            sale_line=sale_fallback.line,
            status=ReturnStatus.COMPLETED,
            quantity=None,
            returned_at=now,
            refund_amount=Decimal("250.00"),
            tax_reversed=Decimal("34.48"),
        )

        # Caso ya poblado (no debe sobreescribirse por backfill)
        sale_existing = self._create_fulfilled_sale_orm(quantity=Decimal("8.000000"))
        ret_existing = Return.objects.create(
            sale_line=sale_existing.line,
            status=ReturnStatus.COMPLETED,
            quantity=Decimal("7.000000"),
            returned_at=now,
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )

        # Ejecutar backfill y re-ejecutar para verificar idempotencia
        mig.backfill_return_quantities(django_apps, None)
        mig.backfill_return_quantities(django_apps, None)

        ret1.refresh_from_db()
        ret2.refresh_from_db()
        ret_bulk.refresh_from_db()
        ret_fallback.refresh_from_db()
        ret_existing.refresh_from_db()

        self.assertEqual(ret1.quantity, Decimal("1.000000"))
        self.assertEqual(ret2.quantity, Decimal("2.000000"))
        self.assertEqual(ret_bulk.quantity, Decimal("2.000000"))
        self.assertEqual(ret_fallback.quantity, Decimal("1.000000"))
        self.assertEqual(ret_existing.quantity, Decimal("7.000000"))

        # Resumen sobre caso legacy 1+2=3
        summary = compute_sale_return_summary(sale_legacy.line)
        self.assertEqual(summary["returned_quantity"], Decimal("3.000000"))
        self.assertEqual(summary["remaining_quantity"], Decimal("0.000000"))
        self.assertTrue(summary["is_fully_returned"])
        self.assertFalse(summary["is_partially_returned"])
        self.assertEqual(summary["return_status"], "FULL")
        self.assertEqual(summary["net_discount"], Decimal("0.00"))
        self.assertEqual(summary["net_final_amount"], Decimal("0.00"))
        self.assertEqual(summary["net_tax_amount"], Decimal("0.00"))
        self.assertEqual(summary["max_refund_possible"], Decimal("0.00"))

        with self.assertRaises(BusinessConflictError):
            register_return(sale_id=sale_legacy.id, quantity_to_return=Decimal("1.000000"))

        # Devoluciones PENDING o REJECTED no cuentan en el resumen
        Return.objects.create(
            sale_line=sale_fallback.line,
            status=ReturnStatus.PENDING,
            quantity=Decimal("1.000000"),
            refund_amount=Decimal("250.00"),
            tax_reversed=Decimal("34.48"),
        )
        Return.objects.create(
            sale_line=sale_fallback.line,
            status=ReturnStatus.REJECTED,
            quantity=Decimal("1.000000"),
            refund_amount=Decimal("250.00"),
            tax_reversed=Decimal("34.48"),
        )
        summary_fb = compute_sale_return_summary(sale_fallback.line)
        self.assertEqual(summary_fb["returned_quantity"], Decimal("1.000000"))
        self.assertEqual(summary_fb["remaining_quantity"], Decimal("1.000000"))
        self.assertEqual(summary_fb["return_status"], "PARTIAL")

    def test_partial_returns_validate_cumulative_quantity_and_reject_excess(self):
        sale = create_sale_request(product_id=self.prod.id, quantity=Decimal("3.000000"), actor=self.admin)
        fulfill_sale(sale.id, actor=self.admin)

        r1 = register_return(sale_id=sale.id, quantity_to_return=Decimal("1.000000"), actor=self.admin)
        self.assertEqual(r1.quantity, Decimal("1.000000"))

        summary = compute_sale_return_summary(sale.line)
        self.assertEqual(summary["remaining_quantity"], Decimal("2.000000"))
        self.assertEqual(summary["return_status"], "PARTIAL")

        # Intentar devolver 2.5 cuando quedan 2.0 -> BusinessConflictError sin efectos
        with self.assertRaises(BusinessConflictError):
            register_return(sale_id=sale.id, quantity_to_return=Decimal("2.500000"), actor=self.admin)
        self.assertEqual(Return.objects.filter(sale_line=sale.line).count(), 1)

        r2 = register_return(sale_id=sale.id, quantity_to_return=Decimal("2.000000"), actor=self.admin)
        self.assertEqual(r2.quantity, Decimal("2.000000"))

        summary_final = compute_sale_return_summary(sale.line)
        self.assertEqual(summary_final["remaining_quantity"], Decimal("0.000000"))
        self.assertEqual(summary_final["return_status"], "FULL")

        with self.assertRaises(BusinessConflictError):
            register_return(sale_id=sale.id, quantity_to_return=Decimal("0.500000"), actor=self.admin)

    def test_rounding_tolerance_005_and_tax_adjustment_without_erasing_retentions(self):
        # 1) Venta de 3 piezas por $100.00 (3 * $35 - $5 desc) -> IVA $13.79
        sale_round = create_sale_request(
            product_id=self.prod.id,
            quantity=Decimal("3.000000"),
            unit_price=Decimal("35.00"),
            discount=Decimal("5.00"),
            actor=self.admin,
        )
        fulfill_sale(sale_round.id, actor=self.admin)
        sale_round.line.refresh_from_db()
        self.assertEqual(sale_round.line.final_amount, Decimal("100.00"))
        self.assertEqual(sale_round.line.tax_amount, Decimal("13.79"))

        r1 = register_return(sale_id=sale_round.id, quantity_to_return=Decimal("1.000000"), actor=self.admin)
        self.assertEqual(r1.refund_amount, Decimal("33.33"))
        self.assertEqual(r1.tax_reversed, Decimal("4.60"))

        r2 = register_return(sale_id=sale_round.id, quantity_to_return=Decimal("1.000000"), actor=self.admin)
        self.assertEqual(r2.refund_amount, Decimal("33.33"))
        self.assertEqual(r2.tax_reversed, Decimal("4.60"))

        # Devolución final: abs(33.34 - 33.33) = 0.01 <= 0.05 -> absorbe centavo y cuadra IVA
        r3 = register_return(sale_id=sale_round.id, quantity_to_return=Decimal("1.000000"), actor=self.admin)
        self.assertEqual(r3.refund_amount, Decimal("33.34"))
        self.assertEqual(r3.tax_reversed, Decimal("4.59"))

        s_round = compute_sale_return_summary(sale_round.line)
        self.assertEqual(s_round["already_refunded"], Decimal("100.00"))
        self.assertEqual(s_round["already_tax_reversed"], Decimal("13.79"))
        self.assertEqual(s_round["net_final_amount"], Decimal("0.00"))
        self.assertEqual(s_round["net_tax_amount"], Decimal("0.00"))
        self.assertEqual(s_round["net_discount"], Decimal("0.00"))

        # 2) Venta de 2 piezas por $1000.00 con retención explícita de $100 en 1ra devolución
        sale_ret = create_sale_request(
            product_id=self.prod.id,
            quantity=Decimal("2.000000"),
            actor=self.admin,
        )
        fulfill_sale(sale_ret.id, actor=self.admin)

        ret_p1 = register_return(
            sale_id=sale_ret.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=Decimal("400.00"),
            actor=self.admin,
        )
        self.assertEqual(ret_p1.refund_amount, Decimal("400.00"))
        self.assertEqual(ret_p1.tax_reversed, Decimal("55.17"))

        # Segunda devolución sin monto: NO debe anular la retención de $100 -> reembolsa $500 (no $600)
        ret_p2 = register_return(
            sale_id=sale_ret.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=None,
            actor=self.admin,
        )
        self.assertEqual(ret_p2.refund_amount, Decimal("500.00"))
        self.assertEqual(ret_p2.tax_reversed, Decimal("68.97"))

        s_ret = compute_sale_return_summary(sale_ret.line)
        self.assertEqual(s_ret["already_refunded"], Decimal("900.00"))
        self.assertEqual(s_ret["net_final_amount"], Decimal("100.00"))
        self.assertEqual(s_ret["net_tax_amount"], Decimal("13.79"))
        self.assertEqual(s_ret["max_refund_possible"], Decimal("0.00"))
        self.assertEqual(s_ret["return_status"], "FULL")

        # 3) refund_amount explícito mayor a max_refund_possible -> siempre BusinessConflictError
        sale_cap = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("2.000000"), actor=self.admin
        )
        fulfill_sale(sale_cap.id, actor=self.admin)
        register_return(
            sale_id=sale_cap.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=Decimal("600.00"),
            actor=self.admin,
        )
        with self.assertRaises(BusinessConflictError):
            register_return(
                sale_id=sale_cap.id,
                quantity_to_return=Decimal("1.000000"),
                refund_amount=Decimal("450.00"),
                actor=self.admin,
            )

    def test_zero_refund_return_allowed_when_remaining_quantity_positive(self):
        # Caso A: max_refund_possible == 0.00 tras reembolso anticipado, pero queda 1 pieza
        sale = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("2.000000"), actor=self.admin
        )
        fulfill_sale(sale.id, actor=self.admin)

        register_return(
            sale_id=sale.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=Decimal("1000.00"),
            actor=self.admin,
        )
        r_zero = register_return(
            sale_id=sale.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=Decimal("0.00"),
            actor=self.admin,
        )
        self.assertEqual(r_zero.refund_amount, Decimal("0.00"))
        self.assertEqual(r_zero.tax_reversed, Decimal("0.00"))
        self.assertEqual(
            compute_sale_return_summary(sale.line)["return_status"], "FULL"
        )

        # Caso B: venta promocional con descuento 100% (final_amount == 0.00)
        sale_promo = create_sale_request(
            product_id=self.prod.id,
            quantity=Decimal("1.000000"),
            discount=Decimal("500.00"),
            actor=self.admin,
        )
        fulfill_sale(sale_promo.id, actor=self.admin)
        self.assertEqual(sale_promo.line.final_amount, Decimal("0.00"))

        r_promo = register_return(
            sale_id=sale_promo.id,
            quantity_to_return=Decimal("1.000000"),
            refund_amount=None,
            actor=self.admin,
        )
        self.assertEqual(r_promo.refund_amount, Decimal("0.00"))
        self.assertEqual(r_promo.tax_reversed, Decimal("0.00"))
        self.assertEqual(
            compute_sale_return_summary(sale_promo.line)["return_status"], "FULL"
        )

    def test_sales_list_keyset_batching_limit_and_include_returned_and_statuses(self):
        self.login_admin()

        # 5 ventas antiguas elegibles (3 NONE + 1 PARTIAL + 1 de otro producto)
        s_old_none_1 = self._create_fulfilled_sale_orm(quantity=Decimal("1.000000"))
        s_old_bulk = self._create_fulfilled_sale_orm(
            product=self.prod_bulk,
            recipe_version=self.rv_bulk,
            price_version=self.pv_bulk,
            quantity=Decimal("1.000000"),
            unit_price=Decimal("300.00"),
            final_amount=Decimal("300.00"),
        )
        s_old_partial = self._create_fulfilled_sale_orm(
            quantity=Decimal("2.000000"),
            unit_price=Decimal("500.00"),
            discount=Decimal("100.00"),
            final_amount=Decimal("900.00"),
            tax_amount=Decimal("124.14"),
        )
        Return.objects.create(
            sale_line=s_old_partial.line,
            status=ReturnStatus.COMPLETED,
            quantity=Decimal("1.000000"),
            returned_at=timezone.now(),
            refund_amount=Decimal("450.00"),
            tax_reversed=Decimal("62.07"),
        )
        s_old_none_2 = self._create_fulfilled_sale_orm(quantity=Decimal("1.000000"))

        # 55 ventas más recientes totalmente devueltas para forzar cruce de batch (> 50)
        newest_full_ids = []
        for _ in range(55):
            sf = self._create_fulfilled_sale_orm(quantity=Decimal("1.000000"))
            Return.objects.create(
                sale_line=sf.line,
                status=ReturnStatus.COMPLETED,
                quantity=Decimal("1.000000"),
                returned_at=timezone.now(),
                refund_amount=Decimal("500.00"),
                tax_reversed=Decimal("68.97"),
            )
            newest_full_ids.append(sf.id)

        # Ventas PENDING y CANCELLED (con tax_amount=None para probar fallback)
        s_pending = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("1.000000"), actor=self.admin
        )
        s_cancelled = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("1.000000"), actor=self.admin
        )
        s_cancelled.status = SaleStatus.CANCELLED
        s_cancelled.save(update_fields=["status"])

        # 1) Default exclude FULL con limit=3 debe cruzar los 55 FULL y entregar las 3 más recientes elegibles
        res_limit = self.client.get("/api/v1/sales?limit=3")
        self.assertEqual(res_limit.status_code, 200)
        sales_data = res_limit.json()["sales"]
        self.assertEqual(
            [item["sale_id"] for item in sales_data],
            [s_old_none_2.id, s_old_partial.id, s_old_bulk.id],
        )

        # Verificar campos netos y originales en la venta PARTIAL
        partial_item = sales_data[1]
        self.assertEqual(partial_item["quantity"], "1.000000")
        self.assertEqual(partial_item["original_quantity"], "2.000000")
        self.assertEqual(partial_item["returned_quantity"], "1.000000")
        self.assertEqual(partial_item["remaining_quantity"], "1.000000")
        self.assertEqual(partial_item["discount"], "50.00")
        self.assertEqual(partial_item["original_discount"], "100.00")
        self.assertEqual(partial_item["net_discount"], "50.00")
        self.assertEqual(partial_item["final_amount"], "450.00")
        self.assertEqual(partial_item["original_final_amount"], "900.00")
        self.assertEqual(partial_item["tax_amount"], "62.07")
        self.assertEqual(partial_item["original_tax_amount"], "124.14")
        self.assertEqual(partial_item["already_refunded"], "450.00")
        self.assertEqual(partial_item["already_tax_reversed"], "62.07")
        self.assertEqual(partial_item["max_refund_possible"], "450.00")
        self.assertTrue(partial_item["is_partially_returned"])
        self.assertFalse(partial_item["is_fully_returned"])
        self.assertEqual(partial_item["return_status"], "PARTIAL")

        # 2) include_returned=0 explícito también excluye FULL
        res_excl = self.client.get("/api/v1/sales?include_returned=0&limit=10")
        self.assertEqual(res_excl.status_code, 200)
        self.assertEqual(
            [i["sale_id"] for i in res_excl.json()["sales"]],
            [s_old_none_2.id, s_old_partial.id, s_old_bulk.id, s_old_none_1.id],
        )

        # 3) include_returned=1 (y aliases bool) incluye FULL
        for flag in ("1", "true", "yes", "si", "on"):
            res_incl = self.client.get(f"/api/v1/sales?include_returned={flag}&limit=2")
            self.assertEqual(res_incl.status_code, 200)
            incl_items = res_incl.json()["sales"]
            self.assertEqual(len(incl_items), 2)
            self.assertEqual(incl_items[0]["sale_id"], newest_full_ids[-1])
            self.assertTrue(incl_items[0]["is_fully_returned"])
            self.assertEqual(incl_items[0]["return_status"], "FULL")
            self.assertEqual(incl_items[0]["max_refund_possible"], "0.00")
            self.assertEqual(incl_items[0]["remaining_quantity"], "0.000000")

        # 4) Filtro por product_id
        res_prod = self.client.get(f"/api/v1/sales?product_id={self.prod_bulk.id}")
        self.assertEqual(res_prod.status_code, 200)
        self.assertEqual([i["sale_id"] for i in res_prod.json()["sales"]], [s_old_bulk.id])

        # 5) PENDING y CANCELLED devuelven 200 con max_refund_possible == "0.00"
        res_pend = self.client.get("/api/v1/sales?status=PENDING")
        self.assertEqual(res_pend.status_code, 200)
        self.assertEqual(len(res_pend.json()["sales"]), 1)
        self.assertEqual(res_pend.json()["sales"][0]["sale_id"], s_pending.id)
        self.assertEqual(res_pend.json()["sales"][0]["max_refund_possible"], "0.00")
        self.assertEqual(res_pend.json()["sales"][0]["tax_amount"], "68.97")

        res_canc = self.client.get("/api/v1/sales?status=CANCELLED")
        self.assertEqual(res_canc.status_code, 200)
        self.assertEqual(len(res_canc.json()["sales"]), 1)
        self.assertEqual(res_canc.json()["sales"][0]["sale_id"], s_cancelled.id)
        self.assertEqual(res_canc.json()["sales"][0]["max_refund_possible"], "0.00")
        self.assertEqual(res_canc.json()["sales"][0]["tax_amount"], "68.97")

    def test_sales_list_query_params_validation_returns_400(self):
        self.login_admin()
        for qs in (
            "limit=0",
            "limit=-5",
            "limit=abc",
            "limit=1.5",
            "product_id=abc",
            "product_id=0",
            "product_id=-1",
        ):
            res = self.client.get(f"/api/v1/sales?{qs}")
            self.assertEqual(res.status_code, 400, msg=f"Esperaba 400 para {qs}")
            self.assertEqual(res.json()["code"], "VALIDATION_ERROR")

    def test_sales_list_avoids_n_plus_one_queries(self):
        self.login_admin()

        # Crear 2 ventas (1 con devolución legacy sin quantity) y medir queries
        s1 = self._create_fulfilled_sale_orm(quantity=Decimal("2.000000"))
        s2 = self._create_fulfilled_sale_orm(quantity=Decimal("2.000000"))
        r_leg = Return.objects.create(
            sale_line=s2.line,
            status=ReturnStatus.COMPLETED,
            quantity=None,
            returned_at=timezone.now(),
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )
        ReturnLine.objects.create(
            ret=r_leg, component=self.c_bot, quantity_returned=Decimal("1.000000"), unit_cost_original=Decimal("50.00")
        )

        with CaptureQueriesContext(connection) as ctx_small:
            res_small = self.client.get("/api/v1/sales?include_returned=1&limit=50")
        self.assertEqual(res_small.status_code, 200)
        small_queries = len(ctx_small)

        # Agregar 15 ventas más con devoluciones y ReturnLines -> el número de queries NO debe crecer
        for idx in range(15):
            sx = self._create_fulfilled_sale_orm(quantity=Decimal("2.000000"))
            rx = Return.objects.create(
                sale_line=sx.line,
                status=ReturnStatus.COMPLETED,
                quantity=None if idx % 2 == 0 else Decimal("1.000000"),
                returned_at=timezone.now(),
                refund_amount=Decimal("500.00"),
                tax_reversed=Decimal("68.97"),
            )
            ReturnLine.objects.create(
                ret=rx, component=self.c_bot, quantity_returned=Decimal("1.000000"), unit_cost_original=Decimal("50.00")
            )

        with self.assertNumQueries(small_queries):
            res_large = self.client.get("/api/v1/sales?include_returned=1&limit=50")
        self.assertEqual(res_large.status_code, 200)
        self.assertEqual(len(res_large.json()["sales"]), 17)

    def test_sale_detail_returns_200_for_full_pending_cancelled_and_404_missing(self):
        self.login_admin()

        s_full = self._create_fulfilled_sale_orm(quantity=Decimal("1.000000"))
        Return.objects.create(
            sale_line=s_full.line,
            status=ReturnStatus.COMPLETED,
            quantity=Decimal("1.000000"),
            returned_at=timezone.now(),
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )
        s_pending = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("1.000000"), actor=self.admin
        )
        s_cancelled = create_sale_request(
            product_id=self.prod.id, quantity=Decimal("1.000000"), actor=self.admin
        )
        s_cancelled.status = SaleStatus.CANCELLED
        s_cancelled.save(update_fields=["status"])

        for s, expected_status, expected_ret_status in (
            (s_full, "FULFILLED", "FULL"),
            (s_pending, "PENDING", "NONE"),
            (s_cancelled, "CANCELLED", "NONE"),
        ):
            res = self.client.get(f"/api/v1/sales/{s.id}")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["sale_id"], s.id)
            self.assertEqual(body["status"], expected_status)
            self.assertEqual(body["return_status"], expected_ret_status)
            self.assertEqual(body["max_refund_possible"], "0.00")
            for key in (
                "device_id",
                "requested_at",
                "fulfilled_at",
                "product_id",
                "product_name",
                "product_sku",
                "quantity",
                "original_quantity",
                "returned_quantity",
                "remaining_quantity",
                "unit_price_with_tax",
                "discount",
                "original_discount",
                "net_discount",
                "final_amount",
                "tax_amount",
                "original_final_amount",
                "original_tax_amount",
                "already_refunded",
                "already_tax_reversed",
                "is_partially_returned",
                "is_fully_returned",
                "components",
            ):
                self.assertIn(key, body)

        # 404 para venta inexistente o venta sin SaleLine
        res_missing = self.client.get("/api/v1/sales/999999")
        self.assertEqual(res_missing.status_code, 404)

        orphan_sale = Sale.objects.create(status=SaleStatus.PENDING)
        res_orphan = self.client.get(f"/api/v1/sales/{orphan_sale.id}")
        self.assertEqual(res_orphan.status_code, 404)

    def test_return_create_view_returns_remaining_quantity_return_status_and_idempotency(self):
        token = self.login_admin()

        # Producto con subinventario entero (5 botellas)
        self.prod.bottle_stock = Decimal("5.000000")
        self.prod.save(update_fields=["bottle_stock"])

        res_sale = self.client.post(
            "/api/v1/sales",
            data=json.dumps({"product_id": self.prod.id, "quantity": "2.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_sale.status_code, 201)
        sale_id = res_sale.json()["sale_id"]

        # Primera devolución parcial (1 de 2) con Idempotency-Key -> stock 3 + 1 = 4 ("4")
        key1 = str(uuid.uuid4())
        res_ret1 = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key1,
        )
        self.assertEqual(res_ret1.status_code, 201)
        b1 = res_ret1.json()
        self.assertEqual(b1["remaining_quantity"], "1.000000")
        self.assertEqual(b1["return_status"], "PARTIAL")
        self.assertEqual(b1["new_bottle_stock"], "4")

        # Reintento idéntico
        res_ret1_retry = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key1,
        )
        self.assertEqual(res_ret1_retry.status_code, 201)
        self.assertEqual(res_ret1_retry.json(), b1)

        # Segunda devolución con fracción de stock en producto para validar formato decimal normalizado
        self.prod.bottle_stock = Decimal("4.500000")
        self.prod.save(update_fields=["bottle_stock"])
        key2 = str(uuid.uuid4())
        res_ret2 = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key2,
        )
        self.assertEqual(res_ret2.status_code, 201)
        b2 = res_ret2.json()
        self.assertEqual(b2["remaining_quantity"], "0.000000")
        self.assertEqual(b2["return_status"], "FULL")
        self.assertEqual(b2["new_bottle_stock"], "5.5")

        # Tercera devolución sobre venta ya FULL -> 409 BUSINESS_CONFLICT
        res_ret3 = self.client.post(
            f"/api/v1/sales/{sale_id}/returns",
            data=json.dumps({"quantity": "1.000000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_ret3.status_code, 409)
        self.assertEqual(res_ret3.json()["code"], "BUSINESS_CONFLICT")


class ExcelSaleQuantityTests(TestCase):
    """
    Fase 5A — Ajuste administrativo de cantidad desde tabla Excel (ADMIN):
      • RBAC (401/403), CSRF (403 CSRF_FAILED) e Idempotency-Key obligatoria (400).
      • Ajustes consecutivos 5 -> 3 -> 1 -> 0 y ajuste parcial/total decimal.
      • Validación 400 (negativos, decimales en entero, >3dp en fraccionable, JSON inválido).
      • Conflictos 409 (>= actual, ya en 0, expected obsoleto, simetría física, PENDING/CANCELLED, colisión UUID).
      • Rollback transaccional de CatalogIdempotency ante 409/422 y replay 200 OK.
      • Costo faltante 422 COST_REQUIRED sobre componentes bloqueados con stock > 0.
      • Auditoría de Return (reason fallback, source, previous/target_remaining_quantity) e InventoryMovement.reason.
      • Retención previa conservada al bajar a 0.
      • Campos from_subinventory / allow_fractional_quantity y assertNumQueries constante en GET /api/v1/sales.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME

        cls.admin = User.objects.create_superuser(
            username="admin_excel", password="Admin-password-123!"
        )
        cls.operator = User.objects.create_user(
            username="op_excel", password="Op-password-123!"
        )
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

    def setUp(self):
        from django.test import Client

        self.client = Client(enforce_csrf_checks=True)
        self.c_liq = Component.objects.create(
            name="Ensamble Excel", base_unit="L", unit_precision=3
        )
        self.c_bot = Component.objects.create(
            name="Botella Excel", base_unit="pza", unit_precision=0
        )
        register_receipt(self.c_liq.id, Decimal("500.000"), Decimal("80.00"))
        register_receipt(self.c_bot.id, Decimal("500.000"), Decimal("50.00"))

        self.prod_int = create_product(name="Valhalla Excel 750")
        self.rv_int = add_recipe_version(
            product_id=self.prod_int.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.750000")},
                {"component_id": self.c_bot.id, "quantity": Decimal("1.000000")},
            ],
        )
        self.pv_int = add_price_version(
            product_id=self.prod_int.id, price_with_tax=Decimal("500.00")
        )

        self.prod_frac = create_product(name="Granel Excel L")
        self.rv_frac = add_recipe_version(
            product_id=self.prod_frac.id,
            components_data=[
                {"component_id": self.c_liq.id, "quantity": Decimal("0.750000")},
            ],
        )
        self.pv_frac = add_price_version(
            product_id=self.prod_frac.id, price_with_tax=Decimal("300.00")
        )

    def _login(self, username: str, password: str, client=None) -> str:
        c = client or self.client
        csrf = c.get("/api/v1/auth/csrf").json()["csrf_token"]
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(res.status_code, 200)
        return c.get("/api/v1/auth/csrf").json()["csrf_token"]

    def _create_fulfilled_sale_orm(
        self,
        *,
        product=None,
        recipe_version=None,
        price_version=None,
        quantity=Decimal("5.000000"),
        unit_price=Decimal("500.00"),
        discount=Decimal("0.00"),
        from_subinventory=False,
        device_id="win-pos",
    ) -> Sale:
        prod = product or self.prod_int
        rv = recipe_version if recipe_version is not None else self.rv_int
        pv = price_version if price_version is not None else self.pv_int
        final_amount = (quantity * unit_price) - discount
        tax_amount = (final_amount * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))
        now = timezone.now()
        sale = Sale.objects.create(
            status=SaleStatus.FULFILLED,
            requested_at=now,
            fulfilled_at=now,
            created_by=self.admin,
            device_id=device_id,
        )
        SaleLine.objects.create(
            sale=sale,
            product=prod,
            quantity=quantity,
            recipe_version=rv,
            price_version=pv,
            unit_price_with_tax=unit_price,
            discount=discount,
            final_amount=final_amount,
            tax_amount=tax_amount,
            cost_applied=Decimal("100.00"),
            from_subinventory=from_subinventory,
        )
        return sale

    def _post_excel_qty(
        self,
        sale_id: int,
        payload: Any,
        *,
        token: str | None = None,
        idem_key: str | None = None,
        client=None,
    ):
        c = client or self.client
        headers: dict[str, str] = {}
        if token is not None:
            headers["HTTP_X_CSRFTOKEN"] = token
        if idem_key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = idem_key
        return c.post(
            f"/api/v1/sales/{sale_id}/excel-quantity",
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def test_permissions_csrf_and_auth_enforcement(self):
        from django.test import Client

        sale = self._create_fulfilled_sale_orm(quantity=Decimal("3.000000"))

        # 1) Anónimo con CSRF válido -> 401 AUTHENTICATION_REQUIRED
        anon_client = Client(enforce_csrf_checks=True)
        anon_token = anon_client.get("/api/v1/auth/csrf").json()["csrf_token"]
        res_anon = self._post_excel_qty(
            sale.id,
            {"target_quantity": "1"},
            token=anon_token,
            idem_key=str(uuid.uuid4()),
            client=anon_client,
        )
        self.assertEqual(res_anon.status_code, 401)
        self.assertEqual(res_anon.json()["code"], "AUTHENTICATION_REQUIRED")

        # 2) OPERADOR -> 403 PERMISSION_DENIED
        op_client = Client(enforce_csrf_checks=True)
        op_token = self._login("op_excel", "Op-password-123!", client=op_client)
        res_op = self._post_excel_qty(
            sale.id,
            {"target_quantity": "1"},
            token=op_token,
            idem_key=str(uuid.uuid4()),
            client=op_client,
        )
        self.assertEqual(res_op.status_code, 403)
        self.assertEqual(res_op.json()["code"], "PERMISSION_DENIED")

        # 3) ADMIN sin CSRF token -> 403 CSRF_FAILED
        self._login("admin_excel", "Admin-password-123!")
        res_no_csrf = self._post_excel_qty(
            sale.id,
            {"target_quantity": "1"},
            token=None,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res_no_csrf.status_code, 403)
        self.assertEqual(res_no_csrf.json()["code"], "CSRF_FAILED")

        # 4) Servicio directo con actor OPERADOR o None -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            adjust_sale_quantity_from_excel(
                sale_id=sale.id, target_quantity=Decimal("1"), actor=self.operator
            )
        with self.assertRaises(PermissionDeniedError):
            adjust_sale_quantity_from_excel(
                sale_id=sale.id, target_quantity=Decimal("1"), actor=None
            )

    def test_consecutive_integer_subinventory_adjustments_5_to_3_to_1_to_0_and_audit(self):
        token = self._login("admin_excel", "Admin-password-123!")

        self.prod_int.bottle_stock = Decimal("10.000000")
        self.prod_int.save(update_fields=["bottle_stock"])

        # Venta de 5 botellas desde subinventario (bottle_stock: 10 -> 5)
        sale = create_sale_request(
            product_id=self.prod_int.id,
            quantity=Decimal("5.000000"),
            actor=self.admin,
            device_id="android-pos-7",
        )
        sale.line.from_subinventory = True
        sale.line.save(update_fields=["from_subinventory"])
        fulfill_sale(sale.id, actor=self.admin, device_id="android-pos-7")
        self.prod_int.refresh_from_db()
        self.assertEqual(self.prod_int.bottle_stock, Decimal("5.000000"))

        # Paso 1: 5 -> 3 con reason en blanco (debe aplicar fallback)
        res1 = self._post_excel_qty(
            sale.id,
            {
                "target_quantity": "3",
                "expected_remaining_quantity": "5.000000",
                "reason": "   ",
            },
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res1.status_code, 200)
        b1 = res1.json()
        self.assertEqual(b1["sale_id"], sale.id)
        self.assertEqual(b1["device_id"], "android-pos-7")
        self.assertEqual(b1["adjustment_device_id"], "windows-excel-admin")
        self.assertEqual(b1["previous_remaining_quantity"], "5.000000")
        self.assertEqual(b1["target_quantity"], "3.000000")
        self.assertEqual(b1["quantity"], "3.000000")
        self.assertEqual(b1["remaining_quantity"], "3.000000")
        self.assertEqual(b1["returned_quantity"], "2.000000")
        self.assertEqual(b1["delta_reversed"], "2.000000")
        self.assertFalse(b1["removed_from_history"])
        self.assertTrue(b1["returned_to_subinventory"])
        self.assertTrue(b1["from_subinventory"])
        self.assertFalse(b1["allow_fractional_quantity"])
        self.assertEqual(b1["new_bottle_stock"], "7")
        self.assertEqual(b1["return_status"], "PARTIAL")
        self.assertEqual(b1["source"], ReturnSource.EXCEL_ADMIN_ADJUSTMENT)
        self.assertEqual(
            b1["reason"],
            "Ajuste de cantidad desde tabla Excel (ADMIN): 5.000000 -> 3.000000",
        )

        ret1 = Return.objects.get(id=b1["return_id"])
        self.assertEqual(ret1.source, ReturnSource.EXCEL_ADMIN_ADJUSTMENT)
        self.assertEqual(ret1.previous_remaining_quantity, Decimal("5.000000"))
        self.assertEqual(ret1.target_remaining_quantity, Decimal("3.000000"))
        self.assertEqual(ret1.reason, b1["reason"])

        # Paso 2: 3 -> 1 con reason explícito
        res2 = self._post_excel_qty(
            sale.id,
            {
                "target_quantity": "1",
                "expected_remaining_quantity": "3.000000",
                "reason": "  Corrección captura Excel  ",
            },
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res2.status_code, 200)
        b2 = res2.json()
        self.assertEqual(b2["previous_remaining_quantity"], "3.000000")
        self.assertEqual(b2["target_quantity"], "1.000000")
        self.assertEqual(b2["remaining_quantity"], "1.000000")
        self.assertEqual(b2["returned_quantity"], "4.000000")
        self.assertEqual(b2["delta_reversed"], "2.000000")
        self.assertEqual(b2["new_bottle_stock"], "9")
        self.assertEqual(b2["reason"], "Corrección captura Excel")
        self.assertFalse(b2["removed_from_history"])

        # Paso 3: 1 -> 0 con reason vacío (fallback de eliminación)
        res3 = self._post_excel_qty(
            sale.id,
            {
                "target_quantity": "0",
                "expected_remaining_quantity": "1.000000",
                "reason": "",
            },
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res3.status_code, 200)
        b3 = res3.json()
        self.assertEqual(b3["previous_remaining_quantity"], "1.000000")
        self.assertEqual(b3["target_quantity"], "0.000000")
        self.assertEqual(b3["remaining_quantity"], "0.000000")
        self.assertEqual(b3["returned_quantity"], "5.000000")
        self.assertEqual(b3["delta_reversed"], "1.000000")
        self.assertTrue(b3["removed_from_history"])
        self.assertTrue(b3["is_fully_returned"])
        self.assertEqual(b3["return_status"], "FULL")
        self.assertEqual(b3["new_bottle_stock"], "10")
        self.assertEqual(
            b3["reason"],
            "Eliminación de venta desde tabla Excel (ADMIN): 1.000000 -> 0",
        )

        # Excluida del listado por defecto y visible con include_returned=1
        res_default = self.client.get("/api/v1/sales")
        self.assertEqual(res_default.status_code, 200)
        self.assertNotIn(sale.id, [item["sale_id"] for item in res_default.json()["sales"]])

        res_incl = self.client.get("/api/v1/sales?include_returned=1")
        self.assertEqual(res_incl.status_code, 200)
        match = next(item for item in res_incl.json()["sales"] if item["sale_id"] == sale.id)
        self.assertTrue(match["is_fully_returned"])
        self.assertEqual(match["remaining_quantity"], "0.000000")

    def test_direct_component_sale_decimal_and_zero_adjustment_and_movement_reason(self):
        token = self._login("admin_excel", "Admin-password-123!")

        # Tener bottle_stock > 0 en prod_frac NO debe desviar a subinventario si from_subinventory=False
        self.prod_frac.bottle_stock = Decimal("2.000000")
        self.prod_frac.save(update_fields=["bottle_stock"])

        stock_before = get_component_stock(self.c_liq.id)
        sale = create_sale_request(
            product_id=self.prod_frac.id,
            quantity=Decimal("3.000000"),
            actor=self.admin,
            device_id="win-main",
        )
        # Forzar flujo directo (from_subinventory=False) con bottle_stock=0 al surtir y luego restaurar bottle_stock
        self.prod_frac.bottle_stock = Decimal("0.000000")
        self.prod_frac.save(update_fields=["bottle_stock"])
        fulfill_sale(sale.id, actor=self.admin, device_id="win-main")
        self.prod_frac.bottle_stock = Decimal("2.000000")
        self.prod_frac.save(update_fields=["bottle_stock"])

        self.assertEqual(
            get_component_stock(self.c_liq.id), stock_before - Decimal("2.250000")
        )

        # Ajuste decimal 3.000 -> 1.375 (delta = 1.625 -> reingreso = 1.625 * 0.750 = 1.218750 L)
        res1 = self._post_excel_qty(
            sale.id,
            {"target_quantity": "1.375", "reason": "  Ajuste granel  "},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res1.status_code, 200)
        b1 = res1.json()
        self.assertTrue(b1["allow_fractional_quantity"])
        self.assertFalse(b1["from_subinventory"])
        self.assertFalse(b1["returned_to_subinventory"])
        self.assertEqual(b1["delta_reversed"], "1.625000")
        self.assertEqual(b1["target_quantity"], "1.375000")
        self.assertEqual(b1["new_bottle_stock"], "2")
        self.assertFalse(b1["removed_from_history"])

        mov1 = InventoryMovement.objects.get(
            reference_type="return", reference_id=str(b1["return_id"])
        )
        self.assertEqual(mov1.movement_type, MovementType.RETURN_REENTRY)
        self.assertEqual(mov1.quantity_delta, Decimal("1.218750"))
        self.assertEqual(mov1.reason, "Ajuste granel")

        # Ajuste 1.375 -> 0 con reason vacío
        res2 = self._post_excel_qty(
            sale.id,
            {"target_quantity": "0"},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res2.status_code, 200)
        b2 = res2.json()
        self.assertTrue(b2["removed_from_history"])
        self.assertEqual(
            b2["reason"],
            "Eliminación de venta desde tabla Excel (ADMIN): 1.375000 -> 0",
        )
        mov2 = InventoryMovement.objects.get(
            reference_type="return", reference_id=str(b2["return_id"])
        )
        self.assertEqual(mov2.reason, b2["reason"])
        self.assertEqual(get_component_stock(self.c_liq.id), stock_before)

    def test_validation_400_cases(self):
        token = self._login("admin_excel", "Admin-password-123!")
        sale_int = self._create_fulfilled_sale_orm(quantity=Decimal("5.000000"))
        sale_frac = self._create_fulfilled_sale_orm(
            product=self.prod_frac,
            recipe_version=self.rv_frac,
            price_version=self.pv_frac,
            quantity=Decimal("5.000000"),
            unit_price=Decimal("300.00"),
        )

        # 1) Falta Idempotency-Key / vacía / inválida -> 400
        for bad_key in (None, "", "   ", "not-a-uuid"):
            res = self._post_excel_qty(
                sale_int.id, {"target_quantity": "2"}, token=token, idem_key=bad_key
            )
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["code"], "VALIDATION_ERROR")

        # 2) Payload no objeto -> 400
        k_nondict = str(uuid.uuid4())
        res_list = self._post_excel_qty(
            sale_int.id, [1, 2], token=token, idem_key=k_nondict
        )
        self.assertEqual(res_list.status_code, 400)
        self.assertEqual(res_list.json()["code"], "VALIDATION_ERROR")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_nondict).exists())

        # 3) Negativos y valores no finitos -> 400
        for payload in (
            {"target_quantity": "-1"},
            {"target_quantity": "-0.001"},
            {"target_quantity": "2", "expected_remaining_quantity": "-1"},
            {"target_quantity": "2", "refund_amount": "-0.01"},
            {"target_quantity": "NaN"},
            {},
        ):
            k = str(uuid.uuid4())
            res = self._post_excel_qty(sale_int.id, payload, token=token, idem_key=k)
            self.assertEqual(res.status_code, 400, msg=f"Falló payload={payload}")
            self.assertEqual(res.json()["code"], "VALIDATION_ERROR")
            self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k).exists())

        # 4) Decimal en producto entero -> 400 con mensaje explícito
        k_dec_int = str(uuid.uuid4())
        res_dec = self._post_excel_qty(
            sale_int.id, {"target_quantity": "2.5"}, token=token, idem_key=k_dec_int
        )
        self.assertEqual(res_dec.status_code, 400)
        self.assertEqual(res_dec.json()["code"], "VALIDATION_ERROR")
        self.assertIn("piezas enteras", res_dec.json()["message"])

        # 5) >3 decimales en producto fraccionable -> 400 con mensaje explícito
        k_4dp = str(uuid.uuid4())
        res_4dp = self._post_excel_qty(
            sale_frac.id, {"target_quantity": "1.2345"}, token=token, idem_key=k_4dp
        )
        self.assertEqual(res_4dp.status_code, 400)
        self.assertEqual(res_4dp.json()["code"], "VALIDATION_ERROR")
        self.assertIn("máximo 3 decimales", res_4dp.json()["message"])

        self.assertEqual(Return.objects.count(), 0)

    def test_conflicts_409_404_and_idempotency_rollback(self):
        token = self._login("admin_excel", "Admin-password-123!")

        # 404 venta inexistente
        k_404 = str(uuid.uuid4())
        res_404 = self._post_excel_qty(
            999999, {"target_quantity": "0"}, token=token, idem_key=k_404
        )
        self.assertEqual(res_404.status_code, 404)
        self.assertEqual(res_404.json()["code"], "RESOURCE_NOT_FOUND")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_404).exists())

        # PENDING y CANCELLED -> 409
        sale_pend = create_sale_request(
            product_id=self.prod_int.id, quantity=Decimal("2.000000"), actor=self.admin
        )
        sale_canc = create_sale_request(
            product_id=self.prod_int.id, quantity=Decimal("2.000000"), actor=self.admin
        )
        sale_canc.status = SaleStatus.CANCELLED
        sale_canc.save(update_fields=["status"])

        for s_bad in (sale_pend, sale_canc):
            k = str(uuid.uuid4())
            res = self._post_excel_qty(
                s_bad.id, {"target_quantity": "1"}, token=token, idem_key=k
            )
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.json()["code"], "BUSINESS_CONFLICT")
            self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k).exists())

        # Venta surtida de 5 unidades (from_subinventory=False)
        sale = self._create_fulfilled_sale_orm(
            quantity=Decimal("5.000000"), from_subinventory=False
        )

        # target >= actual -> 409 + rollback
        for bad_target in ("5", "6"):
            k = str(uuid.uuid4())
            res = self._post_excel_qty(
                sale.id, {"target_quantity": bad_target}, token=token, idem_key=k
            )
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.json()["code"], "BUSINESS_CONFLICT")
            self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k).exists())

        # Simetría física: to_subinventory=True en venta de componentes directos -> 409 + rollback
        k_sym1 = str(uuid.uuid4())
        res_sym1 = self._post_excel_qty(
            sale.id,
            {"target_quantity": "3", "to_subinventory": True},
            token=token,
            idem_key=k_sym1,
        )
        self.assertEqual(res_sym1.status_code, 409)
        self.assertEqual(res_sym1.json()["code"], "BUSINESS_CONFLICT")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_sym1).exists())

        # Simetría física inversa: to_subinventory=False en venta de subinventario -> 409 + rollback
        sale_sub = self._create_fulfilled_sale_orm(
            quantity=Decimal("4.000000"), from_subinventory=True
        )
        k_sym2 = str(uuid.uuid4())
        res_sym2 = self._post_excel_qty(
            sale_sub.id,
            {"target_quantity": "2", "to_subinventory": False},
            token=token,
            idem_key=k_sym2,
        )
        self.assertEqual(res_sym2.status_code, 409)
        self.assertEqual(res_sym2.json()["code"], "BUSINESS_CONFLICT")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_sym2).exists())

        # expected_remaining_quantity obsoleto -> 409 + rollback, luego misma key sí sirve si corrige payload
        k_stale = str(uuid.uuid4())
        res_stale = self._post_excel_qty(
            sale.id,
            {"target_quantity": "3", "expected_remaining_quantity": "4.000000"},
            token=token,
            idem_key=k_stale,
        )
        self.assertEqual(res_stale.status_code, 409)
        self.assertEqual(res_stale.json()["code"], "BUSINESS_CONFLICT")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_stale).exists())

        # Bajar a 0 usando k_stale con expected correcto -> 200 OK
        res_ok = self._post_excel_qty(
            sale.id,
            {"target_quantity": "0", "expected_remaining_quantity": "5.000000"},
            token=token,
            idem_key=k_stale,
        )
        self.assertEqual(res_ok.status_code, 200)
        self.assertTrue(CatalogIdempotency.objects.filter(idempotency_key=k_stale).exists())

        # Venta ya en 0 -> 409 + rollback
        k_zero = str(uuid.uuid4())
        res_already_zero = self._post_excel_qty(
            sale.id, {"target_quantity": "0"}, token=token, idem_key=k_zero
        )
        self.assertEqual(res_already_zero.status_code, 409)
        self.assertEqual(res_already_zero.json()["code"], "BUSINESS_CONFLICT")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_zero).exists())

    def test_idempotency_replay_200_and_key_collision_409(self):
        token = self._login("admin_excel", "Admin-password-123!")
        self.prod_int.bottle_stock = Decimal("5.000000")
        self.prod_int.save(update_fields=["bottle_stock"])
        sale = self._create_fulfilled_sale_orm(
            quantity=Decimal("4.000000"), from_subinventory=True
        )

        key1 = str(uuid.uuid4())
        payload1 = {"target_quantity": "2", "reason": "Ajuste idempotente"}
        res1 = self._post_excel_qty(sale.id, payload1, token=token, idem_key=key1)
        self.assertEqual(res1.status_code, 200)
        body1 = res1.json()

        # Replay idéntico -> 200 OK sin duplicar devolución ni stock
        res1_replay = self._post_excel_qty(sale.id, payload1, token=token, idem_key=key1)
        self.assertEqual(res1_replay.status_code, 200)
        self.assertEqual(res1_replay.json(), body1)
        self.assertEqual(Return.objects.filter(sale_line=sale.line).count(), 1)
        self.prod_int.refresh_from_db()
        self.assertEqual(self.prod_int.bottle_stock, Decimal("7.000000"))

        # Misma Idempotency-Key con payload distinto -> 409 IDEMPOTENCY_KEY_REUSED
        res_collision = self._post_excel_qty(
            sale.id,
            {"target_quantity": "1", "reason": "Otro payload"},
            token=token,
            idem_key=key1,
        )
        self.assertEqual(res_collision.status_code, 409)
        self.assertEqual(res_collision.json()["code"], "IDEMPOTENCY_KEY_REUSED")

        # Colisión contra Return.event_id existente sin CatalogIdempotency -> 409 IDEMPOTENCY_KEY_REUSED
        orphan_uuid = uuid.uuid4()
        Return.objects.create(
            event_id=orphan_uuid,
            sale_line=sale.line,
            status=ReturnStatus.COMPLETED,
            quantity=Decimal("1.000000"),
            returned_at=timezone.now(),
            refund_amount=Decimal("500.00"),
            tax_reversed=Decimal("68.97"),
        )
        res_orphan = self._post_excel_qty(
            sale.id,
            {"target_quantity": "0"},
            token=token,
            idem_key=str(orphan_uuid),
        )
        self.assertEqual(res_orphan.status_code, 409)
        self.assertEqual(res_orphan.json()["code"], "IDEMPOTENCY_KEY_REUSED")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=orphan_uuid).exists())

    def test_cost_required_422_and_rollback(self):
        token = self._login("admin_excel", "Admin-password-123!")
        sale = self._create_fulfilled_sale_orm(
            quantity=Decimal("2.000000"), from_subinventory=False
        )

        # Componente con stock > 0 y average_cost = None -> 422 COST_REQUIRED
        self.c_liq.average_cost = None
        self.c_liq.save(update_fields=["average_cost"])

        k_422 = str(uuid.uuid4())
        res_422 = self._post_excel_qty(
            sale.id, {"target_quantity": "1"}, token=token, idem_key=k_422
        )
        self.assertEqual(res_422.status_code, 422)
        self.assertEqual(res_422.json()["code"], "COST_REQUIRED")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_422).exists())
        self.assertEqual(Return.objects.filter(sale_line=sale.line).count(), 0)

        # Si stock == 0 y average_cost == None, no debe bloquear por COST_REQUIRED
        comp_zero = Component.objects.create(
            name="Componente Cero Stock", base_unit="pza", unit_precision=0, average_cost=None
        )
        prod_zero = create_product(name="Producto Cero Stock")
        rv_zero = add_recipe_version(
            product_id=prod_zero.id,
            components_data=[{"component_id": comp_zero.id, "quantity": Decimal("1.000000")}],
        )
        pv_zero = add_price_version(product_id=prod_zero.id, price_with_tax=Decimal("100.00"))
        sale_zero_stock = self._create_fulfilled_sale_orm(
            product=prod_zero,
            recipe_version=rv_zero,
            price_version=pv_zero,
            quantity=Decimal("2.000000"),
            unit_price=Decimal("100.00"),
            from_subinventory=False,
        )
        res_ok = self._post_excel_qty(
            sale_zero_stock.id,
            {"target_quantity": "1"},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res_ok.status_code, 200)

    def test_previous_retention_preserved_when_adjusting_to_zero(self):
        token = self._login("admin_excel", "Admin-password-123!")
        sale = self._create_fulfilled_sale_orm(
            quantity=Decimal("5.000000"),
            unit_price=Decimal("500.00"),
            from_subinventory=False,
        )

        # Paso 1: 5 -> 3 con retención explícita ($500 en lugar de $1000)
        res1 = self._post_excel_qty(
            sale.id,
            {"target_quantity": "3", "refund_amount": "500.00"},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["refund_amount"], "500.00")
        self.assertEqual(res1.json()["already_refunded"], "500.00")

        # Paso 2: 3 -> 0 sin refund_amount -> reembolsa proporcional $1500.00 y conserva retención de $500.00
        res2 = self._post_excel_qty(
            sale.id,
            {"target_quantity": "0"},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res2.status_code, 200)
        b2 = res2.json()
        self.assertEqual(b2["refund_amount"], "1500.00")
        self.assertEqual(b2["already_refunded"], "2000.00")
        self.assertEqual(b2["final_amount"], "500.00")
        self.assertEqual(b2["max_refund_possible"], "0.00")
        self.assertEqual(b2["return_status"], "FULL")

    def test_allows_fractional_guards_and_constant_queries_on_sales_list(self):
        token = self._login("admin_excel", "Admin-password-123!")

        # Guarda: recipe_version=None con cantidad entera -> False (no all([]) == True)
        sale_no_rv = self._create_fulfilled_sale_orm(
            quantity=Decimal("2.000000"), from_subinventory=False
        )
        sale_no_rv.line.recipe_version = None
        sale_no_rv.line.save(update_fields=["recipe_version"])
        self.assertFalse(
            compute_sale_allows_fractional(
                sale_no_rv.line, compute_sale_return_summary(sale_no_rv.line)
            )
        )

        # Guarda: receta vacía -> False
        empty_rv = RecipeVersion.objects.create(
            product=self.prod_int, version_number=99, active=False, created_by=self.admin
        )
        sale_empty_rv = self._create_fulfilled_sale_orm(
            recipe_version=empty_rv, quantity=Decimal("2.000000")
        )
        self.assertFalse(
            compute_sale_allows_fractional(
                sale_empty_rv.line, compute_sale_return_summary(sale_empty_rv.line)
            )
        )

        # Historia decimal sobre producto con componente entero -> allow_fractional=True
        sale_hist_dec = self._create_fulfilled_sale_orm(
            product=self.prod_int,
            recipe_version=self.rv_int,
            price_version=self.pv_int,
            quantity=Decimal("2.500000"),
            from_subinventory=False,
        )
        self.assertTrue(
            compute_sale_allows_fractional(
                sale_hist_dec.line, compute_sale_return_summary(sale_hist_dec.line)
            )
        )
        res_hist = self._post_excel_qty(
            sale_hist_dec.id,
            {"target_quantity": "1.250"},
            token=token,
            idem_key=str(uuid.uuid4()),
        )
        self.assertEqual(res_hist.status_code, 200)
        self.assertTrue(res_hist.json()["allow_fractional_quantity"])

        # Verificar assertNumQueries constante en GET /api/v1/sales?include_returned=1
        with CaptureQueriesContext(connection) as ctx_small:
            res_small = self.client.get("/api/v1/sales?include_returned=1&limit=50")
        self.assertEqual(res_small.status_code, 200)
        small_queries = len(ctx_small)

        for idx in range(15):
            if idx % 2 == 0:
                self._create_fulfilled_sale_orm(
                    product=self.prod_int,
                    recipe_version=self.rv_int,
                    price_version=self.pv_int,
                    quantity=Decimal("2.000000"),
                    from_subinventory=True,
                )
            else:
                self._create_fulfilled_sale_orm(
                    product=self.prod_frac,
                    recipe_version=self.rv_frac,
                    price_version=self.pv_frac,
                    quantity=Decimal("2.000000"),
                    unit_price=Decimal("300.00"),
                    from_subinventory=False,
                )

        with self.assertNumQueries(small_queries):
            res_large = self.client.get("/api/v1/sales?include_returned=1&limit=50")
        self.assertEqual(res_large.status_code, 200)
        sales_items = res_large.json()["sales"]
        self.assertGreaterEqual(len(sales_items), 18)
        for item in sales_items:
            self.assertIn("from_subinventory", item)
            self.assertIn("allow_fractional_quantity", item)


class CatalogContadorServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME

        cls.admin = User.objects.create_superuser(
            username="admin_cont_srv", password="Admin-password-123!"
        )
        cls.operator = User.objects.create_user(
            username="op_cont_srv", password="Op-password-123!"
        )
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

        cls.comp_liq = Component.objects.create(
            name="Destilado Contador",
            sku="MAT-LIQ-C1",
            component_type=ComponentType.LIQUID,
            base_unit="L",
            unit_precision=3,
            average_cost=Decimal("100.000000"),
        )
        cls.comp_bot = Component.objects.create(
            name="Botella Contador",
            sku="MAT-BOT-C1",
            component_type=ComponentType.CONTAINER,
            base_unit="pza",
            unit_precision=0,
            average_cost=Decimal("25.000000"),
        )
        cls.comp_missing = Component.objects.create(
            name="Etiqueta Sin Costo",
            sku="MAT-ETQ-NONE",
            component_type=ComponentType.OTHER,
            base_unit="pza",
            unit_precision=0,
            average_cost=None,
        )
        cls.comp_zero = Component.objects.create(
            name="Sello Costo Cero",
            sku="MAT-SEL-ZERO",
            component_type=ComponentType.OTHER,
            base_unit="pza",
            unit_precision=0,
            average_cost=Decimal("0.000000"),
        )

    def test_recipe_only_excludes_expenses_price_and_bottle_stock(self):
        prod = create_product(name="Valhalla Contador Base", sku="VAL-C1", actor=self.admin)
        prod.bottle_stock = Decimal("99.000000")
        prod.save(update_fields=["bottle_stock"])

        # Receta v1 inactiva después de v2
        add_recipe_version(
            product_id=prod.id,
            components_data=[{"component_id": self.comp_bot.id, "quantity": Decimal("1")}],
            actor=self.admin,
        )
        # Receta v2 activa: 0.750 * 100 + 1 * 25 = 100.00
        add_recipe_version(
            product_id=prod.id,
            components_data=[
                {"component_id": self.comp_liq.id, "quantity": Decimal("0.750")},
                {"component_id": self.comp_bot.id, "quantity": Decimal("1")},
            ],
            actor=self.admin,
        )
        add_price_version(
            product_id=prod.id,
            price_with_tax=Decimal("750.00"),
            actor=self.admin,
        )
        Expense.objects.create(
            expense_type=ExpenseType.ONE_TIME,
            name="Renta General",
            amount=Decimal("5000.00"),
            occurred_at=timezone.now(),
            created_by=self.admin,
        )

        dto = get_product_contador_summary(prod.id)
        self.assertEqual(dto["current_price"], "750.00")
        self.assertEqual(dto["recipe_version_number"], 2)
        self.assertTrue(dto["has_active_recipe"])
        self.assertTrue(dto["recipe_cost_complete"])
        self.assertTrue(dto["cost_known"])
        self.assertEqual(dto["known_components_subtotal"], "100.00")
        self.assertEqual(dto["recipe_total_cost"], "100.00")
        self.assertEqual(dto["partial_production_cost"], "100.00")
        self.assertEqual(dto["recipe_share_of_partial_percent"], "100.00")
        self.assertEqual(dto["currency"], "MXN")

    def test_line_rounding_round_half_up(self):
        comp_tie = Component.objects.create(
            name="Insumo Tie HalfUp",
            base_unit="L",
            unit_precision=3,
            average_cost=Decimal("1.010000"),
        )
        comp_tie_2 = Component.objects.create(
            name="Insumo Tie HalfUp 2",
            base_unit="L",
            unit_precision=3,
            average_cost=Decimal("1.030000"),
        )
        prod = create_product(name="Producto Redondeo", actor=self.admin)
        add_recipe_version(
            product_id=prod.id,
            components_data=[
                {"component_id": comp_tie.id, "quantity": Decimal("0.500")},
                {"component_id": comp_tie_2.id, "quantity": Decimal("0.500")},
            ],
            actor=self.admin,
        )
        dto = get_product_contador_summary(prod.id)
        by_id = {c["component_id"]: c for c in dto["recipe_components"]}
        # 0.500 * 1.01 = 0.505 -> ROUND_HALF_UP = 0.51 (no ROUND_HALF_EVEN 0.50)
        self.assertEqual(by_id[comp_tie.id]["line_cost"], "0.51")
        self.assertEqual(by_id[comp_tie.id]["line_cost_precise"], "0.505000")
        # 0.500 * 1.03 = 0.515 -> ROUND_HALF_UP = 0.52
        self.assertEqual(by_id[comp_tie_2.id]["line_cost"], "0.52")
        self.assertEqual(dto["recipe_total_cost"], "1.03")

    def test_missing_cost_vs_zero_cost_and_division_by_zero_guard(self):
        # Caso 1: componente con average_cost=None + impuesto FIXED + PERCENT
        prod_missing = create_product(name="Producto Sin Costo", actor=self.admin)
        add_recipe_version(
            product_id=prod_missing.id,
            components_data=[
                {"component_id": self.comp_bot.id, "quantity": Decimal("1")},
                {"component_id": self.comp_missing.id, "quantity": Decimal("2")},
            ],
            actor=self.admin,
        )
        create_product_custom_tax(
            product_id=prod_missing.id,
            name="Fijo",
            tax_type=CustomTaxType.FIXED,
            rate_or_amount=Decimal("15.00"),
            actor=self.admin,
        )
        create_product_custom_tax(
            product_id=prod_missing.id,
            name="Porcentual",
            tax_type=CustomTaxType.PERCENT,
            rate_or_amount=Decimal("16.0000"),
            actor=self.admin,
        )
        dto_missing = get_product_contador_summary(prod_missing.id)
        self.assertFalse(dto_missing["recipe_cost_complete"])
        self.assertFalse(dto_missing["cost_known"])
        self.assertEqual(dto_missing["known_components_subtotal"], "25.00")
        self.assertIsNone(dto_missing["recipe_total_cost"])
        self.assertEqual(dto_missing["missing_cost_components_count"], 1)
        self.assertEqual(dto_missing["missing_cost_components"][0]["component_id"], self.comp_missing.id)
        self.assertFalse(dto_missing["taxes_cost_complete"])
        self.assertEqual(dto_missing["known_taxes_subtotal"], "15.00")
        self.assertIsNone(dto_missing["taxes_total"])
        self.assertIsNone(dto_missing["partial_production_cost"])
        self.assertEqual(dto_missing["recipe_share_of_partial_percent"], "0.00")
        self.assertTrue(dto_missing["taxes"][0]["amount_known"])
        self.assertEqual(dto_missing["taxes"][0]["calculated_amount"], "15.00")
        self.assertFalse(dto_missing["taxes"][1]["amount_known"])
        self.assertIsNone(dto_missing["taxes"][1]["calculated_amount"])
        self.assertEqual(dto_missing["taxes"][1]["share_of_partial_percent"], "0.00")

        # Caso 2: componente con average_cost=0.000000 -> costo conocido $0.00 sin división por cero
        prod_zero = create_product(name="Producto Costo Cero", actor=self.admin)
        add_recipe_version(
            product_id=prod_zero.id,
            components_data=[{"component_id": self.comp_zero.id, "quantity": Decimal("1")}],
            actor=self.admin,
        )
        dto_zero = get_product_contador_summary(prod_zero.id)
        self.assertTrue(dto_zero["recipe_cost_complete"])
        self.assertTrue(dto_zero["cost_known"])
        self.assertEqual(dto_zero["recipe_total_cost"], "0.00")
        self.assertEqual(dto_zero["partial_production_cost"], "0.00")
        self.assertEqual(dto_zero["recipe_components"][0]["share_percent"], "0.00")
        self.assertEqual(dto_zero["recipe_share_of_partial_percent"], "0.00")

        # Caso 3: receta $0.00 + impuesto fijo $10.00 -> partial=$10.00, tax_share=100.00%
        create_product_custom_tax(
            product_id=prod_zero.id,
            name="Envase",
            tax_type=CustomTaxType.FIXED,
            rate_or_amount=Decimal("10.00"),
            actor=self.admin,
        )
        dto_zero_tax = get_product_contador_summary(prod_zero.id)
        self.assertEqual(dto_zero_tax["partial_production_cost"], "10.00")
        self.assertEqual(dto_zero_tax["recipe_share_of_partial_percent"], "0.00")
        self.assertEqual(dto_zero_tax["taxes"][0]["share_of_partial_percent"], "100.00")

        # Caso 4: producto sin receta activa
        prod_no_rec = create_product(name="Producto Sin Receta", actor=self.admin)
        dto_no_rec = get_product_contador_summary(prod_no_rec.id)
        self.assertFalse(dto_no_rec["has_active_recipe"])
        self.assertFalse(dto_no_rec["recipe_cost_complete"])
        self.assertIsNone(dto_no_rec["recipe_total_cost"])
        self.assertIsNone(dto_no_rec["partial_production_cost"])

    def test_three_independent_taxes_no_cascade_and_fourth_tax_conflict(self):
        prod = create_product(name="Valhalla 3 Impuestos", actor=self.admin)
        add_recipe_version(
            product_id=prod.id,
            components_data=[{"component_id": self.comp_liq.id, "quantity": Decimal("2.000")}],
            actor=self.admin,
        )
        # Receta = 200.00
        r1 = create_product_custom_tax(
            product_id=prod.id,
            name="IEPS",
            tax_type=CustomTaxType.PERCENT,
            rate_or_amount=Decimal("26.5"),
            actor=self.admin,
        )
        self.assertEqual(r1["tax"]["edit_value"], "26.5")
        self.assertEqual(r1["tax"]["display_value"], "26.5%")
        self.assertEqual(r1["tax"]["created_by"], "admin_cont_srv")
        self.assertIsInstance(r1["tax"]["created_by"], str)

        create_product_custom_tax(
            product_id=prod.id,
            name="IVA",
            tax_type=CustomTaxType.PERCENT,
            rate_or_amount=Decimal("16"),
            actor=self.admin,
        )
        r3 = create_product_custom_tax(
            product_id=prod.id,
            name="Marbete",
            tax_type=CustomTaxType.FIXED,
            rate_or_amount=Decimal("15.50"),
            actor=self.admin,
        )
        c = r3["contador"]
        # Sin cascada: 200*0.265=53.00, 200*0.16=32.00, fijo=15.50 -> taxes_total=100.50
        self.assertEqual(c["recipe_total_cost"], "200.00")
        self.assertEqual(c["taxes_total"], "100.50")
        self.assertEqual(c["partial_production_cost"], "300.50")
        self.assertEqual(c["taxes_count"], 3)
        self.assertFalse(c["can_add_tax"])
        self.assertEqual(c["limit_state"], "LIMIT_REACHED_3_OF_3")
        self.assertEqual(c["taxes"][0]["calculated_amount"], "53.00")
        self.assertEqual(c["taxes"][1]["calculated_amount"], "32.00")
        self.assertEqual(c["taxes"][1]["edit_value"], "16")
        self.assertEqual(c["taxes"][1]["display_value"], "16%")
        self.assertEqual(c["taxes"][2]["rate_or_amount"], "15.50")
        self.assertEqual(c["taxes"][2]["edit_value"], "15.50")
        self.assertEqual(c["taxes"][2]["display_value"], "$15.50")

        # 4.º impuesto -> BusinessConflictError con details.reason == LIMIT_REACHED_3_OF_3
        with self.assertRaises(BusinessConflictError) as ctx:
            create_product_custom_tax(
                product_id=prod.id,
                name="Cuarto",
                tax_type=CustomTaxType.FIXED,
                rate_or_amount=Decimal("5.00"),
                actor=self.admin,
            )
        self.assertEqual(ctx.exception.details["reason"], "LIMIT_REACHED_3_OF_3")
        self.assertEqual(ctx.exception.details["product_id"], prod.id)
        self.assertEqual(ctx.exception.details["max_taxes"], 3)

        # Posición explícita ocupada y recompactación intermedia ([1,2,3] borra pos 2 -> [1,2])
        t_pos2 = c["taxes"][1]["tax_id"]
        t_pos3 = c["taxes"][2]["tax_id"]
        res_del_mid = delete_product_custom_tax(
            product_id=prod.id, tax_id=t_pos2, actor=self.admin
        )
        self.assertEqual(
            [(t["tax_id"], t["position"]) for t in res_del_mid["contador"]["taxes"]],
            [(c["taxes"][0]["tax_id"], 1), (t_pos3, 2)],
        )
        with self.assertRaises(BusinessConflictError) as ctx_pos:
            create_product_custom_tax(
                product_id=prod.id,
                name="Ocupada",
                tax_type=CustomTaxType.FIXED,
                rate_or_amount=Decimal("2.00"),
                position=2,
                actor=self.admin,
            )
        self.assertEqual(ctx_pos.exception.details["reason"], "POSITION_OCCUPIED")
        with self.assertRaises(BusinessConflictError) as ctx_upd_pos:
            update_product_custom_tax(
                product_id=prod.id,
                tax_id=t_pos3,
                updates={"position": 1},
                actor=self.admin,
            )
        self.assertEqual(ctx_upd_pos.exception.details["reason"], "POSITION_OCCUPIED")

    def test_normalization_rate_validation_rbac_and_orm_constraints(self):
        # Operador / None rechazados en servicio
        prod = create_product(name="Prod Validaciones", actor=self.admin)
        with self.assertRaises(PermissionDeniedError):
            create_product_custom_tax(
                product_id=prod.id,
                name="IVA",
                tax_type=CustomTaxType.PERCENT,
                rate_or_amount=Decimal("16"),
                actor=self.operator,
            )
        with self.assertRaises(PermissionDeniedError):
            create_product_custom_tax(
                product_id=prod.id,
                name="IVA",
                tax_type=CustomTaxType.PERCENT,
                rate_or_amount=Decimal("16"),
                actor=None,
            )

        # Caracteres de control, vacío, no str, longitud > 80
        for bad_name in (123, "", "   ", "IVA\n", "IVA\x00", "IVA\x7f", "IVA\t1", "A" * 81):
            with self.assertRaises(DomainValidationError):
                normalize_tax_name(bad_name)

        clean, norm = normalize_tax_name("  ＩＥＰＳ   Especial  ")
        self.assertEqual(clean, "IEPS Especial")
        self.assertEqual(norm, "ieps especial")

        # Validación de tasa por decimales significativos y límites
        self.assertEqual(validate_tax_type_and_rate("FIXED", "15.5000"), Decimal("15.5000"))
        self.assertEqual(validate_tax_type_and_rate("PERCENT", "16.1234"), Decimal("16.1234"))
        for bad_type, bad_rate in (
            ("OTHER", "10"),
            ("FIXED", "15.555"),
            ("FIXED", "-0.01"),
            ("FIXED", "10000000.00"),
            ("PERCENT", "16.12345"),
            ("PERCENT", "1000.0001"),
            ("PERCENT", True),
            ("PERCENT", "NaN"),
        ):
            with self.assertRaises(DomainValidationError):
                validate_tax_type_and_rate(bad_type, bad_rate)

        # Constraints ORM directos (IntegrityError)
        t1 = ProductCustomTax.objects.create(
            product=prod,
            name="IEPS",
            normalized_name="ieps",
            tax_type=CustomTaxType.PERCENT,
            rate_or_amount=Decimal("16.0000"),
            position=1,
            created_by=self.admin,
        )
        self.assertEqual(t1.position, 1)

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProductCustomTax.objects.create(
                    product=prod,
                    name="IEPS 2",
                    normalized_name="ieps",
                    tax_type=CustomTaxType.FIXED,
                    rate_or_amount=Decimal("5.0000"),
                    position=2,
                )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProductCustomTax.objects.create(
                    product=prod,
                    name="Otro",
                    normalized_name="otro",
                    tax_type=CustomTaxType.FIXED,
                    rate_or_amount=Decimal("5.0000"),
                    position=1,
                )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProductCustomTax.objects.create(
                    product=prod,
                    name="Negativo",
                    normalized_name="negativo",
                    tax_type=CustomTaxType.FIXED,
                    rate_or_amount=Decimal("-0.0001"),
                    position=2,
                )

        for bad_pos in (0, 4):
            with transaction.atomic():
                with self.assertRaises(IntegrityError):
                    ProductCustomTax.objects.create(
                        product=prod,
                        name=f"Pos {bad_pos}",
                        normalized_name=f"pos {bad_pos}",
                        tax_type=CustomTaxType.FIXED,
                        rate_or_amount=Decimal("1.0000"),
                        position=bad_pos,
                    )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ProductCustomTax.objects.create(
                    product=prod,
                    name="Tipo Malo",
                    normalized_name="tipo malo",
                    tax_type="INVALID",
                    rate_or_amount=Decimal("1.0000"),
                    position=2,
                )


class CatalogContadorApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME

        cls.admin = User.objects.create_superuser(
            username="admin_cont_api", password="Admin-password-123!"
        )
        cls.operator = User.objects.create_user(
            username="op_cont_api", password="Op-password-123!"
        )
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

        cls.comp_a = Component.objects.create(
            name="Destilado Api C",
            sku="MAT-API-1",
            component_type=ComponentType.LIQUID,
            base_unit="L",
            unit_precision=3,
            average_cost=Decimal("120.000000"),
        )
        cls.comp_b = Component.objects.create(
            name="Botella Api C",
            sku="MAT-API-2",
            component_type=ComponentType.CONTAINER,
            base_unit="pza",
            unit_precision=0,
            average_cost=Decimal("30.000000"),
        )
        cls.prod_a = create_product(name="Valhalla Contador API A", sku="VAL-CA", actor=cls.admin)
        add_recipe_version(
            product_id=cls.prod_a.id,
            components_data=[
                {"component_id": cls.comp_a.id, "quantity": Decimal("0.750")},
                {"component_id": cls.comp_b.id, "quantity": Decimal("1")},
            ],
            actor=cls.admin,
        )
        add_price_version(
            product_id=cls.prod_a.id,
            price_with_tax=Decimal("450.00"),
            actor=cls.admin,
        )
        cls.prod_b = create_product(name="Valhalla Contador API B", sku="VAL-CB", actor=cls.admin)
        add_recipe_version(
            product_id=cls.prod_b.id,
            components_data=[{"component_id": cls.comp_b.id, "quantity": Decimal("1")}],
            actor=cls.admin,
        )

    def setUp(self):
        from django.test import Client

        self.client = Client(enforce_csrf_checks=True)

    def _get_csrf(self):
        res = self.client.get("/api/v1/auth/csrf")
        self.assertEqual(res.status_code, 200)
        return res.json()["csrf_token"]

    def _login(self, username, password):
        token = self._get_csrf()
        res = self.client.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self._get_csrf()

    def test_rbac_csrf_and_idor_protections(self):
        # 1. Sin autenticar -> 401 en GET y POST (con CSRF)
        csrf_anon = self._get_csrf()
        self.assertEqual(
            self.client.get(f"/api/v1/products/{self.prod_a.id}/contador").status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/products/{self.prod_a.id}/contador/taxes",
                data=json.dumps({"name": "IVA", "tax_type": "PERCENT", "rate_or_amount": "16"}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=csrf_anon,
                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
            ).status_code,
            401,
        )

        # 2. OPERADOR puede leer GET contador y products/pages, pero recibe 403 en POST/PATCH/DELETE
        op_token = self._login("op_cont_api", "Op-password-123!")
        self.assertEqual(
            self.client.get(f"/api/v1/products/{self.prod_a.id}/contador").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/api/v1/products/pages/inicio").status_code,
            200,
        )
        res_op_post = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "IVA", "tax_type": "PERCENT", "rate_or_amount": "16"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=op_token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_op_post.status_code, 403)
        self.assertEqual(res_op_post.json()["code"], "PERMISSION_DENIED")

        # 3. ADMIN sin CSRF recibe 403 CSRF_FAILED
        admin_token = self._login("admin_cont_api", "Admin-password-123!")
        res_no_csrf = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "IVA", "tax_type": "PERCENT", "rate_or_amount": "16"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_no_csrf.status_code, 403)
        self.assertEqual(res_no_csrf.json()["code"], "CSRF_FAILED")

        # 4. IDOR cruzado: impuesto creado en prod_b no es accesible desde prod_a -> 404
        res_tax_b = self.client.post(
            f"/api/v1/products/{self.prod_b.id}/contador/taxes",
            data=json.dumps({"name": "Fijo B", "tax_type": "FIXED", "rate_or_amount": "12.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_tax_b.status_code, 201)
        tax_b_id = res_tax_b.json()["tax"]["tax_id"]

        res_idor_patch = self.client.patch(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{tax_b_id}",
            data=json.dumps({"name": "Hack"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res_idor_patch.status_code, 404)
        self.assertEqual(res_idor_patch.json()["code"], "RESOURCE_NOT_FOUND")

        k_idor_del = str(uuid.uuid4())
        res_idor_del = self.client.delete(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{tax_b_id}",
            data="",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=admin_token,
            HTTP_IDEMPOTENCY_KEY=k_idor_del,
        )
        self.assertEqual(res_idor_del.status_code, 404)
        self.assertTrue(ProductCustomTax.objects.filter(id=tax_b_id, product=self.prod_b).exists())
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_idor_del).exists())

    def test_crud_patch_cross_validation_limit_409_and_delete_idempotency_recompaction(self):
        token = self._login("admin_cont_api", "Admin-password-123!")

        # 1. Crear 3 impuestos (NFKC unicidad + roundtrip FIXED 15.5000 + PERCENT 4dp)
        k1 = str(uuid.uuid4())
        res1 = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "  ＩＥＰＳ   Especial ", "tax_type": "PERCENT", "rate_or_amount": "16.1250"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k1,
        )
        self.assertEqual(res1.status_code, 201)
        t1_id = res1.json()["tax"]["tax_id"]
        self.assertEqual(res1.json()["tax"]["name"], "IEPS Especial")
        self.assertEqual(res1.json()["tax"]["position"], 1)

        # Nombre duplicado Unicode casefold -> 409 y rollback de idempotency
        k_dup = str(uuid.uuid4())
        res_dup = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "ieps especial", "tax_type": "FIXED", "rate_or_amount": "10.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_dup,
        )
        self.assertEqual(res_dup.status_code, 409)
        self.assertEqual(res_dup.json()["details"]["reason"], "DUPLICATE_TAX_NAME")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_dup).exists())

        # Validación cruzada PATCH: PERCENT 16.1250 -> FIXED sin ajustar decimales = 400 + rollback idempotency
        k_cross = str(uuid.uuid4())
        res_cross = self.client.patch(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data=json.dumps({"tax_type": "FIXED"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_cross,
        )
        self.assertEqual(res_cross.status_code, 400)
        self.assertEqual(res_cross.json()["code"], "VALIDATION_ERROR")
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_cross).exists())

        # PATCH vacío {} -> 400 + rollback idempotency
        k_empty = str(uuid.uuid4())
        res_empty = self.client.patch(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_empty,
        )
        self.assertEqual(res_empty.status_code, 400)
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_empty).exists())

        # Roundtrip FIXED "15.5000" en PATCH -> 200 ("15.50" / "$15.50")
        res_patch_fixed = self.client.patch(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data=json.dumps({"tax_type": "FIXED", "rate_or_amount": "15.5000"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_cross,
        )
        self.assertEqual(res_patch_fixed.status_code, 200)
        self.assertEqual(res_patch_fixed.json()["tax"]["tax_type"], "FIXED")
        self.assertEqual(res_patch_fixed.json()["tax"]["rate_or_amount"], "15.50")
        self.assertEqual(res_patch_fixed.json()["tax"]["edit_value"], "15.50")
        self.assertEqual(res_patch_fixed.json()["tax"]["display_value"], "$15.50")

        # Crear impuestos 2 y 3
        res2 = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "IVA", "tax_type": "PERCENT", "rate_or_amount": "16"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res2.status_code, 201)
        t2_id = res2.json()["tax"]["tax_id"]

        res3 = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "Envío Especial", "tax_type": "FIXED", "rate_or_amount": "20.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(res3.status_code, 201)
        t3_id = res3.json()["tax"]["tax_id"]
        self.assertEqual(res3.json()["contador"]["limit_state"], "LIMIT_REACHED_3_OF_3")

        # 4.º impuesto -> 409 con details.reason == LIMIT_REACHED_3_OF_3 y rollback idempotency
        k_4th = str(uuid.uuid4())
        res4 = self.client.post(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes",
            data=json.dumps({"name": "Cuarto", "tax_type": "FIXED", "rate_or_amount": "1.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_4th,
        )
        self.assertEqual(res4.status_code, 409)
        b4 = res4.json()
        self.assertEqual(b4["code"], "BUSINESS_CONFLICT")
        self.assertEqual(b4["details"]["reason"], "LIMIT_REACHED_3_OF_3")
        self.assertEqual(b4["details"]["product_id"], self.prod_a.id)
        self.assertEqual(b4["details"]["max_taxes"], 3)
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_4th).exists())

        # DELETE con body no vacío -> 400 + rollback idempotency
        k_bad_del = str(uuid.uuid4())
        res_bad_del = self.client.delete(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data=json.dumps({"extra": True}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_bad_del,
        )
        self.assertEqual(res_bad_del.status_code, 400)
        self.assertFalse(CatalogIdempotency.objects.filter(idempotency_key=k_bad_del).exists())

        # DELETE impuesto 1 con body vacío + Content-Type: application/json -> 200 y recompacta [2,3] -> [1,2]
        k_del = str(uuid.uuid4())
        res_del = self.client.delete(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data="",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_del,
        )
        self.assertEqual(res_del.status_code, 200)
        del_body = res_del.json()
        self.assertEqual(del_body["deleted_tax_id"], t1_id)
        self.assertEqual(
            [(t["tax_id"], t["position"]) for t in del_body["contador"]["taxes"]],
            [(t2_id, 1), (t3_id, 2)],
        )
        self.assertEqual(del_body["contador"]["limit_state"], "AVAILABLE")
        self.assertTrue(del_body["contador"]["can_add_tax"])

        # Reintento idempotente de DELETE -> 200 cacheado idéntico (NO 404)
        res_del_retry = self.client.delete(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t1_id}",
            data="",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_del,
        )
        self.assertEqual(res_del_retry.status_code, 200)
        self.assertEqual(res_del_retry.json(), del_body)

        # Reusar clave de DELETE con otro tax_id -> 409 IDEMPOTENCY_KEY_REUSED
        res_del_col = self.client.delete(
            f"/api/v1/products/{self.prod_a.id}/contador/taxes/{t2_id}",
            data="",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=k_del,
        )
        self.assertEqual(res_del_col.status_code, 409)
        self.assertEqual(res_del_col.json()["code"], "IDEMPOTENCY_KEY_REUSED")

    def test_product_hard_and_soft_delete_with_custom_taxes(self):
        token = self._login("admin_cont_api", "Admin-password-123!")

        # Hard delete sin ventas: CASCADE borra ProductCustomTax
        prod_hard = create_product(name="Prod Hard Delete C", actor=self.admin)
        add_recipe_version(
            product_id=prod_hard.id,
            components_data=[{"component_id": self.comp_b.id, "quantity": Decimal("1")}],
            actor=self.admin,
        )
        create_product_custom_tax(
            product_id=prod_hard.id,
            name="Impuesto Hard",
            tax_type=CustomTaxType.FIXED,
            rate_or_amount=Decimal("5.00"),
            actor=self.admin,
        )
        self.assertEqual(ProductCustomTax.objects.filter(product=prod_hard).count(), 1)
        res_hd = self.client.delete(
            f"/api/v1/products/{prod_hard.id}",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_hd.status_code, 200)
        self.assertEqual(res_hd.json()["status"], "DELETED")
        self.assertEqual(ProductCustomTax.objects.filter(product_id=prod_hard.id).count(), 0)

        # Soft delete con venta histórica: preserva ProductCustomTax y contador sigue accesible
        register_receipt(self.comp_b.id, Decimal("20.000000"), Decimal("30.000000"), actor=self.admin)
        prod_soft = create_product(name="Prod Soft Delete C", actor=self.admin)
        add_recipe_version(
            product_id=prod_soft.id,
            components_data=[{"component_id": self.comp_b.id, "quantity": Decimal("1")}],
            actor=self.admin,
        )
        add_price_version(product_id=prod_soft.id, price_with_tax=Decimal("200.00"), actor=self.admin)
        create_product_custom_tax(
            product_id=prod_soft.id,
            name="Impuesto Soft",
            tax_type=CustomTaxType.PERCENT,
            rate_or_amount=Decimal("10"),
            actor=self.admin,
        )
        sale = create_sale_request(product_id=prod_soft.id, quantity=Decimal("1"), actor=self.admin)
        fulfill_sale(sale.id, actor=self.admin)

        res_sd = self.client.delete(
            f"/api/v1/products/{prod_soft.id}",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res_sd.status_code, 200)
        self.assertEqual(res_sd.json()["status"], "DEACTIVATED")
        self.assertEqual(ProductCustomTax.objects.filter(product_id=prod_soft.id).count(), 1)

        res_cont_soft = self.client.get(f"/api/v1/products/{prod_soft.id}/contador")
        self.assertEqual(res_cont_soft.status_code, 200)
        self.assertFalse(res_cont_soft.json()["active"])
        self.assertEqual(res_cont_soft.json()["taxes_count"], 1)

    def test_constant_num_queries_on_products_page_and_contador_detail(self):
        self._login("admin_cont_api", "Admin-password-123!")

        # Añadir impuesto a prod_a y prod_b antes de medir baseline pequeño
        create_product_custom_tax(
            product_id=self.prod_a.id,
            name="Base Tax A",
            tax_type=CustomTaxType.FIXED,
            rate_or_amount=Decimal("5.00"),
            actor=self.admin,
        )

        with CaptureQueriesContext(connection) as ctx_list_small:
            res_list_small = self.client.get("/api/v1/products/pages/inicio")
        self.assertEqual(res_list_small.status_code, 200)
        self.assertIn("contador_summary", res_list_small.json()["items"][0])
        list_small_queries = len(ctx_list_small)

        with CaptureQueriesContext(connection) as ctx_detail_small:
            res_det_small = self.client.get(f"/api/v1/products/{self.prod_b.id}/contador")
        self.assertEqual(res_det_small.status_code, 200)
        detail_small_queries = len(ctx_detail_small)

        # Crear 20 productos con múltiples componentes y 3 impuestos cada uno
        extra_comps = [
            Component.objects.create(
                name=f"Extra Comp {i}",
                base_unit="pza",
                unit_precision=0,
                average_cost=Decimal("10.000000"),
            )
            for i in range(5)
        ]
        last_prod = None
        for idx in range(20):
            p = create_product(name=f"Prod N+1 {idx:02d}", actor=self.admin)
            add_recipe_version(
                product_id=p.id,
                components_data=[
                    {"component_id": c.id, "quantity": Decimal("1")} for c in extra_comps
                ],
                actor=self.admin,
            )
            add_price_version(product_id=p.id, price_with_tax=Decimal("300.00"), actor=self.admin)
            for t_idx in range(1, 4):
                create_product_custom_tax(
                    product_id=p.id,
                    name=f"Imp {t_idx}",
                    tax_type=CustomTaxType.FIXED,
                    rate_or_amount=Decimal("2.00"),
                    actor=self.admin,
                )
            last_prod = p

        with self.assertNumQueries(list_small_queries):
            res_list_large = self.client.get("/api/v1/products/pages/inicio")
        self.assertEqual(res_list_large.status_code, 200)
        self.assertEqual(len(res_list_large.json()["items"]), 22)
        for item in res_list_large.json()["items"]:
            self.assertIn("contador_summary", item)

        with self.assertNumQueries(detail_small_queries):
            res_det_large = self.client.get(f"/api/v1/products/{last_prod.id}/contador")
        self.assertEqual(res_det_large.status_code, 200)
        self.assertEqual(len(res_det_large.json()["recipe_components"]), 5)
        self.assertEqual(res_det_large.json()["taxes_count"], 3)


# ===========================================================================
# Fase 5A: Pruebas Exhaustivas de Gastos, Dashboard Mensual y Restricciones
# ===========================================================================

class ExpenseFase5ATests(TestCase):
    """
    Tests exhaustivos para la Fase 5A (Gastos y Dashboard Mensual):
    - chk_expense_amount_strictly_positive en models.
    - Idempotencia por event_id y Idempotency-Key.
    - H-01: Proteger gastos de apertura (device_id == 'initial_import') contra PATCH y DELETE.
    - H-03: Snapshot de auditoría antes de eliminar fila en DELETE.
    - H-04: Cálculo de daily_average (mes actual divisor = max(1, now_cdmx.day); mes pasado = total_days).
    - H-06: Propagación de category en generación de recurrentes.
    - RBAC: OPERADOR puede crear gastos únicos y ver dashboard; solo ADMIN puede editar/borrar.
    """

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.admin = user_model.objects.create_superuser(
            username="admin_exp", password="Admin-password-123!"
        )
        cls.operator = user_model.objects.create_user(
            username="op_exp", password="Op-password-123!"
        )
        from django.contrib.auth.models import Group
        from system.constants import OPERATOR_GROUP_NAME
        op_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        cls.operator.groups.add(op_group)

    def setUp(self):
        from django.test import Client
        self.client = Client(enforce_csrf_checks=True)

    def get_csrf(self, client=None):
        c = client or self.client
        res = c.get("/api/v1/auth/csrf")
        return res.json()["csrf_token"]

    def login(self, username, password, client=None):
        import json
        c = client or self.client
        token = self.get_csrf(c)
        res = c.post(
            "/api/v1/auth/session",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(res.status_code, 200)
        return self.get_csrf(c)

    def test_expense_strictly_positive_constraint_at_db_level(self):
        """Verifica que el constraint chk_expense_amount_strictly_positive bloquee montos <= 0."""
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Expense.objects.create(
                    name="Gasto Invalido Cero",
                    amount=Decimal("0.00"),
                    occurred_at=timezone.now(),
                )

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                Expense.objects.create(
                    name="Gasto Invalido Negativo",
                    amount=Decimal("-10.00"),
                    occurred_at=timezone.now(),
                )

    def test_service_register_single_expense_and_event_id_idempotency(self):
        """Verifica registro atómico, normalización de categoría e idempotencia por event_id."""
        from catalog.services import register_single_expense

        fixed_event_id = uuid.uuid4()
        payload = {
            "name": "  Servicio de Agua  ",
            "amount": "450.50",
            "category": "  servicios generales  ",
            "event_id": fixed_event_id,
        }

        # 1. Creación exitosa por OPERADOR
        result1 = register_single_expense(payload, actor=self.operator)
        self.assertEqual(result1["name"], "Servicio de Agua")
        self.assertEqual(result1["amount"], "450.50")
        self.assertEqual(result1["category"], "SERVICIOS GENERALES")
        self.assertEqual(result1["event_id"], str(fixed_event_id))
        self.assertEqual(Expense.objects.count(), 1)

        # 2. Misma clave event_id con datos idénticos -> idempotente (no duplica)
        result2 = register_single_expense(payload, actor=self.operator)
        self.assertEqual(result2["id"], result1["id"])
        self.assertEqual(Expense.objects.count(), 1)

        # 3. Misma clave event_id con payload modificado -> IdempotencyKeyReusedError
        conflicting_payload = {
            "name": "Servicio de Luz",
            "amount": "999.00",
            "category": "SERVICIOS GENERALES",
            "event_id": fixed_event_id,
        }
        with self.assertRaises(IdempotencyKeyReusedError):
            register_single_expense(conflicting_payload, actor=self.operator)

        # 4. Validaciones de importe y nombre
        with self.assertRaises(DomainValidationError):
            register_single_expense({"name": "", "amount": "100.00"}, actor=self.operator)

        with self.assertRaises(DomainValidationError):
            register_single_expense({"name": "Gasto Cero", "amount": "0.00"}, actor=self.operator)

    def test_service_update_expense_and_opening_expense_protection_h01(self):
        """Verifica actualización segura y bloqueo H-01 sobre initial_import."""
        from catalog.services import update_expense, register_single_expense

        exp_data = register_single_expense(
            {"name": "Gasto Inicial", "amount": "100.00", "category": "INSUMOS"},
            actor=self.admin,
        )
        expense_id = exp_data["id"]

        # 1. Operador no puede actualizar -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            update_expense(expense_id, {"name": "Intento Operador"}, actor=self.operator)

        # 2. Admin actualiza campos exitosamente
        updated = update_expense(
            expense_id,
            {"name": "Gasto Actualizado", "amount": "150.00", "category": "LOGISTICA"},
            actor=self.admin,
        )
        self.assertEqual(updated["name"], "Gasto Actualizado")
        self.assertEqual(updated["amount"], "150.00")
        self.assertEqual(updated["category"], "LOGISTICA")

        # 3. H-01: Proteger gasto de apertura (device_id == 'initial_import')
        opening_exp = Expense.objects.create(
            name="Gasto Apertura Etiquetas",
            amount=Decimal("3250.00"),
            occurred_at=timezone.now(),
            device_id="initial_import",
            created_by=self.admin,
        )
        with self.assertRaises(OpeningExpenseProtectedError) as ctx:
            update_expense(opening_exp.id, {"name": "Modificado"}, actor=self.admin)
        self.assertIn("no se permite", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertTrue(isinstance(ctx.exception, BusinessConflictError))

    def test_service_delete_expense_snapshot_audit_h03_and_opening_protection_h01(self):
        """Verifica eliminación con snapshot de auditoría (H-03) y protección H-01."""
        from catalog.services import delete_expense, register_single_expense

        exp_data = register_single_expense(
            {"name": "Gasto para Borrar", "amount": "300.00", "category": "MANTENIMIENTO"},
            actor=self.admin,
        )
        expense_id = exp_data["id"]

        # 1. Operador no puede eliminar -> PermissionDeniedError
        with self.assertRaises(PermissionDeniedError):
            delete_expense(expense_id, actor=self.operator)

        # 2. Admin elimina con snapshot auditado (H-03)
        res_del = delete_expense(expense_id, actor=self.admin)
        self.assertEqual(res_del["status"], "DELETED")
        self.assertEqual(res_del["deleted_expense_id"], expense_id)
        snapshot = res_del["audit_snapshot"]
        self.assertEqual(snapshot["name"], "Gasto para Borrar")
        self.assertEqual(snapshot["amount"], "300.00")
        self.assertEqual(snapshot["category"], "MANTENIMIENTO")
        self.assertIn("deleted_at", snapshot)
        self.assertEqual(snapshot["deleted_by"], "admin_exp")
        self.assertFalse(Expense.objects.filter(id=expense_id).exists())

        # 3. H-01: Gasto con device_id == 'initial_import' no puede eliminarse
        opening_exp = Expense.objects.create(
            name="Gasto Apertura Banda",
            amount=Decimal("375.00"),
            occurred_at=timezone.now(),
            device_id="initial_import",
            created_by=self.admin,
        )
        with self.assertRaises(OpeningExpenseProtectedError):
            delete_expense(opening_exp.id, actor=self.admin)
        self.assertTrue(Expense.objects.filter(id=opening_exp.id).exists())

    def test_service_monthly_dashboard_and_h04_daily_average(self):
        """Verifica sumas, conteos, porcentajes y regla H-04 para daily_average."""
        import calendar
        import datetime
        from catalog.services import get_monthly_expenses_dashboard, CDMX_TZ

        now_cdmx = timezone.now().astimezone(CDMX_TZ)
        curr_year = now_cdmx.year
        curr_month = now_cdmx.month
        curr_month_str = f"{curr_year:04d}-{curr_month:02d}"

        # Crear 2 gastos en el mes actual en CDMX
        dt_current = datetime.datetime(curr_year, curr_month, min(15, now_cdmx.day), 12, 0, 0, tzinfo=CDMX_TZ)
        dt_current_utc = dt_current.astimezone(datetime.timezone.utc)

        Expense.objects.create(
            name="Insumo A",
            amount=Decimal("600.00"),
            category="INSUMOS",
            occurred_at=dt_current_utc,
            created_by=self.admin,
        )
        Expense.objects.create(
            name="Servicio B",
            amount=Decimal("400.00"),
            category="SERVICIOS",
            occurred_at=dt_current_utc,
            created_by=self.operator,
        )

        # 1. Consultar mes actual: divisor = max(1, now_cdmx.day) según H-04
        dash_curr = get_monthly_expenses_dashboard(month_str=curr_month_str, actor=self.admin)
        self.assertEqual(dash_curr["month"], curr_month_str)
        self.assertEqual(dash_curr["total_amount"], "1000.00")
        self.assertEqual(dash_curr["expense_count"], 2)

        expected_curr_divisor = max(1, now_cdmx.day)
        expected_curr_avg = (Decimal("1000.00") / Decimal(str(expected_curr_divisor))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.assertEqual(dash_curr["daily_average"], str(expected_curr_avg))

        # Categorías y porcentajes
        self.assertEqual(len(dash_curr["categories"]), 2)
        cat_insumos = next(c for c in dash_curr["categories"] if c["category"] == "INSUMOS")
        cat_servicios = next(c for c in dash_curr["categories"] if c["category"] == "SERVICIOS")
        self.assertEqual(cat_insumos["total_amount"], "600.00")
        self.assertEqual(cat_insumos["percentage"], "60.00")
        self.assertEqual(cat_servicios["total_amount"], "400.00")
        self.assertEqual(cat_servicios["percentage"], "40.00")

        # 2. Filtrar por categoría
        dash_filtered = get_monthly_expenses_dashboard(
            month_str=curr_month_str, category_filter="INSUMOS", actor=self.operator
        )
        self.assertEqual(dash_filtered["total_amount"], "600.00")
        self.assertEqual(dash_filtered["expense_count"], 1)
        self.assertEqual(len(dash_filtered["items"]), 1)
        self.assertEqual(dash_filtered["items"][0]["category"], "INSUMOS")

        # 3. Consultar mes pasado (ej: 2026-01): divisor = 31 (días totales del mes) según H-04
        past_dt = datetime.datetime(2026, 1, 10, 10, 0, 0, tzinfo=CDMX_TZ).astimezone(datetime.timezone.utc)
        Expense.objects.create(
            name="Gasto Pasado Enero",
            amount=Decimal("3100.00"),
            category="RENTA",
            occurred_at=past_dt,
            created_by=self.admin,
        )
        dash_past = get_monthly_expenses_dashboard(month_str="2026-01", actor=self.admin)
        self.assertEqual(dash_past["total_amount"], "3100.00")
        # Divisor enero = 31 -> 3100 / 31 = 100.00
        self.assertEqual(dash_past["daily_average"], "100.00")

    def test_recurring_expenses_propagation_h06(self):
        """Verifica que la generación de gastos recurrentes propague la categoría (H-06)."""
        import datetime
        from catalog.services import generate_recurring_expenses

        tmpl = ExpenseTemplate.objects.create(
            name="Licencia Software",
            category="TECNOLOGIA",
            amount=Decimal("1500.00"),
            expense_type=ExpenseType.RECURRING_MONTHLY,
            start_date=datetime.date(2026, 1, 1),
            active=True,
            created_by=self.admin,
        )

        # Generar hasta febrero 2026 (2 ocurrencias: enero y febrero)
        generated = generate_recurring_expenses(target_date=datetime.date(2026, 2, 1), actor=self.admin)
        self.assertEqual(len(generated), 2)
        for g in generated:
            self.assertEqual(g.category, "TECNOLOGIA")  # H-06
            self.assertEqual(g.amount, Decimal("1500.00"))
            self.assertEqual(g.template, tmpl)

        # Reejecución no duplica
        second_run = generate_recurring_expenses(target_date=datetime.date(2026, 2, 1), actor=self.admin)
        self.assertEqual(len(second_run), 0)

    def test_api_expense_list_create_and_dashboard_views(self):
        """Verifica endpoints GET y POST /api/v1/expenses con CSRF e Idempotency-Key."""
        import json

        # 1. No autenticado -> 401
        res_unauth = self.client.get("/api/v1/expenses")
        self.assertEqual(res_unauth.status_code, 401)

        # 2. Operador autenticado consulta dashboard vacío -> 200
        self.login("op_exp", "Op-password-123!")
        res_get = self.client.get("/api/v1/expenses")
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.json()["expense_count"], 0)

        # 3. Operador crea gasto único exitosamente -> 201
        token = self.get_csrf()
        key1 = str(uuid.uuid4())
        post_data = {
            "name": "Compra de Hielo",
            "amount": "250.00",
            "category": "INSUMOS",
        }
        res_post = self.client.post(
            "/api/v1/expenses",
            data=json.dumps(post_data),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key1,
        )
        self.assertEqual(res_post.status_code, 201)
        exp_id = res_post.json()["id"]
        self.assertEqual(res_post.json()["name"], "Compra de Hielo")
        self.assertEqual(res_post.json()["amount"], "250.00")

        # 4. Idempotencia: Misma key y payload -> devuelve 201 en caché sin duplicar
        res_cached = self.client.post(
            "/api/v1/expenses",
            data=json.dumps(post_data),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key1,
        )
        self.assertEqual(res_cached.status_code, 201)
        self.assertEqual(res_cached.json()["id"], exp_id)
        self.assertEqual(Expense.objects.count(), 1)

        # 5. Idempotencia: Misma key con payload distinto -> 409
        diff_data = dict(post_data, amount="999.00")
        res_reused = self.client.post(
            "/api/v1/expenses",
            data=json.dumps(diff_data),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_IDEMPOTENCY_KEY=key1,
        )
        self.assertEqual(res_reused.status_code, 409)

    def test_api_expense_detail_patch_and_delete_rbac_and_protection(self):
        """Verifica PATCH y DELETE /api/v1/expenses/<id>: RBAC, initial_import (H-01) y audit (H-03)."""
        import json

        # Crear gasto normal y gasto de apertura
        exp_normal = Expense.objects.create(
            name="Mantenimiento Molino",
            amount=Decimal("1200.00"),
            category="MANTENIMIENTO",
            occurred_at=timezone.now(),
            created_by=self.admin,
        )
        exp_opening = Expense.objects.create(
            name="Gasto de Apertura Notarial",
            amount=Decimal("8539.00"),
            category="OTROS",
            occurred_at=timezone.now(),
            device_id="initial_import",
            created_by=self.admin,
        )

        # 1. Operador intenta PATCH -> 403 Forbidden
        self.login("op_exp", "Op-password-123!")
        token_op = self.get_csrf()
        key_op = str(uuid.uuid4())
        res_op_patch = self.client.patch(
            f"/api/v1/expenses/{exp_normal.id}",
            data=json.dumps({"name": "Modificado por Op"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token_op,
            HTTP_IDEMPOTENCY_KEY=key_op,
        )
        self.assertEqual(res_op_patch.status_code, 403)

        # 2. Operador intenta DELETE -> 403 Forbidden
        key_op2 = str(uuid.uuid4())
        res_op_del = self.client.delete(
            f"/api/v1/expenses/{exp_normal.id}",
            HTTP_X_CSRFTOKEN=token_op,
            HTTP_IDEMPOTENCY_KEY=key_op2,
        )
        self.assertEqual(res_op_del.status_code, 403)

        # 3. Admin autenticado
        self.login("admin_exp", "Admin-password-123!")
        token_admin = self.get_csrf()

        # 4. H-01: Admin intenta PATCH sobre gasto initial_import -> 403 / 422
        key_adm1 = str(uuid.uuid4())
        res_prot_patch = self.client.patch(
            f"/api/v1/expenses/{exp_opening.id}",
            data=json.dumps({"amount": "9000.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token_admin,
            HTTP_IDEMPOTENCY_KEY=key_adm1,
        )
        self.assertIn(res_prot_patch.status_code, [403, 422])

        # 5. H-01: Admin intenta DELETE sobre gasto initial_import -> 403 / 422
        key_adm2 = str(uuid.uuid4())
        res_prot_del = self.client.delete(
            f"/api/v1/expenses/{exp_opening.id}",
            HTTP_X_CSRFTOKEN=token_admin,
            HTTP_IDEMPOTENCY_KEY=key_adm2,
        )
        self.assertIn(res_prot_del.status_code, [403, 422])
        self.assertTrue(Expense.objects.filter(id=exp_opening.id).exists())

        # 6. Admin realiza PATCH exitoso sobre gasto normal -> 200
        key_patch_ok = str(uuid.uuid4())
        res_patch_ok = self.client.patch(
            f"/api/v1/expenses/{exp_normal.id}",
            data=json.dumps({"name": "Mantenimiento Preventivo Molino", "amount": "1350.00"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token_admin,
            HTTP_IDEMPOTENCY_KEY=key_patch_ok,
        )
        self.assertEqual(res_patch_ok.status_code, 200)
        self.assertEqual(res_patch_ok.json()["name"], "Mantenimiento Preventivo Molino")
        self.assertEqual(res_patch_ok.json()["amount"], "1350.00")

        # 7. Admin realiza DELETE exitoso sobre gasto normal con snapshot (H-03) -> 200
        key_del_ok = str(uuid.uuid4())
        res_del_ok = self.client.delete(
            f"/api/v1/expenses/{exp_normal.id}",
            HTTP_X_CSRFTOKEN=token_admin,
            HTTP_IDEMPOTENCY_KEY=key_del_ok,
        )
        self.assertEqual(res_del_ok.status_code, 200)
        self.assertEqual(res_del_ok.json()["status"], "DELETED")
        self.assertEqual(res_del_ok.json()["deleted_expense_id"], exp_normal.id)
        self.assertIn("audit_snapshot", res_del_ok.json())
        self.assertEqual(res_del_ok.json()["audit_snapshot"]["name"], "Mantenimiento Preventivo Molino")
        self.assertFalse(Expense.objects.filter(id=exp_normal.id).exists())







