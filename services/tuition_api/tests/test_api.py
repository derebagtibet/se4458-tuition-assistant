"""Unit tests: every REST operation has at least one success and one failure/edge-case test."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings


# ------------------------------------------------------------------ Auth
def test_login_success_returns_jwt_with_role(client):
    r = client.post("/api/v1/auth/token", json={"username": "s1001", "password": "Passw0rd!"})
    assert r.status_code == 200
    body = r.json()
    claims = jwt.decode(body["access_token"], settings.jwt_secret, algorithms=["HS256"],
                        issuer=settings.jwt_issuer)
    assert claims["role"] == "student" and claims["studentNo"] == "S1001"
    assert body["studentNo"] == "S1001"


def test_login_wrong_password_401(client):
    r = client.post("/api/v1/auth/token", json={"username": "s1001", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


# ------------------------------------------------------------------ Mobile Query Tuition
def test_mobile_query_overall_balance(client):
    r = client.get("/api/v1/mobile/tuition/S1001")
    assert r.status_code == 200
    b = r.json()
    assert b["scope"] == "OVERALL" and b["tuitionTotal"] == 6000.0 and b["balance"] == 6000.0
    assert {t["term"] for t in b["terms"]} == {"2026-FALL", "2027-SPRING"}
    assert r.headers["X-Student-Quota-Remaining"] == "2"


def test_mobile_query_term_scope_and_case_insensitive_student(client):
    r = client.get("/api/v1/mobile/tuition/s1002", params={"term": "2026-FALL"})
    assert r.status_code == 200
    b = r.json()
    assert b["scope"] == "TERM" and b["studentNo"] == "S1002"
    assert (b["tuitionTotal"], b["paidAmount"], b["balance"]) == (4500.0, 1500.0, 3000.0)


def test_mobile_query_limit_three_per_student_per_day(client):
    for _ in range(3):
        assert client.get("/api/v1/mobile/tuition/S1003").status_code == 200
    r = client.get("/api/v1/mobile/tuition/S1003")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "DAILY_LIMIT_EXCEEDED"
    assert int(r.headers["Retry-After"]) > 0
    # keyed by student, not by caller: another student is still allowed from the same client
    assert client.get("/api/v1/mobile/tuition/S1004").status_code == 200


def test_mobile_query_unknown_student_404(client):
    r = client.get("/api/v1/mobile/tuition/S9999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "STUDENT_NOT_FOUND"


def test_mobile_query_invalid_term_422(client):
    r = client.get("/api/v1/mobile/tuition/S1001", params={"term": "fall"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ Banking Query Tuition
def test_banking_query_own_account(client, student_headers):
    r = client.get("/api/v1/banking/tuition/S1001", params={"term": "2026-FALL"}, headers=student_headers)
    assert r.status_code == 200 and r.json()["balance"] == 3000.0


def test_banking_query_requires_auth(client):
    r = client.get("/api/v1/banking/tuition/S1001")
    assert r.status_code == 401


def test_banking_query_other_students_account_forbidden(client, student_headers):
    # student s1001 must not read S1002 even if the number is supplied (prompt-injection guard)
    r = client.get("/api/v1/banking/tuition/S1002", headers=student_headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_banking_query_expired_token_401(client):
    now = datetime.now(timezone.utc)
    tok = jwt.encode({"iss": settings.jwt_issuer, "sub": "s1001", "role": "student", "studentNo": "S1001",
                      "iat": int((now - timedelta(hours=2)).timestamp()),
                      "exp": int((now - timedelta(hours=1)).timestamp())}, settings.jwt_secret, algorithm="HS256")
    r = client.get("/api/v1/banking/tuition/S1001", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_banking_query_bank_role_any_student(client, bank_headers):
    r = client.get("/api/v1/banking/tuition/S1005", headers=bank_headers)
    assert r.status_code == 200 and r.json()["balance"] == 2750.0


# ------------------------------------------------------------------ Pay Tuition
def _pay(client, headers, amount="500.00", tx="tx-000001", sn="S1001", term="2026-FALL"):
    return client.post("/api/v1/banking/payments", headers=headers,
                       json={"studentNo": sn, "term": term, "amount": amount, "transactionId": tx})


def test_pay_partial_then_full(client, student_headers):
    r = _pay(client, student_headers, "1000.00", "tx-partial-1")
    assert r.status_code == 201
    assert r.json()["remainingBalance"] == 2000.0 and r.json()["paidAmount"] == 1000.0
    r = _pay(client, student_headers, "2000.00", "tx-full-2")
    assert r.status_code == 201 and r.json()["remainingBalance"] == 0.0 and r.json()["totalPaid"] == 3000.0


def test_pay_duplicate_transaction_rejected_and_balance_preserved(client, student_headers):
    assert _pay(client, student_headers, "500.00", "tx-dup-1").status_code == 201
    r = _pay(client, student_headers, "500.00", "tx-dup-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    bal = client.get("/api/v1/banking/tuition/S1001", params={"term": "2026-FALL"}, headers=student_headers)
    assert bal.json()["balance"] == 2500.0  # only charged once


def test_pay_amount_exceeding_balance_rejected(client, student_headers):
    r = _pay(client, student_headers, "3000.01", "tx-over-1")
    assert r.status_code == 422 and r.json()["error"]["code"] == "AMOUNT_EXCEEDS_BALANCE"


def test_pay_non_positive_amount_rejected(client, student_headers):
    r = _pay(client, student_headers, "0", "tx-zero-1")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_pay_more_than_two_decimals_rejected(client, student_headers):
    r = _pay(client, student_headers, "10.001", "tx-dec-1")
    assert r.status_code == 422


def test_pay_for_another_student_forbidden(client, student_headers):
    r = _pay(client, student_headers, "100.00", "tx-other-1", sn="S1002")
    assert r.status_code == 403


def test_pay_unknown_term_404(client, student_headers):
    r = _pay(client, student_headers, "100.00", "tx-term-1", term="2030-FALL")
    assert r.status_code == 404 and r.json()["error"]["code"] == "TERM_NOT_FOUND"


def test_admin_cannot_pay(client, admin_headers):
    assert _pay(client, admin_headers, "100.00", "tx-admin-1").status_code == 403


# ------------------------------------------------------------------ Add Tuition
def test_add_tuition_create_update_unchanged(client, admin_headers):
    body = {"studentNo": "S2001", "term": "2027-SPRING", "amount": "4000.00", "currency": "usd"}
    r = client.post("/api/v1/admin/tuition", json=body, headers=admin_headers)
    assert r.status_code == 201 and r.json()["status"] == "CREATED" and r.json()["currency"] == "USD"
    r = client.post("/api/v1/admin/tuition", json=body, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "UNCHANGED"
    r = client.post("/api/v1/admin/tuition", json={**body, "amount": "4200.00"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "UPDATED" and r.json()["amount"] == 4200.0


def test_add_tuition_below_paid_amount_conflict(client, admin_headers):
    body = {"studentNo": "S1002", "term": "2026-FALL", "amount": "1000.00", "currency": "USD"}
    r = client.post("/api/v1/admin/tuition", json=body, headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "AMOUNT_BELOW_PAID"


def test_add_tuition_currency_mismatch_conflict(client, admin_headers):
    body = {"studentNo": "S1001", "term": "2027-SUMMER", "amount": "1000.00", "currency": "EUR"}
    r = client.post("/api/v1/admin/tuition", json=body, headers=admin_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CURRENCY_MISMATCH"


def test_add_tuition_requires_admin(client, student_headers):
    body = {"studentNo": "S1001", "term": "2027-SUMMER", "amount": "1000.00", "currency": "USD"}
    assert client.post("/api/v1/admin/tuition", json=body, headers=student_headers).status_code == 403


def test_add_tuition_negative_amount_422(client, admin_headers):
    body = {"studentNo": "S1001", "term": "2027-SUMMER", "amount": "-5", "currency": "USD"}
    assert client.post("/api/v1/admin/tuition", json=body, headers=admin_headers).status_code == 422


# ------------------------------------------------------------------ Add Tuition - Batch
def _upload(client, headers, text: str):
    return client.post("/api/v1/admin/tuition/batch", headers=headers,
                       files={"file": ("batch.csv", text.encode(), "text/csv")})


def test_batch_reports_success_and_failure_counts(client, admin_headers):
    csv_text = ("studentNo,term,amount,currency\n"
                "S3001,2026-FALL,3500.00,USD\n"
                "S3002,2026-FALL,abc,USD\n"          # bad amount
                "S3003,fall-2026,1000,USD\n"         # bad term
                "S1002,2026-FALL,100.00,USD\n"       # below paid -> conflict
                "S3004,2026-FALL,1200.50,usd\n"
                "\n")
    r = _upload(client, admin_headers, csv_text)
    assert r.status_code == 200
    b = r.json()
    assert (b["totalRows"], b["successCount"], b["failureCount"]) == (5, 2, 3)
    failed_lines = {x["line"] for x in b["results"] if x["status"] == "FAILED"}
    assert failed_lines == {3, 4, 5}
    # good rows persisted despite failures in between
    unpaid = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL", "pageSize": 100},
                        headers=admin_headers).json()
    assert {"S3001", "S3004"} <= {i["studentNo"] for i in unpaid["items"]}


def test_batch_rejects_wrong_header(client, admin_headers):
    r = _upload(client, admin_headers, "student,term,amount\nS1,2026-FALL,1\n")
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CSV"


def test_batch_requires_auth(client):
    assert _upload(client, {}, "studentNo,term,amount,currency\n").status_code == 401


@pytest.mark.parametrize("bad_row", [
    "S3002,2026-FALL,NaN,USD",
    "S3002,2026-FALL,sNaN,USD",
    "S3002,2026-FALL,Infinity,USD",
    "S3002,2026-FALL,-Infinity,USD",
    "S3002,2026-FALL,10000000000.00,USD",
    "S3002,2026-FALL,10.001,USD",
    "S3002,2026-FALL,100,USD,unexpected",
    "S3002,2026-FALL,100",
])
def test_batch_invalid_row_does_not_crash_or_discard_valid_rows(client, admin_headers, bad_row):
    r = _upload(client, admin_headers,
                f"studentNo,term,amount,currency\nS3001,2026-FALL,25,USD\n{bad_row}\n"
                "S3003,2026-FALL,50,USD\n")
    assert r.status_code == 200
    body = r.json()
    assert (body["successCount"], body["failureCount"]) == (2, 1)
    assert body["results"][1]["status"] == "FAILED"
    for sn, balance in [("S3001", 25), ("S3003", 50)]:
        saved = client.get(f"/api/v1/banking/tuition/{sn}", headers=admin_headers)
        assert saved.status_code == 200 and saved.json()["balance"] == balance


def test_batch_malformed_csv_is_rejected_before_writing(client, admin_headers):
    r = _upload(client, admin_headers, 'studentNo,term,amount,currency\n'
                'S3001,2026-FALL,25,USD\nS3002,2026-FALL,100,"USD\n')
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_CSV"
    assert client.get("/api/v1/banking/tuition/S3001", headers=admin_headers).status_code == 404


def test_duplicate_transaction_does_not_reveal_another_students_payment(client, bank_headers, student_headers):
    assert _pay(client, bank_headers, "123.45", "tx-private-1", sn="S1002").status_code == 201
    r = _pay(client, student_headers, "10", "tx-private-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_TRANSACTION"
    assert "details" not in r.json()["error"]
    assert "S1002" not in r.text and "123.45" not in r.text
    own = client.get("/api/v1/banking/tuition/S1001?term=2026-FALL", headers=student_headers)
    assert own.json()["balance"] == 3000


# ------------------------------------------------------------------ Unpaid Tuition Status
def test_unpaid_paging(client, admin_headers):
    r = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL", "page": 1, "pageSize": 2},
                   headers=admin_headers)
    assert r.status_code == 200
    b = r.json()
    # S1003 is fully paid -> excluded; remaining unpaid: S1001 3000, S1002 3000, S1005 2750, S1004 250
    assert b["totalItems"] == 4 and b["totalPages"] == 2 and len(b["items"]) == 2
    assert [i["balance"] for i in b["items"]] == [3000.0, 3000.0]
    p2 = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL", "page": 2, "pageSize": 2},
                    headers=admin_headers).json()
    assert [i["studentNo"] for i in p2["items"]] == ["S1005", "S1004"]


def test_unpaid_page_out_of_range_is_empty(client, admin_headers):
    r = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL", "page": 9},
                   headers=admin_headers)
    assert r.status_code == 200 and r.json()["items"] == []


def test_unpaid_invalid_page_size_422(client, admin_headers):
    r = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL", "pageSize": 1000},
                   headers=admin_headers)
    assert r.status_code == 422


def test_unpaid_requires_admin(client, student_headers):
    r = client.get("/api/v1/admin/tuition/unpaid", params={"term": "2026-FALL"}, headers=student_headers)
    assert r.status_code == 403
