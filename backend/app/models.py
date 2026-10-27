"""ORM models for the tables the API writes to. The tables already exist (simple.sql creates them),
so nothing here ever calls create_all(). Reads for the dashboard go through database views instead."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    balance: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    account_status: Mapped[str] = mapped_column(String(20))   # ACTIVE | FROZEN | FLAGGED


class FraudRule(Base):
    __tablename__ = "fraud_rules"

    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_name: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(String(255))
    evaluation_metric: Mapped[str] = mapped_column(String(50))
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    lookback_minutes: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))           # FLAG | QUARANTINE | BLOCK
    is_active: Mapped[bool] = mapped_column(Boolean)


class BlocklistedIp(Base):
    __tablename__ = "blocklisted_ips"

    ip_block_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ip_address: Mapped[str] = mapped_column(String(45))
    reason: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(20), default="MANUAL")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BlocklistedAccount(Base):
    __tablename__ = "blocklisted_accounts"

    block_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    external_account_number: Mapped[Optional[str]] = mapped_column(String(50))
    reason: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
