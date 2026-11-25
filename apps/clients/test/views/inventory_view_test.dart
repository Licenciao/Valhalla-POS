import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gestion_inventario/api_client.dart';
import 'package:gestion_inventario/data/database_helper.dart';
import 'package:gestion_inventario/models/inventory_models.dart';
import 'package:gestion_inventario/views/inventory_view.dart';
import 'package:gestion_inventario/widgets/auth_dialogs.dart';

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
  final Map<String, String> _settings = {};

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
  Future<void> setSetting(String key, String value) async {
    _settings[key] = value;
  }

  @override
  Future<String?> getSetting(String key) async {
    return _settings[key];
  }

  @override
  Future<void> saveServerUri(Uri uri) async {
    final validated = ApiBaseUrl.validate(uri, allowLan: true);
    _settings['server_base_uri'] = validated.toString();
  }

  @override
  Future<Uri?> getSavedServerUri() async {
    final raw = _settings['server_base_uri'];
    if (raw == null || raw.trim().isEmpty) return null;
    try {
      return ApiBaseUrl.validate(Uri.parse(raw.trim()), allowLan: true);
    } on Object {
      return null;
    }
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

  group('MI-8 / MI-9: Autenticación, Outbox Sync y Manejo de Errores', () {
    HttpClient createRealHttpClient() {
      return HttpOverrides.runWithHttpOverrides(
        () => HttpClient(),
        _RealHttpOverrides(),
      );
    }

    Future<HttpServer> startTestServer(
      WidgetTester tester,
      Future<void> Function(HttpRequest request) handler,
    ) async {
      return (await tester.runAsync(() async {
        final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
        server.listen((request) async {
          await handler(request);
        });
        return server;
      }))!;
    }

    Future<void> pumpNetwork(WidgetTester tester) async {
      final deadline = DateTime.now().add(const Duration(seconds: 5));
      await tester.pump();
      while (DateTime.now().isBefore(deadline)) {
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 25)),
        );
        await tester.pump();
        if (find.byType(CircularProgressIndicator).evaluate().isEmpty) {
          break;
        }
      }
      await tester.pumpAndSettle(
        const Duration(milliseconds: 100),
        EnginePhase.sendSemanticsUpdate,
        const Duration(seconds: 5),
      );
    }

    testWidgets(
      'Android: 401 muestra Sesión requerida, sync abre LoginDialog y completa POST /sales -> ACK',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        final requests = <String>[];
        String? lastIdempotencyKey;

        final server = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          final cookie = request.headers.value(HttpHeaders.cookieHeader) ?? '';
          final isAuthed = cookie.contains('sessionid=valid-session');
          requests.add('${request.method} $path');

          if (path == '/api/v1/health' && request.method == 'GET') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/auth/csrf' && request.method == 'GET') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'tok-sync'}));
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'POST') {
            final rawBody = await utf8.decoder.bind(request).join();
            final body = jsonDecode(rawBody) as Map<String, dynamic>;
            if (body['username'] == 'cajero' &&
                body['password'] == 'ClaveSegura123!') {
              request.response.headers.add(
                HttpHeaders.setCookieHeader,
                'sessionid=valid-session; HttpOnly',
              );
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'role': 'CAJERO'}));
            } else {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'INVALID_CREDENTIALS',
                  'message': 'Credenciales inválidas.',
                }),
              );
            }
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'GET') {
            if (!isAuthed) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Inicia sesión.',
                }),
              );
            } else {
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'user_id': 2,
                  'display_name': 'Cajero Turno',
                  'role': 'CAJERO',
                }),
              );
            }
          } else if (path == '/api/v1/products/pages/inicio') {
            if (!isAuthed) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sesión requerida.',
                }),
              );
            } else {
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
            }
          } else if (path == '/api/v1/inventory/stock/pages/inicio') {
            if (!isAuthed) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sesión requerida.',
                }),
              );
            } else {
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
            }
          } else if (path == '/api/v1/sales' && request.method == 'POST') {
            await request.drain<void>();
            if (!isAuthed) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sesión requerida.',
                }),
              );
            } else {
              lastIdempotencyKey = request.headers.value('Idempotency-Key');
              request.response.statusCode = HttpStatus.created;
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'sale_id': 101}));
            }
          } else {
            request.response.statusCode = HttpStatus.notFound;
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-http-sale-01',
            type: 'sale',
            payload: jsonEncode({
              'items': [
                {'product_id': 1, 'quantity': '2.000000'},
              ],
            }),
            status: OutboxStatus.pending,
            createdAt: DateTime.now().millisecondsSinceEpoch,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 0,
              session: const {
                'role': 'OPERADOR_MOVIL',
                'display_name': 'Dispositivo Móvil (Offline)',
              },
            ),
          );
          await pumpNetwork(tester);

          // Debe mostrar aviso de sesión requerida, nunca "Servidor Windows Conectado"
          expect(find.text('Sesión de servidor requerida'), findsOneWidget);
          expect(find.text('Servidor Windows Conectado'), findsNothing);
          expect(find.text('Iniciar Sesión'), findsOneWidget);

          // Ir a pestaña Outbox / Sync
          await tester.tap(find.text('Outbox / Sync'));
          await tester.pumpAndSettle();

          expect(find.text('Sesión de servidor requerida'), findsOneWidget);
          expect(find.text('ONLINE'), findsNothing);

          // Iniciar sincronización -> health 200 -> getSession 401 -> abre LoginDialog
          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
          await pumpNetwork(tester);

          expect(find.byType(LoginDialog), findsOneWidget);
          // Verificar que no hay credenciales hardcodeadas precargadas
          final dialogFields = tester.widgetList<TextField>(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.byType(TextField),
            ),
          ).toList();
          expect(dialogFields[0].controller!.text, isEmpty);
          expect(dialogFields[1].controller!.text, isEmpty);

          await tester.enterText(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.widgetWithText(TextField, 'Usuario'),
            ),
            'cajero',
          );
          await tester.enterText(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.widgetWithText(TextField, 'Contraseña'),
            ),
            'ClaveSegura123!',
          );
          await tester.tap(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.text('Ingresar'),
            ),
          );
          await pumpNetwork(tester);

          expect(find.text('✅ ¡Sincronización Exitosa!'), findsOneWidget);
          expect(find.textContaining('Se sincronizaron 1 venta(s)'), findsOneWidget);
          expect(lastIdempotencyKey, 'evt-http-sale-01');
          expect((await dbHelper.allEvents()).first.status, OutboxStatus.ack);

          await tester.tap(find.text('Aceptar'));
          await tester.pumpAndSettle();
          expect(find.text('Cajero Turno (CAJERO)'), findsNothing); // estamos en tab 4
          await tester.tap(find.text('Inicio'));
          await tester.pumpAndSettle();
          expect(find.text('Cajero Turno (CAJERO)'), findsOneWidget);
          expect(find.text('Servidor Windows Conectado'), findsOneWidget);

          expect(
            requests,
            containsAllInOrder([
              'GET /api/v1/health',
              'GET /api/v1/auth/session',
              'GET /api/v1/auth/csrf',
              'POST /api/v1/auth/session',
              'GET /api/v1/auth/csrf',
              'GET /api/v1/auth/session',
              'GET /api/v1/auth/csrf',
              'POST /api/v1/sales',
            ]),
          );

          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: cancelar LoginDialog aborta sync con SnackBar sin diálogo de fallo',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        var salesCalled = 0;
        final server = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          if (path == '/api/v1/health') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/sales') {
            salesCalled++;
            request.response.statusCode = HttpStatus.created;
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'sale_id': 1}));
          } else {
            request.response.statusCode = HttpStatus.unauthorized;
            request.response.headers.contentType = ContentType.json;
            request.response.write(
              jsonEncode({
                'code': 'AUTHENTICATION_REQUIRED',
                'message': 'Sesión requerida.',
              }),
            );
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-cancel-01',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.000000'}),
            status: OutboxStatus.pending,
            createdAt: DateTime.now().millisecondsSinceEpoch,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
          await pumpNetwork(tester);

          expect(find.byType(LoginDialog), findsOneWidget);
          await tester.tap(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.text('Cancelar'),
            ),
          );
          await tester.pumpAndSettle();

          expect(
            find.text('Sincronización cancelada: inicio de sesión requerido.'),
            findsOneWidget,
          );
          expect(find.text('Sin Conexión con Windows'), findsNothing);
          expect(find.text('Sesión o Permisos Requeridos'), findsNothing);
          expect(salesCalled, 0);
          expect((await dbHelper.pendingEvents()).length, 1);

          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: sincroniza evento type == return hacia /sales/<id>/returns y marca ACK',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        String? returnPath;
        String? returnIdempotency;
        Map<String, dynamic>? returnPayload;

        final server = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          if (path == '/api/v1/health') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/auth/session') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN', 'display_name': 'Admin'}));
          } else if (path == '/api/v1/auth/csrf') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'csrf-ret'}));
          } else if (path == '/api/v1/sales/99/returns' &&
              request.method == 'POST') {
            returnPath = path;
            returnIdempotency = request.headers.value('Idempotency-Key');
            final raw = await utf8.decoder.bind(request).join();
            returnPayload = jsonDecode(raw) as Map<String, dynamic>;
            request.response.statusCode = HttpStatus.created;
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'return_id': 77}));
          } else {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-return-99',
            type: 'return',
            payload: jsonEncode({
              'sale_id': 99,
              'quantity': '2.000000',
              'refund_amount': '1000.00',
            }),
            status: OutboxStatus.pending,
            createdAt: DateTime.now().millisecondsSinceEpoch,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
          await pumpNetwork(tester);

          expect(returnPath, '/api/v1/sales/99/returns');
          expect(returnIdempotency, 'evt-return-99');
          expect(returnPayload!['quantity'], '2.000000');
          expect(returnPayload!['refund_amount'], '1000.00');
          expect((await dbHelper.allEvents()).first.status, OutboxStatus.ack);
          expect(
            find.textContaining('Se sincronizaron 0 venta(s) y 1 devolución(es)'),
            findsOneWidget,
          );

          await tester.tap(find.text('Aceptar'));
          await tester.pumpAndSettle();
          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: hasConflict() al inicio de _syncWithServer() detiene antes de enviar nada',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        final syncRequests = <String>[];
        var trackSyncRequests = false;

        final server = await startTestServer(tester, (request) async {
          if (trackSyncRequests) {
            syncRequests.add('${request.method} ${request.uri.path}');
          }
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-conflict-prev',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
            status: OutboxStatus.conflict,
            createdAt: 1000,
          ),
        );
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-pending-after',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
            status: OutboxStatus.pending,
            createdAt: 2000,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          trackSyncRequests = true;
          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
          await pumpNetwork(tester);

          expect(find.text('Cola de Sincronización Pausada'), findsOneWidget);
          expect(syncRequests, isEmpty);

          await tester.tap(find.text('Entendido'));
          await tester.pumpAndSettle();
          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: reintento único en 401 mid-batch reautentica y completa el lote',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        var salePostCount = 0;

        final server = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          final cookie = request.headers.value(HttpHeaders.cookieHeader) ?? '';
          if (path == '/api/v1/health') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/auth/csrf') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'csrf-mid'}));
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'POST') {
            await request.drain<void>();
            request.response.headers.add(
              HttpHeaders.setCookieHeader,
              'sessionid=renewed-session; HttpOnly',
            );
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN'}));
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'GET') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(
              jsonEncode({'role': 'ADMIN', 'display_name': 'Admin'}),
            );
          } else if (path == '/api/v1/sales' && request.method == 'POST') {
            await request.drain<void>();
            salePostCount++;
            // 1ª venta: 201. 2ª venta (intento 1 sin renewed-session): 401. Reintento con renewed-session: 201.
            if (salePostCount == 2 && !cookie.contains('renewed-session')) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sesión expirada.',
                }),
              );
            } else {
              request.response.statusCode = HttpStatus.created;
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'sale_id': 200 + salePostCount}));
            }
          } else {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-mid-1',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
            status: OutboxStatus.pending,
            createdAt: 1000,
          ),
        );
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-mid-2',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '2.0'}),
            status: OutboxStatus.pending,
            createdAt: 2000,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (2)'));
          await pumpNetwork(tester);

          // Debe abrir LoginDialog tras 401 en evt-mid-2
          expect(find.byType(LoginDialog), findsOneWidget);
          await tester.enterText(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.widgetWithText(TextField, 'Usuario'),
            ),
            'admin2',
          );
          await tester.enterText(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.widgetWithText(TextField, 'Contraseña'),
            ),
            'Secret2!',
          );
          await tester.tap(
            find.descendant(
              of: find.byType(LoginDialog),
              matching: find.text('Ingresar'),
            ),
          );
          await pumpNetwork(tester);

          expect(find.text('✅ ¡Sincronización Exitosa!'), findsOneWidget);
          expect(find.textContaining('Se sincronizaron 2 venta(s)'), findsOneWidget);
          expect(salePostCount, 3);
          final events = await dbHelper.allEvents();
          expect(events.every((e) => e.status == OutboxStatus.ack), isTrue);

          await tester.tap(find.text('Aceptar'));
          await tester.pumpAndSettle();
          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: 0 reintentos en 400/404/500 y NETWORK_TIMEOUT; JSON corrupto marca CONFLICT',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        var forcedStatus = HttpStatus.badRequest;
        var postCalls = 0;

        final server = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          if (path == '/api/v1/health') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/auth/session') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN'}));
          } else if (path == '/api/v1/auth/csrf') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'tok'}));
          } else if (path == '/api/v1/sales' && request.method == 'POST') {
            await request.drain<void>();
            postCalls++;
            request.response.statusCode = forcedStatus;
            request.response.headers.contentType = ContentType.json;
            request.response.write(
              jsonEncode({
                'code': 'HTTP_$forcedStatus',
                'message': 'Error forzado $forcedStatus',
              }),
            );
          } else {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-err-01',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
            status: OutboxStatus.pending,
            createdAt: 1000,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          for (final status in [400, 404, 500]) {
            forcedStatus = status;
            final before = postCalls;
            await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
            await pumpNetwork(tester);

            expect(postCalls - before, 1);
            expect(find.byType(LoginDialog), findsNothing);
            expect(find.text('Error al Sincronizar'), findsOneWidget);
            expect(
              (await dbHelper.allEvents()).first.status,
              OutboxStatus.pending,
            );
            await tester.tap(find.text('Cerrar'));
            await tester.pumpAndSettle();
          }

          // Probar JSON corrupto -> marca CONFLICT y pausa eventos posteriores
          final dbCorrupt = FakeDatabaseHelper();
          await dbCorrupt.insertEvent(
            OutboxEvent(
              eventId: 'evt-corrupt-01',
              type: 'sale',
              payload: '{json-invalido',
              status: OutboxStatus.pending,
              createdAt: 1000,
            ),
          );
          await dbCorrupt.insertEvent(
            OutboxEvent(
              eventId: 'evt-after-corrupt',
              type: 'sale',
              payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
              status: OutboxStatus.pending,
              createdAt: 2000,
            ),
          );

          await tester.pumpWidget(const SizedBox.shrink());
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbCorrupt,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          final beforeCorrupt = postCalls;
          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (2)'));
          await pumpNetwork(tester);

          expect(postCalls, beforeCorrupt);
          expect(find.text('Conflicto Detectado en Sincronización'), findsOneWidget);
          final corruptAll = await dbCorrupt.allEvents();
          expect(corruptAll[0].status, OutboxStatus.conflict);
          expect(corruptAll[1].status, OutboxStatus.pending);
          await tester.tap(find.text('Entendido'));
          await tester.pumpAndSettle();

          // Probar NETWORK_TIMEOUT -> 0 reintentos y diálogo Sin Conexión con Windows
          final timeoutApi = _TimeoutSyncApiClient();
          final dbTimeout = FakeDatabaseHelper();
          await dbTimeout.insertEvent(
            OutboxEvent(
              eventId: 'evt-timeout-01',
              type: 'sale',
              payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
              status: OutboxStatus.pending,
              createdAt: 1000,
            ),
          );
          await tester.pumpWidget(const SizedBox.shrink());
          await tester.pumpWidget(
            createTestApp(
              apiClient: timeoutApi,
              databaseHelper: dbTimeout,
              initialIndex: 4,
            ),
          );
          await tester.pumpAndSettle();

          await tester.tap(find.textContaining('Sincronizar Ventas Ahora (1)'));
          await tester.pumpAndSettle();

          expect(timeoutApi.saleCalls, 1);
          expect(find.text('Sin Conexión con Windows'), findsOneWidget);
          await tester.tap(find.text('Cerrar'));
          await tester.pumpAndSettle();

          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Android: cambio de IP persiste en SQLite settings y sincroniza contra nuevo servidor',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.android;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        var server2Sales = 0;

        final server1 = await startTestServer(tester, (request) async {
          request.response.statusCode = HttpStatus.serviceUnavailable;
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'code': 'HTTP_503', 'message': 'Down'}));
          await request.response.close();
        });

        final server2 = await startTestServer(tester, (request) async {
          final path = request.uri.path;
          if (path == '/api/v1/health') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else if (path == '/api/v1/auth/session') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN'}));
          } else if (path == '/api/v1/auth/csrf') {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'tok-2'}));
          } else if (path == '/api/v1/sales' && request.method == 'POST') {
            await request.drain<void>();
            server2Sales++;
            request.response.statusCode = HttpStatus.created;
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'sale_id': 501}));
          } else {
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'items': [], 'next_cursor': null}));
          }
          await request.response.close();
        });

        final apiClient = ApiClient(
          baseUri: Uri.parse('http://127.0.0.1:${server1.port}/api/v1'),
          httpClient: createRealHttpClient(),
        );
        final dbHelper = FakeDatabaseHelper();
        await dbHelper.insertEvent(
          OutboxEvent(
            eventId: 'evt-ip-change-01',
            type: 'sale',
            payload: jsonEncode({'product_id': 1, 'quantity': '1.0'}),
            status: OutboxStatus.pending,
            createdAt: 1000,
          ),
        );

        try {
          await tester.pumpWidget(
            createTestApp(
              apiClient: apiClient,
              databaseHelper: dbHelper,
              initialIndex: 4,
            ),
          );
          await pumpNetwork(tester);

          await tester.tap(find.text('Cambiar IP Servidor'));
          await tester.pumpAndSettle();

          expect(find.byType(ServerConfigDialog), findsOneWidget);
          await tester.enterText(
            find.descendant(
              of: find.byType(ServerConfigDialog),
              matching: find.byType(TextField),
            ),
            '127.0.0.1:${server2.port}',
          );
          await tester.tap(find.text('Conectar y Sincronizar'));
          await pumpNetwork(tester);

          expect(
            (await dbHelper.getSavedServerUri()).toString(),
            'http://127.0.0.1:${server2.port}/api/v1',
          );
          expect(server2Sales, 1);
          expect(find.text('✅ ¡Sincronización Exitosa!'), findsOneWidget);

          await tester.tap(find.text('Aceptar'));
          await tester.pumpAndSettle();
          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          await tester.runAsync(() async {
            apiClient.close();
            await server1.close(force: true);
            await server2.close(force: true);
          });
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );

    testWidgets(
      'Windows 401 no cae a offline aunque tenga FakeDatabaseHelper y muestra Iniciar Sesión en venta',
      (tester) async {
        debugDefaultTargetPlatformOverride = TargetPlatform.windows;
        tester.view.physicalSize = const Size(1200, 1200);
        tester.view.devicePixelRatio = 1.0;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);

        try {
          // 1. 401 al cargar en Windows -> no cae a offline, muestra error y botones Reintentar/Iniciar Sesión
          final unauthApi = _WindowsUnauthApiClient();
          await tester.pumpWidget(
            createTestApp(
              apiClient: unauthApi,
              databaseHelper: FakeDatabaseHelper(),
              initialIndex: 0,
            ),
          );
          await tester.pumpAndSettle();

          expect(find.textContaining('Error al cargar datos:'), findsOneWidget);
          expect(find.text('Reintentar'), findsOneWidget);
          expect(find.text('Iniciar Sesión'), findsOneWidget);
          expect(find.text('Modo Offline Activo'), findsNothing);

          // 2. 401 al confirmar venta en Windows -> SnackBar con acción Iniciar Sesión
          final saleUnauthApi = MockApiClient()
            ..shouldFailSale = true
            ..failSaleCode = 'AUTHENTICATION_REQUIRED'
            ..failSaleMessage = 'Tu sesión expiró.';
          await tester.pumpWidget(const SizedBox.shrink());
          await tester.pumpWidget(
            createTestApp(
              apiClient: saleUnauthApi,
              databaseHelper: FakeDatabaseHelper(),
            ),
          );
          await tester.pumpAndSettle();

          await tester.tap(find.text('Nueva Venta'));
          await tester.pumpAndSettle();

          final submitBtn = find.text('Confirmar y Surtir Venta');
          await tester.ensureVisible(submitBtn);
          await tester.tap(submitBtn);
          await tester.pumpAndSettle();

          expect(
            find.textContaining('AUTHENTICATION_REQUIRED: Tu sesión expiró.'),
            findsOneWidget,
          );
          expect(
            find.descendant(
              of: find.byType(SnackBar),
              matching: find.text('Iniciar Sesión'),
            ),
            findsOneWidget,
          );

          await tester.pumpWidget(const SizedBox.shrink());
        } finally {
          debugDefaultTargetPlatformOverride = null;
        }
      },
    );
  });
}

class _RealHttpOverrides extends HttpOverrides {}

class _TimeoutSyncApiClient extends MockApiClient {
  int saleCalls = 0;

  @override
  Future<Map<String, Object?>> registerMultiSale({
    required List<Map<String, Object?>> items,
    String? deviceId,
    bool pending = false,
    String? idempotencyKey,
  }) async {
    saleCalls++;
    throw const ApiException(
      code: 'NETWORK_TIMEOUT',
      message: 'La API tardó demasiado en responder.',
      retryable: true,
    );
  }
}

class _WindowsUnauthApiClient extends MockApiClient {
  @override
  Future<Map<String, Object?>> getProducts({String cursor = 'inicio'}) async {
    throw const ApiException(
      code: 'AUTHENTICATION_REQUIRED',
      message: 'Sesión requerida.',
      statusCode: 401,
    );
  }
}
