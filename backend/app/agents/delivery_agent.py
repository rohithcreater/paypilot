from app.workflows.state import TransactionState


class DeliveryAgent:
    name = "delivery_agent"

    async def run(self, state: TransactionState) -> TransactionState:
        order = state.order
        if order is None:
            state.add_log(self.name, "track_delivery", "skipped",
                          {"summary": "No order selected yet. Complete a purchase first."})
            return state
        if order.status != "CAPTURED":
            state.add_log(self.name, "track_delivery", "skipped",
                          {"summary": f"Order {order.order_id} is {order.status.replace('_', ' ').lower()}; "
                                      "delivery tracking starts once the payment is captured."})
            return state
        t = order.tracking
        if not t:
            state.add_log(self.name, "track_delivery", "completed",
                          {"summary": "Payment is captured, but the seller has not added tracking yet."})
            return state
        where = f" Last seen: {t['last_location']}." if t.get("last_location") else ""
        demo = " (demo data)" if t.get("simulated") else " (seller-reported; carrier status not independently verified)"
        state.add_log(self.name, "track_delivery", "completed",
                      {"summary": f"{t.get('carrier', 'Carrier')} {t.get('tracking_number', '')}: "
                                  f"{str(t.get('status', 'UNKNOWN')).replace('_', ' ').lower()}.{where}{demo}",
                       "tracking": t})
        return state
