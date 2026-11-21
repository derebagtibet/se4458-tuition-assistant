"""Version 1 of the Tuition REST API. Mounted under /api/v1 (see app/main.py).

A future breaking change is added as app/api/v2 and mounted under /api/v2 side by side, so
existing mobile / banking clients keep working (URI versioning)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import (Principal, create_access_token, ensure_can_access_student, require_roles,
                               verify_password)
from app.db import User, get_db
from app.services import tuition_service as svc

E = {"model": schemas.ErrorResponse}
StudentNoPath = Path(..., pattern=schemas.STUDENT_NO_PATTERN, examples=["S1001"])
TermQuery = Query(None, pattern=r"^\d{4}-(FALL|SPRING|SUMMER)$",
                  description="Optional. If given, totals are for this term only (scope=TERM); "
                              "otherwise totals are the overall balance across all terms (scope=OVERALL).")

router = APIRouter()


def _now() -> datetime:
    return datetime.now(settings.tz)


# ------------------------------------------------------------------ auth
@router.post("/auth/token", tags=["Auth"], response_model=schemas.TokenResponse, response_model_by_alias=True,
             responses={401: E}, summary="Log in and obtain a JWT")
def issue_token(body: schemas.TokenRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "Username or password is incorrect")
    token, ttl = create_access_token(user.username, user.role, user.student_no)
    return schemas.TokenResponse(access_token=token, expires_in=ttl, role=user.role, student_no=user.student_no)


# ------------------------------------------------------------------ University Mobile App
@router.get("/mobile/tuition/{student_no}", tags=["University Mobile App"],
            response_model=schemas.TuitionSummary, response_model_by_alias=True,
            responses={404: E, 422: E, 429: E},
            summary="Query Tuition (no auth, max 3 calls per student per day)")
def mobile_query_tuition(response: Response, student_no: str = StudentNoPath, term: str | None = TermQuery,
                         db: Session = Depends(get_db)):
    sn = svc.normalize_student_no(student_no)
    if settings.enforce_mobile_quota:
        remaining = svc.consume_mobile_quota(db, sn, settings.mobile_daily_limit, _now())
        response.headers["X-Student-Quota-Remaining"] = str(remaining)
    return svc.get_summary(db, sn, term.upper() if term else None)


# ------------------------------------------------------------------ Banking App
@router.get("/banking/tuition/{student_no}", tags=["Banking App"], response_model=schemas.TuitionSummary,
            response_model_by_alias=True, responses={401: E, 403: E, 404: E},
            summary="Query Tuition (authenticated)")
def banking_query_tuition(student_no: str = StudentNoPath, term: str | None = TermQuery,
                          principal: Principal = Depends(require_roles("student", "bank", "admin")),
                          db: Session = Depends(get_db)):
    sn = svc.normalize_student_no(student_no)
    ensure_can_access_student(principal, sn)
    return svc.get_summary(db, sn, term.upper() if term else None)


@router.post("/banking/payments", tags=["Banking App"], response_model=schemas.PaymentResponse,
             response_model_by_alias=True, status_code=status.HTTP_201_CREATED,
             responses={401: E, 403: E, 404: E, 409: E, 422: E},
             summary="Pay Tuition (simulated partial/full payment, idempotent by transactionId)")
def pay_tuition(body: schemas.PaymentRequest,
                principal: Principal = Depends(require_roles("student", "bank")),
                db: Session = Depends(get_db)):
    ensure_can_access_student(principal, body.student_no)
    return svc.record_payment(db, body, principal)


# ------------------------------------------------------------------ University Web Site - Admin
@router.post("/admin/tuition", tags=["Admin"], response_model=schemas.AddTuitionResponse,
             response_model_by_alias=True, responses={401: E, 403: E, 409: E, 422: E},
             summary="Add Tuition (create or update the amount for a student and term)")
def admin_add_tuition(body: schemas.AddTuitionRequest, response: Response,
                      _: Principal = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    result = svc.add_tuition(db, body)
    response.status_code = 201 if result.status == "CREATED" else 200
    return result


@router.post("/admin/tuition/batch", tags=["Admin"], response_model=schemas.BatchResponse,
             response_model_by_alias=True, responses={400: E, 401: E, 403: E, 413: E},
             summary="Add Tuition - Batch (CSV: studentNo,term,amount,currency)")
async def admin_add_tuition_batch(file: UploadFile = File(..., description="UTF-8 CSV file"),
                                  _: Principal = Depends(require_roles("admin")),
                                  db: Session = Depends(get_db)):
    content = await file.read(settings.max_batch_bytes + 1)
    if len(content) > settings.max_batch_bytes:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", f"CSV larger than {settings.max_batch_bytes} bytes")
    return svc.process_batch(db, content)


@router.get("/admin/tuition/unpaid", tags=["Admin"], response_model=schemas.UnpaidPage,
            response_model_by_alias=True, responses={401: E, 403: E, 422: E},
            summary="Unpaid Tuition Status (paged)")
def admin_unpaid(term: str = Query(..., pattern=r"^\d{4}-(FALL|SPRING|SUMMER)$", examples=["2026-FALL"]),
                 page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100, alias="pageSize"),
                 _: Principal = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    return svc.unpaid_page(db, term.upper(), page, page_size)
