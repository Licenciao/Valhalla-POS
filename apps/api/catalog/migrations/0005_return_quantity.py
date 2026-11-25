# Generated manually for FASE 5A: Return.quantity + idempotent backfill

from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations, models

SIX_PLACES = Decimal("0.000001")
ZERO_QTY = Decimal("0.000000")
ZERO_AMT = Decimal("0.00")
ONE_UNIT = Decimal("1.000000")


def _round_quantity(value: Decimal) -> Decimal:
    return value.quantize(SIX_PLACES, rounding=ROUND_HALF_UP)


def _infer_legacy_return_quantity(
    ret,
    recipe_components_by_rv: dict[int, dict[int, Decimal]],
) -> Decimal | None:
    line = getattr(ret, "sale_line", None)
    if line is None:
        return None

    # Estrategia 1: ReturnLine.quantity_returned / RecipeComponent.quantity
    # sobre sale_line.recipe_version_id (priorizando rc.quantity == 1.000000).
    if line.recipe_version_id:
        rc_map = recipe_components_by_rv.get(line.recipe_version_id, {})
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
                inferred = _round_quantity(qty_returned / rc_qty)
                if inferred > ZERO_QTY:
                    return inferred

    # Estrategia 2 (fallback): (ret.refund_amount / line.final_amount) * line.quantity
    if (
        ret.refund_amount is not None
        and ret.refund_amount > ZERO_AMT
        and line.final_amount is not None
        and line.final_amount > ZERO_AMT
        and line.quantity is not None
        and line.quantity > ZERO_QTY
    ):
        inferred = _round_quantity((ret.refund_amount / line.final_amount) * line.quantity)
        if inferred > ZERO_QTY:
            return inferred

    return None


def backfill_return_quantities(apps, schema_editor) -> None:
    Return = apps.get_model("catalog", "Return")
    RecipeComponent = apps.get_model("catalog", "RecipeComponent")

    pending_returns = list(
        Return.objects.filter(quantity__isnull=True)
        .select_related("sale_line")
        .prefetch_related("lines")
        .order_by("id")
    )
    if not pending_returns:
        return

    recipe_version_ids = {
        ret.sale_line.recipe_version_id
        for ret in pending_returns
        if ret.sale_line_id and ret.sale_line.recipe_version_id
    }
    recipe_components_by_rv: dict[int, dict[int, Decimal]] = {}
    if recipe_version_ids:
        for rc in RecipeComponent.objects.filter(recipe_version_id__in=recipe_version_ids):
            recipe_components_by_rv.setdefault(rc.recipe_version_id, {})[rc.component_id] = rc.quantity

    to_update = []
    for ret in pending_returns:
        inferred = _infer_legacy_return_quantity(ret, recipe_components_by_rv)
        if inferred is not None:
            ret.quantity = inferred
            to_update.append(ret)

    if to_update:
        Return.objects.bulk_update(to_update, ["quantity"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_catalogidempotency"),
    ]

    operations = [
        migrations.AddField(
            model_name="return",
            name="quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.RunPython(
            backfill_return_quantities,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
