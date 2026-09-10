"""Korea Investment & Securities (KIS) Open API client."""

import logging
import os
import random
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from quant import broker, storage

KST = ZoneInfo("Asia/Seoul")

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

    def create_order_overseas(self, symbol: str, side: str, quantity: int,
                              price: str, exchange: str) -> dict:
        """Place a US-listed limit order. Blocked unless KIS_DRY_RUN is
        explicitly false.

        Regular session only - the day session (미국주간거래) is a
        different endpoint with its own tr_id pair
        (/uapi/overseas-stock/v1/trading/daytime-order, TTTS6036U/
        TTTS6037U), not implemented here.

        Limit orders only, like create_order(): a market order has no
        price ceiling, and unlike domestic, US regular-session buys don't
        even have a market-order code (00 지정가/32 LOO/34 LOC only) - so
        there is no order_type to make symmetric with sell in the first
        place.

        price is passed through as given (e.g. "145.00"); KIS's actual
        tick/precision rules per price band are not documented anywhere
        this could confirm them, so nothing here rounds or validates it -
        unlike create_order(), which can check against known KRX ticks.
        """
        if exchange not in ("NASD", "NYSE", "AMEX"):
            raise ValueError(
                f"exchange must be NASD/NYSE/AMEX, got {exchange!r}")

        tr_id = {"BUY": "TTTT1002U", "SELL": "TTTT1006U"}[side]
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": price,
            "ORD_DVSN": "00",
            "SLL_TYPE": "00" if side == "SELL" else "",
            "ORD_SVR_DVSN_CD": "0",
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
        }

        if self.dry_run:
            log.warning("[DRY RUN] overseas order not sent: %s (tr_id=%s)",
                       body, tr_id)
            return {"dryRun": True, "request": body}

        log.info("placing overseas order: %s (tr_id=%s)", body, tr_id)
        return self.post("/uapi/overseas-stock/v1/trading/order",
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

    def daily_chart(self, symbol: str, start: str, end: str, *,
                    adjusted: bool = False) -> dict:
        """Domestic daily OHLCV over a date range (국내주식기간별시세,
        tr_id: FHKST03010100). `start`/`end` are YYYYMMDD.

        The response's "output2" is the daily array, newest first, each row
        carrying stck_bsop_date / stck_clpr / stck_oprc / stck_hgpr /
        stck_lwpr / acml_vol / acml_tr_pbmn / flng_cls_code (all strings).
        The endpoint returns at most 100 rows and has no continuation
        cursor (no tr_cont): to reach further back, call again with `end`
        set just before the oldest date received - candles.fetch_kis()
        does this.

        adjusted=True sends FID_ORG_ADJ_PRC="0" (수정주가), which tracks
        Toss's candle series to within a won or two over 300 days of 102110
        (verified live 2026-09-10). adjusted=False ("1", 원주가) diverges by
        the cumulative distribution factor going back (~1.4% at 15 months),
        so candles.fetch_kis uses adjusted=True to stay interchangeable
        with the Toss path. Mid-session the endpoint also returns today's
        forming row with the current price as stck_clpr; callers exclude it
        the same way the Toss path does (candles.get's include_today).
        """
        return self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
            },
        )

    def holidays(self, bass_dt: str) -> dict:
        """Domestic exchange holiday calendar (국내휴장일조회, tr_id:
        CTCA0903R). `bass_dt` is YYYYMMDD.

        "output" is a list of days starting at `bass_dt`, each with
        bass_dt / wday_dvsn_cd (01=Sun..07=Sat) / bzdy_yn (bank business
        day) / tr_day_yn / opnd_yn (KRX open that day, 'Y'/'N') /
        sttl_day_yn. Verified live 2026-09-11: weekends are opnd_yn 'N'.
        The response paginates (tr_cont) but the first page covers ~3
        weeks, so a caller checking one date reads output[0].
        """
        return self.get(
            "/uapi/domestic-stock/v1/quotations/chk-holiday",
            tr_id="CTCA0903R",
            params={"BASS_DT": bass_dt, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
        )

    def orderbook(self, symbol: str) -> dict:
        """Domestic stock orderbook / asking price (tr_id: FHKST01010200)."""
        return self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            tr_id="FHKST01010200",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
            },
        )

    def orderbook_overseas(self, symbol: str, exchange: str = "NAS") -> dict:
        """Overseas stock orderbook / asking price (tr_id: HHDFS76200100)."""
        return self.get(
            "/uapi/overseas-price/v1/quotations/inquire-asking-price",
            tr_id="HHDFS76200100",
            params={
                "AUTH": "",
                "EXCD": exchange,
                "SYMB": symbol,
            },
        )

    def price_detail_overseas(self, symbol: str, exchange: str = "NAS") -> dict:
        """Overseas stock current-price detail incl. valuation ratios
        (해외주식 현재가상세, tr_id: HHDFS76200200). Response body's "output"
        carries perx/pbrx/epsx/bpsx/tomv/shar/tvol/last among other fields -
        confirmed against koreainvestment/open-trading-api's official
        price_detail.py sample.

        Note: overseas quote endpoints require a live (실전) account -
        confirmed unusable against a paper-trading (모의투자) account.
        """
        return self.get(
            "/uapi/overseas-price/v1/quotations/price-detail",
            tr_id="HHDFS76200200",
            params={
                "AUTH": "",
                "EXCD": exchange,
                "SYMB": symbol,
            },
        )

    def news_title_overseas(self, symbol: str = "", nation_cd: str = "US",
                             exchange_cd: str = "", data_dt: str = "",
                             data_tm: str = "", cts: str = "") -> dict:
        """해외뉴스종합(제목) headlines (tr_id: HHPSTH60100C1). Response body's
        "outblock1" is a list of {title, data_dt, data_tm, source, symb,
        symb_name, news_key, ...}. Confirmed against
        koreainvestment/open-trading-api's official news_title.py sample.
        """
        return self.get(
            "/uapi/overseas-price/v1/quotations/news-title",
            tr_id="HHPSTH60100C1",
            params={
                "INFO_GB": "",
                "CLASS_CD": "",
                "NATION_CD": nation_cd,
                "EXCHANGE_CD": exchange_cd,
                "SYMB": symbol,
                "DATA_DT": data_dt,
                "DATA_TM": data_tm,
                "CTS": cts,
            },
        )

    def brknews_title_overseas(self, symbol: str = "") -> dict:
        """해외속보(제목) headlines, up to 100 most recent (tr_id:
        FHKST01011801). Response body's "output" is a list of
        {hts_pbnt_titl_cntt (headline), data_dt, data_tm, dorg (source),
        iscd1..iscd10 (related symbols), ...}. Confirmed against
        koreainvestment/open-trading-api's official brknews_title.py sample.
        """
        return self.get(
            "/uapi/overseas-price/v1/quotations/brknews-title",
            tr_id="FHKST01011801",
            params={
                "FID_NEWS_OFER_ENTP_CODE": "0",
                "FID_COND_SCR_DIV_CODE": "11801",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": symbol,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "",
                "FID_INPUT_HOUR_1": "",
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
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

    def buying_power(self, symbol: str, price: str) -> dict:
        """Order-possible cash inquiry / 매수가능조회 (tr_id: TTTC8908R).

        Verified live on 2026-08-26 against kis-isa, which had sold 5
        symbols the previous day (holdings left only 현대차): the response
        carried both ord_psbl_cash and nrcvb_buy_amt, and they are NOT
        interchangeable -

        - output.ord_psbl_cash was 142,107, identical to holdings()'s
          dnca_tot_amt to the won. It does not reflect the prior day's
          12,693,752 KRW of unsettled sell proceeds at all - despite the
          name, it is a same-day-settled-cash figure like dnca_tot_amt, not
          a post-sell buying-power one. This was the field used here
          originally, which would have undersized a same-day rebuy by
          about 99%.
        - output.nrcvb_buy_amt ("미수없는매수가능금액") was 12,683,594,
          which does include those proceeds: holdings().dnca_tot_amt +
          bfdy_sll_amt - bfdy_tlex_amt = 142,107 + 12,693,752 - 14,945 =
          12,820,914 (nxdy_excc_amt, the D+1-settled total); nrcvb_buy_amt
          sits 137,320 KRW below that, a gap that held fixed across three
          re-queries with different symbol/price pairs. So it is an
          account-level cash figure like ord_psbl_cash, not order-specific,
          and the 137,320 buffer's exact source is unconfirmed but
          conservative (understates rather than overstates what is
          actually spendable) - safe to trade against as-is.

        nrcvb_buy_amt is therefore what available_cash() below reads. The
        endpoint is shaped around one symbol/price (it also returns that
        symbol's nrcvb_buy_qty at that price), but the KRW amount fields
        are account-level, not specific to the symbol passed - confirmed
        by the same re-query test above.
        """
        return self.get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            tr_id="TTTC8908R",
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_UNPR": price,
                "ORD_DVSN": "00",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )

    def daily_orders(self, start: str, end: str) -> dict:
        """Orders and their fills over a date range (tr_id: TTTC8001R).

        Dates are YYYYMMDD. Rows carry tot_ccld_qty and avg_prvs but no
        commission or tax - KIS does not return per-order fees here.
        """
        return self.get(
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            tr_id="TTTC8001R",
            params={
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": start,
                "INQR_END_DT": end,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
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


def available_cash(client: "KisClient", prices: dict[str, Decimal]) -> Decimal:
    """Cash available for a fresh buy right now (주문가능금액), for resizing
    a buy plan after sells fill - see KisClient.buying_power() for why this
    reads nrcvb_buy_amt and not ord_psbl_cash or snapshot()'s dnca_tot_amt.

    The underlying endpoint wants a symbol/price to query against; any one
    from `prices` works, since nrcvb_buy_amt does not vary with which
    symbol is passed (verified live - see buying_power()'s docstring).
    """
    if not prices:
        raise ValueError(
            "available_cash: prices is empty, no symbol to query "
            "buying power with"
        )
    symbol, price = next(iter(prices.items()))
    resp = client.buying_power(symbol, str(price))
    return Decimal(resp["output"]["nrcvb_buy_amt"])


def batch_price(client: "KisClient", symbols: set[str]) -> dict[str, Decimal]:
    """Last price for each symbol.

    KIS has no multi-symbol quote endpoint, unlike Toss - one price() call
    per symbol. output.stck_prpr was verified live (ISA/102110) earlier in
    this project, not assumed from docs.
    """
    return {sym: Decimal(client.price(sym)["output"]["stck_prpr"])
            for sym in symbols}


def dividend_events(client: "KisClient", symbol: str, *,
                    from_date: date, to_date: date) -> list[dict]:
    """Raw per-event KSD payout history (예탁원정보 배당일정 / HHKDB669102C0)
    between the two dates, confirmed live to cover ETF distributions, not
    just stock dividends.

    Returns [{"record_date": "YYYY-MM-DD", "amount": Decimal}, ...],
    newest-or-oldest order as KIS returns it (callers sort if they need
    to). Zero-amount rows are dropped. Uses per_sto_divi_amt (actual won
    paid per share) - the endpoint's own divi_rate(%) field is not used,
    since face_val comes back "0" for ETFs, so it's unclear what that
    percentage is relative to; a won amount is unambiguous.

    F_DT/T_DT genuinely filter server-side by record_date - verified live
    2026-09-01 by narrowing the range around a known event and watching it
    appear/disappear as expected. Load-bearing for any caller that caches
    this incrementally by date range.

    Does not follow pagination (tr_cont): the shared get()/_request()
    wrapper doesn't expose response headers to check it, and no symbol in
    this universe has come close to a full page of events over a 24-month
    window when this was checked live (2026-09-01: at most 8 rows). If
    that ever changes this will silently undercount rather than raise,
    since it only ever sees the first page.
    """
    resp = client.get(
        "/uapi/domestic-stock/v1/ksdinfo/dividend",
        "HHKDB669102C0",
        params={
            "CTS": "",
            "GB1": "0",
            "F_DT": from_date.strftime("%Y%m%d"),
            "T_DT": to_date.strftime("%Y%m%d"),
            "SHT_CD": symbol,
            "HIGH_GB": "",
        },
    )
    out = []
    for row in resp.get("output1", []):
        amount = Decimal(row.get("per_sto_divi_amt") or "0")
        if amount <= 0:
            continue
        raw = row["record_date"]
        out.append({"record_date": f"{raw[:4]}-{raw[4:6]}-{raw[6:]}",
                    "amount": amount})
    return out


def dividend_yield(client: "KisClient", symbol: str, price: Decimal, *,
                   months: int = 12, today: date | None = None) -> Decimal:
    """Trailing distribution yield from real KSD payout history, as a
    fraction of `price`. Thin wrapper over dividend_events() - see there
    for the underlying endpoint/caveats.
    """
    today = today or datetime.now(KST).date()
    events = dividend_events(
        client, symbol,
        from_date=today - timedelta(days=31 * months), to_date=today,
    )
    total = sum((e["amount"] for e in events), Decimal("0"))
    if price <= 0:
        return Decimal("0")
    return total / price


# --- execution interface (see quant/broker.py) ------------------------

def _level(value: str | None) -> Decimal | None:
    """One order-book level, or None when the side is empty.

    KIS fills absent levels with "0" rather than omitting them. Passing a
    zero through as a price would turn "there is no ask" into "the ask is
    zero", which reads downstream as a 100% move away from the last trade
    - the right refusal for the wrong reason, and only by luck.
    """
    if value is None:
        return None
    price = Decimal(value)
    return price if price > 0 else None


def orderbook(client: "KisClient", symbol: str) -> broker.Touch:
    """Best bid/ask. Field names verified live against ISA/102110."""
    out = client.orderbook(symbol).get("output1", {})
    bid, ask = _level(out.get("bidp1")), _level(out.get("askp1"))
    return broker.Touch(
        bid=bid,
        ask=ask,
        bid_volume=int(out.get("bidp_rsqn1") or 0) if bid else 0,
        ask_volume=int(out.get("askp_rsqn1") or 0) if ask else 0,
    )


def place_order(client: "KisClient", order, price: Decimal
                ) -> broker.OrderHandle | None:
    """Send one limit order. None means dry run - nothing was sent."""
    result = client.create_order(
        symbol=order.symbol,
        side=order.side,
        order_type="LIMIT",
        quantity=order.quantity,
        price=str(price),
    )
    if result.get("dryRun"):
        return None

    out = result["output"]
    # Both halves matter: ODNO alone cannot be cancelled later.
    return broker.OrderHandle(order_id=out["ODNO"],
                              org_no=out["KRX_FWDG_ORD_ORGNO"])


def open_orders(client: "KisClient") -> list[broker.OpenOrder]:
    """Our resting orders.

    Verified live on 2026-08-24 against ISA (102110, 1 share, deliberately
    priced 15% below the bid to guarantee it would rest): TTTC8036R's row
    fields are exactly odno / ord_gno_brno / ord_qty / tot_ccld_qty, as
    predicted from this same account's inquire-daily-ccld rows. The
    parse below, the resulting handle, and cancel() were all exercised
    against that real order, which was then cancelled. Keys are still
    read directly rather than with .get() defaults, so a KIS response
    shape change in the future raises instead of quietly reporting an
    order as unfilled.
    """
    rows = client.list_orders().get("output") or []
    return [
        broker.OpenOrder(
            handle=broker.OrderHandle(order_id=row["odno"],
                                      org_no=row["ord_gno_brno"]),
            symbol=row["pdno"],
            quantity=int(row["ord_qty"]),
            filled_quantity=int(row.get("tot_ccld_qty") or 0),
        )
        for row in rows
    ]


def cancel(client: "KisClient", handle: broker.OrderHandle) -> None:
    if handle.org_no is None:
        raise ValueError(
            f"cannot cancel KIS order {handle.order_id}: no org_no on the "
            "handle (KRX_FWDG_ORD_ORGNO is required alongside the id)"
        )
    # quantity=0 selects QTY_ALL_ORD_YN="Y", cancelling whatever remains
    # rather than a figure we would have to re-read to get right.
    client.cancel_order(orgn_odno=handle.order_id, quantity=0,
                        branch_id=handle.org_no)


def execution_for(client: "KisClient", handle: broker.OrderHandle) -> dict:
    """Fills for one order, normalised to the shape storage.save_order wants.

    commission and tax stay None: KIS's order inquiry does not return
    per-order fees, and a rate-based estimate would be recorded as though
    it were the broker's own figure.
    """
    today = datetime.now(KST).strftime("%Y%m%d")
    rows = client.daily_orders(today, today).get("output1") or []
    match = next((r for r in rows if r["odno"] == handle.order_id), None)
    if match is None:
        return {}

    return {
        "filledQuantity": match["tot_ccld_qty"],
        "averageFilledPrice": match["avg_prvs"],
        "commission": None,
        "tax": None,
    }
