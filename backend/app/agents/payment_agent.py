import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, Optional

from app.agents.impact_agent import calc_impact
from app.core.errors import BadRequest, Forbidden, NotFound, PayPalError, Upstream
from app.db.models import Order, utcnow
from app.workflows.state import TransactionState

log = logging.getLogger("paypilot.payment")

CAPTURE_STATUS = {"COMPLETED": "CAPTURED", "PENDING": "CAPTURE_PENDING", "DECLINED": "FAILED", "DENIED": "FAILED",
                  "FAILED": "FAILED", "REFUNDED": "REFUNDED", "PARTIALLY_REFUNDED": "PARTIALLY_REFUNDED",
                  "REVERSED": "REVERSED"}
CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD"}


def first_capture(paypal_order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    units = paypal_order.get("purchase_units") or []
    caps = ((units[0].get("payments") or {}).get("captures") or []) if units else []
    return caps[0] if caps else None


class PaymentAgent:
    """Creates and captures PayPal orders. Order status only ever changes from PayPal's own
    responses, never from anything the client or the LLM says."""
    name = "payment_agent"

    def __init__(self, store, paypal, settings):
        self.store = store
        self.paypal = paypal
        self.s = settings

    async def run(self, state: TransactionState) -> TransactionState:
        if state.payment_blocked:
            state.add_log(self.name, "process_payment", "skipped",
                          {"summary": "No payment was prepared."})
            return state
        if not state.seller_id or state.amount is None:
            state.fail(self.name, "process_payment", "Enter a seller and an amount to start a purchase.")
            return state
        try:
            amount = Decimal(state.amount).quantize(Decimal("0.01"), ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            state.fail(self.name, "process_payment", "That amount is not valid.")
            return state
        if amount <= 0 or amount > Decimal(str(self.s.MAX_ORDER_AMOUNT)):
            state.fail(self.name, "process_payment",
                       f"Amount must be between 0.01 and {self.s.MAX_ORDER_AMOUNT:,.2f}.")
            return state
        if state.currency not in CURRENCIES:
            state.fail(self.name, "process_payment", "Unsupported currency.")
            return state

        request_id = f"order-{state.buyer_id}-{state.request_id or uuid.uuid4().hex}"[:100]
        base = self.s.FRONTEND_URL.rstrip("/")
        try:
            pp = await self.paypal.create_order(
                amount=f"{amount:.2f}", currency=state.currency, request_id=request_id,
                return_url=f"{base}/checkout/return", cancel_url=f"{base}/?cancelled=1")
        except PayPalError as exc:
            log.error("create_order failed issue=%s debug_id=%s", exc.issue, exc.debug_id)
            state.fail(self.name, "process_payment",
                       "PayPal could not create the order right now. Nothing was charged.")
            return state

        approval = self.paypal.approval_url(pp)
        if not pp.get("id") or not approval:
            state.fail(self.name, "process_payment", "PayPal returned an unexpected response. Nothing was charged.")
            return state

        order = await self.store.get_order(pp["id"])      # same request id => same PayPal order
        if order is None:
            order = Order(order_id=pp["id"], buyer_id=state.buyer_id, seller_id=state.seller_id,
                          amount=f"{amount:.2f}", currency=state.currency, approval_url=approval)
            await self.store.save_order(order)
        state.order = order
        state.approval_url = approval if order.status == "CREATED" else None
        state.add_log(self.name, "process_payment", "completed",
                      {"summary": f"Order created for {order.currency} {order.amount}. Nothing is charged until you "
                                  "approve the payment on PayPal.",
                       "order_id": order.order_id, "status": order.status}, requires_approval=True)
        return state

    async def capture(self, order_id: str, user_id: str) -> Order:
        order = await self.store.get_order(order_id)
        if order is None:
            raise NotFound("Order not found.")
        if order.buyer_id != user_id:
            raise Forbidden("Only the buyer can complete this payment.")
        if order.status == "CAPTURED":
            return order
        try:
            data = await self.paypal.capture_order(order_id, request_id=f"capture-{order_id}")
        except PayPalError as exc:
            if exc.issue == "ORDER_ALREADY_CAPTURED":
                data = await self.paypal.get_order(order_id)
            elif exc.issue in ("PAYER_ACTION_REQUIRED", "ORDER_NOT_APPROVED"):
                raise BadRequest("The PayPal checkout has not been approved yet.")
            else:
                log.error("capture failed order=%s issue=%s debug_id=%s", order_id, exc.issue, exc.debug_id)
                raise Upstream("PayPal could not complete the payment. Please try again.")
        return await self.apply_paypal_order(order, data)

    async def apply_paypal_order(self, order: Order, data: Dict[str, Any]) -> Order:
        cap = first_capture(data)
        if cap is None:
            return order
        amt = cap.get("amount") or {}
        try:
            same = Decimal(str(amt.get("value"))) == Decimal(order.amount) and amt.get("currency_code") == order.currency
        except InvalidOperation:
            same = False
        if not same:
            order.status = "NEEDS_REVIEW"
            await self.store.save_order(order)
            raise Upstream("The captured amount does not match the order and was flagged for review.")
        new_status = CAPTURE_STATUS.get(cap.get("status"), order.status)
        first_time = new_status == "CAPTURED" and order.status != "CAPTURED"
        order.status = new_status
        order.capture_id = cap.get("id") or order.capture_id
        if first_time:
            order.captured_at = utcnow()
            order.impact = calc_impact(Decimal(order.amount), order.currency,
                                       self.s.IMPACT_RATE, self.s.IMPACT_USD_PER_TREE)
            if self.s.DEMO_MODE and not order.tracking:
                order.tracking = {"carrier": "DemoCarrier", "tracking_number": f"DEMO{order.order_id[-8:]}",
                                  "status": "IN_TRANSIT", "last_location": "Memphis, TN", "simulated": True}
        await self.store.save_order(order)
        return order
