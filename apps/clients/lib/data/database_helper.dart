// database_helper.dart
//
// T-06 — Inicialización y esquema de la base de datos SQLite local para Android.
//
// Según §7 del pseudocódigo aprobado:
//   • Cada evento Android tiene un UUID único (event_id).
//   • La outbox retiene eventos hasta recibir ACK del servidor.
//   • Un conflicto pausa la cola (status = CONFLICT) hasta resolución.
//   • La sync_state registra la última secuencia confirmada por device.
//
// En P-07 se añadirá cifrado (sqlcipher) y la llave de Android Keystore.
// En este incremento (P-05) se establece el esquema y la API de acceso.

import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart' as sqflite;
import 'package:sqflite/sqflite.dart';

import '../models/inventory_models.dart';

/// Versión actual del esquema de la base de datos local.
const int _kDbVersion = 1;

/// Nombre del archivo de base de datos SQLite local.
const String _kDbFileName = 'inventario_local.db';

/// Estados posibles de un evento en la outbox.
enum OutboxStatus {
  /// Creado localmente, aún no enviado al servidor.
  pending,

  /// Enviado al servidor; esperando ACK.
  sent,

  /// ACK recibido; operación confirmada centralmente.
  ack,

  /// El servidor reportó un conflicto; cola pausada hasta resolución.
  conflict,

  /// ACK terminal de rechazo; el evento no se aplicó oficialmente.
  rejected,
}

/// Extiende [OutboxStatus] con helpers de serialización para SQLite.
extension OutboxStatusX on OutboxStatus {
  String get value => name.toUpperCase();

  static OutboxStatus fromString(String s) {
    return OutboxStatus.values.firstWhere(
      (e) => e.name.toUpperCase() == s.toUpperCase(),
      orElse: () => throw ArgumentError('Estado de outbox desconocido: $s'),
    );
  }
}

/// Representa un evento pendiente de sincronización en la outbox local.
class OutboxEvent {
  const OutboxEvent({
    required this.eventId,
    required this.type,
    required this.payload,
    required this.status,
    required this.createdAt,
    this.sequence,
    this.deviceId,
  });

  /// UUID del evento (PK). Generado localmente; idempotente en el servidor.
  final String eventId;

  /// Tipo de evento (p.ej. 'sale', 'entry', 'return', 'expense').
  final String type;

  /// Payload JSON serializado del evento.
  final String payload;

  /// Secuencia asignada por el servidor (null hasta recibir ACK).
  final int? sequence;

  /// Estado actual del evento en la outbox.
  final OutboxStatus status;

  /// Epoch en milisegundos UTC en que se creó el evento localmente.
  final int createdAt;

  /// Identificador del dispositivo que generó el evento (null = este dispositivo).
  final String? deviceId;

  Map<String, Object?> toMap() => {
        'event_id': eventId,
        'type': type,
        'payload': payload,
        'sequence': sequence,
        'status': status.value,
        'created_at': createdAt,
        'device_id': deviceId,
      };

  factory OutboxEvent.fromMap(Map<String, Object?> m) => OutboxEvent(
        eventId: m['event_id'] as String,
        type: m['type'] as String,
        payload: m['payload'] as String,
        sequence: m['sequence'] as int?,
        status: OutboxStatusX.fromString(m['status'] as String),
        createdAt: m['created_at'] as int,
        deviceId: m['device_id'] as String?,
      );
}

/// Singleton que gestiona la base de datos SQLite local del cliente Android.
///
/// En P-07 esta clase se extenderá para usar SQLCipher con la clave
/// del Android Keystore. Por ahora usa SQLite estándar para análisis y tests.
class DatabaseHelper {
  DatabaseHelper._();

  static final DatabaseHelper instance = DatabaseHelper._();

  Database? _db;

  /// Abre (o crea) la base de datos local con el esquema vigente.
  Future<Database> get database async {
    _db ??= await _openDatabase();
    return _db!;
  }

  Future<Database> _openDatabase() async {
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, _kDbFileName);
    return openDatabase(
      path,
      version: _kDbVersion,
      onCreate: _onCreate,
      onUpgrade: _onUpgrade,
    );
  }

  /// Inicializa el esquema completo en una base de datos nueva.
  Future<void> _onCreate(Database db, int version) async {
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

    // Índice para acceso eficiente por status (cola de envío).
    await db.execute('''
      CREATE INDEX idx_outbox_status ON outbox (status)
    ''');

    // Tabla de estado de sincronización por dispositivo.
    // Registra la última secuencia ACK confirmada por el servidor.
    await db.execute('''
      CREATE TABLE sync_state (
        device_id   TEXT    PRIMARY KEY NOT NULL,
        last_seq    INTEGER NOT NULL DEFAULT 0,
        updated_at  INTEGER NOT NULL
      )
    ''');
  }

  /// Gestiona migraciones incrementales del esquema.
  Future<void> _onUpgrade(Database db, int oldVersion, int newVersion) async {
    // Las migraciones futuras se añadirán aquí como bloques if (oldVersion < X).
    // Ejemplo: if (oldVersion < 2) { await db.execute('ALTER TABLE ...'); }
  }

  // ── Operaciones de outbox ──────────────────────────────────────────────────

  /// Inserta un evento en la outbox local.
  /// Lanza [DatabaseException] si el [eventId] ya existe (garantía de unicidad).
  Future<void> insertEvent(OutboxEvent event) async {
    final db = await database;
    await db.insert(
      'outbox',
      event.toMap(),
      conflictAlgorithm: ConflictAlgorithm.fail,
    );
  }

  /// Devuelve los eventos en estado [OutboxStatus.pending] en orden de creación.
  /// Solo los eventos PENDING se incluyen en la cola activa de envío.
  Future<List<OutboxEvent>> pendingEvents() async {
    final db = await database;
    final rows = await db.query(
      'outbox',
      where: 'status = ?',
      whereArgs: [OutboxStatus.pending.value],
      orderBy: 'created_at ASC',
    );
    return rows.map(OutboxEvent.fromMap).toList();
  }

  /// Devuelve todos los eventos sin filtro de estado (para diagnóstico/auditoría).
  Future<List<OutboxEvent>> allEvents() async {
    final db = await database;
    final rows = await db.query('outbox', orderBy: 'created_at ASC');
    return rows.map(OutboxEvent.fromMap).toList();
  }

  /// Actualiza el estado de un evento y, opcionalmente, su secuencia.
  /// Debe llamarse atómicamente al recibir ACK o al detectar conflicto.
  Future<void> updateEventStatus(
    String eventId,
    OutboxStatus status, {
    int? sequence,
  }) async {
    final db = await database;
    final values = <String, Object?>{
      'status': status.value,
    };
    if (sequence != null) {
      values['sequence'] = sequence;
    }
    final count = await db.update(
      'outbox',
      values,
      where: 'event_id = ?',
      whereArgs: [eventId],
    );
    if (count == 0) {
      throw StateError('Evento no encontrado en outbox: $eventId');
    }
  }

  /// Devuelve true si hay algún evento en estado CONFLICT.
  /// Cuando hay conflicto, la cola se pausa hasta resolución administrativa.
  Future<bool> hasConflict() async {
    final db = await database;
    final count = Sqflite.firstIntValue(
      await db.rawQuery(
        "SELECT COUNT(*) FROM outbox WHERE status = ?",
        [OutboxStatus.conflict.value],
      ),
    );
    return (count ?? 0) > 0;
  }

  // ── Estado de sincronización ───────────────────────────────────────────────

  /// Registra o actualiza la última secuencia ACK confirmada para un dispositivo.
  Future<void> updateSyncState(String deviceId, int lastSeq) async {
    final db = await database;
    final nowMs = DateTime.now().millisecondsSinceEpoch;
    await db.insert(
      'sync_state',
      {'device_id': deviceId, 'last_seq': lastSeq, 'updated_at': nowMs},
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// Devuelve la última secuencia confirmada para un dispositivo, o 0 si no existe.
  Future<int> lastSyncSeq(String deviceId) async {
    final db = await database;
    final rows = await db.query(
      'sync_state',
      columns: ['last_seq'],
      where: 'device_id = ?',
      whereArgs: [deviceId],
      limit: 1,
    );
    if (rows.isEmpty) return 0;
    return rows.first['last_seq'] as int;
  }

  // ── Caché Local de Catálogo para Modo Sin Conexión ────────────────────────

  /// Almacena los productos obtenidos del servidor en SQLite para permitir su uso offline.
  Future<void> cacheProducts(List<ProductItem> products) async {
    final db = await database;
    await db.execute('''
      CREATE TABLE IF NOT EXISTS cached_products (
        product_id    INTEGER PRIMARY KEY NOT NULL,
        name          TEXT    NOT NULL,
        sku           TEXT    NOT NULL,
        current_price REAL,
        active        INTEGER NOT NULL DEFAULT 1,
        description   TEXT
      )
    ''');
    final batch = db.batch();
    for (final p in products) {
      batch.insert(
        'cached_products',
        {
          'product_id': p.id,
          'name': p.name,
          'sku': p.sku,
          'current_price': p.currentPrice,
          'active': p.active ? 1 : 0,
          'description': p.description,
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );
    }
    await batch.commit(noResult: true);
  }

  /// Recupera los productos cacheados para trabajar 100% sin conexión.
  /// Si aún no se ha conectado nunca a la PC, proporciona un catálogo semilla predeterminado.
  Future<List<ProductItem>> getCachedProducts() async {
    final db = await database;
    await db.execute('''
      CREATE TABLE IF NOT EXISTS cached_products (
        product_id    INTEGER PRIMARY KEY NOT NULL,
        name          TEXT    NOT NULL,
        sku           TEXT    NOT NULL,
        current_price REAL,
        active        INTEGER NOT NULL DEFAULT 1,
        description   TEXT
      )
    ''');
    final rows = await db.query('cached_products', orderBy: 'name ASC');
    if (rows.isNotEmpty) {
      return rows.map((r) {
        final rawPrice = r['current_price'];
        return ProductItem(
          id: r['product_id'] as int,
          name: r['name'] as String,
          sku: (r['sku'] as String?) ?? '',
          description: (r['description'] as String?) ?? '',
          active: (r['active'] as int) == 1,
          currentPrice: rawPrice != null ? (rawPrice as num).toDouble() : null,
        );
      }).toList();
    }
    // Catálogo inicial semilla por defecto si la base de datos está vacía y se abre sin conexión
    return const [
      ProductItem(
        id: 1,
        name: 'Valhalla 750',
        sku: 'PROD-VAL-750',
        description: 'Tequila Valhalla Clásico 750ml',
        active: true,
        currentPrice: 500.0,
      ),
      ProductItem(
        id: 2,
        name: 'Valhalla Reposado 750',
        sku: 'PROD-VAL-REP',
        description: 'Tequila Valhalla Reposado 750ml',
        active: true,
        currentPrice: 650.0,
      ),
    ];
  }

  // ── Ciclo de vida ──────────────────────────────────────────────────────────

  /// Cierra la conexión a la base de datos. Llama solo al finalizar la app.
  Future<void> close() async {
    final db = _db;
    if (db != null && db.isOpen) {
      await db.close();
      _db = null;
    }
  }

  /// Elimina completamente la base de datos local.
  /// ⚠ Solo para tests o restauración autorizada — destruye la outbox.
  Future<void> deleteDatabase() async {
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, _kDbFileName);
    await sqflite.deleteDatabase(path);
    _db = null;
  }

  // ── API interna para tests ─────────────────────────────────────────────────

  /// Inyecta una base de datos ya abierta, usada exclusivamente en tests.
  /// No llamar desde código de producción.
  // ignore: invalid_use_of_visible_for_testing_member
  @visibleForTesting
  void injectDatabaseForTesting(Database db) {
    _db = db;
  }
}
