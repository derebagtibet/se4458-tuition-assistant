"""Database engine/session and ORM models (see docs/ER.md)."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone

from sqlalchemy import (BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from app.core.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "students"
    student_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tuitions: Mapped[list["Tuition"]] = relationship(back_populates="student")


class Tuition(Base):
    """One tuition assessment per (student, term). Money is stored as integer cents."""
    __tablename__ = "tuitions"
    __table_args__ = (
        UniqueConstraint("student_no", "term", name="uq_tuition_student_term"),
        CheckConstraint("amount_cents >= 0", name="ck_tuition_amount_nonneg"),
        CheckConstraint("paid_cents >= 0 AND paid_cents <= amount_cents", name="ck_tuition_paid_range"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(ForeignKey("students.student_no"), index=True)
    term: Mapped[str] = mapped_column(String(20), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    paid_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    student: Mapped[Student] = relationship(back_populates="tuitions")
    payments: Mapped[list["Payment"]] = relationship(back_populates="tuition")

    @property
    def balance_cents(self) -> int:
        return self.amount_cents - self.paid_cents


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_payment_positive"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Idempotency key supplied by the caller; UNIQUE => duplicates rejected even under races.
    transaction_id: Mapped[str] = mapped_column(String(64), unique=True)
    tuition_id: Mapped[int] = mapped_column(ForeignKey("tuitions.id"), index=True)
    student_no: Mapped[str] = mapped_column(String(20), index=True)
    term: Mapped[str] = mapped_column(String(20))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(20), default="BANKING")
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tuition: Mapped[Tuition] = relationship(back_populates="payments")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))  # student | bank | admin
    student_no: Mapped[str | None] = mapped_column(ForeignKey("students.student_no"), nullable=True)


class MobileQueryQuota(Base):
    """Per-student daily call counter for the unauthenticated mobile Query Tuition API."""
    __tablename__ = "mobile_query_quota"
    student_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
