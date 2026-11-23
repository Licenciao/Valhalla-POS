# API central

Requiere Python 3.12 o posterior y PostgreSQL. Se desarrolla y prueba con Python
3.13.18. El runtime siempre configura el backend PostgreSQL; no hay fallback a
SQLite.

## Configuración

Configure `DJANGO_SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_HOST` y `POSTGRES_PORT` desde el entorno. En
producción, deje `DJANGO_DEBUG` sin activar: las cookies de sesión y CSRF serán
`Secure`, `HttpOnly` y `SameSite=Strict`; el token CSRF se guarda en la sesión
del servidor. Active `DJANGO_DEBUG=true` solo para desarrollo local sin HTTPS.
`DJANGO_ALLOWED_HOSTS` acepta una lista separada por comas; el valor
predeterminado solo permite loopback.

## Comandos

```powershell
uv sync --python 3.13.18
uv run --python 3.13.18 manage.py migrate
uv run --python 3.13.18 manage.py createsuperuser
uv run --python 3.13.18 manage.py provision_operator --username operador
```

`createsuperuser` aprovisiona ADMIN. `provision_operator` agrega una cuenta
existente al grupo `OPERADOR`; no hay rutas de alta o administración de usuarios.

## Pruebas

Para pruebas unitarias que no dependen de semántica específica de PostgreSQL,
se permite la base SQLite en memoria solo al ejecutar explícitamente el runner
de pruebas. Esto no configura un fallback del runtime:

```powershell
$env:DJANGO_SECRET_KEY = "clave-efimera-solo-para-pruebas"
$env:DJANGO_TEST_SQLITE = "1"
uv run --python 3.13.18 manage.py test system.tests
```

Las pruebas simulan explícitamente una conexión PostgreSQL caída. La suite no
certifica concurrencia, transacciones ni otras semánticas propias de PostgreSQL.
