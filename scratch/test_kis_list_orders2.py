from quant.kis_client import KisClient
client = KisClient("isa")

try:
    resp = client.get(
        "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        tr_id="TTTC8036R",
        params={
            "CANO": client.cano,
            "ACNT_PRDT_CD": client.acnt_prdt_cd,
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "INQR_DVSN_1": "0",
            "INQR_DVSN_2": "0",
        }
    )
    import pprint
    pprint.pprint(resp)
except Exception as e:
    print(f"Error: {e}")
