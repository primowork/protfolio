from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn, logging, os
import yfinance as yf

logging.basicConfig(level=logging.INFO)
app = FastAPI()

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
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    prices, prev = {}, {}
    try:
        tickers = yf.Tickers(" ".join(sym_list))
        for sym in sym_list:
            try:
                info = tickers.tickers[sym].fast_info
                if info.last_price: prices[sym] = info.last_price
                if info.previous_close: prev[sym] = info.previous_close
            except Exception as e:
                logging.warning(f"{sym}: {e}")
    except Exception as e:
        logging.error(e)
    logging.info(f"Got {len(prices)} prices, {len(prev)} prevClose")
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
async def get_revenue(symbols: str = ""):
    if not symbols:
        return JSONResponse({})
    import asyncio, concurrent.futures
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    def fetch_one(sym):
        try:
            t = yf.Ticker(sym)
            income = t.income_stmt
            if income is None or income.empty:
                return sym, None
            # find revenue row
            rev_row = None
            for idx in income.index:
                if 'Revenue' in str(idx):
                    rev_row = income.loc[idx]
                    break
            if rev_row is None or rev_row.dropna().empty:
                return sym, None
            rev_row = rev_row.dropna().sort_index(ascending=False)
            if len(rev_row) < 2:
                return sym, None
            latest, prev = float(rev_row.iloc[0]), float(rev_row.iloc[1])
            if prev == 0:
                return sym, None
            pct = round((latest - prev) / abs(prev) * 100, 1)
            return sym, {
                "latest": round(latest / 1e9, 2),   # billions
                "prev":   round(prev   / 1e9, 2),
                "pct":    pct,
                "rising": pct > 0
            }
        except Exception as e:
            logging.warning(f"Revenue {sym}: {e}")
        return sym, None

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = [loop.run_in_executor(pool, fetch_one, s) for s in sym_list]
        results = await asyncio.gather(*tasks)

    return JSONResponse({sym: data for sym, data in results if data})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
