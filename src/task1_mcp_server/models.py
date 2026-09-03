"""Strict Pydantic models for Task 1 tool inputs and outputs."""

from pydantic import BaseModel, ConfigDict, Field

CUSTOMER_ID_PATTERN = r"^CUST-[0-9]{5}$"


class StrictModel(BaseModel):
    """Base model: reject unknown fields and coercion-friendly input."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class GetCustomerRecordInput(StrictModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)


class TriggerRefundInput(StrictModel):
    customer_id: str = Field(pattern=CUSTOMER_ID_PATTERN)
    amount: float = Field(gt=0)
    reason: str = Field(min_length=10, max_length=500)


class CustomerRecord(StrictModel):
    customer_id: str
    name: str
    status: str
    tier: str


class RefundReceipt(StrictModel):
    refund_id: str
    customer_id: str
    amount: float
    reason: str
    status: str
