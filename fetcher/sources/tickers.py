"""Live prices for user-watchlist tickers (MSTR / ASST / STRC / SATA).

These power the four ticker cards under the BTC hero on the Macro tab.
Each ticker returns current price + 24h $ and % change. A ticker that's
unknown to Yahoo (e.g. a brand-new preferred share without coverage yet)
returns None rather than raising — the UI shows "—" for that card and
keeps the rest of the dashboard healthy.
"""

import yfinance as yf


WATCH_TICKERS = ["MSTR", "ASST", "STRC", "SATA"]


def _fetch_one(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            print(f"[tickers] {symbol}: no Yahoo data")
            return None
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        change_value = (
            round(last_close - prev_close, 2) if prev_close is not None else None
        )
        change_pct = (
            round((last_close - prev_close) / prev_close * 100, 2)
            if prev_close not in (None, 0)
            else None
        )
        return {
            "value": round(last_close, 2),
            "change_value": change_value,
            "change_pct": change_pct,
        }
    except Exception as e:
        print(f"[tickers] {symbol} error: {e}")
        return None


def fetch():
    out = {}
    for sym in WATCH_TICKERS:
        out[sym] = _fetch_one(sym)
    return out
