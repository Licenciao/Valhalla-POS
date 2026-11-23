"""
scripts/backup_manager.py

Herramienta de línea de comandos para ejecución programada del respaldo cifrado diario
(programado para las 22:00 UTC-06:00 o al inicio de la aplicación Windows)
y prueba mensual de restauración en entorno aislado.

Uso:
    python scripts/backup_manager.py backup
    python scripts/backup_manager.py status
    python scripts/backup_manager.py verify-isolated <path_to_backup.enc>
"""

import argparse
import os
import sys
from pathlib import Path

# Configurar path para acceder a Django
ROOT_DIR = Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "apps" / "api"
sys.path.insert(0, str(API_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_TEST_SQLITE", "1")
os.environ.setdefault("DJANGO_SECRET_KEY", "backup-manager-default-local-secret-key-32b")

import django
django.setup()

from django.conf import settings
from django.core.management import call_command

if settings.DATABASES["default"]["NAME"] == ":memory:":
    call_command("migrate", verbosity=0)

from system.backup import (
    DEFAULT_BACKUP_KEY,
    execute_backup,
    get_backup_directory,
    verify_isolated_restore,
)
from system.models import BackupRecord, BackupRecordStatus


def cmd_backup(args):
    print("Iniciando generación de respaldo consistente y cifrado AES-256-GCM...")
    try:
        record = execute_backup(simulate_drive_success=not args.no_drive)
        print(f"Respaldo generado con éxito.")
        print(f"  Backup ID:          {record.backup_id}")
        print(f"  Archivo cifrado:    {record.file_path}")
        print(f"  Tamaño:             {record.size_bytes} bytes")
        print(f"  SHA-256 Cifrado:    {record.ciphertext_sha256}")
        print(f"  SHA-256 Volcado:    {record.dump_sha256}")
        print(f"  Estado Drive:       {record.drive_status}")
        print(f"  Registros totales:  {record.manifest.get('total_records', 0)}")
    except Exception as e:
        print(f"ERROR al generar respaldo: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    last_valid = (
        BackupRecord.objects.filter(status__in=[BackupRecordStatus.SUCCESS, BackupRecordStatus.VERIFIED])
        .order_by("-created_at")
        .first()
    )
    total_valid = BackupRecord.objects.filter(
        status__in=[BackupRecordStatus.SUCCESS, BackupRecordStatus.VERIFIED]
    ).count()

    print("Estado actual del subsistema de respaldos:")
    print(f"  Copias locales válidas: {total_valid} (política: 1 actual + 7 anteriores)")
    if last_valid:
        print(f"  Último respaldo válido: {last_valid.backup_id}")
        print(f"  Fecha de creación:     {last_valid.created_at.isoformat()}")
        print(f"  Tamaño:                {last_valid.size_bytes} bytes")
        print(f"  Estado:                {last_valid.status}")
        print(f"  Drive:                 {last_valid.drive_status}")
    else:
        print("  Aún no se han generado respaldos válidos.")


def cmd_verify(args):
    backup_path = Path(args.file)
    if not backup_path.exists():
        print(f"Error: el archivo {backup_path} no existe.", file=sys.stderr)
        sys.exit(1)

    print(f"Iniciando verificación de restauración en entorno aislado para: {backup_path.name}")
    try:
        res = verify_isolated_restore(backup_path)
        print("Restauración aislada exitosa y certificada.")
        print(f"  Instance ID:        {res.get('instance_id')}")
        print(f"  Fecha de exportación:{res.get('exported_at')}")
        print(f"  Registros validados:{res.get('total_records')}")
        print(f"  Integridad DB:      {res.get('integrity_check')}")
        print("  Cotejo de tablas con manifiesto:")
        for tbl, details in res.get("counts_matched", {}).items():
            print(f"    - {tbl:<20}: {details['actual']} filas (coincide: {details['matched']})")
    except Exception as e:
        print(f"ERROR en verificación aislada: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Gestor de Respaldos y Restauración Aislada (P-10)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="Genera un respaldo cifrado inmediato")
    backup_parser.add_argument("--no-drive", action="store_true", help="Simula falla de conexión con Google Drive")
    backup_parser.set_defaults(func=cmd_backup)

    status_parser = subparsers.add_parser("status", help="Muestra el estado de los respaldos")
    status_parser.set_defaults(func=cmd_status)

    verify_parser = subparsers.add_parser("verify-isolated", help="Ejecuta restauración de prueba en DB aislada")
    verify_parser.add_argument("file", help="Ruta al archivo .enc de respaldo")
    verify_parser.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
