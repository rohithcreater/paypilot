import asyncio
import logging
import time
from typing import Any, Dict, Optional

import httpx

from app.core.errors import PayPalError

log = logging.getLogger("paypilot.paypal")


class PayPalService:
    def __init__(self, client_id: str, client_secret: str, mode: str = "sandbox", timeout: float = 15.0):
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.base_url = "https://api-m.paypal.com" if mode == "live" else "https://api-m.sandbox.paypal.com"
        self._token: Optional[str] = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _error(r) -> PayPalError:
        try:
            body = r.json()
        except Exception:
            body = {}
        details = body.get("details") or []
        issue = None
        if details and isinstance(details[0], dict):
            issue = details[0].get("issue")
        issue = issue or body.get("error") or body.get("name")
        msg = body.get("message") or body.get("error_description") or "PayPal request failed"
        return PayPalError(r.status_code, issue, body.get("debug_id"), msg)

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            if not (self.client_id and self.client_secret):
                raise PayPalError(0, "NOT_CONFIGURED", None, "PayPal credentials are not configured")
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    r = await c.post(f"{self.base_url}/v1/oauth2/token",
                                     auth=(self.client_id, self.client_secret),
                                     data={"grant_type": "client_credentials"})
            except httpx.HTTPError as exc:
                raise PayPalError(0, "NETWORK", None, f"Could not reach PayPal ({type(exc).__name__})")
            if r.status_code != 200:
                raise self._error(r)
            data = r.json()
            self._token = data["access_token"]
            self._expires = time.monotonic() + int(data.get("expires_in", 3600)) - 60
            return self._token

    async def _request(self, method: str, path: str, *, json: Any = None,
                       request_id: Optional[str] = None) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {await self._access_token()}",
                   "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.request(method, f"{self.base_url}{path}", json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise PayPalError(0, "NETWORK", None, f"Could not reach PayPal ({type(exc).__name__})")
        if r.status_code >= 400:
            err = self._error(r)
            log.warning("PayPal %s %s -> %s %s debug_id=%s", method, path, r.status_code, err.issue, err.debug_id)
            raise err
        return r.json() if r.content else {}

    async def create_order(self, *, amount: str, currency: str, request_id: str,
                           return_url: str, cancel_url: str) -> Dict[str, Any]:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": "default",
                                "amount": {"currency_code": currency, "value": amount}}],
            "payment_source": {"paypal": {"experience_context": {
                "return_url": return_url, "cancel_url": cancel_url,
                "user_action": "PAY_NOW", "brand_name": "PayPilot AI"}}},
        }
        return await self._request("POST", "/v2/checkout/orders", json=payload, request_id=request_id)

    async def capture_order(self, order_id: str, request_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", request_id=request_id)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    async def verify_webhook(self, headers: Dict[str, str], event: Dict[str, Any], webhook_id: str) -> bool:
        body = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        if not all(body[k] for k in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time")):
            return False
        try:
            res = await self._request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        except PayPalError:
            return False
        return res.get("verification_status") == "SUCCESS"

    @staticmethod
    def approval_url(order: Dict[str, Any]) -> Optional[str]:
        for link in order.get("links", []):
            if link.get("rel") in ("payer-action", "approve"):
                return link.get("href")
        return None
