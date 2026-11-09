from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.models import Order, utcnow


@dataclass
class AgentMessage:
    agent: str
    task_id: str
    status: str                      # completed | skipped | failed
    result: Dict[str, Any]
    requires_human_approval: bool = False
    timestamp: str = field(default_factory=utcnow)


@dataclass
class TransactionState:
    user_query: str
    buyer_id: str                    # the authenticated caller (buyer or seller)
    seller_id: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: str = "USD"
    request_id: Optional[str] = None
    procurement_campaign_id: Optional[str] = None
    order: Optional[Order] = None
    approval_url: Optional[str] = None
    trust_score: Optional[float] = None
    payment_blocked: bool = False
    plan: List[str] = field(default_factory=list)
    agent_history: List[AgentMessage] = field(default_factory=list)
    final_response: str = ""
    is_terminal: bool = False

    def add_log(self, agent: str, task_id: str, status: str, result: Dict[str, Any],
                requires_approval: bool = False) -> None:
        self.agent_history.append(AgentMessage(agent, task_id, status, result, requires_approval))

    def fail(self, agent: str, task_id: str, message: str) -> None:
        self.add_log(agent, task_id, "failed", {"summary": message})
        self.is_terminal = True

    def logs(self) -> List[Dict[str, Any]]:
        return [vars(m).copy() for m in self.agent_history]
