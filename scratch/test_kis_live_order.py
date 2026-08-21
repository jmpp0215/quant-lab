import logging
from quant.kis_client import KisClient
logging.basicConfig(level=logging.INFO)
client = KisClient("isa")
print(f"dry_run: {client.dry_run}")
if not client.dry_run:
    try:
        resp = client.create_order(symbol="102110", side="BUY", order_type="LIMIT", quantity=1, price="80000")
        print("\n=== Live Order Response ===")
        print(resp)
    except Exception as e:
        print(f"\nError: {e}")
