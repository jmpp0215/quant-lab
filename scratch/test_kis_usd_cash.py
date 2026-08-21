from quant.kis_client import KisClient
client = KisClient("main")
try:
    resp = client.holdings_overseas()
    print("output2:", resp.get("output2", {}))
    print("output3:", resp.get("output3", {}))
except Exception as e:
    print(f"Error: {e}")
