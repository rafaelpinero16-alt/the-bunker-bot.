#!/usr/bin/env python3
"""
railway_redeploy_probe.py — The Bunker OS · v28 · Prueba de redeploy en Railway

Sondea el servicio público DURANTE un redeploy y mide lo que importa del arranque v26:
  • /health contesta 200 en cuanto el proceso nuevo abre el puerto (aunque el bot siga arrancando),
  • mientras arranca, la API devuelve 503 + Retry-After (compuerta StartupGate) y no 500/404,
  • cuánto dura el corte (con volumen, Railway detiene la instancia vieja antes de arrancar la nueva),
  • cuánto tarda la instancia nueva en pasar de "puerto abierto" a "ready".

Solo usa la biblioteca estándar. No necesita credenciales ni toca datos: la ruta de compuerta es una URL
inexistente (/api/__gate_probe) que responde 503 mientras arranca y 404 cuando la API ya está habilitada.

Uso:
    python scripts/railway_redeploy_probe.py https://TU-SERVICIO.up.railway.app --duration 420
    (lánzalo, y con el sondeo ya corriendo pulsa "Redeploy" en Railway)

Salida: línea de tiempo de fases, resumen con veredicto y un CSV con cada muestra (--csv).
"""
from __future__ import annotations

import argparse
import csv
import json
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request

GATE_PATH = "/api/__gate_probe"


def _get(url: str, timeout: float) -> tuple:
    """→ (status | None, latencia_ms, cuerpo_json | None, retry_after | None, error | None)"""
    started = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "bunker-redeploy-probe/1.0", "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read(4096)
            status, retry = resp.status, resp.headers.get("Retry-After")
    except urllib.error.HTTPError as ex:
        raw, status, retry = ex.read(4096) if ex.fp else b"", ex.code, ex.headers.get("Retry-After")
    except Exception as ex:   # conexión rechazada, timeout, TLS, DNS…
        return None, (time.monotonic() - started) * 1000, None, None, type(ex).__name__
    latency = (time.monotonic() - started) * 1000
    try:
        body = json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        body = None
    return status, latency, body, retry, None


def classify(health_status, health_body, gate_status) -> str:
    if health_status is None:
        return "DOWN"                         # nadie escucha (corte entre instancias)
    if health_status != 200:
        return f"EDGE_{health_status}"        # p. ej. 502/503 del proxy de Railway sin instancia detrás
    ready = isinstance(health_body, dict) and health_body.get("ready")
    if ready is None and isinstance(health_body, dict):
        ready = True                          # build anterior a v26: /health no informa "ready"
    if not ready:
        return "STARTING"                     # puerto abierto, compuerta cerrada
    return "READY" if gate_status in (404, 405, 401, 403) else f"READY_GATE_{gate_status}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", help="URL pública del servicio, p. ej. https://bunker.up.railway.app")
    ap.add_argument("--duration", type=float, default=420, help="segundos de sondeo (por defecto 420)")
    ap.add_argument("--interval", type=float, default=0.5, help="segundos entre muestras (por defecto 0.5)")
    ap.add_argument("--timeout", type=float, default=3.0, help="timeout de cada petición (por defecto 3 s)")
    ap.add_argument("--csv", default="redeploy_probe.csv", help="archivo CSV de muestras ('' para no guardarlo)")
    ap.add_argument("--max-health-ms", type=float, default=1000, help="p95 máximo aceptable de /health en ms")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    t0 = time.monotonic()
    samples, phases = [], []
    print(f"▶ Sondeando {base} durante {args.duration:.0f} s. Lanza el redeploy en Railway ahora.\n")
    try:
        while time.monotonic() - t0 < args.duration:
            tick = time.monotonic()
            h_status, h_ms, h_body, _r, h_err = _get(base + "/health", args.timeout)
            g_status, _g_ms, _g_body, g_retry, _g_err = _get(base + GATE_PATH, args.timeout) if h_status == 200 else (None,) * 5
            phase = classify(h_status, h_body, g_status)
            rel = tick - t0
            samples.append({"t_s": round(rel, 2), "phase": phase, "health_status": h_status, "health_ms": round(h_ms, 1),
                            "gate_status": g_status, "gate_retry_after": g_retry, "error": h_err})
            if not phases or phases[-1][1] != phase:
                phases.append((rel, phase))
                extra = f" ({h_err})" if h_err else ""
                print(f"  t={rel:7.1f}s  → {phase}{extra}")
            time.sleep(max(0.0, args.interval - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        print("\n(interrumpido)")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(samples[0].keys()) if samples else ["t_s"])
            writer.writeheader()
            writer.writerows(samples)

    # ---------------- Resumen ----------------
    def first(pred, after=0.0):
        return next((s["t_s"] for s in samples if s["t_s"] >= after and pred(s)), None)

    went_down = first(lambda s: s["phase"] != "READY")
    back_up = first(lambda s: s["health_status"] == 200, after=went_down or 0) if went_down is not None else None
    ready_again = first(lambda s: s["phase"] == "READY", after=back_up or 0) if back_up is not None else None
    starting = [s for s in samples if s["phase"] == "STARTING"]
    bad_gate = [s for s in starting if s["gate_status"] not in (503,)]
    health_ok = [s["health_ms"] for s in samples if s["health_status"] == 200]
    p95 = statistics.quantiles(health_ok, n=20)[18] if len(health_ok) >= 20 else (max(health_ok) if health_ok else None)
    gate_errors = [s for s in samples if s["phase"].startswith("READY_GATE_")]

    print("\n══════════ Resumen ══════════")
    print(f"Muestras: {len(samples)} · fases: {' → '.join(p for _, p in phases) or '—'}")
    if went_down is None:
        print("⚠️  No se observó ningún redeploy (siempre READY). Repite la prueba lanzando el redeploy con el sondeo activo.")
        return 2
    downtime = (back_up - went_down) if back_up is not None else None
    warmup = (ready_again - back_up) if (ready_again is not None and back_up is not None) else None
    print(f"Corte (sin /health 200):          {downtime:.1f} s" if downtime is not None else "Corte: el servicio NO volvió")
    print(f"Puerto abierto → API habilitada:  {warmup:.1f} s" if warmup is not None else "API habilitada: NO observada")
    print(f"Muestras en STARTING:             {len(starting)} (compuerta 503 correcta en {len(starting) - len(bad_gate)})")
    print(f"/health p95:                      {p95:.0f} ms" if p95 is not None else "/health p95: —")

    checks = [
        ("El servicio volvió a READY", ready_again is not None),
        ("La compuerta respondió 503 en todo el arranque", not bad_gate),
        ("Sin errores de la API una vez READY", not gate_errors),
        (f"/health p95 < {args.max_health_ms:.0f} ms", p95 is not None and p95 < args.max_health_ms),
    ]
    print()
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")
    if starting:
        print("  ℹ️  Se observó la fase STARTING: el puerto se abrió antes del candado/BD (arranque v26 activo).")
    else:
        print("  ℹ️  No se observó STARTING: el arranque fue más rápido que el intervalo de sondeo (correcto).")
    if args.csv:
        print(f"\nCSV: {args.csv}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
