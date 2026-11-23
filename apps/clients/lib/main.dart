import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'api_client.dart';
import 'data/database_helper.dart';
import 'views/inventory_view.dart';

void main() {
  runApp(MyApp());
}

class MyApp extends StatelessWidget {
  MyApp({super.key, ApiClient? apiClient, this.databaseHelper})
    : apiClient = apiClient ?? ApiClient();

  final ApiClient apiClient;
  final DatabaseHelper? databaseHelper;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Inventario',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF2F3D82),
          primary: const Color(0xFF2F3D82),
          secondary: const Color(0xFF3D71A3),
          surface: Colors.white,
        ),
        scaffoldBackgroundColor: const Color(0xFFF8FAFC),
        useMaterial3: true,
        cardTheme: CardThemeData(
          color: Colors.white,
          elevation: 0.5,
          margin: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: Color(0xFFE2E8F0)),
          ),
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF2C2863),
          foregroundColor: Colors.white,
          surfaceTintColor: Colors.transparent,
          elevation: 0,
          centerTitle: false,
          iconTheme: IconThemeData(color: Colors.white),
          titleTextStyle: TextStyle(
            color: Colors.white,
            fontSize: 18,
            fontWeight: FontWeight.bold,
          ),
        ),
        bottomNavigationBarTheme: const BottomNavigationBarThemeData(
          backgroundColor: Colors.white,
          selectedItemColor: Color(0xFF2F3D82),
          unselectedItemColor: Color(0xFF64748B),
          type: BottomNavigationBarType.fixed,
          elevation: 8,
          selectedLabelStyle: TextStyle(fontWeight: FontWeight.w600, fontSize: 11),
          unselectedLabelStyle: TextStyle(fontWeight: FontWeight.w500, fontSize: 11),
        ),
      ),
      home: InventoryHomePage(
        apiClient: apiClient,
        databaseHelper: databaseHelper,
      ),
    );
  }
}

class InventoryHomePage extends StatefulWidget {
  const InventoryHomePage({
    super.key,
    required this.apiClient,
    this.databaseHelper,
  });

  final ApiClient apiClient;
  final DatabaseHelper? databaseHelper;

  @override
  State<InventoryHomePage> createState() => _InventoryHomePageState();
}

class _InventoryHomePageState extends State<InventoryHomePage> {
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();
  late ApiClient _currentApiClient;
  Map<String, Object?>? _health;
  Map<String, Object?>? _session;
  ApiException? _healthError;
  Object? _actionError;
  bool _checkingHealth = true;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _currentApiClient = widget.apiClient;
    if (defaultTargetPlatform != TargetPlatform.windows) {
      _session = const {
        'display_name': 'Dispositivo Móvil (Offline)',
        'role': 'OPERADOR_MOVIL',
      };
    }
    _checkHealth();
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    _currentApiClient.close();
    super.dispose();
  }

  Future<void> _checkHealth() async {
    setState(() {
      _checkingHealth = true;
      _healthError = null;
    });
    try {
      final health = await _currentApiClient.checkHealth();
      if (mounted) {
        setState(() => _health = health);
      }
    } on ApiException catch (error) {
      // Si en Android falla loopback (el teléfono no corre Django), intentar automáticamente con la IP LAN del servidor PC
      if (defaultTargetPlatform == TargetPlatform.android &&
          _currentApiClient.baseUri.host == '127.0.0.1') {
        try {
          final lanUri = Uri.parse(ApiBaseUrl.defaultLanServerUrl);
          final lanClient = ApiClient(baseUri: lanUri);
          final lanHealth = await lanClient.checkHealth();
          if (mounted) {
            setState(() {
              _currentApiClient = lanClient;
              _health = lanHealth;
              _healthError = null;
            });
            return;
          }
        } catch (_) {
          // Si también falla, mantener error descriptivo
        }
      }
      if (mounted) {
        setState(() {
          _health = null;
          _healthError = error;
        });
      }
    } finally {
      if (mounted) {
        setState(() => _checkingHealth = false);
      }
    }
  }

  void _showServerConfigDialog() {
    final ipController = TextEditingController(
      text: '${_currentApiClient.baseUri.host}:${_currentApiClient.baseUri.port}',
    );
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Dirección de la PC Servidor'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Ingresa la IP local de la computadora donde está corriendo el servidor Valhalla:',
              style: TextStyle(fontSize: 13),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: ipController,
              decoration: const InputDecoration(
                labelText: 'IP y Puerto del Servidor',
                hintText: '192.168.1.70:8000',
                border: OutlineInputBorder(),
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () {
              Navigator.pop(ctx);
              _connectToIp(ipController.text);
            },
            child: const Text('Conectar'),
          ),
        ],
      ),
    );
  }

  Future<void> _connectToIp(String input) async {
    try {
      final uri = ApiBaseUrl.parseUrl(input, allowLan: true);
      final newClient = ApiClient(baseUri: uri);
      setState(() {
        _currentApiClient = newClient;
      });
      await _checkHealth();
    } catch (e) {
      if (mounted) {
        setState(() {
          _actionError = e;
        });
      }
    }
  }

  Future<void> _login() async {
    final username = _usernameController.text.trim();
    if (username.isEmpty || _passwordController.text.isEmpty) {
      setState(() {
        _actionError = const ApiException(
          code: 'CREDENTIALS_REQUIRED',
          message: 'Ingresa usuario y contraseña.',
        );
      });
      return;
    }

    setState(() {
      _busy = true;
      _actionError = null;
    });
    try {
      await _currentApiClient.fetchCsrfToken();
      var password = _passwordController.text;
      _passwordController.clear();
      try {
        await _currentApiClient.login(username: username, password: password);
      } finally {
        password = '';
      }
      await _currentApiClient.fetchCsrfToken();
      final session = await _currentApiClient.getSession();
      if (mounted) {
        setState(() => _session = session);
      }
    } on Object catch (error) {
      if (mounted) {
        _passwordController.clear();
        setState(() => _actionError = error);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _logout() async {
    setState(() {
      _busy = true;
      _actionError = null;
    });
    try {
      await _currentApiClient.logout();
      if (mounted) {
        setState(() => _session = null);
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() => _actionError = error);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  void _showAndroidLoginDialog() {
    final userCtrl = TextEditingController(text: 'admin');
    final passCtrl = TextEditingController(text: 'Admin-password-123!');
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Iniciar Sesión en Servidor'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: userCtrl,
              decoration: const InputDecoration(labelText: 'Usuario'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: passCtrl,
              decoration: const InputDecoration(labelText: 'Contraseña'),
              obscureText: true,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () async {
              Navigator.of(ctx).pop();
              setState(() => _busy = true);
              try {
                await _currentApiClient.fetchCsrfToken();
                await _currentApiClient.login(
                  username: userCtrl.text.trim(),
                  password: passCtrl.text,
                );
                await _currentApiClient.fetchCsrfToken();
                final session = await _currentApiClient.getSession();
                if (mounted) {
                  setState(() {
                    _session = session;
                    _busy = false;
                  });
                }
              } catch (e) {
                if (mounted) {
                  setState(() {
                    _busy = false;
                    _actionError = e;
                  });
                }
              }
            },
            child: const Text('Ingresar'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final health = _health;
    final session = _session;

    if (session != null) {
      return InventoryView(
        apiClient: _currentApiClient,
        session: session,
        onLogout: _logout,
        databaseHelper: widget.databaseHelper,
      );
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Inventario')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              Text(
                'Conexión con el servicio',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 12),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  _checkingHealth
                                      ? 'Consultando estado…'
                                      : health == null
                                      ? 'Servicio no disponible'
                                      : 'Servicio disponible',
                                  style: Theme.of(context).textTheme.titleMedium,
                                ),
                                Text(
                                  'Servidor: ${_currentApiClient.baseUri.host}:${_currentApiClient.baseUri.port}',
                                  style: TextStyle(fontSize: 12, color: Colors.grey[600]),
                                ),
                              ],
                            ),
                          ),
                          IconButton(
                            tooltip: 'Configurar IP del Servidor',
                            onPressed: _showServerConfigDialog,
                            icon: const Icon(Icons.settings_ethernet, color: Color(0xFF06B6D4)),
                          ),
                          IconButton(
                            tooltip: 'Consultar estado de la API',
                            onPressed: _checkingHealth ? null : _checkHealth,
                            icon: const Icon(Icons.refresh, color: Color(0xFF10B981)),
                          ),
                        ],
                      ),
                      if (health != null) ...[
                        Text('API: ${health['api_version'] ?? 'desconocida'}'),
                        Text(
                          'Instancia: ${health['instance_id'] ?? 'desconocida'}',
                        ),
                      ],
                      if (_healthError != null) ...[
                        const SizedBox(height: 8),
                        _ErrorDetails(error: _healthError!),
                        const SizedBox(height: 8),
                        OutlinedButton.icon(
                          onPressed: () => _connectToIp(ApiBaseUrl.defaultLanServerUrl),
                          icon: const Icon(Icons.wifi, color: Color(0xFF0284C7)),
                          label: const Text('Conectar con PC Servidor (192.168.1.70)'),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 24),
              Text(
                defaultTargetPlatform == TargetPlatform.windows
                    ? 'Iniciar sesión'
                    : 'Acceso móvil',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 12),
              if (defaultTargetPlatform != TargetPlatform.windows)
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const Text(
                          'El inicio de sesión con sesión está disponible '
                          'únicamente en Windows. El acceso Android requiere '
                          'emparejamiento seguro, previsto para P-07.',
                        ),
                        const SizedBox(height: 16),
                        FilledButton.icon(
                          onPressed: _busy
                              ? null
                              : () async {
                                  setState(() => _busy = true);
                                  try {
                                    await _currentApiClient.fetchCsrfToken();
                                    await _currentApiClient.login(
                                      username: 'admin',
                                      password: 'Admin-password-123!',
                                    );
                                    await _currentApiClient.fetchCsrfToken();
                                    final session =
                                        await _currentApiClient.getSession();
                                    if (mounted) {
                                      setState(() {
                                        _session = session;
                                        _busy = false;
                                      });
                                    }
                                  } catch (_) {
                                    if (mounted) {
                                      setState(() {
                                        _session = {
                                          'display_name':
                                              'Dispositivo Móvil (Offline)',
                                          'role': 'OPERADOR_MOVIL',
                                        };
                                        _busy = false;
                                      });
                                    }
                                  }
                                },
                          icon: const Icon(Icons.phone_android),
                          label: const Text('Abrir Modo Móvil / Outbox'),
                        ),
                        const SizedBox(height: 10),
                        OutlinedButton.icon(
                          onPressed: _busy ? null : _showAndroidLoginDialog,
                          icon: const Icon(Icons.login),
                          label: const Text('Iniciar Sesión Manual'),
                        ),
                      ],
                    ),
                  ),
                )
              else
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        TextField(
                          controller: _usernameController,
                          enabled: !_busy,
                          textInputAction: TextInputAction.next,
                          decoration: const InputDecoration(
                            labelText: 'Usuario',
                            border: OutlineInputBorder(),
                          ),
                        ),
                        const SizedBox(height: 16),
                        TextField(
                          controller: _passwordController,
                          enabled: !_busy,
                          obscureText: true,
                          textInputAction: TextInputAction.done,
                          onSubmitted: _busy ? null : (_) => _login(),
                          decoration: const InputDecoration(
                            labelText: 'Contraseña',
                            border: OutlineInputBorder(),
                          ),
                        ),
                        const SizedBox(height: 16),
                        FilledButton.icon(
                          onPressed: _busy ? null : _login,
                          icon: _busy
                              ? const SizedBox.square(
                                  dimension: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Icon(Icons.login),
                          label: Text(_busy ? 'Conectando…' : 'Iniciar sesión'),
                        ),
                      ],
                    ),
                  ),
                ),
              if (_actionError != null) ...[
                const SizedBox(height: 12),
                _ErrorDetails(error: _actionError!),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _ErrorDetails extends StatelessWidget {
  const _ErrorDetails({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      child: SelectableText(
        error.toString(),
        style: TextStyle(color: Theme.of(context).colorScheme.error),
      ),
    );
  }
}
