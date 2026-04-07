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

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
