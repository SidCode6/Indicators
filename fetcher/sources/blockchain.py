import requests


def fetch():
    """Fetch current Bitcoin block height from blockchain.info."""
    try:
        resp = requests.get("https://blockchain.info/q/getblockcount", timeout=10)
        resp.raise_for_status()
        block_height = int(resp.text.strip())
        return {"block_height": block_height}
    except Exception as e:
        print(f"[blockchain] Error: {e}")
        return None
