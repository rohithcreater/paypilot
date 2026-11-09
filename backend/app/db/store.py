import asyncio
import copy
from typing import Dict, List, Optional, Set

import httpx

from app.db.models import Order, utcnow


class MemoryStore:
    """Process-local store for development and tests. Data is lost on restart and is not
    shared between instances: use SupabaseStore for anything real."""

    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}
        self._events: Set[str] = set()
        self._campaigns: Dict[str, dict] = {}
        self._quotes: Dict[str, dict] = {}
        self._commitments: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def get_order(self, order_id: str) -> Optional[Order]:
        o = self._orders.get(order_id)
        return copy.deepcopy(o) if o else None

    async def save_order(self, order: Order) -> None:
        async with self._lock:
            self._orders[order.order_id] = copy.deepcopy(order)

    async def find_order_by_capture(self, capture_id: str) -> Optional[Order]:
        for o in self._orders.values():
            if o.capture_id == capture_id:
                return copy.deepcopy(o)
        return None

    async def list_orders(self, seller_id: Optional[str] = None, limit: int = 1000) -> List[Order]:
        rows = [o for o in self._orders.values() if seller_id is None or o.seller_id == seller_id]
        rows.sort(key=lambda o: o.created_at, reverse=True)
        return [copy.deepcopy(o) for o in rows[:limit]]

    async def seen_event(self, event_id: str) -> bool:
        return event_id in self._events

    async def mark_event(self, event_id: str) -> None:
        self._events.add(event_id)

    async def claim_event(self, event_id: str) -> bool:
        async with self._lock:
            if event_id in self._events:
                return False
            self._events.add(event_id)
            return True

    async def release_event(self, event_id: str) -> None:
        async with self._lock:
            self._events.discard(event_id)

    async def create_group_buy_campaign(self, owner_id: str, data: dict) -> dict:
        import uuid
        row = {**data, "id": str(uuid.uuid4()), "owner_id": owner_id, "status": "OPEN",
               "committed_quantity": 0, "created_at": utcnow()}
        async with self._lock:
            self._campaigns[row["id"]] = row
        return copy.deepcopy(row)

    async def add_supplier_quote(self, campaign_id: str, supplier_id: str, data: dict) -> dict:
        import uuid
        async with self._lock:
            campaign = self._campaigns.get(campaign_id)
            if not campaign or campaign["status"] != "OPEN":
                raise ValueError("Campaign not found or not open")
            row = {**data, "id": str(uuid.uuid4()), "campaign_id": campaign_id,
                   "supplier_id": supplier_id, "status": "SUBMITTED", "created_at": utcnow()}
            self._quotes[row["id"]] = row
            return copy.deepcopy(row)

    async def join_group_buy(self, campaign_id: str, buyer_id: str, quantity: int, max_unit_price: str) -> dict:
        async with self._lock:
            campaign = self._campaigns.get(campaign_id)
            if not campaign or campaign["status"] != "OPEN":
                raise ValueError("Campaign not found or not open")
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(str(campaign["deadline"]).replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                raise ValueError("Campaign deadline has passed")
            key = f"{campaign_id}:{buyer_id}"
            prior = self._commitments.get(key)
            if campaign["committed_quantity"] - (prior["quantity"] if prior else 0) + quantity > campaign["target_quantity"]:
                raise ValueError("Campaign target quantity would be exceeded")
            row = {"campaign_id": campaign_id, "buyer_id": buyer_id, "quantity": quantity,
                   "max_unit_price": max_unit_price, "status": "COMMITTED", "created_at": utcnow()}
            self._commitments[key] = row
            campaign["committed_quantity"] += quantity - (prior["quantity"] if prior else 0)
            return copy.deepcopy(row)

    async def list_group_buy_campaigns(self, status: str = "OPEN", limit: int = 20) -> list:
        rows = [r for r in self._campaigns.values() if r["status"] == status]
        return copy.deepcopy(rows[:limit])

    async def get_group_buy_details(self, campaign_id: str) -> dict:
        async with self._lock:
            campaign = self._campaigns.get(campaign_id)
            if not campaign:
                return None
            return {"campaign": copy.deepcopy(campaign),
                    "quotes": copy.deepcopy([q for q in self._quotes.values() if q["campaign_id"] == campaign_id]),
                    "commitments": copy.deepcopy([c for c in self._commitments.values() if c["campaign_id"] == campaign_id])}

    async def link_group_buy_payment(self, campaign_id: str, buyer_id: str, order_id: str) -> None:
        async with self._lock:
            row = self._commitments.get(f"{campaign_id}:{buyer_id}")
            if not row:
                raise ValueError("Buyer commitment not found")
            row["payment_order_id"] = order_id
            row["status"] = "PAYMENT_PENDING"

    async def mark_group_buy_payment_status(self, order_id: str, status: str) -> None:
        async with self._lock:
            for row in self._commitments.values():
                if row.get("payment_order_id") == order_id:
                    row["status"] = status
                    return

    async def award_group_buy(self, campaign_id: str, owner_id: str, quote_id: str) -> dict:
        async with self._lock:
            campaign = self._campaigns.get(campaign_id)
            quote = self._quotes.get(quote_id)
            if not campaign or campaign["owner_id"] != owner_id:
                raise ValueError("Campaign not found or owner mismatch")
            if not quote or quote["campaign_id"] != campaign_id:
                raise ValueError("Quote not found")
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(str(campaign["deadline"]).replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                raise ValueError("Campaign deadline has passed")
            if campaign["committed_quantity"] < campaign["minimum_quantity"]:
                raise ValueError("Minimum quantity has not been reached")
            if int(quote.get("minimum_quantity", 1)) > campaign["committed_quantity"]:
                raise ValueError("Supplier minimum quantity has not been reached")
            if quote.get("currency") != campaign.get("currency"):
                raise ValueError("Supplier quote currency does not match campaign")
            campaign["status"] = "AWARDED_PENDING_BUYER_APPROVAL"
            campaign["selected_quote_id"] = quote_id
            quote["status"] = "SELECTED_PENDING_APPROVAL"
            return copy.deepcopy(campaign)


class SupabaseStore:
    """Persistent store using Supabase PostgREST with the service-role key (backend only).
    Tables are defined in schema.sql."""

    def __init__(self, url: str, service_key: str, timeout: float = 10.0):
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self._headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}",
                         "Content-Type": "application/json"}

    async def _req(self, method: str, path: str, *, params=None, json=None, prefer: Optional[str] = None):
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.request(method, f"{self.base}{path}", params=params, json=json, headers=headers)
        r.raise_for_status()
        return r

    async def _one(self, column: str, value: str) -> Optional[Order]:
        r = await self._req("GET", "/orders", params={column: f"eq.{value}", "select": "data", "limit": "1"})
        rows = r.json()
        return Order.from_dict(rows[0]["data"]) if rows else None

    async def get_order(self, order_id: str) -> Optional[Order]:
        return await self._one("order_id", order_id)

    async def find_order_by_capture(self, capture_id: str) -> Optional[Order]:
        return await self._one("capture_id", capture_id)

    async def save_order(self, order: Order) -> None:
        row = {"order_id": order.order_id, "buyer_id": order.buyer_id, "seller_id": order.seller_id,
               "capture_id": order.capture_id, "status": order.status,
               "data": order.to_dict(), "updated_at": utcnow()}
        await self._req("POST", "/orders", params={"on_conflict": "order_id"}, json=row,
                        prefer="resolution=merge-duplicates,return=minimal")

    async def list_orders(self, seller_id: Optional[str] = None, limit: int = 1000) -> List[Order]:
        params = {"select": "data", "order": "created_at.desc", "limit": str(limit)}
        if seller_id:
            params["seller_id"] = f"eq.{seller_id}"
        r = await self._req("GET", "/orders", params=params)
        return [Order.from_dict(row["data"]) for row in r.json()]

    async def seen_event(self, event_id: str) -> bool:
        r = await self._req("GET", "/webhook_events",
                            params={"event_id": f"eq.{event_id}", "select": "event_id", "limit": "1"})
        return bool(r.json())

    async def mark_event(self, event_id: str) -> None:
        await self._req("POST", "/rpc/mark_webhook_event_processed", json={"p_event_id": event_id})


    async def claim_event(self, event_id: str) -> bool:
        r = await self._req("POST", "/rpc/claim_webhook_event", json={"p_event_id": event_id})
        value = r.json()
        return bool(value if isinstance(value, bool) else value.get("claimed", False))

    async def release_event(self, event_id: str) -> None:
        await self._req("POST", "/rpc/release_webhook_event", json={"p_event_id": event_id})

    async def create_group_buy_campaign(self, owner_id: str, data: dict) -> dict:
        r = await self._req("POST", "/group_buy_campaigns", json={**data, "owner_id": owner_id},
                            prefer="return=representation")
        return r.json()[0]

    async def add_supplier_quote(self, campaign_id: str, supplier_id: str, data: dict) -> dict:
        r = await self._req("POST", "/rpc/submit_supplier_quote", json={"p_campaign_id": campaign_id,
                            "p_supplier_id": supplier_id, "p_unit_price": data["unit_price"],
                            "p_shipping_per_unit": data["shipping_per_unit"], "p_currency": data["currency"],
                            "p_lead_days": data["lead_days"], "p_minimum_quantity": data["minimum_quantity"],
                            "p_terms": data["terms"]})
        return r.json()

    async def join_group_buy(self, campaign_id: str, buyer_id: str, quantity: int, max_unit_price: str) -> dict:
        r = await self._req("POST", "/rpc/join_group_buy", json={"p_campaign_id": campaign_id,
                            "p_buyer_id": buyer_id, "p_quantity": quantity,
                            "p_max_unit_price": max_unit_price})
        return r.json()

    async def list_group_buy_campaigns(self, status: str = "OPEN", limit: int = 20) -> list:
        r = await self._req("GET", "/group_buy_campaigns", params={"select": "*", "status": f"eq.{status}",
                            "order": "created_at.desc", "limit": str(limit)})
        return r.json()

    async def get_group_buy_details(self, campaign_id: str) -> dict:
        campaign = await self._req("GET", "/group_buy_campaigns", params={"id": f"eq.{campaign_id}",
                               "select": "*", "limit": "1"})
        rows = campaign.json()
        if not rows:
            return None
        quotes = await self._req("GET", "/supplier_quotes", params={"campaign_id": f"eq.{campaign_id}",
                            "select": "*", "order": "created_at.asc"})
        commitments = await self._req("GET", "/group_buy_commitments", params={"campaign_id": f"eq.{campaign_id}",
                                  "select": "buyer_id,quantity,max_unit_price,status,payment_order_id,created_at"})
        return {"campaign": rows[0], "quotes": quotes.json(), "commitments": commitments.json()}

    async def link_group_buy_payment(self, campaign_id: str, buyer_id: str, order_id: str) -> None:
        await self._req("PATCH", "/group_buy_commitments", params={"campaign_id": f"eq.{campaign_id}",
                        "buyer_id": f"eq.{buyer_id}"}, json={"payment_order_id": order_id,
                        "status": "PAYMENT_PENDING"}, prefer="return=minimal")

    async def mark_group_buy_payment_status(self, order_id: str, status: str) -> None:
        await self._req("PATCH", "/group_buy_commitments", params={"payment_order_id": f"eq.{order_id}"},
                        json={"status": status}, prefer="return=minimal")

    async def award_group_buy(self, campaign_id: str, owner_id: str, quote_id: str) -> dict:
        r = await self._req("POST", "/rpc/award_group_buy_quote", json={"p_campaign_id": campaign_id,
                            "p_owner_id": owner_id, "p_quote_id": quote_id})
        return r.json()
