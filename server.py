from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn, logging, asyncio, os, json, time
from datetime import datetime, timedelta
import concurrent.futures
import yfinance as yf

# load .env for local dev (no-op if file absent or python-dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from polygon_stream import stream as polygon_stream

logging.basicConfig(level=logging.INFO)


# ── WebSocket connection manager ─────────────────────────────────────────
class _WSManager:
    """Tracks all connected frontend clients and broadcasts price updates."""
    def __init__(self):
        self._clients: set = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logging.info(f"WS client connected (total: {len(self._clients)})")

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logging.info(f"WS client disconnected (total: {len(self._clients)})")

    async def broadcast(self, updates: dict) -> None:
        """Push price update to all connected frontend clients."""
        if not self._clients:
            return
        payload = json.dumps({
            "type":   "prices",
            "prices": {
                sym: {"price": d.get("price"), "ts": d.get("ts")}
                for sym, d in updates.items()
            },
            "source": "polygon-live",
            "active": True,
        })
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

ws_manager = _WSManager()

# ── Alerts cache (5-minute TTL) ──────────────────────────────────────
_alerts_cache: dict = {"data": None, "ts": 0.0, "key": ""}
ALERTS_TTL = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Wire broadcast callback BEFORE starting the stream
    polygon_stream._on_batch = ws_manager.broadcast
    task = asyncio.create_task(polygon_stream.run())
    yield
    polygon_stream.stop()
    task.cancel()

app = FastAPI(lifespan=lifespan)

_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_dir, "app.html"), "r", encoding="utf-8") as f:
    HTML_CONTENT = f.read()

@app.get("/")
async def root():
    return HTMLResponse(HTML_CONTENT)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/debug")
async def debug():
    try:
        t = yf.Ticker("AAPL")
        info = t.fast_info
        return {"lastPrice": info.last_price, "prevClose": info.previous_close}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/usdils")
async def get_usdils():
    try:
        t = yf.Ticker("ILS=X")
        rate = t.fast_info.last_price
        return {"rate": rate}
    except Exception as e:
        return {"rate": 3.7, "error": str(e)}

@app.get("/api/prices")
async def get_prices(symbols: str = ""):
    if not symbols:
        return JSONResponse({"prices": {}, "prevClose": {}})
    import pandas as pd
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    prices, prev = {}, {}

    # ── current prices: fast_info.last_price (real-time) ─────────────────
    try:
        tickers = yf.Tickers(" ".join(sym_list))
        for sym in sym_list:
            try:
                lp = tickers.tickers[sym].fast_info.last_price
                if lp: prices[sym] = lp
            except Exception as e:
                logging.warning(f"price {sym}: {e}")
    except Exception as e:
        logging.error(e)

    # ── prevClose: yf.download history ───────────────────────────────────
    # fast_info.previous_close returns the WRONG date's close (yfinance bug).
    # yf.download includes today's close as iloc[-1] and yesterday's close as
    # iloc[-2], which matches Google Finance's "previous close" exactly.
    try:
        hist = yf.download(sym_list, period="5d", auto_adjust=True,
                           progress=False, threads=True)
        if not hist.empty:
            closes = hist["Close"]
            # iloc[-2] = previous completed session close (confirmed matches GF)
            if len(closes) >= 2:
                prev_row = closes.iloc[-2]
                if isinstance(prev_row, pd.Series):     # multiple tickers
                    for sym in sym_list:
                        if sym in prev_row.index:
                            v = prev_row[sym]
                            if pd.notna(v): prev[sym] = float(v)
                else:                                   # single ticker
                    if pd.notna(prev_row): prev[sym_list[0]] = float(prev_row)
    except Exception as e:
        logging.error(f"prevClose download: {e}")
        # fallback: regularMarketPreviousClose from .info (also correct)
        import concurrent.futures
        def _pc(sym):
            try:
                v = yf.Ticker(sym).info.get("regularMarketPreviousClose")
                return sym, v
            except: return sym, None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for sym, v in pool.map(_pc, sym_list):
                if v: prev[sym] = v

    logging.info(f"prices={len(prices)} prevClose={len(prev)}")
    return JSONResponse({"prices": prices, "prevClose": prev})

@app.get("/api/ma200")
async def get_ma200(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    result = {}
    try:
        tickers = yf.Tickers(" ".join(sym_list))
        for sym in sym_list:
            try:
                fi = tickers.tickers[sym].fast_info
                price = fi.last_price
                ma200 = getattr(fi, "two_hundred_day_average", None)
                if price and ma200:
                    pct = round((price - ma200) / ma200 * 100, 1)
                    result[sym] = {
                        "price": round(price, 2),
                        "ma200": round(ma200, 2),
                        "below": price < ma200,
                        "pct": pct
                    }
            except Exception as e:
                logging.warning(f"MA200 {sym}: {e}")
    except Exception as e:
        logging.error(e)
    return JSONResponse(result)


@app.get("/api/pe")
async def get_pe(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            info = yf.Ticker(sym).info
            trailing = info.get("trailingPE")
            forward  = info.get("forwardPE")
            if trailing or forward:
                return sym, {
                    "trailingPE": round(trailing, 1) if trailing else None,
                    "forwardPE":  round(forward,  1) if forward  else None
                }
        except Exception as e:
            logging.warning(f"PE {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


@app.get("/api/revenue")
async def get_revenue(symbols: str = "", period: str = "annual"):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            t = yf.Ticker(sym)
            income = t.quarterly_income_stmt if period == "quarterly" else t.income_stmt
            if income is None or income.empty:
                return sym, None
            rev_row = None
            # Prefer exact "Total Revenue" first, then "Operating Revenue",
            # then any row containing "Revenue" that is NOT a cost/expense row.
            for idx in income.index:
                if str(idx).strip() == "Total Revenue":
                    rev_row = income.loc[idx]; break
            if rev_row is None:
                for idx in income.index:
                    if str(idx).strip() == "Operating Revenue":
                        rev_row = income.loc[idx]; break
            if rev_row is None:
                for idx in income.index:
                    s = str(idx)
                    if "Revenue" in s and "Cost" not in s and "Expense" not in s:
                        rev_row = income.loc[idx]; break
            if rev_row is None or rev_row.dropna().empty:
                return sym, None
            rev_row = rev_row.dropna().sort_index(ascending=False)

            if period == "quarterly":
                # prefer YoY quarterly (same quarter last year = iloc[3])
                if len(rev_row) >= 4:
                    latest, prev = float(rev_row.iloc[0]), float(rev_row.iloc[3])
                    comp = "YoY"
                elif len(rev_row) >= 2:
                    latest, prev = float(rev_row.iloc[0]), float(rev_row.iloc[1])
                    comp = "QoQ"
                else:
                    return sym, None
            else:
                if len(rev_row) < 2:
                    return sym, None
                latest, prev = float(rev_row.iloc[0]), float(rev_row.iloc[1])
                comp = "YoY"

            if prev == 0:
                return sym, None
            pct = round((latest - prev) / abs(prev) * 100, 1)
            return sym, {
                "latest": round(latest / 1e9, 2),
                "prev":   round(prev   / 1e9, 2),
                "pct":    pct,
                "rising": pct > 0,
                "comp":   comp
            }
        except Exception as e:
            logging.warning(f"Revenue {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


@app.get("/api/gross_margin")
async def get_gross_margin(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            info = yf.Ticker(sym).info
            gm = info.get("grossMargins")
            if gm is not None:
                return sym, {"pct": round(gm * 100, 1)}
        except Exception as e:
            logging.warning(f"GM {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


@app.get("/api/fundamentals")
async def get_fundamentals(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            t      = yf.Ticker(sym)
            info   = t.info
            result = {}
            fcf    = info.get("freeCashflow")
            mktcap = info.get("marketCap")
            roe    = info.get("returnOnEquity")
            if fcf and mktcap and mktcap > 0:
                result["fcfYield"] = round(fcf / mktcap * 100, 1)
            if roe is not None:
                result["roe"] = round(roe * 100, 1)

            # Debt/EBITDA — try info first, then balance sheet + income stmt
            debt   = info.get("totalDebt") or info.get("longTermDebt")
            ebitda = info.get("ebitda") or info.get("EBITDA")
            if (debt is None or not ebitda or ebitda <= 0):
                try:
                    import pandas as pd
                    bs  = t.balance_sheet
                    inc = t.income_stmt
                    if debt is None and not bs.empty:
                        for idx in bs.index:
                            s = str(idx)
                            if s in ("Total Debt", "Long Term Debt", "Net Debt"):
                                v = bs.iloc[:, 0].get(idx)
                                if v is not None and not pd.isna(v):
                                    debt = float(v); break
                    if (not ebitda or ebitda <= 0) and not inc.empty:
                        for idx in inc.index:
                            s = str(idx)
                            if s == "EBITDA":
                                v = inc.iloc[:, 0].get(idx)
                                if v is not None and not pd.isna(v):
                                    ebitda = float(v); break
                        if not ebitda or ebitda <= 0:
                            # compute: EBIT + D&A
                            ebit, da = None, None
                            for idx in inc.index:
                                s = str(idx)
                                if s in ("Operating Income", "EBIT"):
                                    v = inc.iloc[:, 0].get(idx)
                                    if v is not None and not pd.isna(v): ebit = float(v)
                                if "Depreciation" in s:
                                    v = inc.iloc[:, 0].get(idx)
                                    if v is not None and not pd.isna(v): da = float(v)
                            if ebit is not None and da is not None:
                                ebitda = ebit + abs(da)
                except Exception as fe:
                    logging.debug(f"Fundamentals fallback {sym}: {fe}")

            if debt is not None and ebitda and ebitda > 0:
                result["debtEbitda"] = round(debt / ebitda, 1)

            if result:
                return sym, result
        except Exception as e:
            logging.warning(f"Fundamentals {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


@app.websocket("/ws/prices")
async def ws_prices(ws: WebSocket, symbols: str = ""):
    """
    Frontend connects here once. Receives pushed price updates
    from the Polygon batch callback instead of polling.
    Client also sends its symbol list so we subscribe them to the stream.
    """
    await ws_manager.connect(ws)
    try:
        # register symbols immediately
        if symbols:
            sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
            if sym_list:
                await polygon_stream.subscribe(sym_list)

        # keep connection alive; client may also send symbol updates
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
                if msg.get("action") == "subscribe" and msg.get("symbols"):
                    await polygon_stream.subscribe(msg["symbols"])
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


@app.get("/api/live-prices")
async def get_live_prices(symbols: str = ""):
    """
    Returns Polygon live trade prices (if stream active) or falls back to yfinance.
    The frontend sends its holding symbols here; this call also registers them
    for the stream subscription so the next tick arrives fast.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else []

    # register symbols with the stream (no-op if already subscribed)
    if sym_list:
        await polygon_stream.subscribe(sym_list)

    live = polygon_stream.get_prices()
    active = polygon_stream.is_active()

    if active and live:
        # return only requested symbols (or all if no filter)
        result = {s: live[s] for s in sym_list if s in live} if sym_list else live
        return JSONResponse({"prices": result, "source": "polygon-live", "active": True})

    # fallback: yfinance snapshot (same logic as /api/prices)
    prices, prev = {}, {}
    if sym_list:
        try:
            tickers = yf.Tickers(" ".join(sym_list))
            for sym in sym_list:
                try:
                    info = tickers.tickers[sym].fast_info
                    if info.last_price: prices[sym] = {"price": info.last_price, "src": "yfinance"}
                    if info.previous_close: prev[sym] = info.previous_close
                except Exception as e:
                    logging.warning(f"live-prices fallback {sym}: {e}")
        except Exception as e:
            logging.error(e)
    return JSONResponse({"prices": prices, "prevClose": prev, "source": "yfinance", "active": False})


@app.get("/api/stream-status")
async def get_stream_status():
    return JSONResponse(polygon_stream.status())


@app.get("/api/premarket")
async def get_premarket(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            info = yf.Ticker(sym).info
            state = info.get("marketState", "REGULAR")
            reg   = info.get("regularMarketPrice")

            pre_p   = info.get("preMarketPrice")
            pre_pct = info.get("preMarketChangePercent")
            post_p  = info.get("postMarketPrice")
            post_pct= info.get("postMarketChangePercent")

            base = {"state": state, "regularPrice": round(reg, 2) if reg else None}

            if state == "PRE" and pre_p:
                base.update({"session": "PRE",  "price": round(pre_p,2),
                              "changePct": round(pre_pct,2) if pre_pct else None})
            elif state in ("POST","POSTPOST") and post_p:
                base.update({"session": "POST", "price": round(post_p,2),
                              "changePct": round(post_pct,2) if post_pct else None})
            elif state == "CLOSED":
                p = post_p or pre_p
                pct = post_pct or pre_pct
                if p:
                    base.update({"session": "CLOSED", "price": round(p,2),
                                 "changePct": round(pct,2) if pct else None})
                else:
                    base["session"] = "CLOSED"
            else:
                base["session"] = "REGULAR"
            return sym, base
        except Exception as e:
            logging.warning(f"Premarket {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


# ═══════════════════════════════════════════════════════════════════
#  ALERTS MODULE — /api/alerts
# ═══════════════════════════════════════════════════════════════════

def _check_price_move(sym: str, fi) -> list:
    """Alert when daily price move ≥ 5%."""
    try:
        price = fi.last_price
        prev  = fi.previous_close
        if not price or not prev or prev == 0:
            return []
        pct = (price - prev) / prev * 100
        if abs(pct) < 5:
            return []
        direction = "up" if pct > 0 else "down"
        severity  = "high" if abs(pct) >= 8 else "medium"
        verb      = "עלתה" if pct > 0 else "ירדה"
        return [{"symbol": sym, "type": "price_move", "severity": severity,
                 "title": "תנועה חדה",
                 "message": f"{sym} {verb} {abs(pct):.1f}% היום",
                 "direction": direction}]
    except Exception as e:
        logging.debug(f"price_move {sym}: {e}")
        return []


def _check_ma200_cross(sym: str) -> list:
    """Alert when price crossed the 200-day MA at any point in the last 7 trading days."""
    try:
        hist = yf.download(sym, period="1y", auto_adjust=True, progress=False, threads=False)
        if hist.empty:
            return []
        closes = hist["Close"].squeeze().dropna()
        if len(closes) < 205:
            return []
        ma200 = closes.rolling(200).mean()

        # Scan last 8 rows → up to 7 possible crossings
        window_c   = closes.iloc[-8:]
        window_m   = ma200.iloc[-8:]
        last_cross = None                     # direction of most-recent crossing

        for i in range(1, len(window_c)):
            ma_prev = float(window_m.iloc[i - 1])
            ma_curr = float(window_m.iloc[i])
            if ma_prev == 0 or ma_curr == 0:
                continue
            was_above = float(window_c.iloc[i - 1]) >= ma_prev
            now_above = float(window_c.iloc[i])     >= ma_curr
            if was_above != now_above:
                last_cross = "up" if now_above else "down"

        if last_cross is None:
            return []

        price   = float(closes.iloc[-1])
        ma_now  = float(ma200.iloc[-1])
        side    = "מעל" if last_cross == "up" else "מתחת ל"
        return [{"symbol": sym, "type": "ma200_cross", "severity": "high",
                 "title": f"חצייה ממוצע 200 יומי — {'מעל' if last_cross == 'up' else 'מתחת'}",
                 "message": f"{sym} חצתה {side}ממוצע 200 יום בשבוע האחרון (מחיר: ${price:.2f} | MA200: ${ma_now:.2f})",
                 "direction": last_cross}]
    except Exception as e:
        logging.debug(f"ma200_cross {sym}: {e}")
        return []


def _check_ma200w(sym: str) -> list:
    """Alert when price crossed the 200-week MA in the last week."""
    try:
        hist = yf.download(sym, period="5y", interval="1wk",
                           auto_adjust=True, progress=False, threads=False)
        if hist.empty:
            return []
        closes = hist["Close"].squeeze().dropna()
        if len(closes) < 205:
            return []
        ma200w = closes.rolling(200).mean()

        # Check last 3 weekly bars → up to 2 possible crossings
        window_c   = closes.iloc[-3:]
        window_m   = ma200w.iloc[-3:]
        last_cross = None

        for i in range(1, len(window_c)):
            ma_prev = float(window_m.iloc[i - 1])
            ma_curr = float(window_m.iloc[i])
            if ma_prev == 0 or ma_curr == 0:
                continue
            was_above = float(window_c.iloc[i - 1]) >= ma_prev
            now_above = float(window_c.iloc[i])     >= ma_curr
            if was_above != now_above:
                last_cross = "up" if now_above else "down"

        if last_cross is None:
            return []

        price    = float(closes.iloc[-1])
        ma_now   = float(ma200w.iloc[-1])
        side     = "מעל" if last_cross == "up" else "מתחת ל"
        return [{"symbol": sym, "type": "ma200w_cross", "severity": "high",
                 "title": f"חצייה ממוצע 200 שבועי — {'מעל' if last_cross == 'up' else 'מתחת'}",
                 "message": f"{sym} חצתה {side}ממוצע 200 שבוע בשבוע האחרון (מחיר: ${price:.2f} | MA200W: ${ma_now:.2f})",
                 "direction": last_cross}]
    except Exception as e:
        logging.debug(f"ma200w {sym}: {e}")
        return []


def _check_52w_high(sym: str, fi) -> list:
    """Alert when price is within 2% of the 52-week high."""
    try:
        price    = fi.last_price
        high52   = getattr(fi, "year_high", None)
        if not price or not high52 or high52 == 0:
            return []
        if price < high52 * 0.98:
            return []
        pct = price / high52 * 100
        return [{"symbol": sym, "type": "52w_high", "severity": "low",
                 "title": "קרוב לשיא שנתי",
                 "message": f"{sym} ב-${price:.2f} — {pct:.1f}% מהשיא של 52 שבועות ${high52:.2f}",
                 "direction": "up"}]
    except Exception as e:
        logging.debug(f"52w_high {sym}: {e}")
        return []


def _check_drawdown(sym: str) -> list:
    """Alert when price drops ≥ 10% from the 14-day high."""
    try:
        import pandas as pd
        hist = yf.download(sym, period="20d", auto_adjust=True, progress=False, threads=False)
        if hist.empty:
            return []
        closes = hist["Close"].dropna()
        if len(closes) < 5:
            return []
        vals = closes.values.flatten() if hasattr(closes.values, "flatten") else list(closes.values)
        window = vals[-14:] if len(vals) >= 14 else vals
        recent_high = max(window)
        current     = float(vals[-1])
        if recent_high <= 0:
            return []
        drawdown = (current - recent_high) / recent_high * 100
        if drawdown > -10:
            return []
        severity = "high" if drawdown <= -15 else "medium"
        return [{"symbol": sym, "type": "drawdown", "severity": severity,
                 "title": "ירידה מהשיא",
                 "message": f"{sym} ירדה {abs(drawdown):.1f}% מהשיא ב-14 הימים האחרונים (שיא: ${recent_high:.2f})",
                 "direction": "down"}]
    except Exception as e:
        logging.debug(f"drawdown {sym}: {e}")
        return []


def _check_revenue_trend(sym: str) -> list:
    """Alert on strong positive or negative revenue trend (YoY quarterly)."""
    try:
        t = yf.Ticker(sym)
        income = t.quarterly_income_stmt
        if income is None or income.empty:
            return []
        rev_row = None
        for idx in income.index:
            if "Revenue" in str(idx):
                rev_row = income.loc[idx]
                break
        if rev_row is None:
            return []
        rev = rev_row.dropna().sort_index(ascending=False)
        if len(rev) < 4:
            return []
        vals = [float(rev.iloc[i]) for i in range(4)]
        if vals[3] == 0:
            return []
        yoy_pct = (vals[0] - vals[3]) / abs(vals[3]) * 100
        diffs   = [vals[i] - vals[i + 1] for i in range(3)]
        all_up   = all(d > 0 for d in diffs)
        all_down = all(d < 0 for d in diffs)

        if all_down and yoy_pct < -5:
            return [{"symbol": sym, "type": "revenue_trend", "severity": "medium",
                     "title": "מגמת הכנסות שלילית",
                     "message": f"{sym}: הכנסות ירדו {abs(yoy_pct):.1f}% YoY, מגמה רבעונית שלילית עקבית",
                     "direction": "down"}]
        if all_up and yoy_pct > 10:
            return [{"symbol": sym, "type": "revenue_trend", "severity": "low",
                     "title": "מגמת הכנסות חיובית",
                     "message": f"{sym}: הכנסות גדלו {yoy_pct:.1f}% YoY, מגמה רבעונית חיובית עקבית",
                     "direction": "up"}]
        return []
    except Exception as e:
        logging.debug(f"revenue_trend {sym}: {e}")
        return []


def _check_earnings(sym: str) -> list:
    """Alert when earnings date is within the next 14 days."""
    try:
        t   = yf.Ticker(sym)
        cal = t.calendar
        if cal is None:
            return []
        # calendar can be a dict or DataFrame depending on yfinance version
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date", [])
        elif hasattr(cal, "to_dict"):
            dates = cal.get("Earnings Date", []) if hasattr(cal, "get") else []
        else:
            return []
        if not isinstance(dates, (list, tuple)):
            dates = [dates]

        now      = datetime.utcnow()
        horizon  = now + timedelta(days=14)
        alerts   = []
        for ed in dates:
            if ed is None:
                continue
            # Convert pandas Timestamp to datetime if needed
            if hasattr(ed, "to_pydatetime"):
                ed = ed.to_pydatetime()
            if hasattr(ed, "replace"):
                ed_naive = ed.replace(tzinfo=None) if getattr(ed, "tzinfo", None) else ed
                if now <= ed_naive <= horizon:
                    days_left = max(0, (ed_naive - now).days)
                    alerts.append({"symbol": sym, "type": "earnings", "severity": "medium",
                                   "title": "דוח רווחים קרוב",
                                   "message": f"{sym}: דוח רווחים צפוי בעוד {days_left} ימים ({ed_naive.strftime('%d/%m/%Y')})",
                                   "direction": "neutral"})
        return alerts
    except Exception as e:
        logging.debug(f"earnings {sym}: {e}")
        return []


def _check_concentration(sym_list: list, shares_map: dict) -> list:
    """Alert when a single position exceeds 25% of total portfolio value."""
    try:
        syms_with_shares = [s for s in sym_list if s in shares_map and shares_map[s] > 0]
        if not syms_with_shares:
            return []
        tickers = yf.Tickers(" ".join(syms_with_shares))
        values  = {}
        for sym in syms_with_shares:
            try:
                price = tickers.tickers[sym].fast_info.last_price
                if price:
                    values[sym] = price * shares_map[sym]
            except Exception:
                pass
        total = sum(values.values())
        if total <= 0:
            return []
        alerts = []
        for sym, val in values.items():
            pct = val / total * 100
            if pct >= 25:
                severity = "high" if pct >= 40 else "medium"
                alerts.append({"symbol": sym, "type": "concentration", "severity": severity,
                                "title": "ריכוז פורטפוליו גבוה",
                                "message": f"{sym} מהווה {pct:.1f}% מהפורטפוליו (${val:,.0f} מתוך ${ total:,.0f})",
                                "direction": "neutral"})
        return alerts
    except Exception as e:
        logging.warning(f"concentration check: {e}")
        return []


def _run_sym_checks(sym: str, shares_map: dict) -> list:
    """Run all per-symbol alert checks in a single thread."""
    alerts = []
    try:
        fi = yf.Ticker(sym).fast_info
        alerts += _check_price_move(sym, fi)
        alerts += _check_52w_high(sym, fi)
    except Exception as e:
        logging.warning(f"fast_info {sym}: {e}")
    alerts += _check_ma200_cross(sym)
    alerts += _check_ma200w(sym)
    alerts += _check_drawdown(sym)
    alerts += _check_revenue_trend(sym)
    alerts += _check_earnings(sym)
    return alerts


async def _compute_alerts(sym_list: list, shares_map: dict) -> dict:
    loop = asyncio.get_event_loop()
    all_alerts: list = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        sym_tasks  = [loop.run_in_executor(pool, _run_sym_checks, s, shares_map) for s in sym_list]
        conc_task  = loop.run_in_executor(pool, _check_concentration, sym_list, shares_map)
        results    = await asyncio.gather(*sym_tasks, conc_task, return_exceptions=True)

    for r in results:
        if isinstance(r, list):
            all_alerts.extend(r)
        elif isinstance(r, Exception):
            logging.warning(f"alerts task error: {r}")

    order = {"high": 0, "medium": 1, "low": 2}
    all_alerts.sort(key=lambda a: (order.get(a.get("severity", "low"), 2), a.get("symbol", "")))

    return {
        "alerts": all_alerts,
        "count": len(all_alerts),
        "generated_at": datetime.utcnow().isoformat(),
    }


@app.get("/api/alerts")
async def get_alerts(symbols: str = "", shares: str = ""):
    if not symbols:
        return JSONResponse({"alerts": [], "count": 0,
                             "generated_at": datetime.utcnow().isoformat()})

    sym_list  = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    cache_key = symbols + "|" + shares
    now       = time.time()

    if (_alerts_cache["data"] is not None
            and now - _alerts_cache["ts"] < ALERTS_TTL
            and _alerts_cache["key"] == cache_key):
        return JSONResponse(_alerts_cache["data"])

    shares_map: dict = {}
    for part in (shares.split(",") if shares else []):
        if ":" in part:
            sym, cnt = part.split(":", 1)
            try:
                shares_map[sym.strip().upper()] = float(cnt.strip())
            except ValueError:
                pass

    result = await _compute_alerts(sym_list, shares_map)
    _alerts_cache.update({"data": result, "ts": now, "key": cache_key})
    return JSONResponse(result)


@app.get("/api/insider")
async def get_insider(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    finnhub_key = os.getenv("FINNHUB_API_KEY", "")
    six_months_ago = (datetime.utcnow() - timedelta(days=180)).strftime("%Y-%m-%d")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    def fetch_finnhub(sym):
        import urllib.request, json as _json
        url = (f"https://finnhub.io/api/v1/stock/insider-transactions"
               f"?symbol={sym}&from={six_months_ago}&to={today}&token={finnhub_key}")
        with urllib.request.urlopen(url, timeout=8) as r:
            data = _json.loads(r.read())
        buys = [
            {"name": tx.get("name", ""),
             "date": tx.get("transactionDate", ""),
             "shares": tx.get("change", 0),
             "price": tx.get("transactionPrice", 0),
             "value": round((tx.get("transactionPrice") or 0) * abs(tx.get("change") or 0))}
            for tx in data.get("data", [])
            if tx.get("transactionCode") == "P" and (tx.get("change") or 0) > 0
        ]
        return {"hasBuys": len(buys) > 0, "buyCount": len(buys), "buys": buys[:3], "source": "finnhub"}

    def fetch_yfinance_fallback(sym):
        t = yf.Ticker(sym)
        txns = t.insider_transactions
        if txns is None or (hasattr(txns, "empty") and txns.empty):
            return {"hasBuys": False, "buyCount": 0, "buys": [], "source": "yfinance"}
        cutoff = datetime.utcnow() - timedelta(days=180)
        buys = []
        for col in txns.columns:
            pass  # just iterate rows below
        for _, row in txns.iterrows():
            # try every plausible date/transaction column name
            date_val = None
            for dc in ("Date", "Start Date", "startDate", "date"):
                if dc in row.index and row[dc] is not None:
                    date_val = row[dc]; break
            trans_val = ""
            for tc in ("Transaction", "transaction", "transactionType"):
                if tc in row.index and row[tc] is not None:
                    trans_val = str(row[tc]).lower(); break
            if date_val is None:
                continue
            if hasattr(date_val, "to_pydatetime"):
                date_val = date_val.to_pydatetime()
            date_naive = date_val.replace(tzinfo=None) if getattr(date_val, "tzinfo", None) else date_val
            if date_naive >= cutoff and ("buy" in trans_val or "purchase" in trans_val):
                name = ""
                for nc in ("Insider Trading", "insider", "name", "Name"):
                    if nc in row.index and row[nc] is not None:
                        name = str(row[nc]); break
                shares_val = 0
                for sc in ("Shares", "shares", "change"):
                    if sc in row.index and row[sc] is not None:
                        try: shares_val = int(row[sc])
                        except: pass
                        break
                buys.append({"name": name, "date": date_naive.strftime("%d/%m/%Y"),
                             "shares": shares_val, "value": 0})
        return {"hasBuys": len(buys) > 0, "buyCount": len(buys), "buys": buys[:3], "source": "yfinance"}

    def fetch_one(sym):
        try:
            if finnhub_key:
                return sym, fetch_finnhub(sym)
            return sym, fetch_yfinance_fallback(sym)
        except Exception as e:
            logging.warning(f"Insider {sym}: {e}")
            try:
                return sym, fetch_yfinance_fallback(sym)
            except Exception as e2:
                logging.warning(f"Insider yf fallback {sym}: {e2}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)
    return JSONResponse({sym: data for sym, data in results if data})


@app.get("/api/growth_trend")
async def get_growth_trend(symbols: str = ""):
    """
    Returns CAGR (3-year annual) and true TTM growth (last 4 quarters vs prior 4 quarters).
    Flags deceleration when TTM growth is <70% of historical CAGR.
    """
    if not symbols:
        return JSONResponse({})
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def _find_revenue(income):
        # 1. exact match — "Total Revenue" is yfinance's standard revenue row
        for idx in income.index:
            if str(idx).strip() == "Total Revenue":
                return income.loc[idx]
        # 2. "Operating Revenue" fallback (used by some industries)
        for idx in income.index:
            if str(idx).strip() == "Operating Revenue":
                return income.loc[idx]
        # 3. Any "Revenue" row that is NOT "Cost Of Revenue" or similar expense row
        for idx in income.index:
            s = str(idx)
            if "Revenue" in s and "Cost" not in s and "Expense" not in s:
                return income.loc[idx]
        return None

    def fetch_one(sym):
        try:
            t = yf.Ticker(sym)

            # ── True TTM: sum of last 4 quarterly revenues vs prior 4 ──
            q_income = t.quarterly_income_stmt
            ttm_growth = None
            if q_income is not None and not q_income.empty:
                q_rev = _find_revenue(q_income)
                if q_rev is not None:
                    q_vals = q_rev.dropna().sort_index(ascending=False)
                    n = len(q_vals)
                    if n >= 8:
                        # Ideal: true TTM (last 4 quarters vs prior 4 quarters)
                        ttm      = sum(float(q_vals.iloc[i]) for i in range(4))
                        prev_ttm = sum(float(q_vals.iloc[i]) for i in range(4, 8))
                        if prev_ttm != 0:
                            ttm_growth = (ttm - prev_ttm) / abs(prev_ttm) * 100
                    elif n >= 5:
                        # Partial: compare N-quarter window to same window a year ago
                        k = min(4, n - 4)
                        recent = sum(float(q_vals.iloc[i]) for i in range(k))
                        prior  = sum(float(q_vals.iloc[i]) for i in range(4, 4 + k))
                        if prior != 0:
                            ttm_growth = (recent - prior) / abs(prior) * 100
                    elif n >= 4:
                        # Fallback: YoY same-quarter (Q_latest vs Q_latest-last-year)
                        latest = float(q_vals.iloc[0])
                        yoy    = float(q_vals.iloc[3])
                        if yoy != 0:
                            ttm_growth = (latest - yoy) / abs(yoy) * 100

            # ── CAGR: 3-year from annual income statement ──
            a_income = t.income_stmt
            cagr = None
            if a_income is not None and not a_income.empty:
                a_rev = _find_revenue(a_income)
                if a_rev is not None:
                    a_vals = a_rev.dropna().sort_index(ascending=False)
                    a_list = [float(a_vals.iloc[i]) for i in range(min(4, len(a_vals)))]
                    if len(a_list) >= 4 and a_list[3] > 0:
                        cagr = ((a_list[0] / a_list[3]) ** (1.0 / 3) - 1) * 100
                    elif len(a_list) >= 3 and a_list[2] > 0:
                        cagr = ((a_list[0] / a_list[2]) ** (1.0 / 2) - 1) * 100
                    elif len(a_list) >= 2 and a_list[1] > 0:
                        cagr = ((a_list[0] / a_list[1]) - 1) * 100

            # Last-resort fallback: if quarterly failed, derive TTM from annual YoY
            if ttm_growth is None and a_income is not None and not a_income.empty:
                a_rev = _find_revenue(a_income)
                if a_rev is not None:
                    a_vals = a_rev.dropna().sort_index(ascending=False)
                    if len(a_vals) >= 2 and float(a_vals.iloc[1]) != 0:
                        ttm_growth = (float(a_vals.iloc[0]) - float(a_vals.iloc[1])) / abs(float(a_vals.iloc[1])) * 100

            if ttm_growth is None or cagr is None:
                return sym, None

            decelerating = False
            decel_pct = None
            if cagr > 3:
                decel_pct = cagr - ttm_growth
                decelerating = ttm_growth < cagr * 0.70  # TTM < 70% of CAGR

            return sym, {
                "cagr": round(cagr, 1),
                "ttmGrowth": round(ttm_growth, 1),
                "decelerating": decelerating,
                "decelPct": round(decel_pct, 1) if decel_pct is not None else None,
            }
        except Exception as e:
            logging.warning(f"GrowthTrend {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)
    return JSONResponse({sym: data for sym, data in results if data})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
