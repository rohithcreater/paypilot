"""Run with: pytest -q   (from the backend/ folder). No network or API keys needed."""
import asyncio
from decimal import Decimal

import pytest

from app.agents.orchestrator import keyword_route, sanitize
from app.agents.trust_agent import compute_trust, trust_level
from app.core.config import Settings
from app.core.errors import BadRequest, Forbidden, PayPalError, Upstream
from app.core.llm import LLMClient, grounded
from app.db.store import MemoryStore
from app.services import PayPilot


class FakePayPal:
    def __init__(self):
        self.orders, self.n, self.approved = {}, 0, set()
        self.capture_amount = None

    async def create_order(self, *, amount, currency, request_id, return_url, cancel_url):
        for oid, o in self.orders.items():                      # PayPal-Request-Id idempotency
            if o["request_id"] == request_id:
                return o["body"]
        self.n += 1
        oid = f"ORDER{self.n:04d}XYZ"
        body = {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                "links": [{"rel": "self", "href": "x"}, {"rel": "payer-action", "href": f"https://pp/{oid}"}]}
        self.orders[oid] = {"request_id": request_id, "amount": amount, "currency": currency, "body": body}
        return body

    def _captured(self, oid):
        o = self.orders[oid]
        return {"id": oid, "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
            {"id": f"CAP-{oid}", "status": "COMPLETED",
             "amount": {"value": self.capture_amount or o["amount"], "currency_code": o["currency"]}}]}}]}

    async def capture_order(self, order_id, request_id):
        if order_id not in self.approved:
            raise PayPalError(422, "PAYER_ACTION_REQUIRED", "dbg", "not approved")
        return self._captured(order_id)

    async def get_order(self, order_id):
        return self._captured(order_id) if order_id in self.approved else {"id": order_id, "status": "CREATED"}

    async def verify_webhook(self, headers, event, webhook_id):
        return headers.get("good") == "1"

    @staticmethod
    def approval_url(order):
        return next((l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve")), None)


def make(**over):
    s = Settings(**over) if over else Settings()
    pp = FakePayPal()
    return PayPilot(s, MemoryStore(), LLMClient("", "http://x", "m"), pp), pp


def run(coro):
    return asyncio.run(coro)


def buy(svc, user="u1", seller="seller_abc", amount="500", rid="r1"):
    return svc.handle_chat(user_id=user, message="I want to buy a laptop", seller_id=seller,
                           amount=Decimal(amount), request_id=rid)


def test_agent_registry_has_no_duplicate_commerce_specialist():
    svc, _ = make()
    assert set(svc.agents) == {
        "trust_agent", "payment_agent", "delivery_agent", "dispute_agent",
        "intel_agent", "collective_agent", "impact_agent",
    }
    assert "commerce_agent" not in svc.agents


def test_keyword_routing():
    assert keyword_route("I want to buy a laptop, check they are trustworthy and safe") == \
        ["trust_agent", "payment_agent"]
    assert keyword_route("show the environmental impact of my purchase") == ["impact_agent"]
    assert keyword_route("buy a laptop and include the impact pledge") == \
        ["trust_agent", "payment_agent", "impact_agent"]
    assert keyword_route("show group buy campaigns") == ["collective_agent"]
    assert keyword_route("Where is my laptop?") == ["delivery_agent"]
    assert keyword_route("Check my order") == ["delivery_agent"]
    assert keyword_route("dispute from buyer") == ["delivery_agent", "dispute_agent"]
    assert keyword_route("show my sales") == ["intel_agent"]
    assert keyword_route("compare supplier quotes for group procurement") == ["collective_agent"]
    assert keyword_route("hello there") == []


def test_sanitize_blocks_unknown_and_orders_plan():
    assert sanitize(["impact_agent", "rm_rf", "trust_agent", "trust_agent"]) == ["trust_agent", "impact_agent"]
    assert sanitize(["commerce_agent", "collective_agent"]) == ["collective_agent"]
    assert sanitize("payment_agent") == []


def test_trust_score_and_levels():
    score, _ = compute_trust(120, 2, 450)
    assert 0.85 < score < 0.95 and trust_level(score) == "High"
    assert trust_level(compute_trust(5, 4, 10)[0]) == "Low"


def test_grounding_guard():
    assert grounded("You pay 500.00 today", "Order for USD 500.00") is True
    assert grounded("You pay 900 today", "Order for USD 500.00") is False


def test_buy_flow_creates_pending_order_with_approval_link():
    svc, pp = make()
    out = run(buy(svc))
    assert out["plan"] == ["trust_agent", "payment_agent"]
    assert out["payment_url"].startswith("https://pp/")
    assert out["order"]["status"] == "CREATED" and out["order"]["amount"] == "500.00"
    assert "once the payment is captured" not in out["response"]
    assert all(l["status"] != "failed" for l in out["agent_logs"])
    assert "impact_agent" not in [entry["agent"] for entry in out["agent_logs"]]


def test_same_request_id_does_not_duplicate_order():
    svc, pp = make()
    a, b = run(buy(svc)), run(buy(svc))
    assert a["order_id"] == b["order_id"] and len(pp.orders) == 1
    assert run(buy(svc, rid="r2"))["order_id"] != a["order_id"]


def test_amount_limits_and_missing_fields():
    svc, _ = make()
    assert "between" in run(buy(svc, amount="999999"))["response"]
    assert "which seller" in run(svc.handle_chat(user_id="u1", message="buy a laptop"))["response"]
    out = run(svc.handle_chat(user_id="u1", message="buy a laptop", seller_id="seller_abc"))
    assert "seller and an amount" in out["response"]


def test_low_trust_seller_is_blocked():
    svc, pp = make()
    run(svc.store.save_order(__import__("app.db.models", fromlist=["Order"]).Order(
        "OLD1", "x", "bad_seller", "10.00", "USD", status="CAPTURED", dispute={"dispute_id": "D1"})))
    out = run(buy(svc, seller="bad_seller"))
    assert out["order_id"] is None and not pp.orders and "too low" in out["response"]


def test_capture_requires_approval_then_is_idempotent_and_buyer_only():
    svc, pp = make()
    oid = run(buy(svc))["order_id"]
    with pytest.raises(BadRequest):
        run(svc.capture_order("u1", oid))
    pp.approved.add(oid)
    with pytest.raises(Forbidden):
        run(svc.capture_order("intruder", oid))
    first = run(svc.capture_order("u1", oid))
    again = run(svc.capture_order("u1", oid))
    assert first["status"] == "CAPTURED" and first["captured_at"] == again["captured_at"]
    assert first["impact"]["amount"] == "5.00" and first["impact"]["est_trees"] == 5


def test_amount_mismatch_flags_order_for_review():
    svc, pp = make()
    oid = run(buy(svc))["order_id"]
    pp.approved.add(oid)
    pp.capture_amount = "1.00"
    with pytest.raises(Upstream):
        run(svc.capture_order("u1", oid))
    assert run(svc.store.get_order(oid)).status == "NEEDS_REVIEW"


def test_delivery_before_and_after_capture():
    svc, pp = make()
    oid = run(buy(svc))["order_id"]
    early = run(svc.handle_chat(user_id="u1", message="Where is my laptop?", order_id=oid))
    assert "starts once the payment is captured" in early["response"]
    pp.approved.add(oid)
    run(svc.capture_order("u1", oid))
    late = run(svc.handle_chat(user_id="u1", message="Where is my laptop?", order_id=oid))
    assert "in transit" in late["response"] and "demo data" in late["response"]


def test_order_isolated_between_users():
    svc, _ = make()
    oid = run(buy(svc))["order_id"]
    with pytest.raises(Forbidden):
        run(svc.handle_chat(user_id="other", message="where is my order", order_id=oid))


def test_webhook_marks_captured_and_dedupes_and_needs_signature():
    svc, pp = make(PAYPAL_WEBHOOK_ID="WH1")
    oid = run(buy(svc))["order_id"]
    pp.approved.add(oid)
    ev = {"id": "EV1", "event_type": "PAYMENT.CAPTURE.COMPLETED",
          "resource": {"supplementary_data": {"related_ids": {"order_id": oid}}}}
    from app.core.errors import Unauthorized
    with pytest.raises(Unauthorized):
        run(svc.handle_webhook({}, ev))
    assert run(svc.handle_webhook({"good": "1"}, ev))["status"] == "processed"
    assert run(svc.handle_webhook({"good": "1"}, ev))["status"] == "duplicate_or_in_progress"
    assert run(svc.store.get_order(oid)).status == "CAPTURED"


def test_dispute_webhook_and_draft_excludes_demo_tracking():
    svc, pp = make()
    oid = run(buy(svc))["order_id"]
    pp.approved.add(oid)
    run(svc.capture_order("u1", oid))
    ev = {"id": "EV2", "event_type": "CUSTOMER.DISPUTE.CREATED",
          "resource": {"dispute_id": "PP-D-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "status": "OPEN",
                       "disputed_transactions": [{"seller_transaction_id": f"CAP-{oid}"}]}}
    assert run(svc.handle_webhook({}, ev))["status"] == "processed"
    out = run(svc.handle_chat(user_id="u1", message="buyer opened a dispute", order_id=oid))
    assert "PP-D-1" in out["response"] and "demo tracking" in out["response"]
    draft = [l for l in out["agent_logs"] if l["agent"] == "dispute_agent"][0]
    assert draft["requires_human_approval"] and "DemoCarrier" not in draft["result"]["draft"]
    run(svc.add_tracking("u1", oid, "UPS", "1Z999"))
    out2 = run(svc.handle_chat(user_id="u1", message="buyer opened a dispute", order_id=oid))
    assert "UPS" in [l for l in out2["agent_logs"] if l["agent"] == "dispute_agent"][0]["result"]["draft"]


def test_intel_uses_real_orders():
    svc, pp = make()
    for i in range(2):
        oid = run(buy(svc, rid=f"r{i}"))["order_id"]
    pp.approved.add(oid)
    run(svc.capture_order("u1", oid))
    out = run(svc.handle_chat(user_id="u1", message="show my sales", seller_id="seller_abc"))
    assert "2 orders, 1 paid (50%)" in out["response"] and "USD 500.00" in out["response"]


def test_unknown_request_gets_help():
    svc, _ = make()
    assert "I can help" in run(svc.handle_chat(user_id="u1", message="tell me a joke"))["response"]


def test_collective_procurement_award_and_buyer_paypal_checkout():
    svc, pp = make()
    from datetime import datetime, timedelta, timezone

    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    campaign = run(svc.create_group_buy_campaign(
        "owner_1", title="Laptop accessories bulk order", product_ref="SKU-USB-C-HUB",
        target_quantity=100, minimum_quantity=10, currency="USD",
        target_unit_price=Decimal("18.00"), deadline=deadline))
    campaign_id = campaign["id"]
    run(svc.join_group_buy("buyer_1", campaign_id, quantity=10, max_unit_price=Decimal("22.00")))
    quote = run(svc.submit_supplier_quote(
        "supplier_1", campaign_id, unit_price=Decimal("19.00"), currency="USD",
        lead_days=5, minimum_quantity=10, shipping_per_unit=Decimal("1.00")))
    awarded = run(svc.award_group_buy_quote("owner_1", campaign_id, quote["id"]))
    assert awarded["status"] == "AWARDED_PENDING_BUYER_APPROVAL"

    result = run(svc.handle_chat(user_id="buyer_1", message="compare supplier quotes",
                                 procurement_campaign_id=campaign_id))
    collective = [x for x in result["agent_logs"] if x["agent"] == "collective_agent"][0]
    assert collective["requires_human_approval"]
    assert "supplier_1" in collective["result"]["summary"]
    assert collective["result"]["ranked_quotes"][0]["counter_offer_draft"].startswith("Could you meet our target")

    checkout = run(svc.checkout_group_buy("buyer_1", campaign_id))
    assert checkout["total"] == "200.00"
    assert checkout["payment_url"].startswith("https://pp/")
    assert checkout["order"]["status"] == "CREATED"
    assert len(pp.orders) == 1
    pp.approved.add(checkout["order"]["order_id"])
    run(svc.capture_order("buyer_1", checkout["order"]["order_id"]))
    details = run(svc.store.get_group_buy_details(campaign_id))
    assert details["commitments"][0]["status"] == "PAID"


def test_group_buy_cannot_award_before_minimum_quantity():
    svc, _ = make()
    from datetime import datetime, timedelta, timezone

    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    campaign = run(svc.create_group_buy_campaign(
        "owner_1", title="Bulk keyboards", product_ref="SKU-KEYBOARD", target_quantity=20,
        minimum_quantity=10, currency="USD", target_unit_price=Decimal("30.00"), deadline=deadline))
    quote = run(svc.submit_supplier_quote("supplier_1", campaign["id"], unit_price=Decimal("28.00"),
        currency="USD", lead_days=3, minimum_quantity=5))
    with pytest.raises(BadRequest):
        run(svc.award_group_buy_quote("owner_1", campaign["id"], quote["id"]))
