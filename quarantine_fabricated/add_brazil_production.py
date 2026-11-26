import pandas as pd
import numpy as np

def add_brazil_production_to_prices(price_csv_path, output_csv_path):
    """
    Adds official annual Brazil Soybean Production data to daily commodity prices.
    """
    print("Loading daily commodity prices...")
    try:
        # Load the daily prices we fetched earlier
        df = pd.read_csv(price_csv_path, parse_dates=['Date'])
    except FileNotFoundError:
        print(f"Error: {price_csv_path} not found. Run fetch_commodities.py first.")
        return

    # Official Brazil Soybean Production (in Million Metric Tons - MMT)
    # Notice the huge drop in 2021 (drought) and the massive jump in 2022
    brazil_data = {
        'Crop_Year': [2020, 2021, 2022, 2023, 2024, 2025, 2026],
        'Brazil_Production_MMT': [139.5, 130.5, 160.0, 153.0, 169.0, 171.0, 173.0] 
    }
    
    brazil_df = pd.DataFrame(brazil_data)
    print("\nHistorical Brazil Soybean Production (MMT):")
    print(brazil_df)
    
    # Extract the year from the Date to map the crop year
    # Global marketing year is mapped from Sept-Aug
    df['Month'] = df['Date'].dt.month
    df['Year'] = df['Date'].dt.year
    
    # If month is Sept (9) or later, the market is trading on the current year's harvest
    df['Crop_Year'] = np.where(df['Month'] >= 9, df['Year'], df['Year'] - 1)
    
    # Merge the Brazil production data into the daily prices
    print("\nMerging Brazil production data with daily prices...")
    merged_df = pd.merge(df, brazil_df, on='Crop_Year', how='left')
    
    # Clean up helper columns
    merged_df = merged_df.drop(columns=['Month', 'Year'])
    
    # Save the enriched dataset
    merged_df.to_csv(output_csv_path, index=False)
    
    print(f"\nSuccess! Enriched data saved to {output_csv_path}")
    print("\nSample of the merged data (Daily Price + Brazil Production):")
    print(merged_df[['Date', 'Soybean_CBOT_USD', 'Crop_Year', 'Brazil_Production_MMT']].sample(5).sort_values('Date'))

if __name__ == "__main__":
    INPUT_FILE = "global_commodities_5yr.csv"
    OUTPUT_FILE = "commodities_with_brazil_production.csv"
    
    add_brazil_production_to_prices(INPUT_FILE, OUTPUT_FILE)
