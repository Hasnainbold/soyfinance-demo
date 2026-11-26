"""
debug_data_gov_api.py
======================
Diagnostic script — run this directly (`python debug_data_gov_api.py`) to see
the RAW HTTP response from data.gov.in instead of the swallowed None that
agri_data_sources.py returns on any failure. Paste the printed output back
so the actual cause (bad key, wrong resource id, rate limit, network) can be
identified instead of guessed at.
"""

import requests

API_KEY = "579b464db66ec23bdd0000014aa051e453434dcf7fb7c2222b5491bc"
RAINFALL_RESOURCE_ID = "6c05cd1b-ed59-40c2-bc31-e314f39c6971"
PRODUCTION_RESOURCE_ID = "35be999b-0208-4354-b557-f6ca9a5355de"

# Also test the mandi-price resource your app already uses successfully,
# as a control — if THIS also fails, the problem is network/key, not the
# two new resource ids.
MANDI_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"


def probe(name, resource_id, extra_params=None):
    url = f"https://api.data.gov.in/resource/{resource_id}"
    params = {"api-key": API_KEY, "format": "json", "limit": "5", "offset": "0"}
    if extra_params:
        params.update(extra_params)
    print(f"\n{'='*70}\n{name}  (resource={resource_id})\nURL: {url}\nParams: {params}")
    try:
        r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        print(f"Status code: {r.status_code}")
        print(f"Final URL:   {r.url}")
        try:
            data = r.json()
            print(f"total: {data.get('total')}  count: {data.get('count')}  limit: {data.get('limit')}  offset: {data.get('offset')}")
            recs = data.get("records", [])
            print(f"records returned: {len(recs)}")
            if recs:
                print("First record:")
                print(recs[0])
                print("First record keys:", list(recs[0].keys()))
            else:
                print("records list is EMPTY. Full JSON (first 3000 chars) for inspection:")
                print(r.text[:3000])
        except ValueError:
            print(f"Response was not valid JSON. Raw text (first 1500 chars):\n{r.text[:1500]}")
    except requests.exceptions.RequestException as e:
        print(f"REQUEST EXCEPTION: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"OTHER EXCEPTION: {type(e).__name__}: {e}")


if __name__ == "__main__":
    probe("CONTROL: Mandi Price (known-working resource used elsewhere in app.py)", MANDI_RESOURCE_ID,
          {"filters[commodity]": "Soyabean"})
    probe("Rainfall (no filter)", RAINFALL_RESOURCE_ID)
    probe("Rainfall (state=Maharashtra)", RAINFALL_RESOURCE_ID, {"filters[state]": "Maharashtra"})
    probe("Crop Production (no filter)", PRODUCTION_RESOURCE_ID)
    probe("Crop Production (Crop=Soyabean)", PRODUCTION_RESOURCE_ID, {"filters[Crop]": "Soyabean"})
