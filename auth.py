"""
auth.py — The Bunker OS · v41 · Autenticación dual estricta (Mini App de Telegram + sesión web)

Fuente ÚNICA de verdad criptográfica del backend (main.py importa de aquí; ningún otro módulo re-implementa firmas):

1. initData de Telegram WebApp (Mini App)
   secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
   hash       = HEX(HMAC_SHA256(key=secret_key, msg=data_check_string))
   + caducidad configurable (INIT_DATA_MAX_AGE, 24 h por defecto) y rechazo de auth_date en el futuro.
   El initData NUNCA se persiste: el cliente lo lee en memoria (tgApp.tg.initData) en cada petición.

2. Widget «Log in with Telegram» (navegador)
   secret_key = SHA256(bot_token) · misma caducidad y control de reloj.

3. Token de sesión web → JWT HS256 estándar (RFC 7519)
   Cabecera {"alg":"HS256","typ":"JWT"} · claims: iss, sub, uid, iat, nbf, exp, jti, fn, un, ph.
   • Solo se acepta alg=HS256 (un token con "alg":"none" u otro algoritmo se rechaza siempre).
   • Secreto: SESSION_SECRET (variable de entorno, ≥ 32 caracteres) o, si no existe, el derivado del BOT_TOKEN que
     ya usaban las versiones anteriores (así los tokens vigentes no se invalidan al desplegar).
   • Compatibilidad: los tokens heredados "<b64>.<hex>" (v8–v40) siguen siendo válidos hasta su caducidad original
     de 30 días; los nuevos inicios de sesión ya reciben JWT. Desactivable con ACCEPT_LEGACY_SESSION_TOKENS=0.

El cliente envía el JWT como `Authorization: Bearer <token>` (REST) o `?token=` / frame {"action":"auth"} (WebSocket).
The Bunker Command OS © 2026 — Cloud Media Management
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
from typing import Any, Dict, Optional, TypedDict

logger = logging.getLogger("bunker.auth")

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "the-bunker-os"
CLOCK_SKEW_SECONDS = 60
MAX_TOKEN_LENGTH = 4096


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except ValueError:
        return default
    return max(lo, min(hi, value))


INIT_DATA_MAX_AGE = _env_int("INIT_DATA_MAX_AGE", 86400, 60, 7 * 86400)
WIDGET_MAX_AGE = _env_int("WIDGET_AUTH_MAX_AGE", 86400, 60, 7 * 86400)
SESSION_TTL_SECONDS = _env_int("SESSION_TTL_DAYS", 30, 1, 90) * 86400
LEGACY_TOKEN_TTL_SECONDS = 30 * 86400
ACCEPT_LEGACY_SESSION_TOKENS = os.getenv("ACCEPT_LEGACY_SESSION_TOKENS", "1").strip().lower() not in ("0", "false", "no", "off")


class TelegramIdentity(TypedDict):
    id: int
    first_name: str
    last_name: str
    username: str
    language_code: str
    photo_url: str
    auth_date: int
    start_param: Optional[str]


class SessionClaims(TypedDict, total=False):
    iss: str
    sub: str
    uid: int
    iat: int
    nbf: int
    exp: int
    jti: str
    fn: str
    un: str
    ph: str
    legacy: bool


# ==========================================
# 🔑 SECRETOS
# ==========================================
def _legacy_secret(bot_token: str) -> bytes:
    """Clave que usaban v8–v40 (tokens heredados y respaldo cuando no hay SESSION_SECRET)."""
    return hashlib.sha256(f"bunker-web-session::{bot_token}".encode("utf-8")).digest()


_WARNED_SHORT_SECRET = False


def session_secret(bot_token: str) -> bytes:
    global _WARNED_SHORT_SECRET
    explicit = os.getenv("SESSION_SECRET", "").strip()
    if explicit:
        if len(explicit) < 32:
            if not _WARNED_SHORT_SECRET:
                _WARNED_SHORT_SECRET = True
                logger.warning("⚠️ [Auth] SESSION_SECRET tiene menos de 32 caracteres: se ignora y se usa el derivado del BOT_TOKEN.")
        else:
            return hashlib.sha256(f"bunker-jwt::{explicit}".encode("utf-8")).digest()
    return _legacy_secret(bot_token)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# ==========================================
# 📱 initData DE LA MINI APP
# ==========================================
def validate_init_data(init_data: str, bot_token: str, max_age: Optional[int] = None) -> Optional[TelegramIdentity]:
    """Valida la firma y la vigencia del initData. Devuelve la identidad o None (nunca lanza)."""
    if not init_data or not bot_token or len(init_data) > 8192:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(received_hash)):
        logger.warning("⚠️ [Auth] initData con firma inválida rechazado.")
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    now = int(time.time())
    limit = INIT_DATA_MAX_AGE if max_age is None else int(max_age)
    if auth_date <= 0 or auth_date > now + CLOCK_SKEW_SECONDS or (limit > 0 and now - auth_date > limit):
        return None
    try:
        user = json.loads(pairs.get("user", "") or "{}")
        user_id = int(user.get("id", 0)) if isinstance(user, dict) else 0
    except (ValueError, TypeError):
        return None
    if user_id <= 0:
        return None
    return TelegramIdentity(
        id=user_id, first_name=str(user.get("first_name") or ""), last_name=str(user.get("last_name") or ""),
        username=str(user.get("username") or ""), language_code=str(user.get("language_code") or ""),
        photo_url=str(user.get("photo_url") or ""), auth_date=auth_date, start_param=pairs.get("start_param"),
    )


def parse_telegram_user_id(init_data: str, bot_token: str) -> int:
    identity = validate_init_data(init_data, bot_token)
    return identity["id"] if identity else 0


# ==========================================
# 🌐 WIDGET «LOG IN WITH TELEGRAM»
# ==========================================
def verify_widget_login(data: Any, bot_token: str, max_age: Optional[int] = None) -> bool:
    """Firma oficial del widget (SHA256(bot_token) como clave) + caducidad y control de reloj."""
    if not isinstance(data, dict) or not bot_token:
        return False
    received_hash = data.get("hash")
    if not received_hash:
        return False
    fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(received_hash)):
        return False
    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    limit = WIDGET_MAX_AGE if max_age is None else int(max_age)
    return 0 < auth_date <= now + CLOCK_SKEW_SECONDS and (limit <= 0 or now - auth_date <= limit)


# ==========================================
# 🎫 SESIÓN WEB (JWT HS256)
# ==========================================
def issue_session_token(user_id: int, bot_token: str, first_name: str = "", username: str = "",
                        photo_url: str = "", ttl_seconds: Optional[int] = None) -> str:
    now = int(time.time())
    claims: SessionClaims = {
        "iss": JWT_ISSUER, "sub": str(int(user_id)), "uid": int(user_id), "iat": now, "nbf": now - 5,
        "exp": now + int(ttl_seconds or SESSION_TTL_SECONDS), "jti": secrets.token_urlsafe(12),
        "fn": str(first_name or "")[:64], "un": str(username or "")[:64], "ph": str(photo_url or "")[:512],
    }
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    signing_input = (f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
                     f"{_b64url_encode(json.dumps(claims, separators=(',', ':')).encode('utf-8'))}")
    signature = hmac.new(session_secret(bot_token), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def _verify_jwt(token: str, bot_token: str) -> Optional[SessionClaims]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if not isinstance(header, dict) or header.get("alg") != JWT_ALGORITHM or header.get("typ", "JWT") != "JWT":
            return None
        expected = hmac.new(session_secret(bot_token), f"{header_b64}.{payload_b64}".encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            return None
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict) or claims.get("iss") != JWT_ISSUER:
        return None
    now = int(time.time())
    try:
        exp, nbf, iat, uid = int(claims["exp"]), int(claims.get("nbf", 0)), int(claims.get("iat", 0)), int(claims["uid"])
    except (KeyError, TypeError, ValueError):
        return None
    if exp < now - CLOCK_SKEW_SECONDS or nbf > now + CLOCK_SKEW_SECONDS or iat > now + CLOCK_SKEW_SECONDS or uid <= 0:
        return None
    if str(claims.get("sub", "")) != str(uid):
        return None
    out: SessionClaims = {"iss": JWT_ISSUER, "sub": str(uid), "uid": uid, "iat": iat, "nbf": nbf, "exp": exp,
                          "jti": str(claims.get("jti", "")), "fn": str(claims.get("fn", "")),
                          "un": str(claims.get("un", "")), "ph": str(claims.get("ph", "")), "legacy": False}
    return out


def _verify_legacy(token: str, bot_token: str) -> Optional[SessionClaims]:
    """Formato v8–v40: base64url(json).hexHMAC (clave derivada del BOT_TOKEN, 30 días desde iat)."""
    try:
        raw_b64, signature = token.rsplit(".", 1)
        expected = hmac.new(_legacy_secret(bot_token), raw_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64url_decode(raw_b64))
        uid, iat = int(payload.get("uid", 0)), int(payload.get("iat", 0))
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, AttributeError):
        return None
    now = int(time.time())
    if uid <= 0 or iat > now + CLOCK_SKEW_SECONDS or now - iat > LEGACY_TOKEN_TTL_SECONDS:
        return None
    return {"iss": JWT_ISSUER, "sub": str(uid), "uid": uid, "iat": iat, "nbf": iat, "exp": iat + LEGACY_TOKEN_TTL_SECONDS,
            "jti": "", "fn": str(payload.get("fn", "")), "un": str(payload.get("un", "")),
            "ph": str(payload.get("ph", "")), "legacy": True}


def verify_session_token(token: Optional[str], bot_token: str) -> Optional[SessionClaims]:
    """JWT HS256 (actual) o token heredado vigente. None si no es válido (nunca lanza)."""
    if not token or not bot_token or len(token) > MAX_TOKEN_LENGTH:
        return None
    token = token.strip()
    if token.count(".") == 2:
        return _verify_jwt(token, bot_token)
    if token.count(".") == 1 and ACCEPT_LEGACY_SESSION_TOKENS:
        return _verify_legacy(token, bot_token)
    return None


__all__ = ["validate_init_data", "parse_telegram_user_id", "verify_widget_login", "issue_session_token",
           "verify_session_token", "session_secret", "TelegramIdentity", "SessionClaims", "INIT_DATA_MAX_AGE",
           "SESSION_TTL_SECONDS"]