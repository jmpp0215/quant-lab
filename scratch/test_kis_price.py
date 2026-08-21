from quant.kis_client import KisClient
client = KisClient("isa")
try:
    resp = client.price("102110")
    print(resp)
except Exception as e:
    print(f"Error: {e}")
