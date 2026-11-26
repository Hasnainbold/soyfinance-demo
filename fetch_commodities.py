import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def fetch_global_commodities(years=5):
    """
    Fetches daily closing prices for global commodity benchmarks over the last X years.
    Returns a combined DataFrame.
    """
    # Global Benchmarks (US CBOT) - Dictates global pricing including China & India import parity
    tickers = {
        'Soybean_CBOT_USD': 'ZS=F',       # Soybean Futures
        'Soy_Meal_DOC_CBOT_USD': 'ZM=F',  # Soybean Meal (DOC) Futures
        'Soy_Oil_CBOT_USD': 'ZL=F',       # Soybean Oil Futures
        'Corn_CBOT_USD': 'ZC=F'           # Corn Futures
    }
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    
    print(f"Fetching data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...\n")
    
    combined_df = pd.DataFrame()
    
    for name, ticker in tickers.items():
        try:
            print(f"Downloading {name} ({ticker})...")
            # Fetch data from Yahoo Finance
            data = yf.download(ticker, start=start_date, end=end_date, progress=False)
            
            if not data.empty:
                # Keep only the 'Close' price and rename the column
                # Handling yfinance multi-index columns if present
                if isinstance(data.columns, pd.MultiIndex):
                    close_series = data['Close'][ticker]
                else:
                    close_series = data['Close']
                    
                combined_df[name] = close_series
            else:
                print(f"No data found for {ticker}")
                
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            
    # Drop rows where all data is missing (weekends/holidays)
    combined_df = combined_df.dropna(how='all')
    
    # Forward fill missing data for days where one market was closed but others were open
    combined_df = combined_df.ffill()
    
    return combined_df

if __name__ == "__main__":
    print("--- AgriCred Commodity Price Fetcher ---")
    
    # Fetch 5 years of data
    df = fetch_global_commodities(years=5)
    
    print("\nSuccessfully fetched data! Shape:", df.shape)
    print("\nFirst 5 rows (Oldest):")
    print(df.head())
    
    print("\nLast 5 rows (Most Recent):")
    print(df.tail())
    
    # Save to CSV for the ML model
    filename = "global_commodities_5yr.csv"
    df.to_csv(filename)
    print(f"\nSaved full dataset to {filename}")
    
    # Calculate crush margin feature
    print("\nCalculating Crush Margin Feature (1 Bushel Bean -> 11 lbs Oil + 44 lbs Meal)...")
    # CBOT standard approximation:
    # Oil is in cents/lb, Meal is in USD/short ton, Beans in cents/bushel
    # Crush Margin = (Oil * 11) + (Meal * 0.022) * 100 - Bean
    df['Crush_Margin_USD'] = (df['Soy_Oil_CBOT_USD'] * 11) + (df['Soy_Meal_DOC_CBOT_USD'] * 0.022 * 100) - df['Soybean_CBOT_USD']
    
    print("Latest Crush Margins:")
    print(df[['Soybean_CBOT_USD', 'Soy_Oil_CBOT_USD', 'Soy_Meal_DOC_CBOT_USD', 'Crush_Margin_USD']].tail())
