"""Toss Securities Open API client."""

import os
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://openapi.tossinvest.com"

class TossApiError(Exception):
    """Wraps the error envelope returned by the Toss API."""

    def __init__(self, status: int, code: str, message: str,
                 field: str | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.field = field

        detail = f" (field={field})" if field else ""
        super().__init__(f"[{status} {code}] {message}{detail}")

class TossClient:
    def __init__(self) -> None:
        self.client_id = os.environ["TOSS_CLIENT_ID"]
        self.client_secret = os.environ["TOSS_CLIENT_SECRET"]
        self.account_seq = os.environ["TOSS_ACCOUNT_SEQ"]
        self.dry_run = os.getenv("TOSS_DRY_RUN", "true").lower() != "false"
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._session = requests.Session()

    def _get_token(self) -> str:
        # Reuse the cached token until it is close to expiry.
        if self._token and time.time() < self._expires_at:
            return self._token

        response = self._session.post(
            f"{BASE_URL}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        response.raise_for_status()

        body = response.json()
        self._token = body["access_token"]
        # Refresh 60s early to avoid using a token that expires mid-request.
        self._expires_at = time.time() + int(body["expires_in"]) - 60
        return self._token

    def _request(self, method: str, path: str, *,
                 params: dict | None = None,
                 body: dict | None = None,
                 need_account: bool = False) -> dict:
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }
        if need_account:
            headers["X-Tossinvest-Account"] = self.account_seq
        if body is not None:
            headers["Content-Type"] = "application/json"

        response = self._session.request(
            method,
            f"{BASE_URL}{path}",
            headers=headers,
            params=params,
            json=body,
            timeout=15,
        )

        if response.status_code >= 400:
            error = response.json().get("error", {})
            raise TossApiError(
                status=response.status_code,
                code=error.get("code", "unknown"),
                message=error.get("message", response.text[:200]),
                field=(error.get("data") or {}).get("field"),
            )

        return response.json()

    def get(self, path: str, params: dict | None = None,
            need_account: bool = False) -> dict:
        return self._request("GET", path, params=params,
                             need_account=need_account)

    def post(self, path: str, body: dict,
             need_account: bool = False) -> dict:
        return self._request("POST", path, body=body,
                             need_account=need_account)


    def list_accounts(self) -> dict:
        return self.get("/api/v1/accounts")

    def buying_power(self, currency: str = "KRW") -> dict:
            return self.get(
                "/api/v1/buying-power",
                params={"currency": currency},
                need_account=True,
            )
    def holdings(self) -> dict:
        return self.get("/api/v1/holdings", need_account=True)

    def price(self, symbol: str) -> dict:
        return self.get("/api/v1/prices", params={"symbols": symbol})

    def create_order(self, symbol: str, side: str, order_type: str,
                     quantity: int, price: str | None = None) -> dict:
        """Place an order. Blocked unless TOSS_DRY_RUN is explicitly false."""
        body: dict = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "quantity": quantity,
            "clientOrderId": uuid.uuid4().hex[:32],
        }
        if price is not None:
            body["price"] = price

        if self.dry_run:
            print(f"[DRY RUN] would POST /api/v1/orders {body}")
            return {"dryRun": True, "request": body}

        return self.post("/api/v1/orders", body, need_account=True)

    def list_orders(self, status: str = "OPEN") -> dict:
        return self.get("/api/v1/orders", params={"status": status},
                        need_account=True)

    def cancel_order(self, order_id: str) -> dict:
        if self.dry_run:
            print(f"[DRY RUN] would cancel {order_id}")
            return {"dryRun": True}
        return self.post(f"/api/v1/orders/{order_id}/cancel", {},
                         need_account=True)

    def market_calendar(self, country: str = "US") -> dict:
        return self.get(f"/api/v1/market-calendar/{country}")
