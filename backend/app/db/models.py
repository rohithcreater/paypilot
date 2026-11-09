from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Order:
    order_id: str                       # PayPal order id
    buyer_id: str
    seller_id: str
    amount: str                         # always 2-decimal string, e.g. "500.00"
    currency: str
    status: str = "CREATED"             # CREATED | CAPTURED | CAPTURE_PENDING | FAILED | REFUNDED | REVERSED |
                                        # PARTIALLY_REFUNDED | NEEDS_REVIEW
    approval_url: Optional[str] = None
    capture_id: Optional[str] = None
    created_at: str = field(default_factory=utcnow)
    captured_at: Optional[str] = None
    tracking: Optional[Dict[str, Any]] = None
    dispute: Optional[Dict[str, Any]] = None
    impact: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Order":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def public(self) -> Dict[str, Any]:
        d = self.to_dict()
        d.pop("approval_url", None)
        return d
