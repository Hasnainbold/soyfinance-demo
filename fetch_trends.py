from pytrends.request import TrendReq
import pandas as pd
import time

def fetch_agri_trends(keywords=["soybean price", "soy mandi", "kharif crop"], timeframe='today 12-m', geo='IN'):
    """
    Fetches Google Trends search volume for agricultural keywords in India.
    
    Args:
        keywords (list): List of keywords to search (max 5)
        timeframe (str): Timeframe (e.g., 'today 12-m' for last 12 months, 'today 5-y' for 5 years)
        geo (str): Country code (e.g., 'IN' for India)
    """
    try:
        # Initialize pytrends
        pytrends = TrendReq(hl='en-IN', tz=330) # tz=330 is IST (UTC+5:30)
        
        print(f"Fetching Google Trends for: {keywords} in {geo}...")
        
        # Build payload
        pytrends.build_payload(keywords, cat=0, timeframe=timeframe, geo=geo)
        
        # Get interest over time
        trends_df = pytrends.interest_over_time()
        
        if not trends_df.empty:
            # Drop the 'isPartial' column if it exists
            if 'isPartial' in trends_df.columns:
                trends_df = trends_df.drop(columns=['isPartial'])
            
            print("Successfully fetched trends data!")
            return trends_df
        else:
            print("No data found for the given keywords.")
            return None
            
    except Exception as e:
        print(f"Error fetching trends: {e}")
        return None

if __name__ == "__main__":
    # Test the function for the last 90 days in India
    df = fetch_agri_trends(
        keywords=["soybean price", "soy mandi", "kharif crop", "soybean rate"], 
        timeframe='today 3-m', 
        geo='IN'
    )
    
    if df is not None:
        print(df.tail(10))
        # df.to_csv("google_trends_soybean.csv")
