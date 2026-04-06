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

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

async def fetch_yahoo(client: httpx.AsyncClient, symbols: str) -> dict:
    fields = "regularMarketPrice,regularMarketPreviousClose,symbol"
    
    # נסה שני endpoints
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/quote?symbols={symbols}&fields={fields}",
        f"https://query2.finance.yahoo.com/v8/finance/quote?symbols={symbols}&fields={fields}",
        f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}",
    ]
    
    for url in urls:
        try:
            r = await client.get(url, headers=YAHOO_HEADERS, timeout=8.0)
            logging.info(f"Yahoo [{r.status_code}] {url[:60]}")
            if r.status_code == 200:
                data = r.json()
                results = data.get("quoteResponse", {}).get("result", [])
                if results:
                    prices, prev_close = {}, {}
                    for item in results:
                        sym = item.get("symbol")
                        if sym:
                            p = item.get("regularMarketPrice")
                            pc = item.get("regularMarketPreviousClose")
                            if p: prices[sym] = p
                            if pc: prev_close[sym] = pc
                    return {"prices": prices, "prevClose": prev_close}
        except Exception as e:
            logging.warning(f"URL failed {url[:60]}: {e}")
    
    return {"prices": {}, "prevClose": {}}

@app.get("/api/prices")
async def get_prices(symbols: str = ""):
    if not symbols:
        return JSONResponse({"prices": {}, "prevClose": {}})
    
    logging.info(f"Fetching prices for: {symbols[:100]}")
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await fetch_yahoo(client, symbols)
    
    logging.info(f"Got {len(result['prices'])} prices, {len(result['prevClose'])} prevClose")
    return JSONResponse(result)

@app.get("/api/debug")
async def debug_prices():
    """בדיקה - מחזיר מה Yahoo מחזיר ל-AAPL"""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.get(
                "https://query1.finance.yahoo.com/v8/finance/quote?symbols=AAPL&fields=regularMarketPrice,regularMarketPreviousClose,symbol",
                headers=YAHOO_HEADERS,
                timeout=8.0
            )
            return JSONResponse({
                "status": r.status_code,
                "body_preview": r.text[:500]
            })
        except Exception as e:
            return JSONResponse({"error": str(e)})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
