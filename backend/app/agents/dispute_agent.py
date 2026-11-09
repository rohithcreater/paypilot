from app.core.llm import grounded
from app.workflows.state import TransactionState


class DisputeAgent:
    """Drafts a seller response from facts on the order record. It never submits anything:
    the draft always requires merchant review."""
    name = "dispute_agent"

    def __init__(self, llm, settings):
        self.llm = llm
        self.s = settings

    async def run(self, state: TransactionState) -> TransactionState:
        o = state.order
        if o is None:
            state.fail(self.name, "draft_dispute_response", "Select an order first.")
            return state
        if state.buyer_id != o.seller_id and not self.s.DEMO_MODE:
            state.fail(self.name, "draft_dispute_response", "Only the seller can prepare a dispute response.")
            return state

        evidence, gaps = [], []
        if o.status == "CAPTURED":
            evidence.append(f"Payment of {o.currency} {o.amount} captured on {o.captured_at} (capture {o.capture_id}).")
        else:
            gaps.append(f"Payment is {o.status.replace('_', ' ').lower()}, not captured.")
        t = o.tracking
        if t and not t.get("simulated"):
            evidence.append(f"Shipped via {t.get('carrier')} #{t.get('tracking_number')}, "
                            f"status {str(t.get('status')).replace('_', ' ').lower()}.")
        elif t:
            gaps.append("Only demo tracking exists, which is not valid evidence.")
        else:
            gaps.append("No shipping or tracking proof is on file.")
        d = o.dispute or {}
        header = f"Dispute {d['dispute_id']}" if d.get("dispute_id") else "No open dispute on file"
        reason = f" Reason: {d['reason']}." if d.get("reason") else ""

        draft = "\n".join([f"Subject: Seller response, order {o.order_id}", "", "Evidence:"]
                          + [f"- {e}" for e in evidence] + ([] if evidence else ["- (none available)"])
                          + ["", "We ask PayPal to review the evidence above."])
        if self.llm.enabled and evidence:
            facts = "\n".join(evidence)
            polished = await self.llm.generate(
                f"Evidence:\n{facts}", max_tokens=300,
                system="Write a short, professional seller response to a payment dispute. Use ONLY the "
                       "evidence provided. Do not add facts, dates, or numbers. Return plain text.")
            if polished and grounded(polished, facts):
                draft = polished
        summary = f"{header}.{reason} Draft prepared for your review; nothing was sent to PayPal."
        if gaps:
            summary += " Gaps: " + " ".join(gaps)
        state.add_log(self.name, "draft_dispute_response", "completed",
                      {"summary": summary, "draft": draft, "gaps": gaps}, requires_approval=True)
        return state
