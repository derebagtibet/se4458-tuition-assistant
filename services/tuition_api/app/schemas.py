"""Request/response contracts (these drive the OpenAPI / Swagger document)."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

TERM_PATTERN = r"^\d{4}-(FALL|SPRING|SUMMER)$"
STUDENT_NO_PATTERN = r"^[A-Za-z0-9]{3,20}$"

Term = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=TERM_PATTERN)]
StudentNo = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=STUDENT_NO_PATTERN)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")]
Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# ---------- auth ----------
class TokenRequest(CamelModel):
    username: str = Field(min_length=1, max_length=60, examples=["s1001"])
    password: str = Field(min_length=1, max_length=200, examples=["Passw0rd!"])


class TokenResponse(CamelModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: str
    student_no: str | None = Field(default=None, serialization_alias="studentNo")


# ---------- tuition query ----------
class TermBalance(CamelModel):
    term: str
    currency: str
    tuition_total: float = Field(serialization_alias="tuitionTotal")
    paid_amount: float = Field(serialization_alias="paidAmount")
    balance: float


class TuitionSummary(CamelModel):
    """Contract: if `term` is supplied the scope is TERM and totals are for that term only;
    otherwise scope is OVERALL and totals are summed across all terms (per-term rows in `terms`)."""
    student_no: str = Field(serialization_alias="studentNo")
    scope: Literal["TERM", "OVERALL"]
    term: str | None
    currency: str
    tuition_total: float = Field(serialization_alias="tuitionTotal")
    paid_amount: float = Field(serialization_alias="paidAmount")
    balance: float
    terms: list[TermBalance]


# ---------- payment ----------
class PaymentRequest(CamelModel):
    student_no: StudentNo = Field(alias="studentNo", examples=["S1001"])
    term: Term = Field(examples=["2026-FALL"])
    amount: Money = Field(examples=["500.00"])
    transaction_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=64,
                                                     pattern=r"^[A-Za-z0-9._:-]+$")] = Field(
        alias="transactionId", examples=["bank-7f3c2a10"])

    @field_validator("amount")
    @classmethod
    def _positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be greater than 0")
        return v


class PaymentResponse(CamelModel):
    status: Literal["SUCCESS"] = "SUCCESS"
    transaction_id: str = Field(serialization_alias="transactionId")
    student_no: str = Field(serialization_alias="studentNo")
    term: str
    currency: str
    paid_amount: float = Field(serialization_alias="paidAmount", description="Amount recorded by this payment")
    total_paid: float = Field(serialization_alias="totalPaid", description="Total paid for the term so far")
    remaining_balance: float = Field(serialization_alias="remainingBalance")


# ---------- admin ----------
class AddTuitionRequest(CamelModel):
    student_no: StudentNo = Field(alias="studentNo", examples=["S1006"])
    term: Term = Field(examples=["2027-SPRING"])
    amount: Money = Field(examples=["4000.00"])
    currency: Currency = Field(examples=["USD"])

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("amount must be >= 0")
        return v


class AddTuitionResponse(CamelModel):
    status: Literal["CREATED", "UPDATED", "UNCHANGED"]
    student_no: str = Field(serialization_alias="studentNo")
    term: str
    amount: float
    currency: str


class BatchRowResult(CamelModel):
    line: int
    student_no: str | None = Field(default=None, serialization_alias="studentNo")
    term: str | None = None
    status: Literal["CREATED", "UPDATED", "UNCHANGED", "FAILED"]
    error: str | None = None


class BatchResponse(CamelModel):
    status: Literal["COMPLETED"] = "COMPLETED"
    total_rows: int = Field(serialization_alias="totalRows")
    success_count: int = Field(serialization_alias="successCount")
    failure_count: int = Field(serialization_alias="failureCount")
    results: list[BatchRowResult]


class UnpaidItem(CamelModel):
    student_no: str = Field(serialization_alias="studentNo")
    term: str
    currency: str
    tuition_total: float = Field(serialization_alias="tuitionTotal")
    paid_amount: float = Field(serialization_alias="paidAmount")
    balance: float


class UnpaidPage(CamelModel):
    term: str
    page: int
    page_size: int = Field(serialization_alias="pageSize")
    total_items: int = Field(serialization_alias="totalItems")
    total_pages: int = Field(serialization_alias="totalPages")
    items: list[UnpaidItem]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
