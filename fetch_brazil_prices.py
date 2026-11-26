import pandas as pd
from datetime import datetime, timedelta

def fetch_brazil_cepea_prices(years=5):
    """
    Fetches official CEPEA/ESALQ Brazilian Soybean spot prices using agrobr.
    """
    try:
        # Import agrobr (we will install it via pip before running)
        from agrobr.sync import cepea
        
        start_date = (datetime.now() - timedelta(days=years*365)).strftime('%Y-%m-%d')
        print(f"Fetching official CEPEA Brazil Soybean (Soja) prices since {start_date}...\n")
        
        # 'soja' is the Portuguese term for Soybean. 
        # CEPEA tracks the Paranagua port price, which is the global benchmark for Brazil
        df = cepea.indicador('soja', inicio=start_date)
        
        if df is not None and not df.empty:
            print("\nSuccessfully fetched CEPEA data! Shape:", df.shape)
            
            # CEPEA usually returns columns like 'data', 'valor', 'valor_usd'
            # Let's clean it up for our ML dataset
            
            # Print raw columns to see what agrobr gives us
            print("\nRaw Data Columns:")
            print(df.columns.tolist())
            
            print("\nLatest 5 Days of Brazil Pricing:")
            print(df.tail())
            
            output_file = "brazil_cepea_soybean_prices.csv"
            df.to_csv(output_file, index=False)
            print(f"\nSaved official Brazilian prices to {output_file}")
            
        else:
            print("No data returned from agrobr CEPEA API.")
            
    except Exception as e:
        print(f"Error fetching data from agrobr: {e}")

if __name__ == "__main__":
    print("--- AgriCred Brazil CEPEA Price Fetcher ---")
    fetch_brazil_cepea_prices(years=5)
