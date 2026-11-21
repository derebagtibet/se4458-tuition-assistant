"""Verify real Kong routes and save evidence; creates an isolated demo tuition record.

Run with the stack up: python scripts/gateway_demo.py
Uses seeded demo accounts, a fresh student number and simulated payments only.
The direct API payment check is NOT evidence of agent/user payment confirmation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv(ROOT / ".env")
    url = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
    log_path = ROOT / os.getenv("EVIDENCE_LOG", "docs/evidence/gateway-demo.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log, httpx.Client(base_url=url, timeout=120) as client:
        def emit(text: str) -> None:
            print(text, flush=True)
            log.write(text + "\n")
            log.flush()

        def check(label: str, response: httpx.Response, expected: int, *, body: bool = True) -> dict:
            emit(f"{label}: {response.request.method} {response.request.url} -> {response.status_code}")
            for name in ("server", "x-kong-proxy-latency", "x-request-id", "retry-after",
                         "x-ratelimit-remaining-student-day"):
                if name in response.headers:
                    emit(f"  {name}: {response.headers[name]}")
            data = response.json() if "json" in response.headers.get("content-type", "") else {}
            if body:
                emit(json.dumps(data, ensure_ascii=True))
            assert response.status_code == expected, f"{label}: expected {expected}, got {response.status_code}"
            return data

        def login(username: str, password: str) -> dict[str, str]:
            response = client.post("/api/v1/auth/token", json={"username": username, "password": password})
            # Tokens are deliberately excluded from the saved evidence.
            data = check(f"Login {username}", response, 200, body=False)
            return {"Authorization": f"Bearer {data['access_token']}"}

        emit(f"Run: {datetime.now(timezone.utc).isoformat()}\nGateway: {url}")
        check("Swagger", client.get("/api/docs"), 200, body=False)
        check("OpenAPI", client.get("/api/openapi.json"), 200, body=False)
        check("Protected route without JWT", client.get("/api/v1/banking/tuition/S1001"), 401)
        student = login("s1001", "Passw0rd!")
        check("Protected route with JWT", client.get("/api/v1/banking/tuition/S1001", headers=student), 200)
        check("Another student's account", client.get("/api/v1/banking/tuition/S1002", headers=student), 403)
        admin = login("admin", "Admin123!")
        bank = login("bankapp", "Bank123!")
        sn = "DEMO" + uuid.uuid4().hex[:10].upper()
        check("Create isolated demo tuition", client.post("/api/v1/admin/tuition", headers=admin,
              json={"studentNo": sn, "term": "2026-FALL", "amount": "20.00", "currency": "USD"}), 201)
        for call in range(1, 5):
            response = client.get(f"/api/v1/mobile/tuition/{sn}")
            result = check(f"Student daily quota call {call}", response, 200 if call <= 3 else 429)
            if call == 4:
                assert result["error"]["source"] == "api-gateway"
        other = "CHECK" + uuid.uuid4().hex[:10].upper()
        check("Different student from same IP has independent quota",
              client.get(f"/api/v1/mobile/tuition/{other}"), 404)
        payment = {"studentNo": sn, "term": "2026-FALL", "amount": "5.00",
                   "transactionId": "gateway-demo-" + uuid.uuid4().hex}
        result = check("Direct simulated payment (not agent confirmation)",
                       client.post("/api/v1/banking/payments", headers=bank, json=payment), 201)
        assert result["remainingBalance"] == 15
        check("Duplicate transaction", client.post("/api/v1/banking/payments", headers=bank, json=payment), 409)
        csv = f"studentNo,term,amount,currency\n{sn},2027-SPRING,10,USD\n{sn},2027-SUMMER,NaN,USD\n"
        result = check("Batch valid and invalid row", client.post("/api/v1/admin/tuition/batch", headers=admin,
                       files={"file": ("demo.csv", csv.encode(), "text/csv")}), 200)
        assert (result["successCount"], result["failureCount"]) == (1, 1)
        check("Paged unpaid tuition", client.get("/api/v1/admin/tuition/unpaid", headers=admin,
              params={"term": "2026-FALL", "page": 1, "pageSize": 2}), 200)
        emit("Overall result: PASSED")


if __name__ == "__main__":
    main()
