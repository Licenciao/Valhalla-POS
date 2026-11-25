import 'dart:async';
import 'dart:convert';
import 'dart:io';

const _defaultApiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://127.0.0.1:8000/api/v1',
);

class ApiBaseUrl {
  static const String defaultLanServerUrl = 'http://192.168.1.70:8000/api/v1';

  static Uri fromEnvironment({bool allowLan = true}) =>
      validate(Uri.parse(_defaultApiBaseUrl), allowLan: allowLan);

  static Uri validate(Uri uri, {bool allowLan = false}) {
    final path = uri.path.replaceFirst(RegExp(r'/+$'), '');
    final isApiPath = path == '/api/v1' || path.endsWith('/api/v1');
    final address = InternetAddress.tryParse(uri.host);
    final isLoopbackHost =
        uri.host.toLowerCase() == 'localhost' || (address?.isLoopback ?? false);
    final isLanHost = address != null && _isLanAddress(address);

    if ((uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        !isApiPath ||
        (!isLoopbackHost && (!allowLan || !isLanHost))) {
      throw ArgumentError.value(
        uri,
        'API_BASE_URL',
        'Use una URL /api/v1 en loopback o red local autorizada; el acceso remoto requiere HTTPS con fijación de certificado.',
      );
    }

    return uri.replace(path: path);
  }

  static bool _isLanAddress(InternetAddress address) {
    if (address.isLoopback) return true;
    final raw = address.rawAddress;
    if (raw.length == 4) {
      if (raw[0] == 10) return true;
      if (raw[0] == 172 && raw[1] >= 16 && raw[1] <= 31) return true;
      if (raw[0] == 192 && raw[1] == 168) return true;
    }
    return false;
  }

  static Uri parseUrl(String input, {bool allowLan = true}) {
    String clean = input.trim();
    if (!clean.startsWith('http://') && !clean.startsWith('https://')) {
      clean = 'http://$clean';
    }
    var uri = Uri.parse(clean);
    final path = uri.path.replaceFirst(RegExp(r'/+$'), '');
    if (path.isEmpty || path == '/') {
      uri = uri.replace(path: '/api/v1');
    } else if (!path.endsWith('/api/v1')) {
      uri = uri.replace(path: '$path/api/v1');
    }
    return validate(uri, allowLan: allowLan);
  }
}

class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.statusCode,
    this.requestId,
    this.retryable = false,
    this.details,
  });

  final String code;
  final String message;
  final int? statusCode;
  final String? requestId;
  final bool retryable;
  final Object? details;

  @override
  String toString() {
    final parts = <String>['$code: $message'];
    if (requestId != null && requestId!.isNotEmpty) {
      parts.add('request_id: $requestId');
    }
    if (retryable) {
      parts.add('reintentable');
    }
    final hasDetails = switch (details) {
      null => false,
      Map value => value.isNotEmpty,
      Iterable value => value.isNotEmpty,
      _ => true,
    };
    if (hasDetails) {
      parts.add('details: ${jsonEncode(details)}');
    }
    return parts.join('\n');
  }
}

class ApiClient {
  ApiClient({Uri? baseUri, HttpClient? httpClient, bool allowLan = true})
    : _baseUri = baseUri != null
          ? ApiBaseUrl.validate(baseUri, allowLan: allowLan)
          : ApiBaseUrl.fromEnvironment(allowLan: allowLan),
      _httpClient = httpClient ?? HttpClient() {
    _httpClient.connectionTimeout = const Duration(seconds: 10);
  }

  Uri _baseUri;
  Uri get baseUri => _baseUri;

  void updateBaseUri(Uri newUri, {bool allowLan = true}) {
    _baseUri = ApiBaseUrl.validate(newUri, allowLan: allowLan);
    _cookies.clear();
    _csrfToken = null;
  }

  String get hostAndPort => '${_baseUri.host}:${_baseUri.port}';
  final HttpClient _httpClient;
  final Map<String, String> _cookies = {};
  String? _csrfToken;
  bool _closed = false;

  Future<Map<String, Object?>> checkHealth() {
    return _runWithSingleUnauthRetry(_checkHealthOnce);
  }

  Future<Map<String, Object?>> _checkHealthOnce() async {
    final response = await _request('GET', 'health');
    final body = _decodeObject(response.body, 'health');
    if (response.statusCode != HttpStatus.ok || body['status'] != 'ok') {
      throw ApiException(
        code: 'INVALID_HEALTH_RESPONSE',
        message: 'La API respondió con un estado de salud inesperado.',
        statusCode: response.statusCode,
      );
    }
    return body;
  }

  Future<void> fetchCsrfToken() {
    return _runWithSingleUnauthRetry(_fetchCsrfTokenOnce);
  }

  Future<void> _fetchCsrfTokenOnce() async {
    final response = await _request('GET', 'auth/csrf');
    final body = _decodeObject(response.body, 'auth/csrf');
    final token = body['csrf_token'];
    if (response.statusCode < 200 ||
        response.statusCode >= 300 ||
        token is! String ||
        token.isEmpty) {
      throw ApiException(
        code: 'INVALID_CSRF_RESPONSE',
        message: 'La API no entregó un token CSRF válido.',
        statusCode: response.statusCode,
      );
    }
    _csrfToken = token;
  }

  Future<T> _runWithSingleUnauthRetry<T>(Future<T> Function() action) async {
    try {
      return await action();
    } on ApiException catch (error) {
      if (_isUnauthenticatedException(error)) {
        _clearAuthCookies();
        return await action();
      }
      rethrow;
    }
  }

  bool _isUnauthenticatedException(ApiException error) =>
      error.statusCode == HttpStatus.unauthorized ||
      error.code == 'AUTHENTICATION_REQUIRED' ||
      error.code == 'HTTP_401';

  void _clearAuthCookies() {
    _cookies.remove('sessionid');
    _cookies.remove('csrftoken');
    _csrfToken = null;
  }

  Future<void> login({
    required String username,
    required String password,
  }) async {
    final csrfToken = _csrfToken;
    if (csrfToken == null) {
      throw const ApiException(
        code: 'CSRF_TOKEN_REQUIRED',
        message: 'Obtén un token CSRF antes de iniciar sesión.',
      );
    }
    final response = await _request(
      'POST',
      'auth/session',
      body: {'username': username, 'password': password},
      csrfToken: csrfToken,
    );
    _decodeObject(response.body, 'auth/session');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        code: 'INVALID_LOGIN_RESPONSE',
        message: 'La API respondió de forma inesperada al iniciar sesión.',
        statusCode: response.statusCode,
      );
    }
  }

  Future<Map<String, Object?>> getSession() async {
    final response = await _request('GET', 'auth/session');
    final body = _decodeObject(response.body, 'auth/session');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        code: 'INVALID_SESSION_RESPONSE',
        message: 'La API respondió de forma inesperada al consultar la sesión.',
        statusCode: response.statusCode,
      );
    }
    return body;
  }

  Future<void> logout() async {
    try {
      await fetchCsrfToken();
      final response = await _request(
        'DELETE',
        'auth/session',
        csrfToken: _csrfToken,
      );
      if (response.statusCode < 200 || response.statusCode >= 300) {
        _decodeObject(response.body, 'auth/session');
        throw ApiException(
          code: 'INVALID_LOGOUT_RESPONSE',
          message: 'La API respondió de forma inesperada al cerrar sesión.',
          statusCode: response.statusCode,
        );
      }
    } finally {
      _cookies.clear();
      _csrfToken = null;
    }
  }

  // ── Catálogo, Inventario y Ventas ──────────────────────────────────────────

  Future<Map<String, Object?>> getProducts({String cursor = 'inicio'}) async {
    final response = await _request('GET', 'products/pages/$cursor');
    return _decodeObject(response.body, 'products');
  }

  Future<Map<String, Object?>> getStock({String cursor = 'inicio'}) async {
    final response = await _request('GET', 'inventory/stock/pages/$cursor');
    return _decodeObject(response.body, 'stock');
  }

  Future<Map<String, Object?>> registerSale({
    required int productId,
    required double quantity,
    double? priceOverride,
    Map<String, Object?>? discount,
    String? idempotencyKey,
  }) async {
    await fetchCsrfToken();
    final body = <String, Object?>{
      'product_id': productId,
      'quantity': quantity.toStringAsFixed(6),
      if (priceOverride != null) 'price_override': priceOverride.toStringAsFixed(2),
      if (discount != null) 'discount': discount,
    };
    final response = await _request(
      'POST',
      'sales',
      body: body,
      csrfToken: _csrfToken,
      idempotencyKey: idempotencyKey,
    );
    return _decodeObject(response.body, 'sales');
  }

  Future<Map<String, Object?>> registerMultiSale({
    required List<Map<String, Object?>> items,
    String? deviceId,
    bool pending = false,
    String? idempotencyKey,
  }) async {
    await fetchCsrfToken();
    final body = <String, Object?>{
      'items': items,
      if (deviceId != null) 'device_id': deviceId,
      if (pending) 'status': 'PENDING',
    };
    final response = await _request(
      'POST',
      'sales',
      body: body,
      csrfToken: _csrfToken,
      idempotencyKey: idempotencyKey,
    );
    return _decodeObject(response.body, 'sales');
  }

  Future<Map<String, Object?>> registerReturn({
    required int saleId,
    required double quantity,
    double? refundAmount,
    String? idempotencyKey,
  }) async {
    await fetchCsrfToken();
    final body = <String, Object?>{
      'quantity': quantity.toStringAsFixed(6),
      if (refundAmount != null) 'refund_amount': refundAmount.toStringAsFixed(2),
    };
    final response = await _request(
      'POST',
      'sales/$saleId/returns',
      body: body,
      csrfToken: _csrfToken,
      idempotencyKey: idempotencyKey,
    );
    return _decodeObject(response.body, 'returns');
  }

  void close() {
    if (!_closed) {
      _closed = true;
      _cookies.clear();
      _csrfToken = null;
      _httpClient.close(force: true);
    }
  }

  Future<_ApiResponse> _request(
    String method,
    String endpoint, {
    Map<String, Object?>? body,
    String? csrfToken,
    String? idempotencyKey,
  }) async {
    if (_closed) {
      throw const ApiException(
        code: 'CLIENT_CLOSED',
        message: 'El cliente HTTP ya está cerrado.',
      );
    }

    try {
      final request = await _httpClient
          .openUrl(method, baseUri.replace(path: '${baseUri.path}/$endpoint'))
          .timeout(const Duration(seconds: 15));
      request.followRedirects = false;
      request.headers.set(HttpHeaders.acceptHeader, ContentType.json.mimeType);
      final cookieHeader = _cookies.entries
          .map((entry) => '${entry.key}=${entry.value}')
          .join('; ');
      if (cookieHeader.isNotEmpty) {
        request.headers.set(HttpHeaders.cookieHeader, cookieHeader);
      }
      if (csrfToken != null) {
        request.headers.set('X-CSRFToken', csrfToken);
      }
      if (idempotencyKey != null) {
        request.headers.set('Idempotency-Key', idempotencyKey);
      }
      if (body != null) {
        final bodyBytes = utf8.encode(jsonEncode(body));
        request.headers.contentType = ContentType.json;
        request.contentLength = bodyBytes.length;
        request.add(bodyBytes);
      }

      final response = await request.close().timeout(
        const Duration(seconds: 15),
      );
      _updateCookies(response.headers);
      final responseBody = await utf8.decoder
          .bind(response)
          .join()
          .timeout(const Duration(seconds: 15));
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw _apiException(response.statusCode, responseBody);
      }
      return _ApiResponse(response.statusCode, responseBody);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw const ApiException(
        code: 'NETWORK_TIMEOUT',
        message: 'La API tardó demasiado en responder.',
        retryable: true,
      );
    } on SocketException {
      throw const ApiException(
        code: 'NETWORK_UNAVAILABLE',
        message: 'No se pudo conectar con la API.',
        retryable: true,
      );
    } on HandshakeException {
      throw const ApiException(
        code: 'TLS_ERROR',
        message: 'No se pudo establecer una conexión TLS segura.',
      );
    } on HttpException {
      throw const ApiException(
        code: 'HTTP_ERROR',
        message: 'La respuesta de la API no pudo procesarse.',
      );
    } on FormatException {
      throw const ApiException(
        code: 'INVALID_JSON',
        message: 'La API devolvió una respuesta JSON inválida.',
      );
    }
  }

  void _updateCookies(HttpHeaders headers) {
    for (final header in headers[HttpHeaders.setCookieHeader] ?? const []) {
      final parts = header.split(';');
      final pair = parts.first.trim();
      final separator = pair.indexOf('=');
      if (separator <= 0) {
        continue;
      }
      final name = pair.substring(0, separator).trim();
      final value = pair.substring(separator + 1).trim();
      if (name != 'csrftoken' && name != 'sessionid') {
        continue;
      }
      final hasZeroMaxAge = parts.skip(1).any((attr) {
        final trimmed = attr.trim().toLowerCase();
        final eq = trimmed.indexOf('=');
        if (eq <= 0) {
          return false;
        }
        final attrName = trimmed.substring(0, eq).trim();
        final attrValue = trimmed.substring(eq + 1).trim();
        return attrName == 'max-age' && attrValue == '0';
      });
      if (value.isEmpty || value == '""' || hasZeroMaxAge) {
        _cookies.remove(name);
      } else {
        _cookies[name] = value;
      }
    }
  }

  ApiException _apiException(int statusCode, String responseBody) {
    try {
      final decoded = jsonDecode(responseBody);
      if (decoded is Map<String, dynamic> &&
          decoded['code'] is String &&
          decoded['message'] is String) {
        return ApiException(
          code: decoded['code'] as String,
          message: decoded['message'] as String,
          statusCode: statusCode,
          requestId: decoded['request_id'] as String?,
          retryable: decoded['retryable'] == true,
          details: decoded['details'],
        );
      }
    } on FormatException {
      // Fall through to the generic HTTP error without exposing response HTML.
    }
    return ApiException(
      code: 'HTTP_$statusCode',
      message:
          'La API respondió con HTTP $statusCode sin un sobre de error válido.',
      statusCode: statusCode,
    );
  }

  Map<String, Object?> _decodeObject(String body, String endpoint) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      }
    } on FormatException {
      // Report a protocol error below; do not show arbitrary response content.
    }
    throw ApiException(
      code: 'INVALID_API_RESPONSE',
      message: 'La API devolvió una respuesta inválida para $endpoint.',
    );
  }
}

class _ApiResponse {
  const _ApiResponse(this.statusCode, this.body);

  final int statusCode;
  final String body;
}
