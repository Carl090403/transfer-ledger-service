import pytest

from ledger.domain import ValidationError, parse_amount, validate_transfer


@pytest.mark.parametrize("raw,cents", [("0.01", 1), ("125.50", 12550), ("10", 1000)])
def test_parse_amount_accepts_decimal_strings(raw, cents):
    assert parse_amount(raw) == cents


@pytest.mark.parametrize(
    "raw",
    [10.5, "abc", "0", "-5", "1.001", "1000000.01", None,
     "NaN", "Infinity", "-Infinity", "sNaN", "1e2", "1_000", " 5.00", "5.", ".5", "+5"],
)
def test_parse_amount_rejects_bad_values(raw):
    with pytest.raises(ValidationError):
        parse_amount(raw)


def test_validate_transfer_reports_all_problems_at_once():
    with pytest.raises(ValidationError) as exc:
        validate_transfer({"source": "bad1", "destination": "bad2", "amount": "x", "memo": "m" * 200})
    assert len(exc.value.problems) == 4


def test_same_source_and_destination_is_rejected():
    with pytest.raises(ValidationError, match="must differ"):
        validate_transfer({"source": "CHQ-000001", "destination": "CHQ-000001", "amount": "1.00"})


def test_valid_payload_becomes_request():
    req = validate_transfer({"source": "CHQ-000001", "destination": "SAV-000002", "amount": "12.34"})
    assert req.amount_cents == 1234 and req.memo == ""
