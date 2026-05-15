"""
Macro economic data from FREE public APIs (no API key needed).

Sources:
- Fed Funds Rate: NY Fed API
- Treasury Yields (2Y, 10Y, 30Y, 3-month): yfinance ^TNX/^TYX/^IRX + FRED CSV for 2Y
- CPI / Inflation: BLS API v1
- National Debt: Treasury Fiscal Data API
- Debt-to-GDP: World Bank + Treasury
- Federal Deficit: Treasury Fiscal Data (MTS)
"""

import requests
import yfinance as yf


def _fetch_yahoo_yield(symbol, label):
    """Generic Yahoo yield fetcher — used for ^TNX (10Y), ^TYX (30Y), ^IRX (3M T-bill)."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d")
        if hist.empty:
            return None
        current = float(hist["Close"].iloc[-1])
        previous = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        change = round(current - previous, 4) if previous is not None else None
        return {
            "value": round(current, 2),
            "change": change,
            "date": str(hist.index[-1].date()),
        }
    except Exception as e:
        print(f"[macro] {label} ({symbol}) error: {e}")
        return None


def _fetch_fred_csv(series_id):
    """Pull a FRED series via the public CSV graph endpoint (no key needed).

    Returns latest observation + change vs prior observation.
    """
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Indicators-Dashboard/1.0"},
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        if len(lines) < 2:
            return None
        rows = []
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) != 2:
                continue
            date, value = parts[0].strip(), parts[1].strip()
            if value and value != ".":
                try:
                    rows.append((date, float(value)))
                except ValueError:
                    continue
        if not rows:
            return None
        date, current = rows[-1]
        previous = rows[-2][1] if len(rows) > 1 else None
        change = round(current - previous, 4) if previous is not None else None
        return {
            "value": round(current, 2),
            "change": change,
            "date": date,
        }
    except Exception as e:
        print(f"[fred-csv] {series_id} error: {e}")
        return None


def _fetch_fed_funds_rate():
    """Fetch effective federal funds rate from NY Fed API."""
    try:
        url = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rates = data.get("refRates", [])
        if rates:
            rate = rates[0]
            return {
                "value": float(rate.get("percentRate", 0)),
                "target_from": float(rate.get("targetRateFrom", 0)),
                "target_to": float(rate.get("targetRateTo", 0)),
                "change": None,
                "date": rate.get("effectiveDate", ""),
            }
    except Exception as e:
        print(f"[macro] Fed Funds Rate error: {e}")
    return None


def _fetch_treasury_10y():
    """10-Year Treasury Yield from Yahoo (^TNX)."""
    return _fetch_yahoo_yield("^TNX", "10Y Treasury")


def _fetch_cpi():
    """Fetch CPI data from Bureau of Labor Statistics API v1 (no key needed)."""
    try:
        url = "https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        series_data = data.get("Results", {}).get("series", [{}])[0].get("data", [])
        if len(series_data) < 13:
            return None

        # Data comes in reverse chronological order
        # Find latest and same month 12 months ago to compute YoY inflation
        latest = series_data[0]
        latest_value = float(latest["value"])
        latest_year = int(latest["year"])
        latest_month = latest["period"]  # e.g. "M02" for February

        # Find same month from previous year
        for obs in series_data:
            if int(obs["year"]) == latest_year - 1 and obs["period"] == latest_month:
                past_value = float(obs["value"])
                yoy_inflation = round(((latest_value - past_value) / past_value) * 100, 1)
                month_num = latest_month.replace("M", "")
                return {
                    "value": yoy_inflation,
                    "change": None,  # Could compute month-over-month
                    "date": f"{latest_year}-{month_num.zfill(2)}-01",
                    "cpi_index": latest_value,
                }
                break

    except Exception as e:
        print(f"[macro] CPI error: {e}")
    return None


def _fetch_national_debt():
    """Fetch US national debt from Treasury Fiscal Data API."""
    try:
        url = (
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v2/accounting/od/debt_to_penny"
            "?sort=-record_date&page%5Bsize%5D=2"
            "&fields=record_date,tot_pub_debt_out_amt"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        if records:
            latest = records[0]
            value = float(latest["tot_pub_debt_out_amt"])
            previous = float(records[1]["tot_pub_debt_out_amt"]) if len(records) > 1 else None
            change = round(value - previous, 0) if previous else None
            return {
                "value": value,
                "change": change,
                "date": latest["record_date"],
            }
    except Exception as e:
        print(f"[macro] National Debt error: {e}")
    return None


def _fetch_debt_to_gdp():
    """Fetch debt-to-GDP ratio using World Bank GDP + Treasury debt."""
    try:
        # Get latest GDP from World Bank
        gdp_url = "https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD?format=json&per_page=3"
        resp = requests.get(gdp_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        gdp_value = None
        gdp_year = None
        if len(data) > 1:
            for entry in data[1]:
                if entry.get("value") is not None:
                    gdp_value = float(entry["value"])
                    gdp_year = entry["date"]
                    break

        if gdp_value is None:
            return None

        # Get current debt (already fetched, but get it again for fresh data)
        debt_data = _fetch_national_debt()
        if debt_data and debt_data["value"]:
            ratio = round((debt_data["value"] / gdp_value) * 100, 1)
            return {
                "value": ratio,
                "change": None,
                "date": f"{gdp_year} GDP basis",
            }
    except Exception as e:
        print(f"[macro] Debt-to-GDP error: {e}")
    return None


def _fetch_federal_deficit():
    """Fetch federal deficit from Treasury Monthly Treasury Statement."""
    try:
        url = (
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
            "v1/accounting/mts/mts_table_5"
            "?filter=line_code_nbr:eq:5694"
            "&sort=-record_date&page%5Bsize%5D=2"
            "&fields=record_date,current_fytd_net_outly_amt,record_fiscal_year"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        if records:
            latest = records[0]
            value = float(latest["current_fytd_net_outly_amt"])
            return {
                "value": value,
                "change": None,
                "date": latest["record_date"],
                "fiscal_year": latest.get("record_fiscal_year", ""),
            }
    except Exception as e:
        print(f"[macro] Federal Deficit error: {e}")
    return None


def fetch():
    """Fetch all macro economic data from free public APIs."""
    try:
        results = {}

        # Fed Funds Rate
        ffr = _fetch_fed_funds_rate()
        if ffr:
            results["FEDFUNDS"] = ffr
            print(f"  [macro] Fed Funds Rate: {ffr['value']}%")

        # 2Y Treasury (FRED CSV — Yahoo doesn't expose a 2Y ticker)
        t2y = _fetch_fred_csv("DGS2")
        if t2y:
            results["DGS2"] = t2y
            print(f"  [macro] 2Y Treasury: {t2y['value']}%")

        # 10Y Treasury (Yahoo ^TNX)
        t10y = _fetch_treasury_10y()
        if t10y:
            results["DGS10"] = t10y
            print(f"  [macro] 10Y Treasury: {t10y['value']}%")

        # 30Y Treasury (Yahoo ^TYX)
        t30y = _fetch_yahoo_yield("^TYX", "30Y Treasury")
        if t30y:
            results["DGS30"] = t30y
            print(f"  [macro] 30Y Treasury: {t30y['value']}%")

        # 3-Month T-Bill (Yahoo ^IRX, technically 13-week)
        t3m = _fetch_yahoo_yield("^IRX", "3M T-Bill")
        if t3m:
            results["DTB3"] = t3m
            print(f"  [macro] 3M T-Bill: {t3m['value']}%")

        # CPI / Inflation
        cpi = _fetch_cpi()
        if cpi:
            results["CPIAUCSL"] = cpi
            print(f"  [macro] CPI YoY: {cpi['value']}%")

        # National Debt
        debt = _fetch_national_debt()
        if debt:
            results["GFDEBTN"] = {
                "value": debt["value"] / 1e6,  # Convert to millions for compatibility
                "change": debt["change"] / 1e6 if debt["change"] else None,
                "date": debt["date"],
            }
            print(f"  [macro] National Debt: ${debt['value']/1e12:.2f}T")

        # Debt-to-GDP
        d2g = _fetch_debt_to_gdp()
        if d2g:
            results["GFDEGDQ188S"] = d2g
            print(f"  [macro] Debt-to-GDP: {d2g['value']}%")

        # Federal Deficit
        deficit = _fetch_federal_deficit()
        if deficit:
            results["FYFSD"] = {
                "value": deficit["value"] / 1e6,  # Convert to millions
                "change": None,
                "date": deficit["date"],
            }
            print(f"  [macro] FYTD Deficit: ${deficit['value']/1e9:.1f}B")

        return results if results else None

    except Exception as e:
        print(f"[macro] Error: {e}")
        return None
