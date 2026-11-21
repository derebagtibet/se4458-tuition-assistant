"""Demo/test data. Idempotent: running it twice does not duplicate anything."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import Payment, Student, Tuition, User

STUDENTS = [
    ("S1001", "Ada Yilmaz"), ("S1002", "Baris Demir"), ("S1003", "Cem Kaya"),
    ("S1004", "Deniz Acar"), ("S1005", "Ece Sahin"),
]
# (student, term, amount, paid)
TUITIONS = [
    ("S1001", "2026-FALL", 3000_00, 0),
    ("S1001", "2027-SPRING", 3000_00, 0),
    ("S1002", "2026-FALL", 4500_00, 1500_00),
    ("S1003", "2026-FALL", 2000_00, 2000_00),
    ("S1004", "2026-FALL", 250_00, 0),
    ("S1005", "2026-FALL", 3750_00, 1000_00),
]
USERS = [  # username, password, role, student_no
    ("s1001", "Passw0rd!", "student", "S1001"),
    ("s1002", "Passw0rd!", "student", "S1002"),
    ("s1003", "Passw0rd!", "student", "S1003"),
    ("s1004", "Passw0rd!", "student", "S1004"),
    ("s1005", "Passw0rd!", "student", "S1005"),
    ("bankapp", "Bank123!", "bank", None),
    ("admin", "Admin123!", "admin", None),
]


def seed(db: Session) -> None:
    for sn, name in STUDENTS:
        if db.get(Student, sn) is None:
            db.add(Student(student_no=sn, full_name=name))
    db.flush()
    for sn, term, amount, paid in TUITIONS:
        exists = db.scalar(select(Tuition).where(Tuition.student_no == sn, Tuition.term == term))
        if exists is None:
            t = Tuition(student_no=sn, term=term, amount_cents=amount, paid_cents=paid, currency="USD")
            db.add(t)
            db.flush()
            if paid:
                db.add(Payment(transaction_id=f"seed-{sn}-{term}", tuition_id=t.id, student_no=sn, term=term,
                               amount_cents=paid, channel="SEED", created_by="seed"))
    for username, pw, role, sn in USERS:
        if db.scalar(select(User).where(User.username == username)) is None:
            db.add(User(username=username, password_hash=hash_password(pw), role=role, student_no=sn))
    db.commit()
