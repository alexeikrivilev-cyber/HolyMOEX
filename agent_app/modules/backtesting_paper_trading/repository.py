from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class MarketBar:
    instrument_id: str
    open_ts: str
    close_ts: str
    close_price: float
    volume: float
    turnover: float
    source_ref: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MarketBar":
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            open_ts=str(payload.get("open_ts") or payload.get("timestamp") or ""),
            close_ts=str(payload.get("close_ts") or payload.get("open_ts") or payload.get("timestamp") or ""),
            close_price=_optional_float(payload.get("close_price") or payload.get("price")) or 0.0,
            volume=_optional_float(payload.get("volume")) or 0.0,
            turnover=_optional_float(payload.get("turnover")) or 0.0,
            source_ref=str(payload.get("source_ref") or ""),
            payload=_mapping(payload.get("payload") or payload.get("source_payload")),
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
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float | None
    normalized_value: float | None
    unit: str | None
    horizon: str
    contour: str
    timestamp: str
    ttl_seconds: int | None
    confidence_score: float | None
    source_module: str
    source_refs: tuple[str, ...]
    calculation_version: str
    quality_flags: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FeatureRecord":
        ttl_value = payload.get("ttl_seconds")
        return cls(
            feature_id=str(payload.get("feature_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            metric_name=str(payload.get("metric_name") or ""),
            metric_group=str(payload.get("metric_group") or ""),
            metric_type=str(payload.get("metric_type") or ""),
            raw_value=_optional_float(payload.get("raw_value")),
            normalized_value=_optional_float(payload.get("normalized_value")),
            unit=_optional_text(payload.get("unit")),
            horizon=str(payload.get("horizon") or ""),
            contour=str(payload.get("contour") or ""),
            timestamp=str(payload.get("timestamp") or payload.get("as_of_ts") or ""),
            ttl_seconds=int(ttl_value) if ttl_value not in (None, "") else None,
            confidence_score=_optional_float(payload.get("confidence_score")),
            source_module=str(payload.get("source_module") or ""),
            source_refs=_string_tuple(payload.get("source_refs")),
            calculation_version=str(payload.get("calculation_version") or ""),
            quality_flags=_string_tuple(payload.get("quality_flags")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class WeightsProfile:
    weights_profile_id: str
    profile_name: str
    version: str
    status: str
    horizon: str
    run_mode_allowed: tuple[str, ...]
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
    min_confidence_score: float | None
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
            min_confidence_score=_optional_float(payload.get("min_confidence_score")),
            stale_policy=str(payload.get("stale_policy") or "block"),
            calculation_version=str(payload.get("calculation_version") or ""),
        )


@dataclass(frozen=True)
class RiskPolicy:
    risk_policy_id: str
    policy_name: str
    version: str
    status: str
    run_mode_allowed: tuple[str, ...]
    rules: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RiskPolicy":
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or payload.get("id") or ""),
            policy_name=str(payload.get("policy_name") or ""),
            version=str(payload.get("version") or ""),
            status=str(payload.get("status") or ""),
            run_mode_allowed=_string_tuple(payload.get("run_mode_allowed")),
            rules=_mapping(payload.get("rules")),
        )


@dataclass(frozen=True)
class InstrumentLimit:
    risk_policy_id: str
    instrument_id: str
    max_position_pct: float | None
    max_order_value_rub: float | None
    max_slippage_bps: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentLimit":
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            max_position_pct=_optional_float(payload.get("max_position_pct")),
            max_order_value_rub=_optional_float(payload.get("max_order_value_rub")),
            max_slippage_bps=_optional_float(payload.get("max_slippage_bps")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class PortfolioLimit:
    risk_policy_id: str
    limit_name: str
    limit_value: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioLimit":
        return cls(
            risk_policy_id=str(payload.get("risk_policy_id") or ""),
            limit_name=str(payload.get("limit_name") or ""),
            limit_value=_optional_float(payload.get("limit_value")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class CorporateActionRecord:
    corporate_action_record_id: str
    instrument_id: str
    action_type: str
    effective_date: str | None
    adjustment_factor: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CorporateActionRecord":
        return cls(
            corporate_action_record_id=str(payload.get("corporate_action_record_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            action_type=str(payload.get("action_type") or ""),
            effective_date=_optional_text(payload.get("effective_date")),
            adjustment_factor=_optional_float(payload.get("adjustment_factor")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class SimulationReportRecord:
    simulation_report_id: str
    simulation_id: str
    universe_id: str
    horizon: str
    as_of_ts: str
    status: str
    payload: Mapping[str, Any]

    def to_contract(self) -> dict[str, Any]:
        report = self.payload.get("simulation_report")
        return dict(report) if isinstance(report, Mapping) else dict(self.payload)


@dataclass(frozen=True)
class PaperExecutionResultRecord:
    execution_result_id: str
    order_intent_id: str
    status: str
    submitted_at: str
    last_update_at: str
    filled_quantity: float
    avg_fill_price: float | None
    fees: float
    slippage_bps: float | None
    errors: tuple[str, ...]
    payload: Mapping[str, Any]


class BacktestingPaperTradingRepository(Protocol):
    def list_market_bars(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketBar, ...]:
        ...

    def list_feature_vectors(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureVector, ...]:
        ...

    def list_feature_records(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        ...

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        ...

    def list_metric_weight_rules(self, weights_profile_id: str, horizon: str) -> tuple[MetricWeightRule, ...]:
        ...

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        ...

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        ...

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        ...

    def list_corporate_actions(
        self,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[CorporateActionRecord, ...]:
        ...

    def save_simulation_report(self, report: SimulationReportRecord) -> str:
        ...

    def save_paper_execution_result(self, execution_result: PaperExecutionResultRecord) -> str:
        ...


class InMemoryBacktestingPaperTradingRepository:
    def __init__(
        self,
        market_bars: tuple[Mapping[str, Any] | MarketBar, ...] = (),
        feature_vectors: tuple[Mapping[str, Any] | FeatureVector, ...] = (),
        feature_records: tuple[Mapping[str, Any] | FeatureRecord, ...] = (),
        weights_profiles: tuple[Mapping[str, Any] | WeightsProfile, ...] = (),
        metric_weight_rules: tuple[Mapping[str, Any] | MetricWeightRule, ...] = (),
        risk_policies: tuple[Mapping[str, Any] | RiskPolicy, ...] = (),
        instrument_limits: tuple[Mapping[str, Any] | InstrumentLimit, ...] = (),
        portfolio_limits: tuple[Mapping[str, Any] | PortfolioLimit, ...] = (),
        corporate_actions: tuple[Mapping[str, Any] | CorporateActionRecord, ...] = (),
    ) -> None:
        self.market_bars = tuple(item if isinstance(item, MarketBar) else MarketBar.from_mapping(item) for item in market_bars)
        self.feature_vectors = tuple(
            item if isinstance(item, FeatureVector) else FeatureVector.from_mapping(item)
            for item in feature_vectors
        )
        self.feature_records = tuple(
            item if isinstance(item, FeatureRecord) else FeatureRecord.from_mapping(item)
            for item in feature_records
        )
        self.weights_profiles = tuple(
            item if isinstance(item, WeightsProfile) else WeightsProfile.from_mapping(item)
            for item in weights_profiles
        )
        self.metric_weight_rules = tuple(
            item if isinstance(item, MetricWeightRule) else MetricWeightRule.from_mapping(item)
            for item in metric_weight_rules
        )
        self.risk_policies = tuple(item if isinstance(item, RiskPolicy) else RiskPolicy.from_mapping(item) for item in risk_policies)
        self.instrument_limits = tuple(
            item if isinstance(item, InstrumentLimit) else InstrumentLimit.from_mapping(item)
            for item in instrument_limits
        )
        self.portfolio_limits = tuple(
            item if isinstance(item, PortfolioLimit) else PortfolioLimit.from_mapping(item)
            for item in portfolio_limits
        )
        self.corporate_actions = tuple(
            item if isinstance(item, CorporateActionRecord) else CorporateActionRecord.from_mapping(item)
            for item in corporate_actions
        )
        self.saved_simulation_reports: list[SimulationReportRecord] = []
        self.saved_paper_execution_results: list[PaperExecutionResultRecord] = []

    def list_market_bars(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketBar, ...]:
        del universe_id
        requested = set(instrument_ids)
        start = parse_utc_iso(from_ts)
        end = parse_utc_iso(to_ts)
        return tuple(
            sorted(
                (
                    bar
                    for bar in self.market_bars
                    if (not requested or bar.instrument_id in requested)
                    and bar.open_ts
                    and start <= parse_utc_iso(bar.open_ts) <= end
                ),
                key=lambda item: (item.open_ts, item.instrument_id),
            )
        )

    def list_feature_vectors(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureVector, ...]:
        requested = set(instrument_ids)
        start = parse_utc_iso(from_ts)
        end = parse_utc_iso(to_ts)
        return tuple(
            sorted(
                (
                    vector
                    for vector in self.feature_vectors
                    if (not requested or vector.instrument_id in requested)
                    and vector.horizon == horizon
                    and vector.as_of_ts
                    and start <= parse_utc_iso(vector.as_of_ts) <= end
                ),
                key=lambda item: (item.as_of_ts, item.instrument_id, item.feature_vector_id),
            )
        )

    def list_feature_records(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        requested = set(instrument_ids)
        start = parse_utc_iso(from_ts)
        end = parse_utc_iso(to_ts)
        return tuple(
            sorted(
                (
                    record
                    for record in self.feature_records
                    if (not requested or record.instrument_id in requested)
                    and record.horizon == horizon
                    and record.timestamp
                    and start <= parse_utc_iso(record.timestamp) <= end
                ),
                key=lambda item: (item.timestamp, item.instrument_id, item.metric_name, item.feature_id),
            )
        )

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        requested = ref_tail(weights_profile_id)
        for profile in self.weights_profiles:
            if profile.weights_profile_id == requested:
                return profile
        return None

    def list_metric_weight_rules(self, weights_profile_id: str, horizon: str) -> tuple[MetricWeightRule, ...]:
        requested = ref_tail(weights_profile_id)
        return tuple(
            sorted(
                (
                    rule
                    for rule in self.metric_weight_rules
                    if rule.weights_profile_id == requested and rule.horizon == horizon
                ),
                key=lambda item: item.metric_weight_rule_id,
            )
        )

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        requested = ref_tail(risk_policy_id)
        for policy in self.risk_policies:
            if policy.risk_policy_id == requested:
                return policy
        return None

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        requested = ref_tail(risk_policy_id)
        requested_ids = set(instrument_ids)
        return tuple(
            sorted(
                (
                    limit
                    for limit in self.instrument_limits
                    if limit.risk_policy_id == requested and (not requested_ids or limit.instrument_id in requested_ids)
                ),
                key=lambda item: item.instrument_id,
            )
        )

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        requested = ref_tail(risk_policy_id)
        return tuple(sorted((limit for limit in self.portfolio_limits if limit.risk_policy_id == requested), key=lambda item: item.limit_name))

    def list_corporate_actions(
        self,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[CorporateActionRecord, ...]:
        del from_ts
        requested = set(instrument_ids)
        end_date = parse_utc_iso(to_ts).date()
        return tuple(
            sorted(
                (
                    action
                    for action in self.corporate_actions
                    if (not requested or action.instrument_id in requested)
                    and action.effective_date
                    and parse_utc_iso(f"{action.effective_date}T00:00:00+00:00").date() <= end_date
                ),
                key=lambda item: (item.effective_date or "", item.instrument_id, item.corporate_action_record_id),
            )
        )

    def save_simulation_report(self, report: SimulationReportRecord) -> str:
        self.saved_simulation_reports.append(report)
        return f"audit.research_report:{report.simulation_report_id}"

    def save_paper_execution_result(self, execution_result: PaperExecutionResultRecord) -> str:
        self.saved_paper_execution_results.append(execution_result)
        return f"orders.execution_result:{execution_result.execution_result_id}"


class PostgresBacktestingPaperTradingRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_market_bars(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketBar, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_candle_id::text, instrument_id, open_ts,
                           COALESCE(close_ts, open_ts) AS close_ts,
                           close_price, volume, turnover, source_payload
                     FROM raw_market.raw_candle
                     WHERE (%s = '' OR universe_id = %s OR universe_id IS NULL)
                       AND (cardinality(%s::text[]) = 0 OR instrument_id = ANY(%s::text[]))
                       AND open_ts >= %s
                       AND open_ts <= %s
                       AND close_price IS NOT NULL
                     ORDER BY open_ts, instrument_id, raw_candle_id
                    """,
                    (
                        universe_id,
                        universe_id,
                        list(instrument_ids),
                        list(instrument_ids),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(_market_bar_from_row(row) for row in rows)

    def list_feature_vectors(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureVector, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT feature_vector_id, instrument_id, horizon, as_of_ts,
                           features, coverage_ratio, data_quality_score, build_version
                      FROM features.feature_vector
                     WHERE (cardinality(%s::text[]) = 0 OR instrument_id = ANY(%s::text[]))
                       AND horizon = %s
                       AND as_of_ts >= %s
                       AND as_of_ts <= %s
                     ORDER BY as_of_ts, instrument_id, feature_vector_id
                    """,
                    (
                        list(instrument_ids),
                        list(instrument_ids),
                        horizon,
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(_feature_vector_from_row(row) for row in rows)

    def list_feature_records(
        self,
        instrument_ids: tuple[str, ...],
        horizon: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT feature_id, instrument_id, metric_name, metric_group,
                           metric_type, raw_value, normalized_value, unit,
                           horizon, contour, timestamp, ttl_seconds,
                           confidence_score, source_module, source_refs,
                           calculation_version, quality_flags, payload
                      FROM features.feature_record
                     WHERE (cardinality(%s::text[]) = 0 OR instrument_id = ANY(%s::text[]))
                       AND horizon = %s
                       AND timestamp >= %s
                       AND timestamp <= %s
                     ORDER BY timestamp, instrument_id, metric_name, feature_id
                    """,
                    (
                        list(instrument_ids),
                        list(instrument_ids),
                        horizon,
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                    ),
                )
                rows = cur.fetchall()
        return tuple(_feature_record_from_row(row) for row in rows)

    def get_weights_profile(self, weights_profile_id: str) -> WeightsProfile | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT weights_profile_id, profile_name, version, status,
                           horizon, run_mode_allowed, validation_report_ref
                      FROM weights.weights_profile
                     WHERE weights_profile_id = %s
                    """,
                    (ref_tail(weights_profile_id),),
                )
                row = cur.fetchone()
        return _weights_profile_from_row(row) if row else None

    def list_metric_weight_rules(self, weights_profile_id: str, horizon: str) -> tuple[MetricWeightRule, ...]:
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
                     ORDER BY metric_weight_rule_id
                    """,
                    (ref_tail(weights_profile_id), horizon),
                )
                rows = cur.fetchall()
        return tuple(_metric_weight_rule_from_row(row) for row in rows)

    def get_risk_policy(self, risk_policy_id: str) -> RiskPolicy | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_policy_id, policy_name, version, status,
                           run_mode_allowed, rules
                      FROM risk.risk_policy
                     WHERE risk_policy_id = %s
                    """,
                    (ref_tail(risk_policy_id),),
                )
                row = cur.fetchone()
        return _risk_policy_from_row(row) if row else None

    def list_instrument_limits(self, risk_policy_id: str, instrument_ids: tuple[str, ...]) -> tuple[InstrumentLimit, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_policy_id, instrument_id, max_position_pct,
                           max_order_value_rub, max_slippage_bps, payload
                      FROM risk.instrument_limit
                     WHERE risk_policy_id = %s
                       AND (cardinality(%s::text[]) = 0 OR instrument_id = ANY(%s::text[]))
                     ORDER BY instrument_id
                    """,
                    (ref_tail(risk_policy_id), list(instrument_ids), list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(_instrument_limit_from_row(row) for row in rows)

    def list_portfolio_limits(self, risk_policy_id: str) -> tuple[PortfolioLimit, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT risk_policy_id, limit_name, limit_value, payload
                      FROM risk.portfolio_limit
                     WHERE risk_policy_id = %s
                     ORDER BY limit_name
                    """,
                    (ref_tail(risk_policy_id),),
                )
                rows = cur.fetchall()
        return tuple(_portfolio_limit_from_row(row) for row in rows)

    def list_corporate_actions(
        self,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[CorporateActionRecord, ...]:
        del from_ts
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT corporate_action_record_id::text, instrument_id,
                           action_type, effective_date, adjustment_factor, payload
                      FROM events.corporate_action_record
                     WHERE (cardinality(%s::text[]) = 0 OR instrument_id = ANY(%s::text[]))
                       AND effective_date <= %s
                     ORDER BY effective_date, instrument_id, corporate_action_record_id
                    """,
                    (list(instrument_ids), list(instrument_ids), parse_utc_iso(to_ts).date()),
                )
                rows = cur.fetchall()
        return tuple(_corporate_action_from_row(row) for row in rows)

    def save_simulation_report(self, report: SimulationReportRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.research_report (
                        report_type, universe_id, horizon, as_of_ts, payload
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING research_report_id
                    """,
                    (
                        "simulation_report",
                        report.universe_id,
                        report.horizon,
                        parse_utc_iso(report.as_of_ts),
                        Jsonb(dict(report.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.research_report:{row[0]}"

    def save_paper_execution_result(self, execution_result: PaperExecutionResultRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO orders.execution_result (
                        execution_result_id, order_intent_id, status,
                        broker_order_id, submitted_at, last_update_at,
                        filled_quantity, avg_fill_price, fees, slippage_bps,
                        errors, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (execution_result_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        last_update_at = EXCLUDED.last_update_at,
                        filled_quantity = EXCLUDED.filled_quantity,
                        avg_fill_price = EXCLUDED.avg_fill_price,
                        fees = EXCLUDED.fees,
                        slippage_bps = EXCLUDED.slippage_bps,
                        errors = EXCLUDED.errors,
                        payload = EXCLUDED.payload
                    """,
                    (
                        execution_result.execution_result_id,
                        execution_result.order_intent_id,
                        execution_result.status,
                        None,
                        parse_utc_iso(execution_result.submitted_at),
                        parse_utc_iso(execution_result.last_update_at),
                        execution_result.filled_quantity,
                        execution_result.avg_fill_price,
                        execution_result.fees,
                        execution_result.slippage_bps,
                        list(execution_result.errors),
                        Jsonb(dict(execution_result.payload)),
                    ),
                )
        return f"orders.execution_result:{execution_result.execution_result_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def ref_tail(ref: str | None) -> str:
    value = str(ref or "")
    return value.rsplit(":", 1)[-1] if ":" in value else value


def _market_bar_from_row(row: tuple[Any, ...]) -> MarketBar:
    return MarketBar(
        source_ref=f"raw_market.raw_candle:{row[0]}",
        instrument_id=row[1] or "",
        open_ts=_iso(row[2]),
        close_ts=_iso(row[3]),
        close_price=_optional_float(row[4]) or 0.0,
        volume=_optional_float(row[5]) or 0.0,
        turnover=_optional_float(row[6]) or 0.0,
        payload=row[7] or {},
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


def _feature_record_from_row(row: tuple[Any, ...]) -> FeatureRecord:
    return FeatureRecord(
        feature_id=row[0] or "",
        instrument_id=row[1] or "",
        metric_name=row[2] or "",
        metric_group=row[3] or "",
        metric_type=row[4] or "",
        raw_value=_optional_float(row[5]),
        normalized_value=_optional_float(row[6]),
        unit=row[7],
        horizon=row[8] or "",
        contour=row[9] or "",
        timestamp=_iso(row[10]),
        ttl_seconds=int(row[11]) if row[11] is not None else None,
        confidence_score=_optional_float(row[12]),
        source_module=row[13] or "",
        source_refs=tuple(row[14] or ()),
        calculation_version=row[15] or "",
        quality_flags=tuple(row[16] or ()),
        payload=row[17] or {},
    )


def _weights_profile_from_row(row: tuple[Any, ...]) -> WeightsProfile:
    return WeightsProfile(
        weights_profile_id=row[0] or "",
        profile_name=row[1] or "",
        version=row[2] or "",
        status=row[3] or "",
        horizon=row[4] or "",
        run_mode_allowed=tuple(row[5] or ()),
        validation_report_ref=row[6],
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
        min_confidence_score=_optional_float(row[11]),
        stale_policy=row[12] or "block",
        calculation_version=row[13] or "",
    )


def _risk_policy_from_row(row: tuple[Any, ...]) -> RiskPolicy:
    return RiskPolicy(
        risk_policy_id=row[0] or "",
        policy_name=row[1] or "",
        version=row[2] or "",
        status=row[3] or "",
        run_mode_allowed=tuple(row[4] or ()),
        rules=row[5] or {},
    )


def _instrument_limit_from_row(row: tuple[Any, ...]) -> InstrumentLimit:
    return InstrumentLimit(
        risk_policy_id=row[0] or "",
        instrument_id=row[1] or "",
        max_position_pct=_optional_float(row[2]),
        max_order_value_rub=_optional_float(row[3]),
        max_slippage_bps=_optional_float(row[4]),
        payload=row[5] or {},
    )


def _portfolio_limit_from_row(row: tuple[Any, ...]) -> PortfolioLimit:
    return PortfolioLimit(
        risk_policy_id=row[0] or "",
        limit_name=row[1] or "",
        limit_value=_optional_float(row[2]),
        payload=row[3] or {},
    )


def _corporate_action_from_row(row: tuple[Any, ...]) -> CorporateActionRecord:
    return CorporateActionRecord(
        corporate_action_record_id=row[0] or "",
        instrument_id=row[1] or "",
        action_type=row[2] or "",
        effective_date=str(row[3]) if row[3] is not None else None,
        adjustment_factor=_optional_float(row[4]),
        payload=row[5] or {},
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
