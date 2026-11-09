from datetime import datetime, timezone
from typing import Dict, List, Tuple

from app.workflows.state import TransactionState

# Used only when DEMO_MODE is on, so the demo has a seller with history.
DEMO_SELLERS = {"seller_abc": {"completed": 120, "disputes": 2, "age_days": 450}}


def compute_trust(completed: int, disputes: int, age_days: int) -> Tuple[float, List[str]]:
    volume = completed / (completed + 20)
    dispute_rate = disputes / max(completed, 1)
    reliability = 1 - min(dispute_rate * 5, 1)
    age = min(age_days / 365, 1)
    score = round(0.5 * volume + 0.35 * reliability + 0.15 * age, 2)
    factors = [f"{completed} completed orders", f"{dispute_rate:.1%} dispute rate", f"{age_days} days active"]
    return score, factors


def trust_level(score: float) -> str:
    return "High" if score >= 0.75 else "Medium" if score >= 0.5 else "Low"


class TrustAgent:
    name = "trust_agent"

    def __init__(self, store, settings):
        self.store = store
        self.s = settings

    async def _stats(self, seller_id: str) -> Tuple[Dict[str, int], str]:
        orders = await self.store.list_orders(seller_id=seller_id)
        completed = [o for o in orders if o.status in ("CAPTURED", "PARTIALLY_REFUNDED")]
        disputes = sum(1 for o in orders if o.dispute)
        age_days = 0
        if orders:
            first = min(datetime.fromisoformat(o.created_at) for o in orders)
            age_days = max((datetime.now(timezone.utc) - first).days, 0)
        real = {"completed": len(completed), "disputes": disputes, "age_days": age_days}
        demo = DEMO_SELLERS.get(seller_id)
        if self.s.DEMO_MODE and demo and real["completed"] < demo["completed"]:
            return dict(demo), "demo_seed"
        return real, "orders"

    async def run(self, state: TransactionState) -> TransactionState:
        if not state.seller_id:
            state.fail(self.name, "analyze_trust", "Tell me which seller you want to check.")
            return state
        stats, source = await self._stats(state.seller_id)
        if stats["completed"] == 0:
            result = {"summary": f"Seller {state.seller_id} has no completed orders on PayPilot yet, so there is "
                                 "no history to score. This seller is unverified on PayPilot; this is not a guarantee of safety.",
                      "score": None, "level": "Unverified", "source": source, "stats": stats}
        else:
            score, factors = compute_trust(**stats)
            level = trust_level(score)
            state.trust_score = score
            note = " (demo data)" if source == "demo_seed" else ""
            result = {"summary": f"Seller {state.seller_id} has a PayPilot transaction-history signal of {score:.2f} ({level}){note}: "
                                 + ", ".join(factors) + ". This is a limited history signal, not identity verification or a safety guarantee.",
                      "score": score, "level": level, "source": source, "stats": stats}
            if score < self.s.MIN_TRUST_SCORE:
                state.payment_blocked = True
                result["summary"] += " Payment was not prepared because the trust score is too low."
        state.add_log(self.name, "analyze_trust", "completed", result)
        return state
