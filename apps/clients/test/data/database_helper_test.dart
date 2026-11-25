// database_helper_test.dart
//
// T-07 — Pruebas unitarias del esquema de outbox y operaciones de DatabaseHelper.
//
// Usa sqflite_common_ffi para ejecutar SQLite en memoria en entornos
// de CI (Windows/Linux sin dispositivo Android).
//
// Criterios de aceptación verificados:
//   CA-android-01: La base de datos se crea con el esquema correcto (outbox + sync_state).
//   CA-android-02: Los eventos se insertan con status PENDING y UUID único.
//   CA-android-03: pendingEvents() devuelve solo eventos PENDING en orden.
//   CA-android-04: updateEventStatus() transiciona el estado correctamente.
//   CA-android-05: hasConflict() detecta conflictos y bloquea la cola.
//   CA-android-06: sync_state registra y actualiza la última secuencia confirmada.
//   CA-android-07: Insertar el mismo event_id dos veces lanza excepción (idempotencia).

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'package:gestion_inventario/data/database_helper.dart';

/// Fábrica de DatabaseHelper que usa una base de datos FFI en memoria para tests.
/// Evita efectos secundarios en la base de datos de producción.
Future<DatabaseHelper> _buildTestHelper() async {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  final helper = DatabaseHelper.instance;

  // Usar una ruta en memoria única por test para aislamiento total.
  final db = await databaseFactoryFfi.openDatabase(
    inMemoryDatabasePath,
    options: OpenDatabaseOptions(
      version: 2,
      onCreate: (db, version) async {
        // ignore: invalid_use_of_visible_for_testing_member
        await helper.createSchemaForTesting(db, version: version);
      },
    ),
  );

  // ignore: invalid_use_of_visible_for_testing_member
  helper.injectDatabaseForTesting(db);
  return helper;
}

/// Genera un evento de prueba con valores predeterminados.
OutboxEvent _makeEvent({
  String? eventId,
  String type = 'sale',
  String payload = '{"product_id":"abc","qty":1}',
  OutboxStatus status = OutboxStatus.pending,
}) {
  return OutboxEvent(
    eventId: eventId ?? 'evt-${DateTime.now().microsecondsSinceEpoch}',
    type: type,
    payload: payload,
    status: status,
    createdAt: DateTime.now().millisecondsSinceEpoch,
  );
}

void main() {
  late DatabaseHelper helper;

  setUp(() async {
    helper = await _buildTestHelper();
  });

  tearDown(() async {
    await helper.close();
  });

  // ── CA-android-01: Esquema correcto ───────────────────────────────────────
  group('Esquema de base de datos', () {
    test('outbox existe y acepta una inserción válida', () async {
      final event = _makeEvent(eventId: 'evt-schema-01');
      await expectLater(helper.insertEvent(event), completes);
    });

    test('sync_state existe y acepta una actualización', () async {
      await expectLater(
        helper.updateSyncState('device-001', 42),
        completes,
      );
    });
  });

  // ── CA-android-02: Inserción con status PENDING ───────────────────────────
  group('insertEvent()', () {
    test('inserta evento con status PENDING', () async {
      const eventId = 'evt-insert-01';
      await helper.insertEvent(_makeEvent(eventId: eventId));
      final events = await helper.allEvents();
      expect(events, hasLength(1));
      expect(events.first.eventId, eventId);
      expect(events.first.status, OutboxStatus.pending);
    });

    // CA-android-07: Idempotencia — mismo UUID debe fallar
    test('lanza excepción al insertar event_id duplicado', () async {
      const eventId = 'evt-dup-01';
      await helper.insertEvent(_makeEvent(eventId: eventId));
      await expectLater(
        () => helper.insertEvent(_makeEvent(eventId: eventId)),
        throwsA(isA<DatabaseException>()),
      );
      // La outbox solo tiene un evento; no se duplicó.
      expect(await helper.allEvents(), hasLength(1));
    });
  });

  // ── CA-android-03: pendingEvents() ───────────────────────────────────────
  group('pendingEvents()', () {
    test('devuelve solo eventos PENDING en orden de created_at', () async {
      final t0 = DateTime.now().millisecondsSinceEpoch;
      await helper.insertEvent(
        OutboxEvent(
          eventId: 'evt-pend-01',
          type: 'entry',
          payload: '{}',
          status: OutboxStatus.pending,
          createdAt: t0,
        ),
      );
      await helper.insertEvent(
        OutboxEvent(
          eventId: 'evt-ack-01',
          type: 'entry',
          payload: '{}',
          status: OutboxStatus.ack,
          createdAt: t0 + 1,
        ),
      );
      await helper.insertEvent(
        OutboxEvent(
          eventId: 'evt-pend-02',
          type: 'sale',
          payload: '{}',
          status: OutboxStatus.pending,
          createdAt: t0 + 2,
        ),
      );

      final pending = await helper.pendingEvents();
      expect(pending, hasLength(2));
      expect(pending.map((e) => e.eventId).toList(), [
        'evt-pend-01',
        'evt-pend-02',
      ]);
    });

    test('devuelve lista vacía si no hay eventos pendientes', () async {
      final pending = await helper.pendingEvents();
      expect(pending, isEmpty);
    });
  });

  // ── CA-android-04: updateEventStatus() ───────────────────────────────────
  group('updateEventStatus()', () {
    test('transiciona PENDING → SENT → ACK con secuencia', () async {
      const eventId = 'evt-transition-01';
      await helper.insertEvent(_makeEvent(eventId: eventId));

      await helper.updateEventStatus(eventId, OutboxStatus.sent);
      var event = (await helper.allEvents()).first;
      expect(event.status, OutboxStatus.sent);
      expect(event.sequence, isNull);

      await helper.updateEventStatus(eventId, OutboxStatus.ack, sequence: 7);
      event = (await helper.allEvents()).first;
      expect(event.status, OutboxStatus.ack);
      expect(event.sequence, 7);
    });

    test('lanza StateError al actualizar event_id inexistente', () async {
      await expectLater(
        () => helper.updateEventStatus('no-existe', OutboxStatus.ack),
        throwsA(isA<StateError>()),
      );
    });
  });

  // ── CA-android-05: hasConflict() ─────────────────────────────────────────
  group('hasConflict()', () {
    test('devuelve false sin eventos en conflicto', () async {
      await helper.insertEvent(_makeEvent(eventId: 'evt-ok-01'));
      expect(await helper.hasConflict(), isFalse);
    });

    test('devuelve true cuando hay un evento en CONFLICT', () async {
      const eventId = 'evt-conflict-01';
      await helper.insertEvent(_makeEvent(eventId: eventId));
      await helper.updateEventStatus(eventId, OutboxStatus.conflict);
      expect(await helper.hasConflict(), isTrue);
    });

    test('vuelve a false después de resolver el conflicto', () async {
      const eventId = 'evt-conflict-02';
      await helper.insertEvent(_makeEvent(eventId: eventId));
      await helper.updateEventStatus(eventId, OutboxStatus.conflict);
      expect(await helper.hasConflict(), isTrue);

      // Resolución administrativa: ACK terminal de rechazo.
      await helper.updateEventStatus(eventId, OutboxStatus.rejected,
          sequence: 3);
      expect(await helper.hasConflict(), isFalse);
    });
  });

  // ── CA-android-06: sync_state ────────────────────────────────────────────
  group('sync_state', () {
    test('lastSyncSeq devuelve 0 para dispositivo nuevo', () async {
      expect(await helper.lastSyncSeq('device-nuevo'), 0);
    });

    test('updateSyncState registra y luego actualiza la secuencia', () async {
      await helper.updateSyncState('device-001', 10);
      expect(await helper.lastSyncSeq('device-001'), 10);

      await helper.updateSyncState('device-001', 25);
      expect(await helper.lastSyncSeq('device-001'), 25);
    });

    test('dispositivos distintos tienen secuencias independientes', () async {
      await helper.updateSyncState('device-A', 100);
      await helper.updateSyncState('device-B', 5);
      expect(await helper.lastSyncSeq('device-A'), 100);
      expect(await helper.lastSyncSeq('device-B'), 5);
    });
  });

  // ── MI-5: app_settings y migración v1 -> v2 ───────────────────────────────
  group('app_settings y migración v1 -> v2', () {
    test('setSetting y getSetting persisten y actualizan ajustes locales', () async {
      expect(await helper.getSetting('clave_inexistente'), isNull);

      await helper.setSetting('theme_mode', 'light');
      expect(await helper.getSetting('theme_mode'), 'light');

      await helper.setSetting('theme_mode', 'dark');
      expect(await helper.getSetting('theme_mode'), 'dark');
    });

    test('saveServerUri y getSavedServerUri validan y persisten URIs LAN/loopback', () async {
      expect(await helper.getSavedServerUri(), isNull);

      final lanUri = Uri.parse('http://192.168.1.70:8000/api/v1/');
      await helper.saveServerUri(lanUri);
      expect(
        (await helper.getSavedServerUri()).toString(),
        'http://192.168.1.70:8000/api/v1',
      );

      final loopbackHttps = Uri.parse('https://127.0.0.1:8443/api/v1');
      await helper.saveServerUri(loopbackHttps);
      expect(
        (await helper.getSavedServerUri()).toString(),
        'https://127.0.0.1:8443/api/v1',
      );

      expect(
        () => helper.saveServerUri(Uri.parse('https://example.com/api/v1')),
        throwsArgumentError,
      );
    });

    test('getSavedServerUri devuelve null si el valor almacenado está vacío o corrupto', () async {
      await helper.setSetting('server_base_uri', '   ');
      expect(await helper.getSavedServerUri(), isNull);

      await helper.setSetting('server_base_uri', 'https://public-host.example/api/v1');
      expect(await helper.getSavedServerUri(), isNull);

      await helper.setSetting('server_base_uri', '://uri-invalida');
      expect(await helper.getSavedServerUri(), isNull);
    });

    test('migra base de datos v1 a v2 creando app_settings sin perder outbox', () async {
      await helper.close();

      final dbV1 = await databaseFactoryFfi.openDatabase(
        inMemoryDatabasePath,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: (db, version) async {
            await db.execute('''
              CREATE TABLE outbox (
                event_id    TEXT    PRIMARY KEY NOT NULL,
                type        TEXT    NOT NULL,
                payload     TEXT    NOT NULL,
                sequence    INTEGER,
                status      TEXT    NOT NULL DEFAULT 'PENDING',
                created_at  INTEGER NOT NULL,
                device_id   TEXT
              )
            ''');
            await db.execute('''
              CREATE INDEX idx_outbox_status ON outbox (status)
            ''');
            await db.execute('''
              CREATE TABLE sync_state (
                device_id   TEXT    PRIMARY KEY NOT NULL,
                last_seq    INTEGER NOT NULL DEFAULT 0,
                updated_at  INTEGER NOT NULL
              )
            ''');
          },
        ),
      );

      await dbV1.insert('outbox', {
        'event_id': 'evt-v1-legacy',
        'type': 'sale',
        'payload': '{"items":[]}',
        'status': 'PENDING',
        'created_at': 123456,
      });
      await dbV1.insert('sync_state', {
        'device_id': 'dev-v1',
        'last_seq': 9,
        'updated_at': 123456,
      });

      // ignore: invalid_use_of_visible_for_testing_member
      await helper.runUpgradeForTesting(dbV1, 1, 2);
      // ignore: invalid_use_of_visible_for_testing_member
      helper.injectDatabaseForTesting(dbV1);

      final events = await helper.allEvents();
      expect(events, hasLength(1));
      expect(events.first.eventId, 'evt-v1-legacy');
      expect(await helper.lastSyncSeq('dev-v1'), 9);

      final serverUri = Uri.parse('http://192.168.1.99:8000/api/v1');
      await helper.saveServerUri(serverUri);
      expect((await helper.getSavedServerUri()).toString(), serverUri.toString());
    });
  });
}
