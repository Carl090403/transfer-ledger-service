import threading

import pytest

from ledger.domain import BusinessRuleError, NotFoundError, TransferRequest
from ledger.store import Ledger


@pytest.fixture
def ledger():
    lg = Ledger()
    lg.open_account("CHQ-000001", "Alice", 100_00)
    lg.open_account("SAV-000002", "Bob", 0)
    return lg


def req(amount=25_00, memo=""):
    return TransferRequest("CHQ-000001", "SAV-000002", amount, memo)


def test_transfer_moves_money_and_conserves_total(ledger):
    transfer, created = ledger.transfer(req(), "key-00000001")
    assert created and transfer["amount_cents"] == 25_00
    assert ledger.balance("CHQ-000001") == 75_00
    assert ledger.balance("SAV-000002") == 25_00
    assert ledger.total_money() == 100_00


def test_insufficient_funds_rolls_back(ledger):
    with pytest.raises(BusinessRuleError, match="insufficient"):
        ledger.transfer(req(amount=500_00), "key-00000002")
    assert ledger.balance("CHQ-000001") == 100_00
    assert ledger.audit_events()[-1][0] == "transfer_rejected"


def test_unknown_account(ledger):
    with pytest.raises(NotFoundError):
        ledger.transfer(TransferRequest("CHQ-000001", "SAV-999999", 1, ""), "key-00000003")
    assert ledger.balance("CHQ-000001") == 100_00


def test_idempotent_replay_returns_same_transfer(ledger):
    first, created1 = ledger.transfer(req(), "key-00000004")
    second, created2 = ledger.transfer(req(), "key-00000004")
    assert created1 and not created2 and first == second
    assert ledger.balance("CHQ-000001") == 75_00  # charged once


def test_reusing_key_with_different_body_is_rejected(ledger):
    ledger.transfer(req(), "key-00000005")
    with pytest.raises(BusinessRuleError, match="idempotency"):
        ledger.transfer(req(amount=1_00), "key-00000005")


def test_concurrent_transfers_never_overdraw(ledger):
    errors = []

    def worker(i):
        try:
            ledger.transfer(req(amount=30_00), f"key-concurrent-{i:04d}")
        except BusinessRuleError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ledger.balance("CHQ-000001") == 10_00  # 3 succeed, 7 rejected
    assert len(errors) == 7
    assert ledger.total_money() == 100_00


def test_audit_log_has_no_personal_data(ledger):
    ledger.transfer(req(memo="rent for Alice"), "key-00000006")
    for _, detail in ledger.audit_events():
        assert "Alice" not in str(detail) and "rent" not in str(detail)
