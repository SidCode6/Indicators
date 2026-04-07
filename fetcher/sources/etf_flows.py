import requests
from bs4 import BeautifulSoup


ETF_URL = "https://farside.co.uk/btc/"

# Column mapping for the Farside BTC ETF tracker table
# The exact columns may shift; these are the commonly tracked ETFs
ETF_NAMES = [
    "IBIT",   # BlackRock
    "FBTC",   # Fidelity
    "BITB",   # Bitwise
    "ARKB",   # ARK
    "BTCO",   # Invesco
    "EZBC",   # Franklin
    "BRRR",   # Valkyrie
    "HODL",   # VanEck
    "BTCW",   # WisdomTree
    "GBTC",   # Grayscale
    "BTC",    # Grayscale Mini
]


def _parse_flow_value(text):
    """Parse a flow value string into a float.

    Handles:
    - Regular numbers: "100.5" -> 100.5
    - Parentheses for negatives: "(100.5)" -> -100.5
    - Dashes for zero: "–" or "-" -> 0
    - Empty strings -> 0
    """
    text = text.strip()
    if not text or text in ("–", "-", "—", "\u2013", "\u2014"):
        return 0.0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    # Remove commas and whitespace
    text = text.replace(",", "").strip()

    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return 0.0


def fetch():
    """Fetch Bitcoin ETF flow data from Farside."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(ETF_URL, timeout=15, headers=headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table")
        if not table:
            print("[etf_flows] No table found on page")
            return None

        rows = table.find_all("tr")
        if len(rows) < 2:
            print("[etf_flows] Not enough rows in table")
            return None

        # Find the last non-empty data row
        data_row = None
        for row in reversed(rows):
            cells = row.find_all("td")
            if cells and len(cells) >= 3:
                # Check if first cell looks like a date
                first_cell = cells[0].get_text(strip=True)
                if first_cell and any(c.isdigit() for c in first_cell):
                    data_row = cells
                    break

        if not data_row:
            print("[etf_flows] Could not find a valid data row")
            return None

        date_str = data_row[0].get_text(strip=True)

        # Parse individual ETF flows from remaining columns
        flows = {}
        total_flow = 0.0

        for i, etf_name in enumerate(ETF_NAMES):
            col_idx = i + 1  # +1 because first column is date
            if col_idx < len(data_row):
                value = _parse_flow_value(data_row[col_idx].get_text(strip=True))
                flows[etf_name] = value
                total_flow += value

        # Check if the last column is a total
        last_cell = data_row[-1].get_text(strip=True)
        total_from_table = _parse_flow_value(last_cell)
        if total_from_table != 0:
            total_flow = total_from_table

        return {
            "date": date_str,
            "flows": flows,
            "total_daily_flow": round(total_flow, 1),
        }

    except Exception as e:
        print(f"[etf_flows] Error: {e}")
        return None
