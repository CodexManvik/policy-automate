"""
SQLAlchemy async ORM models for the enterprise policy ledger.

Table strategy:
  - policies            → PolicyApiResponse
  - members             → MemberApiResponse
  - claims_history      → ClaimsHistoryApiResponse
  - porting_records     → PortingApiResponse
  - network_providers   → NetworkApiResponse
  - benefit_balances    → BenefitBalanceApiResponse  (mutable, row-level locked on write)
  - lifetime_states     → LifetimeStateApiResponse   (mutable, row-level locked on write)
  - endorsements        → EndorsementItem list

All monetary columns are NUMERIC(18,2) to prevent float drift.
JSONB is used for list/dict columns (riders, ped_declarations, etc.)
because they vary per product and are not queried relationally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    Boolean, DateTime, Integer, Numeric, String, Text, ForeignKey,
    UniqueConstraint, Index, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------

class Policy(Base):
    __tablename__ = "policies"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_variant: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    base_sum_insured: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    optional_riders: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    co_pay_option: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    deductible_option: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    room_category_entitled: Mapped[str] = mapped_column(String(128), nullable=False)
    premium_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False, default="individual")
    hospital_daily_cash_amount: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    pa_sum_insured: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)

    # relationships
    members: Mapped[List["Member"]] = relationship("Member", back_populates="policy", lazy="select")
    balances: Mapped[List["BenefitBalance"]] = relationship("BenefitBalance", back_populates="policy", lazy="select")
    lifetime_state: Mapped[Optional["LifetimeState"]] = relationship("LifetimeState", back_populates="policy", uselist=False, lazy="select")
    porting: Mapped[Optional["PortingRecord"]] = relationship("PortingRecord", back_populates="policy", uselist=False, lazy="select")
    endorsements: Mapped[List["Endorsement"]] = relationship("Endorsement", back_populates="policy", lazy="select")


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------

class Member(Base):
    __tablename__ = "members"

    member_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_type: Mapped[str] = mapped_column("relationship", String(64), nullable=False)
    date_of_addition: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ped_declarations: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    eligibility_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    policy: Mapped["Policy"] = relationship("Policy", back_populates="members")
    claims_history: Mapped[List["ClaimsHistory"]] = relationship("ClaimsHistory", back_populates="member", lazy="select")
    balances: Mapped[List["BenefitBalance"]] = relationship("BenefitBalance", back_populates="member", lazy="select")


# ---------------------------------------------------------------------------
# claims_history
# ---------------------------------------------------------------------------

class ClaimsHistory(Base):
    __tablename__ = "claims_history"
    __table_args__ = (
        UniqueConstraint("policy_id", "member_id", name="uq_claims_history_policy_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), nullable=False)
    member_id: Mapped[str] = mapped_column(String(64), ForeignKey("members.member_id"), nullable=False)
    prior_claims_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_prior_amount_paid: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    cumulative_exclusions_triggered: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    deductible_consumed_ytd: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    last_claim_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_free_years: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    member: Mapped["Member"] = relationship("Member", back_populates="claims_history")

    __table_args__ = (
        UniqueConstraint("policy_id", "member_id", name="uq_claims_history_policy_member"),
        Index("ix_claims_history_policy_member", "policy_id", "member_id"),
    )


# ---------------------------------------------------------------------------
# porting_records
# ---------------------------------------------------------------------------

class PortingRecord(Base):
    __tablename__ = "porting_records"

    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), primary_key=True)
    is_ported_policy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    waiting_period_credit_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    moratorium_eligible_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    continuous_coverage_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    policy: Mapped["Policy"] = relationship("Policy", back_populates="porting")


# ---------------------------------------------------------------------------
# network_providers
# ---------------------------------------------------------------------------

class NetworkProvider(Base):
    __tablename__ = "network_providers"

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hospital_name: Mapped[str] = mapped_column(String(256), nullable=False)
    network_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    heads_up_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ---------------------------------------------------------------------------
# benefit_balances  (mutable — SELECT FOR UPDATE on adjudication write-path)
# ---------------------------------------------------------------------------

class BenefitBalance(Base):
    __tablename__ = "benefit_balances"
    __table_args__ = (
        UniqueConstraint("policy_id", "member_id", name="uq_benefit_balance_policy_member"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), nullable=False)
    member_id: Mapped[str] = mapped_column(String(64), ForeignKey("members.member_id"), nullable=False)
    base_si_remaining: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    booster_plus_remaining: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    reassure_forever_pool: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    cash_bag_plus_wallet_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    hospital_cash_days_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    personal_accident_limit_remaining: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)

    policy: Mapped["Policy"] = relationship("Policy", back_populates="balances")
    member: Mapped["Member"] = relationship("Member", back_populates="balances")


# ---------------------------------------------------------------------------
# lifetime_states  (mutable — SELECT FOR UPDATE on adjudication write-path)
# ---------------------------------------------------------------------------

class LifetimeState(Base):
    __tablename__ = "lifetime_states"

    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), primary_key=True)
    lock_the_clock_age_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_premium_age: Mapped[int] = mapped_column(Integer, nullable=False)
    reassure_forever_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reassure_forever_triggered_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reassure_forever_triggered_claim_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lock_the_clock_unlocked_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    convalescence_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    critical_illness_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    critical_illness_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    booster_plus_accumulated: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    booster_plus_last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSONB for nested live_healthy and cash_bag_plus sub-objects
    live_healthy: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    cash_bag_plus: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    policy: Mapped["Policy"] = relationship("Policy", back_populates="lifetime_state")


# ---------------------------------------------------------------------------
# endorsements
# ---------------------------------------------------------------------------

class Endorsement(Base):
    __tablename__ = "endorsements"

    endorsement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(64), ForeignKey("policies.policy_id"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mutated_fields: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)

    policy: Mapped["Policy"] = relationship("Policy", back_populates="endorsements")

    __table_args__ = (
        Index("ix_endorsements_policy_id", "policy_id"),
    )
