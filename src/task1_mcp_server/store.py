"""Synthetic in-memory data store used only by the assessment server."""

from hashlib import sha256

from .models import CustomerRecord, RefundReceipt

_CUSTOMERS = {
    "CUST-00001": CustomerRecord(
        customer_id="CUST-00001",
        name="Ada Morgan",
        status="active",
        tier="gold",
    ),
    "CUST-00002": CustomerRecord(
        customer_id="CUST-00002",
        name="Jordan Lee",
        status="active",
        tier="standard",
    ),
}


def get_customer(customer_id: str) -> CustomerRecord | None:
    """Return a synthetic customer record when present."""

    return _CUSTOMERS.get(customer_id)


def create_refund(customer_id: str, amount: float, reason: str) -> RefundReceipt:
    """Create a deterministic mock refund receipt with no external side effect."""

    digest = sha256(f"{customer_id}|{amount:.2f}|{reason}".encode()).hexdigest()[:12].upper()
    return RefundReceipt(
        refund_id=f"RFD-{digest}",
        customer_id=customer_id,
        amount=amount,
        reason=reason,
        status="accepted",
    )
