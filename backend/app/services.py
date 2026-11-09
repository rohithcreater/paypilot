import logging
from datetime import datetime, timezone
import hashlib
import asyncio
from decimal import Decimal
from typing import Any, Dict, Optional

from app.agents.collective_agent import CollectiveProcurementAgent
from app.agents.delivery_agent import DeliveryAgent
from app.agents.dispute_agent import DisputeAgent
from app.agents.impact_agent import ImpactAgent
from app.agents.intel_agent import IntelAgent
from app.agents.orchestrator import MasterOrchestrator
from app.agents.payment_agent import PaymentAgent
from app.agents.trust_agent import TrustAgent
from app.core.errors import (BadRequest, DomainError, Forbidden, NotFound, ServiceUnavailable,
                             Unauthorized)
from app.db.models import Order, utcnow
from app.workflows.state import TransactionState

log = logging.getLogger("paypilot.service")


def _group_buy_payment_status(order_status: str) -> str:
    if order_status == "CAPTURED":
        return "PAID"
    if order_status in ("FAILED", "REFUNDED", "PARTIALLY_REFUNDED", "REVERSED"):
        return "PAYMENT_FAILED" if order_status == "FAILED" else "REFUNDED"
    if order_status == "NEEDS_REVIEW":
        return "PAYMENT_REVIEW"
    return "PAYMENT_PENDING"


def _capture_id_from_links(resource: Dict[str, Any]) -> Optional[str]:
    for link in resource.get("links") or []:
        if link.get("rel") == "up" and link.get("href"):
            return link["href"].rstrip("/").split("/")[-1]
    return None


class PayPilot:
    def __init__(self, settings, store, llm, paypal):
        self.s = settings
        self.store = store
        self.llm = llm
        self.paypal = paypal
        self.orchestrator = MasterOrchestrator(llm)
        self.payment = PaymentAgent(store, paypal, settings)
        self.agents = {
            "trust_agent": TrustAgent(store, settings),
            "payment_agent": self.payment,
            "impact_agent": ImpactAgent(settings),
            "delivery_agent": DeliveryAgent(),
            "dispute_agent": DisputeAgent(llm, settings),
            "intel_agent": IntelAgent(store),
            "collective_agent": CollectiveProcurementAgent(store),
        }

    async def _owned_order(self, user_id: str, order_id: str) -> Order:
        order = await self.store.get_order(order_id)
        if order is None:
            raise NotFound("Order not found.")
        if user_id not in (order.buyer_id, order.seller_id):
            raise Forbidden("This order belongs to another account.")
        return order

    async def handle_chat(self, *, user_id: str, message: str, seller_id: Optional[str] = None,
                          amount: Optional[Decimal] = None, order_id: Optional[str] = None,
                          request_id: Optional[str] = None,
                          procurement_campaign_id: Optional[str] = None) -> Dict[str, Any]:
        state = TransactionState(user_query=message, buyer_id=user_id, seller_id=seller_id,
                                 amount=amount, request_id=request_id,
                                 procurement_campaign_id=procurement_campaign_id)
        if order_id:
            order = await self.store.get_order(order_id)
            if order:
                if user_id not in (order.buyer_id, order.seller_id):
                    raise Forbidden("This order belongs to another account.")
                state.order = order
                state.seller_id = state.seller_id or order.seller_id

        state = await self.orchestrator.route_request(state)
        for name in state.plan:
            if state.is_terminal:
                break
            try:
                state = await asyncio.wait_for(self.agents[name].run(state),
                                               timeout=self.s.AGENT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                state.fail(name, "timeout", "This agent took too long. Please retry the request.")
            except DomainError as exc:
                state.fail(name, "run", exc.message)
            except Exception:
                log.exception("agent %s crashed", name)
                state.fail(name, "run", "Something went wrong on our side. Please try again.")
        state = await self.orchestrator.finalize(state)
        return {"response": state.final_response,
                "order_id": state.order.order_id if state.order else None,
                "payment_url": state.approval_url,
                "plan": state.plan,
                "agent_logs": state.logs(),
                "order": state.order.public() if state.order else None}

    async def capture_order(self, user_id: str, order_id: str) -> Dict[str, Any]:
        order = await self.payment.capture(order_id, user_id)
        await self.store.mark_group_buy_payment_status(order_id, _group_buy_payment_status(order.status))
        return order.public()

    async def get_order(self, user_id: str, order_id: str) -> Dict[str, Any]:
        return (await self._owned_order(user_id, order_id)).public()

    async def add_tracking(self, user_id: str, order_id: str, carrier: str, tracking_number: str,
                           status: str = "SHIPPED") -> Dict[str, Any]:
        order = await self._owned_order(user_id, order_id)
        if user_id != order.seller_id and not self.s.DEMO_MODE:
            raise Forbidden("Only the seller can add tracking.")
        if order.status != "CAPTURED":
            raise BadRequest("Tracking can only be added after the payment is captured.")
        order.tracking = {"carrier": carrier, "tracking_number": tracking_number,
                          "status": status, "simulated": False, "updated_at": utcnow()}
        await self.store.save_order(order)
        return order.public()

    async def create_group_buy_campaign(self, owner_id: str, *, title: str, product_ref: str,
                                        target_quantity: int, minimum_quantity: int, currency: str,
                                        target_unit_price: Decimal, deadline: str) -> Dict[str, Any]:
        if minimum_quantity < 2 or target_quantity < minimum_quantity or target_quantity > 100000:
            raise BadRequest("Set a minimum quantity of at least 2 and a target no lower than the minimum.")
        if target_unit_price <= 0 or target_unit_price > Decimal("10000000"):
            raise BadRequest("Invalid target unit price.")
        if not title.strip() or not product_ref.strip():
            raise BadRequest("Campaign title and product reference are required.")
        try:
            due = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            raise BadRequest("Deadline must be an ISO-8601 timestamp with a timezone.")
        if due.tzinfo is None or due <= datetime.now(timezone.utc):
            raise BadRequest("Deadline must include a timezone and be in the future.")
        if currency not in {"USD", "EUR", "GBP", "CAD", "AUD"}:
            raise BadRequest("Unsupported PayPal campaign currency.")
        data = {"title": title.strip(), "product_ref": product_ref.strip(),
                "target_quantity": target_quantity, "minimum_quantity": minimum_quantity,
                "currency": currency, "target_unit_price": str(target_unit_price), "deadline": due.isoformat()}
        return await self.store.create_group_buy_campaign(owner_id, data)

    async def submit_supplier_quote(self, supplier_id: str, campaign_id: str, *, unit_price: Decimal,
                                    currency: str, lead_days: int, minimum_quantity: int,
                                    shipping_per_unit: Decimal = Decimal("0"), terms: str = "") -> Dict[str, Any]:
        details = await self.store.get_group_buy_details(campaign_id)
        if not details or details["campaign"]["status"] != "OPEN":
            raise NotFound("Open campaign not found.")
        try:
            deadline = datetime.fromisoformat(str(details["campaign"]["deadline"]).replace("Z", "+00:00"))
        except ValueError:
            raise BadRequest("Campaign deadline is invalid.")
        if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
            raise BadRequest("Campaign deadline has passed.")
        if currency != details["campaign"]["currency"] or unit_price <= 0 or shipping_per_unit < 0:
            raise BadRequest("Quote currency must match the campaign and prices must be valid.")
        if lead_days < 0 or lead_days > 365 or minimum_quantity < 1:
            raise BadRequest("Invalid lead time or supplier minimum quantity.")
        return await self.store.add_supplier_quote(campaign_id, supplier_id,
            {"unit_price": str(unit_price), "shipping_per_unit": str(shipping_per_unit),
             "currency": currency, "lead_days": lead_days, "minimum_quantity": minimum_quantity,
             "terms": terms[:1000]})

    async def join_group_buy(self, buyer_id: str, campaign_id: str, *, quantity: int,
                             max_unit_price: Decimal) -> Dict[str, Any]:
        details = await self.store.get_group_buy_details(campaign_id)
        if not details or details["campaign"]["status"] != "OPEN":
            raise NotFound("Open campaign not found.")
        if quantity < 1 or quantity > 10000 or max_unit_price <= 0:
            raise BadRequest("Invalid quantity or maximum unit price.")
        try:
            return await self.store.join_group_buy(campaign_id, buyer_id, quantity, str(max_unit_price))
        except Exception as exc:
            raise BadRequest(str(exc)[:180])

    async def award_group_buy_quote(self, owner_id: str, campaign_id: str, quote_id: str) -> Dict[str, Any]:
        details = await self.store.get_group_buy_details(campaign_id)
        if not details or details["campaign"]["owner_id"] != owner_id:
            raise NotFound("Campaign not found.")
        quote = next((q for q in details["quotes"] if q["id"] == quote_id), None)
        if not quote:
            raise NotFound("Supplier quote not found.")
        # The atomic database function rechecks owner, campaign status, minimum quantity and quote.
        try:
            return await self.store.award_group_buy(campaign_id, owner_id, quote_id)
        except Exception as exc:
            raise BadRequest(str(exc)[:180])

    async def checkout_group_buy(self, buyer_id: str, campaign_id: str) -> Dict[str, Any]:
        details = await self.store.get_group_buy_details(campaign_id)
        if not details:
            raise NotFound("Campaign not found.")
        campaign = details["campaign"]
        if campaign["status"] != "AWARDED_PENDING_BUYER_APPROVAL":
            raise BadRequest("The campaign has not been awarded yet.")
        commitment = next((c for c in details["commitments"] if c["buyer_id"] == buyer_id), None)
        if not commitment:
            raise Forbidden("Join this campaign before checking out.")
        if commitment.get("payment_order_id"):
            order = await self.store.get_order(commitment["payment_order_id"])
            if order:
                return {"order": order.public(), "payment_url": order.approval_url}
        quote = next((q for q in details["quotes"] if q["id"] == campaign["selected_quote_id"]), None)
        if not quote:
            raise ServiceUnavailable("The selected supplier quote is unavailable.")
        landed = Decimal(str(quote["unit_price"])) + Decimal(str(quote.get("shipping_per_unit", "0")))
        if landed > Decimal(str(commitment["max_unit_price"])):
            raise BadRequest("The selected offer exceeds your maximum approved unit price.")
        total = (landed * int(commitment["quantity"])).quantize(Decimal("0.01"))
        state = TransactionState(user_query="Pay for approved collective procurement", buyer_id=buyer_id,
                                 seller_id=quote["supplier_id"], amount=total,
                                 currency=campaign["currency"], request_id="gb-" + hashlib.sha256(
                                     f"{campaign_id}:{buyer_id}".encode()).hexdigest()[:48])
        await self.payment.run(state)
        if not state.order or not state.approval_url:
            raise ServiceUnavailable(state.final_response or "Could not create the PayPal checkout.")
        await self.store.link_group_buy_payment(campaign_id, buyer_id, state.order.order_id)
        return {"order": state.order.public(), "payment_url": state.approval_url,
                "quantity": commitment["quantity"], "unit_price": str(landed), "total": str(total)}

    async def handle_webhook(self, headers: Dict[str, str], event: Dict[str, Any]) -> Dict[str, str]:
        if self.s.PAYPAL_WEBHOOK_ID:
            if not await self.paypal.verify_webhook(headers, event, self.s.PAYPAL_WEBHOOK_ID):
                raise Unauthorized("Invalid webhook signature")
        elif not self.s.DEMO_MODE:
            raise ServiceUnavailable("Webhook verification is not configured")
        event_id, etype = str(event.get("id") or ""), str(event.get("event_type") or "")
        if not event_id or not etype:
            raise BadRequest("Malformed event")
        if not await self.store.claim_event(event_id):
            return {"status": "duplicate_or_in_progress"}
        try:
            handled = await self._apply_event(etype, event.get("resource") or {})
            await self.store.mark_event(event_id)
            return {"status": "processed" if handled else "ignored"}
        except Exception:
            await self.store.release_event(event_id)
            raise

    async def _apply_event(self, etype: str, resource: Dict[str, Any]) -> bool:
        if etype.startswith("PAYMENT.CAPTURE."):
            order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
            order = await self.store.get_order(order_id) if order_id else None
            if order is None:
                cap_id = _capture_id_from_links(resource) or resource.get("id")
                order = await self.store.find_order_by_capture(cap_id) if cap_id else None
            if order is None:
                raise ServiceUnavailable("No matching order exists for this PayPal capture event yet; retry delivery.")
            # PayPal's API is the source of truth, not the webhook body.
            updated = await self.payment.apply_paypal_order(order, await self.paypal.get_order(order.order_id))
            await self.store.mark_group_buy_payment_status(updated.order_id, _group_buy_payment_status(updated.status))
            return True
        if etype.startswith("CUSTOMER.DISPUTE."):
            txs = resource.get("disputed_transactions") or []
            cap_id = txs[0].get("seller_transaction_id") if txs else None
            order = await self.store.find_order_by_capture(cap_id) if cap_id else None
            if order is None:
                raise ServiceUnavailable("No matching order exists for this PayPal dispute event yet; retry delivery.")
            order.dispute = {"dispute_id": resource.get("dispute_id"), "reason": resource.get("reason"),
                             "status": resource.get("status"),
                             "amount": (resource.get("dispute_amount") or {}).get("value"),
                             "response_due": resource.get("seller_response_due_date"),
                             "updated_at": utcnow()}
            await self.store.save_order(order)
            return True
        return False
