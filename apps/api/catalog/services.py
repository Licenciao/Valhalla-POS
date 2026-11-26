"""
catalog/services.py

Capa de servicios y transacciones del dominio de inventario.
Implementa las especificaciones de 09-pseudocodigo.md §§4, 5, 6 y 11-borrador-contrato-api.md.

Garantías arquitectónicas:
  • Transacciones ACID estrictas con transaction.atomic().
  • Control de concurrencia mediante select_for_update() con ordenamiento fijo
    de IDs para prevenir interbloqueos (deadlocks).
  • Precisión decimal fija con ROUND_HALF_UP.
  • Idempotencia por event_id.
  • Separación estricta entre saldos oficiales y movimientos provisionales.
"""

import calendar
import datetime
import hashlib
import json
import logging
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

try:
    from zoneinfo import ZoneInfo
    CDMX_TZ = ZoneInfo("America/Mexico_City")
except Exception:
    CDMX_TZ = datetime.timezone(datetime.timedelta(hours=-6))

logger = logging.getLogger(__name__)

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models, transaction
from django.db.models import Prefetch, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from system.constants import ADMIN_ROLE, OPERATOR_ROLE
from system.permissions import get_user_role
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
    Component,
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

User = get_user_model()

# Precisión decimal para redondeo monetario e impuesto (2 decimales)
TWO_PLACES = Decimal("0.01")
# Precisión decimal para cantidades y costos (6 decimales)
SIX_PLACES = Decimal("0.000001")
ZERO_AMT = Decimal("0.00")
ZERO_QTY = Decimal("0.000000")
ONE_UNIT = Decimal("1.000000")


def round_currency(value: Decimal) -> Decimal:
    """Redondea importe monetario a 2 decimales usando mitad hacia arriba."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_quantity(value: Decimal, precision: int = 6) -> Decimal:
    """Redondea cantidad o costo a la precisión decimal indicada."""
    if precision == 0:
        return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    places = Decimal("10") ** -precision
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _to_finite_decimal(value: Any, field_name: str) -> Decimal:
    """Convierte y valida que el valor sea un Decimal finito (no booleano, NaN o infinito)."""
    if isinstance(value, bool):
        raise DomainValidationError(f"El campo '{field_name}' debe ser numérico.")
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainValidationError(f"El campo '{field_name}' debe ser numérico.") from exc
    if not dec.is_finite():
        raise DomainValidationError(f"El campo '{field_name}' debe ser un número finito.")
    return dec


# ---------------------------------------------------------------------------
# Consultas de existencias
# ---------------------------------------------------------------------------

def get_component_stock(component_id: int) -> Decimal:
    """
    Calcula la existencia central oficial confirmada de un componente.
    Los movimientos provisionales están estrictamente excluidos.
    """
    total = (
        InventoryMovement.objects.filter(
            component_id=component_id,
            status=MovementStatus.CONFIRMED,
        ).aggregate(total=models.Sum("quantity_delta"))["total"]
        or Decimal("0.000000")
    )
    return total


def calculate_moving_average(
    current_qty: Decimal,
    current_avg: Decimal | None,
    incoming_qty: Decimal,
    incoming_cost: Decimal,
) -> Decimal:
    """
    NUEVO_PROMEDIO(cantidad_actual, promedio_actual, cantidad_entrada, costo_entrada)
    según 09-pseudocodigo.md §5.
    """
    if current_qty <= Decimal("0.000000"):
        return round_quantity(incoming_cost, 6)

    if current_avg is None:
        raise CostRequiredError(
            "Existe saldo positivo previo pero se carece de promedio conocido. "
            "Se requiere valoración administrativa."
        )

    numerator = (current_qty * current_avg) + (incoming_qty * incoming_cost)
    denominator = current_qty + incoming_qty
    if denominator == Decimal("0"):
        return round_quantity(incoming_cost, 6)

    return round_quantity(numerator / denominator, 6)


# ---------------------------------------------------------------------------
# Entradas de inventario (Receipts)
# ---------------------------------------------------------------------------

def register_receipt(
    component_id: int,
    quantity: Decimal,
    unit_cost: Decimal,
    reference_type: str = "",
    reference_id: str = "",
    reason: str = "",
    actor: Any | None = None,
    occurred_at: Any | None = None,
    event_id: uuid.UUID | None = None,
) -> InventoryMovement:
    """
    REGISTRAR_ENTRADA según 09-pseudocodigo.md §5.
    Ejecuta en una transacción atómica central.
    """
    if quantity <= Decimal("0"):
        raise DomainValidationError("La cantidad recibida debe ser mayor a cero.")
    if unit_cost < Decimal("0"):
        raise DomainValidationError("El costo unitario no puede ser negativo.")

    evt_id = event_id or uuid.uuid4()
    occ_at = occurred_at or timezone.now()

    with transaction.atomic():
        # Verificación de idempotencia
        existing = InventoryMovement.objects.filter(event_id=evt_id).first()
        if existing is not None:
            return existing

        component = (
            Component.objects.select_for_update().filter(id=component_id).first()
        )
        if component is None:
            raise ResourceNotFoundError(f"Componente {component_id} no existe.")

        current_stock = get_component_stock(component.id)
        avg_before = component.average_cost

        # Manejo de déficit previo (ventas con saldo negativo aceptadas por excepción)
        quantity_for_deficit = min(quantity, max(Decimal("0"), -current_stock))
        remaining = quantity - quantity_for_deficit

        if remaining > Decimal("0"):
            positive_prev = max(Decimal("0"), current_stock)
            if positive_prev > Decimal("0") and avg_before is None:
                raise CostRequiredError(
                    "El componente tiene saldo positivo previo sin costo promedio conocido. "
                    "Se requiere valoración administrativa antes de registrar nuevas entradas."
                )
            if positive_prev == Decimal("0"):
                new_avg = round_quantity(unit_cost, 6)
            else:
                new_avg = calculate_moving_average(
                    positive_prev, avg_before, remaining, unit_cost
                )
            component.average_cost = new_avg
            component.save(update_fields=["average_cost", "updated_at"])
            avg_after = new_avg
        else:
            avg_after = avg_before

        movement = InventoryMovement.objects.create(
            event_id=evt_id,
            component=component,
            movement_type=MovementType.ENTRY,
            quantity_delta=quantity,
            unit_cost=round_quantity(unit_cost, 6),
            average_cost_before=avg_before,
            average_cost_after=avg_after,
            status=MovementStatus.CONFIRMED,
            occurred_at=occ_at,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            created_by=actor,
        )
        return movement


# ---------------------------------------------------------------------------
# Catálogo, Recetas y Precios
# ---------------------------------------------------------------------------

def create_product(
    name: str,
    sku: str = "",
    description: str = "",
    base_unit: str = "pza",
    actor: Any | None = None,
) -> Product:
    """
    CREAR_PRODUCTO según 09-pseudocodigo.md §4.
    Crea el producto en estado inactivo (active=False).
    """
    if not name or not name.strip():
        raise DomainValidationError("El nombre del producto es obligatorio.")

    product = Product.objects.create(
        name=name.strip(),
        sku=sku.strip(),
        description=description.strip(),
        base_unit=base_unit,
        active=False,
    )
    return product


def verify_product_auto_activation(product: Product) -> bool:
    """
    Activa automáticamente el producto si cuenta con al menos una
    RecipeVersion activa y una PriceVersion activa.
    """
    has_active_recipe = product.recipe_versions.filter(active=True).exists()
    has_active_price = product.price_versions.filter(active=True).exists()

    if has_active_recipe and has_active_price and not product.active:
        product.active = True
        product.save(update_fields=["active", "updated_at"])
        return True
    return product.active


def add_recipe_version(
    product_id: int,
    components_data: list[dict[str, Any]],
    actor: Any | None = None,
    effective_from: Any | None = None,
) -> RecipeVersion:
    """
    Crea una nueva versión de receta para un producto.
    components_data: [{'component_id': int, 'quantity': Decimal}, ...]
    """
    if not components_data:
        raise DomainValidationError("La receta debe incluir al menos un componente.")

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        # Obtener siguiente número de versión
        last_version = (
            product.recipe_versions.order_by("-version_number").first()
        )
        next_ver = 1 if last_version is None else last_version.version_number + 1

        # Desactivar versión anterior activa
        product.recipe_versions.filter(active=True).update(active=False)

        recipe = RecipeVersion.objects.create(
            product=product,
            version_number=next_ver,
            active=True,
            effective_from=effective_from or timezone.now(),
            created_by=actor,
        )

        for item in components_data:
            c_id = item["component_id"]
            qty = Decimal(str(item["quantity"]))
            if qty <= Decimal("0"):
                raise DomainValidationError("La cantidad del componente debe ser positiva.")
            comp = Component.objects.filter(id=c_id).first()
            if comp is None:
                raise ResourceNotFoundError(f"Componente {c_id} no existe.")

            RecipeComponent.objects.create(
                recipe_version=recipe,
                component=comp,
                quantity=round_quantity(qty, comp.unit_precision),
            )

        verify_product_auto_activation(product)
        return recipe


def add_price_version(
    product_id: int,
    price_with_tax: Decimal,
    actor: Any | None = None,
    effective_from: Any | None = None,
) -> PriceVersion:
    """
    Crea una nueva versión de precio vigente (con IVA incluido) para un producto.
    """
    if price_with_tax < Decimal("0"):
        raise DomainValidationError("El precio no puede ser negativo.")

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        last_price = (
            product.price_versions.order_by("-version_number").first()
        )
        next_ver = 1 if last_price is None else last_price.version_number + 1

        product.price_versions.filter(active=True).update(active=False)

        price = PriceVersion.objects.create(
            product=product,
            version_number=next_ver,
            price_with_tax=round_currency(price_with_tax),
            active=True,
            effective_from=effective_from or timezone.now(),
            created_by=actor,
        )

        verify_product_auto_activation(product)
        return price


def calculate_availability(product_id: int) -> int:
    """
    DISPONIBLE(producto, fuente) según 09-pseudocodigo.md §4.
    Calcula unidades completas que pueden surtirse a partir del stock central no asignado.
    """
    product = Product.objects.filter(id=product_id, active=True).first()
    if product is None:
        return 0

    recipe = product.recipe_versions.filter(active=True).first()
    if recipe is None:
        return 0

    components = recipe.components.select_related("component").all()
    if not components:
        return 0

    limit = None
    for rc in components:
        stock = get_component_stock(rc.component_id)
        if stock <= Decimal("0"):
            return 0
        possible = int(stock // rc.quantity)
        if limit is None or possible < limit:
            limit = possible

    return max(0, limit or 0)


# ---------------------------------------------------------------------------
# Ventas y Surtido Atómico
# ---------------------------------------------------------------------------

def create_sale_request(
    product_id: int,
    quantity: Decimal,
    unit_price: Decimal | None = None,
    discount: Decimal = Decimal("0.00"),
    actor: Any | None = None,
    device_id: str = "",
    event_id: uuid.UUID | None = None,
) -> Sale:
    """
    CREAR_SOLICITUD_VENTA según 09-pseudocodigo.md §5.
    Crea la venta en estado PENDING sin reservar stock ni afectar reportes.
    """
    if quantity <= Decimal("0"):
        raise DomainValidationError("La cantidad de venta debe ser positiva.")

    product = Product.objects.filter(id=product_id, active=True).first()
    if product is None:
        raise DomainValidationError("El producto no existe o no está activo.")

    # Snapshot de precio vigente si no se ingresó manual
    if unit_price is None:
        price_ver = product.price_versions.filter(active=True).first()
        if price_ver is None:
            raise BusinessConflictError("El producto no tiene una versión de precio activa.")
        price_snapshot = price_ver.price_with_tax
    else:
        price_snapshot = round_currency(unit_price)

    if price_snapshot < Decimal("0"):
        raise DomainValidationError("El precio no puede ser negativo.")

    gross = round_currency(quantity * price_snapshot)
    disc = round_currency(discount)
    if disc < Decimal("0") or disc > gross:
        raise DomainValidationError("El descuento debe estar entre 0 y el importe bruto.")

    sale = Sale.objects.create(
        event_id=event_id or uuid.uuid4(),
        status=SaleStatus.PENDING,
        created_by=actor,
        device_id=device_id,
    )
    SaleLine.objects.create(
        sale=sale,
        product=product,
        quantity=quantity,
        unit_price_with_tax=price_snapshot,
        discount=disc,
        final_amount=gross - disc,
    )
    return sale


def add_bottles_to_product(
    product_id: int,
    quantity: Decimal,
    actor: Any | None = None,
    device_id: str = "",
) -> dict:
    """
    EMBOTELLADO / AGREGAR BOTELLA AL SUBINVENTARIO:
    Declara la producción/incorporación de botellas terminadas, descontando
    inmediatamente las materias primas del stock central según la receta activa.
    """
    if quantity <= Decimal("0"):
        raise DomainValidationError("La cantidad de botellas a agregar debe ser mayor a cero.")

    with transaction.atomic():
        product = (
            Product.objects.select_for_update()
            .filter(id=product_id)
            .first()
        )
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no encontrado.")

        recipe = (
            product.recipe_versions.filter(active=True)
            .prefetch_related("components__component")
            .first()
        )
        if recipe is None:
            raise BusinessConflictError(f"El producto '{product.name}' no tiene una receta activa configurada.")

        recipe_components = list(recipe.components.all())
        recipe_components.sort(key=lambda rc: rc.component_id)

        component_ids = [rc.component_id for rc in recipe_components]
        locked_components = {
            c.id: c
            for c in Component.objects.select_for_update().filter(id__in=component_ids)
        }

        # 1. Verificar stock disponible de materias primas
        for rc in recipe_components:
            comp = locked_components[rc.component_id]
            req_qty = rc.quantity * quantity
            current_stock = get_component_stock(comp.id)
            if current_stock < req_qty:
                raise InsufficientStockError(
                    f"Materia prima insuficiente para '{comp.name}': "
                    f"disponible {current_stock} {comp.base_unit}, requerido {req_qty} {comp.base_unit} "
                    f"para embotellar {quantity} botellas."
                )

        # 2. Descontar materias primas mediante InventoryMovement
        now = timezone.now()
        consumed_info = []
        for rc in recipe_components:
            comp = locked_components[rc.component_id]
            req_qty = rc.quantity * quantity
            cost = comp.average_cost or Decimal("0.000000")
            current_stock = get_component_stock(comp.id)

            InventoryMovement.objects.create(
                event_id=uuid.uuid4(),
                component=comp,
                movement_type=MovementType.SALE_CONSUME,
                quantity_delta=-req_qty,
                unit_cost=cost,
                average_cost_before=comp.average_cost,
                average_cost_after=comp.average_cost,
                status=MovementStatus.CONFIRMED,
                occurred_at=now,
                reference_type="bottling",
                reference_id=str(product.id),
                created_by=actor,
                device_id=device_id or "windows-main",
            )
            consumed_info.append({
                "component_id": comp.id,
                "component_name": comp.name,
                "sku": comp.sku,
                "quantity_consumed": str(req_qty),
                "unit": comp.base_unit,
                "remaining_stock": str(current_stock - req_qty),
            })

        # 3. Incrementar subinventario de botellas terminadas
        product.bottle_stock = (product.bottle_stock or Decimal("0.000000")) + quantity
        product.save(update_fields=["bottle_stock"])

        return {
            "product_id": product.id,
            "product_name": product.name,
            "bottles_added": str(quantity),
            "new_bottle_stock": str(product.bottle_stock),
            "consumed_components": consumed_info,
        }


def fulfill_sale(
    sale_id: int,
    actor: Any | None = None,
    device_id: str = "",
) -> Sale:
    """
    SURTIR_VENTA según 09-pseudocodigo.md §5.
    Operación transaccional con select_for_update para control de concurrencia.
    Deduce stock central, genera movimientos, congela receta, precio y costo.
    """
    with transaction.atomic():
        sale = (
            Sale.objects.select_for_update()
            .filter(id=sale_id)
            .select_related("line__product")
            .first()
        )
        if sale is None:
            raise ResourceNotFoundError(f"Venta {sale_id} no encontrada.")

        if sale.status != SaleStatus.PENDING:
            raise BusinessConflictError(
                f"La venta {sale_id} no está pendiente (estado actual: {sale.status})."
            )

        line = sale.line
        product = line.product

        # Receta activa para el surtido
        recipe = product.recipe_versions.filter(active=True).prefetch_related("components__component").first()
        if recipe is None:
            raise BusinessConflictError("El producto no tiene una receta activa configurada.")

        # Versión de precio activa
        price_ver = product.price_versions.filter(active=True).first()

        recipe_components = list(recipe.components.all())
        # Ordenar componentes por ID para prevenir deadlocks
        recipe_components.sort(key=lambda rc: rc.component_id)

        # ── Flujo Subinventario de Botellas Terminadas ─────────────────────────
        # Si la venta proviene del subinventario o el producto cuenta con botellas terminadas:
        use_subinventory = (
            getattr(line, "from_subinventory", False)
            or (product.bottle_stock is not None and product.bottle_stock >= line.quantity)
        )

        if use_subinventory:
            if product.bottle_stock is None or product.bottle_stock < line.quantity:
                raise InsufficientStockError(
                    f"Subinventario insuficiente para {product.name}: "
                    f"disponibles {product.bottle_stock or 0} botellas terminadas, solicitadas {line.quantity}."
                )

            # Descontar del subinventario de botellas terminadas (la materia prima ya se descontó al embotellar)
            product.bottle_stock -= line.quantity
            product.save(update_fields=["bottle_stock"])
            line.from_subinventory = True

            total_cogs = Decimal("0.00")
            for rc in recipe_components:
                comp = rc.component
                cost = comp.average_cost or Decimal("0.000000")
                total_cogs += round_currency(rc.quantity * line.quantity * cost)

            final_amt = line.final_amount or round_currency(
                (line.quantity * (line.unit_price_with_tax or Decimal("0.00"))) - line.discount
            )
            tax_included = round_currency(final_amt * Decimal("16") / Decimal("116"))

            line.recipe_version = recipe
            line.price_version = price_ver
            line.cost_applied = total_cogs
            line.tax_amount = tax_included
            line.final_amount = final_amt
            line.save()

            sale.status = SaleStatus.FULFILLED
            sale.fulfilled_at = timezone.now()
            sale.save()
            return sale

        # ── Flujo Directo de Consumo de Componentes (Sin Subinventario) ───────
        # 1. Bloqueo y verificación de stock y costos conocidos
        component_ids = [rc.component_id for rc in recipe_components]
        locked_components = {
            c.id: c
            for c in Component.objects.select_for_update().filter(id__in=component_ids)
        }

        consumptions = {}
        total_cogs = Decimal("0.00")

        for rc in recipe_components:
            comp = locked_components[rc.component_id]
            req_qty = rc.quantity * line.quantity
            consumptions[comp.id] = req_qty

            stock = get_component_stock(comp.id)
            if stock < req_qty:
                raise InsufficientStockError(
                    f"Stock insuficiente para {comp.name}: requerido {req_qty}, disponible {stock}."
                )

            if comp.average_cost is None:
                raise CostRequiredError(
                    f"El componente {comp.name} no tiene costo promedio conocido. "
                    "Se requiere valoración administrativa antes de surtir."
                )

            total_cogs += round_currency(req_qty * comp.average_cost)

        # 2. Descuento de stock y creación de movimientos
        now = timezone.now()
        for rc in recipe_components:
            comp = locked_components[rc.component_id]
            req_qty = consumptions[comp.id]

            InventoryMovement.objects.create(
                event_id=uuid.uuid4(),
                component=comp,
                movement_type=MovementType.SALE_CONSUME,
                quantity_delta=-req_qty,
                unit_cost=comp.average_cost,
                average_cost_before=comp.average_cost,
                average_cost_after=comp.average_cost,
                status=MovementStatus.CONFIRMED,
                occurred_at=now,
                reference_type="sale",
                reference_id=str(sale.id),
                created_by=actor,
                device_id=device_id,
            )

        # 3. Congelación de la línea de venta y métricas
        final_amt = line.final_amount or round_currency(
            (line.quantity * (line.unit_price_with_tax or Decimal("0.00"))) - line.discount
        )
        tax_included = round_currency(final_amt * Decimal("16") / Decimal("116"))

        line.recipe_version = recipe
        line.price_version = price_ver
        line.cost_applied = total_cogs
        line.tax_amount = tax_included
        line.final_amount = final_amt
        line.save()

        sale.status = SaleStatus.FULFILLED
        sale.fulfilled_at = now
        sale.save(update_fields=["status", "fulfilled_at"])

        return sale


# ---------------------------------------------------------------------------
# Devoluciones (Returns)
# ---------------------------------------------------------------------------

def infer_return_quantity(ret: Return, line: SaleLine) -> Decimal:
    """
    Obtiene la cantidad de producto devuelta en una Return.
    Usa `.all()` en memoria para respetar `prefetch_related` y evitar consultas N+1.
    """
    if ret.quantity is not None:
        return max(ZERO_QTY, round_quantity(ret.quantity, 6))

    # Estrategia 1 (fallback legacy): ReturnLine.quantity_returned / RecipeComponent.quantity
    if line.recipe_version is not None:
        rc_map = {
            rc.component_id: rc.quantity
            for rc in line.recipe_version.components.all()
        }
        if rc_map:
            candidates = []
            for rl in ret.lines.all():
                rc_qty = rc_map.get(rl.component_id)
                if rc_qty is None or rc_qty <= ZERO_QTY:
                    continue
                if rl.quantity_returned is None or rl.quantity_returned <= ZERO_QTY:
                    continue
                priority = 0 if rc_qty == ONE_UNIT else 1
                candidates.append((priority, rl.component_id, rl.quantity_returned, rc_qty))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                _, _, qty_returned, rc_qty = candidates[0]
                inferred = round_quantity(qty_returned / rc_qty, 6)
                if inferred > ZERO_QTY:
                    return inferred

    # Estrategia 2 (fallback): proporción monetaria
    if (
        ret.refund_amount is not None
        and ret.refund_amount > ZERO_AMT
        and line.final_amount is not None
        and line.final_amount > ZERO_AMT
        and line.quantity is not None
        and line.quantity > ZERO_QTY
    ):
        inferred = round_quantity((ret.refund_amount / line.final_amount) * line.quantity, 6)
        if inferred > ZERO_QTY:
            return inferred

    return ZERO_QTY


def compute_sale_return_summary(line: SaleLine) -> dict[str, Any]:
    """
    Calcula métricas netas y acumuladas de devoluciones completadas de una SaleLine.
    Usa `.all()` en memoria sobre relaciones prefetcheadas para evitar consultas N+1.
    """
    original_quantity = round_quantity(line.quantity or ZERO_QTY, 6)
    original_discount = round_currency(line.discount or ZERO_AMT)

    if line.final_amount is not None:
        original_final_amount = round_currency(line.final_amount)
    else:
        gross = round_currency(
            original_quantity * (line.unit_price_with_tax or ZERO_AMT)
        )
        original_final_amount = max(ZERO_AMT, round_currency(gross - original_discount))

    if line.tax_amount is not None:
        original_tax_amount = round_currency(line.tax_amount)
    else:
        original_tax_amount = round_currency(
            original_final_amount * Decimal("16") / Decimal("116")
        )

    returned_quantity = ZERO_QTY
    already_refunded = ZERO_AMT
    already_tax_reversed = ZERO_AMT

    for ret in line.returns.all():
        if ret.status != ReturnStatus.COMPLETED:
            continue
        returned_quantity += infer_return_quantity(ret, line)
        already_refunded += round_currency(ret.refund_amount or ZERO_AMT)
        already_tax_reversed += round_currency(ret.tax_reversed or ZERO_AMT)

    returned_quantity = round_quantity(returned_quantity, 6)
    already_refunded = round_currency(already_refunded)
    already_tax_reversed = round_currency(already_tax_reversed)

    remaining_quantity = max(
        ZERO_QTY, round_quantity(original_quantity - returned_quantity, 6)
    )
    net_final_amount = max(
        ZERO_AMT, round_currency(original_final_amount - already_refunded)
    )
    net_tax_amount = max(
        ZERO_AMT, round_currency(original_tax_amount - already_tax_reversed)
    )

    is_fully_returned = original_quantity > ZERO_QTY and remaining_quantity <= ZERO_QTY
    is_partially_returned = ZERO_QTY < returned_quantity < original_quantity

    if is_fully_returned:
        return_status = "FULL"
        net_discount = ZERO_AMT
        max_refund_possible = ZERO_AMT
    elif is_partially_returned:
        return_status = "PARTIAL"
        net_discount = (
            round_currency((original_discount * remaining_quantity) / original_quantity)
            if original_quantity > ZERO_QTY
            else ZERO_AMT
        )
        max_refund_possible = net_final_amount
    else:
        return_status = "NONE"
        net_discount = original_discount
        max_refund_possible = net_final_amount

    return {
        "original_quantity": original_quantity,
        "returned_quantity": returned_quantity,
        "remaining_quantity": remaining_quantity,
        "original_discount": original_discount,
        "net_discount": net_discount,
        "original_final_amount": original_final_amount,
        "already_refunded": already_refunded,
        "net_final_amount": net_final_amount,
        "original_tax_amount": original_tax_amount,
        "already_tax_reversed": already_tax_reversed,
        "net_tax_amount": net_tax_amount,
        "max_refund_possible": max_refund_possible,
        "is_partially_returned": is_partially_returned,
        "is_fully_returned": is_fully_returned,
        "return_status": return_status,
    }


def compute_sale_allows_fractional(
    line: SaleLine,
    summary: dict[str, Any],
    recipe_components: list[RecipeComponent] | None = None,
) -> bool:
    """
    Indica si la línea admite cantidades fraccionables:
    - Todos los componentes de la receta congelada tienen unit_precision > 0 (y hay al menos uno), o
    - La venta original o su saldo remanente ya contiene fracción decimal.
    Usa `.all()` en memoria para respetar `prefetch_related` y evitar N+1.
    """
    if recipe_components is not None:
        rc_list = list(recipe_components)
    elif line.recipe_version is not None:
        rc_list = list(line.recipe_version.components.all())
    else:
        rc_list = []

    all_components_fractional = bool(rc_list) and all(
        (rc.component.unit_precision or 0) > 0 for rc in rc_list
    )
    has_decimal_history = (
        ((line.quantity or ZERO_QTY) % Decimal("1") != ZERO_QTY)
        or (summary["remaining_quantity"] % Decimal("1") != ZERO_QTY)
    )
    return bool(all_components_fractional or has_decimal_history)


def register_return(
    sale_id: int,
    quantity_to_return: Decimal,
    refund_amount: Decimal | None = None,
    actor: Any | None = None,
    device_id: str = "",
    event_id: uuid.UUID | None = None,
    to_subinventory: bool | None = None,
    reason: str = "",
    source: str = ReturnSource.MANUAL_RETURN,
    previous_remaining_quantity: Decimal | None = None,
    target_remaining_quantity: Decimal | None = None,
) -> Return:
    """
    REGISTRAR_DEVOLUCION:
    Reingresa directamente al conteo de botellas rellenas (subinventario) para su reventa,
    o en modo legacy reingresa insumos a granel si no se originó de subinventario.
    """
    if not isinstance(quantity_to_return, Decimal):
        quantity_to_return = Decimal(str(quantity_to_return))
    if not quantity_to_return.is_finite() or quantity_to_return <= ZERO_QTY:
        raise DomainValidationError("La cantidad a devolver debe ser mayor a cero.")
    qty_to_return = round_quantity(quantity_to_return, 6)
    if qty_to_return <= ZERO_QTY:
        raise DomainValidationError("La cantidad a devolver debe ser mayor a cero.")

    clean_reason = (reason or "").strip()

    with transaction.atomic():
        # Bloqueo sin outer join para compatibilidad estricta con PostgreSQL FOR UPDATE.
        sale = Sale.objects.select_for_update().filter(id=sale_id).first()
        if sale is None:
            raise ResourceNotFoundError(f"Venta {sale_id} no encontrada.")

        if sale.status != SaleStatus.FULFILLED:
            raise BusinessConflictError("Solo pueden devolverse ventas previamente surtidas.")

        line = (
            SaleLine.objects.select_for_update()
            .select_related("product")
            .filter(sale_id=sale.id)
            .first()
        )
        if line is None:
            raise BusinessConflictError("La venta no tiene una línea de venta válida.")

        frozen_recipe = line.recipe_version
        if frozen_recipe is None:
            raise BusinessConflictError("La venta no tiene una receta congelada válida.")

        summary = compute_sale_return_summary(line)
        original_quantity = summary["original_quantity"]
        previous_returned_qty = summary["returned_quantity"]
        remaining_qty = summary["remaining_quantity"]
        original_final_amount = summary["original_final_amount"]
        previous_returns_sum = summary["already_refunded"]
        max_refund_possible = summary["max_refund_possible"]
        max_tax_reversible = summary["net_tax_amount"]

        if remaining_qty <= ZERO_QTY:
            raise BusinessConflictError("Esta venta ya ha sido devuelta en su totalidad.")

        if round_quantity(previous_returned_qty + qty_to_return, 6) > original_quantity:
            raise BusinessConflictError(
                f"La cantidad solicitada ({qty_to_return}) supera las piezas disponibles por devolver ({remaining_qty})."
            )

        is_final_qty = round_quantity(previous_returned_qty + qty_to_return, 6) >= original_quantity
        proportional_refund = (
            round_currency((original_final_amount * qty_to_return) / original_quantity)
            if original_quantity > ZERO_QTY
            else ZERO_AMT
        )

        if refund_amount is None:
            if is_final_qty:
                if (
                    previous_returns_sum == ZERO_AMT
                    or abs(max_refund_possible - proportional_refund) <= Decimal("0.05")
                    or proportional_refund > max_refund_possible
                ):
                    actual_refund = max_refund_possible
                else:
                    actual_refund = proportional_refund
            else:
                actual_refund = min(proportional_refund, max_refund_possible)
        else:
            if not isinstance(refund_amount, Decimal):
                refund_amount = Decimal(str(refund_amount))
            actual_refund = round_currency(refund_amount)
            if actual_refund < ZERO_AMT:
                raise DomainValidationError("El monto de reembolso no puede ser negativo.")
            if actual_refund > max_refund_possible:
                raise BusinessConflictError(
                    f"El reembolso solicitado (${actual_refund}) supera el remanente disponible (${max_refund_possible})."
                )

        if is_final_qty and actual_refund == max_refund_possible:
            tax_reversed = max_tax_reversible
        else:
            tax_reversed = min(
                max_tax_reversible,
                round_currency(actual_refund * Decimal("16") / Decimal("116")),
            )

        now = timezone.now()

        ret = Return.objects.create(
            event_id=event_id or uuid.uuid4(),
            sale_line=line,
            status=ReturnStatus.COMPLETED,
            quantity=qty_to_return,
            returned_at=now,
            refund_amount=actual_refund,
            tax_reversed=tax_reversed,
            reason=clean_reason,
            source=source or ReturnSource.MANUAL_RETURN,
            previous_remaining_quantity=previous_remaining_quantity,
            target_remaining_quantity=target_remaining_quantity,
            created_by=actor,
            device_id=device_id,
        )

        # Determinar si la devolución reingresa al subinventario de botellas terminadas
        is_subinventory_return = (
            to_subinventory is True
            or getattr(line, "from_subinventory", False)
            or (to_subinventory is None and (line.product.bottle_stock is not None and line.product.bottle_stock > ZERO_QTY))
        )

        recipe_components = frozen_recipe.components.select_related("component").all()

        if is_subinventory_return:
            # Reingreso directo al subinventario de botellas terminadas listas para venta
            prod = Product.objects.select_for_update().get(pk=line.product_id)
            prod.bottle_stock = (prod.bottle_stock or ZERO_QTY) + qty_to_return
            prod.save(update_fields=["bottle_stock"])
            line.product = prod

        for rc in recipe_components:
            comp = rc.component
            qty_reenter = round_quantity(rc.quantity * qty_to_return, 6)
            cost_orig = comp.average_cost or ZERO_QTY

            ReturnLine.objects.create(
                ret=ret,
                component=comp,
                quantity_returned=qty_reenter,
                unit_cost_original=cost_orig,
            )

            if not is_subinventory_return:
                InventoryMovement.objects.create(
                    event_id=uuid.uuid4(),
                    component=comp,
                    movement_type=MovementType.RETURN_REENTRY,
                    quantity_delta=qty_reenter,
                    unit_cost=cost_orig,
                    average_cost_before=comp.average_cost,
                    average_cost_after=comp.average_cost,
                    status=MovementStatus.CONFIRMED,
                    occurred_at=now,
                    reason=clean_reason,
                    reference_type="return",
                    reference_id=str(ret.id),
                    created_by=actor,
                    device_id=device_id,
                )

        return ret


def adjust_sale_quantity_from_excel(
    sale_id: int,
    target_quantity: Decimal,
    expected_remaining_quantity: Decimal | None = None,
    reason: str = "",
    to_subinventory: bool | None = None,
    refund_amount: Decimal | None = None,
    actor: Any | None = None,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """
    Ajuste administrativo de cantidad remanente desde la tabla tipo Excel (solo ADMIN).
    Reduce la cantidad neta remanente hacia `target_quantity` (0 <= target < current_remaining)
    registrando una devolución auditada sin mutar retroactivamente la venta original.
    """
    if actor is None or get_user_role(actor) != ADMIN_ROLE:
        raise PermissionDeniedError(
            "Solo un usuario con rol ADMIN puede ajustar cantidades desde la tabla Excel."
        )

    raw_target = _to_finite_decimal(target_quantity, "target_quantity")
    if raw_target < ZERO_QTY:
        raise DomainValidationError("La cantidad objetivo no puede ser negativa.")

    expected_qty: Decimal | None = None
    if expected_remaining_quantity is not None:
        expected_dec = _to_finite_decimal(
            expected_remaining_quantity, "expected_remaining_quantity"
        )
        if expected_dec < ZERO_QTY:
            raise DomainValidationError("La cantidad esperada no puede ser negativa.")
        expected_qty = round_quantity(expected_dec, 6)

    normalized_refund: Decimal | None = None
    if refund_amount is not None:
        refund_dec = _to_finite_decimal(refund_amount, "refund_amount")
        if refund_dec < ZERO_AMT:
            raise DomainValidationError("El monto de reembolso no puede ser negativo.")
        normalized_refund = round_currency(refund_dec)

    clean_reason = (reason or "").strip()

    with transaction.atomic():
        # Bloqueo sin outer joins para compatibilidad estricta con PostgreSQL FOR UPDATE.
        sale = Sale.objects.select_for_update().filter(id=sale_id).first()
        if sale is None:
            raise ResourceNotFoundError(f"Venta {sale_id} no encontrada.")

        if sale.status != SaleStatus.FULFILLED:
            raise BusinessConflictError(
                "Solo pueden ajustarse ventas previamente surtidas."
            )

        line = SaleLine.objects.select_for_update().filter(sale_id=sale.id).first()
        if line is None:
            raise BusinessConflictError("La venta no tiene una línea de venta válida.")
        if line.recipe_version_id is None:
            raise BusinessConflictError("La venta no tiene una receta congelada válida.")

        summary_before = compute_sale_return_summary(line)
        current_remaining = summary_before["remaining_quantity"]

        if expected_qty is not None and expected_qty != current_remaining:
            raise BusinessConflictError(
                f"La cantidad remanente actual ({current_remaining}) cambió respecto a la esperada ({expected_qty})."
            )

        if current_remaining <= ZERO_QTY or raw_target >= current_remaining:
            raise BusinessConflictError(
                "La cantidad objetivo debe ser menor a la cantidad remanente actual."
            )

        rc_list = list(
            RecipeComponent.objects.select_related("component")
            .filter(recipe_version_id=line.recipe_version_id)
            .order_by("component_id")
        )
        allow_fractional = compute_sale_allows_fractional(
            line, summary_before, recipe_components=rc_list
        )
        if not allow_fractional and raw_target != raw_target.to_integral_value():
            raise DomainValidationError(
                "Este producto se vende en piezas enteras; ingrese una cantidad entera."
            )
        if allow_fractional and round_quantity(raw_target, 3) != raw_target:
            raise DomainValidationError(
                "Los productos fraccionables admiten máximo 3 decimales."
            )

        target_qty = round_quantity(raw_target, 6)

        if to_subinventory is not None and bool(to_subinventory) != bool(line.from_subinventory):
            raise BusinessConflictError(
                "El destino físico de la devolución debe coincidir con el origen de surtido de la venta."
            )
        effective_to_subinventory = bool(line.from_subinventory)

        if effective_to_subinventory:
            Product.objects.select_for_update().get(pk=line.product_id)
        else:
            component_ids = sorted({rc.component_id for rc in rc_list})
            locked_components = list(
                Component.objects.select_for_update()
                .filter(id__in=component_ids)
                .order_by("id")
            )
            for comp in locked_components:
                if comp.average_cost is None and get_component_stock(comp.id) > ZERO_QTY:
                    raise CostRequiredError(
                        f"El componente {comp.name} no tiene costo promedio conocido. "
                        "Se requiere valoración administrativa antes de ajustar inventario."
                    )

        delta = round_quantity(current_remaining - target_qty, 6)
        if clean_reason:
            effective_reason = clean_reason
        elif target_qty == ZERO_QTY:
            effective_reason = (
                f"Eliminación de venta desde tabla Excel (ADMIN): {current_remaining} -> 0"
            )
        else:
            effective_reason = (
                f"Ajuste de cantidad desde tabla Excel (ADMIN): {current_remaining} -> {target_qty}"
            )

        ret = register_return(
            sale_id=sale.id,
            quantity_to_return=delta,
            refund_amount=normalized_refund,
            actor=actor,
            device_id="windows-excel-admin",
            event_id=event_id,
            to_subinventory=effective_to_subinventory,
            reason=effective_reason,
            source=ReturnSource.EXCEL_ADMIN_ADJUSTMENT,
            previous_remaining_quantity=current_remaining,
            target_remaining_quantity=target_qty,
        )

        updated_sale = (
            Sale.objects.select_related(
                "line__product",
                "line__recipe_version",
                "line__price_version",
            )
            .prefetch_related(
                "line__recipe_version__components__component",
                "line__returns__lines",
            )
            .get(pk=sale.id)
        )
        summary_after = compute_sale_return_summary(updated_sale.line)

        return {
            "sale": updated_sale,
            "return": ret,
            "summary_before": summary_before,
            "summary_after": summary_after,
            "return_id": ret.id,
            "previous_remaining_quantity": current_remaining,
            "target_quantity": target_qty,
            "delta_reversed": delta,
            "removed_from_history": target_qty == ZERO_QTY,
            "refund_amount": ret.refund_amount,
            "tax_reversed": ret.tax_reversed,
            "returned_to_subinventory": effective_to_subinventory,
            "new_bottle_stock": updated_sale.line.product.bottle_stock or ZERO_QTY,
            "reason": ret.reason,
            "source": ret.source,
            "adjustment_device_id": ret.device_id,
            "occurred_at": ret.returned_at,
        }


# ---------------------------------------------------------------------------
# P-09: Importación Inicial Conciliada y Doble Confirmación Administrativa
# ---------------------------------------------------------------------------

def _normalize_dict_for_json(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _normalize_dict_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_normalize_dict_for_json(v) for v in data]
    elif isinstance(data, Decimal):
        return str(data)
    elif isinstance(data, uuid.UUID):
        return str(data)
    return data


def compute_payload_hash(data: dict) -> str:
    """Calcula un hash SHA-256 canónico y determinista del payload recibido."""
    normalized = _normalize_dict_for_json(data)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_initial_import(
    import_id: uuid.UUID,
    quantities: list[dict],
    opening_expenses: list[dict] | None = None,
    actor: Any | None = None,
) -> ImportBatch:
    """
    CARGAR_SALDO_INICIAL_PROVISIONAL según 09-pseudocodigo.md §9 y CA-28.
    Carga cantidades como PROVISIONALES (excluidas de disponibilidad y ventas).
    Garantiza idempotencia por import_id y hash del payload.
    """
    payload_dict = _normalize_dict_for_json({
        "quantities": quantities,
        "opening_expenses": opening_expenses or [],
    })
    payload_hash = compute_payload_hash(payload_dict)

    existing = ImportBatch.objects.filter(import_id=import_id).first()
    if existing:
        if existing.payload_hash == payload_hash:
            return existing
        raise IdempotencyKeyReusedError(
            "El import_id ya fue utilizado previamente con un payload diferente."
        )

    # Validar que todos los componentes existan y las cantidades sean positivas
    for item in quantities:
        comp_id = item.get("component_id")
        if not comp_id or not Component.objects.filter(id=comp_id).exists():
            raise ResourceNotFoundError(f"Componente #{comp_id} no existe en catálogo.")
        qty = Decimal(str(item.get("quantity", "0")))
        if qty <= Decimal("0"):
            raise DomainValidationError("La cantidad del saldo inicial debe ser mayor a cero.")

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            import_id=import_id,
            status=ImportBatchStatus.PROVISIONAL,
            preview_version=1,
            payload_hash=payload_hash,
            raw_data=payload_dict,
            notes="Carga provisional de inventario inicial",
        )

        now = timezone.now()
        for item in quantities:
            comp = Component.objects.get(id=item["component_id"])
            ref_cost = (
                Decimal(str(item["reference_unit_cost"]))
                if item.get("reference_unit_cost") is not None
                else None
            )
            InventoryMovement.objects.create(
                event_id=uuid.uuid4(),
                component=comp,
                movement_type=MovementType.OPENING,
                quantity_delta=Decimal(str(item["quantity"])),
                unit_cost=ref_cost,
                status=MovementStatus.PROVISIONAL,
                occurred_at=now,
                reference_type="initial_import",
                reference_id=str(import_id),
                created_by=actor,
                reason="Saldo inicial provisional pendiente de conciliación",
            )

        return batch


def get_initial_import_preview(import_id: uuid.UUID) -> dict:
    """
    Obtiene la vista previa estructurada de un lote de importación inicial (CA-28).
    """
    batch = ImportBatch.objects.filter(import_id=import_id).first()
    if not batch:
        raise ResourceNotFoundError("Lote de importación inicial no encontrado.")

    raw = batch.raw_data or {}
    quantities = []
    for q in raw.get("quantities", []):
        comp = Component.objects.filter(id=q["component_id"]).first()
        quantities.append({
            "component_id": q["component_id"],
            "component_name": comp.name if comp else "",
            "quantity": str(q["quantity"]),
            "unit": q.get("unit") or (comp.base_unit if comp else ""),
            "reference_unit_cost": str(q["reference_unit_cost"]) if q.get("reference_unit_cost") is not None else None,
        })

    confirmations_received = 2 if batch.status == ImportBatchStatus.CONFIRMED else (1 if batch.first_confirmation_id else 0)
    warnings = []
    if batch.status == ImportBatchStatus.PROVISIONAL:
        warnings.append(
            "Las cantidades provisionales no están disponibles para venta ni se reflejan en "
            "reportes oficiales hasta su conciliación física y doble confirmación administrativa."
        )

    return {
        "import_id": str(batch.import_id),
        "preview_version": batch.preview_version,
        "status": batch.status,
        "quantities": quantities,
        "differences": [],
        "warnings": warnings,
        "opening_expenses": raw.get("opening_expenses", []),
        "confirmations_received": confirmations_received,
        "first_confirmation_user": batch.first_confirmation_user.username if batch.first_confirmation_user else None,
        "created_at": batch.created_at.isoformat(),
        "confirmed_at": batch.confirmed_at.isoformat() if batch.confirmed_at else None,
    }


def confirm_initial_import(
    import_id: uuid.UUID,
    confirmation_id: uuid.UUID,
    preview_version: int,
    session_key: str,
    actor: Any,
    physical_count: bool = True,
    verified_costs: bool = True,
) -> dict:
    """
    CONCILIAR_SALDO_INICIAL según 09-pseudocodigo.md §9, CA-28 y CA-31.
    Exige actor == ADMIN autenticado.
    Exige dos confirmaciones explícitas separadas de la misma cuenta y misma sesión.
    Al recibir la 2da confirmación, convierte provisional a oficial y lo hace disponible.
    """
    batch = ImportBatch.objects.filter(import_id=import_id).first()
    if not batch:
        raise ResourceNotFoundError("Lote de importación inicial no encontrado.")

    if batch.status == ImportBatchStatus.CONFIRMED:
        if confirmation_id in [batch.first_confirmation_id, batch.second_confirmation_id]:
            return {
                "import_id": str(batch.import_id),
                "confirmations_received": 2,
                "status": "CONFIRMED",
                "audit_ids": [str(batch.first_confirmation_id), str(batch.second_confirmation_id)],
            }
        raise BusinessConflictError("El lote de importación inicial ya ha sido confirmado y oficializado.")

    if batch.status != ImportBatchStatus.PROVISIONAL:
        raise BusinessConflictError(f"El lote de importación tiene estado inválido para confirmación: {batch.status}.")

    if not session_key:
        raise PermissionDeniedError("Se requiere una sesión activa válida para registrar la confirmación.")

    # 1. Primera confirmación
    if batch.first_confirmation_id is None:
        batch.first_confirmation_id = confirmation_id
        batch.first_confirmation_at = timezone.now()
        batch.first_confirmation_session = session_key
        batch.first_confirmation_user = actor
        batch.save(update_fields=[
            "first_confirmation_id",
            "first_confirmation_at",
            "first_confirmation_session",
            "first_confirmation_user",
        ])
        return {
            "import_id": str(batch.import_id),
            "confirmations_received": 1,
            "status": "PENDING_SECOND_CONFIRMATION",
            "message": "Primera confirmación registrada. Se requiere una segunda confirmación explícita de la misma cuenta administradora en la misma sesión para oficializar los saldos.",
        }

    # 2. Reintento idempotente de la primera confirmación
    if confirmation_id == batch.first_confirmation_id:
        return {
            "import_id": str(batch.import_id),
            "confirmations_received": 1,
            "status": "PENDING_SECOND_CONFIRMATION",
            "message": "Primera confirmación ya registrada. Se requiere una segunda confirmación explícita con un confirmation_id diferente.",
        }

    # 3. Validación estricta para la segunda confirmación (CA-28 / CA-31)
    if actor.pk != batch.first_confirmation_user_id:
        raise PermissionDeniedError(
            "La segunda confirmación debe provenir de la misma cuenta administradora autenticada que realizó la primera."
        )

    if session_key != batch.first_confirmation_session:
        raise PermissionDeniedError(
            "La segunda confirmación debe realizarse dentro de la misma sesión activa."
        )

    # 4. Oficialización atómica definitiva
    with transaction.atomic():
        now = timezone.now()
        batch.second_confirmation_id = confirmation_id
        batch.second_confirmation_at = now
        batch.second_confirmation_session = session_key
        batch.second_confirmation_user = actor
        batch.confirmed_by = actor
        batch.confirmed_at = now
        batch.status = ImportBatchStatus.CONFIRMED
        batch.save()

        # Oficializar movimientos provisionales y actualizar costos promedio
        provisional_movs = InventoryMovement.objects.filter(
            reference_type="initial_import",
            reference_id=str(batch.import_id),
            status=MovementStatus.PROVISIONAL,
        )

        for m in provisional_movs:
            m.status = MovementStatus.CONFIRMED
            m.movement_type = MovementType.OPENING
            m.occurred_at = now
            m.save(update_fields=["status", "movement_type", "occurred_at"])

            if m.unit_cost is not None and m.quantity_delta > 0:
                comp = m.component
                comp.average_cost = m.unit_cost
                comp.save(update_fields=["average_cost", "updated_at"])

        # Registrar gastos de apertura si están incluidos en el lote
        for exp in batch.raw_data.get("opening_expenses", []):
            Expense.objects.create(
                expense_type=ExpenseType.ONE_TIME,
                name=exp.get("name", "Gasto de apertura"),
                category=exp.get("category", "OTROS"),
                amount=round_currency(Decimal(str(exp["amount"]))),
                occurred_at=exp.get("occurred_at") or now,
                created_by=actor,
                device_id="initial_import",
            )

        return {
            "import_id": str(batch.import_id),
            "confirmations_received": 2,
            "status": "CONFIRMED",
            "audit_ids": [str(batch.first_confirmation_id), str(confirmation_id)],
        }


def correct_initial_import(
    import_id: uuid.UUID,
    correction_id: uuid.UUID,
    quantities: list[dict],
    opening_expenses: list[dict] | None = None,
    reason: str = "",
    actor: Any | None = None,
) -> dict:
    """
    CORREGIR_IMPORTACION según 09-pseudocodigo.md §9 y CA-28.
    Agrega movimientos compensatorios a fecha actual y preserva el promedio ponderado vigente.
    Si el saldo queda negativo, marca déficit provisional con costo pendiente.
    """
    batch = ImportBatch.objects.filter(import_id=import_id).first()
    if not batch:
        raise ResourceNotFoundError("Lote de importación inicial no encontrado.")

    if batch.status not in [ImportBatchStatus.CONFIRMED, ImportBatchStatus.CORRECTED]:
        raise BusinessConflictError(
            "Solo se puede corregir un lote de importación previamente oficializado (CONFIRMED)."
        )

    correction_dict = _normalize_dict_for_json({
        "quantities": quantities,
        "opening_expenses": opening_expenses or [],
        "reason": reason,
    })
    payload_hash = compute_payload_hash(correction_dict)

    if batch.correction_id == correction_id:
        if batch.correction_payload_hash == payload_hash:
            return {
                "import_id": str(batch.import_id),
                "correction_id": str(correction_id),
                "status": batch.status,
                "message": "Corrección ya registrada previamente de forma idéntica.",
            }
        raise IdempotencyKeyReusedError(
            "El correction_id ya fue utilizado previamente con un payload diferente."
        )

    with transaction.atomic():
        orig_map = {
            item["component_id"]: Decimal(str(item["quantity"]))
            for item in batch.raw_data.get("quantities", [])
        }

        compensating_movements = []
        pending_deficits = []
        now = timezone.now()

        for item in quantities:
            comp_id = item["component_id"]
            comp = Component.objects.filter(id=comp_id).first()
            if not comp:
                raise ResourceNotFoundError(f"Componente #{comp_id} no encontrado.")

            new_qty = Decimal(str(item["quantity"]))
            old_qty = orig_map.get(comp_id, Decimal("0.000000"))
            delta = new_qty - old_qty

            if delta != Decimal("0"):
                mtype = MovementType.ADJUSTMENT_POS if delta > 0 else MovementType.ADJUSTMENT_NEG
                current_avg = comp.average_cost

                # Preserva promedio vigente y snapshots (§9)
                mov = InventoryMovement.objects.create(
                    event_id=uuid.uuid4(),
                    component=comp,
                    movement_type=mtype,
                    quantity_delta=delta,
                    unit_cost=current_avg,
                    average_cost_before=current_avg,
                    average_cost_after=current_avg,
                    status=MovementStatus.CONFIRMED,
                    occurred_at=now,
                    reference_type="import_correction",
                    reference_id=str(correction_id),
                    created_by=actor,
                    reason=f"Corrección importación {import_id}: {reason}".strip(),
                )
                compensating_movements.append(str(mov.event_id))

                # Verificar si el saldo queda negativo tras el ajuste
                stock_now = get_component_stock(comp_id)
                if stock_now < Decimal("0"):
                    pending_deficits.append({
                        "component_id": comp_id,
                        "component_name": comp.name,
                        "deficit": str(stock_now),
                        "warning": "Saldo negativo provisional. Se cubrirá con entradas posteriores a costo de entrada.",
                    })

        batch.status = ImportBatchStatus.CORRECTED
        batch.correction_id = correction_id
        batch.correction_payload_hash = payload_hash
        batch.correction_data = correction_dict
        batch.save(update_fields=["status", "correction_id", "correction_payload_hash", "correction_data"])

        return {
            "import_id": str(batch.import_id),
            "correction_id": str(correction_id),
            "status": "CORRECTED",
            "compensating_movements": compensating_movements,
            "pending_deficits": pending_deficits,
        }


# ---------------------------------------------------------------------------
# CONTADOR: Costos de receta e impuestos personalizables por producto
# ---------------------------------------------------------------------------

MAX_PRODUCT_CUSTOM_TAXES = 3
MAX_PERCENT_TAX_RATE = Decimal("1000.0000")
MAX_FIXED_TAX_AMOUNT = Decimal("9999999.99")
CONTADOR_COST_BASIS_NOTE = (
    "Estimación interna calculada únicamente con costos promedio de la receta activa "
    "vigente e impuestos personalizables por producto. No sustituye declaraciones "
    "fiscales ni prorratea gastos generales."
)
_TAX_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _require_admin_actor(actor: Any | None) -> None:
    if actor is None or get_user_role(actor) != ADMIN_ROLE:
        raise PermissionDeniedError(
            "Solo un usuario con rol ADMIN puede administrar impuestos en CONTADOR."
        )


def normalize_tax_name(raw_name: Any) -> tuple[str, str]:
    """
    Normaliza y valida el nombre de un impuesto personalizable (NFKC, colapso de
    espacios y rechazo de caracteres de control). Retorna (clean_name, normalized_name).
    """
    if not isinstance(raw_name, str):
        raise DomainValidationError("El nombre del impuesto debe ser una cadena de texto.")
    if _TAX_NAME_CONTROL_RE.search(raw_name):
        raise DomainValidationError(
            "El nombre del impuesto no puede contener caracteres de control."
        )
    nfkc = unicodedata.normalize("NFKC", raw_name).strip()
    if _TAX_NAME_CONTROL_RE.search(nfkc):
        raise DomainValidationError(
            "El nombre del impuesto no puede contener caracteres de control."
        )
    clean = re.sub(r"\s+", " ", nfkc)
    if len(clean) < 1:
        raise DomainValidationError("El nombre del impuesto es obligatorio.")
    if len(clean) > 80:
        raise DomainValidationError("El nombre del impuesto no puede exceder 80 caracteres.")
    norm = clean.casefold()
    if len(norm) > 160:
        raise DomainValidationError(
            "El nombre normalizado del impuesto excede el límite permitido."
        )
    return clean, norm


def validate_tax_type_and_rate(tax_type: Any, raw_rate: Any) -> Decimal:
    """
    Valida tipo ('PERCENT' o 'FIXED') y magnitud del impuesto según decimales
    significativos tras .normalize() y topes máximos. Retorna Decimal redondeado a 4dp.
    """
    if not isinstance(tax_type, str) or tax_type not in (
        CustomTaxType.PERCENT,
        CustomTaxType.FIXED,
    ):
        raise DomainValidationError("El tipo de impuesto debe ser 'PERCENT' o 'FIXED'.")

    dec = _to_finite_decimal(raw_rate, "rate_or_amount")
    if dec < Decimal("0"):
        raise DomainValidationError("La tasa o monto del impuesto no puede ser negativo.")

    normalized = dec.normalize()
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):
        raise DomainValidationError("El campo 'rate_or_amount' debe ser un número finito.")

    if tax_type == CustomTaxType.FIXED:
        if exponent < -2:
            raise DomainValidationError(
                "Un impuesto de monto fijo (MXN) admite máximo 2 decimales significativos."
            )
        if dec > MAX_FIXED_TAX_AMOUNT:
            raise DomainValidationError(
                f"El monto fijo del impuesto no puede exceder {MAX_FIXED_TAX_AMOUNT} MXN."
            )
    else:
        if exponent < -4:
            raise DomainValidationError(
                "Un impuesto porcentual (%) admite máximo 4 decimales significativos."
            )
        if dec > MAX_PERCENT_TAX_RATE:
            raise DomainValidationError(
                f"El porcentaje del impuesto no puede exceder {MAX_PERCENT_TAX_RATE}%."
            )

    return round_quantity(dec, 4)


def _format_tax_values(tax_type: str, rate_or_amount: Decimal) -> tuple[str, str, str]:
    if tax_type == CustomTaxType.FIXED:
        amt = round_currency(rate_or_amount)
        amt_str = f"{amt:.2f}"
        return amt_str, amt_str, f"${amt_str}"
    pct = round_quantity(rate_or_amount, 4)
    rate_str = f"{pct:.4f}"
    clean_pct = format(pct.normalize(), "f")
    return rate_str, clean_pct, f"{clean_pct}%"


def build_contador_product_queryset(
    queryset: models.QuerySet[Product],
) -> models.QuerySet[Product]:
    """
    Adjunta prefetch en O(1) queries para precios activos, recetas activas con
    componentes ordenados e impuestos personalizables de CONTADOR.
    """
    return queryset.prefetch_related(
        Prefetch(
            "price_versions",
            queryset=PriceVersion.objects.filter(active=True).order_by(
                "-version_number", "-id"
            ),
            to_attr="prefetched_active_prices",
        ),
        Prefetch(
            "recipe_versions",
            queryset=RecipeVersion.objects.filter(active=True)
            .order_by("-version_number", "-id")
            .prefetch_related(
                Prefetch(
                    "components",
                    queryset=RecipeComponent.objects.select_related("component").order_by(
                        "component_id"
                    ),
                    to_attr="prefetched_components",
                )
            ),
            to_attr="prefetched_active_recipes",
        ),
        Prefetch(
            "contador_taxes",
            queryset=ProductCustomTax.objects.select_related("created_by").order_by(
                "position", "id"
            ),
            to_attr="prefetched_contador_taxes",
        ),
    )


def _build_contador_bundle(product: Product) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Regla única de cálculo CONTADOR (ALTA-01):
    calcula costos de receta activa vigente, impuestos personalizables sin cascada,
    costo parcial de producción y proporciones, reutilizando relaciones prefetched.
    """
    prefetched_prices = getattr(product, "prefetched_active_prices", None)
    if prefetched_prices is not None:
        curr_price = prefetched_prices[0] if prefetched_prices else None
    else:
        curr_price = (
            product.price_versions.filter(active=True)
            .order_by("-version_number", "-id")
            .first()
        )
    current_price_str = (
        f"{round_currency(curr_price.price_with_tax):.2f}"
        if curr_price is not None
        else None
    )

    prefetched_recipes = getattr(product, "prefetched_active_recipes", None)
    if prefetched_recipes is not None:
        curr_recipe = prefetched_recipes[0] if prefetched_recipes else None
    else:
        curr_recipe = (
            product.recipe_versions.filter(active=True)
            .order_by("-version_number", "-id")
            .first()
        )

    has_active_recipe = curr_recipe is not None
    if curr_recipe is not None:
        prefetched_comps = getattr(curr_recipe, "prefetched_components", None)
        if prefetched_comps is not None:
            raw_components = prefetched_comps
        else:
            raw_components = list(
                curr_recipe.components.select_related("component").order_by(
                    "component_id"
                )
            )
    else:
        raw_components = []

    recipe_components: list[dict[str, Any]] = []
    missing_cost_components: list[dict[str, Any]] = []
    known_line_costs: list[Decimal] = []

    for rc in raw_components:
        comp = rc.component
        qty_dec = round_quantity(rc.quantity, 6)
        avg_cost = comp.average_cost
        cost_known = avg_cost is not None

        if cost_known:
            avg_cost_dec = round_quantity(avg_cost, 6)
            line_cost_dec: Decimal | None = round_currency(qty_dec * avg_cost_dec)
            line_cost_precise_dec: Decimal | None = round_quantity(
                qty_dec * avg_cost_dec, 6
            )
            known_line_costs.append(line_cost_dec)
        else:
            avg_cost_dec = None
            line_cost_dec = None
            line_cost_precise_dec = None
            missing_cost_components.append(
                {
                    "component_id": comp.id,
                    "component_name": comp.name,
                    "sku": comp.sku,
                    "base_unit": comp.base_unit,
                    "quantity": f"{qty_dec:.6f}",
                }
            )

        recipe_components.append(
            {
                "component_id": comp.id,
                "component_name": comp.name,
                "sku": comp.sku,
                "component_type": comp.component_type,
                "base_unit": comp.base_unit,
                "unit_precision": comp.unit_precision,
                "active": comp.active,
                "quantity": f"{qty_dec:.6f}",
                "average_cost": f"{avg_cost_dec:.6f}" if avg_cost_dec is not None else None,
                "cost_known": cost_known,
                "line_cost": f"{line_cost_dec:.2f}" if line_cost_dec is not None else None,
                "line_cost_precise": (
                    f"{line_cost_precise_dec:.6f}"
                    if line_cost_precise_dec is not None
                    else None
                ),
                "share_percent": "0.00",
                "_line_cost_dec": line_cost_dec,
            }
        )

    known_components_subtotal = round_currency(sum(known_line_costs, start=ZERO_AMT))
    recipe_cost_complete = (
        has_active_recipe
        and len(recipe_components) > 0
        and len(missing_cost_components) == 0
    )
    recipe_total_cost = known_components_subtotal if recipe_cost_complete else None

    for row in recipe_components:
        line_dec = row.pop("_line_cost_dec")
        if recipe_total_cost is not None and recipe_total_cost > ZERO_AMT and line_dec is not None:
            share_dec = round_currency((line_dec * Decimal("100")) / recipe_total_cost)
            row["share_percent"] = f"{share_dec:.2f}"

    prefetched_taxes = getattr(product, "prefetched_contador_taxes", None)
    if prefetched_taxes is not None:
        tax_rows = list(prefetched_taxes)
    else:
        tax_rows = list(
            product.contador_taxes.select_related("created_by").order_by(
                "position", "id"
            )
        )

    serialized_taxes: list[dict[str, Any]] = []
    known_tax_amounts: list[Decimal] = []

    for tax in tax_rows:
        rate_str, edit_value, display_value = _format_tax_values(
            tax.tax_type, tax.rate_or_amount
        )
        if tax.tax_type == CustomTaxType.FIXED:
            calc_dec: Decimal | None = round_currency(tax.rate_or_amount)
            amount_known = True
            known_tax_amounts.append(calc_dec)
        elif recipe_cost_complete and recipe_total_cost is not None:
            calc_dec = round_currency(
                (recipe_total_cost * tax.rate_or_amount) / Decimal("100")
            )
            amount_known = True
            known_tax_amounts.append(calc_dec)
        else:
            calc_dec = None
            amount_known = False

        serialized_taxes.append(
            {
                "tax_id": tax.id,
                "product_id": tax.product_id,
                "name": tax.name,
                "tax_type": tax.tax_type,
                "rate_or_amount": rate_str,
                "edit_value": edit_value,
                "display_value": display_value,
                "position": tax.position,
                "amount_known": amount_known,
                "calculated_amount": f"{calc_dec:.2f}" if calc_dec is not None else None,
                "share_of_partial_percent": "0.00",
                "created_by": (
                    str(tax.created_by.username) if tax.created_by is not None else None
                ),
                "created_at": tax.created_at.isoformat(),
                "updated_at": tax.updated_at.isoformat(),
                "_calc_dec": calc_dec,
            }
        )

    taxes_cost_complete = all(t["amount_known"] for t in serialized_taxes)
    known_taxes_subtotal = round_currency(sum(known_tax_amounts, start=ZERO_AMT))
    taxes_total = known_taxes_subtotal if taxes_cost_complete else None
    partial_production_cost = (
        round_currency(recipe_total_cost + taxes_total)
        if (recipe_cost_complete and recipe_total_cost is not None and taxes_total is not None)
        else None
    )

    if partial_production_cost is None or partial_production_cost <= ZERO_AMT:
        recipe_share_of_partial_percent = "0.00"
        for t in serialized_taxes:
            t.pop("_calc_dec")
    else:
        recipe_share_dec = round_currency(
            (recipe_total_cost * Decimal("100")) / partial_production_cost
        )
        recipe_share_of_partial_percent = f"{recipe_share_dec:.2f}"
        for t in serialized_taxes:
            calc_dec = t.pop("_calc_dec")
            if t["amount_known"] and calc_dec is not None:
                tax_share_dec = round_currency(
                    (calc_dec * Decimal("100")) / partial_production_cost
                )
                t["share_of_partial_percent"] = f"{tax_share_dec:.2f}"

    taxes_count = len(serialized_taxes)
    can_add_tax = taxes_count < MAX_PRODUCT_CUSTOM_TAXES
    limit_state = (
        "LIMIT_REACHED_3_OF_3" if taxes_count >= MAX_PRODUCT_CUSTOM_TAXES else "AVAILABLE"
    )

    contador_summary = {
        "has_active_recipe": has_active_recipe,
        "recipe_cost_complete": recipe_cost_complete,
        "cost_known": recipe_cost_complete,
        "known_components_subtotal": f"{known_components_subtotal:.2f}",
        "recipe_total_cost": (
            f"{recipe_total_cost:.2f}" if recipe_total_cost is not None else None
        ),
        "taxes_count": taxes_count,
        "max_taxes": MAX_PRODUCT_CUSTOM_TAXES,
        "limit_state": limit_state,
        "taxes_cost_complete": taxes_cost_complete,
        "known_taxes_subtotal": f"{known_taxes_subtotal:.2f}",
        "taxes_total": f"{taxes_total:.2f}" if taxes_total is not None else None,
        "partial_production_cost": (
            f"{partial_production_cost:.2f}"
            if partial_production_cost is not None
            else None
        ),
    }

    contador = {
        "product_id": product.id,
        "name": product.name,
        "sku": product.sku,
        "base_unit": product.base_unit,
        "active": product.active,
        "current_price": current_price_str,
        "has_active_recipe": has_active_recipe,
        "recipe_version_id": curr_recipe.id if curr_recipe is not None else None,
        "recipe_version_number": (
            curr_recipe.version_number if curr_recipe is not None else None
        ),
        "recipe_effective_from": (
            curr_recipe.effective_from.isoformat()
            if curr_recipe is not None and curr_recipe.effective_from is not None
            else None
        ),
        "recipe_cost_complete": recipe_cost_complete,
        "cost_known": recipe_cost_complete,
        "known_components_subtotal": f"{known_components_subtotal:.2f}",
        "recipe_total_cost": (
            f"{recipe_total_cost:.2f}" if recipe_total_cost is not None else None
        ),
        "missing_cost_components_count": len(missing_cost_components),
        "missing_cost_components": missing_cost_components,
        "recipe_components": recipe_components,
        "taxes": serialized_taxes,
        "taxes_count": taxes_count,
        "max_taxes": MAX_PRODUCT_CUSTOM_TAXES,
        "can_add_tax": can_add_tax,
        "limit_state": limit_state,
        "taxes_cost_complete": taxes_cost_complete,
        "known_taxes_subtotal": f"{known_taxes_subtotal:.2f}",
        "taxes_total": f"{taxes_total:.2f}" if taxes_total is not None else None,
        "partial_production_cost": (
            f"{partial_production_cost:.2f}"
            if partial_production_cost is not None
            else None
        ),
        "recipe_share_of_partial_percent": recipe_share_of_partial_percent,
        "cost_basis_note": CONTADOR_COST_BASIS_NOTE,
        "currency": "MXN",
    }
    return contador, contador_summary


def build_product_contador_summary(product: Product) -> dict[str, Any]:
    """Calcula el resumen CONTADOR de un producto ya prefetched sin queries adicionales."""
    _, summary = _build_contador_bundle(product)
    return summary


# UTC-06:00 fijo todo el año (H-04)
TZ_UTC_MINUS_6 = datetime.timezone(datetime.timedelta(hours=-6))


def calculate_product_absorption_costing(
    product: Product,
    month_str: str | None = None,
    recipe_total_cost: Decimal | None = None,
    taxes_total: Decimal | None = None,
    recipe_cost_complete: bool = False,
    taxes_cost_complete: bool = False,
    price_with_tax: Decimal | None = None,
) -> dict[str, Any]:
    """
    Calcula el costeo por absorción mensual (Fase 5A):
    - H-04: Rango mensual semiabierto [inicio_utc, fin_utc) en UTC-06:00 fijo todo el año.
    - H-01: Prorrateo basado en botellas netas de TODO el negocio (no filtrado por product_id).
    - H-02: Guarda estricta: si total_business_bottles_sold_net <= 0, has_proration_base=False,
      prorrated_expense_per_bottle=None, total_production_cost=None.
    - H-03: Coalesce(Sum(...), Value(...)) para evitar NoneType. Si recipe_cost_complete es False,
      cost_known=False y total_production_cost=None.
    - H-05: En margin_analysis, si el producto no tiene versión de precio activa, has_price=False
      y margins=None.
    """
    if not month_str or not str(month_str).strip():
        now_local = timezone.now().astimezone(TZ_UTC_MINUS_6)
        year = now_local.year
        month = now_local.month
        resolved_month = f"{year:04d}-{month:02d}"
    else:
        resolved_month = str(month_str).strip()
        month_match = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", resolved_month)
        if not month_match:
            raise DomainValidationError("El parámetro 'month' debe tener el formato YYYY-MM.")
        year = int(month_match.group(1))
        month = int(month_match.group(2))

    inicio_local = datetime.datetime(year, month, 1, 0, 0, 0, tzinfo=TZ_UTC_MINUS_6)
    if month == 12:
        fin_local = datetime.datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=TZ_UTC_MINUS_6)
    else:
        fin_local = datetime.datetime(year, month + 1, 1, 0, 0, 0, tzinfo=TZ_UTC_MINUS_6)

    inicio_utc = inicio_local.astimezone(datetime.timezone.utc)
    fin_utc = fin_local.astimezone(datetime.timezone.utc)

    # 1. Total gastos de todo el negocio en el mes (H-03)
    total_business_expenses = Expense.objects.filter(
        occurred_at__gte=inicio_utc,
        occurred_at__lt=fin_utc,
    ).aggregate(
        total=Coalesce(Sum("amount"), Value(Decimal("0.00"), output_field=models.DecimalField()))
    )["total"]
    total_business_expenses = round_currency(total_business_expenses)

    # 2. Total botellas vendidas netas de todo el negocio en el mes (H-01, H-03)
    sold_bottles = SaleLine.objects.filter(
        sale__status=SaleStatus.FULFILLED,
        sale__fulfilled_at__gte=inicio_utc,
        sale__fulfilled_at__lt=fin_utc,
    ).aggregate(
        total=Coalesce(Sum("quantity"), Value(Decimal("0.000000"), output_field=models.DecimalField()))
    )["total"]
    sold_bottles = round_quantity(sold_bottles, 6)

    # Devoluciones completadas
    returned_bottles = Return.objects.filter(
        status=ReturnStatus.COMPLETED,
        returned_at__gte=inicio_utc,
        returned_at__lt=fin_utc,
    ).aggregate(
        total=Coalesce(Sum("quantity"), Value(Decimal("0.000000"), output_field=models.DecimalField()))
    )["total"]

    if returned_bottles == ZERO_QTY:
        ret_lines_qty = ReturnLine.objects.filter(
            ret__status=ReturnStatus.COMPLETED,
            ret__returned_at__gte=inicio_utc,
            ret__returned_at__lt=fin_utc,
        ).aggregate(
            total=Coalesce(Sum("quantity_returned"), Value(Decimal("0.000000"), output_field=models.DecimalField()))
        )["total"]
        if ret_lines_qty > ZERO_QTY:
            returned_bottles = ret_lines_qty

    returned_bottles = round_quantity(returned_bottles, 6)
    net_bottles = max(Decimal("0.000000"), sold_bottles - returned_bottles)
    net_bottles = round_quantity(net_bottles, 6)

    # 3. Prorrateo y guardas estrictas (H-02, H-03)
    if net_bottles > Decimal("0.000000"):
        has_proration_base = True
        prorrated_expense_per_bottle = round_currency(total_business_expenses / net_bottles)
    else:
        has_proration_base = False
        prorrated_expense_per_bottle = None

    if (
        has_proration_base
        and recipe_cost_complete
        and taxes_cost_complete
        and recipe_total_cost is not None
        and taxes_total is not None
        and prorrated_expense_per_bottle is not None
    ):
        total_production_cost = round_currency(
            recipe_total_cost + taxes_total + prorrated_expense_per_bottle
        )
    else:
        total_production_cost = None

    # 4. Desglose porcentual relativo
    if total_production_cost is not None and total_production_cost > ZERO_AMT:
        recipe_pct = round_currency((recipe_total_cost * Decimal("100")) / total_production_cost)
        taxes_pct = round_currency((taxes_total * Decimal("100")) / total_production_cost)
        expenses_pct = round_currency((prorrated_expense_per_bottle * Decimal("100")) / total_production_cost)
        cost_breakdown_percent = {
            "recipe_percent": f"{recipe_pct:.2f}",
            "taxes_percent": f"{taxes_pct:.2f}",
            "expenses_percent": f"{expenses_pct:.2f}",
        }
    else:
        recipe_pct = None
        taxes_pct = None
        expenses_pct = None
        cost_breakdown_percent = {
            "recipe_percent": "0.00",
            "taxes_percent": "0.00",
            "expenses_percent": "0.00",
        }

    cost_known = bool(
        total_production_cost is not None
        and has_proration_base
        and recipe_cost_complete
        and taxes_cost_complete
    )

    # 5. Análisis de margen frente a precio de venta vigente (H-05)
    if price_with_tax is None:
        margin_analysis = {
            "has_price": False,
            "sale_price": None,
            "price_with_tax": None,
            "margins": None,
            "net_margin": None,
            "margin_amount": None,
            "margin_percent": None,
            "markup_percent": None,
        }
    else:
        sale_price = round_currency(price_with_tax)
        if total_production_cost is not None:
            margin_amt = round_currency(sale_price - total_production_cost)
            margin_pct = (
                round_currency((margin_amt * Decimal("100")) / sale_price)
                if sale_price > ZERO_AMT
                else ZERO_AMT
            )
            markup_pct = (
                round_currency((margin_amt * Decimal("100")) / total_production_cost)
                if total_production_cost > ZERO_AMT
                else None
            )
            margins_obj = {
                "net_margin": f"{margin_amt:.2f}",
                "margin_amount": f"{margin_amt:.2f}",
                "margin_percent": f"{margin_pct:.2f}",
                "markup_percent": f"{markup_pct:.2f}" if markup_pct is not None else None,
            }
            margin_analysis = {
                "has_price": True,
                "sale_price": f"{sale_price:.2f}",
                "price_with_tax": f"{sale_price:.2f}",
                "margins": margins_obj,
                "net_margin": f"{margin_amt:.2f}",
                "margin_amount": f"{margin_amt:.2f}",
                "margin_percent": f"{margin_pct:.2f}",
                "markup_percent": f"{markup_pct:.2f}" if markup_pct is not None else None,
            }
        else:
            margin_analysis = {
                "has_price": True,
                "sale_price": f"{sale_price:.2f}",
                "price_with_tax": f"{sale_price:.2f}",
                "margins": None,
                "net_margin": None,
                "margin_amount": None,
                "margin_percent": None,
                "markup_percent": None,
            }

    return {
        "month": resolved_month,
        "has_proration_base": has_proration_base,
        "cost_known": cost_known,
        "total_business_expenses": f"{total_business_expenses:.2f}",
        "total_business_bottles_sold_net": f"{net_bottles:.6f}",
        "business_bottles_fulfilled": f"{sold_bottles:.6f}",
        "business_bottles_returned": f"{returned_bottles:.6f}",
        "prorrated_expense_per_bottle": (
            f"{prorrated_expense_per_bottle:.2f}"
            if prorrated_expense_per_bottle is not None
            else None
        ),
        "recipe_total_cost": (
            f"{recipe_total_cost:.2f}"
            if recipe_total_cost is not None
            else None
        ),
        "taxes_total": (
            f"{taxes_total:.2f}"
            if taxes_total is not None
            else None
        ),
        "total_production_cost": (
            f"{total_production_cost:.2f}"
            if total_production_cost is not None
            else None
        ),
        "recipe_percent": (
            f"{recipe_pct:.2f}"
            if recipe_pct is not None
            else None
        ),
        "taxes_percent": (
            f"{taxes_pct:.2f}"
            if taxes_pct is not None
            else None
        ),
        "expenses_percent": (
            f"{expenses_pct:.2f}"
            if expenses_pct is not None
            else None
        ),
        "cost_breakdown_percent": cost_breakdown_percent,
        "breakdown": cost_breakdown_percent,
        "margin_analysis": margin_analysis,
    }


def get_product_contador_summary(product_id: int, month_str: str | None = None) -> dict[str, Any]:
    """Obtiene el DTO completo de CONTADOR para un producto o lanza 404 si no existe."""
    product = build_contador_product_queryset(
        Product.objects.filter(id=product_id)
    ).first()
    if product is None:
        raise ResourceNotFoundError(f"Producto {product_id} no existe.")
    contador, _ = _build_contador_bundle(product)

    price_val = (
        Decimal(contador["current_price"])
        if contador.get("current_price") is not None
        else None
    )

    recipe_total_cost = (
        Decimal(contador["recipe_total_cost"])
        if contador.get("recipe_total_cost") is not None
        else None
    )
    taxes_total = (
        Decimal(contador["taxes_total"])
        if contador.get("taxes_total") is not None
        else None
    )

    contador["absorption_costing"] = calculate_product_absorption_costing(
        product=product,
        month_str=month_str,
        recipe_total_cost=recipe_total_cost,
        taxes_total=taxes_total,
        recipe_cost_complete=contador.get("recipe_cost_complete", False),
        taxes_cost_complete=contador.get("taxes_cost_complete", False),
        price_with_tax=price_val,
    )
    return contador


def create_product_custom_tax(
    product_id: int,
    name: Any,
    tax_type: Any,
    rate_or_amount: Any,
    position: int | None = None,
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Crea un impuesto personalizable (máximo 3 por producto) de forma atómica y
    retorna {'tax': ..., 'contador': ...}.
    """
    _require_admin_actor(actor)
    clean_name, norm_name = normalize_tax_name(name)
    validated_rate = validate_tax_type_and_rate(tax_type, rate_or_amount)

    if position is not None:
        if (
            isinstance(position, bool)
            or not isinstance(position, int)
            or position < 1
            or position > MAX_PRODUCT_CUSTOM_TAXES
        ):
            raise DomainValidationError(
                "La posición del impuesto debe ser un entero entre 1 y 3."
            )

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        existing = list(
            ProductCustomTax.objects.select_for_update()
            .filter(product_id=product.id)
            .order_by("position", "id")
        )
        if len(existing) >= MAX_PRODUCT_CUSTOM_TAXES:
            raise BusinessConflictError(
                f"El producto ya alcanzó el límite máximo de {MAX_PRODUCT_CUSTOM_TAXES} impuestos personalizables.",
                details={
                    "reason": "LIMIT_REACHED_3_OF_3",
                    "product_id": product.id,
                    "max_taxes": MAX_PRODUCT_CUSTOM_TAXES,
                },
            )

        if any(t.normalized_name == norm_name for t in existing):
            raise BusinessConflictError(
                f"Ya existe un impuesto llamado '{clean_name}' en este producto.",
                details={"reason": "DUPLICATE_TAX_NAME", "product_id": product.id},
            )

        used_positions = {t.position for t in existing}
        if position is None:
            free_positions = sorted(
                set(range(1, MAX_PRODUCT_CUSTOM_TAXES + 1)) - used_positions
            )
            target_position = free_positions[0]
        else:
            if position in used_positions:
                raise BusinessConflictError(
                    f"La posición {position} ya está ocupada en este producto.",
                    details={
                        "reason": "POSITION_OCCUPIED",
                        "product_id": product.id,
                        "position": position,
                    },
                )
            target_position = position

        try:
            with transaction.atomic():
                tax = ProductCustomTax.objects.create(
                    product=product,
                    name=clean_name,
                    normalized_name=norm_name,
                    tax_type=tax_type,
                    rate_or_amount=validated_rate,
                    position=target_position,
                    created_by=actor,
                )
        except IntegrityError as exc:
            raise BusinessConflictError(
                "Conflicto de concurrencia o unicidad al registrar el impuesto."
            ) from exc

        contador = get_product_contador_summary(product.id)
        serialized_tax = next(
            (t for t in contador["taxes"] if t["tax_id"] == tax.id),
            None,
        )
        return {"tax": serialized_tax, "contador": contador}


def update_product_custom_tax(
    product_id: int,
    tax_id: int,
    updates: dict[str, Any],
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Actualiza parcialmente un impuesto personalizable con validación cruzada
    (tax_type efectivo vs rate_or_amount efectivo) y lookup estricto anti-IDOR.
    """
    _require_admin_actor(actor)
    if not isinstance(updates, dict) or not any(
        k in updates for k in ("name", "tax_type", "rate_or_amount", "position")
    ):
        raise DomainValidationError(
            "Debe proporcionar al menos un campo para actualizar (name, tax_type, rate_or_amount o position)."
        )

    if "position" in updates:
        pos_val = updates["position"]
        if (
            isinstance(pos_val, bool)
            or not isinstance(pos_val, int)
            or pos_val < 1
            or pos_val > MAX_PRODUCT_CUSTOM_TAXES
        ):
            raise DomainValidationError(
                "La posición del impuesto debe ser un entero entre 1 y 3."
            )

    clean_name: str | None = None
    norm_name: str | None = None
    if "name" in updates:
        clean_name, norm_name = normalize_tax_name(updates["name"])

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        tax = (
            ProductCustomTax.objects.select_for_update()
            .filter(id=tax_id, product_id=product.id)
            .first()
        )
        if tax is None:
            raise ResourceNotFoundError(
                f"El impuesto {tax_id} no existe para el producto {product_id}."
            )

        effective_name = clean_name if clean_name is not None else tax.name
        effective_norm = norm_name if norm_name is not None else tax.normalized_name
        effective_type = updates.get("tax_type", tax.tax_type)
        effective_rate = updates.get("rate_or_amount", tax.rate_or_amount)
        validated_rate = validate_tax_type_and_rate(effective_type, effective_rate)
        effective_position = updates.get("position", tax.position)

        if (
            ProductCustomTax.objects.filter(
                product_id=product.id, normalized_name=effective_norm
            )
            .exclude(id=tax.id)
            .exists()
        ):
            raise BusinessConflictError(
                f"Ya existe otro impuesto llamado '{effective_name}' en este producto.",
                details={"reason": "DUPLICATE_TAX_NAME", "product_id": product.id},
            )

        if (
            ProductCustomTax.objects.filter(
                product_id=product.id, position=effective_position
            )
            .exclude(id=tax.id)
            .exists()
        ):
            raise BusinessConflictError(
                f"La posición {effective_position} ya está ocupada en este producto.",
                details={
                    "reason": "POSITION_OCCUPIED",
                    "product_id": product.id,
                    "position": effective_position,
                },
            )

        tax.name = effective_name
        tax.normalized_name = effective_norm
        tax.tax_type = effective_type
        tax.rate_or_amount = validated_rate
        tax.position = effective_position

        try:
            with transaction.atomic():
                tax.save()
        except IntegrityError as exc:
            raise BusinessConflictError(
                "Conflicto de concurrencia o unicidad al actualizar el impuesto."
            ) from exc

        contador = get_product_contador_summary(product.id)
        serialized_tax = next(
            (t for t in contador["taxes"] if t["tax_id"] == tax.id),
            None,
        )
        return {"tax": serialized_tax, "contador": contador}


def delete_product_custom_tax(
    product_id: int,
    tax_id: int,
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Elimina un impuesto personalizable del producto (anti-IDOR) y recompacta
    las posiciones restantes en orden ascendente (1..N) sin colisiones.
    """
    _require_admin_actor(actor)

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(id=product_id).first()
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        tax = (
            ProductCustomTax.objects.select_for_update()
            .filter(id=tax_id, product_id=product.id)
            .first()
        )
        if tax is None:
            raise ResourceNotFoundError(
                f"El impuesto {tax_id} no existe para el producto {product_id}."
            )

        deleted_id = tax.id
        tax.delete()

        remaining = list(
            ProductCustomTax.objects.select_for_update()
            .filter(product_id=product.id)
            .order_by("position", "id")
        )
        for idx, row in enumerate(remaining, start=1):
            if row.position != idx:
                row.position = idx
                row.save(update_fields=["position", "updated_at"])

        contador = get_product_contador_summary(product.id)
        return {"deleted_tax_id": deleted_id, "contador": contador}


# ---------------------------------------------------------------------------
# Gastos y Dashboard Mensual (Fase 5A)
# ---------------------------------------------------------------------------

def _require_admin_or_operator_actor(actor: Any | None) -> None:
    if actor is None or get_user_role(actor) not in (ADMIN_ROLE, OPERATOR_ROLE):
        raise PermissionDeniedError(
            "Rol no autorizado para registrar o consultar gastos. Se requiere ADMIN u OPERADOR."
        )


def _require_expense_admin_actor(actor: Any | None) -> None:
    if actor is None or get_user_role(actor) != ADMIN_ROLE:
        raise PermissionDeniedError(
            "Solo un usuario con rol ADMIN puede modificar o eliminar gastos."
        )


def _serialize_expense_item(exp: Expense) -> dict[str, Any]:
    return {
        "id": exp.id,
        "event_id": str(exp.event_id),
        "name": exp.name,
        "amount": str(round_currency(exp.amount)),
        "category": exp.category,
        "expense_type": exp.expense_type,
        "occurred_at": exp.occurred_at.isoformat(),
        "template": exp.template_id,
        "template_id": exp.template_id,
        "device_id": exp.device_id,
        "created_by": exp.created_by_id,
        "created_by_username": exp.created_by.username if exp.created_by else None,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "updated_at": exp.updated_at.isoformat() if exp.updated_at else None,
    }


def get_monthly_expenses_dashboard(
    month_str: str | None = None,
    category_filter: str | None = None,
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Dashboard mensual de gastos (Fase 5A):
    - Resuelve mes 'YYYY-MM' en America/Mexico_City (UTC-06:00).
    - Delimita inicio_local y fin_local, convierte a UTC.
    - Agrega total_amount, expense_count, daily_average (con lógica H-04).
    - Agrupa por categoría con sumas y porcentajes sobre el total mensual.
    - Serializa ítems ordenados por -occurred_at, -id.
    """
    _require_admin_or_operator_actor(actor)

    now_cdmx = timezone.now().astimezone(CDMX_TZ)

    if not month_str or not str(month_str).strip():
        resolved_month = now_cdmx.strftime("%Y-%m")
    else:
        resolved_month = str(month_str).strip()

    month_match = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", resolved_month)
    if not month_match:
        raise DomainValidationError("El parámetro 'month' debe tener el formato YYYY-MM.")

    year = int(month_match.group(1))
    month = int(month_match.group(2))
    _, total_days = calendar.monthrange(year, month)

    inicio_local = datetime.datetime(year, month, 1, 0, 0, 0, tzinfo=CDMX_TZ)
    fin_local = datetime.datetime(year, month, total_days, 23, 59, 59, 999999, tzinfo=CDMX_TZ)

    inicio_utc = inicio_local.astimezone(datetime.timezone.utc)
    fin_utc = fin_local.astimezone(datetime.timezone.utc)

    month_qs = Expense.objects.filter(
        occurred_at__gte=inicio_utc,
        occurred_at__lte=fin_utc,
    )

    # Agrupación por categoría sobre el mes completo
    month_total_dec = month_qs.aggregate(t=models.Sum("amount"))["t"] or ZERO_AMT
    month_total_dec = round_currency(month_total_dec)

    cat_aggregates = (
        month_qs.values("category")
        .annotate(
            cat_total=models.Sum("amount"),
            cat_count=models.Count("id"),
        )
        .order_by("-cat_total", "category")
    )

    categories_list: list[dict[str, Any]] = []
    for row in cat_aggregates:
        c_tot = round_currency(row["cat_total"] or ZERO_AMT)
        c_pct = (
            round_currency((c_tot * Decimal("100")) / month_total_dec)
            if month_total_dec > ZERO_AMT
            else ZERO_AMT
        )
        categories_list.append({
            "category": row["category"],
            "total_amount": str(c_tot),
            "expense_count": row["cat_count"],
            "percentage": str(c_pct),
        })

    # Filtrado opcional de categoría
    clean_cat_filter = None
    if category_filter and str(category_filter).strip():
        norm_filter = str(category_filter).strip().upper()
        if norm_filter not in ("ALL", "TODAS", "TODOS"):
            clean_cat_filter = norm_filter
            items_qs = month_qs.filter(category__iexact=norm_filter)
        else:
            items_qs = month_qs
    else:
        items_qs = month_qs

    # Totales y daily average (H-04)
    active_total_dec = items_qs.aggregate(t=models.Sum("amount"))["t"] or ZERO_AMT
    active_total_dec = round_currency(active_total_dec)
    active_count = items_qs.count()

    # H-04: Cálculo de daily_average:
    # si el mes es el actual en CDMX (UTC-06:00), divisor = max(1, now_cdmx.day);
    # si es mes pasado (o distinto), divisor = días totales del mes.
    if year == now_cdmx.year and month == now_cdmx.month:
        divisor = max(1, now_cdmx.day)
    else:
        divisor = total_days

    daily_avg_dec = round_currency(active_total_dec / Decimal(str(divisor)))

    # Serializar ítems ordenados por -occurred_at, -id
    ordered_items = items_qs.select_related("created_by").order_by("-occurred_at", "-id")
    serialized_items = [_serialize_expense_item(exp) for exp in ordered_items]

    return {
        "month": resolved_month,
        "total_amount": str(active_total_dec),
        "expense_count": active_count,
        "daily_average": str(daily_avg_dec),
        "category_filter": clean_cat_filter,
        "categories": categories_list,
        "items": serialized_items,
    }


def register_single_expense(data: dict[str, Any], actor: Any | None = None) -> dict[str, Any]:
    """
    Registra un gasto único de forma transaccional con idempotencia por event_id.
    Permitido para ADMIN y OPERADOR.
    """
    _require_admin_or_operator_actor(actor)

    raw_name = data.get("name", "")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise DomainValidationError("El nombre del gasto es obligatorio.")
    name = unicodedata.normalize("NFKC", raw_name).strip()

    raw_amount = data.get("amount")
    dec_amount = _to_finite_decimal(raw_amount, "amount")
    dec_amount = round_currency(dec_amount)
    if dec_amount <= ZERO_AMT:
        raise DomainValidationError("El importe del gasto debe ser estrictamente positivo.")

    raw_cat = data.get("category", "OTROS")
    category = unicodedata.normalize("NFKC", str(raw_cat or "")).strip().upper() or "OTROS"

    raw_occurred = data.get("occurred_at")
    if raw_occurred is None:
        occurred_at = timezone.now()
    elif isinstance(raw_occurred, str):
        try:
            dt = datetime.datetime.fromisoformat(raw_occurred)
        except (ValueError, TypeError) as exc:
            raise DomainValidationError("Formato de fecha 'occurred_at' inválido.") from exc
        if timezone.is_naive(dt):
            occurred_at = timezone.make_aware(dt, CDMX_TZ).astimezone(datetime.timezone.utc)
        else:
            occurred_at = dt.astimezone(datetime.timezone.utc)
    elif isinstance(raw_occurred, datetime.datetime):
        if timezone.is_naive(raw_occurred):
            occurred_at = timezone.make_aware(raw_occurred, CDMX_TZ).astimezone(datetime.timezone.utc)
        else:
            occurred_at = raw_occurred.astimezone(datetime.timezone.utc)
    else:
        raise DomainValidationError("Formato de fecha 'occurred_at' inválido.")

    raw_event_id = data.get("event_id")
    if raw_event_id:
        try:
            event_id = uuid.UUID(str(raw_event_id))
        except (ValueError, TypeError) as exc:
            raise DomainValidationError("El event_id debe ser un UUID válido.") from exc
    else:
        event_id = uuid.uuid4()

    expense_type = data.get("expense_type", ExpenseType.ONE_TIME)

    with transaction.atomic():
        existing = Expense.objects.filter(event_id=event_id).first()
        if existing is not None:
            if (
                existing.name == name
                and existing.amount == dec_amount
                and existing.category == category
            ):
                return _serialize_expense_item(existing)
            raise IdempotencyKeyReusedError("El event_id ya fue utilizado con un payload diferente.")

        expense = Expense.objects.create(
            event_id=event_id,
            name=name,
            amount=dec_amount,
            category=category,
            expense_type=expense_type,
            occurred_at=occurred_at,
            created_by=actor if isinstance(actor, User) else None,
            device_id=str(data.get("device_id") or ""),
        )

        return _serialize_expense_item(expense)


def update_expense(
    expense_id: int,
    updates: dict[str, Any],
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Actualiza name, amount, category, occurred_at de un gasto de forma transaccional.
    Solo ADMIN.
    H-01: Si device_id == 'initial_import', rechaza con OpeningExpenseProtectedError (403/422).
    """
    _require_expense_admin_actor(actor)

    if not updates:
        raise DomainValidationError("No se proporcionaron campos para actualizar.")

    with transaction.atomic():
        expense = Expense.objects.select_for_update().filter(id=expense_id).first()
        if expense is None:
            raise ResourceNotFoundError(f"Gasto con ID {expense_id} no encontrado.")

        # H-01: Proteger gastos de apertura contra PATCH
        if expense.device_id == "initial_import":
            raise OpeningExpenseProtectedError(
                "No se permite modificar un gasto originado por la importación de saldos iniciales."
            )

        update_fields: list[str] = ["updated_at"]

        if "name" in updates:
            raw_name = updates["name"]
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise DomainValidationError("El nombre del gasto no puede estar vacío.")
            expense.name = unicodedata.normalize("NFKC", raw_name).strip()
            update_fields.append("name")

        if "amount" in updates:
            raw_amount = updates["amount"]
            dec_amount = _to_finite_decimal(raw_amount, "amount")
            dec_amount = round_currency(dec_amount)
            if dec_amount <= ZERO_AMT:
                raise DomainValidationError("El importe del gasto debe ser estrictamente positivo.")
            expense.amount = dec_amount
            update_fields.append("amount")

        if "category" in updates:
            raw_cat = updates["category"]
            cat = unicodedata.normalize("NFKC", str(raw_cat or "")).strip().upper()
            expense.category = cat or "OTROS"
            update_fields.append("category")

        if "occurred_at" in updates:
            raw_occurred = updates["occurred_at"]
            if isinstance(raw_occurred, str):
                try:
                    dt = datetime.datetime.fromisoformat(raw_occurred)
                except (ValueError, TypeError) as exc:
                    raise DomainValidationError("Formato de fecha 'occurred_at' inválido.") from exc
                if timezone.is_naive(dt):
                    occurred_at = timezone.make_aware(dt, CDMX_TZ).astimezone(datetime.timezone.utc)
                else:
                    occurred_at = dt.astimezone(datetime.timezone.utc)
            elif isinstance(raw_occurred, datetime.datetime):
                if timezone.is_naive(raw_occurred):
                    occurred_at = timezone.make_aware(raw_occurred, CDMX_TZ).astimezone(datetime.timezone.utc)
                else:
                    occurred_at = raw_occurred.astimezone(datetime.timezone.utc)
            else:
                raise DomainValidationError("Formato de fecha 'occurred_at' inválido.")
            expense.occurred_at = occurred_at
            update_fields.append("occurred_at")

        expense.save(update_fields=update_fields)

        return _serialize_expense_item(expense)


def delete_expense(
    expense_id: int,
    actor: Any | None = None,
) -> dict[str, Any]:
    """
    Elimina un gasto de forma transaccional con select_for_update.
    Solo ADMIN.
    H-01: Si device_id == 'initial_import', rechaza con OpeningExpenseProtectedError (403/422).
    H-03: Registra snapshot en auditoría antes de eliminar la fila.
    """
    _require_expense_admin_actor(actor)

    with transaction.atomic():
        expense = Expense.objects.select_for_update().filter(id=expense_id).first()
        if expense is None:
            raise ResourceNotFoundError(f"Gasto con ID {expense_id} no encontrado.")

        # H-01: Proteger gastos de apertura contra DELETE
        if expense.device_id == "initial_import":
            raise OpeningExpenseProtectedError(
                "No se permite eliminar un gasto originado por la importación de saldos iniciales."
            )

        # H-03: Snapshot de auditoría antes de eliminar fila
        snapshot = _serialize_expense_item(expense)
        snapshot["deleted_at"] = timezone.now().isoformat()
        snapshot["deleted_by"] = actor.username if hasattr(actor, "username") else str(actor)

        logger.info(
            "AUDIT: Eliminación de gasto id=%d por usuario=%s: snapshot=%s",
            expense_id,
            actor,
            snapshot,
        )

        expense.delete()

        return {
            "deleted_expense_id": expense_id,
            "audit_snapshot": snapshot,
            "status": "DELETED",
        }


def generate_recurring_expenses(
    target_date: datetime.date | None = None,
    actor: Any | None = None,
) -> list[Expense]:
    """
    Genera ocurrencias de gastos recurrentes (mensuales o anuales) hasta target_date.
    H-06: Propaga category de ExpenseTemplate a Expense.
    Evita duplicar ocurrencias ya generadas para la misma plantilla y fecha.
    """
    now_cdmx = timezone.now().astimezone(CDMX_TZ)
    effective_target_date = target_date or now_cdmx.date()

    templates = ExpenseTemplate.objects.filter(active=True)
    generated: list[Expense] = []

    with transaction.atomic():
        for tmpl in templates:
            if not tmpl.start_date or tmpl.start_date > effective_target_date:
                continue

            current_date = tmpl.start_date
            while current_date <= effective_target_date:
                if tmpl.end_date and current_date > tmpl.end_date:
                    break

                # Comprobar si ya existe ocurrencia para esta plantilla en esta fecha
                start_day_local = datetime.datetime.combine(current_date, datetime.time.min, tzinfo=CDMX_TZ)
                end_day_local = datetime.datetime.combine(current_date, datetime.time.max, tzinfo=CDMX_TZ)
                start_day_utc = start_day_local.astimezone(datetime.timezone.utc)
                end_day_utc = end_day_local.astimezone(datetime.timezone.utc)

                exists = Expense.objects.filter(
                    template=tmpl,
                    occurred_at__gte=start_day_utc,
                    occurred_at__lte=end_day_utc,
                ).exists()

                if not exists:
                    exp = Expense.objects.create(
                        event_id=uuid.uuid4(),
                        name=tmpl.name,
                        category=tmpl.category,  # H-06: Propagación de categoría
                        amount=round_currency(tmpl.amount),
                        expense_type=tmpl.expense_type,
                        occurred_at=start_day_utc,
                        template=tmpl,
                        created_by=actor if isinstance(actor, User) else tmpl.created_by,
                        device_id="recurring_generator",
                    )
                    generated.append(exp)

                # Siguiente fecha según frecuencia
                if tmpl.expense_type == ExpenseType.RECURRING_MONTHLY:
                    next_year = current_date.year + (1 if current_date.month == 12 else 0)
                    next_month = 1 if current_date.month == 12 else current_date.month + 1
                    _, max_days = calendar.monthrange(next_year, next_month)
                    day = min(tmpl.start_date.day, max_days)
                    current_date = datetime.date(next_year, next_month, day)
                elif tmpl.expense_type == ExpenseType.RECURRING_ANNUAL:
                    next_year = current_date.year + 1
                    _, max_days = calendar.monthrange(next_year, tmpl.start_date.month)
                    day = min(tmpl.start_date.day, max_days)
                    current_date = datetime.date(next_year, tmpl.start_date.month, day)
                else:
                    break

    return generated
