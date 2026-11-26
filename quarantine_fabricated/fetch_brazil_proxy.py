import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def fetch_brazil_calculated_price(years=5):
    """
    Fetches the USD/BRL exchange rate and calculates the domestic Brazilian 
    soybean price using the global CBOT benchmark.
    """
    print("Loading global commodity prices...")
    try:
        df = pd.read_csv("commodities_with_global_production.csv", parse_dates=['Date'])
    except FileNotFoundError:
        print("Error: commodities_with_global_production.csv not found.")
        return
        
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years * 365)
    
    print(f"\nFetching USD/BRL Exchange Rate from {start_date.strftime('%Y-%m-%d')}...")
    
    try:
        # BRL=X is the Yahoo Finance ticker for USD to Brazilian Real
        brl_currency = yf.download("BRL=X", start=start_date, end=end_date, progress=False)
        
        if not brl_currency.empty:
            # Handle multi-index if present
            if isinstance(brl_currency.columns, pd.MultiIndex):
                brl_close = brl_currency['Close']['BRL=X']
            else:
                brl_close = brl_currency['Close']
                
            brl_df = pd.DataFrame({'Date': brl_close.index, 'USD_BRL_Rate': brl_close.values})
            
            # Merge exchange rate into our main dataset
            print("Merging currency data...")
            merged_df = pd.merge(df, brl_df, on='Date', how='left')
            
            # Forward fill weekends/holidays where currency might not have traded
            merged_df['USD_BRL_Rate'] = merged_df['USD_BRL_Rate'].ffill()
            
            # CALCULATE BRAZILIAN LOCAL PRICE
            # CBOT Soybeans are priced in Cents per Bushel. 
            # 1 Bushel = ~27.215 kg. A Brazilian bag (saca) is 60 kg.
            # Formula: (Price in Cents / 100) * (60 / 27.215) * Exchange Rate
            
            print("Calculating local Brazilian Soybean Price (BRL per 60kg bag)...")
            merged_df['Brazil_Local_Price_BRL'] = (
                (merged_df['Soybean_CBOT_USD'] / 100) * 
                (60 / 27.215) * 
                merged_df['USD_BRL_Rate']
            )
            
            output_file = "final_ml_dataset.csv"
            merged_df.to_csv(output_file, index=False)
            
            print(f"\nSuccess! Final ML dataset saved to {output_file}")
            print("\nHere is the last 5 years of Brazilian Pricing (Sample):")
            sample_cols = ['Date', 'Soybean_CBOT_USD', 'USD_BRL_Rate', 'Brazil_Local_Price_BRL']
            
            # Show a few historical years to prove it goes back 5 years
            print(merged_df[sample_cols].dropna().sample(5).sort_values('Date'))
            
            print("\nLatest 5 Days of Calculated Brazilian Pricing:")
            print(merged_df[sample_cols].tail())
            
        else:
            print("Failed to fetch currency data.")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    fetch_brazil_calculated_price(years=5)
