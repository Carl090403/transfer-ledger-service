"""Domain rules, kept free of HTTP and storage concerns so they are easy to test."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

ACCOUNT_RE = re.compile(r"^[A-Z]{3}-\d{6}$")
AMOUNT_RE = re.compile(r"-?\d+(\.\d{1,2})?")  # "-" allowed only so "-5" gets the clearer "> 0" message
MAX_TRANSFER_CENTS = 1_000_000_00  # 1,000,000.00 CAD per transfer (demo limit)


class ValidationError(Exception):
    """Raised with a list of human-readable problems (HTTP 400)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class BusinessRuleError(Exception):
    """A well-formed request that breaks a rule, e.g. insufficient funds (HTTP 422)."""


class NotFoundError(Exception):
    """Unknown account or transfer (HTTP 404)."""


@dataclass(frozen=True)
class TransferRequest:
    source: str
    destination: str
    amount_cents: int
    memo: str


def parse_amount(raw: object) -> int:
    """Accept a decimal string such as "125.50" and return integer cents.

    Floats are rejected on purpose: 0.1 + 0.2 style errors are unacceptable for money.
    """
    if not isinstance(raw, str):
        raise ValidationError(["amount must be a decimal string, e.g. \"125.50\""])
    # Strict format first: Decimal() alone also accepts "NaN", "Infinity", "1e2", "1_000" and padding.
    if re.fullmatch(r"-?\d+\.\d{3,}", raw):
        raise ValidationError(["amount cannot have more than 2 decimal places"])
    if not AMOUNT_RE.fullmatch(raw):
        raise ValidationError(["amount is not a valid decimal number"])
    try:
        value = Decimal(raw)
    except InvalidOperation:  # pragma: no cover - unreachable after the regex
        raise ValidationError(["amount is not a valid decimal number"]) from None
    cents = int(value * 100)
    if cents <= 0:
        raise ValidationError(["amount must be greater than 0"])
    if cents > MAX_TRANSFER_CENTS:
        raise ValidationError(["amount exceeds the per-transfer limit"])
    return cents


def validate_transfer(payload: object) -> TransferRequest:
    if not isinstance(payload, dict):
        raise ValidationError(["body must be a JSON object"])
    problems: list[str] = []
    source = payload.get("source")
    destination = payload.get("destination")
    memo = payload.get("memo", "")
    for name, value in (("source", source), ("destination", destination)):
        if not isinstance(value, str) or not ACCOUNT_RE.match(value):
            problems.append(f"{name} must look like ABC-123456")
    if source and source == destination:
        problems.append("source and destination must differ")
    if not isinstance(memo, str) or len(memo) > 140:
        problems.append("memo must be a string of at most 140 characters")
    amount_cents = 0
    try:
        amount_cents = parse_amount(payload.get("amount"))
    except ValidationError as exc:
        problems.extend(exc.problems)
    if problems:
        raise ValidationError(problems)
    return TransferRequest(source, destination, amount_cents, memo)  # type: ignore[arg-type]


def format_cents(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"
