import pytest
from pydantic import ValidationError

from task1_mcp_server.models import GetCustomerRecordInput, TriggerRefundInput


def test_customer_id_accepts_required_shape() -> None:
    model = GetCustomerRecordInput(customer_id="CUST-12345")
    assert model.customer_id == "CUST-12345"


@pytest.mark.parametrize(
    "value",
    ["CUST-1234", "cust-12345", "CUST-ABCDE", "12345", "CUST-123456", ""],
)
def test_customer_id_rejects_malformed_values(value: str) -> None:
    with pytest.raises(ValidationError):
        GetCustomerRecordInput(customer_id=value)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GetCustomerRecordInput(customer_id="CUST-12345", unexpected="value")


def test_refund_requires_positive_float_and_meaningful_reason() -> None:
    model = TriggerRefundInput(
        customer_id="CUST-12345",
        amount=12.5,
        reason="Duplicate charge",
    )
    assert model.amount == 12.5


@pytest.mark.parametrize("amount", [0.0, -1.0, "12.5", True])
def test_refund_rejects_invalid_amounts(amount: object) -> None:
    with pytest.raises(ValidationError):
        TriggerRefundInput(
            customer_id="CUST-12345",
            amount=amount,
            reason="Duplicate charge",
        )


def test_refund_rejects_short_reason_after_whitespace_stripping() -> None:
    with pytest.raises(ValidationError):
        TriggerRefundInput(
            customer_id="CUST-12345",
            amount=5.0,
            reason="   too short   ",
        )
