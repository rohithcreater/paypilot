from decimal import ROUND_HALF_UP, Decimal

from app.workflows.state import TransactionState

PROJECT = "Amazon Reforestation"


def calc_impact(amount: Decimal, currency: str, rate: float, usd_per_tree: float) -> dict:
    contribution = (amount * Decimal(str(rate))).quantize(Decimal("0.01"), ROUND_HALF_UP)
    trees = int(contribution // Decimal(str(usd_per_tree))) if usd_per_tree > 0 else 0
    return {"project": PROJECT, "amount": str(contribution), "currency": currency,
            "est_trees": trees, "rate": rate}


class ImpactAgent:
    """Computes the purchase's impact pledge. This is a ledger entry only: no money is
    transferred to any project by this service."""
    name = "impact_agent"

    def __init__(self, settings):
        self.s = settings

    async def run(self, state: TransactionState) -> TransactionState:
        order = state.order
        if order is None:
            return state
        imp = calc_impact(Decimal(order.amount), order.currency, self.s.IMPACT_RATE, self.s.IMPACT_USD_PER_TREE)
        pct = f"{self.s.IMPACT_RATE * 100:g}%"
        if order.status == "CAPTURED":
            summary = (f"Impact recorded: {pct} of this purchase ({imp['currency']} {imp['amount']}) "
                       f"is pledged to {PROJECT} (about {imp['est_trees']} trees).")
        else:
            summary = (f"{pct} of this purchase ({imp['currency']} {imp['amount']}, about {imp['est_trees']} trees) "
                       f"will be pledged to {PROJECT} once the payment is captured.")
        state.add_log(self.name, "track_impact", "completed", {"summary": summary, **imp})
        return state
