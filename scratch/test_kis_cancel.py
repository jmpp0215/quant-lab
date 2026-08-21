import os
os.environ["KIS_DRY_RUN"] = "false"
from quant.kis_client import KisClient
import logging
logging.basicConfig(level=logging.INFO)
client = KisClient("isa")

try:
    resp = client.post(
        "/uapi/domestic-stock/v1/trading/order-rvsecncl",
        tr_id="TTTC0803U",
        body={
            "CANO": client.cano,
            "ACNT_PRDT_CD": client.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "91252",
            "ORGN_ODNO": "0024989600",
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02", # cancel
            "ORD_QTY": "0",
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y"
        }
    )
    import pprint
    pprint.pprint(resp)
except Exception as e:
    print(f"Error: {e}")
