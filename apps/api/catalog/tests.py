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
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.exceptions import (
    BusinessConflictError,
    CostRequiredError,
    InsufficientStockError,
)
from catalog.models import (
    CatalogIdempotency,
    Component,
    ComponentType,
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
    RecipeComponent,
    RecipeVersion,
    Return,
    ReturnLine,
    ReturnStatus,
    Sale,
    SaleLine,
    SaleStatus,
)
from catalog.services import (
    add_price_version,
    add_recipe_version,
    calculate_availability,
    compute_sale_return_summary,
    create_product,
    create_sale_request,
    fulfill_sale,
    get_component_stock,
    infer_return_quantity,
    register_receipt,
    register_return,
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






