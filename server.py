import os
from flask import Flask, send_file, jsonify, request
import yfinance as yf

app = Flask(__name__)

@app.route('/api/prices')
def prices():
    symbols = [s.strip() for s in request.args.get('symbols', '').split(',') if s.strip()]
    result_prices, result_prev = {}, {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).fast_info
            if info.last_price:    result_prices[sym] = round(info.last_price, 2)
            if info.previous_close: result_prev[sym]  = round(info.previous_close, 2)
        except:
            pass
    return jsonify({'prices': result_prices, 'prevClose': result_prev})


@app.route('/api/usdils')
def usdils():
    try:
        info = yf.Ticker('USDILS=X').fast_info
        rate = info.last_price
        prev = info.previous_close
        if rate:
            return jsonify({'rate': round(rate, 4), 'prev': round(prev, 4) if prev else None})
    except:
        pass
    return jsonify({'rate': None, 'prev': None})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    return send_file('app.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)))
