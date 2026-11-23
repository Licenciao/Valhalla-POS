"""
catalog/views.py

Vistas API DRF para Catálogo, Recetas, Inventario y Ventas.
Cumple con las rutas, permisos y payloads de 11-borrador-contrato-api.md.
"""

import uuid
from decimal import Decimal
from typing import Any

from django.db import models, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import exceptions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from system.authentication import SessionAuthentication
from system.constants import ADMIN_ROLE
from system.permissions import AuthenticatedApplicationUser, get_user_role
from catalog.exceptions import DomainValidationError, ResourceNotFoundError
from catalog.models import (
    Component,
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
    ReturnStatus,
    Sale,
    SaleStatus,
)
from catalog.pagination import decode_cursor, encode_cursor
from catalog.serializers import (
    AddBottlesSerializer,
    ComponentCreateSerializer,
    InitialImportConfirmationSerializer,
    InitialImportCorrectionSerializer,
    InitialImportCreateSerializer,
    PriceVersionCreateSerializer,
    ProductCreateSerializer,
    ReceiptCreateSerializer,
    RecipeVersionCreateSerializer,
    ReturnCreateSerializer,
    SaleCreateSerializer,
)
from catalog.services import (
    add_bottles_to_product,
    add_price_version,
    add_recipe_version,
    calculate_availability,
    confirm_initial_import,
    correct_initial_import,
    create_initial_import,
    create_product,
    create_sale_request,
    fulfill_sale,
    get_component_stock,
    get_initial_import_preview,
    register_receipt,
    register_return,
)

PAGE_SIZE = 50


class IsAdminUserRole(AuthenticatedApplicationUser):
    def has_permission(self, request: Request, view: Any) -> bool:
        if not super().has_permission(request, view):
            return False
        return get_user_role(request.user) == ADMIN_ROLE


def _extract_idempotency_key(request: Request) -> uuid.UUID:
    raw = request.headers.get("Idempotency-Key", "").strip()
    if not raw:
        return uuid.uuid4()
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise DomainValidationError("El header Idempotency-Key debe ser un UUID válido.") from exc


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------

class ProductsPageView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, cursor: str = "inicio") -> Response:
        last_id = decode_cursor(cursor, "products", str(request.user.pk))
        qs = (
            Product.objects.filter(id__gt=last_id)
            .order_by("id")
            .prefetch_related("price_versions", "recipe_versions")[: PAGE_SIZE + 1]
        )
        products = list(qs)
        has_more = len(products) > PAGE_SIZE
        items_slice = products[:PAGE_SIZE]

        items = []
        for p in items_slice:
            curr_price = p.price_versions.filter(active=True).first()
            curr_recipe = p.recipe_versions.filter(active=True).first()

            recipe_comps = []
            if curr_recipe:
                for rc in curr_recipe.components.select_related("component").all():
                    recipe_comps.append({
                        "component_id": rc.component.id,
                        "component_name": rc.component.name,
                        "sku": rc.component.sku,
                        "quantity": str(rc.quantity),
                        "unit": rc.component.base_unit,
                    })

            bottle_stock_dec = p.bottle_stock or Decimal("0.000000")
            items.append({
                "product_id": p.id,
                "name": p.name,
                "sku": p.sku,
                "description": p.description,
                "active": p.active,
                "current_price": str(curr_price.price_with_tax) if curr_price else None,
                "recipe_version": curr_recipe.version_number if curr_recipe else None,
                "bottle_stock": f"{bottle_stock_dec:.0f}",
                "bottle_stock_precise": f"{bottle_stock_dec:.6f}",
                "recipe_components": recipe_comps,
            })

        next_cursor = (
            encode_cursor("products", str(request.user.pk), items_slice[-1].id)
            if has_more and items_slice
            else None
        )
        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


@method_decorator(csrf_protect, name="dispatch")
class ProductCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request) -> Response:
        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            product = create_product(
                name=data["name"],
                sku=data.get("sku", ""),
                description=data.get("description", ""),
                base_unit=data.get("base_unit", "pza"),
                actor=request.user,
            )

            comps_data = data.get("components")
            if comps_data:
                add_recipe_version(
                    product_id=product.id,
                    components_data=comps_data,
                    actor=request.user,
                )

            price_val = data.get("price_with_tax")
            if price_val is not None:
                add_price_version(
                    product_id=product.id,
                    price_with_tax=price_val,
                    actor=request.user,
                )

            product.refresh_from_db()

        return Response(
            {
                "product_id": product.id,
                "name": product.name,
                "sku": product.sku,
                "active": product.active,
                "bottle_stock": f"{(product.bottle_stock or Decimal('0.000000')):.0f}",
                "current_price": str(price_val) if price_val is not None else None,
            },
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_protect, name="dispatch")
class ProductDetailView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, product_id: int) -> Response:
        product = (
            Product.objects.filter(id=product_id)
            .prefetch_related("price_versions", "recipe_versions__components__component")
            .first()
        )
        if product is None:
            raise ResourceNotFoundError(f"Producto {product_id} no existe.")

        curr_price = product.price_versions.filter(active=True).first()
        curr_recipe = product.recipe_versions.filter(active=True).first()

        recipe_comps = []
        if curr_recipe:
            for rc in curr_recipe.components.all():
                recipe_comps.append({
                    "component_id": rc.component.id,
                    "component_name": rc.component.name,
                    "sku": rc.component.sku,
                    "quantity": str(rc.quantity),
                    "unit": rc.component.base_unit,
                })

        bottle_stock_dec = product.bottle_stock or Decimal("0.000000")
        return Response({
            "product_id": product.id,
            "name": product.name,
            "sku": product.sku,
            "description": product.description,
            "base_unit": product.base_unit,
            "active": product.active,
            "current_price": str(curr_price.price_with_tax) if curr_price else None,
            "recipe_version": curr_recipe.version_number if curr_recipe else None,
            "bottle_stock": f"{bottle_stock_dec:.0f}",
            "bottle_stock_precise": f"{bottle_stock_dec:.6f}",
            "recipe_components": recipe_comps,
        })

    def patch(self, request: Request, product_id: int) -> Response:
        return self.put(request, product_id)

    def put(self, request: Request, product_id: int) -> Response:
        if get_user_role(request.user) != ADMIN_ROLE:
            raise exceptions.PermissionDenied("Se requieren privilegios de Administrador para editar productos.")

        with transaction.atomic():
            product = (
                Product.objects.select_for_update()
                .filter(id=product_id)
                .first()
            )
            if product is None:
                raise ResourceNotFoundError(f"Producto {product_id} no existe.")

            data = request.data
            if "name" in data and data["name"] is not None:
                name = str(data["name"]).strip()
                if not name:
                    raise DomainValidationError("El nombre del producto no puede estar vacío.")
                if Product.objects.filter(name=name).exclude(id=product_id).exists():
                    raise DomainValidationError(f"Ya existe otro producto registrado con el nombre '{name}'.")
                product.name = name

            if "sku" in data:
                product.sku = str(data.get("sku") or "").strip()

            if "description" in data:
                product.description = str(data.get("description") or "").strip()

            if "bottle_stock" in data and data["bottle_stock"] is not None:
                try:
                    b_stock = Decimal(str(data["bottle_stock"]))
                except Exception:
                    raise DomainValidationError("La cantidad de botellas debe ser un número válido.")
                if b_stock < Decimal("0"):
                    raise DomainValidationError("La existencia de botellas no puede ser negativa.")
                product.bottle_stock = b_stock

            if "active" in data and data["active"] is not None:
                product.active = bool(data["active"])

            if "price_with_tax" in data and data["price_with_tax"] is not None:
                try:
                    price_val = Decimal(str(data["price_with_tax"]))
                except Exception:
                    raise DomainValidationError("El precio debe ser un número válido.")
                if price_val < Decimal("0"):
                    raise DomainValidationError("El precio no puede ser negativo.")

                curr_price = product.price_versions.filter(active=True).first()
                if curr_price is None or curr_price.price_with_tax != price_val:
                    add_price_version(
                        product_id=product.id,
                        price_with_tax=price_val,
                        actor=request.user,
                    )

            if "components" in data and data["components"] is not None:
                comps_data = data["components"]
                if comps_data:
                    add_recipe_version(
                        product_id=product.id,
                        components_data=comps_data,
                        actor=request.user,
                    )

            product.save()
            product.refresh_from_db()

        curr_price = product.price_versions.filter(active=True).first()
        bottle_stock_dec = product.bottle_stock or Decimal("0.000000")
        return Response({
            "product_id": product.id,
            "name": product.name,
            "sku": product.sku,
            "description": product.description,
            "active": product.active,
            "bottle_stock": f"{bottle_stock_dec:.0f}",
            "bottle_stock_precise": f"{bottle_stock_dec:.6f}",
            "current_price": str(curr_price.price_with_tax) if curr_price else None,
            "message": f"Producto '{product.name}' actualizado exitosamente.",
        }, status=status.HTTP_200_OK)

    def delete(self, request: Request, product_id: int) -> Response:
        if get_user_role(request.user) != ADMIN_ROLE:
            raise exceptions.PermissionDenied("Se requieren privilegios de Administrador para eliminar productos.")

        with transaction.atomic():
            product = (
                Product.objects.select_for_update()
                .filter(id=product_id)
                .first()
            )
            if product is None:
                raise ResourceNotFoundError(f"Producto {product_id} no existe.")

            has_sales = product.sale_lines.exists()

            if has_sales:
                product.active = False
                product.save(update_fields=["active", "updated_at"])
                return Response({
                    "status": "DEACTIVATED",
                    "product_id": product.id,
                    "active": False,
                    "message": f"El producto '{product.name}' tiene ventas históricas registradas. Ha sido desactivado del catálogo para preservar la integridad y trazabilidad fiscal.",
                }, status=status.HTTP_200_OK)
            else:
                product_name = product.name
                product.price_versions.all().delete()
                for rv in product.recipe_versions.all():
                    rv.components.all().delete()
                product.recipe_versions.all().delete()
                product.delete()
                return Response({
                    "status": "DELETED",
                    "product_id": product_id,
                    "message": f"El producto '{product_name}' ha sido eliminado exitosamente del catálogo.",
                }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Componentes
# ---------------------------------------------------------------------------

class ComponentsPageView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, cursor: str = "inicio") -> Response:
        last_id = decode_cursor(cursor, "components", str(request.user.pk))
        qs = Component.objects.filter(id__gt=last_id).order_by("id")[: PAGE_SIZE + 1]
        comps = list(qs)
        has_more = len(comps) > PAGE_SIZE
        items_slice = comps[:PAGE_SIZE]

        items = [
            {
                "component_id": c.id,
                "name": c.name,
                "base_unit": c.base_unit,
                "precision": c.unit_precision,
                "average_cost": str(c.average_cost) if c.average_cost is not None else None,
                "cost_known": c.average_cost is not None,
            }
            for c in items_slice
        ]

        next_cursor = (
            encode_cursor("components", str(request.user.pk), items_slice[-1].id)
            if has_more and items_slice
            else None
        )
        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


@method_decorator(csrf_protect, name="dispatch")
class ComponentCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request) -> Response:
        serializer = ComponentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        initial_stock = data.get("initial_stock", Decimal("0.000000"))
        unit_cost = data.get("unit_cost", Decimal("0.000000"))

        with transaction.atomic():
            comp = Component.objects.create(
                name=data["name"].strip(),
                sku=data.get("sku", "").strip(),
                description=data.get("description", "").strip(),
                component_type=data.get("component_type", "OTHER"),
                base_unit=data["base_unit"],
                unit_precision=data.get("unit_precision", 0),
                average_cost=unit_cost if unit_cost > Decimal("0.000000") else None,
            )

            if initial_stock > Decimal("0.000000"):
                register_receipt(
                    component_id=comp.id,
                    quantity=initial_stock,
                    unit_cost=unit_cost,
                    actor=request.user,
                    reason="Existencia inicial al crear materia prima personalizada",
                    reference_type="custom_component_creation",
                    reference_id=str(comp.id),
                )
                comp.refresh_from_db()

        stock_dec = get_component_stock(comp.id)

        return Response(
            {
                "component_id": comp.id,
                "name": comp.name,
                "sku": comp.sku,
                "base_unit": comp.base_unit,
                "precision": comp.unit_precision,
                "stock": f"{stock_dec:.6f}",
                "average_cost": f"{comp.average_cost:.6f}" if comp.average_cost is not None else None,
            },
            status=status.HTTP_201_CREATED,
        )


@method_decorator(csrf_protect, name="dispatch")
class ComponentDetailView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, component_id: int) -> Response:
        comp = Component.objects.filter(id=component_id).first()
        if comp is None:
            raise ResourceNotFoundError(f"Componente {component_id} no existe.")

        stock_dec = get_component_stock(comp.id)
        return Response({
            "component_id": comp.id,
            "name": comp.name,
            "sku": comp.sku,
            "description": comp.description,
            "component_type": comp.component_type,
            "base_unit": comp.base_unit,
            "unit_precision": comp.unit_precision,
            "stock": f"{stock_dec:.6f}",
            "average_cost": f"{comp.average_cost:.6f}" if comp.average_cost is not None else None,
            "active": comp.active,
        })

    def patch(self, request: Request, component_id: int) -> Response:
        return self.put(request, component_id)

    def put(self, request: Request, component_id: int) -> Response:
        if get_user_role(request.user) != ADMIN_ROLE:
            raise exceptions.PermissionDenied("Se requieren privilegios de Administrador para editar materias primas.")

        with transaction.atomic():
            comp = (
                Component.objects.select_for_update()
                .filter(id=component_id)
                .first()
            )
            if comp is None:
                raise ResourceNotFoundError(f"Componente {component_id} no existe.")

            data = request.data
            if "name" in data and data["name"] is not None:
                name = str(data["name"]).strip()
                if not name:
                    raise DomainValidationError("El nombre del componente no puede estar vacío.")
                if Component.objects.filter(name=name).exclude(id=component_id).exists():
                    raise DomainValidationError(f"Ya existe otra materia prima registrada con el nombre '{name}'.")
                comp.name = name

            if "sku" in data:
                comp.sku = str(data.get("sku") or "").strip()

            if "description" in data:
                comp.description = str(data.get("description") or "").strip()

            if "component_type" in data and data["component_type"]:
                comp.component_type = data["component_type"]

            if "active" in data and data["active"] is not None:
                comp.active = bool(data["active"])

            if "unit_cost" in data or "average_cost" in data:
                cost_val = data.get("unit_cost") if "unit_cost" in data else data.get("average_cost")
                if cost_val is not None:
                    try:
                        cost_dec = Decimal(str(cost_val))
                    except Exception:
                        raise DomainValidationError("El costo debe ser un número válido.")
                    if cost_dec < Decimal("0"):
                        raise DomainValidationError("El costo no puede ser negativo.")
                    comp.average_cost = cost_dec

            # Ajuste de inventario físico si se envía "stock"
            if "stock" in data and data["stock"] is not None:
                try:
                    target_stock = Decimal(str(data["stock"]))
                except Exception:
                    raise DomainValidationError("La cantidad de stock debe ser un número válido.")
                if target_stock < Decimal("0"):
                    raise DomainValidationError("El stock no puede ser negativo.")

                current_stock = get_component_stock(comp.id)
                delta = target_stock - current_stock
                if delta != Decimal("0"):
                    mtype = MovementType.ADJUSTMENT_POS if delta > Decimal("0") else MovementType.ADJUSTMENT_NEG
                    avg_c = comp.average_cost
                    InventoryMovement.objects.create(
                        event_id=uuid.uuid4(),
                        component=comp,
                        movement_type=mtype,
                        quantity_delta=delta,
                        unit_cost=avg_c,
                        average_cost_before=avg_c,
                        average_cost_after=avg_c,
                        status=MovementStatus.CONFIRMED,
                        occurred_at=timezone.now(),
                        reference_type="manual_adjustment",
                        reference_id=str(comp.id),
                        reason=data.get("reason") or "Ajuste manual de inventario físico desde panel administrativo",
                        created_by=request.user,
                    )

            comp.save()
            comp.refresh_from_db()

        stock_dec = get_component_stock(comp.id)
        return Response({
            "component_id": comp.id,
            "name": comp.name,
            "sku": comp.sku,
            "description": comp.description,
            "component_type": comp.component_type,
            "base_unit": comp.base_unit,
            "precision": comp.unit_precision,
            "stock": f"{stock_dec:.6f}",
            "average_cost": f"{comp.average_cost:.6f}" if comp.average_cost is not None else None,
            "active": comp.active,
            "message": f"Materia prima '{comp.name}' actualizada exitosamente.",
        }, status=status.HTTP_200_OK)

    def delete(self, request: Request, component_id: int) -> Response:
        if get_user_role(request.user) != ADMIN_ROLE:
            raise exceptions.PermissionDenied("Se requieren privilegios de Administrador para eliminar materias primas.")

        with transaction.atomic():
            comp = (
                Component.objects.select_for_update()
                .filter(id=component_id)
                .first()
            )
            if comp is None:
                raise ResourceNotFoundError(f"Componente {component_id} no existe.")

            active_recipes = RecipeComponent.objects.filter(
                component=comp,
                recipe_version__active=True,
            ).select_related("recipe_version__product")
            if active_recipes.exists():
                prods_using = list({rc.recipe_version.product.name for rc in active_recipes})
                raise DomainValidationError(
                    f"No se puede eliminar la materia prima '{comp.name}' porque está siendo utilizada en la receta activa de: {', '.join(prods_using)}. "
                    f"Modifique o archive primero las recetas correspondientes."
                )

            has_movements = comp.movements.exists()
            has_recipes = comp.used_in_recipes.exists()

            if has_movements or has_recipes:
                comp.active = False
                comp.save(update_fields=["active", "updated_at"])
                return Response({
                    "status": "DEACTIVATED",
                    "component_id": comp.id,
                    "active": False,
                    "message": f"La materia prima '{comp.name}' cuenta con historial de movimientos o recetas anteriores. Ha sido desactivada para preservar la trazabilidad contable.",
                }, status=status.HTTP_200_OK)
            else:
                comp_name = comp.name
                comp.delete()
                return Response({
                    "status": "DELETED",
                    "component_id": component_id,
                    "message": f"La materia prima '{comp_name}' ha sido eliminada exitosamente.",
                }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Versiones de Receta y Precio
# ---------------------------------------------------------------------------

@method_decorator(csrf_protect, name="dispatch")
class RecipeVersionCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, product_id: int) -> Response:
        serializer = RecipeVersionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        recipe = add_recipe_version(
            product_id=product_id,
            components_data=data["components"],
            actor=request.user,
            effective_from=data.get("effective_at"),
        )
        return Response(
            {
                "recipe_version_id": recipe.id,
                "product_id": product_id,
                "effective_at": recipe.effective_from.isoformat(),
                "components": [
                    {
                        "component_id": rc.component_id,
                        "quantity": str(rc.quantity),
                    }
                    for rc in recipe.components.all()
                ],
                "product_active": recipe.product.active,
            },
            status=status.HTTP_201_CREATED,
        )



@method_decorator(csrf_protect, name="dispatch")
class PriceVersionCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, product_id: int) -> Response:
        serializer = PriceVersionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        price = add_price_version(
            product_id=product_id,
            price_with_tax=data["amount"],
            actor=request.user,
            effective_from=data.get("effective_at"),
        )
        return Response(
            {
                "price_version_id": price.id,
                "product_id": product_id,
                "amount": str(price.price_with_tax),
                "effective_at": price.effective_from.isoformat(),
                "product_active": price.product.active,
            },
            status=status.HTTP_201_CREATED,
        )



# ---------------------------------------------------------------------------
# Existencias e Inventario
# ---------------------------------------------------------------------------

class InventoryStockPageView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, cursor: str = "inicio") -> Response:
        last_id = decode_cursor(cursor, "stock", str(request.user.pk))
        qs = Component.objects.filter(id__gt=last_id).order_by("id")[: PAGE_SIZE + 1]
        comps = list(qs)
        has_more = len(comps) > PAGE_SIZE
        items_slice = comps[:PAGE_SIZE]

        items = []
        for c in items_slice:
            stock = get_component_stock(c.id)
            stock_dec = Decimal(str(stock))
            items.append({
                "component_id": c.id,
                "component_name": c.name,
                "sku": c.sku,
                "central_unassigned": f"{stock_dec:.6f}",
                "provisional": "0.000000",
                "pending_cost": f"{max(Decimal('0'), -stock_dec):.6f}",
                "unit": c.base_unit,
                "average_cost": f"{c.average_cost:.6f}" if c.average_cost is not None else None,
            })

        next_cursor = (
            encode_cursor("stock", str(request.user.pk), items_slice[-1].id)
            if has_more and items_slice
            else None
        )
        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


@method_decorator(csrf_protect, name="dispatch")
class ReceiptCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def post(self, request: Request) -> Response:
        serializer = ReceiptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        movement_ids = []
        balances = {}

        for line in data["lines"]:
            m = register_receipt(
                component_id=line["component_id"],
                quantity=line["quantity"],
                unit_cost=line["unit_cost"],
                reference_type="purchase_receipt",
                reference_id=data.get("reference", ""),
                reason=data.get("reason", ""),
                actor=request.user,
                occurred_at=data.get("occurred_at"),
            )
            movement_ids.append(m.id)
            balances[str(line["component_id"])] = str(get_component_stock(line["component_id"]))

        return Response(
            {
                "receipt_id": str(uuid.uuid4()),
                "movement_ids": movement_ids,
                "resulting_balances": balances,
            },
            status=status.HTTP_201_CREATED,
        )



# ---------------------------------------------------------------------------
# Ventas y Devoluciones
# ---------------------------------------------------------------------------

@method_decorator(csrf_protect, name="dispatch")
class SaleCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request) -> Response:
        limit = min(int(request.query_params.get("limit", 100)), 1000)
        product_id = request.query_params.get("product_id")
        status_param = request.query_params.get("status")
        qs = Sale.objects.select_related(
            "line__product",
            "line__recipe_version",
            "line__price_version"
        )
        if status_param:
            qs = qs.filter(status=status_param.upper())
        else:
            qs = qs.filter(status=SaleStatus.FULFILLED)

        if product_id:
            qs = qs.filter(line__product_id=product_id)

        sales = qs.order_by("-id")[:limit]
        items = []
        for s in sales:
            line = getattr(s, "line", None)
            if not line:
                continue

            components_info = []
            if line.recipe_version:
                for rc in line.recipe_version.components.select_related("component"):
                    components_info.append({
                        "component_id": rc.component.id,
                        "component_name": rc.component.name,
                        "sku": rc.component.sku,
                        "quantity_per_unit": str(rc.quantity),
                        "unit": rc.component.base_unit,
                    })

            previous_returns_sum = (
                Return.objects.filter(
                    sale_line=line,
                    status=ReturnStatus.COMPLETED,
                ).aggregate(total_refund=models.Sum("refund_amount"))["total_refund"]
                or Decimal("0.00")
            )
            max_refund = max(Decimal("0.00"), line.final_amount - previous_returns_sum)

            items.append({
                "sale_id": s.id,
                "status": s.status,
                "device_id": s.device_id,
                "requested_at": s.requested_at.isoformat(),
                "fulfilled_at": s.fulfilled_at.isoformat() if s.fulfilled_at else None,
                "product_id": line.product.id,
                "product_name": line.product.name,
                "product_sku": line.product.sku,
                "quantity": str(line.quantity),
                "unit_price_with_tax": str(line.unit_price_with_tax),
                "discount": str(line.discount),
                "final_amount": str(line.final_amount),
                "tax_amount": str(line.tax_amount or (line.final_amount * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))),
                "already_refunded": str(previous_returns_sum),
                "max_refund_possible": str(max_refund),
                "components": components_info,
            })

        return Response({"sales": items})

    def post(self, request: Request) -> Response:
        is_pending = request.data.get("status") == "PENDING"
        device_id = request.data.get("device_id") or "windows-main"

        # Soporte para compra multi-producto (varios productos de diferentes tipos en un mismo pedido)
        if "items" in request.data:
            items_data = request.data.get("items")
            if not isinstance(items_data, list) or len(items_data) == 0:
                raise exceptions.ValidationError({"items": "Debe incluir al menos un producto en la compra."})

            created_sales = []
            total_amount_paid = Decimal("0.00")
            total_tax = Decimal("0.00")
            total_quantity = Decimal("0.00")

            with transaction.atomic():
                for it in items_data:
                    prod_id = it.get("product_id")
                    if not prod_id:
                        raise exceptions.ValidationError({"product_id": "ID de producto requerido."})
                    qty = Decimal(str(it.get("quantity", "1")))
                    price_override = Decimal(str(it["price_override"])) if it.get("price_override") is not None else None
                    from_subinv = it.get("from_subinventory", True)

                    disc_amt = Decimal("0.00")
                    discount_data = it.get("discount")
                    if discount_data:
                        if isinstance(discount_data, dict):
                            if discount_data.get("type") == "AMOUNT":
                                disc_amt = Decimal(str(discount_data.get("value", 0)))
                            elif discount_data.get("type") == "PERCENT":
                                prod_obj = Product.objects.filter(id=prod_id).first()
                                if prod_obj:
                                    p_ver = prod_obj.price_versions.filter(active=True).first()
                                    unit_p = price_override or (p_ver.price_with_tax if p_ver else Decimal("0"))
                                    gross = qty * unit_p
                                    disc_amt = (gross * Decimal(str(discount_data["value"])) / Decimal("100")).quantize(Decimal("0.01"))
                        elif isinstance(discount_data, (str, int, float, Decimal)):
                            try:
                                disc_amt = Decimal(str(discount_data))
                            except Exception:
                                disc_amt = Decimal("0.00")

                    sale = create_sale_request(
                        product_id=prod_id,
                        quantity=qty,
                        unit_price=price_override,
                        discount=disc_amt,
                        actor=request.user,
                        device_id=device_id,
                    )
                    if from_subinv:
                        sale.line.from_subinventory = True
                        sale.line.save(update_fields=["from_subinventory"])

                    if not is_pending:
                        sale = fulfill_sale(
                            sale_id=sale.id,
                            actor=request.user,
                            device_id=device_id,
                        )

                    line = sale.line
                    line_tax = line.tax_amount if line.tax_amount is not None else (line.final_amount * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))
                    total_amount_paid += line.final_amount
                    total_tax += line_tax
                    total_quantity += line.quantity

                    created_sales.append({
                        "sale_id": sale.id,
                        "product_id": line.product.id,
                        "product_name": line.product.name,
                        "quantity": str(line.quantity),
                        "price_snapshot": str(line.unit_price_with_tax),
                        "discount_snapshot": str(line.discount),
                        "amount_paid": str(line.final_amount),
                        "tax": str(line_tax),
                        "status": sale.status,
                    })

            return Response(
                {
                    "purchase_id": str(uuid.uuid4()),
                    "sale_id": created_sales[0]["sale_id"],
                    "sales": created_sales,
                    "total_items": len(created_sales),
                    "total_quantity": str(total_quantity),
                    "amount_paid": str(total_amount_paid),
                    "tax": str(total_tax),
                    "status": "PENDING" if is_pending else "FULFILLED",
                },
                status=status.HTTP_201_CREATED,
            )

        # Flujo individual original (compatibilidad 100% con tests existentes)
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        discount_data = data.get("discount")
        discount_amt = Decimal("0.00")
        if discount_data:
            disc_type = discount_data["type"]
            disc_val = discount_data["value"]
            if disc_type == "AMOUNT":
                discount_amt = disc_val
            elif disc_type == "PERCENT":
                prod = Product.objects.filter(id=data["product_id"]).first()
                if prod:
                    p_ver = prod.price_versions.filter(active=True).first()
                    unit_p = data.get("price_override") or (p_ver.price_with_tax if p_ver else Decimal("0"))
                    gross = data["quantity"] * unit_p
                    discount_amt = (gross * disc_val / Decimal("100")).quantize(Decimal("0.01"))

        sale = create_sale_request(
            product_id=data["product_id"],
            quantity=data["quantity"],
            unit_price=data.get("price_override"),
            discount=discount_amt,
            actor=request.user,
            device_id=device_id,
        )
        if data.get("from_subinventory", False):
            sale.line.from_subinventory = True
            sale.line.save(update_fields=["from_subinventory"])

        if is_pending:
            line = sale.line
            return Response(
                {
                    "sale_id": sale.id,
                    "status": sale.status,
                    "price_snapshot": str(line.unit_price_with_tax),
                    "discount_snapshot": str(line.discount),
                    "amount_paid": str(line.final_amount),
                    "tax": str(line.tax_amount or (line.final_amount * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))),
                    "cost_snapshot": "0.000000",
                },
                status=status.HTTP_201_CREATED,
            )

        fulfilled_sale = fulfill_sale(
            sale_id=sale.id,
            actor=request.user,
            device_id=device_id,
        )
        line = fulfilled_sale.line
        return Response(
            {
                "sale_id": fulfilled_sale.id,
                "status": fulfilled_sale.status,
                "price_snapshot": str(line.unit_price_with_tax),
                "discount_snapshot": str(line.discount),
                "amount_paid": str(line.final_amount),
                "tax": str(line.tax_amount),
                "cost_snapshot": str(line.cost_applied),
            },
            status=status.HTTP_201_CREATED,
        )



@method_decorator(csrf_protect, name="dispatch")
class ProductAddBottlesView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def post(self, request: Request, product_id: int) -> Response:
        serializer = AddBottlesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        qty = serializer.validated_data["quantity"]

        result = add_bottles_to_product(
            product_id=product_id,
            quantity=qty,
            actor=request.user,
            device_id="windows-main",
        )
        return Response(result, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class SaleDetailView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def get(self, request: Request, sale_id: int) -> Response:
        s = Sale.objects.select_related(
            "line__product",
            "line__recipe_version",
            "line__price_version"
        ).filter(id=sale_id).first()

        if not s or not getattr(s, "line", None):
            return Response({"message": f"Venta {sale_id} no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        line = s.line
        components_info = []
        if line.recipe_version:
            for rc in line.recipe_version.components.select_related("component"):
                components_info.append({
                    "component_id": rc.component.id,
                    "component_name": rc.component.name,
                    "sku": rc.component.sku,
                    "quantity_per_unit": str(rc.quantity),
                    "unit": rc.component.base_unit,
                })

        previous_returns_sum = (
            Return.objects.filter(
                sale_line=line,
                status=ReturnStatus.COMPLETED,
            ).aggregate(total_refund=models.Sum("refund_amount"))["total_refund"]
            or Decimal("0.00")
        )
        max_refund = max(Decimal("0.00"), line.final_amount - previous_returns_sum)

        return Response({
            "sale_id": s.id,
            "status": s.status,
            "requested_at": s.requested_at.isoformat(),
            "product_id": line.product.id,
            "product_name": line.product.name,
            "product_sku": line.product.sku,
            "quantity": str(line.quantity),
            "unit_price_with_tax": str(line.unit_price_with_tax),
            "discount": str(line.discount),
            "final_amount": str(line.final_amount),
            "tax_amount": str(line.tax_amount),
            "already_refunded": str(previous_returns_sum),
            "max_refund_possible": str(max_refund),
            "components": components_info,
        })


@method_decorator(csrf_protect, name="dispatch")
class SaleFulfillView(APIView):
    """
    POST /api/v1/sales/<sale_id>/fulfill
    Acepta y surte una venta pendiente (por ejemplo generada desde un dispositivo Android).
    Descuenta de forma atómica el inventario (subinventario o componentes) y actualiza a FULFILLED.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def post(self, request: Request, sale_id: int) -> Response:
        sale = Sale.objects.select_related("line__product").filter(id=sale_id).first()
        if not sale or not getattr(sale, "line", None):
            return Response({"message": f"Venta {sale_id} no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if sale.status != SaleStatus.PENDING:
            return Response(
                {"message": f"La venta #{sale_id} ya se encuentra en estado {sale.status} (solo se pueden aceptar ventas PENDING)."},
                status=status.HTTP_409_CONFLICT,
            )

        fulfilled_sale = fulfill_sale(
            sale_id=sale.id,
            actor=request.user,
            device_id="windows-main",
        )
        line = fulfilled_sale.line
        return Response(
            {
                "sale_id": fulfilled_sale.id,
                "status": fulfilled_sale.status,
                "device_id": fulfilled_sale.device_id,
                "product_id": line.product.id,
                "product_name": line.product.name,
                "quantity": str(line.quantity),
                "amount_paid": str(line.final_amount),
                "tax": str(line.tax_amount),
                "fulfilled_at": fulfilled_sale.fulfilled_at.isoformat() if fulfilled_sale.fulfilled_at else None,
                "message": f"Venta #{fulfilled_sale.id} aceptada y surtida con éxito en el inventario central.",
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class SaleCancelView(APIView):
    """
    POST /api/v1/sales/<sale_id>/cancel
    Rechaza/cancela una venta pendiente proveniente de un dispositivo móvil Android.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def post(self, request: Request, sale_id: int) -> Response:
        sale = Sale.objects.filter(id=sale_id).first()
        if not sale:
            return Response({"message": f"Venta {sale_id} no encontrada."}, status=status.HTTP_404_NOT_FOUND)

        if sale.status != SaleStatus.PENDING:
            return Response(
                {"message": f"La venta #{sale_id} no se puede rechazar porque ya está en estado {sale.status}."},
                status=status.HTTP_409_CONFLICT,
            )

        reason = request.data.get("reason", "Rechazada por el operador en Windows.")
        sale.status = SaleStatus.CANCELLED
        sale.save(update_fields=["status"])
        return Response(
            {
                "sale_id": sale.id,
                "status": sale.status,
                "reason": reason,
                "message": f"Venta #{sale.id} rechazada correctamente.",
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_protect, name="dispatch")
class ReturnCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [AuthenticatedApplicationUser]

    def post(self, request: Request, sale_id: int) -> Response:
        serializer = ReturnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ret = register_return(
            sale_id=sale_id,
            quantity_to_return=data["quantity"],
            refund_amount=data.get("refund_amount"),
            actor=request.user,
            device_id="windows-main",
            to_subinventory=data.get("to_subinventory"),
        )
        return Response(
            {
                "return_id": ret.id,
                "quantity": str(data["quantity"]),
                "refund": str(ret.refund_amount),
                "tax_reversed": str(ret.tax_reversed),
                "occurred_at": ret.returned_at.isoformat(),
                "product_name": ret.sale_line.product.name,
                "new_bottle_stock": str(int(ret.sale_line.product.bottle_stock or 0)),
            },
            status=status.HTTP_201_CREATED,
        )



# ---------------------------------------------------------------------------
# P-09: Vistas de Importación Inicial, Confirmaciones y Correcciones
# ---------------------------------------------------------------------------

@method_decorator(csrf_protect, name="dispatch")
class InitialImportListCreateView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request) -> Response:
        serializer = InitialImportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        batch = create_initial_import(
            import_id=data["import_id"],
            quantities=data["quantities"],
            opening_expenses=data.get("opening_expenses", []),
            actor=request.user,
        )
        return Response(
            {
                "import_id": str(batch.import_id),
                "status": batch.status,
                "preview_version": batch.preview_version,
            },
            status=status.HTTP_201_CREATED,
        )


    def get(self, request: Request, cursor: str = "inicio") -> Response:
        last_id = decode_cursor(cursor, "initial_imports", str(request.user.pk))
        qs = ImportBatch.objects.all().order_by("-created_at")[: PAGE_SIZE + 1]
        batches = list(qs)
        has_more = len(batches) > PAGE_SIZE
        items_slice = batches[:PAGE_SIZE]

        items = []
        for b in items_slice:
            items.append({
                "import_id": str(b.import_id),
                "status": b.status,
                "preview_version": b.preview_version,
                "created_at": b.created_at.isoformat(),
                "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
                "confirmations_received": 2 if b.status == ImportBatchStatus.CONFIRMED else (1 if b.first_confirmation_id else 0),
            })

        next_cursor = None
        if has_more and items_slice:
            next_cursor = encode_cursor("initial_imports", str(request.user.pk), 1)

        return Response({
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        })


class InitialImportPreviewView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def get(self, request: Request, import_id: uuid.UUID) -> Response:
        preview = get_initial_import_preview(import_id)
        return Response(preview, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class InitialImportConfirmationView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, import_id: uuid.UUID) -> Response:
        serializer = InitialImportConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not request.session.session_key:
            request.session.save()
        session_key = request.session.session_key

        result = confirm_initial_import(
            import_id=import_id,
            confirmation_id=data["confirmation_id"],
            preview_version=data.get("preview_version", 1),
            session_key=session_key,
            actor=request.user,
            physical_count=data.get("physical_count", True),
            verified_costs=data.get("verified_costs", True),
        )
        return Response(result, status=status.HTTP_200_OK)


@method_decorator(csrf_protect, name="dispatch")
class InitialImportCorrectionView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUserRole]

    def post(self, request: Request, import_id: uuid.UUID) -> Response:
        serializer = InitialImportCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = correct_initial_import(
            import_id=import_id,
            correction_id=data["correction_id"],
            quantities=data["quantities"],
            opening_expenses=data.get("opening_expenses", []),
            reason=data.get("reason", ""),
            actor=request.user,
        )
        return Response(result, status=status.HTTP_200_OK)
