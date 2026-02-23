"""Debug script to inspect compound offer pricing."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from helpers import fetch_offers

result = fetch_offers('N110', ['288372P'])
for o in result.get('offers', []):
    print('Offer ID:', o.get('id'))
    pricing = o.get('pricing', {}) or {}
    normal_pricing = o.get('normalPricing', {}) or {}
    print(f'  Offer price: {pricing.get("price")}')
    print(f'  Offer normal: {normal_pricing.get("price")}')
    print(f'  Offer unit: {json.dumps(pricing.get("unit"), ensure_ascii=False)}')
    
    for pw in o.get('products', []):
        prod = pw.get('product', {}) or {}
        name = (prod.get('localizedName', {}) or {}).get('finnish', '?')
        msp = (prod.get('mobilescan', {}) or {}).get('pricing', {}) or {}
        normal = msp.get('normal', {}) or {}
        batch = msp.get('batch', {}) or {}
        disc = msp.get('discount', {}) or {}
        
        print(f'  Product: {name}')
        print(f'    normal.price={normal.get("price")}')
        print(f'    batch.price={batch.get("price")}, batch.amount={batch.get("amount")}')
        print(f'    discount.price={disc.get("price")}')
