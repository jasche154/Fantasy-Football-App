"""The declarative base and shared helpers."""
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass
