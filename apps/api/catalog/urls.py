"""
catalog/urls.py

Rutas URL para los endpoints de Catálogo, Recetas, Inventario y Ventas.
Montado bajo /api/v1/ en config/urls.py.
"""

from django.urls import path

from catalog.views import (
    ComponentCreateView,
    ComponentDetailView,
    ComponentsPageView,
    InitialImportConfirmationView,
    InitialImportCorrectionView,
    InitialImportListCreateView,
    InitialImportPreviewView,
    InventoryStockPageView,
    PriceVersionCreateView,
    ProductAddBottlesView,
    ProductCreateView,
    ProductDetailView,
    ProductsPageView,
    ReceiptCreateView,
    RecipeVersionCreateView,
    ReturnCreateView,
    SaleCancelView,
    SaleCreateView,
    SaleDetailView,
    SaleExcelQuantityView,
    SaleFulfillView,
)

urlpatterns = [
    # Productos
    path("products/pages/<str:cursor>", ProductsPageView.as_view(), name="products_page"),
    path("products", ProductCreateView.as_view(), name="product_create"),
    path("products/<int:product_id>", ProductDetailView.as_view(), name="product_detail"),
    path("products/<int:product_id>/recipe-versions", RecipeVersionCreateView.as_view(), name="recipe_version_create"),
    path("products/<int:product_id>/price-versions", PriceVersionCreateView.as_view(), name="price_version_create"),
    path("products/<int:product_id>/add-bottles", ProductAddBottlesView.as_view(), name="product_add_bottles"),

    # Componentes
    path("components/pages/<str:cursor>", ComponentsPageView.as_view(), name="components_page"),
    path("components", ComponentCreateView.as_view(), name="component_create"),
    path("components/<int:component_id>", ComponentDetailView.as_view(), name="component_detail"),

    # Inventario
    path("inventory/stock/pages/<str:cursor>", InventoryStockPageView.as_view(), name="inventory_stock_page"),
    path("inventory/receipts", ReceiptCreateView.as_view(), name="receipt_create"),

    # Ventas y Devoluciones
    path("sales", SaleCreateView.as_view(), name="sale_create"),
    path("sales/<int:sale_id>", SaleDetailView.as_view(), name="sale_detail"),
    path("sales/<int:sale_id>/fulfill", SaleFulfillView.as_view(), name="sale_fulfill"),
    path("sales/<int:sale_id>/cancel", SaleCancelView.as_view(), name="sale_cancel"),
    path("sales/<int:sale_id>/returns", ReturnCreateView.as_view(), name="return_create"),
    path("sales/<int:sale_id>/excel-quantity", SaleExcelQuantityView.as_view(), name="sale_excel_quantity"),

    # P-09: Importación Inicial, Confirmaciones y Correcciones
    path("initial-imports", InitialImportListCreateView.as_view(), name="initial_import_create"),
    path("initial-imports/pages/<str:cursor>", InitialImportListCreateView.as_view(), name="initial_import_page"),
    path("initial-imports/<uuid:import_id>/preview", InitialImportPreviewView.as_view(), name="initial_import_preview"),
    path("initial-imports/<uuid:import_id>/confirmations", InitialImportConfirmationView.as_view(), name="initial_import_confirmation"),
    path("initial-imports/<uuid:import_id>/corrections", InitialImportCorrectionView.as_view(), name="initial_import_correction"),
]
