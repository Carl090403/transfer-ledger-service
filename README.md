# Transfer Ledger Service

A small, **test-driven** funds-transfer microservice written to practise the skills listed for a
new-graduate software developer role at a bank: TDD, SDLC discipline, troubleshooting, secure
coding, logging and performance-aware design.

Python 3.11+, **standard library only** (http.server, sqlite3, decimal, threading) plus pytest/ruff for development.

## Features

| Area | What is implemented |
|---|---|
| API | `POST /transfers`, `GET /transfers/{id}`, `GET /accounts/{id}/balance`, `GET /health` |
| Money | Amounts are decimal **strings** converted to integer cents; floats are rejected |
| Atomicity | Each transfer runs in one SQLite transaction (`BEGIN IMMEDIATE`); failures roll back |
| Idempotency | Required `Idempotency-Key` header; replaying the same key returns the original transfer, reusing it with a different body is refused |
| Concurrency | A test fires 10 parallel transfers against a 100.00 balance: exactly 3 succeed, total money is conserved |
| Validation | Every input problem returned at once (400); rule violations such as insufficient funds return 422 |
| Logging & audit | One structured JSON log line per request with `X-Request-Id` and latency; audit table without personal data |
| Security basics | Body size limit, content-type check, no stack traces in responses, `Cache-Control: no-store` |
| CI | GitHub Actions on Python 3.11 and 3.12: ruff + pytest |

## Run

```bash
python -m ledger.api        # demo server on http://127.0.0.1:8080 with two demo accounts .You should see Listening on http://127.0.0.1:8080. Leave that terminal open, because the server runs there.
pip install -e ".[dev]" && pytest -q     # 43 tests
```

```bash
curl -X POST localhost:8080/transfers -H "Content-Type: application/json" \
     -H "Idempotency-Key: demo-00000001" \
     -d '{"source":"CHQ-000001","destination":"SAV-000002","amount":"25.00"}'
``` # Test it from a second terminal

On Windows PowerShell, `curl` is an alias for `Invoke-WebRequest`, so use:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/transfers -ContentType "application/json" `
  -Headers @{ "Idempotency-Key" = "demo-00000001" } `
  -Body '{"source":"CHQ-000001","destination":"SAV-000002","amount":"25.00"}'
```

## How it was built (TDD)

Domain rules (`domain.py`) were written test-first, then the ledger (`store.py`), then the HTTP
layer (`api.py`). Layers are separated so business logic is tested without a network.

## Honest limits

- Demo scope: no authentication/authorisation, no currency conversion, single-node SQLite.
- The global lock is simple and correct but limits write throughput; a production system would use
  a server database with row-level locking.
- Not reviewed by a security team; it shows habits, not a compliance-ready system.

## Next steps

1. Port to Java 21 + Spring Boot with JUnit 5 (same tests), containerise with Docker.
2. PostgreSQL with `SELECT ... FOR UPDATE` and migrations.
3. OAuth2 client-credentials auth and rate limiting.
4. OpenTelemetry traces and a load test.
