import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:gestion_inventario/api_client.dart';
import 'package:gestion_inventario/data/database_helper.dart';
import 'package:gestion_inventario/main.dart';
import 'package:gestion_inventario/models/inventory_models.dart';

class _FakeTestApiClient extends ApiClient {
  _FakeTestApiClient() : super(baseUri: ApiBaseUrl.fromEnvironment());
  @override
  Future<Map<String, Object?>> checkHealth() async => {'status': 'healthy'};
  @override
  Future<Map<String, Object?>> getProducts({String cursor = 'inicio'}) async => {
        'items': <Map<String, Object?>>[],
        'next_cursor': null,
      };
  @override
  Future<Map<String, Object?>> getStock({String cursor = 'inicio'}) async => {
        'items': <Map<String, Object?>>[],
        'next_cursor': null,
      };
}

class _FakeTestDatabaseHelper implements DatabaseHelper {
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);

  @override
  Future<List<OutboxEvent>> allEvents() async => [];

  @override
  Future<void> cacheProducts(List<ProductItem> products) async {}
}

void main() {
  testWidgets('shows service failure and the Windows login form', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    try {
      await tester.pumpWidget(MyApp());
      await tester.pumpAndSettle();

      expect(find.text('Servicio no disponible'), findsOneWidget);
      expect(find.textContaining('HTTP_400'), findsOneWidget);
      expect(find.byType(TextField), findsNWidgets(2));
      expect(find.text('Usuario'), findsOneWidget);
      expect(find.text('Contraseña'), findsOneWidget);

      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('abre directamente en modo móvil con sesión outbox en Android', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    try {
      await tester.pumpWidget(MyApp(
        apiClient: _FakeTestApiClient(),
        databaseHelper: _FakeTestDatabaseHelper(),
      ));
      await tester.pumpAndSettle();

      expect(find.text('Inicio'), findsWidgets);
      expect(find.text('Valhalla POS'), findsOneWidget);
      expect(find.byType(TextField), findsNothing);

      await tester.pumpWidget(const SizedBox.shrink());
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });
}
