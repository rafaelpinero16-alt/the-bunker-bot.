"""
tests/test_v81.py — Pruebas de los cambios v8.1 sin aiogram ni base de datos reales.
    python -m pytest tests/test_v81.py -q      (o: python tests/test_v81.py)
"""
import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _stub_aiogram():
    """Dobles mínimos de aiogram para importar handlers/payments.py en pruebas."""
    class _Any:
        def __init__(self, *a, **k): pass
        def __call__(self, *a, **k):
            if len(a) == 1 and callable(a[0]) and not k and not isinstance(a[0], _Any):
                return a[0]          # usado como decorador
            return _Any()
        def __getattr__(self, name): return _Any()
        def __invert__(self): return _Any()
        def __and__(self, o): return _Any()
        def __eq__(self, o): return _Any()
        def __hash__(self): return 0
    aiogram = types.ModuleType("aiogram")
    aiogram.Router = lambda *a, **k: _Any()
    aiogram.F = _Any()
    aiogram.Bot = object
    exc = types.ModuleType("aiogram.exceptions")
    exc.TelegramBadRequest = type("TelegramBadRequest", (Exception,), {})
    exc.TelegramForbiddenError = type("TelegramForbiddenError", (Exception,), {})
    tps = types.ModuleType("aiogram.types")
    for n in ("Message", "CallbackQuery", "InlineKeyboardMarkup", "InlineKeyboardButton",
              "LabeledPrice", "PreCheckoutQuery", "WebAppInfo"):
        setattr(tps, n, _Any)
    flt = types.ModuleType("aiogram.filters")
    flt.Command = _Any
    flt.CommandObject = object
    sys.modules.update({"aiogram": aiogram, "aiogram.exceptions": exc,
                        "aiogram.types": tps, "aiogram.filters": flt})


_stub_aiogram()
try:
    from handlers import payments  # noqa: E402  (estructura del repositorio)
except ImportError:
    import payments  # noqa: E402
from telegram_html import normalize_telegram_html, telegram_html_to_plain, visible_length  # noqa: E402


def test_cache_ttl_in_range():
    assert 10 <= payments.CHANNEL_PLAN_CACHE_TTL <= 15


def test_invalidate_clears_ram_immediately():
    payments._cache_channel_plan(7, {"channel_id": -100, "status": "active", "stars_price": 50})
    assert payments._cached_channel_plan(7)[0]
    payments.invalidate_channel_plan_cache(7)
    assert payments._cached_channel_plan(7) == (False, None)


def test_inflight_read_cannot_resurrect_paused_plan():
    """Lectura lenta iniciada antes de pausar → no debe volver a cachear 'active'."""
    async def scenario():
        gate = asyncio.Event()

        async def slow_get(plan_id):
            await gate.wait()
            return {"channel_id": -100, "status": "active", "stars_price": 50}   # dato viejo

        payments.get_channel_plan = slow_get
        refresh = asyncio.create_task(payments._refresh_channel_plan_cache(8))
        await asyncio.sleep(0)
        payments.invalidate_channel_plan_cache(8)      # el operador pausa desde la Mini App
        gate.set()
        await refresh
        return payments._cached_channel_plan(8)

    assert asyncio.run(scenario()) == (False, None)


def test_global_invalidation_also_blocks_inflight():
    async def scenario():
        gate = asyncio.Event()

        async def slow_get(plan_id):
            await gate.wait()
            return {"channel_id": -100, "status": "active", "stars_price": 50}

        payments.get_channel_plan = slow_get
        task = asyncio.create_task(payments._refresh_channel_plan_cache(9))
        await asyncio.sleep(0)
        payments.invalidate_channel_plan_cache(None)
        gate.set()
        await task
        return payments._cached_channel_plan(9)

    assert asyncio.run(scenario()) == (False, None)


def test_invalidate_drops_issued_offers_of_plan():
    payments._register_offer("chan_sub_-100_11_30", 50)
    payments._register_offer("chan_sub_-100_12_30", 70)
    payments.invalidate_channel_plan_cache(11)
    assert payments._offer_matches("chan_sub_-100_11_30", 50) is None
    assert payments._offer_matches("chan_sub_-100_12_30", 70) is True


def test_license_lock_serializes_same_chat():
    async def scenario():
        order = []

        async def buy(tag):
            async with payments.license_lock(-555):
                order.append(f"{tag}-in")
                await asyncio.sleep(0.01)
                order.append(f"{tag}-out")

        await asyncio.gather(buy("a"), buy("b"))
        return order

    out = asyncio.run(scenario())
    assert out in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


def test_html_normalizer():
    assert normalize_telegram_html("<b>x</strong>") == "<b>x</b>"
    assert normalize_telegram_html("A &amp; B < C") == "A &amp; B &lt; C"
    assert normalize_telegram_html("<b onclick=x>y</b>") == "&lt;b onclick=x&gt;y"
    assert normalize_telegram_html("<code><b>x</b></code>") == "<code>&lt;b&gt;x&lt;/b&gt;</code>"
    assert telegram_html_to_plain("<b>Hola</b> &amp; adiós") == "Hola & adiós"
    assert visible_length("<b>😀</b>") == 2   # UTF-16: un emoji cuenta 2


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("OK ", t.__name__)
    print(f"{len(tests)} pruebas superadas")
