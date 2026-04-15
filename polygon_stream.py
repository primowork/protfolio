"""
Polygon.io stream manager — dual-mode (WebSocket or REST).

Auto-detects plan on startup:
  • WS auth_success   → live trade stream, ~200ms latency
  • WS plan error     → REST polling mode, 1-min OHLC, 60s poll
  • No API key        → disabled, yfinance fallback stays active

Same public interface regardless of mode:
    stream.get_prices()   → {sym: {price, ts, src}}
    stream.is_active()    → bool
    stream.status()       → dict
    stream.subscribe([…]) → coroutine
    stream.on_batch       → async callback(updates: dict)
"""

import asyncio
import json
import logging
import os
import time
from typing import Callable, Optional
from datetime import datetime, timezone

logger = logging.getLogger("polygon")

# ── orjson → stdlib fallback ─────────────────────────────────────────────
try:
    import orjson
    def _loads(raw): return orjson.loads(raw)
except ImportError:
    def _loads(raw): return json.loads(raw)

# ── config ───────────────────────────────────────────────────────────────
API_KEY          = os.getenv("POLYGON_API_KEY", "")
WS_URL           = "wss://socket.polygon.io/stocks"
REST_BASE        = "https://api.polygon.io"
QUEUE_MAX        = 10_000
BATCH_MS         = 200      # WS flush interval
REST_POLL_S      = 60       # REST polling interval (seconds)
WS_ACTIVE_CUTOFF = 5        # WS stale if no tick for N seconds
REST_ACTIVE_CUTOFF = 90     # REST stale if no update for N seconds
RECONNECT_DELAY  = 3        # WS reconnect delay


def _active_cutoff(mode: str) -> int:
    return WS_ACTIVE_CUTOFF if mode == "ws" else REST_ACTIVE_CUTOFF


# ── tick normalizer (WS path) ────────────────────────────────────────────
def _normalize_trade(msg: dict) -> Optional[dict]:
    if msg.get("ev") != "T" or not msg.get("sym"):
        return None
    return {
        "sym":   msg["sym"],
        "price": msg.get("p"),
        "size":  msg.get("s"),
        "ts":    msg.get("t", 0) / 1000,  # ms → seconds
        "src":   "polygon-ws",
    }


class PolygonStream:
    def __init__(self, on_batch: Optional[Callable] = None):
        self._prices:   dict  = {}
        self._subs:     set   = set()
        self._ws              = None
        self._running         = False
        self._connected       = False
        self._mode:     str   = "none"   # "ws" | "rest" | "none"
        self._last_tick: float = 0.0
        self._queue: Optional[asyncio.Queue] = None
        self._on_batch = on_batch

    # ── public ──────────────────────────────────────────────────────────

    def get_prices(self) -> dict:
        return dict(self._prices)

    def is_active(self) -> bool:
        if not self._connected:
            return False
        return time.time() - self._last_tick < _active_cutoff(self._mode)

    def status(self) -> dict:
        age = round(time.time() - self._last_tick, 1) if self._last_tick else None
        return {
            "connected":    self._connected,
            "active":       self.is_active(),
            "mode":         self._mode,
            "symbols":      sorted(self._subs),
            "symbolCount":  len(self._subs),
            "lastTickAgo":  age,
            "queueDepth":   self._queue.qsize() if self._queue else 0,
        }

    async def subscribe(self, symbols: list) -> None:
        new = {s.upper() for s in symbols} - self._subs
        if not new:
            return
        self._subs.update(new)
        if self._mode == "ws" and self._ws and self._connected:
            params = ",".join(f"T.{s}" for s in new)
            try:
                await self._ws.send(json.dumps({"action": "subscribe", "params": params}))
                logger.info(f"Polygon WS: +{len(new)} symbols")
            except Exception as e:
                logger.warning(f"WS subscribe error: {e}")

    # ── WS mode ─────────────────────────────────────────────────────────

    async def _ws_producer(self, ws) -> None:
        async for raw in ws:
            msgs = _loads(raw)
            for m in msgs:
                if self._queue.full():
                    try: self._queue.get_nowait()
                    except asyncio.QueueEmpty: pass
                try: self._queue.put_nowait(m)
                except asyncio.QueueFull: pass

    async def _ws_consumer(self) -> None:
        interval = BATCH_MS / 1000
        while self._running:
            await asyncio.sleep(interval)
            if self._queue is None or self._queue.empty():
                continue
            updates = {}
            while not self._queue.empty():
                try:
                    tick = _normalize_trade(self._queue.get_nowait())
                    if tick:
                        sym = tick["sym"]
                        self._prices[sym] = tick
                        updates[sym] = tick
                        self._last_tick = time.time()
                except asyncio.QueueEmpty:
                    break
            if updates:
                await self._fire(updates)

    async def _ws_connect(self) -> bool:
        """
        Opens one WS session. Returns True if auth_success, False if plan error.
        Raises on other failures.
        """
        try:
            import websockets
        except ImportError:
            raise RuntimeError("pip install websockets")

        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            # read connection banner
            await ws.recv()
            # send auth
            await ws.send(json.dumps({"action": "auth", "params": API_KEY}))
            resp = _loads(await ws.recv())

            if any(m.get("status") == "auth_success" for m in resp):
                logger.info("Polygon WS: auth_success ✓")
                self._connected = True
                self._mode = "ws"
                if self._subs:
                    params = ",".join(f"T.{s}" for s in self._subs)
                    await ws.send(json.dumps({"action": "subscribe", "params": params}))
                    logger.info(f"Polygon WS: subscribed {len(self._subs)} symbols")
                await self._ws_producer(ws)
                return True

            # check for plan restriction
            msgs_str = str(resp).lower()
            if "plan" in msgs_str or "upgrade" in msgs_str or "auth_failed" in msgs_str:
                logger.warning(f"Polygon WS plan restriction — switching to REST mode. ({resp})")
                return False

            raise RuntimeError(f"Polygon WS unexpected auth response: {resp}")

    async def _run_ws(self) -> None:
        self._queue = asyncio.Queue(maxsize=QUEUE_MAX)
        consumer = asyncio.create_task(self._ws_consumer())
        try:
            plan_ok = await self._ws_connect()
            if not plan_ok:
                consumer.cancel()
                return  # caller will switch to REST
            while self._running:
                try:
                    await self._ws_connect()
                except Exception as e:
                    self._connected = False
                    self._ws = None
                    logger.warning(f"Polygon WS error: {e!r} — reconnecting in {RECONNECT_DELAY}s")
                    await asyncio.sleep(RECONNECT_DELAY)
        finally:
            consumer.cancel()

    # ── REST mode ────────────────────────────────────────────────────────

    async def _rest_fetch_one(self, session, sym: str) -> Optional[dict]:
        """Fetch latest 1-minute bar close for one symbol."""
        from datetime import timedelta
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # 5-day window covers weekends + long holiday weekends
        from_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        url = (f"{REST_BASE}/v2/aggs/ticker/{sym}/range/1/minute"
               f"/{from_date}/{today}"
               f"?adjusted=true&sort=desc&limit=1&apiKey={API_KEY}")
        try:
            resp = await session.get(url)
            data = _loads(await resp.aread())
            results = data.get("results")
            if results:
                bar = results[0]
                return {
                    "sym":   sym,
                    "price": bar.get("c"),          # close of last minute bar
                    "size":  bar.get("v"),
                    "ts":    bar.get("t", 0) / 1000,  # ms → seconds
                    "src":   "polygon-rest",
                    "ohlc":  {
                        "o": bar.get("o"), "h": bar.get("h"),
                        "l": bar.get("l"), "c": bar.get("c"),
                    },
                }
        except Exception as e:
            logger.warning(f"Polygon REST {sym}: {e}")
        return None

    async def _run_rest(self) -> None:
        try:
            import httpx
        except ImportError:
            logger.error("pip install httpx — REST mode unavailable")
            return

        self._mode = "rest"
        self._connected = True
        logger.info("Polygon: REST polling mode active (60s interval)")

        async with httpx.AsyncClient(timeout=10) as session:
            while self._running:
                syms = list(self._subs)
                if syms:
                    tasks = [self._rest_fetch_one(session, s) for s in syms]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    updates = {}
                    for r in results:
                        if isinstance(r, dict) and r.get("price") is not None:
                            sym = r["sym"]
                            self._prices[sym] = r
                            updates[sym] = r
                            self._last_tick = time.time()
                    if updates:
                        logger.info(f"Polygon REST: refreshed {len(updates)} prices")
                        await self._fire(updates)

                await asyncio.sleep(REST_POLL_S)

    # ── shared ──────────────────────────────────────────────────────────

    async def _fire(self, updates: dict) -> None:
        if self._on_batch and updates:
            try:
                if asyncio.iscoroutinefunction(self._on_batch):
                    asyncio.create_task(self._on_batch(updates))
                else:
                    self._on_batch(updates)
            except Exception as e:
                logger.warning(f"on_batch error: {e}")

    async def run(self) -> None:
        """
        Boot sequence:
        1. Try WS auth — if plan supports it, run WS loop forever.
        2. If plan error → fall back to REST polling loop.
        3. If no API key → exit silently (yfinance stays active).
        """
        if not API_KEY:
            logger.warning("POLYGON_API_KEY not set — Polygon disabled, yfinance active")
            return

        self._running = True

        # try WS first (one attempt to check plan)
        try:
            import websockets  # noqa
            ws_ok = await self._ws_connect_probe()
            if ws_ok:
                await self._run_ws()
                return
        except ImportError:
            logger.warning("websockets not installed — using REST mode")
        except Exception as e:
            logger.warning(f"Polygon WS probe error: {e!r} — using REST mode")

        # REST fallback
        await self._run_rest()

    async def _ws_connect_probe(self) -> bool:
        """
        Single throw-away WS connection just to check if the plan allows WS.
        Returns True = WS ok, False = plan restriction.
        """
        import websockets
        try:
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.recv()   # banner
                await ws.send(json.dumps({"action": "auth", "params": API_KEY}))
                resp = _loads(await ws.recv())
                if any(m.get("status") == "auth_success" for m in resp):
                    logger.info("Polygon WS: plan supports WebSocket ✓")
                    return True
                msgs_str = str(resp).lower()
                if "plan" in msgs_str or "upgrade" in msgs_str:
                    logger.info("Polygon WS: plan requires upgrade — using REST")
                    return False
                raise RuntimeError(f"Unexpected WS auth: {resp}")
        except Exception as e:
            raise

    def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            asyncio.create_task(self._ws.close())


# ── module-level singleton ────────────────────────────────────────────────
stream = PolygonStream()
