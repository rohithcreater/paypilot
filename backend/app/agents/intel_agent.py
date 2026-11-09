from decimal import Decimal

from app.workflows.state import TransactionState


class IntelAgent:
    """Merchant analytics computed from stored orders only."""
    name = "intel_agent"

    def __init__(self, store):
        self.store = store

    async def run(self, state: TransactionState) -> TransactionState:
        merchant = state.seller_id or state.buyer_id
        orders = await self.store.list_orders(seller_id=merchant)
        if not orders:
            state.add_log(self.name, "analyze_merchant", "completed",
                          {"summary": f"No orders found for merchant {merchant} yet."})
            return state
        paid = [o for o in orders if o.status == "CAPTURED"]
        partially_refunded = [o for o in orders if o.status == "PARTIALLY_REFUNDED"]
        unpaid = [o for o in orders if o.status in ("CREATED", "CAPTURE_PENDING")]
        disputes = [o for o in orders if o.dispute]
        revenue = sum((Decimal(o.amount) for o in paid), Decimal("0"))
        rate = len(paid) / len(orders)
        cur = orders[0].currency
        insights = []
        if unpaid:
            insights.append(f"{len(unpaid)} order(s) were started but not paid; a reminder may recover them.")
        if partially_refunded:
            insights.append(f"{len(partially_refunded)} order(s) are partially refunded; gross captured value is shown before refund deductions.")
        if disputes:
            insights.append(f"{len(disputes)} order(s) have disputes; respond before the PayPal deadline.")
        if rate < 0.8:
            insights.append(f"Only {rate:.0%} of orders reach payment; check where buyers drop off.")
        summary = (f"{len(orders)} orders, {len(paid)} paid ({rate:.0%}) (fully captured), gross captured value {cur} {revenue:,.2f}."
                   + ("" if not insights else " " + " ".join(insights)))
        state.add_log(self.name, "analyze_merchant", "completed",
                      {"summary": summary, "revenue": str(revenue), "orders": len(orders),
                       "paid": len(paid), "disputes": len(disputes)})
        return state
