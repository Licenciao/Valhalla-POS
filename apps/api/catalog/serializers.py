"""
catalog/serializers.py

Serializadores DRF para catálogo, recetas, inventario y ventas.
Valida y normaliza según 11-borrador-contrato-api.md.
"""

from decimal import Decimal
from typing import Any
import unicodedata
from rest_framework import serializers

from catalog.models import (
    Component,
    ComponentType,
    CustomTaxType,
    Expense,
    ExpenseTemplate,
    ExpenseType,
    PriceVersion,
    Product,
    RecipeComponent,
    RecipeVersion,
    Return,
    Sale,
    SaleLine,
)


class RecipeComponentInputSerializer(serializers.Serializer):
    component_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))
    unit = serializers.CharField(max_length=20, required=False, default="")


class ProductCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    sku = serializers.CharField(max_length=100, required=False, default="")
    description = serializers.CharField(required=False, default="")
    base_unit = serializers.CharField(max_length=20, required=False, default="pza")
    # Campos opcionales para creación atómica de botella con receta y precio
    price_with_tax = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.00"), required=False, default=None
    )
    components = RecipeComponentInputSerializer(many=True, required=False, default=None)


class ComponentCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    sku = serializers.CharField(max_length=100, required=False, default="")
    description = serializers.CharField(required=False, default="")
    component_type = serializers.ChoiceField(
        choices=ComponentType.choices,
        required=False,
        default=ComponentType.OTHER,
    )
    base_unit = serializers.CharField(max_length=20)
    unit_precision = serializers.IntegerField(min_value=0, max_value=6, required=False, default=None)
    initial_stock = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=Decimal("0.000000"), required=False, default=Decimal("0.000000")
    )
    unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=Decimal("0.000000"), required=False, default=Decimal("0.000000")
    )

    def validate(self, attrs):
        raw_unit = attrs.get("base_unit", "").strip().lower()
        if raw_unit in ["l", "litro", "litros"]:
            attrs["base_unit"] = "L"
            if attrs.get("unit_precision") is None:
                attrs["unit_precision"] = 3
        elif raw_unit in ["pza", "pzas", "pieza", "piezas"]:
            attrs["base_unit"] = "pza"
            if attrs.get("unit_precision") is None:
                attrs["unit_precision"] = 0
        else:
            if attrs.get("unit_precision") is None:
                attrs["unit_precision"] = 0
        return attrs


class RecipeVersionCreateSerializer(serializers.Serializer):
    effective_at = serializers.DateTimeField(required=False)
    components = RecipeComponentInputSerializer(many=True, allow_empty=False)


class PriceVersionCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.00"))
    currency = serializers.CharField(max_length=3, required=False, default="MXN")
    tax_included = serializers.BooleanField(required=False, default=True)
    effective_at = serializers.DateTimeField(required=False)


class ReceiptLineSerializer(serializers.Serializer):
    component_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))
    unit = serializers.CharField(max_length=20, required=False, default="")
    unit_cost = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000000"))


class ReceiptCreateSerializer(serializers.Serializer):
    occurred_at = serializers.DateTimeField(required=False)
    reference = serializers.CharField(max_length=100, required=False, default="")
    reason = serializers.CharField(required=False, default="")
    lines = ReceiptLineSerializer(many=True, allow_empty=False)


class DiscountSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["AMOUNT", "PERCENT"])
    value = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.00"))


class SaleCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))
    unit = serializers.CharField(max_length=20, required=False, default="pza")
    price_override = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    discount = DiscountSerializer(required=False)
    from_subinventory = serializers.BooleanField(required=False, default=False)


class AddBottlesSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))


class ReturnCreateSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))
    refund_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    occurred_at = serializers.DateTimeField(required=False)
    to_subinventory = serializers.BooleanField(required=False, default=None, allow_null=True)


class ExcelSaleQuantityAdjustSerializer(serializers.Serializer):
    target_quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000000"),
        required=True,
        allow_null=False,
    )
    expected_remaining_quantity = serializers.DecimalField(
        max_digits=18,
        decimal_places=6,
        min_value=Decimal("0.000000"),
        required=False,
        allow_null=True,
        default=None,
    )
    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        allow_null=True,
        default="",
    )
    to_subinventory = serializers.BooleanField(
        required=False,
        allow_null=True,
        default=None,
    )
    refund_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=False,
        allow_null=True,
        default=None,
    )

    @staticmethod
    def _validate_finite(value: Decimal | None, field_name: str) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise serializers.ValidationError(f"El campo {field_name} debe ser un número finito.")
        return value

    def validate_target_quantity(self, value: Decimal) -> Decimal:
        return self._validate_finite(value, "target_quantity")

    def validate_expected_remaining_quantity(self, value: Decimal | None) -> Decimal | None:
        return self._validate_finite(value, "expected_remaining_quantity")

    def validate_refund_amount(self, value: Decimal | None) -> Decimal | None:
        return self._validate_finite(value, "refund_amount")

    def validate_reason(self, value: str | None) -> str:
        return (value or "").strip()


# ---------------------------------------------------------------------------
# P-09: Serializadores de Importación Inicial
# ---------------------------------------------------------------------------

class InitialImportQuantityInputSerializer(serializers.Serializer):
    component_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=18, decimal_places=6, min_value=Decimal("0.000001"))
    unit = serializers.CharField(max_length=20, required=False, default="")
    reference_unit_cost = serializers.DecimalField(
        max_digits=18, decimal_places=6, required=False, min_value=Decimal("0.00")
    )


class OpeningExpenseInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    category = serializers.CharField(max_length=50, required=False, default="OTROS")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(max_length=3, required=False, default="MXN")
    occurred_at = serializers.DateTimeField(required=False)


class InitialImportCreateSerializer(serializers.Serializer):
    import_id = serializers.UUIDField()
    quantities = InitialImportQuantityInputSerializer(many=True)
    opening_expenses = OpeningExpenseInputSerializer(many=True, required=False, default=list)


class InitialImportConfirmationSerializer(serializers.Serializer):
    confirmation_id = serializers.UUIDField()
    preview_version = serializers.IntegerField(min_value=1, default=1)
    physical_count = serializers.BooleanField(required=False, default=True)
    verified_costs = serializers.BooleanField(required=False, default=True)
    acknowledged = serializers.BooleanField()

    def validate_acknowledged(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError("Debes confirmar y reconocer la conciliación física de saldos.")
        return value


class InitialImportCorrectionSerializer(serializers.Serializer):
    correction_id = serializers.UUIDField()
    quantities = InitialImportQuantityInputSerializer(many=True)
    opening_expenses = OpeningExpenseInputSerializer(many=True, required=False, default=list)
    reason = serializers.CharField(required=False, default="", allow_blank=True)


# ---------------------------------------------------------------------------
# CONTADOR: Impuestos personalizables por producto
# ---------------------------------------------------------------------------

class StrictCharField(serializers.CharField):
    def to_internal_value(self, data: Any) -> str:
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictIntField(serializers.IntegerField):
    def to_internal_value(self, data: Any) -> int:
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class ProductCustomTaxCreateSerializer(serializers.Serializer):
    name = StrictCharField(max_length=256, trim_whitespace=False, allow_blank=False)
    tax_type = serializers.ChoiceField(choices=CustomTaxType.choices)
    rate_or_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0000"),
    )
    position = StrictIntField(
        min_value=1,
        max_value=3,
        required=False,
        default=None,
        allow_null=True,
    )

    def validate_rate_or_amount(self, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise serializers.ValidationError("La tasa o monto debe ser un número finito.")
        return value


class ProductCustomTaxUpdateSerializer(serializers.Serializer):
    name = StrictCharField(
        max_length=256, trim_whitespace=False, allow_blank=False, required=False
    )
    tax_type = serializers.ChoiceField(choices=CustomTaxType.choices, required=False)
    rate_or_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0000"),
        required=False,
    )
    position = StrictIntField(min_value=1, max_value=3, required=False)

    def validate_rate_or_amount(self, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise serializers.ValidationError("La tasa o monto debe ser un número finito.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not any(k in attrs for k in ("name", "tax_type", "rate_or_amount", "position")):
            raise serializers.ValidationError(
                "Debe proporcionar al menos un campo para actualizar (name, tax_type, rate_or_amount o position)."
            )
        return attrs


# ---------------------------------------------------------------------------
# Gastos (Fase 5A)
# ---------------------------------------------------------------------------

class ExpenseSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = Expense
        fields = [
            "id",
            "event_id",
            "name",
            "amount",
            "category",
            "expense_type",
            "occurred_at",
            "template",
            "device_id",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ExpenseCreateSerializer(serializers.Serializer):
    event_id = serializers.UUIDField(required=False)
    name = StrictCharField(max_length=200, trim_whitespace=True, allow_blank=False)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    category = serializers.CharField(max_length=50, required=False, default="OTROS")
    occurred_at = serializers.DateTimeField(required=False)
    expense_type = serializers.ChoiceField(
        choices=ExpenseType.choices,
        required=False,
        default=ExpenseType.ONE_TIME,
    )

    def validate_amount(self, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= Decimal("0.00"):
            raise serializers.ValidationError("El importe debe ser estrictamente positivo.")
        return value

    def validate_category(self, value: str) -> str:
        clean = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
        return clean or "OTROS"

    def validate_name(self, value: str) -> str:
        clean = unicodedata.normalize("NFKC", str(value or "")).strip()
        if not clean:
            raise serializers.ValidationError("El nombre no puede estar vacío.")
        return clean


class ExpenseUpdateSerializer(serializers.Serializer):
    name = StrictCharField(max_length=200, trim_whitespace=True, allow_blank=False, required=False)
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
    )
    category = serializers.CharField(max_length=50, required=False)
    occurred_at = serializers.DateTimeField(required=False)

    def validate_amount(self, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= Decimal("0.00"):
            raise serializers.ValidationError("El importe debe ser estrictamente positivo.")
        return value

    def validate_category(self, value: str) -> str:
        clean = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
        return clean or "OTROS"

    def validate_name(self, value: str) -> str:
        clean = unicodedata.normalize("NFKC", str(value or "")).strip()
        if not clean:
            raise serializers.ValidationError("El nombre no puede estar vacío.")
        return clean

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not any(k in attrs for k in ("name", "amount", "category", "occurred_at")):
            raise serializers.ValidationError(
                "Debe proporcionar al menos un campo para actualizar (name, amount, category u occurred_at)."
            )
        return attrs


class ExpenseCategoryBreakdownSerializer(serializers.Serializer):
    category = serializers.CharField(max_length=50)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    expense_count = serializers.IntegerField(min_value=0)
    percentage = serializers.DecimalField(max_digits=5, decimal_places=2)


class ExpenseMonthlyDashboardSerializer(serializers.Serializer):
    month = serializers.CharField(max_length=7)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    expense_count = serializers.IntegerField(min_value=0)
    daily_average = serializers.DecimalField(max_digits=14, decimal_places=2)
    category_filter = serializers.CharField(max_length=50, required=False, allow_null=True)
    categories = ExpenseCategoryBreakdownSerializer(many=True)
    items = ExpenseSerializer(many=True)

