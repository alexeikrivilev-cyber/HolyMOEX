from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float | None
    normalized_value: float | None
    unit: str
    horizon: str
    contour: str
    timestamp: str
    ttl_seconds: int | None
    confidence_score: float
    source_module: str
    source_refs: tuple[str, ...]
    calculation_version: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FeatureRecord":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        confidence = _optional_float(payload.get("confidence_score"))
        return cls(
            feature_id=str(payload.get("feature_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            metric_name=str(payload.get("metric_name") or ""),
            metric_group=str(payload.get("metric_group") or ""),
            metric_type=str(payload.get("metric_type") or ""),
            raw_value=_optional_float(payload.get("raw_value")),
            normalized_value=_optional_float(payload.get("normalized_value")),
            unit=str(payload.get("unit") or ""),
            horizon=str(payload.get("horizon") or ""),
            contour=str(payload.get("contour") or ""),
            timestamp=str(payload.get("timestamp") or ""),
            ttl_seconds=_optional_int(payload.get("ttl_seconds")),
            confidence_score=1.0 if confidence is None else confidence,
            source_module=str(payload.get("source_module") or ""),
            source_refs=_string_tuple(payload.get("source_refs")),
            calculation_version=str(payload.get("calculation_version") or ""),
            quality_flags=_string_tuple(payload.get("quality_flags")),
            payload=source_payload,
        )

    @property
    def value(self) -> float | None:
        return self.normalized_value if self.normalized_value is not None else self.raw_value


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
class OrderExecutionRecord:
    order_intent_id: str
    instrument_id: str
    side: str
    quantity: float | None
    run_mode: str
    created_at: str
    execution_result_id: str | None = None
    status: str | None = None
    submitted_at: str | None = None
    last_update_at: str | None = None
    filled_quantity: float | None = None
    avg_fill_price: float | None = None
    fees: float | None = None
    slippage_bps: float | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OrderExecutionRecord":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            order_intent_id=str(payload.get("order_intent_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            side=str(payload.get("side") or ""),
            quantity=_optional_float(payload.get("quantity")),
            run_mode=str(payload.get("run_mode") or ""),
            created_at=str(payload.get("created_at") or payload.get("submitted_at") or ""),
            execution_result_id=_optional_text(payload.get("execution_result_id")),
            status=_optional_text(payload.get("status")),
            submitted_at=_optional_text(payload.get("submitted_at")),
            last_update_at=_optional_text(payload.get("last_update_at")),
            filled_quantity=_optional_float(payload.get("filled_quantity")),
            avg_fill_price=_optional_float(payload.get("avg_fill_price")),
            fees=_optional_float(payload.get("fees")),
            slippage_bps=_optional_float(payload.get("slippage_bps")),
            payload=source_payload,
        )


@dataclass(frozen=True)
class ResearchReportRecord:
    validation_report_id: str
    report_type: str
    universe_id: str
    horizon: str | None
    as_of_ts: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class WeightsProfileDraft:
    weights_profile_id: str
    profile_name: str
    version: str
    horizon: str
    run_mode_allowed: tuple[str, ...]
    validation_report_ref: str
    status: str = "draft"
    approved_by: str | None = None


@dataclass(frozen=True)
class MetricWeightRuleDraft:
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


class FeatureValidationResearchRepository(Protocol):
    def list_feature_records(
        self,
        feature_set_ref: str,
        return_targets: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        ...

    def list_market_state_records(
        self,
        universe_id: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketStateRecord, ...]:
        ...

    def list_order_execution_records(
        self,
        from_ts: str,
        to_ts: str,
    ) -> tuple[OrderExecutionRecord, ...]:
        ...

    def save_research_report(self, report: ResearchReportRecord) -> str:
        ...

    def save_weights_profile_draft(self, profile: WeightsProfileDraft) -> str:
        ...

    def save_metric_weight_rule_draft(self, rule: MetricWeightRuleDraft) -> str:
        ...


class InMemoryFeatureValidationResearchRepository:
    def __init__(
        self,
        feature_records: tuple[Mapping[str, Any] | FeatureRecord, ...] = (),
        market_state_records: tuple[Mapping[str, Any] | MarketStateRecord, ...] = (),
        order_execution_records: tuple[Mapping[str, Any] | OrderExecutionRecord, ...] = (),
    ) -> None:
        self.feature_records = tuple(
            record if isinstance(record, FeatureRecord) else FeatureRecord.from_mapping(record)
            for record in feature_records
        )
        self.market_state_records = tuple(
            record if isinstance(record, MarketStateRecord) else MarketStateRecord.from_mapping(record)
            for record in market_state_records
        )
        self.order_execution_records = tuple(
            record if isinstance(record, OrderExecutionRecord) else OrderExecutionRecord.from_mapping(record)
            for record in order_execution_records
        )
        self.saved_research_reports: list[ResearchReportRecord] = []
        self.saved_weight_profiles: list[WeightsProfileDraft] = []
        self.saved_metric_weight_rules: list[MetricWeightRuleDraft] = []

    def list_feature_records(
        self,
        feature_set_ref: str,
        return_targets: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        requested_horizons = set(horizons)
        targets = set(return_targets)
        records = [
            record
            for record in self.feature_records
            if record.timestamp
            and record.horizon in requested_horizons
            and from_dt <= parse_utc_iso(record.timestamp) <= to_dt
            and (record.metric_name in targets or _matches_feature_set_ref(record, feature_set_ref))
        ]
        return tuple(sorted(records, key=lambda item: (item.horizon, item.metric_name, item.instrument_id, item.timestamp, item.feature_id)))

    def list_market_state_records(
        self,
        universe_id: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketStateRecord, ...]:
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        records = [
            record
            for record in self.market_state_records
            if record.as_of_ts
            and from_dt <= parse_utc_iso(record.as_of_ts) <= to_dt
            and (record.universe_id in (None, "", universe_id))
        ]
        return tuple(sorted(records, key=lambda item: (item.as_of_ts, item.market_state_record_id)))

    def list_order_execution_records(
        self,
        from_ts: str,
        to_ts: str,
    ) -> tuple[OrderExecutionRecord, ...]:
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        records = [
            record
            for record in self.order_execution_records
            if record.created_at and from_dt <= parse_utc_iso(record.created_at) <= to_dt
        ]
        return tuple(sorted(records, key=lambda item: (item.created_at, item.order_intent_id)))

    def save_research_report(self, report: ResearchReportRecord) -> str:
        self.saved_research_reports.append(report)
        return f"audit.research_report:{report.validation_report_id}"

    def save_weights_profile_draft(self, profile: WeightsProfileDraft) -> str:
        if profile.status != "draft" or profile.approved_by is not None:
            raise ValueError("Feature Validation & Research Module can write only draft weights profiles")
        self.saved_weight_profiles.append(profile)
        return f"weights.weights_profile:{profile.weights_profile_id}"

    def save_metric_weight_rule_draft(self, rule: MetricWeightRuleDraft) -> str:
        profile = next(
            (item for item in self.saved_weight_profiles if item.weights_profile_id == rule.weights_profile_id),
            None,
        )
        if profile is None or profile.status != "draft" or profile.approved_by is not None:
            raise ValueError("metric weight rules can be written only for draft weights profiles")
        self.saved_metric_weight_rules.append(rule)
        return f"weights.metric_weight_rule:{rule.metric_weight_rule_id}"


class PostgresFeatureValidationResearchRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_feature_records(
        self,
        feature_set_ref: str,
        return_targets: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        ref_tail = _ref_tail(feature_set_ref)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT feature_id, instrument_id, metric_name, metric_group,
                           metric_type, raw_value, normalized_value, unit, horizon,
                           contour, timestamp, ttl_seconds, confidence_score,
                           source_module, source_refs, calculation_version,
                           quality_flags, payload
                      FROM features.feature_record
                     WHERE horizon = ANY(%s)
                       AND timestamp BETWEEN %s AND %s
                       AND (
                            metric_name = ANY(%s)
                            OR %s = ''
                            OR feature_id IN (%s, %s)
                            OR %s = ANY(source_refs)
                            OR %s = ANY(source_refs)
                            OR calculation_version = %s
                            OR payload ->> 'feature_set_ref' = %s
                            OR payload ->> 'feature_set_id' = %s
                       )
                     ORDER BY horizon, metric_name, instrument_id, timestamp, feature_id
                    """,
                    (
                        list(horizons),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(to_ts),
                        list(return_targets),
                        feature_set_ref,
                        feature_set_ref,
                        ref_tail,
                        feature_set_ref,
                        ref_tail,
                        feature_set_ref,
                        feature_set_ref,
                        ref_tail,
                    ),
                )
                rows = cur.fetchall()
        return tuple(_feature_record_from_row(row) for row in rows)

    def list_market_state_records(
        self,
        universe_id: str,
        from_ts: str,
        to_ts: str,
    ) -> tuple[MarketStateRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT market_state_record_id, universe_id, as_of_ts,
                           market_session_status, market_regime, payload
                      FROM features.market_state_record
                     WHERE as_of_ts BETWEEN %s AND %s
                       AND (universe_id = %s OR universe_id IS NULL)
                     ORDER BY as_of_ts, market_state_record_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts), universe_id),
                )
                rows = cur.fetchall()
        return tuple(_market_state_record_from_row(row) for row in rows)

    def list_order_execution_records(
        self,
        from_ts: str,
        to_ts: str,
    ) -> tuple[OrderExecutionRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT oi.order_intent_id, oi.instrument_id, oi.side,
                           oi.quantity, oi.run_mode, oi.created_at,
                           er.execution_result_id, er.status, er.submitted_at,
                           er.last_update_at, er.filled_quantity, er.avg_fill_price,
                           er.fees, er.slippage_bps,
                           COALESCE(er.payload, oi.payload)
                      FROM orders.order_intent oi
                      LEFT JOIN orders.execution_result er
                        ON er.order_intent_id = oi.order_intent_id
                     WHERE oi.created_at BETWEEN %s AND %s
                     ORDER BY oi.created_at, oi.order_intent_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_order_execution_record_from_row(row) for row in rows)

    def save_research_report(self, report: ResearchReportRecord) -> str:
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
                        report.report_type,
                        report.universe_id,
                        report.horizon,
                        parse_utc_iso(report.as_of_ts),
                        Jsonb(dict(report.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.research_report:{row[0]}"

    def save_weights_profile_draft(self, profile: WeightsProfileDraft) -> str:
        if profile.status != "draft" or profile.approved_by is not None:
            raise ValueError("Feature Validation & Research Module can write only unapproved draft weights profiles")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO weights.weights_profile (
                        weights_profile_id, profile_name, version, status,
                        horizon, run_mode_allowed, approved_by,
                        validation_report_ref
                    ) VALUES (%s, %s, %s, 'draft', %s, %s, NULL, %s)
                    ON CONFLICT (weights_profile_id) DO UPDATE SET
                        profile_name = EXCLUDED.profile_name,
                        version = EXCLUDED.version,
                        run_mode_allowed = EXCLUDED.run_mode_allowed,
                        validation_report_ref = EXCLUDED.validation_report_ref
                    WHERE weights.weights_profile.status = 'draft'
                    """,
                    (
                        profile.weights_profile_id,
                        profile.profile_name,
                        profile.version,
                        profile.horizon,
                        list(profile.run_mode_allowed),
                        profile.validation_report_ref,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("active weights profiles cannot be updated by research module")
        return f"weights.weights_profile:{profile.weights_profile_id}"

    def save_metric_weight_rule_draft(self, rule: MetricWeightRuleDraft) -> str:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO weights.metric_weight_rule (
                        metric_weight_rule_id, weights_profile_id, metric_name,
                        metric_group, horizon, instrument_scope, instrument_ids,
                        sector, weight, direction, transform,
                        min_confidence_score, stale_policy, calculation_version
                    )
                    SELECT %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    WHERE EXISTS (
                        SELECT 1
                          FROM weights.weights_profile profile
                         WHERE profile.weights_profile_id = %s
                           AND profile.status = 'draft'
                    )
                    ON CONFLICT (metric_weight_rule_id) DO UPDATE SET
                        metric_group = EXCLUDED.metric_group,
                        instrument_scope = EXCLUDED.instrument_scope,
                        instrument_ids = EXCLUDED.instrument_ids,
                        sector = EXCLUDED.sector,
                        weight = EXCLUDED.weight,
                        direction = EXCLUDED.direction,
                        transform = EXCLUDED.transform,
                        min_confidence_score = EXCLUDED.min_confidence_score,
                        stale_policy = EXCLUDED.stale_policy,
                        calculation_version = EXCLUDED.calculation_version
                    WHERE EXISTS (
                        SELECT 1
                          FROM weights.weights_profile profile
                         WHERE profile.weights_profile_id = EXCLUDED.weights_profile_id
                           AND profile.status = 'draft'
                    )
                    """,
                    (
                        rule.metric_weight_rule_id,
                        rule.weights_profile_id,
                        rule.metric_name,
                        rule.metric_group,
                        rule.horizon,
                        rule.instrument_scope,
                        list(rule.instrument_ids),
                        rule.sector,
                        rule.weight,
                        rule.direction,
                        rule.transform,
                        rule.min_confidence_score,
                        rule.stale_policy,
                        rule.calculation_version,
                        rule.weights_profile_id,
                    ),
                )
                if cur.rowcount == 0:
                    raise ValueError("metric weight rules can be written only for draft weights profiles")
        return f"weights.metric_weight_rule:{rule.metric_weight_rule_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _feature_record_from_row(row: tuple[Any, ...]) -> FeatureRecord:
    confidence = _optional_float(row[12])
    return FeatureRecord(
        feature_id=str(row[0]),
        instrument_id=row[1] or "",
        metric_name=row[2] or "",
        metric_group=row[3] or "",
        metric_type=row[4] or "",
        raw_value=_optional_float(row[5]),
        normalized_value=_optional_float(row[6]),
        unit=row[7] or "",
        horizon=row[8] or "",
        contour=row[9] or "",
        timestamp=_iso(row[10]),
        ttl_seconds=_optional_int(row[11]),
        confidence_score=1.0 if confidence is None else confidence,
        source_module=row[13] or "",
        source_refs=tuple(row[14] or ()),
        calculation_version=row[15] or "",
        quality_flags=tuple(row[16] or ()),
        payload=row[17] or {},
    )


def _market_state_record_from_row(row: tuple[Any, ...]) -> MarketStateRecord:
    return MarketStateRecord(
        market_state_record_id=str(row[0]),
        universe_id=row[1],
        as_of_ts=_iso(row[2]),
        market_session_status=row[3],
        market_regime=row[4],
        payload=row[5] or {},
    )


def _order_execution_record_from_row(row: tuple[Any, ...]) -> OrderExecutionRecord:
    return OrderExecutionRecord(
        order_intent_id=row[0] or "",
        instrument_id=row[1] or "",
        side=row[2] or "",
        quantity=_optional_float(row[3]),
        run_mode=row[4] or "",
        created_at=_iso(row[5]),
        execution_result_id=_optional_text(row[6]),
        status=_optional_text(row[7]),
        submitted_at=_iso(row[8]) if row[8] is not None else None,
        last_update_at=_iso(row[9]) if row[9] is not None else None,
        filled_quantity=_optional_float(row[10]),
        avg_fill_price=_optional_float(row[11]),
        fees=_optional_float(row[12]),
        slippage_bps=_optional_float(row[13]),
        payload=row[14] or {},
    )


def _matches_feature_set_ref(record: FeatureRecord, feature_set_ref: str) -> bool:
    if not feature_set_ref:
        return True
    ref_tail = _ref_tail(feature_set_ref)
    return (
        record.feature_id in {feature_set_ref, ref_tail}
        or feature_set_ref in record.source_refs
        or ref_tail in record.source_refs
        or record.calculation_version == feature_set_ref
        or record.payload.get("feature_set_ref") == feature_set_ref
        or record.payload.get("feature_set_id") == ref_tail
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


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
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
