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
}
