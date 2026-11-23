// inventory_models.dart
//
// Modelos cliente para Catálogo, Insumos, Existencias y Ventas.
// Mapea exactamente las respuestas de /api/v1/products y /api/v1/inventory/stock.

import 'dart:math';

class ProductItem {
  const ProductItem({
    required this.id,
    required this.name,
    required this.sku,
    required this.description,
    required this.active,
    this.currentPrice,
    this.recipeVersion,
    this.bottleStock,
  });

  final int id;
  final String name;
  final String sku;
  final String description;
  final bool active;
  final double? currentPrice;
  final int? recipeVersion;
  final double? bottleStock;

  factory ProductItem.fromJson(Map<String, Object?> json) {
    final rawPrice = json['current_price'];
    final rawRecipe = json['recipe_version'];
    final rawStock = json['bottle_stock'];
    final rawId = json['product_id'] ?? json['id'];
    return ProductItem(
      id: rawId is int ? rawId : int.parse(rawId.toString()),
      name: json['name'] as String,
      sku: (json['sku'] as String?) ?? '',
      description: (json['description'] as String?) ?? '',
      active: (json['active'] as bool?) ?? false,
      currentPrice: rawPrice != null ? double.tryParse(rawPrice.toString()) : null,
      recipeVersion: rawRecipe != null ? int.tryParse(rawRecipe.toString()) : null,
      bottleStock: rawStock != null ? double.tryParse(rawStock.toString()) : null,
    );
  }
}

class StockItem {
  const StockItem({
    required this.componentId,
    this.componentName,
    this.sku,
    required this.centralUnassigned,
    required this.unit,
    this.averageCost,
    required this.pendingCost,
  });

  final int componentId;
  final String? componentName;
  final String? sku;
  final double centralUnassigned;
  final String unit;
  final double? averageCost;
  final double pendingCost;

  factory StockItem.fromJson(Map<String, Object?> json) {
    final rawCentral = json['central_unassigned'];
    final rawAvg = json['average_cost'];
    final rawPending = json['pending_cost'];
    final rawCompId = json['component_id'] ?? json['id'];
    return StockItem(
      componentId: rawCompId is int ? rawCompId : int.parse(rawCompId.toString()),
      componentName: json['component_name'] as String?,
      sku: json['sku'] as String?,
      centralUnassigned: rawCentral != null ? double.tryParse(rawCentral.toString()) ?? 0.0 : 0.0,
      unit: (json['unit'] as String?) ?? '',
      averageCost: rawAvg != null ? double.tryParse(rawAvg.toString()) : null,
      pendingCost: rawPending != null ? double.tryParse(rawPending.toString()) ?? 0.0 : 0.0,
    );
  }
}

String generateClientUuidV4() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // RFC4122 v4
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}
