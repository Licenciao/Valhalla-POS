"""
devices/crypto.py

Módulo criptográfico para autenticación de dispositivos Android.
Implementa las especificaciones de CA-26 (06-criterios-y-pruebas.md)
y 10-plan-de-implementacion-y-gates.md:
  • Claves ECDSA P-256 (SECP256R1) con SHA-256.
  • SPKI DER codificado en Base64URL sin padding.
  • key_id = Base64URL(SHA-256(spki_der_bytes)) sin padding.
  • Formato de firma canónica UTF-8 separado por '\\n'.
  • Firmas DER codificadas en Base64URL sin padding.
  • Ventana de timestamp: ±5 minutos (300 s).
  • Ventana de retención de nonce: 15 minutos (900 s).
"""

import base64
import hashlib
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework import exceptions, status

TIMESTAMP_TOLERANCE_SECONDS = 300  # ±5 minutos
NONCE_TTL_SECONDS = 900             # 15 minutos


def base64url_encode(data: bytes) -> str:
    """Codifica bytes en Base64URL sin padding '='."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def base64url_decode(data_str: str) -> bytes:
    """Decodifica cadena Base64URL permitiendo ausencia de padding '='."""
    padding = "=" * (-len(data_str) % 4)
    return base64.urlsafe_b64decode(data_str + padding)


def derive_key_id(spki_der_bytes: bytes) -> str:
    """Calcula key_id = Base64URL(SHA-256(spki_der_bytes)) sin padding."""
    digest = hashlib.sha256(spki_der_bytes).digest()
    return base64url_encode(digest)


def build_canonical_string(
    protocol_version: str,
    instance_id: str,
    key_id: str,
    method: str,
    path: str,
    body_bytes: bytes,
    timestamp: str | float,
    nonce: str,
) -> str:
    """
    Construye la representación UTF-8 canónica separada por '\\n':
    1. protocol_version
    2. instance_id
    3. key_id
    4. method (en mayúsculas)
    5. path (sin query)
    6. body_sha256 (hexdigest SHA-256 de los bytes exactos)
    7. timestamp
    8. nonce
    """
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    ts_str = str(timestamp)
    lines = [
        protocol_version.strip(),
        str(instance_id).strip(),
        key_id.strip(),
        method.upper().strip(),
        path.strip(),
        body_sha256,
        ts_str.strip(),
        nonce.strip(),
    ]
    return "\n".join(lines)


def verify_device_signature(
    public_key_spki_b64: str,
    signature_b64: str,
    canonical_string: str,
) -> bool:
    """
    Verifica la firma ECDSA P-256 / SHA-256 DER sobre la cadena canónica UTF-8.
    """
    try:
        spki_der = base64url_decode(public_key_spki_b64)
        public_key = serialization.load_der_public_key(spki_der)
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            return False
        if not isinstance(public_key.curve, ec.SECP256R1):
            return False

        signature_der = base64url_decode(signature_b64)
        public_key.verify(
            signature_der,
            canonical_string.encode("utf-8"),
            ec.ECDSA(hashes.SHA256()),
        )
        return True
    except (InvalidSignature, ValueError, TypeError, Exception):
        return False


def validate_timestamp(timestamp: float | str) -> bool:
    """Verifica que el timestamp se encuentre dentro de la ventana de ±5 minutos."""
    try:
        ts = float(timestamp)
    except (ValueError, TypeError):
        return False
    now = time.time()
    return abs(now - ts) <= TIMESTAMP_TOLERANCE_SECONDS


# ---------------------------------------------------------------------------
# Helpers para generación de claves y firmas en pruebas
# ---------------------------------------------------------------------------

def generate_device_keypair() -> tuple[ec.EllipticCurvePrivateKey, str, str]:
    """
    Genera un par de claves ECDSA P-256 para pruebas.
    Devuelve (private_key, spki_base64url, key_id).
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    spki_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    spki_b64 = base64url_encode(spki_der)
    key_id = derive_key_id(spki_der)
    return private_key, spki_b64, key_id


def sign_canonical_string(
    private_key: ec.EllipticCurvePrivateKey,
    canonical_string: str,
) -> str:
    """Firma la cadena canónica y devuelve la firma DER en Base64URL sin padding."""
    sig_der = private_key.sign(
        canonical_string.encode("utf-8"),
        ec.ECDSA(hashes.SHA256()),
    )
    return base64url_encode(sig_der)
