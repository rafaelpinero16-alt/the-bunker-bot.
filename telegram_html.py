"""
telegram_html.py — Normalización del HTML que escribe el operador (promo_text, custom_welcome).

Misma gramática que ui.safeTelegramHtml (bunker_minapp/js/ui.js), para que la vista previa de la
Mini App y lo que Telegram publica sean idénticos:

  • Etiquetas permitidas, SIN atributos: b, i, u, s, code (+ alias strong, em, ins, strike, del,
    que se canonicalizan: <strong>…</b> deja de ser un error de Telegram).
  • Entidades válidas de Telegram (&lt; &gt; &amp; &quot; &#NN; &#xHH;) se conservan tal cual.
  • Cualquier otro <, >, & se escapa → Telegram nunca rechaza el mensaje con "can't parse entities".
  • Cierres huérfanos se descartan, cruces se corrigen y lo que quede abierto se cierra al final.
  • Dentro de <code> no se abren otras etiquetas (Telegram no admite entidades anidadas en code).

Sin dependencias: se usa en channel_plans_api.py y handlers/payments.py.
"""
from __future__ import annotations

import html
import re

ALIASES = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "ins": "u",
           "s": "s", "strike": "s", "del": "s", "code": "code"}

_TOKEN_RE = re.compile(
    r"(<(/?)(b|strong|i|em|u|ins|s|strike|del|code)>)"            # 1-3: etiqueta permitida
    r"|(&(?:lt|gt|amp|quot|#\d{1,7}|#x[0-9a-fA-F]{1,6});)",      # 4: entidad válida
    re.IGNORECASE,
)


def normalize_telegram_html(text: object) -> str:
    """Devuelve HTML válido para parse_mode=HTML de Telegram."""
    src = "" if text is None else str(text)
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(src):
        out.append(html.escape(src[pos:m.start()], quote=False))
        pos = m.end()
        if m.group(4):                       # entidad ya válida: se respeta
            out.append(m.group(4))
            continue
        closing, name = bool(m.group(2)), ALIASES[m.group(3).lower()]
        in_code = "code" in stack
        if not closing:
            if in_code:                      # Telegram no admite entidades dentro de code
                out.append(html.escape(m.group(1), quote=False))
                continue
            stack.append(name)
            out.append(f"<{name}>")
            continue
        if name not in stack:                # cierre huérfano
            if in_code:
                out.append(html.escape(m.group(1), quote=False))
            continue
        if in_code and name != "code":       # dentro de code, solo </code> es etiqueta
            out.append(html.escape(m.group(1), quote=False))
            continue
        # Cierra hasta la etiqueta pedida y reabre las intermedias: <b><i>x</b>y</i> → <b><i>x</i></b><i>y</i>
        reopen: list[str] = []
        while stack:
            top = stack.pop()
            out.append(f"</{top}>")
            if top == name:
                break
            reopen.append(top)
        for tag in reversed(reopen):
            stack.append(tag)
            out.append(f"<{tag}>")
    out.append(html.escape(src[pos:], quote=False))
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def telegram_html_to_plain(text: object) -> str:
    """Texto plano (descripciones de factura): sin etiquetas y con entidades decodificadas."""
    return html.unescape(re.sub(r"<[^>]*>", "", normalize_telegram_html(text)))


def visible_length(text_html: str) -> int:
    """Longitud que cuenta Telegram para captions/mensajes: texto tras parsear, en unidades UTF-16."""
    return len(telegram_html_to_plain(text_html).encode("utf-16-le")) // 2
