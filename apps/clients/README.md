# Cliente de inventario

Primer slice de P-05 para Android y Windows: consulta `GET /health`; en
Windows ofrece inicio/consulta/cierre de sesión mediante el flujo CSRF y Django
Auth del contrato `/api/v1`. Android no expone inicio de sesión Django y queda
a la espera del emparejamiento seguro. La contraseña se borra del campo al
enviar y no se guarda; cookie de sesión y token CSRF permanecen únicamente en
memoria y se eliminan al cerrar sesión o cerrar la aplicación.

## URL de la API

La URL predeterminada es
`http://127.0.0.1:8000/api/v1`, solo para desarrollo local. Se puede cambiar a
otra URL loopback `/api/v1` al ejecutar Flutter:

```powershell
& 'C:\Users\pablo\develop\flutter\bin\flutter.bat' run `
  --dart-define=API_BASE_URL=http://localhost:8000/api/v1
```

El cliente solo permite hosts loopback en este incremento, tanto para HTTP de
desarrollo como para HTTPS local. No usarlo para acceder a la PC por LAN:
rechazará hosts remotos hasta que P-07 implemente y valide la fijación de
certificado. En Android, la política de red también solo permite HTTP de
desarrollo a `localhost` y `127.0.0.1`; el inicio de sesión Django está
disponible únicamente en Windows, mientras que Android requerirá el flujo de
emparejamiento seguro previsto para P-07.

## Validación local

```powershell
Set-Location apps\clients
& 'C:\Users\pablo\develop\flutter\bin\flutter.bat' analyze
& 'C:\Users\pablo\develop\flutter\bin\flutter.bat' test
```

No ejecutar builds nativos mientras no estén instalados los toolchains
Android/Windows requeridos.
