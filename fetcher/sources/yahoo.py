import yfinance as yf
from datetime import datetime, timedelta, timezone


TICKERS = {
    "DXY": "DX-Y.NYB",
    "SP500": "^GSPC",
    "GOLD": "GC=F",
    "BTC": "BTC-USD",
}

TIMEFRAMES = {
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "3Y": 365 * 3,
    "5Y": 365 * 5,
}


def _calculate_return(current, past):
    """Calculate percentage return."""
    if past is None or past == 0:
        return None
    return round(((current - past) / past) * 100, 2)


def _get_price_at_offset(hist, days_ago):
    """Get the close price approximately days_ago days in the past."""
    if hist.empty:
        return None
    target_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    # Make target_date timezone-naive if index is naive, or vice versa
    try:
        if hist.index.tz is not None:
            # Index is tz-aware, use tz-aware target
            mask = hist.index <= target_date
        else:
            # Index is tz-naive, use naive target
            mask = hist.index <= target_date.replace(tzinfo=None)
    except TypeError:
        # Fallback: convert index to naive
        naive_index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        mask = naive_index <= target_date.replace(tzinfo=None)
        hist = hist.copy()
        hist.index = naive_index
    if mask.any():
        return float(hist.loc[mask, "Close"].iloc[-1])
    return None


def fetch():
    """Fetch asset prices and compute returns using yfinance."""
    try:
        current_prices = {}
        histories = {}

        for name, ticker_symbol in TICKERS.items():
            try:
                ticker = yf.Ticker(ticker_symbol)
                hist = ticker.history(period="5y")
                if hist.empty:
                    print(f"[yahoo] No data for {name} ({ticker_symbol})")
                    continue
                histories[name] = hist
                current_prices[name] = float(hist["Close"].iloc[-1])
            except Exception as e:
                print(f"[yahoo] Error fetching {name}: {e}")
                continue

        # Build current values dict (excluding BTC since it comes from CoinGecko)
        current = {}
        for name in ["DXY", "SP500", "GOLD"]:
            if name in current_prices:
                current[name] = round(current_prices[name], 2)

        # Compute returns for all assets across timeframes
        asset_returns = {}
        for tf_name, tf_days in TIMEFRAMES.items():
            tf_returns = {}
            for name in ["BTC", "GOLD", "SP500"]:
                if name in histories and name in current_prices:
                    past_price = _get_price_at_offset(histories[name], tf_days)
                    ret = _calculate_return(current_prices[name], past_price)
                    if ret is not None:
                        tf_returns[name] = ret
            asset_returns[tf_name] = tf_returns

        return {
            "current": current,
            "asset_returns": asset_returns,
        }

    except Exception as e:
        print(f"[yahoo] Error: {e}")
        return None
