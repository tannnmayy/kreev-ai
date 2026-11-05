"""What the conversation knows, as plain data.

The conversation's facts live here, not in chat history. The model sees a compact rendering on every turn, every change
goes through a validated tool, and the screen renders the same data, so what you see is what Ala believes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CartLine:
    sku: str
    qty: float
    cut: Optional[str] = None


@dataclass
class Needs:
    occasion: Optional[str] = None
    servings: Optional[int] = None
    budget_inr: Optional[int] = None
    budget_flexible: bool = False
    vegetarian: bool = False            # stated by the customer, for this visit
    avoid: List[str] = field(default_factory=list)   # tags or words: "bony", "fish", "prawns"


@dataclass
class Pending:
    """A question Ala asked that the customer's next words may answer."""
    kind: str                           # order_review | confirm_item | cut | basket | substitute
    data: Dict[str, Any] = field(default_factory=dict)
    summary_hash: str = ""              # order_review: the exact cart the read-back described
    heard_complete: bool = True         # order_review: did the customer hear it to the end?


@dataclass
class Order:
    id: str
    items: List[Dict[str, Any]]
    total: int
    summary_hash: str
    status: str = "placed"              # placed | cancelled


@dataclass
class Customer:
    name: str = ""
    returning: bool = False
    avoid: List[str] = field(default_factory=list)
    usual: List[Dict[str, Any]] = field(default_factory=list)    # the last order's lines
    cut: Dict[str, str] = field(default_factory=dict)            # group -> usual cut


@dataclass
class SessionState:
    customer: Customer = field(default_factory=Customer)
    needs: Needs = field(default_factory=Needs)
    shown: List[str] = field(default_factory=list)      # skus, numbered from 1 as the customer saw them
    cart: List[CartLine] = field(default_factory=list)
    standing: List[str] = field(default_factory=list)   # instructions that hold until changed
    pending: Optional[Pending] = None
    orders: List[Order] = field(default_factory=list)
    cut_asked: bool = False                             # asked once per visit, then Ala remembers the answer
    cut_default: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionState":
        s = cls()
        s.customer = Customer(**raw.get("customer", {}))
        s.needs = Needs(**raw.get("needs", {}))
        s.shown = list(raw.get("shown", []))
        s.cart = [CartLine(**x) for x in raw.get("cart", [])]
        s.standing = list(raw.get("standing", []))
        s.pending = Pending(**raw["pending"]) if raw.get("pending") else None
        s.orders = [Order(**x) for x in raw.get("orders", [])]
        s.cut_asked = bool(raw.get("cut_asked", False))
        s.cut_default = raw.get("cut_default")
        return s
