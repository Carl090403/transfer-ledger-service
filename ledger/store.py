"""SQLite-backed ledger. Every transfer is atomic, idempotent and audited."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime

from .domain import BusinessRuleError, NotFoundError, TransferRequest

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0)
);
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    request_hash TEXT NOT NULL,
    source TEXT NOT NULL REFERENCES accounts(id),
    destination TEXT NOT NULL REFERENCES accounts(id),
    amount_cents INTEGER NOT NULL,
    memo TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        # SQLite serialises writes, but one connection is shared across HTTP threads,
        # so a process-level lock keeps each BEGIN/COMMIT block exclusive.
        self._lock = threading.Lock()

    # -- accounts -------------------------------------------------------
    def open_account(self, account_id: str, owner: str, balance_cents: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO accounts (id, owner, balance_cents) VALUES (?, ?, ?)",
                (account_id, owner, balance_cents),
            )
            self._audit("account_opened", {"account": account_id})

    def balance(self, account_id: str) -> int:
        row = self._conn.execute("SELECT balance_cents FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"account {account_id} not found")
        return row[0]

    # -- transfers ------------------------------------------------------
    def transfer(self, req: TransferRequest, idempotency_key: str) -> tuple[dict, bool]:
        """Return (transfer, created). Replaying the same key + body returns the original result."""
        request_hash = hashlib.sha256(
            json.dumps([req.source, req.destination, req.amount_cents, req.memo]).encode()
        ).hexdigest()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM transfers WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing[2] != request_hash:
                    raise BusinessRuleError("idempotency key was already used with a different request")
                return self._row_to_transfer(existing), False

            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for acct in (req.source, req.destination):
                    if self._conn.execute("SELECT 1 FROM accounts WHERE id = ?", (acct,)).fetchone() is None:
                        raise NotFoundError(f"account {acct} not found")
                updated = self._conn.execute(
                    "UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ? AND balance_cents >= ?",
                    (req.amount_cents, req.source, req.amount_cents),
                ).rowcount
                if updated != 1:
                    raise BusinessRuleError("insufficient funds")
                self._conn.execute(
                    "UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                    (req.amount_cents, req.destination),
                )
                transfer_id = str(uuid.uuid4())
                now = datetime.now(UTC).isoformat()
                self._conn.execute(
                    "INSERT INTO transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (transfer_id, idempotency_key, request_hash, req.source, req.destination, req.amount_cents, req.memo, now),
                )
                self._audit("transfer_created", {"id": transfer_id, "amount_cents": req.amount_cents})
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                self._audit("transfer_rejected", {"source": req.source, "amount_cents": req.amount_cents})
                raise
            row = self._conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
            return self._row_to_transfer(row), True

    def get_transfer(self, transfer_id: str) -> dict:
        row = self._conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
        if row is None:
            raise NotFoundError("transfer not found")
        return self._row_to_transfer(row)

    def audit_events(self) -> list[tuple[str, dict]]:
        return [(e, json.loads(d)) for e, d in self._conn.execute("SELECT event, detail FROM audit_log ORDER BY seq")]

    def total_money(self) -> int:
        return self._conn.execute("SELECT COALESCE(SUM(balance_cents), 0) FROM accounts").fetchone()[0]

    # -- helpers --------------------------------------------------------
    def _audit(self, event: str, detail: dict) -> None:
        # Audit entries never contain owner names or memos (data minimisation).
        self._conn.execute(
            "INSERT INTO audit_log (at, event, detail) VALUES (?, ?, ?)",
            (datetime.now(UTC).isoformat(), event, json.dumps(detail, sort_keys=True)),
        )

    @staticmethod
    def _row_to_transfer(row: tuple) -> dict:
        return {
            "id": row[0], "source": row[3], "destination": row[4],
            "amount_cents": row[5], "memo": row[6], "created_at": row[7],
        }
