import 'package:flutter/material.dart';

import '../api_client.dart';

/// Resultado devuelto por [LoginDialog] tras autenticarse contra el servidor.
class LoginDialogResult {
  const LoginDialogResult({
    required this.session,
    required this.username,
  });

  final Map<String, Object?> session;
  final String username;
}

/// Diálogo dedicado para configurar y validar la IP/URL del servidor LAN.
class ServerConfigDialog extends StatefulWidget {
  const ServerConfigDialog({
    super.key,
    required this.initialHostOrUrl,
    this.title = 'Configurar IP de la PC Servidor',
    this.description =
        'Ingresa la dirección IP de la computadora donde corre Valhalla (ej. 192.168.1.70:8000):',
    this.confirmLabel = 'Conectar',
  });

  final String initialHostOrUrl;
  final String title;
  final String description;
  final String confirmLabel;

  @override
  State<ServerConfigDialog> createState() => _ServerConfigDialogState();
}

class _ServerConfigDialogState extends State<ServerConfigDialog> {
  late final TextEditingController _ipController;
  String? _errorText;

  @override
  void initState() {
    super.initState();
    _ipController = TextEditingController(text: widget.initialHostOrUrl);
  }

  @override
  void dispose() {
    _ipController.dispose();
    super.dispose();
  }

  void _submit() {
    final input = _ipController.text.trim();
    if (input.isEmpty) {
      setState(() {
        _errorText = 'Ingresa la dirección IP o URL del servidor.';
      });
      return;
    }

    try {
      final parsedUri = ApiBaseUrl.parseUrl(input, allowLan: true);
      Navigator.of(context).pop<Uri>(parsedUri);
    } on Object catch (error) {
      setState(() {
        _errorText = 'Dirección IP inválida: $error';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.description,
            style: const TextStyle(fontSize: 13),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _ipController,
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => _submit(),
            decoration: InputDecoration(
              labelText: 'IP y Puerto del Servidor',
              hintText: '192.168.1.70:8000',
              border: const OutlineInputBorder(),
              errorText: _errorText,
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop<Uri>(),
          child: const Text('Cancelar'),
        ),
        FilledButton(
          onPressed: _submit,
          child: Text(widget.confirmLabel),
        ),
      ],
    );
  }
}

/// Diálogo dedicado para autenticación manual contra `/api/v1/auth/session`.
///
/// La contraseña siempre inicia vacía y nunca se persiste ni se precarga.
class LoginDialog extends StatefulWidget {
  const LoginDialog({
    super.key,
    required this.apiClient,
    this.initialUsername,
    this.title = 'Iniciar Sesión',
    this.subtitle,
  });

  final ApiClient apiClient;
  final String? initialUsername;
  final String title;
  final String? subtitle;

  @override
  State<LoginDialog> createState() => _LoginDialogState();
}

class _LoginDialogState extends State<LoginDialog> {
  late final TextEditingController _usernameController;
  late final TextEditingController _passwordController;
  bool _submitting = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _usernameController = TextEditingController(
      text: widget.initialUsername ?? '',
    );
    _passwordController = TextEditingController();
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting) return;
    final username = _usernameController.text.trim();
    var password = _passwordController.text;
    if (username.isEmpty || password.isEmpty) {
      setState(() {
        _errorMessage = 'Ingresa usuario y contraseña.';
      });
      return;
    }

    setState(() {
      _submitting = true;
      _errorMessage = null;
    });

    try {
      await widget.apiClient.fetchCsrfToken();
      try {
        await widget.apiClient.login(username: username, password: password);
      } finally {
        password = '';
      }
      await widget.apiClient.fetchCsrfToken();
      final session = await widget.apiClient.getSession();
      if (!mounted) return;
      Navigator.of(context).pop<LoginDialogResult>(
        LoginDialogResult(session: session, username: username),
      );
    } on ApiException catch (error) {
      if (!mounted) return;
      _passwordController.clear();
      setState(() {
        _submitting = false;
        _errorMessage = '${error.code}: ${error.message}';
      });
    } on Object catch (error) {
      if (!mounted) return;
      _passwordController.clear();
      setState(() {
        _submitting = false;
        _errorMessage = error.toString();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final subtitle = widget.subtitle;
    return PopScope(
      canPop: !_submitting,
      child: AlertDialog(
        title: Text(widget.title),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (subtitle != null && subtitle.isNotEmpty) ...[
              Text(
                subtitle,
                style: const TextStyle(fontSize: 13, color: Color(0xFF475569)),
              ),
              const SizedBox(height: 12),
            ],
            TextField(
              controller: _usernameController,
              enabled: !_submitting,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(
                labelText: 'Usuario',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _passwordController,
              enabled: !_submitting,
              obscureText: true,
              textInputAction: TextInputAction.done,
              onSubmitted: _submitting ? null : (_) => _submit(),
              decoration: const InputDecoration(
                labelText: 'Contraseña',
                border: OutlineInputBorder(),
              ),
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 12),
              Text(
                _errorMessage!,
                style: TextStyle(
                  color: Theme.of(context).colorScheme.error,
                  fontSize: 12,
                ),
              ),
            ],
          ],
        ),
        actions: [
          TextButton(
            onPressed: _submitting
                ? null
                : () => Navigator.of(context).pop<LoginDialogResult>(),
            child: const Text('Cancelar'),
          ),
          FilledButton.icon(
            onPressed: _submitting ? null : _submit,
            icon: _submitting
                ? const SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : const Icon(Icons.login, size: 18),
            label: Text(_submitting ? 'Ingresando…' : 'Ingresar'),
          ),
        ],
      ),
    );
  }
}
