import json
import threading
import urllib.error
import urllib.request

import pytest

from ledger.api import serve
from ledger.store import Ledger


@pytest.fixture(scope="module")
def base_url():
    lg = Ledger()
    lg.open_account("CHQ-000001", "Alice", 100_00)
    lg.open_account("SAV-000002", "Bob", 0)
    server = serve(lg, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read()), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read()), err.headers


TRANSFER = {"source": "CHQ-000001", "destination": "SAV-000002", "amount": "10.00"}


def test_health(base_url):
    assert call(base_url, "GET", "/health")[0] == 200


def test_missing_idempotency_key_is_400(base_url):
    status, body, _ = call(base_url, "POST", "/transfers", TRANSFER)
    assert status == 400 and "Idempotency-Key" in body["problems"][0]


def test_create_then_replay_then_fetch(base_url):
    h = {"Idempotency-Key": "api-test-0001"}
    s1, t1, headers = call(base_url, "POST", "/transfers", TRANSFER, h)
    s2, t2, _ = call(base_url, "POST", "/transfers", TRANSFER, h)
    assert (s1, s2) == (201, 200) and t1 == t2
    assert headers["X-Request-Id"]
    assert call(base_url, "GET", f"/transfers/{t1['id']}")[1]["amount_cents"] == 1000
    assert call(base_url, "GET", "/accounts/CHQ-000001/balance")[1]["balance"] == "90.00"


def test_rule_violation_is_422(base_url):
    body = {**TRANSFER, "amount": "5000.00"}
    assert call(base_url, "POST", "/transfers", body, {"Idempotency-Key": "api-test-0002"})[0] == 422


def test_unknown_route_is_404_and_errors_do_not_leak(base_url):
    status, body, _ = call(base_url, "GET", "/admin")
    assert status == 404 and "Traceback" not in json.dumps(body)


@pytest.mark.parametrize("amount", ["NaN", "Infinity"])
def test_non_finite_amount_is_400_not_500(base_url, amount):
    body = {**TRANSFER, "amount": amount}
    assert call(base_url, "POST", "/transfers", body, {"Idempotency-Key": "api-test-nan-" + amount})[0] == 400


@pytest.mark.parametrize("length", ["abc", "-1"])
def test_bad_content_length_is_400(base_url, length):
    import http.client
    host, port = base_url.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/transfers")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Idempotency-Key", "api-test-len-0001")
    conn.putheader("Content-Length", length)
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_query_string_is_ignored_for_routing(base_url):
    assert call(base_url, "GET", "/health?probe=1")[0] == 200


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_unsupported_method_is_json_405(base_url, method):
    status, body, _ = call(base_url, method, "/transfers")
    assert status == 405 and body["error"] == "method_not_allowed"
