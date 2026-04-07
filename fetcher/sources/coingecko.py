import requests
import time


BASE_URL = "https://api.coingecko.com"


def _get(url, params=None, retries=1):
    """GET request with timeout and retry."""
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            if attempt < retries:
                time.sleep(2)
                continue
            raise


def fetch():
    """Fetch Bitcoin price and stablecoin data from CoinGecko."""
    try:
        # Bitcoin price, 24h change, market cap
        btc_data = _get(
            f"{BASE_URL}/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            },
        )

        bitcoin = {
            "price_usd": btc_data["bitcoin"]["usd"],
            "change_24h_pct": btc_data["bitcoin"].get("usd_24h_change", 0),
            "market_cap": btc_data["bitcoin"].get("usd_market_cap", 0),
        }

        # USDT data
        usdt_data = _get(f"{BASE_URL}/api/v3/coins/tether")
        usdt = {
            "total_supply": usdt_data.get("market_data", {}).get("total_supply", 0),
            "market_cap": usdt_data.get("market_data", {}).get("market_cap", {}).get("usd", 0),
        }

        # USDC data
        usdc_data = _get(f"{BASE_URL}/api/v3/coins/usd-coin")
        usdc = {
            "total_supply": usdc_data.get("market_data", {}).get("total_supply", 0),
            "market_cap": usdc_data.get("market_data", {}).get("market_cap", {}).get("usd", 0),
        }

        return {
            "bitcoin": bitcoin,
            "stablecoins": {
                "usdt": usdt,
                "usdc": usdc,
            },
        }

    except Exception as e:
        print(f"[coingecko] Error: {e}")
        return None
