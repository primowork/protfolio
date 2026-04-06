from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
import uvicorn
import logging
import os

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Primo Portfolio")

with open("app.html", "r", encoding="utf-8") as f:
    HTML_CONTENT = f.read()

@app.get("/")
async def root():
    return HTMLResponse(HTML_CONTENT)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/prices")
async def get_prices(symbols: str = ""):
    """Proxy to Yahoo Finance to avoid CORS"""
    if not symbols:
        return JSONResponse({"prices": {}, "prevClose": {}})
    
    fields = "regularMarketPrice,regularMarketPreviousClose,symbol"
    url = f"https://query1.finance.yahoo.com/v8/finance/quote?symbols={symbols}&fields={fields}"
    
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                results = data.get("quoteResponse", {}).get("result", [])
                prices = {}
                prev_close = {}
                for item in results:
                    sym = item.get("symbol")
                    if sym:
                        if item.get("regularMarketPrice"):
                            prices[sym] = item["regularMarketPrice"]
                        if item.get("regularMarketPreviousClose"):
                            prev_close[sym] = item["regularMarketPreviousClose"]
                return JSONResponse({"prices": prices, "prevClose": prev_close})
    except Exception as e:
        logging.error(f"Price fetch error: {e}")
    
    return JSONResponse({"prices": {}, "prevClose": {}})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
