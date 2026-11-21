# ER / Data Model — Tuition database (PostgreSQL 16)

```mermaid
erDiagram
    STUDENTS ||--o{ TUITIONS : "is assessed"
    TUITIONS ||--o{ PAYMENTS : "is paid by"
    STUDENTS |o--o{ USERS : "login of (role=student)"
    STUDENTS ||--o{ MOBILE_QUERY_QUOTA : "daily mobile calls (logical)"

    STUDENTS {
        varchar(20) student_no PK
        varchar(120) full_name
        timestamptz created_at
    }
    TUITIONS {
        int id PK
        varchar(20) student_no FK
        varchar(20) term "e.g. 2026-FALL"
        bigint amount_cents "assessed tuition, >= 0"
        bigint paid_cents "0 <= paid <= amount"
        char(3) currency
        timestamptz created_at
        timestamptz updated_at
    }
    PAYMENTS {
        int id PK
        varchar(64) transaction_id UK "idempotency key"
        int tuition_id FK
        varchar(20) student_no
        varchar(20) term
        bigint amount_cents "> 0"
        varchar(20) channel "BANKING | SEED"
        varchar(60) created_by "username from JWT"
        timestamptz created_at
    }
    USERS {
        int id PK
        varchar(60) username UK
        varchar(200) password_hash "PBKDF2-SHA256"
        varchar(20) role "student | bank | admin"
        varchar(20) student_no FK "nullable"
    }
    MOBILE_QUERY_QUOTA {
        varchar(20) student_no PK
        date day PK "Europe/Istanbul date"
        int count
    }
```

**Constraints / design notes**

| Rule | How it is enforced |
|---|---|
| One tuition per student per term | `UNIQUE (student_no, term)` on `tuitions` |
| Balance never negative | `CHECK (paid_cents <= amount_cents)`; API rejects payments above balance |
| Duplicate payments | `UNIQUE (transaction_id)` on `payments` + pre-check → `409 DUPLICATE_TRANSACTION` |
| Concurrent payments | `SELECT … FOR UPDATE` on the tuition row inside the payment transaction |
| Money precision | Stored as integer cents (`bigint`) → no floating-point rounding |
| Balance | `amount_cents − paid_cents` (denormalised `paid_cents` is updated in the same transaction as the payment insert) |
| Mobile 3/day rule | `mobile_query_quota (student_no, day)` counter (defence in depth behind the gateway's per-student counter) |
