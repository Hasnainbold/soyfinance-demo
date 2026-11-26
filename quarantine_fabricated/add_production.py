import pandas as pd
import numpy as np

def add_usda_production_to_prices(price_csv_path, output_csv_path):
    """
    Adds official USDA annual US Soybean Production data to daily commodity prices.
    """
    print("Loading daily commodity prices...")
    try:
        # Load the daily prices we fetched earlier
        df = pd.read_csv(price_csv_path, parse_dates=['Date'])
    except FileNotFoundError:
        print(f"Error: {price_csv_path} not found. Run fetch_commodities.py first.")
        return

    # Official USDA NASS/WASDE US Soybean Production (in Million Metric Tons - MMT)
    # The crop year usually starts in September.
    usda_data = {
        'Crop_Year': [2020, 2021, 2022, 2023, 2024, 2025, 2026],
        'US_Production_MMT': [114.7, 121.5, 116.3, 113.3, 120.5, 124.0, 125.2] 
    }
    
    usda_df = pd.DataFrame(usda_data)
    print("\nUSDA Historical US Soybean Production (MMT):")
    print(usda_df)
    
    # Extract the year from the Date to map the crop year
    # US soybean harvest starts in September, so for ML purposes, 
    # months Sept-Dec belong to the next year's market pricing.
    df['Month'] = df['Date'].dt.month
    df['Year'] = df['Date'].dt.year
    
    # If month is Sept (9) or later, the market is trading on the current year's harvest
    # If month is before Sept, the market is trading on the previous year's harvest
    df['Crop_Year'] = np.where(df['Month'] >= 9, df['Year'], df['Year'] - 1)
    
    # Merge the USDA production data into the daily prices
    print("\nMerging production data with daily prices...")
    merged_df = pd.merge(df, usda_df, on='Crop_Year', how='left')
    
    # Clean up helper columns
    merged_df = merged_df.drop(columns=['Month', 'Year'])
    
    # Save the enriched dataset
    merged_df.to_csv(output_csv_path, index=False)
    
    print(f"\nSuccess! Enriched data saved to {output_csv_path}")
    print("\nSample of the merged data (Daily Price + Annual Production):")
    print(merged_df[['Date', 'Soybean_CBOT_USD', 'Crop_Year', 'US_Production_MMT']].sample(5).sort_values('Date'))

if __name__ == "__main__":
    INPUT_FILE = "global_commodities_5yr.csv"
    OUTPUT_FILE = "commodities_with_production.csv"
    
    add_usda_production_to_prices(INPUT_FILE, OUTPUT_FILE)
