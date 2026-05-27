from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


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
    created_at: str | None = None

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
            created_at=_optional_text(payload.get("created_at")),
        )


@dataclass(frozen=True)
class WeightsProfile:
    weights_profile_id: str
    profile_name: str
    version: str
    status: str
    horizon: str
    run_mode_allowed: tuple[str, ...]
    approved_by: str | None = None
    validation_report_ref: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "WeightsProfile":
        return cls(
            weights_profile_id=str(payload.get("weights_profile_id") or payload.get("id") or ""),
            profile_name=str(payload.get("profile_name") or ""),
            version=str(payload.get("version") or ""),
            status=str(payload.get("status") or ""),
            horizon=str(payload.get("horizon") or ""),
            run_mode_allowed=_string_tuple(payload.get("run_mode_allowed")),
            approved_by=_optional_text(payload.get("approved_by")),
            validation_report_ref=_optional_text(payload.get("validation_report_ref")),
        )


@dataclass(frozen=True)
class MetricWeightRule:
    metric_weight_rule_id: str
    weights_profile_id: str
    metric_name: str
    metric_group: str
    horizon: str
    instrument_scope: str
    instrument_ids: tuple[str, ...]
    sector: str | None
    weight: float
    direction: str
    transform: str
    min_confidence_score: float
    stale_policy: str
    calculation_version: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MetricWeightRule":
        return cls(
            metric_weight_rule_id=str(payload.get("metric_weight_rule_id") or payload.get("id") or ""),
            weights_profile_id=str(payload.get("weights_profile_id") or ""),
            metric_name=str(payload.get("metric_name") or ""),
            metric_group=str(payload.get("metric_group") or ""),
            horizon=str(payload.get("horizon") or ""),
            instrument_scope=str(payload.get("instrument_scope") or "all"),
            instrument_ids=_string_tuple(payload.get("instrument_ids")),
            sector=_optional_text(payload.get("sector")),
            weight=_optional_float(payload.get("weight")) or 0.0,
            direction=str(payload.get("direction") or "positive"),
            transform=str(payload.get("transform") or "identity"),
            min_confidence_score=_optional_float(payload.get("min_confidence_score")) or 0.0,
            stale_policy=str(payload.get("stale_policy") or "block_decision"),
            calculation_version=str(payload.get("calculation_version") or ""),
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
            payload=source_payload,
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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PositionState":
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
        )


@dataclass(frozen=True)
class MarketStateRecord:
    market_state_record_id: str
    universe_id: str | None
    as_of_ts: str
    market_session_status: str | None
    market_regime: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MarketStateRecord":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            market_state_record_id=str(payload.get("market_state_record_id") or payload.get("id") or ""),
            universe_id=_optional_text(payload.get("universe_id")),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            market_session_status=_optional_text(payload.get("market_session_status")),
            market_regime=_optional_text(payload.get("market_regime")),
            payload=source_payload,
        )


@dataclass(frozen=True)
class DecisionRequestRecord:
    decision_request_id: str
    universe_id: str
    instrument_ids: tuple[str, ...]
    horizon: str
    as_of_ts: str
    feature_vector_refs: tuple[str, ...]
    portfolio_state_ref: str
    weights_profile_id: str
    run_mode: str
    decision_mode: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionSetRecord:
    decision_set_id: str
    decision_request_id: str
    horizon: str
    decisions: tuple[Mapping[str, Any], ...]
    calculation_version: str
    created_at: str

    def to_contract(self) -> dict[str, Any]:
        return {
            "decision_set_id": self.decision_set_id,
            "decision_request_id": self.decision_request_id,
            "horizon": self.horizon,
            "decisions": [dict(item) for item in self.decisions],
            "calculation_version": self.calculation_version,
        }


@dataclass(frozen=True)
class DecisionRecord:
    decision_record_id: str
    decision_set_id: str
    instrument_id: str
    action: str
    target_position_pct: float
    target_quantity: float
    confidence_score: float
    expected_edge_score: float
    risk_score: float
    primary_reason_codes: tuple[str, ...]
    feature_contributions: Mapping[str, float]


@dataclass(frozen=True)
class DecisionExplanation:
    decision_explanation_id: str
    decision_record_id: str
    explanation: Mapping[str, Any]


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


class DecisionEngineRepository(Protocol):
    def list_feature_vectors(
        self,
        feature_vector_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizon: str,
        as_of_ts: str,
    ) -> tuple[FeatureVector, ...]:
        ...

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        ...

    def list_metric_weight_rules(
        self,
        weights_profile_id: str,
        horizon: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[MetricWeightRule, ...]:
        ...

    def get_portfolio_snapshot(
        self,
        portfolio_state_ref: str,
        universe_id: str,
        as_of_ts: str,
    ) -> PortfolioSnapshot | None:
        ...

    def list_position_states(
        self,
        portfolio_id: str,
        instrument_ids: tuple[str, ...],
        as_of_ts: str,
    ) -> tuple[PositionState, ...]:
        ...

    def get_market_state(
        self,
        universe_id: str,
        as_of_ts: str,
    ) -> MarketStateRecord | None:
        ...

    def save_decision_request(self, request: DecisionRequestRecord) -> str:
        ...

    def save_decision_set(self, decision_set: DecisionSetRecord) -> str:
        ...

    def save_decision_record(self, record: DecisionRecord) -> str:
        ...

    def save_decision_explanation(self, explanation: DecisionExplanation) -> str:
        ...

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        ...


class InMemoryDecisionEngineRepository:
    def __init__(
        self,
        feature_vectors: tuple[Mapping[str, Any] | FeatureVector, ...] = (),
        weights_profiles: tuple[Mapping[str, Any] | WeightsProfile, ...] = (),
        metric_weight_rules: tuple[Mapping[str, Any] | MetricWeightRule, ...] = (),
        portfolio_snapshots: tuple[Mapping[str, Any] | PortfolioSnapshot, ...] = (),
        position_states: tuple[Mapping[str, Any] | PositionState, ...] = (),
        market_state_records: tuple[Mapping[str, Any] | MarketStateRecord, ...] = (),
    ) -> None:
        self.feature_vectors = tuple(item if isinstance(item, FeatureVector) else FeatureVector.from_mapping(item) for item in feature_vectors)
        self.weights_profiles = tuple(item if isinstance(item, WeightsProfile) else WeightsProfile.from_mapping(item) for item in weights_profiles)
        self.metric_weight_rules = tuple(item if isinstance(item, MetricWeightRule) else MetricWeightRule.from_mapping(item) for item in metric_weight_rules)
        self.portfolio_snapshots = tuple(item if isinstance(item, PortfolioSnapshot) else PortfolioSnapshot.from_mapping(item) for item in portfolio_snapshots)
        self.position_states = tuple(item if isinstance(item, PositionState) else PositionState.from_mapping(item) for item in position_states)
        self.market_state_records = tuple(item if isinstance(item, MarketStateRecord) else MarketStateRecord.from_mapping(item) for item in market_state_records)
        self.saved_decision_requests: list[DecisionRequestRecord] = []
        self.saved_decision_sets: list[DecisionSetRecord] = []
        self.saved_decision_records: list[DecisionRecord] = []
        self.saved_decision_explanations: list[DecisionExplanation] = []
        self.saved_audit_records: list[AuditRecord] = []

    def list_feature_vectors(
        self,
        feature_vector_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizon: str,
        as_of_ts: str,
    ) -> tuple[FeatureVector, ...]:
        requested_refs = {_ref_tail(ref) for ref in feature_vector_refs if _ref_tail(ref) not in {"latest", "scheduled"}}
        requested_ids = set(instrument_ids)
        as_of = parse_utc_iso(as_of_ts)
        fallback_from = as_of - timedelta(seconds=_env_float("DECISION_FEATURE_VECTOR_FALLBACK_LOOKBACK_SECONDS", 3600.0))
        records = [
            vector
            for vector in self.feature_vectors
            if vector.horizon == horizon
            and vector.instrument_id in requested_ids
            and parse_utc_iso(vector.as_of_ts) <= as_of
            and (
                not requested_refs
                or vector.feature_vector_id in requested_refs
                or (
                    parse_utc_iso(vector.as_of_ts) >= fallback_from
                    and _feature_vector_is_usable(vector)
                )
            )
        ]
        latest: dict[str, FeatureVector] = {}
        for vector in records:
            current = latest.get(vector.instrument_id)
            if current is None or _feature_vector_sort_key(vector, requested_refs) > _feature_vector_sort_key(current, requested_refs):
                latest[vector.instrument_id] = vector
        return tuple(sorted(latest.values(), key=lambda item: item.instrument_id))

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        requested = _ref_tail(weights_profile_id)
        for profile in self.weights_profiles:
            if profile.weights_profile_id == requested:
                return profile
        return None

    def list_metric_weight_rules(
        self,
        weights_profile_id: str,
        horizon: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[MetricWeightRule, ...]:
        requested_profile_id = _ref_tail(weights_profile_id)
        requested_ids = set(instrument_ids)
        return tuple(
            sorted(
                (
                    rule
                    for rule in self.metric_weight_rules
                    if rule.weights_profile_id == requested_profile_id
                    and rule.horizon == horizon
                    and (
                        rule.instrument_scope == "all"
                        or bool(set(rule.instrument_ids) & requested_ids)
                    )
                ),
                key=lambda item: (item.metric_name, item.metric_weight_rule_id),
            )
        )

    def get_portfolio_snapshot(
        self,
        portfolio_state_ref: str,
        universe_id: str,
        as_of_ts: str,
    ) -> PortfolioSnapshot | None:
        requested = _ref_tail(portfolio_state_ref)
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            snapshot
            for snapshot in self.portfolio_snapshots
            if snapshot.as_of_ts
            and parse_utc_iso(snapshot.as_of_ts) <= as_of
            and snapshot.universe_id in (None, "", universe_id)
            and (requested in {"", "latest", "scheduled"} or snapshot.portfolio_snapshot_id == requested or snapshot.universe_id == universe_id)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item.as_of_ts, item.portfolio_snapshot_id))[-1]

    def list_position_states(
        self,
        portfolio_id: str,
        instrument_ids: tuple[str, ...],
        as_of_ts: str,
    ) -> tuple[PositionState, ...]:
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

    def get_market_state(self, universe_id: str, as_of_ts: str) -> MarketStateRecord | None:
        as_of = parse_utc_iso(as_of_ts)
        candidates = [
            record
            for record in self.market_state_records
            if record.as_of_ts
            and parse_utc_iso(record.as_of_ts) <= as_of
            and record.universe_id in (None, "", universe_id)
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item.as_of_ts, item.market_state_record_id))[-1]

    def save_decision_request(self, request: DecisionRequestRecord) -> str:
        self.saved_decision_requests.append(request)
        return f"decisions.decision_request:{request.decision_request_id}"

    def save_decision_set(self, decision_set: DecisionSetRecord) -> str:
        self.saved_decision_sets.append(decision_set)
        return f"decisions.decision_set:{decision_set.decision_set_id}"

    def save_decision_record(self, record: DecisionRecord) -> str:
        self.saved_decision_records.append(record)
        return f"decisions.decision_record:{record.decision_record_id}"

    def save_decision_explanation(self, explanation: DecisionExplanation) -> str:
        self.saved_decision_explanations.append(explanation)
        return f"decisions.decision_explanation:{explanation.decision_explanation_id}"

    def save_audit_record(self, audit_record: AuditRecord) -> str:
        self.saved_audit_records.append(audit_record)
        return f"audit.audit_record:{audit_record.audit_record_id}"


class PostgresDecisionEngineRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_feature_vectors(
        self,
        feature_vector_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizon: str,
        as_of_ts: str,
    ) -> tuple[FeatureVector, ...]:
        requested_refs = tuple(dict.fromkeys(_ref_tail(ref) for ref in feature_vector_refs if ref and _ref_tail(ref) not in {"latest", "scheduled"}))
        requested = bool(requested_refs)
        as_of = parse_utc_iso(as_of_ts)
        fallback_from = as_of - timedelta(seconds=_env_float("DECISION_FEATURE_VECTOR_FALLBACK_LOOKBACK_SECONDS", 3600.0))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH candidates AS (
                        SELECT feature_vector_id, instrument_id, horizon, as_of_ts,
                               features, coverage_ratio, data_quality_score,
                               build_version, created_at,
                               feature_vector_id = ANY(%s) AS is_requested,
                               (
                                   COALESCE(coverage_ratio, 0) > 0
                                   AND COALESCE((
                                       SELECT count(*)
                                         FROM jsonb_object_keys(COALESCE(features, '{}'::jsonb) - '_meta')
                                   ), 0) > 0
                               ) AS is_usable
                          FROM features.feature_vector
                         WHERE instrument_id = ANY(%s)
                           AND horizon = %s
                           AND as_of_ts <= %s
                           AND (
                               NOT %s
                               OR feature_vector_id = ANY(%s)
                               OR (
                                   as_of_ts >= %s
                                   AND COALESCE(coverage_ratio, 0) > 0
                                   AND COALESCE((
                                       SELECT count(*)
                                         FROM jsonb_object_keys(COALESCE(features, '{}'::jsonb) - '_meta')
                                   ), 0) > 0
                               )
                           )
                    )
                    SELECT DISTINCT ON (instrument_id)
                           feature_vector_id, instrument_id, horizon, as_of_ts,
                           features, coverage_ratio, data_quality_score,
                           build_version, created_at
                      FROM candidates
                     ORDER BY instrument_id,
                              CASE
                                  WHEN %s AND is_requested AND is_usable THEN 4
                                  WHEN %s AND is_usable THEN 3
                                  WHEN %s AND is_requested THEN 2
                                  WHEN NOT %s AND is_usable THEN 4
                                  ELSE 1
                              END DESC,
                              as_of_ts DESC,
                              created_at DESC NULLS LAST,
                              feature_vector_id DESC
                    """,
                    (
                        list(requested_refs),
                        list(instrument_ids),
                        horizon,
                        as_of,
                        requested,
                        list(requested_refs),
                        fallback_from,
                        requested,
                        requested,
                        requested,
                        requested,
                    ),
                )
                rows = cur.fetchall()
        return tuple(_feature_vector_from_row(row) for row in rows)

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT weights_profile_id, profile_name, version, status,
                           horizon, run_mode_allowed, approved_by,
                           validation_report_ref
                      FROM weights.weights_profile
                     WHERE weights_profile_id = %s
                    """,
                    (_ref_tail(weights_profile_id),),
                )
                row = cur.fetchone()
        return _weights_profile_from_row(row) if row else None

    def list_metric_weight_rules(
        self,
        weights_profile_id: str,
        horizon: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[MetricWeightRule, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT metric_weight_rule_id, weights_profile_id, metric_name,
                           metric_group, horizon, instrument_scope, instrument_ids,
                           sector, weight, direction, transform,
                           min_confidence_score, stale_policy, calculation_version
                      FROM weights.metric_weight_rule
                     WHERE weights_profile_id = %s
                       AND horizon = %s
                       AND (
                            instrument_scope = 'all'
                            OR instrument_ids && %s
                       )
                     ORDER BY metric_name, metric_weight_rule_id
                    """,
                    (_ref_tail(weights_profile_id), horizon, list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(_metric_weight_rule_from_row(row) for row in rows)

    def get_portfolio_snapshot(
        self,
        portfolio_state_ref: str,
        universe_id: str,
        as_of_ts: str,
    ) -> PortfolioSnapshot | None:
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
                       AND (universe_id = %s OR universe_id IS NULL)
                       AND (%s IN ('', 'latest', 'scheduled') OR portfolio_snapshot_id = %s OR universe_id = %s)
                     ORDER BY as_of_ts DESC, portfolio_snapshot_id DESC
                     LIMIT 1
                    """,
                    (parse_utc_iso(as_of_ts), universe_id, requested, requested, universe_id),
                )
                row = cur.fetchone()
        return _portfolio_snapshot_from_row(row) if row else None

    def list_position_states(
        self,
        portfolio_id: str,
        instrument_ids: tuple[str, ...],
        as_of_ts: str,
    ) -> tuple[PositionState, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (instrument_id)
                           position_state_id, portfolio_id, instrument_id, as_of_ts,
                           quantity, average_price, market_price, market_value,
                           unrealized_pnl
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

    def get_market_state(self, universe_id: str, as_of_ts: str) -> MarketStateRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT market_state_record_id, universe_id, as_of_ts,
                           market_session_status, market_regime, payload
                      FROM features.market_state_record
                     WHERE as_of_ts <= %s
                       AND (universe_id = %s OR universe_id IS NULL)
                     ORDER BY as_of_ts DESC, market_state_record_id DESC
                     LIMIT 1
                    """,
                    (parse_utc_iso(as_of_ts), universe_id),
                )
                row = cur.fetchone()
        return _market_state_from_row(row) if row else None

    def save_decision_request(self, request: DecisionRequestRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO decisions.decision_request (
                        decision_request_id, universe_id, instrument_ids, horizon,
                        as_of_ts, feature_vector_refs, portfolio_state_ref,
                        weights_profile_id, run_mode, decision_mode, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (decision_request_id) DO UPDATE SET
                        universe_id = EXCLUDED.universe_id,
                        instrument_ids = EXCLUDED.instrument_ids,
                        horizon = EXCLUDED.horizon,
                        as_of_ts = EXCLUDED.as_of_ts,
                        feature_vector_refs = EXCLUDED.feature_vector_refs,
                        portfolio_state_ref = EXCLUDED.portfolio_state_ref,
                        weights_profile_id = EXCLUDED.weights_profile_id,
                        run_mode = EXCLUDED.run_mode,
                        decision_mode = EXCLUDED.decision_mode,
                        payload = EXCLUDED.payload
                    """,
                    (
                        request.decision_request_id,
                        request.universe_id,
                        list(request.instrument_ids),
                        request.horizon,
                        parse_utc_iso(request.as_of_ts),
                        list(request.feature_vector_refs),
                        request.portfolio_state_ref,
                        request.weights_profile_id,
                        request.run_mode,
                        request.decision_mode,
                        Jsonb(dict(request.payload)),
                    ),
                )
        return f"decisions.decision_request:{request.decision_request_id}"

    def save_decision_set(self, decision_set: DecisionSetRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO decisions.decision_set (
                        decision_set_id, decision_request_id, horizon, decisions,
                        calculation_version, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (decision_set_id) DO UPDATE SET
                        decision_request_id = EXCLUDED.decision_request_id,
                        horizon = EXCLUDED.horizon,
                        decisions = EXCLUDED.decisions,
                        calculation_version = EXCLUDED.calculation_version,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        decision_set.decision_set_id,
                        decision_set.decision_request_id,
                        decision_set.horizon,
                        Jsonb([dict(item) for item in decision_set.decisions]),
                        decision_set.calculation_version,
                        parse_utc_iso(decision_set.created_at),
                    ),
                )
        return f"decisions.decision_set:{decision_set.decision_set_id}"

    def save_decision_record(self, record: DecisionRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO decisions.decision_record (
                        decision_record_id, decision_set_id, instrument_id, action,
                        target_position_pct, target_quantity, confidence_score,
                        expected_edge_score, risk_score, primary_reason_codes,
                        feature_contributions
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (decision_record_id) DO UPDATE SET
                        action = EXCLUDED.action,
                        target_position_pct = EXCLUDED.target_position_pct,
                        target_quantity = EXCLUDED.target_quantity,
                        confidence_score = EXCLUDED.confidence_score,
                        expected_edge_score = EXCLUDED.expected_edge_score,
                        risk_score = EXCLUDED.risk_score,
                        primary_reason_codes = EXCLUDED.primary_reason_codes,
                        feature_contributions = EXCLUDED.feature_contributions
                    RETURNING decision_record_id
                    """,
                    (
                        record.decision_record_id,
                        record.decision_set_id,
                        record.instrument_id,
                        record.action,
                        record.target_position_pct,
                        record.target_quantity,
                        record.confidence_score,
                        record.expected_edge_score,
                        record.risk_score,
                        list(record.primary_reason_codes),
                        Jsonb(dict(record.feature_contributions)),
                    ),
                )
                row = cur.fetchone()
        return f"decisions.decision_record:{row[0]}"

    def save_decision_explanation(self, explanation: DecisionExplanation) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO decisions.decision_explanation (
                        decision_explanation_id, decision_record_id, explanation
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (decision_explanation_id) DO UPDATE SET
                        explanation = EXCLUDED.explanation
                    RETURNING decision_explanation_id
                    """,
                    (
                        explanation.decision_explanation_id,
                        explanation.decision_record_id,
                        Jsonb(dict(explanation.explanation)),
                    ),
                )
                row = cur.fetchone()
        return f"decisions.decision_explanation:{row[0]}"

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


def stable_uuid(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


def _feature_vector_from_row(row: tuple[Any, ...]) -> FeatureVector:
    return FeatureVector(
        feature_vector_id=str(row[0]),
        instrument_id=row[1] or "",
        horizon=row[2] or "",
        as_of_ts=_iso(row[3]),
        features=row[4] or {},
        coverage_ratio=_optional_float(row[5]) or 0.0,
        data_quality_score=_optional_float(row[6]) or 0.0,
        build_version=row[7] or "",
        created_at=_iso(row[8]),
    )


def _feature_vector_is_usable(vector: FeatureVector) -> bool:
    return (
        float(vector.coverage_ratio or 0.0) > 0.0
        and any(str(name) and not str(name).startswith("_") for name in vector.features)
    )


def _feature_vector_sort_key(vector: FeatureVector, requested_refs: set[str]) -> tuple[int, str, str]:
    is_requested = vector.feature_vector_id in requested_refs
    is_usable = _feature_vector_is_usable(vector)
    if requested_refs:
        rank = 4 if is_requested and is_usable else 3 if is_usable else 2 if is_requested else 1
    else:
        rank = 4 if is_usable else 1
    return (rank, vector.as_of_ts, vector.feature_vector_id)


def _weights_profile_from_row(row: tuple[Any, ...]) -> WeightsProfile:
    return WeightsProfile(
        weights_profile_id=row[0] or "",
        profile_name=row[1] or "",
        version=row[2] or "",
        status=row[3] or "",
        horizon=row[4] or "",
        run_mode_allowed=tuple(row[5] or ()),
        approved_by=row[6],
        validation_report_ref=row[7],
    )


def _metric_weight_rule_from_row(row: tuple[Any, ...]) -> MetricWeightRule:
    return MetricWeightRule(
        metric_weight_rule_id=row[0] or "",
        weights_profile_id=row[1] or "",
        metric_name=row[2] or "",
        metric_group=row[3] or "",
        horizon=row[4] or "",
        instrument_scope=row[5] or "all",
        instrument_ids=tuple(row[6] or ()),
        sector=row[7],
        weight=_optional_float(row[8]) or 0.0,
        direction=row[9] or "positive",
        transform=row[10] or "identity",
        min_confidence_score=_optional_float(row[11]) or 0.0,
        stale_policy=row[12] or "block_decision",
        calculation_version=row[13] or "",
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
    )


def _market_state_from_row(row: tuple[Any, ...]) -> MarketStateRecord:
    return MarketStateRecord(
        market_state_record_id=str(row[0]),
        universe_id=row[1],
        as_of_ts=_iso(row[2]),
        market_session_status=row[3],
        market_regime=row[4],
        payload=row[5] or {},
    )


def _ref_tail(ref: str) -> str:
    text = str(ref)
    if ":" not in text:
        return text
    prefix, value = text.split(":", 1)
    if "." in prefix:
        return value
    return text


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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
