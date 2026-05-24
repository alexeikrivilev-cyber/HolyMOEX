from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class DecisionSet:
    decision_set_id: str
    decision_request_id: str
    horizon: str
    decisions: tuple[Mapping[str, Any], ...]
    calculation_version: str
    created_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DecisionSet":
        decisions = payload.get("decisions") or ()
        if not isinstance(decisions, (list, tuple)):
            decisions = ()
        return cls(
            decision_set_id=str(payload.get("decision_set_id") or payload.get("id") or ""),
            decision_request_id=str(payload.get("decision_request_id") or ""),
            horizon=str(payload.get("horizon") or ""),
            decisions=tuple(dict(item) for item in decisions if isinstance(item, Mapping)),
            calculation_version=str(payload.get("calculation_version") or ""),
            created_at=_optional_text(payload.get("created_at")),
        )


@dataclass(frozen=True)
class RiskPolicy:
    risk_policy_id: str
    policy_name: str
    version: str
    status: str
    run_mode_allowed: tuple[str, ...]
    rules: Mapping[str, Any]
    approved_by: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicy":
        rules = payload.get("rules") or {}
        if not isinstance(rules, Mapping):
            rules = {}
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or payload.get("id") or ""),
            policy_name=str(payload.get("policy_name") or ""),
            version=str(payload.get("version") or ""),
            status=str(payload.get("status") or ""),
            run_mode_allowed=_string_tuple(payload.get("run_mode_allowed")),
            rules=dict(rules),
            approved_by=_optional_text(payload.get("approved_by")),
        )


@dataclass(frozen=True)
class InstrumentLimit:
    risk_policy_id: str
    instrument_id: str
    max_position_pct: float | None
    max_order_value_rub: float | None
    max_slippage_bps: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    instrument_limit_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentLimit":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            max_position_pct=_optional_float(payload.get("max_position_pct")),
            max_order_value_rub=_optional_float(payload.get("max_order_value_rub")),
            max_slippage_bps=_optional_float(payload.get("max_slippage_bps")),
            payload=dict(source_payload),
            instrument_limit_id=_optional_text(payload.get("instrument_limit_id")),
        )


@dataclass(frozen=True)
class PortfolioLimit:
    risk_policy_id: str
    limit_name: str
    limit_value: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    portfolio_limit_id: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioLimit":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or ""),
            limit_name=str(payload.get("limit_name") or ""),
            limit_value=_optional_float(payload.get("limit_value")),
            payload=dict(source_payload),
            portfolio_limit_id=_optional_text(payload.get("portfolio_limit_id")),
        )


@dataclass(frozen=True)
class PortfolioSnapshot:
    portfolio_snapshot_id: str
    portfolio_id: str
    universe_id: str | None
    as_of_ts: str
    initial_capital_rub: float | None
    cash: float | None
    equity: float | None
    gross_exposure: float | None
    net_exposure: float | None
    realized_pnl: float | None
    unrealized_pnl: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioSnapshot":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            portfolio_snapshot_id=str(payload.get("portfolio_snapshot_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            initial_capital_rub=_optional_float(payload.get("initial_capital_rub")),
            cash=_optional_float(payload.get("cash")),
            equity=_optional_float(payload.get("equity")),
            gross_exposure=_optional_float(payload.get("gross_exposure")),
            net_exposure=_optional_float(payload.get("net_exposure")),
            realized_pnl=_optional_float(payload.get("realized_pnl")),
            unrealized_pnl=_optional_float(payload.get("unrealized_pnl")),
            payload=dict(source_payload),
        )


@dataclass(frozen=True)
class PositionState:
    position_state_id: str
    portfolio_id: str
    instrument_id: str
    as_of_ts: str
    quantity: float
    average_price: float | None
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PositionState":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            position_state_id=str(payload.get("position_state_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            quantity=_optional_float(payload.get("quantity")) or 0.0,
            average_price=_optional_float(payload.get("average_price")),
            market_price=_optional_float(payload.get("market_price")),
            market_value=_optional_float(payload.get("market_value")),
            unrealized_pnl=_optional_float(payload.get("unrealized_pnl")),
            payload=dict(source_payload),
        )


@dataclass(frozen=True)
class FeatureVector:
    feature_vector_id: str
    instrument_id: str
    horizon: str
    as_of_ts: str
    features: Mapping[str, Mapping[str, Any]]
    coverage_ratio: float
    data_quality_score: float
    build_version: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FeatureVector":
        features = payload.get("features") or {}
        if not isinstance(features, Mapping):
            features = {}
        return cls(
            feature_vector_id=str(payload.get("feature_vector_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            horizon=str(payload.get("horizon") or ""),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            features={str(key): dict(value) for key, value in features.items() if isinstance(value, Mapping)},
            coverage_ratio=_optional_float(payload.get("coverage_ratio")) or 0.0,
            data_quality_score=_optional_float(payload.get("data_quality_score")) or 0.0,
            build_version=str(payload.get("build_version") or ""),
        )


@dataclass(frozen=True)
class RiskCheckResultRecord:
    risk_check_id: str
    decision_set_id: str
    status: str
    approved_order_intents: tuple[str, ...]
    rejected_decisions: tuple[str, ...]
    risk_flags: tuple[str, ...]
    adjustments: tuple[Mapping[str, Any], ...]
    checked_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_contract(self) -> dict[str, Any]:
        return {
            "risk_check_id": self.risk_check_id,
            "decision_set_id": self.decision_set_id,
            "status": self.status,
            "approved_order_intents": list(self.approved_order_intents),
            "rejected_decisions": list(self.rejected_decisions),
            "risk_flags": list(self.risk_flags),
            "adjustments": [dict(item) for item in self.adjustments],
        }


@dataclass(frozen=True)
class RiskEventRecord:
    universe_id: str
    instrument_id: str | None
    as_of_ts: str
    payload: Mapping[str, Any]
    source_module: str
    calculation_version: str


@dataclass(frozen=True)
class OrderIntentRecord:
    order_intent_id: str
    instrument_id: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    time_in_force: str
    max_slippage_bps: float
    execution_ttl_seconds: int
    decision_set_id: str
    risk_check_id: str
    run_mode: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_contract(self) -> dict[str, Any]:
        return {
            "order_intent_id": self.order_intent_id,
            "instrument_id": self.instrument_id,
            "side": self.side,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "time_in_force": self.time_in_force,
            "max_slippage_bps": self.max_slippage_bps,
            "execution_ttl_seconds": self.execution_ttl_seconds,
            "decision_set_id": self.decision_set_id,
            "risk_check_id": self.risk_check_id,
            "run_mode": self.run_mode,
        }


@dataclass(frozen=True)
class AuditRecord:
    audit_record_id: str
    module_name: str
    job_id: str
    severity: str
    event_type: str
    message: str
    object_type: str
    object_ref: str
    reason_codes: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)


class RiskControlRepository(Protocol):
    def get_decision_set(self, decision_set_id: str) -> DecisionSet | None:
        ...

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        ...

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        ...

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        ...

    def get_portfolio_snapshot(self, portfolio_state_ref: str, as_of_ts: str) -> PortfolioSnapshot | None:
        ...

    def list_position_states(self, portfolio_id: str, instrument_ids: tuple[str, ...], as_of_ts: str) -> tuple[PositionState, ...]:
        ...

    def list_feature_vectors(self, instrument_ids: tuple[str, ...], horizon: str, as_of_ts: str) -> tuple[FeatureVector, ...]:
        ...

    def save_risk_check_result(self, result: RiskCheckResultRecord) -> str:
        ...

    def save_order_intent(self, order_intent: OrderIntentRecord) -> str:
        ...

    def save_risk_event(self, risk_event: RiskEventRecord) -> str:
        ...

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        ...


class InMemoryRiskControlRepository:
    def __init__(
        self,
        decision_sets: tuple[Mapping[str, Any] | DecisionSet, ...] = (),
        risk_policies: tuple[Mapping[str, Any] | RiskPolicy, ...] = (),
        instrument_limits: tuple[Mapping[str, Any] | InstrumentLimit, ...] = (),
        portfolio_limits: tuple[Mapping[str, Any] | PortfolioLimit, ...] = (),
        portfolio_snapshots: tuple[Mapping[str, Any] | PortfolioSnapshot, ...] = (),
        position_states: tuple[Mapping[str, Any] | PositionState, ...] = (),
        feature_vectors: tuple[Mapping[str, Any] | FeatureVector, ...] = (),
    ) -> None:
        self.decision_sets = tuple(item if isinstance(item, DecisionSet) else DecisionSet.from_mapping(item) for item in decision_sets)
        self.risk_policies = tuple(item if isinstance(item, RiskPolicy) else RiskPolicy.from_mapping(item) for item in risk_policies)
        self.instrument_limits = tuple(item if isinstance(item, InstrumentLimit) else InstrumentLimit.from_mapping(item) for item in instrument_limits)
        self.portfolio_limits = tuple(item if isinstance(item, PortfolioLimit) else PortfolioLimit.from_mapping(item) for item in portfolio_limits)
        self.portfolio_snapshots = tuple(item if isinstance(item, PortfolioSnapshot) else PortfolioSnapshot.from_mapping(item) for item in portfolio_snapshots)
        self.position_states = tuple(item if isinstance(item, PositionState) else PositionState.from_mapping(item) for item in position_states)
        self.feature_vectors = tuple(item if isinstance(item, FeatureVector) else FeatureVector.from_mapping(item) for item in feature_vectors)
        self.saved_risk_check_results: list[RiskCheckResultRecord] = []
        self.saved_order_intents: list[OrderIntentRecord] = []
        self.saved_risk_events: list[RiskEventRecord] = []
        self.saved_audit_records: list[AuditRecord] = []

    def get_decision_set(self, decision_set_id: str) -> DecisionSet | None:
        requested = _ref_tail(decision_set_id)
        if requested in {"", "latest", "scheduled"}:
            if not self.decision_sets:
                return None
            return sorted(self.decision_sets, key=lambda item: (item.created_at, item.decision_set_id))[-1]
        for decision_set in self.decision_sets:
            if decision_set.decision_set_id == requested:
                return decision_set
        return None

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        requested = _ref_tail(risk_policy_id)
        for policy in self.risk_policies:
            if policy.risk_policy_id == requested:
                return policy
        return None

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        requested = _ref_tail(risk_policy_id)
        requested_ids = set(instrument_ids)
        return tuple(
            sorted(
                (
                    item
                    for item in self.instrument_limits
                    if item.risk_policy_id == requested and item.instrument_id in requested_ids
                ),
                key=lambda item: item.instrument_id,
            )
        )

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        requested = _ref_tail(risk_policy_id)
        return tuple(sorted((item for item in self.portfolio_limits if item.risk_policy_id == requested), key=lambda item: item.limit_name))

    def get_portfolio_snapshot(self, portfolio_state_ref: str, as_of_ts: str) -> PortfolioSnapshot | None:
        requested = _ref_tail(portfolio_state_ref)
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            snapshot
            for snapshot in self.portfolio_snapshots
            if snapshot.as_of_ts
            and parse_utc_iso(snapshot.as_of_ts) <= as_of
            and (requested in {"", "latest", "scheduled"} or snapshot.portfolio_snapshot_id == requested)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item.as_of_ts, item.portfolio_snapshot_id))[-1]

    def list_position_states(self, portfolio_id: str, instrument_ids: tuple[str, ...], as_of_ts: str) -> tuple[PositionState, ...]:
        requested_ids = set(instrument_ids)
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            position
            for position in self.position_states
            if position.portfolio_id == portfolio_id
            and position.instrument_id in requested_ids
            and position.as_of_ts
            and parse_utc_iso(position.as_of_ts) <= as_of
        ]
        latest: dict[str, PositionState] = {}
        for position in candidates:
            current = latest.get(position.instrument_id)
            if current is None or (position.as_of_ts, position.position_state_id) > (current.as_of_ts, current.position_state_id):
                latest[position.instrument_id] = position
        return tuple(sorted(latest.values(), key=lambda item: item.instrument_id))

    def list_feature_vectors(self, instrument_ids: tuple[str, ...], horizon: str, as_of_ts: str) -> tuple[FeatureVector, ...]:
        requested_ids = set(instrument_ids)
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            vector
            for vector in self.feature_vectors
            if vector.instrument_id in requested_ids
            and vector.horizon == horizon
            and vector.as_of_ts
            and parse_utc_iso(vector.as_of_ts) <= as_of
        ]
        latest: dict[str, FeatureVector] = {}
        for vector in candidates:
            current = latest.get(vector.instrument_id)
            if current is None or (vector.as_of_ts, vector.feature_vector_id) > (current.as_of_ts, current.feature_vector_id):
                latest[vector.instrument_id] = vector
        return tuple(sorted(latest.values(), key=lambda item: item.instrument_id))

    def save_risk_check_result(self, result: RiskCheckResultRecord) -> str:
        self.saved_risk_check_results.append(result)
        return f"risk.risk_check_result:{result.risk_check_id}"

    def save_order_intent(self, order_intent: OrderIntentRecord) -> str:
        self.saved_order_intents.append(order_intent)
        return f"orders.order_intent:{order_intent.order_intent_id}"

    def save_risk_event(self, risk_event: RiskEventRecord) -> str:
        self.saved_risk_events.append(risk_event)
        event_id = stable_record_id(
            "risk_context",
            {
                "universe_id": risk_event.universe_id,
                "instrument_id": risk_event.instrument_id,
                "as_of_ts": risk_event.as_of_ts,
                "calculation_version": risk_event.calculation_version,
                "payload": dict(risk_event.payload),
            },
        )
        return f"risk.risk_context_record:{event_id}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        self.saved_audit_records.append(audit_record)
        return f"audit.audit_record:{audit_record.audit_record_id}"


class PostgresRiskControlRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def get_decision_set(self, decision_set_id: str) -> DecisionSet | None:
        requested = _ref_tail(decision_set_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if requested in {"", "latest", "scheduled"}:
                    cur.execute(
                        """
                        SELECT decision_set_id, decision_request_id, horizon, decisions,
                               calculation_version, created_at
                          FROM decisions.decision_set
                         ORDER BY created_at DESC, decision_set_id DESC
                         LIMIT 1
                        """
                    )
                else:
                    cur.execute(
                        """
                        SELECT decision_set_id, decision_request_id, horizon, decisions,
                               calculation_version, created_at
                          FROM decisions.decision_set
                         WHERE decision_set_id = %s
                        """,
                        (requested,),
                    )
                row = cur.fetchone()
        return _decision_set_from_row(row) if row else None

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_policy_id, policy_name, version, status,
                           run_mode_allowed, rules, approved_by
                      FROM risk.risk_policy
                     WHERE risk_policy_id = %s
                    """,
                    (_ref_tail(risk_policy_id),),
                )
                row = cur.fetchone()
        return _risk_policy_from_row(row) if row else None

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_limit_id, risk_policy_id, instrument_id,
                           max_position_pct, max_order_value_rub,
                           max_slippage_bps, payload
                      FROM risk.instrument_limit
                     WHERE risk_policy_id = %s
                       AND instrument_id = ANY(%s)
                    """,
                    (_ref_tail(risk_policy_id), list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(_instrument_limit_from_row(row) for row in rows)

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT portfolio_limit_id, risk_policy_id, limit_name,
                           limit_value, payload
                      FROM risk.portfolio_limit
                     WHERE risk_policy_id = %s
                    """,
                    (_ref_tail(risk_policy_id),),
                )
                rows = cur.fetchall()
        return tuple(_portfolio_limit_from_row(row) for row in rows)

    def get_portfolio_snapshot(self, portfolio_state_ref: str, as_of_ts: str) -> PortfolioSnapshot | None:
        requested = _ref_tail(portfolio_state_ref)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT portfolio_snapshot_id, portfolio_id, universe_id, as_of_ts,
                           initial_capital_rub, cash, equity, gross_exposure,
                           net_exposure, realized_pnl, unrealized_pnl, payload
                      FROM portfolio.portfolio_snapshot
                     WHERE as_of_ts <= %s
                       AND (%s IN ('', 'latest', 'scheduled') OR portfolio_snapshot_id = %s)
                     ORDER BY as_of_ts DESC, portfolio_snapshot_id DESC
                     LIMIT 1
                    """,
                    (parse_utc_iso(as_of_ts), requested, requested),
                )
                row = cur.fetchone()
        return _portfolio_snapshot_from_row(row) if row else None

    def list_position_states(self, portfolio_id: str, instrument_ids: tuple[str, ...], as_of_ts: str) -> tuple[PositionState, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           position_state_id, portfolio_id, instrument_id, as_of_ts,
                           quantity, average_price, market_price, market_value,
                           unrealized_pnl, payload
                      FROM portfolio.position_state
                     WHERE portfolio_id = %s
                       AND instrument_id = ANY(%s)
                       AND as_of_ts <= %s
                     ORDER BY instrument_id, as_of_ts DESC, position_state_id DESC
                    """,
                    (portfolio_id, list(instrument_ids), parse_utc_iso(as_of_ts)),
                )
                rows = cur.fetchall()
        return tuple(_position_state_from_row(row) for row in rows)

    def list_feature_vectors(self, instrument_ids: tuple[str, ...], horizon: str, as_of_ts: str) -> tuple[FeatureVector, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           feature_vector_id, instrument_id, horizon, as_of_ts,
                           features, coverage_ratio, data_quality_score,
                           build_version
                      FROM features.feature_vector
                     WHERE instrument_id = ANY(%s)
                       AND horizon = %s
                       AND as_of_ts <= %s
                     ORDER BY instrument_id, as_of_ts DESC, feature_vector_id DESC
                    """,
                    (list(instrument_ids), horizon, parse_utc_iso(as_of_ts)),
                )
                rows = cur.fetchall()
        return tuple(_feature_vector_from_row(row) for row in rows)

    def save_risk_check_result(self, result: RiskCheckResultRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk.risk_check_result (
                        risk_check_id, decision_set_id, status,
                        approved_order_intents, rejected_decisions, risk_flags,
                        adjustments, checked_at, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (risk_check_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        approved_order_intents = EXCLUDED.approved_order_intents,
                        rejected_decisions = EXCLUDED.rejected_decisions,
                        risk_flags = EXCLUDED.risk_flags,
                        adjustments = EXCLUDED.adjustments,
                        checked_at = EXCLUDED.checked_at,
                        payload = EXCLUDED.payload
                    """,
                    (
                        result.risk_check_id,
                        result.decision_set_id,
                        result.status,
                        list(result.approved_order_intents),
                        list(result.rejected_decisions),
                        list(result.risk_flags),
                        Jsonb([dict(item) for item in result.adjustments]),
                        parse_utc_iso(result.checked_at),
                        Jsonb(dict(result.payload)),
                    ),
                )
        return f"risk.risk_check_result:{result.risk_check_id}"

    def save_order_intent(self, order_intent: OrderIntentRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders.order_intent (
                        order_intent_id, instrument_id, side, quantity,
                        order_type, limit_price, time_in_force,
                        max_slippage_bps, execution_ttl_seconds,
                        decision_set_id, risk_check_id, run_mode, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_intent_id) DO UPDATE SET
                        quantity = EXCLUDED.quantity,
                        limit_price = EXCLUDED.limit_price,
                        max_slippage_bps = EXCLUDED.max_slippage_bps,
                        execution_ttl_seconds = EXCLUDED.execution_ttl_seconds,
                        payload = EXCLUDED.payload
                    """,
                    (
                        order_intent.order_intent_id,
                        order_intent.instrument_id,
                        order_intent.side,
                        order_intent.quantity,
                        order_intent.order_type,
                        order_intent.limit_price,
                        order_intent.time_in_force,
                        order_intent.max_slippage_bps,
                        order_intent.execution_ttl_seconds,
                        order_intent.decision_set_id,
                        order_intent.risk_check_id,
                        order_intent.run_mode,
                        Jsonb(dict(order_intent.payload)),
                    ),
                )
        return f"orders.order_intent:{order_intent.order_intent_id}"

    def save_risk_event(self, risk_event: RiskEventRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk.risk_context_record (
                        universe_id, instrument_id, as_of_ts, payload,
                        source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING risk_context_record_id
                    """,
                    (
                        risk_event.universe_id,
                        risk_event.instrument_id,
                        parse_utc_iso(risk_event.as_of_ts),
                        Jsonb(dict(risk_event.payload)),
                        risk_event.source_module,
                        risk_event.calculation_version,
                    ),
                )
                row = cur.fetchone()
        return f"risk.risk_context_record:{row[0]}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.audit_record (
                        module_name, job_id, severity, event_type, message,
                        object_type, object_ref, reason_codes, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING audit_record_id
                    """,
                    (
                        audit_record.module_name,
                        audit_record.job_id,
                        audit_record.severity,
                        audit_record.event_type,
                        audit_record.message,
                        audit_record.object_type,
                        audit_record.object_ref,
                        list(audit_record.reason_codes),
                        Jsonb(dict(audit_record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _decision_set_from_row(row: tuple[Any, ...]) -> DecisionSet:
    return DecisionSet(
        decision_set_id=row[0] or "",
        decision_request_id=row[1] or "",
        horizon=row[2] or "",
        decisions=tuple(dict(item) for item in (row[3] or ()) if isinstance(item, Mapping)),
        calculation_version=row[4] or "",
        created_at=_iso(row[5]),
    )


def _risk_policy_from_row(row: tuple[Any, ...]) -> RiskPolicy:
    return RiskPolicy(
        risk_policy_id=row[0] or "",
        policy_name=row[1] or "",
        version=row[2] or "",
        status=row[3] or "",
        run_mode_allowed=tuple(row[4] or ()),
        rules=row[5] or {},
        approved_by=row[6],
    )


def _instrument_limit_from_row(row: tuple[Any, ...]) -> InstrumentLimit:
    return InstrumentLimit(
        instrument_limit_id=str(row[0]),
        risk_policy_id=row[1] or "",
        instrument_id=row[2] or "",
        max_position_pct=_optional_float(row[3]),
        max_order_value_rub=_optional_float(row[4]),
        max_slippage_bps=_optional_float(row[5]),
        payload=row[6] or {},
    )


def _portfolio_limit_from_row(row: tuple[Any, ...]) -> PortfolioLimit:
    return PortfolioLimit(
        portfolio_limit_id=str(row[0]),
        risk_policy_id=row[1] or "",
        limit_name=row[2] or "",
        limit_value=_optional_float(row[3]),
        payload=row[4] or {},
    )


def _portfolio_snapshot_from_row(row: tuple[Any, ...]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        portfolio_snapshot_id=row[0] or "",
        portfolio_id=row[1] or "",
        universe_id=row[2],
        as_of_ts=_iso(row[3]),
        initial_capital_rub=_optional_float(row[4]),
        cash=_optional_float(row[5]),
        equity=_optional_float(row[6]),
        gross_exposure=_optional_float(row[7]),
        net_exposure=_optional_float(row[8]),
        realized_pnl=_optional_float(row[9]),
        unrealized_pnl=_optional_float(row[10]),
        payload=row[11] or {},
    )


def _position_state_from_row(row: tuple[Any, ...]) -> PositionState:
    return PositionState(
        position_state_id=row[0] or "",
        portfolio_id=row[1] or "",
        instrument_id=row[2] or "",
        as_of_ts=_iso(row[3]),
        quantity=_optional_float(row[4]) or 0.0,
        average_price=_optional_float(row[5]),
        market_price=_optional_float(row[6]),
        market_value=_optional_float(row[7]),
        unrealized_pnl=_optional_float(row[8]),
        payload=row[9] or {},
    )


def _feature_vector_from_row(row: tuple[Any, ...]) -> FeatureVector:
    return FeatureVector(
        feature_vector_id=row[0] or "",
        instrument_id=row[1] or "",
        horizon=row[2] or "",
        as_of_ts=_iso(row[3]),
        features=row[4] or {},
        coverage_ratio=_optional_float(row[5]) or 0.0,
        data_quality_score=_optional_float(row[6]) or 0.0,
        build_version=row[7] or "",
    )


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        iterator = iter(value)
    except TypeError:
        return (str(value),)
    return tuple(str(item) for item in iterator if item not in (None, ""))


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
