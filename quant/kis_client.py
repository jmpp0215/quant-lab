"""Korea Investment & Securities (KIS) Open API client."""

import logging
import os
import random
import time
from decimal import Decimal

import requests
from dotenv import load_dotenv

from quant import storage

load_dotenv()

BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_RETRIES = 3
BACKOFF_BASE = 1.0
log = logging.getLogger(__name__)


class KisApiError(Exception):
    """Wraps the error envelope returned by the KIS API (rt_cd/msg_cd/msg1)."""

    def __init__(self, status: int, rt_cd: str, msg_cd: str, message: str) -> None:
        self.status = status
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd
        self.message = message
        super().__init__(f"[{status} rt_cd={rt_cd} {msg_cd}] {message}")


class KisClient:
    def __init__(self, account: str) -> None:
        # Each account (main, isa, ...) has its own app key/secret and thus
        # its own token, so both credentials and the token cache live on
        # the instance rather than at module scope.
        self.account = account
        prefix = f"KIS_{account.upper()}"
        self.app_key = os.environ[f"{prefix}_APP_KEY"]
        self.app_secret = os.environ[f"{prefix}_APP_SECRET"]
        self.cano = os.environ[f"{prefix}_CANO"]
        self.acnt_prdt_cd = os.environ[f"{prefix}_ACNT_PRDT_CD"]
        self.dry_run = os.getenv("KIS_DRY_RUN", "true").lower() != "false"
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._session = requests.Session()

    def _get_token(self) -> str:
        # Reuse the cached token until it is close to expiry.
        if self._token and time.time() < self._expires_at:
            return self._token

        response = self._session.post(
            f"{BASE_URL}/oauth2/tokenP",
            headers={"Content-Type": "application/json; charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        response.raise_for_status()

        body = response.json()
        self._token = body["access_token"]
        # Refresh 60s early to avoid using a token that expires mid-request.
        self._expires_at = time.time() + int(body["expires_in"]) - 60
        log.info("access token issued, expires_in=%s", body["expires_in"])
        return self._token

    def _request(self, method: str, path: str, tr_id: str, *,
                 params: dict | None = None,
                 body: dict | None = None) -> dict:
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Authorization": f"Bearer {self._get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

        # Retrying a POST can duplicate an order, so only GET retries on
        # network errors. Both retry on 429, which means nothing was processed.
        retry_network = method == "GET"

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers=headers,
                    params=params,
                    json=body,
                    timeout=15,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                if not retry_network or attempt == MAX_RETRIES:
                    raise
                delay = self._backoff(attempt)
                log.warning("%s %s network error (%s), retry in %.1fs",
                            method, path, type(e).__name__, delay)
                time.sleep(delay)
                continue

            log.debug("%s %s -> %d", method, path, response.status_code)

            if response.status_code == 429 and attempt < MAX_RETRIES:
                delay = self._retry_after(response) or self._backoff(attempt)
                log.warning("%s %s rate limited, retry in %.1fs",
                            method, path, delay)
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                # KIS's own per-second throttle surfaces as a 500 with
                # rt_cd=1 EGW00201, not a 429 - same "nothing was
                # processed" case as 429, so it gets the same backoff/retry.
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = {}
                if error_body.get("msg_cd") == "EGW00201" and attempt < MAX_RETRIES:
                    delay = self._backoff(attempt)
                    log.warning("%s %s rate limited (EGW00201), retry in %.1fs",
                                method, path, delay)
                    time.sleep(delay)
                    continue
                self._raise_for_error(method, path, response)

            payload = response.json()
            # KIS returns HTTP 200 even for business-logic failures; rt_cd
            # in the body is the real success/failure signal ("0" = ok).
            if payload.get("rt_cd") not in (None, "0"):
                self._raise_for_error(method, path, response)

            return payload

        raise RuntimeError("unreachable: retry loop exited without returning")

    @staticmethod
    def _raise_for_error(method: str, path: str,
                          response: requests.Response) -> None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        log.error(
            "%s %s failed: %d rt_cd=%s %s %s",
            method, path, response.status_code,
            payload.get("rt_cd"), payload.get("msg_cd"), payload.get("msg1"),
        )
        raise KisApiError(
            status=response.status_code,
            rt_cd=payload.get("rt_cd", "unknown"),
            msg_cd=payload.get("msg_cd", "unknown"),
            message=payload.get("msg1", response.text[:200]),
        )

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter to avoid synchronised retries."""
        return BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.5)

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        """Honour the server's Retry-After header when present."""
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def get(self, path: str, tr_id: str, params: dict | None = None) -> dict:
        return self._request("GET", path, tr_id, params=params)

    def post(self, path: str, tr_id: str, body: dict) -> dict:
        return self._request("POST", path, tr_id, body=body)

    def create_order(self, symbol: str, side: str, order_type: str,
                     quantity: int, price: str | None = None) -> dict:
        """Place a domestic stock cash order. Blocked unless KIS_DRY_RUN is
        explicitly false."""
        tr_id = {"BUY": "TTTC0802U", "SELL": "TTTC0801U"}[side]
        ord_dvsn = {"LIMIT": "00", "MARKET": "01"}[order_type]

        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(quantity),
            "ORD_UNPR": price if order_type == "LIMIT" else "0",
        }

        if self.dry_run:
            log.warning("[DRY RUN] order not sent: %s (tr_id=%s)", body, tr_id)
            return {"dryRun": True, "request": body}

        log.info("placing order: %s (tr_id=%s)", body, tr_id)
        return self.post("/uapi/domestic-stock/v1/trading/order-cash",
                         tr_id, body)

    def list_orders(self) -> dict:
        """Domestic stock uncleared/cancelable orders (tr_id: TTTC8036R)."""
        return self.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            tr_id="TTTC8036R",
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",  # 0: 전체
                "INQR_DVSN_2": "0",  # 0: 전체
            },
        )

    def cancel_order(self, orgn_odno: str, quantity: int = 0, branch_id: str = "") -> dict:
        """Cancel an uncleared domestic stock order (tr_id: TTTC0803U).
        
        branch_id (KRX_FWDG_ORD_ORGNO) is often returned as 'ord_gno_brno' in list_orders.
        """
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": branch_id,
            "ORGN_ODNO": orgn_odno,
            "ORD_DVSN": "00",            # required
            "RVSE_CNCL_DVSN_CD": "02",   # 02: 취소
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y" if quantity == 0 else "N",
        }
        
        if self.dry_run:
            log.warning("[DRY RUN] cancel not sent: %s (tr_id=TTTC0803U)", body)
            return {"dryRun": True, "request": body}

        log.info("canceling order %s: %s (tr_id=TTTC0803U)", orgn_odno, body)
        return self.post("/uapi/domestic-stock/v1/trading/order-rvsecncl",
                         "TTTC0803U", body)

    def price(self, symbol: str) -> dict:
        """Domestic stock current price (tr_id: FHKST01010100)."""
        return self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )

    def holdings(self) -> dict:
        """Domestic stock balance inquiry (실전 tr_id: TTTC8434R)."""
        return self.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id="TTTC8434R",
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def holdings_overseas(self) -> dict:
        """Overseas stock balance inquiry (실전 tr_id: CTRP6504R)."""
        return self.get(
            "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            tr_id="CTRP6504R",
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "WCRC_FRCR_DVSN_CD": "02",  # 01: KRW, 02: USD
                "NATN_CD": "840",           # US
                "TR_MKET_CD": "00",         # All markets
                "INQR_DVSN_CD": "00",       # 00: 전체
            },
        )


def snapshot(client: "KisClient") -> storage.AccountSnapshot:
    """Current holdings and cash, in the common cross-broker shape.

    Unlike toss_client.snapshot() (which has no broker-reported total and
    derives one as cash + sum(position value)), this trusts KIS's own
    output2.tot_evlu_amt rather than recomputing it. The two are not
    interchangeable: on a day with same-day trading, dnca_tot_amt (cash)
    lags because a same-day buy hasn't settled out of it yet, while
    tot_evlu_amt already reflects it - cash + position value would then
    overstate the account by roughly the day's unsettled trade notional.
    So a Toss-account total and a KIS-account total are computed by two
    different methods; simply summing totals across accounts for a
    combined-asset view should account for that rather than assume both
    numbers mean exactly the same thing.
    """
    resp = client.holdings()
    output2 = (resp.get("output2") or [{}])[0]

    positions = [
        {"symbol": item["pdno"], "name": item["prdt_name"],
         "qty": int(item["hldg_qty"]), "price": item["prpr"]}
        for item in resp.get("output1", [])
        if int(item.get("hldg_qty", 0)) > 0
    ]
    cash = Decimal(output2.get("dnca_tot_amt", "0"))
    total = Decimal(output2.get("tot_evlu_amt", "0"))
    return storage.AccountSnapshot(account=f"kis-{client.account}", currency="KRW",
                                   total=total, cash=cash, positions=positions)


def snapshot_overseas(client: "KisClient") -> storage.AccountSnapshot:
    """Current overseas holdings and cash, in the common cross-broker shape.

    Similar to domestic snapshot(), this trusts KIS's broker-reported total
    (output3.tot_asst_amt) instead of summing position values and cash.
    This is necessary because unsettled trades (like same-day buys) tie up
    cash but aren't fully reflected in the base cash balance until settlement,
    so simply summing output1 (positions) + output2 (cash) would overstate
    total assets by the unsettled trade notional.

    Note: output3.tot_asst_amt is provided in KRW, whereas output1 positions
    are in USD. The returned snapshot sets currency="KRW" and cash is also
    computed in KRW to align with the total.
    """
    resp = client.holdings_overseas()
    
    positions = [
        {"symbol": item["pdno"], "name": item["prdt_name"],
         "qty": int(Decimal(item["ccld_qty_smtl1"])), "price": item["ovrs_now_pric1"],
         "currency": "USD"}
        for item in resp.get("output1", [])
        if int(Decimal(item.get("ccld_qty_smtl1", "0"))) > 0
    ]
    
    output3 = resp.get("output3", {})
    # total asset in KRW
    total = Decimal(output3.get("tot_asst_amt", "0"))
    
    # Since total is in KRW, cash should also be in KRW to make sense.
    stock_value_krw = Decimal(output3.get("evlu_amt_smtl_amt", "0"))
    cash = total - stock_value_krw
    
    return storage.AccountSnapshot(
        account=f"kis-{client.account}-overseas",
        currency="KRW",
        total=total,
        cash=cash,
        positions=positions
    )


def batch_price(client: "KisClient", symbols: set[str]) -> dict[str, Decimal]:
    """Last price for each symbol.

    KIS has no multi-symbol quote endpoint, unlike Toss - one price() call
    per symbol. output.stck_prpr was verified live (ISA/102110) earlier in
    this project, not assumed from docs.
    """
    return {sym: Decimal(client.price(sym)["output"]["stck_prpr"])
            for sym in symbols}
