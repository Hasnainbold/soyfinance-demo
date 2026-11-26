import requests
import pandas as pd
from datetime import datetime

# ============================================================
# BRAZIL SOYBEAN EXPORT PRICE DATA
# Source: Brazil Comex Stat / MDIC
# Product: Soybeans
# NCM: 1201
# ============================================================

API_URL = "https://api.comexstat.mdic.gov.br/general"

START_YEAR = 2020
END_YEAR = 2026


def fetch_brazil_soybean_exports():

    print("=" * 70)
    print("BRAZIL SOYBEAN EXPORT DATA - COMEX STAT")
    print("=" * 70)

    # --------------------------------------------------------
    # NCM 1201 = Soybeans
    # --------------------------------------------------------

    payload = {
        "flow": "export",
        "monthStart": 1,
        "monthEnd": 12,
        "yearStart": START_YEAR,
        "yearEnd": END_YEAR,

        "filters": [
            {
                "filter": "ncm",
                "values": ["1201"]
            }
        ],

        "details": [
            "year",
            "month",
            "ncm"
        ],

        "metrics": [
            "metricFOB",
            "metricKG"
        ]
    }

    print("\nRequesting data from Comex Stat...")
    print(f"Period: {START_YEAR} - {END_YEAR}")
    print("Product: Soybeans (NCM 1201)")

    try:
        response = requests.post(
            API_URL,
            json=payload,
            timeout=120
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as e:

        print("\nERROR connecting to Comex Stat:")
        print(e)
        return None

    # --------------------------------------------------------
    # Convert response to JSON
    # --------------------------------------------------------

    try:
        result = response.json()

    except ValueError:

        print("\nERROR: API did not return valid JSON.")
        print(response.text[:1000])
        return None

    # --------------------------------------------------------
    # Inspect response
    # --------------------------------------------------------

    print("\nAPI response received successfully.")

    # Different versions of the API may return the data
    # under different keys, so inspect the response.

    if "data" in result:
        data = result["data"]

    elif "data" in result.get("result", {}):
        data = result["result"]["data"]

    else:
        print("\nCould not find data in API response.")
        print(result)
        return None

    if not data:

        print("\nNo soybean export data returned.")
        return None

    # --------------------------------------------------------
    # Convert to DataFrame
    # --------------------------------------------------------

    df = pd.DataFrame(data)

    print("\nRaw columns returned by API:")
    print(df.columns.tolist())

    print("\nNumber of records:", len(df))

    # --------------------------------------------------------
    # Rename columns
    # --------------------------------------------------------

    rename_map = {
        "CO_ANO": "Year",
        "CO_MES": "Month",
        "VL_FOB": "Export_Value_USD",
        "KG_LIQUIDO": "Export_Quantity_KG",
        "CO_NCM": "NCM"
    }

    df.rename(columns=rename_map, inplace=True)

    # --------------------------------------------------------
    # Calculate Metric Tons
    # --------------------------------------------------------

    if "Export_Quantity_KG" in df.columns:

        df["Export_Quantity_MT"] = (
            df["Export_Quantity_KG"] / 1000
        )

    # --------------------------------------------------------
    # Calculate actual export price
    #
    # USD / Metric Ton
    # --------------------------------------------------------

    if (
        "Export_Value_USD" in df.columns
        and "Export_Quantity_MT" in df.columns
    ):

        df["Brazil_Soybean_Export_Price_USD_MT"] = (
            df["Export_Value_USD"]
            / df["Export_Quantity_MT"]
        )

    # --------------------------------------------------------
    # Create date column
    # --------------------------------------------------------

    if "Year" in df.columns and "Month" in df.columns:

        df["Date"] = pd.to_datetime(
            dict(
                year=df["Year"],
                month=df["Month"],
                day=1
            ),
            errors="coerce"
        )

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    if "Date" in df.columns:

        df = df.sort_values("Date")

    # --------------------------------------------------------
    # Select final columns
    # --------------------------------------------------------

    final_columns = [
        "Date",
        "Year",
        "Month",
        "NCM",
        "Export_Value_USD",
        "Export_Quantity_KG",
        "Export_Quantity_MT",
        "Brazil_Soybean_Export_Price_USD_MT"
    ]

    final_columns = [
        col for col in final_columns
        if col in df.columns
    ]

    df = df[final_columns]

    # --------------------------------------------------------
    # Save CSV
    # --------------------------------------------------------

    output_file = "brazil_soybean_export_prices_2021_2026.csv"

    df.to_csv(
        output_file,
        index=False
    )

    print("\n" + "=" * 70)
    print("SUCCESS")
    print("=" * 70)

    print(f"\nCSV saved as:")
    print(output_file)

    print("\nFirst 10 records:")
    print(df.head(10).to_string(index=False))

    print("\nLatest 10 records:")
    print(df.tail(10).to_string(index=False))

    return df


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    df = fetch_brazil_soybean_exports()