"""
database/vault.py — The Bunker OS (Criptografía AES-256 / Fernet)

Bóveda perimetral para cifrar en reposo tokens de bots clones y cadenas de sesión
(StringSession) de los Centinelas MTProto en SQLite.

- Si ENCRYPTION_KEY no está definida en el entorno, se genera una derivada segura
  automática (nota: se recomienda definir ENCRYPTION_KEY en producción para
  garantizar persistencia entre redeploys).
- Cifrado transparente: encrypt_secret() / decrypt_secret().

The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any, Optional

logger = logging.getLogger("bunker.vault")

# Intentar importar cryptography.fernet
try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import-not-found]
except ImportError:
    Fernet = None
    InvalidToken = Exception

_FERNET_INSTANCE = None
_FERNET_LOCK = None

def _get_fernet() -> Optional[Any]:
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is not None:
        return _FERNET_INSTANCE

    if Fernet is None:
        logger.error("❌ [Vault] La librería 'cryptography' no está instalada. Los secretos se guardarán sin cifrar.")
        return None

    raw_key = os.getenv("ENCRYPTION_KEY", "").strip()
    if not raw_key:
        # Generar una clave derivada automática si no se proveyó en entorno
        # (Advertencia: en Railway se recomienda fijar ENCRYPTION_KEY para persistencia entre deploys).
        digest = hashlib.sha256(b"TheBunkerOSMasterKey2026::CloudMediaManagement").digest()
        raw_key = base64.urlsafe_b64encode(digest).decode("utf-8")
        logger.warning("⚠️ [Vault] ENCRYPTION_KEY no definida en entorno; usando clave derivada automática predeterminada.")

    try:
        # Asegurar longitud y formato correcto para Fernet (32 url-safe base64-encoded bytes)
        key_bytes = raw_key.encode("utf-8")
        if len(key_bytes) != 44:
            digest = hashlib.sha256(key_bytes).digest()
            key_bytes = base64.urlsafe_b64encode(digest)
        _FERNET_INSTANCE = Fernet(key_bytes)
    except Exception as ex:
        logger.error(f"❌ [Vault] Clave de cifrado inválida: {ex}. Cifrado deshabilitado.")
        _FERNET_INSTANCE = None

    return _FERNET_INSTANCE


def encrypt_secret(plain_text: Optional[str]) -> str:
    """Cifra un token o session_string en texto plano para almacenarlo de forma segura en SQLite."""
    if not plain_text:
        return ""
    text_str = str(plain_text).strip()
    # Si ya está cifrado con fernet (comienza con gAAAAA...), no recifrar
    if text_str.startswith("gAAAAA"):
        return text_str
    fernet = _get_fernet()
    if fernet is None:
        return text_str
    try:
        token = fernet.encrypt(text_str.encode("utf-8"))
        return token.decode("utf-8")
    except Exception as ex:
        logger.error(f"❌ [Vault] Error cifrando secreto: {ex}")
        return text_str


def decrypt_secret(cipher_text: Optional[str]) -> str:
    """Descifra un token o session_string almacenado en SQLite para su uso en runtime."""
    if not cipher_text:
        return ""
    text_str = str(cipher_text).strip()
    if not text_str.startswith("gAAAAA"):
        # Podría ser un registro antiguo en texto plano aún no migrado
        return text_str
    fernet = _get_fernet()
    if fernet is None:
        return text_str
    try:
        decrypted = fernet.decrypt(text_str.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        logger.warning("⚠️ [Vault] Token Fernet inválido o clave de cifrado cambiada; devolviendo valor original.")
        return text_str
    except Exception as ex:
        logger.error(f"❌ [Vault] Error descifrando secreto: {ex}")
        return text_str