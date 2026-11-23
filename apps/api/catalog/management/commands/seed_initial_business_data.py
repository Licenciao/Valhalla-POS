"""
apps/api/catalog/management/commands/seed_initial_business_data.py

Comando de aprovisionamiento del inventario inicial real de negocio
basado en docs/plan/08-inventario-inicial.md y especificación 09-pseudocodigo.md.

Ejecuta el flujo oficial P-09:
  1. Alta de componentes básicos (insumos inventariables).
  2. Creación de recetas congeladas y precios vigentes con IVA (16/116).
  3. Carga del lote de importación inicial provisional.
  4. Doble confirmación administrativa en la misma sesión para oficializar saldos en stock central.
  5. Registro de gastos de apertura de uniformes, papelería y auxiliares.
"""

import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import (
    Component,
    ComponentType,
    PriceVersion,
    Product,
    RecipeComponent,
    RecipeVersion,
)
from catalog.services import confirm_initial_import, create_initial_import
from system.constants import ADMIN_ROLE, OPERATOR_GROUP_NAME


class Command(BaseCommand):
    help = "Aprovisiona el catálogo base, recetas, precios e inventario inicial conciliado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Ejecuta la doble confirmación administrativa automática para oficializar el inventario.",
        )

    def handle(self, *args, **options):
        from django.conf import settings
        from django.core.management import call_command

        if settings.DATABASES["default"]["NAME"] == ":memory:":
            call_command("migrate", verbosity=0)

        self.stdout.write(self.style.NOTICE("Iniciando aprovisionamiento de datos iniciales de negocio..."))

        user_model = get_user_model()
        admin_user = user_model.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = user_model.objects.create_superuser(
                username="admin",
                email="admin@valhalla.local",
                password="Admin-password-123!",
            )
            self.stdout.write(self.style.SUCCESS(f"Usuario ADMIN creado: admin / Admin-password-123!"))

        operator_group, _ = Group.objects.get_or_create(name=OPERATOR_GROUP_NAME)
        operator_user = user_model.objects.filter(groups=operator_group).first()
        if not operator_user:
            operator_user = user_model.objects.create_user(
                username="operador",
                password="Operador-password-123!",
            )
            operator_user.groups.add(operator_group)
            self.stdout.write(self.style.SUCCESS(f"Usuario OPERADOR creado: operador / Operador-password-123!"))

        # ── 1. Componentes (docs/plan/08-inventario-inicial.md) ───────────────────
        components_data = [
            {
                "name": "Líquido doble destilado",
                "sku": "LIQ-DOB-01",
                "component_type": ComponentType.LIQUID,
                "base_unit": "L",
                "unit_precision": 3,
                "average_cost": Decimal("80.000000"),
                "initial_qty": Decimal("40.000000"),
            },
            {
                "name": "Líquido un destilado",
                "sku": "LIQ-DEST-01",
                "component_type": ComponentType.LIQUID,
                "base_unit": "L",
                "unit_precision": 3,
                "average_cost": Decimal("80.000000"),
                "initial_qty": Decimal("20.000000"),
            },
            {
                "name": "Botella Insurgentes 750ml",
                "sku": "BOT-INS-750",
                "component_type": ComponentType.CONTAINER,
                "base_unit": "pza",
                "unit_precision": 0,
                "average_cost": Decimal("55.190000"),
                "initial_qty": Decimal("72.000000"),
            },
            {
                "name": "Corcho Insurgente",
                "sku": "COR-INS-01",
                "component_type": ComponentType.CLOSURE,
                "base_unit": "pza",
                "unit_precision": 0,
                "average_cost": Decimal("12.500000"),
                "initial_qty": Decimal("72.000000"),
            },
            {
                "name": "Botella Nórdica 375ml",
                "sku": "BOT-NOR-375",
                "component_type": ComponentType.CONTAINER,
                "base_unit": "pza",
                "unit_precision": 0,
                "average_cost": Decimal("23.300000"),
                "initial_qty": Decimal("12.000000"),
            },
            {
                "name": "Corcho Nórdico",
                "sku": "COR-NOR-01",
                "component_type": ComponentType.CLOSURE,
                "base_unit": "pza",
                "unit_precision": 0,
                "average_cost": Decimal("9.500000"),
                "initial_qty": Decimal("12.000000"),
            },
        ]

        component_map = {}
        for cdata in components_data:
            comp, created = Component.objects.get_or_create(
                name=cdata["name"],
                defaults={
                    "sku": cdata["sku"],
                    "component_type": cdata["component_type"],
                    "base_unit": cdata["base_unit"],
                    "unit_precision": cdata["unit_precision"],
                    "average_cost": cdata["average_cost"],
                    "active": True,
                },
            )
            component_map[comp.name] = (comp, cdata["initial_qty"], cdata["average_cost"])
            status_txt = "creado" if created else "existente"
            self.stdout.write(f"  • Insumo {comp.name} [{status_txt}]")

        # ── 2. Productos, Recetas y Precios ───────────────────────────────────────
        # Producto 1: Valhalla 750 ($500.00 con IVA; 0.750L Líquido doble destilado, 1 Botella Insurgentes, 1 Corcho Insurgente)
        valhalla, _ = Product.objects.get_or_create(
            name="Valhalla 750",
            defaults={"sku": "PROD-VAL-750", "base_unit": "pza", "active": True},
        )
        valhalla_rec, rec_created = RecipeVersion.objects.get_or_create(
            product=valhalla,
            version_number=1,
            defaults={"active": True, "created_by": admin_user},
        )
        if rec_created:
            RecipeComponent.objects.create(
                recipe_version=valhalla_rec,
                component=component_map["Líquido doble destilado"][0],
                quantity=Decimal("0.750000"),
            )
            RecipeComponent.objects.create(
                recipe_version=valhalla_rec,
                component=component_map["Botella Insurgentes 750ml"][0],
                quantity=Decimal("1.000000"),
            )
            RecipeComponent.objects.create(
                recipe_version=valhalla_rec,
                component=component_map["Corcho Insurgente"][0],
                quantity=Decimal("1.000000"),
            )
        PriceVersion.objects.get_or_create(
            product=valhalla,
            version_number=1,
            defaults={
                "price_with_tax": Decimal("500.00"),
                "active": True,
                "created_by": admin_user,
            },
        )
        valhalla.active = True
        valhalla.save()
        self.stdout.write(self.style.SUCCESS("  ✔ Producto activo: Valhalla 750 ($500.00)"))

        # Producto 2: Valhalla Mini 375 ($260.00 con IVA; 0.375L Líquido un destilado, 1 Botella Nórdica, 1 Corcho Nórdico)
        mini, _ = Product.objects.get_or_create(
            name="Valhalla Mini 375",
            defaults={"sku": "PROD-VAL-375", "base_unit": "pza", "active": True},
        )
        mini_rec, mini_rec_created = RecipeVersion.objects.get_or_create(
            product=mini,
            version_number=1,
            defaults={"active": True, "created_by": admin_user},
        )
        if mini_rec_created:
            RecipeComponent.objects.create(
                recipe_version=mini_rec,
                component=component_map["Líquido un destilado"][0],
                quantity=Decimal("0.375000"),
            )
            RecipeComponent.objects.create(
                recipe_version=mini_rec,
                component=component_map["Botella Nórdica 375ml"][0],
                quantity=Decimal("1.000000"),
            )
            RecipeComponent.objects.create(
                recipe_version=mini_rec,
                component=component_map["Corcho Nórdico"][0],
                quantity=Decimal("1.000000"),
            )
        PriceVersion.objects.get_or_create(
            product=mini,
            version_number=1,
            defaults={
                "price_with_tax": Decimal("260.00"),
                "active": True,
                "created_by": admin_user,
            },
        )
        mini.active = True
        mini.save()
        self.stdout.write(self.style.SUCCESS("  ✔ Producto activo: Valhalla Mini 375 ($260.00)"))

        # ── 3. Importación Inicial de Inventario y Gastos de Apertura ──────────────
        import_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
        quantities_payload = [
            {
                "component_id": comp_item[0].id,
                "quantity": str(comp_item[1]),
                "unit_cost": str(comp_item[2]),
            }
            for comp_item in component_map.values()
        ]

        opening_expenses_payload = [
            {"name": "Camisa hombre (Uniforme)", "amount": "1649.00"},
            {"name": "Camisa mujer (Uniforme)", "amount": "1649.00"},
            {"name": "Vestido edecán (Uniforme)", "amount": "150.00"},
            {"name": "Bordados (Uniforme)", "amount": "540.00"},
            {"name": "Etiquetas (5 pliegos)", "amount": "3250.00"},
            {"name": "Etiqueta banda (1.5 pliegos)", "amount": "375.00"},
            {"name": "Tarjetas de presentación", "amount": "840.00"},
            {"name": "Termo adherible 85x70 mm", "amount": "46.00"},
            {"name": "Termo adherible 55x69 mm", "amount": "40.00"},
        ]

        batch = create_initial_import(
            import_id=import_id,
            quantities=quantities_payload,
            opening_expenses=opening_expenses_payload,
            actor=admin_user,
        )
        self.stdout.write(self.style.NOTICE(f"  • Lote de importación inicial #{import_id} registrado ({batch.status})"))

        if options["confirm"]:
            session_key = "initial_seed_admin_session"
            confirm_initial_import(
                import_id=import_id,
                confirmation_id=uuid.uuid4(),
                preview_version=1,
                session_key=session_key,
                actor=admin_user,
            )
            confirm_result = confirm_initial_import(
                import_id=import_id,
                confirmation_id=uuid.uuid4(),
                preview_version=1,
                session_key=session_key,
                actor=admin_user,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✔ Doble confirmación administrativa completada: {confirm_result['status']} (Saldos oficiales en stock)"
                )
            )

        self.stdout.write(self.style.SUCCESS("Aprovisionamiento de datos de negocio completado exitosamente."))
