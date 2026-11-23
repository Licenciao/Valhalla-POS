# Dockerfile para Valhalla — Control de Inventario y Ventas
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_SQLITE_PATH=/app/apps/api/db.sqlite3 \
    DJANGO_SECRET_KEY=local-dev-secret-key-valhalla-system-secure-32b \
    DJANGO_DEBUG=1 \
    DJANGO_ALLOWED_HOSTS=*

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar librerías de Python
RUN pip install --no-cache-dir \
    "Django>=5.1,<5.3" \
    "djangorestframework>=3.15,<3.17" \
    "psycopg[binary]>=3.2,<4" \
    "cryptography>=44.0.0" \
    "django-cors-headers>=4.4.0"

# Copiar aplicaciones
COPY apps /app/apps

WORKDIR /app/apps/api

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"]
