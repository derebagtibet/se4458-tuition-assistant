"""Business logic for tuition queries, payments and administration (framework-independent)."""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import schemas
from app.core.errors import ApiError
from app.core.security import Principal
from app.db import MobileQueryQuota, Payment, Student, Tuition


def to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_exact())


def to_money(cents: int) -> float:
    return round(cents / 100, 2)


def normalize_student_no(student_no: str) -> str:
    sn = student_no.strip().upper()
    if not re.fullmatch(schemas.STUDENT_NO_PATTERN, sn):
        raise ApiError(422, "VALIDATION_ERROR", "studentNo must be 3-20 alphanumeric characters")
    return sn


def normalize_term(term: str) -> str:
    t = term.strip().upper()
    if not re.fullmatch(schemas.TERM_PATTERN, t):
        raise ApiError(422, "VALIDATION_ERROR", "term must look like 2026-FALL / 2027-SPRING / 2027-SUMMER")
    return t


# ------------------------------------------------------------------ queries
def get_summary(db: Session, student_no: str, term: str | None) -> schemas.TuitionSummary:
    student = db.get(Student, student_no)
    if student is None:
        raise ApiError(404, "STUDENT_NOT_FOUND", f"No tuition record exists for student {student_no}")
    stmt = select(Tuition).where(Tuition.student_no == student_no).order_by(Tuition.term)
    if term:
        stmt = stmt.where(Tuition.term == term)
    rows = list(db.scalars(stmt))
    if term and not rows:
        raise ApiError(404, "TERM_NOT_FOUND", f"No tuition assessed for {student_no} in {term}")
    currency = rows[0].currency if rows else "USD"
    terms = [schemas.TermBalance(term=r.term, currency=r.currency, tuition_total=to_money(r.amount_cents),
                                 paid_amount=to_money(r.paid_cents), balance=to_money(r.balance_cents))
             for r in rows]
    return schemas.TuitionSummary(
        student_no=student_no,
        scope="TERM" if term else "OVERALL",
        term=term,
        currency=currency,
        tuition_total=to_money(sum(r.amount_cents for r in rows)),
        paid_amount=to_money(sum(r.paid_cents for r in rows)),
        balance=to_money(sum(r.balance_cents for r in rows)),
        terms=terms,
    )


def consume_mobile_quota(db: Session, student_no: str, limit: int, now: datetime) -> int:
    """Count one mobile query for this student today. Keyed by student number, not client IP.
    Returns remaining calls; raises 429 when the daily limit is already used up."""
    today: date = now.date()
    for _ in range(2):  # one retry covers a concurrent first-insert race
        row = db.get(MobileQueryQuota, (student_no, today), with_for_update=True)
        if row is None:
            row = MobileQueryQuota(student_no=student_no, day=today, count=0)
            db.add(row)
        if row.count >= limit:
            db.rollback()
            midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)
            retry_after = max(1, int((midnight - now).total_seconds()))
            raise ApiError(429, "DAILY_LIMIT_EXCEEDED",
                           f"Query Tuition is limited to {limit} calls per student per day",
                           details={"studentNo": student_no, "limit": limit, "day": today.isoformat()},
                           headers={"Retry-After": str(retry_after)})
        row.count += 1
        try:
            db.commit()
            return limit - row.count
        except IntegrityError:
            db.rollback()
    raise ApiError(409, "CONFLICT", "Concurrent quota update, please retry")


# ------------------------------------------------------------------ payments
def record_payment(db: Session, req: schemas.PaymentRequest, principal: Principal,
                   channel: str = "BANKING") -> schemas.PaymentResponse:
    existing = db.scalar(select(Payment).where(Payment.transaction_id == req.transaction_id))
    if existing is not None:
        raise ApiError(409, "DUPLICATE_TRANSACTION",
                       f"Transaction {req.transaction_id} was already processed; no new payment recorded")

    tuition = db.scalar(select(Tuition).where(Tuition.student_no == req.student_no, Tuition.term == req.term)
                        .with_for_update())
    if tuition is None:
        raise ApiError(404, "TERM_NOT_FOUND", f"No tuition assessed for {req.student_no} in {req.term}")

    amount_cents = to_cents(req.amount)
    if amount_cents > tuition.balance_cents:
        db.rollback()
        raise ApiError(422, "AMOUNT_EXCEEDS_BALANCE",
                       f"Payment {to_money(amount_cents):.2f} exceeds outstanding balance "
                       f"{to_money(tuition.balance_cents):.2f}",
                       details={"balance": to_money(tuition.balance_cents)})

    tuition.paid_cents += amount_cents
    db.add(Payment(transaction_id=req.transaction_id, tuition_id=tuition.id, student_no=req.student_no,
                   term=req.term, amount_cents=amount_cents, channel=channel, created_by=principal.username))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "DUPLICATE_TRANSACTION", f"Transaction {req.transaction_id} was already processed")
    return schemas.PaymentResponse(
        transaction_id=req.transaction_id, student_no=req.student_no, term=req.term, currency=tuition.currency,
        paid_amount=to_money(amount_cents), total_paid=to_money(tuition.paid_cents),
        remaining_balance=to_money(tuition.balance_cents))


# ------------------------------------------------------------------ admin
def upsert_tuition(db: Session, student_no: str, term: str, amount_cents: int, currency: str) -> str:
    """Create-or-update rule for (student, term):
    * no row          -> CREATED (student auto-created if new)
    * same amount     -> UNCHANGED (idempotent re-submission)
    * different amount-> UPDATED, unless new amount < already-paid amount (409 AMOUNT_BELOW_PAID)
    * a student keeps a single currency (409 CURRENCY_MISMATCH otherwise).
    Does not commit; caller owns the transaction."""
    if db.get(Student, student_no) is None:
        db.add(Student(student_no=student_no))
        db.flush()
    other_currency = db.scalar(select(Tuition.currency).where(Tuition.student_no == student_no,
                                                               Tuition.currency != currency).limit(1))
    if other_currency:
        raise ApiError(409, "CURRENCY_MISMATCH",
                       f"Student {student_no} is billed in {other_currency}; got {currency}")
    row = db.scalar(select(Tuition).where(Tuition.student_no == student_no, Tuition.term == term)
                    .with_for_update())
    if row is None:
        db.add(Tuition(student_no=student_no, term=term, amount_cents=amount_cents, paid_cents=0,
                       currency=currency))
        db.flush()
        return "CREATED"
    if row.amount_cents == amount_cents:
        return "UNCHANGED"
    if amount_cents < row.paid_cents:
        raise ApiError(409, "AMOUNT_BELOW_PAID",
                       f"New amount {to_money(amount_cents):.2f} is below the amount already paid "
                       f"{to_money(row.paid_cents):.2f}")
    row.amount_cents = amount_cents
    db.flush()
    return "UPDATED"


def add_tuition(db: Session, req: schemas.AddTuitionRequest) -> schemas.AddTuitionResponse:
    try:
        status = upsert_tuition(db, req.student_no, req.term, to_cents(req.amount), req.currency)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return schemas.AddTuitionResponse(status=status, student_no=req.student_no, term=req.term,
                                      amount=to_money(to_cents(req.amount)), currency=req.currency)


REQUIRED_COLUMNS = ["studentNo", "term", "amount", "currency"]


def process_batch(db: Session, content: bytes) -> schemas.BatchResponse:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError(400, "INVALID_CSV", "CSV must be UTF-8 encoded")
    reader = csv.DictReader(io.StringIO(text), strict=True)
    try:
        header = reader.fieldnames or []
        rows = list(reader)  # reject CSV syntax errors before changing any records
    except csv.Error:
        raise ApiError(400, "INVALID_CSV", "CSV contains malformed quoting or an oversized field")
    if header != REQUIRED_COLUMNS:
        raise ApiError(400, "INVALID_CSV", f"CSV header must be exactly: {','.join(REQUIRED_COLUMNS)}",
                       details={"found": header})
    results: list[schemas.BatchRowResult] = []
    for idx, raw in enumerate(rows, start=2):  # line 1 is the header
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue  # skip blank lines
        sn, term = row.get("studentNo") or None, row.get("term") or None
        try:
            if None in raw or any(v is None for v in raw.values()):
                raise ApiError(422, "VALIDATION_ERROR", "Each CSV row must contain exactly four columns")
            try:
                req = schemas.AddTuitionRequest.model_validate(row)
            except ValidationError as exc:
                message = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                raise ApiError(422, "VALIDATION_ERROR", message)
            sn, term = req.student_no, req.term
            with db.begin_nested():  # savepoint: a bad row never rolls back good rows
                status = upsert_tuition(db, sn, term, to_cents(req.amount), req.currency)
            results.append(schemas.BatchRowResult(line=idx, student_no=sn, term=term, status=status))
        except ApiError as e:
            results.append(schemas.BatchRowResult(line=idx, student_no=sn, term=term, status="FAILED",
                                                  error=f"{e.code}: {e.message}"))
    db.commit()
    failures = sum(1 for r in results if r.status == "FAILED")
    return schemas.BatchResponse(total_rows=len(results), success_count=len(results) - failures,
                                 failure_count=failures, results=results)


def unpaid_page(db: Session, term: str, page: int, page_size: int) -> schemas.UnpaidPage:
    base = select(Tuition).where(Tuition.term == term, Tuition.amount_cents > Tuition.paid_cents)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by((Tuition.amount_cents - Tuition.paid_cents).desc(), Tuition.student_no)
                      .offset((page - 1) * page_size).limit(page_size))
    items = [schemas.UnpaidItem(student_no=r.student_no, term=r.term, currency=r.currency,
                                tuition_total=to_money(r.amount_cents), paid_amount=to_money(r.paid_cents),
                                balance=to_money(r.balance_cents)) for r in rows]
    return schemas.UnpaidPage(term=term, page=page, page_size=page_size, total_items=total,
                              total_pages=math.ceil(total / page_size) if total else 0, items=items)
