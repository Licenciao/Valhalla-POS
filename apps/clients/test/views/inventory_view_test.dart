import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gestion_inventario/api_client.dart';
import 'package:gestion_inventario/data/database_helper.dart';
import 'package:gestion_inventario/models/inventory_models.dart';
import 'package:gestion_inventario/views/inventory_view.dart';

class MockApiClient implements ApiClient {
  @override
  Uri get baseUri => Uri.parse('http://127.0.0.1:8000/api/v1');

  List<Map<String, Object?>> products = [
    {
      'product_id': 1,
      'sku': 'BOT-TEQ-750',
      'name': 'Botella Tequila 750ml',
      'active': true,
      'current_price': '500.00',
      'recipe_version': 1,
    },
    {
      'product_id': 2,
      'sku': 'COCTEL-MARG',
      'name': 'Margarita Clásica',
      'active': false,
      'current_price': '120.00',
      'recipe_version': null,
    },
  ];

  List<Map<String, Object?>> stocks = [
    {
      'component_id': 101,
      'component_code': 'TEQ-BLANCO',
      'component_name': 'Tequila Blanco Granel',
      'unit': 'L',
      'central_unassigned': '15.500000',
      'average_cost': '180.50',
    },
    {
      'component_id': 102,
      'component_code': 'BOTELLA-750',
      'component_name': 'Envase Vidrio 750ml',
      'unit': 'PZA',
      'central_unassigned': '50.000000',
      'average_cost': '25.00',
    },
  ];

  Map<String, Object?>? lastSaleRegistered;
  Map<String, Object?>? lastReturnRegistered;
  bool shouldFailSale = false;
  String failSaleCode = 'INSUFFICIENT_STOCK';
  String failSaleMessage = 'No hay suficiente Tequila Blanco Granel.';

  @override
  Future<Map<String, Object?>> getProducts({String cursor = 'inicio'}) async {
    return {'items': products, 'next_cursor': null};
  }

  @override
  Future<Map<String, Object?>> getStock({String cursor = 'inicio'}) async {
    return {'items': stocks, 'next_cursor': null};
  }

  @override
  Future<Map<String, Object?>> registerSale({
    required int productId,
    required double quantity,
    double? priceOverride,
    Map<String, Object?>? discount,
    String? idempotencyKey,
  }) async {
    if (shouldFailSale) {
      throw ApiException(code: failSaleCode, message: failSaleMessage);
    }
    lastSaleRegistered = {
      'product_id': productId,
      'quantity': quantity,
      'price_override': priceOverride,
      'discount': discount,
      'idempotency_key': idempotencyKey,
    };
    final unitPrice = priceOverride ?? 500.00;
    final discountVal = discount != null ? double.parse(discount['value'] as String) : 0.0;
    final amountPaid = (quantity * unitPrice) - discountVal;
    final tax = amountPaid * 16.0 / 116.0;
    return {
      'sale_id': 99,
      'amount_paid': amountPaid.toStringAsFixed(2),
      'tax': tax.toStringAsFixed(2),
      'status': 'FULFILLED',
    };
  }

  @override
  Future<Map<String, Object?>> registerMultiSale({
    required List<Map<String, Object?>> items,
    String? deviceId,
    bool pending = false,
    String? idempotencyKey,
  }) async {
    if (shouldFailSale) {
      throw ApiException(code: failSaleCode, message: failSaleMessage);
    }
    double totalPaid = 0.0;
    for (final it in items) {
      final qty = double.parse(it['quantity'].toString());
      final unitP = 500.00;
      totalPaid += qty * unitP;
    }
    final tax = totalPaid * 16.0 / 116.0;
    return {
      'purchase_id': idempotencyKey ?? 'purchase-123',
      'sale_id': 99,
      'amount_paid': totalPaid.toStringAsFixed(2),
      'tax': tax.toStringAsFixed(2),
      'total_items': items.length,
      'status': pending ? 'PENDING' : 'FULFILLED',
    };
  }

  @override
  Future<Map<String, Object?>> registerReturn({
    required int saleId,
    required double quantity,
    double? refundAmount,
    String? idempotencyKey,
  }) async {
    lastReturnRegistered = {
      'sale_id': saleId,
      'quantity': quantity,
      'refund_amount': refundAmount,
      'idempotency_key': idempotencyKey,
    };
    return {
      'return_id': 42,
      'sale_id': saleId,
      'quantity_returned': quantity.toStringAsFixed(6),
      'refund_amount': (refundAmount ?? (quantity * 500.00)).toStringAsFixed(2),
      'status': 'CONFIRMED',
    };
  }

  @override
  Future<Map<String, Object?>> checkHealth() async => {'status': 'ok'};
  @override
  Future<void> fetchCsrfToken() async {}
  @override
  Future<void> login({required String username, required String password}) async {}
  @override
  Future<Map<String, Object?>> getSession() async => {'role': 'ADMIN'};
  @override
  Future<void> logout() async {}
  @override
  void close() {}
  @override
  String get hostAndPort => '127.0.0.1:8000';
  @override
  void updateBaseUri(Uri newUri, {bool allowLan = true}) {}
}

class FakeDatabaseHelper implements DatabaseHelper {
  final List<OutboxEvent> _events = [];
  final List<ProductItem> _cachedProducts = [];

  @override
  Future<void> cacheProducts(List<ProductItem> products) async {
    _cachedProducts.clear();
    _cachedProducts.addAll(products);
  }

  @override
  Future<List<ProductItem>> getCachedProducts() async {
    return _cachedProducts;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);

  @override
  Future<void> insertEvent(OutboxEvent event) async {
    _events.add(event);
  }

  @override
  Future<List<OutboxEvent>> pendingEvents() async {
    return _events.where((e) => e.status == OutboxStatus.pending).toList();
  }

  @override
  Future<List<OutboxEvent>> allEvents() async {
    return List.unmodifiable(_events);
  }

  @override
  Future<void> updateEventStatus(
    String eventId,
    OutboxStatus status, {
    int? sequence,
  }) async {
    final idx = _events.indexWhere((e) => e.eventId == eventId);
    if (idx == -1) throw StateError('Evento no encontrado: $eventId');
    final ev = _events[idx];
    _events[idx] = OutboxEvent(
      eventId: ev.eventId,
      type: ev.type,
      payload: ev.payload,
      sequence: sequence ?? ev.sequence,
      status: status,
      createdAt: ev.createdAt,
      deviceId: ev.deviceId,
    );
  }

  @override
  Future<bool> hasConflict() async {
    return _events.any((e) => e.status == OutboxStatus.conflict);
  }

  @override
  Future<void> close() async {}
}

Widget createTestApp({
  required ApiClient apiClient,
  Map<String, Object?>? session,
  VoidCallback? onLogout,
  DatabaseHelper? databaseHelper,
  int initialIndex = 1,
}) {
  return MaterialApp(
    home: InventoryView(
      apiClient: apiClient,
      session: session ?? {'role': 'ADMIN', 'display_name': 'Administrador'},
      onLogout: onLogout ?? () {},
      databaseHelper: databaseHelper ?? FakeDatabaseHelper(),
      initialIndex: initialIndex,
    ),
  );
}

void main() {
  testWidgets('abre por defecto en el menú de Inicio con accesos rápidos', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final mockApi = MockApiClient();
    await tester.pumpWidget(createTestApp(apiClient: mockApi, initialIndex: 0));
    await tester.pumpAndSettle();

    expect(find.text('Valhalla POS'), findsOneWidget);
    expect(find.text('Comienza a Explorar Valhalla'), findsOneWidget);
    expect(find.text('Haz una Venta'), findsOneWidget);
    expect(find.text('Catálogo y Existencias'), findsOneWidget);
  });

  testWidgets('carga y muestra el catálogo de productos y existencias de insumos', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      // Verificación de encabezado y pestañas de navegación
      expect(find.text('Valhalla POS Mobile'), findsOneWidget);
      expect(find.byIcon(Icons.liquor), findsOneWidget);
      expect(find.text('Catálogo y Stock'), findsOneWidget);
      expect(find.text('Nueva Venta'), findsOneWidget);
      expect(find.text('Devoluciones'), findsOneWidget);
      expect(find.text('Outbox / Sync'), findsOneWidget);

      // Verificación de productos
      expect(find.text('Botella Tequila 750ml'), findsOneWidget);
      expect(find.text('Vender'), findsOneWidget);
      expect(find.text('Margarita Clásica'), findsOneWidget);
      expect(find.text('Inactivo'), findsOneWidget);

      // Verificación de existencias
      expect(find.text('ID #101'), findsOneWidget);
      expect(find.text('15.500 L'), findsOneWidget);
      expect(find.text('\$180.50 MXN'), findsOneWidget);
      expect(find.text('ID #102'), findsOneWidget);
      expect(find.text('50.000 PZA'), findsOneWidget);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('filtra productos en el catálogo mediante la barra de búsqueda', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      // Búsqueda específica
      final searchField = find.widgetWithText(TextField, 'Buscar producto por nombre o ID…');
      await tester.enterText(searchField, 'Tequila');
      await tester.pumpAndSettle();

      expect(find.text('Botella Tequila 750ml'), findsOneWidget);
      expect(find.text('Margarita Clásica'), findsNothing);

      // Limpiar búsqueda con texto inexistente
      await tester.enterText(searchField, 'Inexistente');
      await tester.pumpAndSettle();
      expect(find.text('No hay productos que coincidan con la búsqueda.'), findsOneWidget);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('calcula importes e IVA incluido 16/116 en tiempo real', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      // Navegar a la pestaña de venta
      await tester.tap(find.text('Nueva Venta'));
      await tester.pumpAndSettle();

      expect(find.text('Registrar Nueva Venta'), findsOneWidget);

      // Cambiar cantidad a 2 y verificar cálculo
      final quantityField = find.widgetWithText(TextField, 'Cantidad');
      await tester.enterText(quantityField, '2');
      await tester.pumpAndSettle();

      // Cantidad 2 * Precio 500 = Bruto 1000.00, Total 1000.00
      // IVA: 1000 * 16 / 116 = 137.931... -> 137.93
      expect(find.text('\$1000.00 MXN'), findsNWidgets(2)); // Bruto y Total
      expect(find.text('\$137.93 MXN'), findsOneWidget); // IVA incluido

      // Agregar descuento de 100.00
      final discountField = find.widgetWithText(TextField, 'Descuento (\$)');
      await tester.enterText(discountField, '100.00');
      await tester.pumpAndSettle();

      // Bruto 1000.00, Descuento -100.00, Total 900.00
      // IVA: 900 * 16 / 116 = 124.137... -> 124.14
      expect(find.text('\$900.00 MXN'), findsOneWidget); // Total
      expect(find.text('-\$100.00 MXN'), findsOneWidget); // Descuento
      expect(find.text('\$124.14 MXN'), findsOneWidget); // IVA
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('envía venta satisfactoria a la API central en Windows con Idempotency-Key', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Nueva Venta'));
      await tester.pumpAndSettle();

      final quantityField = find.widgetWithText(TextField, 'Cantidad');
      await tester.enterText(quantityField, '3');
      await tester.pumpAndSettle();

      // Enviar venta
      final submitBtn = find.text('Confirmar y Surtir Venta');
      await tester.ensureVisible(submitBtn);
      await tester.tap(submitBtn);
      await tester.pumpAndSettle();

      // Verificar llamada y diálogo
      expect(mockApi.lastSaleRegistered, isNotNull);
      expect(mockApi.lastSaleRegistered!['product_id'], 1);
      expect(mockApi.lastSaleRegistered!['quantity'], 3.0);
      expect(mockApi.lastSaleRegistered!['idempotency_key'], isNotNull);

      expect(find.text('¡Venta Surtida con Éxito!'), findsOneWidget);
      expect(find.textContaining('Venta #99 confirmada centralmente.'), findsOneWidget);

      await tester.tap(find.text('Aceptar'));
      await tester.pumpAndSettle();
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('muestra error mapeado cuando la API rechaza por stock insuficiente', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      mockApi.shouldFailSale = true;
      mockApi.failSaleCode = 'INSUFFICIENT_STOCK';
      mockApi.failSaleMessage = 'No hay suficiente Tequila Blanco Granel para surtir.';

      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Nueva Venta'));
      await tester.pumpAndSettle();

      final submitBtn = find.text('Confirmar y Surtir Venta');
      await tester.ensureVisible(submitBtn);
      await tester.tap(submitBtn);
      await tester.pumpAndSettle();

      // Se muestra la notificación de error
      expect(
        find.textContaining('INSUFFICIENT_STOCK: No hay suficiente Tequila Blanco Granel para surtir.'),
        findsOneWidget,
      );
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('devolución requiere confirmación explícita de recepción física de insumos', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final mockApi = MockApiClient();
      await tester.pumpWidget(createTestApp(apiClient: mockApi));
      await tester.pumpAndSettle();

      // Ir a la pestaña de Devoluciones
      await tester.tap(find.text('Devoluciones'));
      await tester.pumpAndSettle();

      final saleIdField = find.widgetWithText(TextField, 'Número de Venta (ID)');
      await tester.enterText(saleIdField, '99');
      await tester.pumpAndSettle();

      // Intentar enviar sin marcar el checkbox
      final submitReturnBtn = find.text('Procesar y Reingresar Devolución');
      await tester.ensureVisible(submitReturnBtn);
      await tester.tap(submitReturnBtn);
      await tester.pumpAndSettle();

      expect(
        find.text('Debes confirmar la recepción física completa de los componentes.'),
        findsOneWidget,
      );

      // Marcar checkbox
      final checkbox = find.byType(CheckboxListTile);
      await tester.ensureVisible(checkbox);
      await tester.tap(checkbox);
      await tester.pumpAndSettle();

      // Enviar devolución confirmada
      await tester.ensureVisible(submitReturnBtn);
      await tester.tap(submitReturnBtn);
      await tester.pumpAndSettle();

      expect(mockApi.lastReturnRegistered, isNotNull);
      expect(mockApi.lastReturnRegistered!['sale_id'], 99);
      expect(mockApi.lastReturnRegistered!['quantity'], 1.0);

      expect(find.text('¡Devolución Confirmada!'), findsOneWidget);
      expect(find.textContaining('Devolución #42 registrada exitosamente.'), findsOneWidget);
      expect(find.textContaining('Venta #99'), findsOneWidget);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('en Android offline encola la venta en la outbox local SQLite', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final dbHelper = FakeDatabaseHelper();
      final mockApi = MockApiClient();

      await tester.pumpWidget(createTestApp(
        apiClient: mockApi,
        databaseHelper: dbHelper,
        session: {'role': 'OPERADOR_MOVIL', 'display_name': 'Móvil Local'},
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Nueva Venta'));
      await tester.pumpAndSettle();

      final submitBtn = find.text('Confirmar y Surtir Venta');
      await tester.ensureVisible(submitBtn);
      await tester.tap(submitBtn);
      await tester.pumpAndSettle();

      // Verificación de diálogo local Android
      expect(find.text('Venta Registrada en Outbox Local'), findsOneWidget);
      expect(
        find.textContaining('La venta se sincronizará automáticamente al conectarse por Wi-Fi.'),
        findsOneWidget,
      );

      // Comprobar que en la base de datos local existe el evento PENDING
      final pending = await dbHelper.pendingEvents();
      expect(pending.length, 1);
      expect(pending.first.type, 'sale');
      expect(pending.first.status, OutboxStatus.pending);

      await tester.tap(find.text('Aceptar'));
      await tester.pumpAndSettle();
      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('permite agregar productos al carrito y confirmar venta multi-producto en el APK', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    tester.view.physicalSize = const Size(1200, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final dbHelper = FakeDatabaseHelper();
      final mockApi = MockApiClient();

      await tester.pumpWidget(createTestApp(
        apiClient: mockApi,
        databaseHelper: dbHelper,
        session: {'role': 'OPERADOR_MOVIL', 'display_name': 'Móvil Local'},
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Nueva Venta'));
      await tester.pumpAndSettle();

      // Pulsar "➕ Agregar al Pedido" para agregar el producto seleccionado al carrito
      final addToCartBtn = find.text('➕ Agregar al Pedido');
      await tester.ensureVisible(addToCartBtn);
      await tester.tap(addToCartBtn);
      await tester.pumpAndSettle();

      // Validar que aparece la sección del carrito con el producto agregado
      expect(find.textContaining('🛒 Pedido (1 tipos):'), findsOneWidget);
      expect(find.text('Botella Tequila 750ml'), findsWidgets);

      // Confirmar la compra multi-producto
      final multiSubmitBtn = find.textContaining('Confirmar Compra Multi-Producto (1)');
      await tester.ensureVisible(multiSubmitBtn);
      await tester.tap(multiSubmitBtn);
      await tester.pumpAndSettle();

      // Verificar diálogo de éxito en Android Outbox
      expect(find.text('Compra Multi-Producto en Outbox Local'), findsOneWidget);

      final pending = await dbHelper.pendingEvents();
      expect(pending.length, 1);
      expect(pending.first.status, OutboxStatus.pending);

      await tester.tap(find.text('Aceptar'));
      await tester.pumpAndSettle();
      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('permite usar la APK sin conexion y sincronizar ventas con el servidor Windows', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    tester.view.physicalSize = const Size(1200, 1200);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      final dbHelper = FakeDatabaseHelper();
      final mockApi = MockApiClient();

      // 1. Simular evento de venta previo pendiente en Outbox
      await dbHelper.insertEvent(OutboxEvent(
        eventId: 'evt-offline-01',
        type: 'sale',
        payload: jsonEncode({
          'device_id': 'android-terminal-01',
          'status': 'PENDING',
          'items': [
            {'product_id': 1, 'quantity': '3', 'price_override': '500.00'}
          ],
        }),
        status: OutboxStatus.pending,
        createdAt: DateTime.now().millisecondsSinceEpoch,
      ));

      await tester.pumpWidget(createTestApp(
        apiClient: mockApi,
        databaseHelper: dbHelper,
        session: {'role': 'OPERADOR_MOVIL', 'display_name': 'Móvil Offline'},
      ));
      await tester.pumpAndSettle();

      // Verificar que el badge de sincronización muestra 1 venta pendiente
      expect(find.text('1'), findsWidgets);

      // Ir a la pestaña Outbox / Sync
      await tester.tap(find.text('Outbox / Sync'));
      await tester.pumpAndSettle();

      expect(find.text('Sincronización con Windows'), findsOneWidget);
      expect(find.textContaining('Sincronizar Ventas Ahora (1)'), findsOneWidget);

      // Ejecutar sincronización
      final syncBtn = find.textContaining('Sincronizar Ventas Ahora (1)');
      await tester.ensureVisible(syncBtn);
      await tester.tap(syncBtn);
      await tester.pumpAndSettle();

      // Verificar diálogo de sincronización completada
      expect(find.text('✅ ¡Sincronización Exitosa!'), findsOneWidget);
      expect(find.textContaining('Se sincronizaron 1 venta(s)'), findsOneWidget);

      // Cerrar diálogo
      await tester.tap(find.text('Aceptar'));
      await tester.pumpAndSettle();

      // Verificar que el evento en Outbox pasó a ACK
      final all = await dbHelper.allEvents();
      expect(all.first.status, OutboxStatus.ack);

      final pendingAfter = await dbHelper.pendingEvents();
      expect(pendingAfter.length, 0);

      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });
}
