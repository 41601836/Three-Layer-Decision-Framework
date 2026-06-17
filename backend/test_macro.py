import os
import sys
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from app.core.akshare_client import fetch_overseas_macro, fetch_domestic_macro
from app.dao.macro_dao import upsert_macro, get_macro, get_latest_macro_date

def run_tests():
    print("Testing fetch_overseas_macro...")
    overseas = fetch_overseas_macro()
    print("Overseas Data:", overseas)
    
    print("\nTesting fetch_domestic_macro...")
    domestic = fetch_domestic_macro()
    print("Domestic Data:", domestic)

    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\nTesting upsert_macro into macro_cache for date: {today}...")

    if overseas.get('spx') is not None:
        upsert_macro('overseas', 'SPX', str(overseas['spx']), today)
        print(f"Upserted SPX: {overseas['spx']}")
        
    if domestic.get('pmi') is not None:
        upsert_macro('domestic', 'PMI', str(domestic['pmi']), today)
        print(f"Upserted PMI: {domestic['pmi']}")
        
    print("\nTesting get_macro...")
    val_spx = get_macro('SPX', today)
    print(f"Retrieved SPX: {val_spx}")
    
    latest_date = get_latest_macro_date('SPX')
    print(f"Latest SPX Date: {latest_date}")
    
    print("\nTests completed successfully.")

if __name__ == "__main__":
    run_tests()
