"""Collective procurement intelligence: compare real submitted supplier quotes.

The agent recommends; it never awards a contract or moves money. Human approval
is required before selecting a supplier and every buyer approves their own payment.
"""
from decimal import Decimal, InvalidOperation
from app.workflows.state import TransactionState


class CollectiveProcurementAgent:
    name = "collective_agent"

    def __init__(self, store):
        self.store = store

    @staticmethod
    def _rank_quotes(quotes, committed_quantity, campaign_currency):
        valid = []
        for quote in quotes:
            try:
                minimum = int(quote.get("minimum_quantity", 1))
                if quote.get("currency") != campaign_currency or minimum > committed_quantity:
                    continue
                price = Decimal(str(quote["unit_price"]))
                shipping = Decimal(str(quote.get("shipping_per_unit", "0")))
                lead = int(quote["lead_days"])
                if price <= 0 or shipping < 0 or lead < 0 or minimum < 1:
                    continue
                valid.append((quote, price + shipping, lead, minimum))
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
        if not valid:
            return []
        min_cost, max_cost = min(x[1] for x in valid), max(x[1] for x in valid)
        min_lead, max_lead = min(x[2] for x in valid), max(x[2] for x in valid)
        ranked = []
        for quote, landed, lead, minimum in valid:
            cost_score = Decimal("1") if max_cost == min_cost else (max_cost - landed) / (max_cost - min_cost)
            time_score = Decimal("1") if max_lead == min_lead else Decimal(max_lead - lead) / Decimal(max_lead - min_lead)
            # Transparent decision-support weights; no unverified seller rating is used.
            score = float(cost_score * Decimal("0.70") + time_score * Decimal("0.30"))
            ranked.append({**quote, "landed_unit_price": str(landed), "decision_score": round(score, 4),
                           "score_basis": "70% landed unit price, 30% lead time"})
        return sorted(ranked, key=lambda x: (-x["decision_score"], Decimal(x["landed_unit_price"])))

    async def run(self, state: TransactionState) -> TransactionState:
        campaign_id = state.procurement_campaign_id
        if campaign_id:
            details = await self.store.get_group_buy_details(campaign_id)
            if not details:
                state.add_log(self.name, "inspect_campaign", "failed",
                              {"summary": "I could not find that procurement campaign."})
                return state
            campaign = details["campaign"]
            ranked = self._rank_quotes(details["quotes"], int(campaign.get("committed_quantity", 0)),
                                       campaign["currency"])
            target_price = Decimal(str(campaign["target_unit_price"]))
            for quote in ranked:
                if Decimal(quote["landed_unit_price"]) > target_price:
                    quote["counter_offer_draft"] = (
                        f"Could you meet our target landed price of {campaign['currency']} {target_price:.2f} per unit "
                        f"for a committed quantity of {campaign['committed_quantity']} units? Please confirm final "
                        "pricing, delivery date and all fees."
                    )
            if not ranked:
                summary = f"{campaign['title']}: no valid supplier quotes have been submitted yet."
            else:
                best = ranked[0]
                summary = (f"{campaign['title']}: {len(ranked)} supplier quote(s) compared. Current best decision-support "
                           f"option is {best['supplier_id']} at {best['currency']} {best['landed_unit_price']} per unit, "
                           f"{best['lead_days']} day(s) lead time. Score uses {best['score_basis']}. "
                           "This is a recommendation only; the campaign owner must approve the supplier and buyers "
                           "must separately approve their own PayPal payments.")
                if best.get("counter_offer_draft"):
                    summary += " A counter-offer draft is available for quotes above the campaign target."
            state.add_log(self.name, "compare_supplier_quotes", "completed",
                          {"summary": summary, "campaign": campaign, "ranked_quotes": ranked,
                           "requires_human_approval": True}, requires_approval=True)
            return state

        campaigns = await self.store.list_group_buy_campaigns(limit=10)
        if not campaigns:
            summary = ("Collective procurement is ready: create a campaign, collect buyer commitments, "
                       "invite suppliers to quote, then compare landed price and lead time. No open campaigns exist yet.")
        else:
            summary = "Open collective procurement campaigns: " + "; ".join(
                f"{c['title']} ({c.get('committed_quantity', 0)}/{c['target_quantity']} units committed)"
                for c in campaigns)
        state.add_log(self.name, "discover_group_buys", "completed", {"summary": summary, "campaigns": campaigns})
        return state
