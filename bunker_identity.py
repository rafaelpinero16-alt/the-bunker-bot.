"""
bunker_identity.py — The Bunker Command OS © 2026 · Cloud Media Management

v47 · Fuente ÚNICA de las identidades privilegiadas. Ningún módulo lleva IDs escritos en el código: todos leen de aquí,
y aquí solo se leen variables de entorno.

  • OWNER_ID (alias CREATOR_ID / BOT_OWNER_ID) → Creador Absoluto. Un solo ID con jerarquía máxima e inmunidad total:
    ningún handler, middleware ni consulta de la base de datos puede banearlo, expulsarlo, silenciarlo, advertirlo,
    marcarlo como userbot ni restringirlo.
  • ADMIN_IDS (y SUPER_ADMIN_IDS, opcional) → Arquitectos / Super Admins. Separados por comas, espacios o punto y coma.
    El Creador Absoluto se incluye SIEMPRE entre los Arquitectos (is_owner ⊂ is_super_admin).

Mismas reglas de lectura que assistant.py (v46): los tokens no numéricos o no positivos se avisan y se ignoran.
Sin dependencias del proyecto (solo stdlib): se puede importar desde cualquier módulo sin ciclos.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

_log = logging.getLogger("bunker_identity")
_ID_SPLIT_RE = re.compile(r"[\s,;]+")


def parse_id_list(*env_names: str) -> set[int]:
    """IDs de usuario positivos leídos de una o varias variables de entorno (valores inválidos: aviso y se ignoran)."""
    ids: set[int] = set()
    for env_name in env_names:
        raw = os.getenv(env_name, "") or ""
        for token in _ID_SPLIT_RE.split(raw.strip()):
            token = token.strip().strip('"').strip("'")
            if not token:
                continue
            try:
                value = int(token)
            except ValueError:
                _log.warning(f"⚠️ [Identidades] Valor ignorado en {env_name}: {token!r} (no es un ID numérico).")
                continue
            if value > 0:
                ids.add(value)
            else:
                _log.warning(f"⚠️ [Identidades] ID ignorado en {env_name}: {value} (los usuarios tienen IDs positivos).")
    return ids


def _resolve_owner_id() -> Optional[int]:
    for env_name in ("OWNER_ID", "CREATOR_ID", "BOT_OWNER_ID"):
        found = parse_id_list(env_name)
        if found:
            if len(found) > 1:
                _log.warning(f"⚠️ [Identidades] {env_name} contiene varios IDs; el Creador Absoluto es uno solo: "
                             f"se usa {min(found)}.")
            return min(found)
    return None


OWNER_ID: Optional[int] = _resolve_owner_id()
# Conjunto compartido por todos los módulos (cada uno lo expone con su nombre histórico SUPER_ADMIN_IDS).
SUPER_ADMIN_IDS: set[int] = parse_id_list("ADMIN_IDS", "SUPER_ADMIN_IDS")
if OWNER_ID is not None:
    SUPER_ADMIN_IDS.add(OWNER_ID)

if OWNER_ID is None:
    _log.warning("⚠️ [Identidades] OWNER_ID no está definido: no hay Creador Absoluto con inmunidad total. "
                 "Defínelo en las variables de Railway.")
if not SUPER_ADMIN_IDS:
    _log.critical("🚨 [Identidades] Ni OWNER_ID ni ADMIN_IDS están definidos: el bot no reconoce a ningún Arquitecto "
                  "(sin Matriz del Arquitecto, sin inmunidad ni acceso global). Define OWNER_ID y ADMIN_IDS en Railway.")


def _as_int(user_id) -> Optional[int]:
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def is_owner(user_id) -> bool:
    """Creador Absoluto (OWNER_ID): jerarquía máxima, por encima de cualquier Arquitecto o administrador."""
    uid = _as_int(user_id)
    return OWNER_ID is not None and uid == OWNER_ID


def is_super_admin(user_id) -> bool:
    """Arquitecto (ADMIN_IDS) o Creador Absoluto. Acepta int o str numérico; cualquier otro valor → False."""
    uid = _as_int(user_id)
    return uid is not None and (uid == OWNER_ID or uid in SUPER_ADMIN_IDS)


def custodian_id() -> Optional[int]:
    """
    Cuenta que custodia un chat cuyo creador real no se puede verificar (antes, un ID fijo escrito en el código):
    el Creador Absoluto o, sin él, el Arquitecto de ID más bajo (determinista). None si no hay ninguno configurado.
    """
    if OWNER_ID is not None:
        return OWNER_ID
    admins = SUPER_ADMIN_IDS - ({OWNER_ID} if OWNER_ID is not None else set())
    return min(admins) if admins else None
