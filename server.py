cat > server.py << 'EOF'
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import httpx, uvicorn, logging, os

logging.basicConfig(level=logging.INFO)
app = FastAPI()

with open("app.html", "r", encoding="utf-8") as f:
    HTML_CONTENT = f.read()

@app.get("/")
async def root():
    return HTMLResponse(HTML_CONTENT)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/debug")
async def debug():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            r = await client.get(
                "https://query1.finance.yahoo.com/v8/finance/quote?symbols=AAPL&fields=regularMarketPrice,regularMarketPreviousClose,symbol",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8.0
            )
            return {"status": r.status_code, "body": r.text[:800]}
        except Exception as e:
            return {"error": str(e)}

@app.get("/api/prices")
async def get_prices(symbols: str = ""):
    if not symbols:
        return {"prices": {}, "prevClose": {}}
    fields = "regularMarketPrice,regularMarketPreviousClose,symbol"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for url in [
            f"https://query1.finance.yahoo.com/v8/finance/quote?symbols={symbols}&fields={fields}",
            f"https://query2.finance.yahoo.com/v8/finance/quote?symbols={symbols}&fields={fields}",
        ]:
            try:
                r = await client.get(url, headers=headers, timeout=8.0)
                if r.status_code == 200:
                    results = r.json().get("quoteResponse", {}).get("result", [])
                    prices, prev = {}, {}
                    for item in results:
                        s = item.get("symbol")
                        if s:
                            if item.get("regularMarketPrice"): prices[s] = item["regularMarketPrice"]
                            if item.get("regularMarketPreviousClose"): prev[s] = item["regularMarketPreviousClose"]
                    if prices:
                        return {"prices": prices, "prevClose": prev}
            except Exception as e:
                logging.warning(e)
    return {"prices": {}, "prevClose": {}}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
EOF
