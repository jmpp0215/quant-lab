from quant.kis_client import KisClient
client = KisClient("isa")
try:
    resp = client.create_order(symbol="102110", side="BUY", order_type="LIMIT", quantity=1, price="80000")
    print(resp)
except Exception as e:
    print(f"Error: {e}")
