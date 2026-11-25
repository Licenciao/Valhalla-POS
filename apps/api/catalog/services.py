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

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

from system.constants import ADMIN_ROLE
from system.permissions import get_user_role
from catalog.exceptions import (
    BusinessConflictError,
    CostRequiredError,
    DomainValidationError,
    IdempotencyKeyReusedError,
    InsufficientStockError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from catalog.models import (
    Component,
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
