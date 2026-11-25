"""
catalog/models.py

P-06 — Dominio de catálogo, recetas, inventario, ventas, devoluciones y gastos.

Fuente de verdad: docs/plan/03-modelo-de-datos.md y 09-pseudocodigo.md.
Las reglas de negocio se documentan en 02-reglas-de-negocio-y-metricas.md.

Decisiones de implementación:
  • Cantidades y costos: DecimalField(max_digits=18, decimal_places=6) para
    precisión suficiente sin coma flotante binaria.
  • Importes monetarios MXN: DecimalField(max_digits=14, decimal_places=2).
  • Zonas horarias: solo se guarda UTC; la zona fija UTC-06:00 se aplica al
    presentar periodos al usuario (nunca DST).
  • UUIDs: como clave primaria donde se requiere idempotencia (eventos/sync).
  • Nombres en español para mantener coherencia con el dominio del negocio.

No se implementa en este commit: Device, DeviceQuota, SyncEvent (P-07),
ni la caché SQLite de Windows (P-05 avanzado).
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone

# ---------------------------------------------------------------------------
# Tipos compartidos
# ---------------------------------------------------------------------------

AMOUNT = dict(max_digits=14, decimal_places=2)       # MXN con 2 decimales
QUANTITY = dict(max_digits=18, decimal_places=6)     # cantidades con 6 decimales
COST = dict(max_digits=18, decimal_places=6)         # costos unitarios con 6 decimales

User = get_user_model


# ---------------------------------------------------------------------------
# 1. Catálogo de componentes (insumos inventariables)
# ---------------------------------------------------------------------------

class ComponentType(models.TextChoices):
    LIQUID = "LIQUID", "Líquido"
    CONTAINER = "CONTAINER", "Envase"
    CLOSURE = "CLOSURE", "Cierre/Corcho"
    OTHER = "OTHER", "Otro"


class Component(models.Model):
    """
    Insumo controlado en el inventario (líquido, botella, corcho, etc.).

    Regla: unidad base y precisión son inmutables tras el primer movimiento.
    Regla: el costo promedio se recalcula en cada entrada confirmada.
    """
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200, unique=True)
    sku = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    component_type = models.CharField(
        max_length=20,
        choices=ComponentType.choices,
        default=ComponentType.OTHER,
    )
    # Unidad base de medición (ej: "L", "pza"). Fija desde el primer movimiento.
    base_unit = models.CharField(max_length=20)
    # Número de decimales permitidos en cantidades (0 = piezas enteras, 3 = fraccionables).
    unit_precision = models.PositiveSmallIntegerField(default=0)

    # Costo promedio ponderado móvil actual (recalculado en cada entrada).
    # Null indica que no hay saldo con costo conocido.
    average_cost = models.DecimalField(null=True, blank=True, **COST)

    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "componente"
        verbose_name_plural = "componentes"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# 2. Productos (artículos vendibles: Valhalla 750, Mini 375)
# ---------------------------------------------------------------------------

class Product(models.Model):
    """
    Producto vendible. No mantiene existencia de producto terminado:
    el surtido consume los componentes de la receta activa.

    Regla: se activa automáticamente al tener tanto RecipeVersion activa
    como PriceVersion activa. No se desactiva al versionar después.
    """
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200, unique=True)
    sku = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    # Unidad base de venta (ej: "pza").
    base_unit = models.CharField(max_length=20, default="pza")
    active = models.BooleanField(default=False)
    # Subinventario de botellas terminadas listas para venta
    bottle_stock = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.000000")
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "producto"
        verbose_name_plural = "productos"

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# 3. Recetas y versiones de receta
# ---------------------------------------------------------------------------

class RecipeVersion(models.Model):
    """
    Versión de receta: fija los componentes y cantidades para un producto.

    Regla: una versión con movimientos no se edita retroactivamente.
    El surtido y la devolución referencian la versión congelada en la venta.
    Solo puede haber una versión activa por producto a la vez.
    """
    id = models.AutoField(primary_key=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="recipe_versions"
    )
    version_number = models.PositiveIntegerField()
    active = models.BooleanField(default=False)
    effective_from = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("product", "version_number")]
        ordering = ["product", "-version_number"]
        verbose_name = "versión de receta"
        verbose_name_plural = "versiones de receta"

    def __str__(self) -> str:
        return f"{self.product.name} v{self.version_number}"


class RecipeComponent(models.Model):
    """
    Componente individual dentro de una versión de receta.

    Regla: Valhalla 750 consume 0.750 L Ensamble, 1 botella 750, 1 corcho sintético.
           Mini 375 consume 0.375 L 1 Dest., 1 botella 375, 1 corcho madera.
    """
    id = models.AutoField(primary_key=True)
    recipe_version = models.ForeignKey(
        RecipeVersion, on_delete=models.PROTECT, related_name="components"
    )
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="used_in_recipes"
    )
    # Cantidad que se consume por unidad de producto vendida.
    quantity = models.DecimalField(**QUANTITY)

    class Meta:
        unique_together = [("recipe_version", "component")]
        verbose_name = "componente de receta"
        verbose_name_plural = "componentes de receta"

    def __str__(self) -> str:
        return f"{self.recipe_version} → {self.component.name} × {self.quantity}"


# ---------------------------------------------------------------------------
# 4. Versiones de precio
# ---------------------------------------------------------------------------

class PriceVersion(models.Model):
    """
    Versión de precio por producto. El precio incluye IVA.
    Solo una versión puede estar activa por producto.

    Regla: importe_final × 16 / 116 = IVA desglosado (métrica interna, no fiscal).
    """
    id = models.AutoField(primary_key=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="price_versions"
    )
    version_number = models.PositiveIntegerField()
    # Precio final con IVA incluido.
    price_with_tax = models.DecimalField(**AMOUNT)
    active = models.BooleanField(default=False)
    effective_from = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("product", "version_number")]
        ordering = ["product", "-version_number"]
        verbose_name = "versión de precio"
        verbose_name_plural = "versiones de precio"

    def __str__(self) -> str:
        return f"{self.product.name} v{self.version_number} ${self.price_with_tax}"


# ---------------------------------------------------------------------------
# 5. Movimientos de inventario
# ---------------------------------------------------------------------------

class MovementType(models.TextChoices):
    OPENING = "OPENING", "Saldo inicial oficial"
    ENTRY = "ENTRY", "Entrada"
    SALE_CONSUME = "SALE_CONSUME", "Consumo por venta"
    RETURN_REENTRY = "RETURN_REENTRY", "Reingreso por devolución"
    ADJUSTMENT_POS = "ADJUSTMENT_POS", "Ajuste positivo"
    ADJUSTMENT_NEG = "ADJUSTMENT_NEG", "Ajuste negativo"


class MovementStatus(models.TextChoices):
    PROVISIONAL = "PROVISIONAL", "Provisional (no conciliado)"
    CONFIRMED = "CONFIRMED", "Confirmado"


class InventoryMovement(models.Model):
    """
    Movimiento de inventario de un componente. Inmutable una vez confirmado.

    Regla: saldo = saldo_inicial_oficial + entradas + reingresos + ajustes_pos
                   - consumos_venta - ajustes_neg
    Regla: promedio ponderado se recalcula solo en entradas/ajustes positivos.
    Regla: movimientos provisionales quedan excluidos del saldo oficial.
    """
    id = models.AutoField(primary_key=True)
    # UUID de evento para idempotencia (trazabilidad a outbox Android o Idempotency-Key Windows).
    event_id = models.UUIDField(unique=True, default=uuid.uuid4)
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="movements"
    )
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    # Delta de cantidad: positivo = entrada, negativo = salida.
    quantity_delta = models.DecimalField(**QUANTITY)
    # Costo unitario aplicado en este movimiento (None para consumos por venta).
    unit_cost = models.DecimalField(null=True, blank=True, **COST)
    # Costo promedio del componente justo antes de este movimiento (snapshot para auditoría).
    average_cost_before = models.DecimalField(null=True, blank=True, **COST)
    # Costo promedio resultante después de aplicar este movimiento.
    average_cost_after = models.DecimalField(null=True, blank=True, **COST)
    status = models.CharField(
        max_length=20,
        choices=MovementStatus.choices,
        default=MovementStatus.CONFIRMED,
    )
    # Fecha en que ocurrió la operación (negocio, UTC).
    occurred_at = models.DateTimeField()
    # Fecha en que se recibió/registró en el sistema.
    received_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")
    # Tipo de documento de referencia (ej: "sale", "return", "purchase_receipt").
    reference_type = models.CharField(max_length=50, blank=True, default="")
    reference_id = models.CharField(max_length=100, blank=True, default="")
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Identificador del dispositivo Android que originó el movimiento (None = Windows/admin).
    device_id = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["occurred_at", "id"]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"
        indexes = [
            models.Index(fields=["component", "occurred_at"]),
            models.Index(fields=["event_id"]),
            models.Index(fields=["reference_type", "reference_id"]),
        ]

    def __str__(self) -> str:
        sign = "+" if self.quantity_delta >= 0 else ""
        return (
            f"{self.component.name} "
            f"{sign}{self.quantity_delta} {self.component.base_unit} "
            f"[{self.movement_type}]"
        )


# ---------------------------------------------------------------------------
# 6. Ventas
# ---------------------------------------------------------------------------

class SaleStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente (local, no reserva stock)"
    FULFILLED = "FULFILLED", "Surtida"
    CANCELLED = "CANCELLED", "Cancelada"


class Sale(models.Model):
    """
    Venta independiente por producto. No hay ticket conjunto.

    Regla: una venta pendiente no reserva stock ni aparece en reportes.
    Regla: al surtir, se congelan precio, receta y costos de los componentes.
    """
    id = models.AutoField(primary_key=True)
    event_id = models.UUIDField(unique=True, default=uuid.uuid4)
    status = models.CharField(
        max_length=20, choices=SaleStatus.choices, default=SaleStatus.PENDING
    )
    # Fecha en que se tomó el pedido.
    requested_at = models.DateTimeField(default=timezone.now)
    # Fecha en que se surtió (None si aún está pendiente).
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    device_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "venta"
        verbose_name_plural = "ventas"

    def __str__(self) -> str:
        return f"Venta #{self.id} [{self.status}]"


class SaleLine(models.Model):
    """
    Partida de venta: un producto, su precio y receta congelados.

    Regla: cada venta tiene exactamente una SaleLine (un producto por venta).
    Regla: al surtir, se congelan recipe_version, price_version, unit_cost_applied.
    """
    id = models.AutoField(primary_key=True)
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="line")
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="sale_lines"
    )
    quantity = models.DecimalField(**QUANTITY)
    # Versión de receta congelada al surtir (None hasta el surtido).
    recipe_version = models.ForeignKey(
        RecipeVersion,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Versión de precio congelada al surtir.
    price_version = models.ForeignKey(
        PriceVersion,
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    # Precio final con IVA al surtir (congelado).
    unit_price_with_tax = models.DecimalField(null=True, blank=True, **AMOUNT)
    # Descuento aplicado (0 si no hay).
    discount = models.DecimalField(default=Decimal("0.00"), **AMOUNT)
    # Importe final = unit_price_with_tax × quantity - discount.
    final_amount = models.DecimalField(null=True, blank=True, **AMOUNT)
    # IVA desglosado = final_amount × 16 / 116 (métrica interna).
    tax_amount = models.DecimalField(null=True, blank=True, **AMOUNT)
    # Costo total de los componentes al momento del surtido.
    cost_applied = models.DecimalField(null=True, blank=True, **AMOUNT)
    # Indica si la venta se surtió desde el subinventario de botellas terminadas
    from_subinventory = models.BooleanField(default=False)

    class Meta:
        verbose_name = "línea de venta"
        verbose_name_plural = "líneas de venta"


# ---------------------------------------------------------------------------
# 7. Devoluciones
# ---------------------------------------------------------------------------

class ReturnStatus(models.TextChoices):
    PENDING = "PENDING", "Pendiente"
    COMPLETED = "COMPLETED", "Completada"
    REJECTED = "REJECTED", "Rechazada"


class Return(models.Model):
    """
    Devolución vinculada a una venta. Reingresa componentes con costo original.

    Regla: exige recepción completa de todos los componentes controlados de
           la receta congelada en la venta.
    Regla: reembolso acumulado no supera el pago original de la SaleLine.
    """
    id = models.AutoField(primary_key=True)
    event_id = models.UUIDField(unique=True, default=uuid.uuid4)
    sale_line = models.ForeignKey(
        SaleLine, on_delete=models.PROTECT, related_name="returns"
    )
    status = models.CharField(
        max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.PENDING
    )
    # Cantidad de producto devuelta en esta devolución (null solo en registros legacy previos a migración 0005).
    quantity = models.DecimalField(null=True, blank=True, **QUANTITY)
    # Fecha real de recepción de los componentes.
    returned_at = models.DateTimeField(null=True, blank=True)
    # Reembolso aprobado (≤ final_amount de la SaleLine menos reembolsos previos).
    refund_amount = models.DecimalField(null=True, blank=True, **AMOUNT)
    tax_reversed = models.DecimalField(null=True, blank=True, **AMOUNT)
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    device_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "devolución"
        verbose_name_plural = "devoluciones"


class ReturnLine(models.Model):
    """
    Partida de devolución por componente recibido.
    """
    id = models.AutoField(primary_key=True)
    ret = models.ForeignKey(Return, on_delete=models.PROTECT, related_name="lines")
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="return_lines"
    )
    quantity_returned = models.DecimalField(**QUANTITY)
    # Costo original del componente al surtir (congelado de la venta).
    unit_cost_original = models.DecimalField(**COST)

    class Meta:
        unique_together = [("ret", "component")]
        verbose_name = "línea de devolución"
        verbose_name_plural = "líneas de devolución"


# ---------------------------------------------------------------------------
# 8. Gastos generales
# ---------------------------------------------------------------------------

class ExpenseType(models.TextChoices):
    ONE_TIME = "ONE_TIME", "Único"
    RECURRING_MONTHLY = "RECURRING_MONTHLY", "Recurrente mensual"
    RECURRING_ANNUAL = "RECURRING_ANNUAL", "Recurrente anual"


class ExpenseTemplate(models.Model):
    """
    Plantilla de gasto recurrente. Solo ADMIN puede crearla/editarla.
    Editar la plantilla actualiza ocurrencias históricas con auditoría.
    """
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200)
    expense_type = models.CharField(
        max_length=25,
        choices=ExpenseType.choices,
        default=ExpenseType.ONE_TIME,
    )
    amount = models.DecimalField(**AMOUNT)
    # Fecha de inicio de la recurrencia (primera ocurrencia).
    start_date = models.DateField(null=True, blank=True)
    # Fecha de fin opcional.
    end_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "plantilla de gasto"
        verbose_name_plural = "plantillas de gasto"

    def __str__(self) -> str:
        return f"{self.name} ({self.expense_type})"


class Expense(models.Model):
    """
    Ocurrencia individual de gasto. No se asigna ni prorratean por producto.

    Regla: OPERADOR puede registrar gastos únicos.
           ADMIN gestiona plantillas recurrentes.
    Regla: cambios a ocurrencias históricas (vía template) quedan auditados.
    """
    id = models.AutoField(primary_key=True)
    event_id = models.UUIDField(unique=True, default=uuid.uuid4)
    name = models.CharField(max_length=200)
    amount = models.DecimalField(**AMOUNT)
    expense_type = models.CharField(
        max_length=25,
        choices=ExpenseType.choices,
        default=ExpenseType.ONE_TIME,
    )
    # Fecha en que ocurrió el gasto (negocio, UTC).
    occurred_at = models.DateTimeField()
    # Referencia a la plantilla si es recurrente.
    template = models.ForeignKey(
        ExpenseTemplate,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences",
    )
    created_by = models.ForeignKey(
        get_user_model(),
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    device_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name = "gasto"
        verbose_name_plural = "gastos"
        indexes = [
            models.Index(fields=["occurred_at"]),
            models.Index(fields=["event_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ${self.amount} [{self.occurred_at.date()}]"


# ---------------------------------------------------------------------------
# 9. Importación de saldos iniciales (P-09, referenciada aquí para FK)
# ---------------------------------------------------------------------------

class ImportBatchStatus(models.TextChoices):
    PROVISIONAL = "PROVISIONAL", "Provisional"
    CONFIRMED = "CONFIRMED", "Confirmado (doble confirmación ADMIN)"
    CORRECTED = "CORRECTED", "Corregido (con movimientos compensatorios)"
    REVERSED = "REVERSED", "Revertido"


class ImportBatch(models.Model):
    """
    Lote de importación de saldos iniciales conciliados.

    Regla: requiere doble confirmación de ADMIN en la misma sesión.
    Regla: el import_id es idempotente — mismo id con datos distintos se rechaza.
    Regla: los provisionales no se usan para vender ni reportes oficiales.
    """
    import_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    status = models.CharField(
        max_length=20,
        choices=ImportBatchStatus.choices,
        default=ImportBatchStatus.PROVISIONAL,
    )
    preview_version = models.PositiveIntegerField(default=1)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    raw_data = models.JSONField(default=dict, encoder=DjangoJSONEncoder)

    # Primera confirmación administrativa
    first_confirmation_id = models.UUIDField(null=True, blank=True)
    first_confirmation_at = models.DateTimeField(null=True, blank=True)
    first_confirmation_session = models.CharField(max_length=128, blank=True, default="")
    first_confirmation_user = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # Segunda confirmación administrativa (oficializa)
    second_confirmation_id = models.UUIDField(null=True, blank=True)
    second_confirmation_at = models.DateTimeField(null=True, blank=True)
    second_confirmation_session = models.CharField(max_length=128, blank=True, default="")
    second_confirmation_user = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # Corrección auditada (P-09 §9)
    correction_id = models.UUIDField(null=True, blank=True)
    correction_payload_hash = models.CharField(max_length=64, blank=True, default="")
    correction_data = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)

    confirmed_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "lote de importación"
        verbose_name_plural = "lotes de importación"


# ---------------------------------------------------------------------------
# 10. Idempotencia de operaciones mutantes (ventas y devoluciones)
# ---------------------------------------------------------------------------

class CatalogIdempotency(models.Model):
    """Registro de idempotencia para operaciones mutantes del módulo catalog."""

    id = models.AutoField(primary_key=True)
    scope = models.CharField(max_length=64)
    actor_user_id = models.CharField(max_length=64)
    idempotency_key = models.UUIDField(unique=True)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField(default=201)
    response_body = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "registro de idempotencia"
        verbose_name_plural = "registros de idempotencia"
        indexes = [
            models.Index(fields=["scope", "idempotency_key"]),
        ]

    def __str__(self) -> str:
        return f"Idempotency [{self.scope}] {self.idempotency_key}"
