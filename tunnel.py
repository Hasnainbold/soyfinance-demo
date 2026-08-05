from pyngrok import ngrok
import time

# Create tunnel to Streamlit (port 8501)
public_url = ngrok.connect(8501)
print("=" * 60)
print("🚀 SHARE THIS LINK WITH YOUR FRIEND:")
print(f"   {public_url.public_url}")
print("=" * 60)
print("\nKeep this window open! Press Ctrl+C to stop sharing.\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    ngrok.disconnect(public_url.public_url)
    print("\nTunnel closed.")
