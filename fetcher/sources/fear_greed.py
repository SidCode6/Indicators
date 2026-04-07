import requests


def fetch():
    """Fetch Bitcoin Fear & Greed Index from alternative.me."""
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=10)
        resp.raise_for_status()
        data = resp.json()

        entry = data.get("data", [{}])[0]
        return {
            "value": int(entry.get("value", 0)),
            "classification": entry.get("value_classification", "Unknown"),
            "timestamp": entry.get("timestamp", ""),
        }
    except Exception as e:
        print(f"[fear_greed] Error: {e}")
        return None
