import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gestion_inventario/api_client.dart';

void main() {
  group('API base URL policy', () {
    test('uses the loopback development default', () {
      expect(
        ApiBaseUrl.fromEnvironment().toString(),
        'http://127.0.0.1:8000/api/v1',
      );
    });

    test('rejects remote URLs and non-API paths before TLS pinning exists', () {
      expect(
        () => ApiBaseUrl.validate(Uri.parse('http://192.168.1.20/api/v1')),
        throwsArgumentError,
      );
      expect(
        () => ApiBaseUrl.validate(Uri.parse('https://api.local/api/v1')),
        throwsArgumentError,
      );
      expect(
        () => ApiBaseUrl.validate(Uri.parse('https://api.local/health')),
        throwsArgumentError,
      );
    });

    test('allows HTTPS API URLs on loopback', () {
      final uri = ApiBaseUrl.validate(Uri.parse('https://127.0.0.1/api/v1'));
      expect(uri.scheme, 'https');
      expect(uri.host, '127.0.0.1');
    });

    test('fromEnvironment allows LAN URLs while validate defaults to false', () {
      final lanUri = Uri.parse(ApiBaseUrl.defaultLanServerUrl);
      expect(() => ApiBaseUrl.validate(lanUri), throwsArgumentError);
      expect(ApiBaseUrl.validate(lanUri, allowLan: true), lanUri);
      expect(ApiBaseUrl.fromEnvironment(allowLan: true).host, '127.0.0.1');
    });
  });

  test('performs health and the in-memory CSRF session lifecycle', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final requests = <String>[];
    final serverTask = () async {
      await for (final request in server) {
        final path = request.uri.path;
        requests.add('${request.method} $path');

        if (path == '/api/v1/health' && request.method == 'GET') {
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({
              'status': 'ok',
              'api_version': 'v1',
              'instance_id': 'test-instance',
            }),
          );
        } else if (path == '/api/v1/auth/csrf' && request.method == 'GET') {
          final loggedIn = requests.where((item) {
            return item == 'POST /api/v1/auth/session';
          }).isNotEmpty;
          request.response.headers.add(
            HttpHeaders.setCookieHeader,
            loggedIn ? 'csrftoken=csrf-after' : 'csrftoken=csrf-before',
          );
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({'csrf_token': loggedIn ? 'csrf-after' : 'csrf-before'}),
          );
        } else if (path == '/api/v1/auth/session' && request.method == 'POST') {
          expect(request.headers.value('X-CSRFToken'), 'csrf-before');
          expect(
            request.headers.value(HttpHeaders.cookieHeader),
            contains('csrftoken='),
          );
          final requestBytes = await request.fold<List<int>>(
            [],
            (bytes, chunk) => bytes..addAll(chunk),
          );
          final body = jsonDecode(utf8.decode(requestBytes));
          expect(body, {'username': 'admin', 'password': 'secret'});
          request.response.headers.add(
            HttpHeaders.setCookieHeader,
            'csrftoken=csrf-after',
          );
          request.response.headers.add(
            HttpHeaders.setCookieHeader,
            'sessionid=session-value; HttpOnly',
          );
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'role': 'ADMIN'}));
        } else if (path == '/api/v1/auth/session' && request.method == 'GET') {
          expect(
            request.headers.value(HttpHeaders.cookieHeader),
            contains('sessionid=session-value'),
          );
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({
              'user_id': 1,
              'display_name': 'Administración',
              'role': 'ADMIN',
            }),
          );
        } else if (path == '/api/v1/auth/session' &&
            request.method == 'DELETE') {
          expect(request.headers.value('X-CSRFToken'), 'csrf-after');
          expect(
            request.headers.value(HttpHeaders.cookieHeader),
            contains('sessionid=session-value'),
          );
          request.response.statusCode = HttpStatus.noContent;
        } else {
          request.response.statusCode = HttpStatus.notFound;
        }
        await request.response.close();
      }
    }();
    final client = ApiClient(
      baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
    );

    try {
      expect((await client.checkHealth())['status'], 'ok');
      await client.fetchCsrfToken();
      await client.login(username: 'admin', password: 'secret');
      await client.fetchCsrfToken();
      final session = await client.getSession();
      expect(session['display_name'], 'Administración');
      expect(session['role'], 'ADMIN');
      await client.logout();

      expect(requests, [
        'GET /api/v1/health',
        'GET /api/v1/auth/csrf',
        'POST /api/v1/auth/session',
        'GET /api/v1/auth/csrf',
        'GET /api/v1/auth/session',
        'GET /api/v1/auth/csrf',
        'DELETE /api/v1/auth/session',
      ]);
    } finally {
      client.close();
      await server.close(force: true);
      await serverTask;
    }
  });

  test('displays the approved API error envelope', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final serverTask = () async {
      await for (final request in server) {
        request.response.statusCode = HttpStatus.forbidden;
        request.response.headers.contentType = ContentType.json;
        request.response.write(
          jsonEncode({
            'code': 'CSRF_FAILED',
            'message': 'El token CSRF falta o no es válido.',
            'request_id': 'req-123',
            'retryable': false,
            'details': {'field': 'X-CSRFToken'},
          }),
        );
        await request.response.close();
      }
    }();
    final client = ApiClient(
      baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
    );

    try {
      await expectLater(
        client.fetchCsrfToken(),
        throwsA(
          isA<ApiException>()
              .having((error) => error.code, 'code', 'CSRF_FAILED')
              .having((error) => error.statusCode, 'statusCode', 403)
              .having((error) => error.requestId, 'request ID', 'req-123')
              .having(
                (error) => error.toString(),
                'formatted error',
                contains('X-CSRFToken'),
              ),
        ),
      );
    } finally {
      client.close();
      await server.close(force: true);
      await serverTask;
    }
  });

  test(
    'supports CSRF_USE_SESSIONS=True (sessionid only), session rotation and Max-Age=0 cookie deletion',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final seenSessionCookies = <String?>[];
      var csrfCalls = 0;

      final serverTask = () async {
        await for (final request in server) {
          final path = request.uri.path;
          final cookieHeader = request.headers.value(HttpHeaders.cookieHeader);
          if (path == '/api/v1/auth/csrf' && request.method == 'GET') {
            csrfCalls++;
            // CSRF_USE_SESSIONS=True: no Set-Cookie de csrftoken; solo JSON y sessionid inicial o rotada.
            if (csrfCalls == 1) {
              request.response.headers.add(
                HttpHeaders.setCookieHeader,
                'sessionid=pre-login-session; HttpOnly',
              );
            }
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'csrf_token': 'csrf-$csrfCalls'}));
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'POST') {
            expect(cookieHeader, contains('sessionid=pre-login-session'));
            expect(request.headers.value('X-CSRFToken'), 'csrf-1');
            await request.drain<void>();
            // Rotación de sesión en login
            request.response.headers.add(
              HttpHeaders.setCookieHeader,
              'sessionid=rotated-session; HttpOnly',
            );
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN'}));
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'GET') {
            seenSessionCookies.add(cookieHeader);
            if (cookieHeader != null &&
                cookieHeader.contains('sessionid=rotated-session')) {
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({'user_id': 1, 'role': 'ADMIN'}),
              );
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
          } else if (path == '/api/v1/auth/session' &&
              request.method == 'DELETE') {
            expect(cookieHeader, contains('sessionid=rotated-session'));
            // Respuesta estilo Django logout: comillas vacías + Max-Age=0
            request.response.headers.add(
              HttpHeaders.setCookieHeader,
              'sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/',
            );
            request.response.statusCode = HttpStatus.noContent;
          } else if (path == '/api/v1/health' && request.method == 'GET') {
            seenSessionCookies.add(cookieHeader);
            // También probar eliminación por max-age=0 aunque el valor no sea vacío
            request.response.headers.add(
              HttpHeaders.setCookieHeader,
              'sessionid=stale; max-age=0; Path=/',
            );
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'status': 'ok'}));
          } else {
            request.response.statusCode = HttpStatus.notFound;
          }
          await request.response.close();
        }
      }();

      final client = ApiClient(
        baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
      );

      try {
        await client.fetchCsrfToken();
        await client.login(username: 'cajero', password: 'secret');
        await client.fetchCsrfToken();
        final session = await client.getSession();
        expect(session['role'], 'ADMIN');

        await client.logout();

        // Tras logout, getSession no debe enviar sessionid ni cookies eliminadas
        await expectLater(
          client.getSession(),
          throwsA(
            isA<ApiException>().having((e) => e.statusCode, 'statusCode', 401),
          ),
        );

        // Además verificar eliminación por max-age=0 aunque el valor no esté vacío
        await client.checkHealth();
        expect(seenSessionCookies[0], contains('sessionid=rotated-session'));
        expect(seenSessionCookies[1], isNull);
        expect(seenSessionCookies[2], isNull);
      } finally {
        client.close();
        await server.close(force: true);
        await serverTask;
      }
    },
  );

  test(
    'checkHealth and fetchCsrfToken clear auth cookies and retry once on 401',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      var healthAttempts = 0;
      var csrfAttempts = 0;
      final healthCookies = <String?>[];
      final csrfCookies = <String?>[];

      final serverTask = () async {
        await for (final request in server) {
          final path = request.uri.path;
          final cookieHeader = request.headers.value(HttpHeaders.cookieHeader);
          if (path == '/api/v1/auth/session' && request.method == 'POST') {
            await request.drain<void>();
            request.response.headers.add(
              HttpHeaders.setCookieHeader,
              'sessionid=expired-session; HttpOnly',
            );
            request.response.headers.contentType = ContentType.json;
            request.response.write(jsonEncode({'role': 'ADMIN'}));
          } else if (path == '/api/v1/health' && request.method == 'GET') {
            healthAttempts++;
            healthCookies.add(cookieHeader);
            if (cookieHeader != null &&
                cookieHeader.contains('expired-session')) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sesión caducada.',
                }),
              );
            } else {
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'status': 'ok'}));
            }
          } else if (path == '/api/v1/auth/csrf' && request.method == 'GET') {
            csrfAttempts++;
            csrfCookies.add(cookieHeader);
            if (cookieHeader != null &&
                cookieHeader.contains('expired-session')) {
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'HTTP_401',
                  'message': 'No autorizado.',
                }),
              );
            } else if (csrfAttempts >= 6) {
              // Forzar fallo doble 401 para validar límite de 1 solo reintento
              request.response.statusCode = HttpStatus.unauthorized;
              request.response.headers.contentType = ContentType.json;
              request.response.write(
                jsonEncode({
                  'code': 'AUTHENTICATION_REQUIRED',
                  'message': 'Sigue rechazado.',
                }),
              );
            } else {
              request.response.headers.add(
                HttpHeaders.setCookieHeader,
                'csrftoken=fresh-csrf',
              );
              request.response.headers.contentType = ContentType.json;
              request.response.write(jsonEncode({'csrf_token': 'fresh-csrf'}));
            }
          } else {
            request.response.statusCode = HttpStatus.notFound;
          }
          await request.response.close();
        }
      }();

      final client = ApiClient(
        baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
      );

      try {
        // 1. Obtener CSRF + simular login que entrega sessionid caducado
        await client.fetchCsrfToken();
        await client.login(username: 'admin', password: 'secret');

        // 2. checkHealth recibe 401 con cookie caducada -> limpia cookies y reintenta 1 vez
        final health = await client.checkHealth();
        expect(health['status'], 'ok');
        expect(healthAttempts, 2);
        expect(healthCookies[0], contains('expired-session'));
        expect(healthCookies[1], isNull);

        // 3. Reinyectar sessionid caducado y probar fetchCsrfToken con éxito en reintento
        await client.fetchCsrfToken(); // csrfAttempts = 2
        await client.login(username: 'admin', password: 'secret');
        await client.fetchCsrfToken(); // intento 3 con expired -> 401; reintento 4 sin cookie -> 200
        expect(csrfAttempts, 4);
        expect(csrfCookies[2], contains('expired-session'));
        expect(csrfCookies[3], isNull);

        // 4. Probar límite de 1 solo reintento cuando ambos intentos responden 401
        await client.login(username: 'admin', password: 'secret');
        await client.fetchCsrfToken(); // intento 5 con expired -> 401; reintento 6 -> 401
      } on ApiException catch (e) {
        expect(e.statusCode, 401);
        expect(csrfAttempts, 6);
        expect(csrfCookies[4], contains('expired-session'));
        expect(csrfCookies[5], isNull);
      } finally {
        client.close();
        await server.close(force: true);
        await serverTask;
      }
    },
  );

  test('logout always clears cookies and CSRF token even on HTTP failure', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final seenSessionHeaders = <String?>[];
    var deleteCalled = false;

    final serverTask = () async {
      await for (final request in server) {
        final path = request.uri.path;
        final cookieHeader = request.headers.value(HttpHeaders.cookieHeader);
        if (path == '/api/v1/auth/csrf' && request.method == 'GET') {
          request.response.headers.add(
            HttpHeaders.setCookieHeader,
            'csrftoken=tok-1',
          );
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'csrf_token': 'tok-1'}));
        } else if (path == '/api/v1/auth/session' && request.method == 'POST') {
          await request.drain<void>();
          request.response.headers.add(
            HttpHeaders.setCookieHeader,
            'sessionid=active-1; HttpOnly',
          );
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'role': 'ADMIN'}));
        } else if (path == '/api/v1/auth/session' &&
            request.method == 'DELETE') {
          deleteCalled = true;
          request.response.statusCode = HttpStatus.internalServerError;
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({'code': 'INTERNAL_ERROR', 'message': 'Fallo servidor'}),
          );
        } else if (path == '/api/v1/auth/session' && request.method == 'GET') {
          seenSessionHeaders.add(cookieHeader);
          request.response.statusCode = HttpStatus.unauthorized;
          request.response.headers.contentType = ContentType.json;
          request.response.write(
            jsonEncode({'code': 'AUTHENTICATION_REQUIRED', 'message': 'No auth'}),
          );
        } else {
          request.response.statusCode = HttpStatus.notFound;
        }
        await request.response.close();
      }
    }();

    final client = ApiClient(
      baseUri: Uri.parse('http://127.0.0.1:${server.port}/api/v1'),
    );

    try {
      await client.fetchCsrfToken();
      await client.login(username: 'user', password: 'pwd');
      await expectLater(
        client.logout(),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 500)),
      );
      expect(deleteCalled, isTrue);

      await expectLater(client.getSession(), throwsA(isA<ApiException>()));
      expect(seenSessionHeaders.single, isNull);
    } finally {
      client.close();
      await server.close(force: true);
      await serverTask;
    }
  });
}
