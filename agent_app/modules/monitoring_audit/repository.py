from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class AuditLogRecord:
    audit_record_id: str
    module_name: str
    job_id: str
    severity: str
    event_type: str
    message: str
    object_type: str
    object_ref: str
    reason_codes: tuple[str, ...]
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AuditLogRecord":
        return cls(
            audit_record_id=str(payload.get("audit_record_id") or payload.get("id") or ""),
            module_name=str(payload.get("module_name") or ""),
            job_id=str(payload.get("job_id") or ""),
            severity=str(payload.get("severity") or "info"),
            event_type=str(payload.get("event_type") or ""),
            message=str(payload.get("message") or ""),
            object_type=str(payload.get("object_type") or ""),
            object_ref=str(payload.get("object_ref") or ""),
            reason_codes=_string_tuple(payload.get("reason_codes")),
            created_at=str(payload.get("created_at") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class RequestLogRecord:
    request_id: str
    caller_module: str
    provider: str
    request_type: str
    status: str
    latency_ms: float | None
    cost_units: float
    cache_hit: bool
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RequestLogRecord":
        return cls(
            request_id=str(payload.get("request_id") or payload.get("external_request_log_id") or ""),
            caller_module=str(payload.get("caller_module") or ""),
            provider=str(payload.get("provider") or ""),
            request_type=str(payload.get("request_type") or ""),
            status=str(payload.get("status") or ""),
            latency_ms=_optional_float(payload.get("latency_ms")),
            cost_units=_optional_float(payload.get("cost_units")) or 0.0,
            cache_hit=bool(payload.get("cache_hit", False)),
            created_at=str(payload.get("created_at") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class ModuleJobResultRecord:
    job_id: str
    module_name: str
    status: str
    started_at: str
    finished_at: str
    output_refs: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    metrics_written: int
    events_written: int
    data_quality_score: float | None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ModuleJobResultRecord":
        return cls(
            job_id=str(payload.get("job_id") or ""),
            module_name=str(payload.get("module_name") or ""),
            status=str(payload.get("status") or ""),
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            output_refs=_string_tuple(payload.get("output_refs")),
            warnings=_string_tuple(payload.get("warnings")),
            errors=_string_tuple(payload.get("errors")),
            metrics_written=int(payload.get("metrics_written") or 0),
            events_written=int(payload.get("events_written") or 0),
            data_quality_score=_optional_float(payload.get("data_quality_score")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class DecisionRecord:
    decision_record_id: str
    decision_set_id: str
    instrument_id: str
    action: str
    confidence_score: float | None
    risk_score: float | None
    primary_reason_codes: tuple[str, ...]
    created_at: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DecisionRecord":
        return cls(
            decision_record_id=str(payload.get("decision_record_id") or payload.get("id") or ""),
            decision_set_id=str(payload.get("decision_set_id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            action=str(payload.get("action") or ""),
            confidence_score=_optional_float(payload.get("confidence_score")),
            risk_score=_optional_float(payload.get("risk_score")),
            primary_reason_codes=_string_tuple(payload.get("primary_reason_codes")),
            created_at=str(payload.get("created_at") or ""),
        )


@dataclass(frozen=True)
class DecisionExplanationRecord:
    decision_explanation_id: str
    decision_record_id: str
    created_at: str
    explanation: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DecisionExplanationRecord":
        return cls(
            decision_explanation_id=str(payload.get("decision_explanation_id") or payload.get("id") or ""),
            decision_record_id=str(payload.get("decision_record_id") or ""),
            created_at=str(payload.get("created_at") or ""),
            explanation=_mapping(payload.get("explanation")),
        )


@dataclass(frozen=True)
class ExecutionResultRecord:
    execution_result_id: str
    order_intent_id: str
    status: str
    submitted_at: str
    last_update_at: str
    fees: float
    slippage_bps: float | None
    errors: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutionResultRecord":
        return cls(
            execution_result_id=str(payload.get("execution_result_id") or payload.get("id") or ""),
            order_intent_id=str(payload.get("order_intent_id") or ""),
            status=str(payload.get("status") or ""),
            submitted_at=str(payload.get("submitted_at") or ""),
            last_update_at=str(payload.get("last_update_at") or payload.get("submitted_at") or ""),
            fees=_optional_float(payload.get("fees")) or 0.0,
            slippage_bps=_optional_float(payload.get("slippage_bps")),
            errors=_string_tuple(payload.get("errors")),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class OrderStatusRecord:
    order_status_id: str
    order_intent_id: str
    status: str
    status_ts: str
    provider: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "OrderStatusRecord":
        return cls(
            order_status_id=str(payload.get("order_status_id") or payload.get("id") or ""),
            order_intent_id=str(payload.get("order_intent_id") or ""),
            status=str(payload.get("status") or ""),
            status_ts=str(payload.get("status_ts") or ""),
            provider=str(payload.get("provider") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class PortfolioSnapshotRecord:
    portfolio_snapshot_id: str
    portfolio_id: str
    universe_id: str
    as_of_ts: str
    equity: float | None
    gross_exposure: float | None
    net_exposure: float | None
    source_module: str
    created_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PortfolioSnapshotRecord":
        return cls(
            portfolio_snapshot_id=str(payload.get("portfolio_snapshot_id") or payload.get("id") or ""),
            portfolio_id=str(payload.get("portfolio_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            as_of_ts=str(payload.get("as_of_ts") or ""),
            equity=_optional_float(payload.get("equity")),
            gross_exposure=_optional_float(payload.get("gross_exposure")),
            net_exposure=_optional_float(payload.get("net_exposure")),
            source_module=str(payload.get("source_module") or ""),
            created_at=str(payload.get("created_at") or ""),
            payload=_mapping(payload.get("payload")),
        )


@dataclass(frozen=True)
class MonitoringRecord:
    monitoring_record_id: str
    check_name: str
    status: str
    severity: str
    observed_at: str
    payload: Mapping[str, Any]

    def to_ref(self) -> str:
        return f"audit.monitoring_record:{self.monitoring_record_id}"


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
    payload: Mapping[str, Any]

    def to_ref(self) -> str:
        return f"audit.audit_record:{self.audit_record_id}"


class MonitoringAuditRepository(Protocol):
    def list_audit_records(self, from_ts: str, to_ts: str) -> tuple[AuditLogRecord, ...]:
        ...

    def list_request_logs(self, from_ts: str, to_ts: str) -> tuple[RequestLogRecord, ...]:
        ...

    def list_module_job_results(self, from_ts: str, to_ts: str) -> tuple[ModuleJobResultRecord, ...]:
        ...

    def list_decision_records(self, from_ts: str, to_ts: str) -> tuple[DecisionRecord, ...]:
        ...

    def list_decision_explanations(self, from_ts: str, to_ts: str) -> tuple[DecisionExplanationRecord, ...]:
        ...

    def list_execution_results(self, from_ts: str, to_ts: str) -> tuple[ExecutionResultRecord, ...]:
        ...

    def list_order_statuses(self, from_ts: str, to_ts: str) -> tuple[OrderStatusRecord, ...]:
        ...

    def list_portfolio_snapshots(self, from_ts: str, to_ts: str) -> tuple[PortfolioSnapshotRecord, ...]:
        ...

    def save_monitoring_record(self, record: MonitoringRecord) -> str:
        ...

    def save_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryMonitoringAuditRepository:
    def __init__(
        self,
        audit_records: tuple[Mapping[str, Any] | AuditLogRecord, ...] = (),
        request_logs: tuple[Mapping[str, Any] | RequestLogRecord, ...] = (),
        decision_records: tuple[Mapping[str, Any] | DecisionRecord, ...] = (),
        decision_explanations: tuple[Mapping[str, Any] | DecisionExplanationRecord, ...] = (),
        execution_results: tuple[Mapping[str, Any] | ExecutionResultRecord, ...] = (),
        order_statuses: tuple[Mapping[str, Any] | OrderStatusRecord, ...] = (),
        portfolio_snapshots: tuple[Mapping[str, Any] | PortfolioSnapshotRecord, ...] = (),
        module_job_results: tuple[Mapping[str, Any] | ModuleJobResultRecord, ...] = (),
    ) -> None:
        self.audit_records = tuple(
            record if isinstance(record, AuditLogRecord) else AuditLogRecord.from_mapping(record)
            for record in audit_records
        )
        self.request_logs = tuple(
            record if isinstance(record, RequestLogRecord) else RequestLogRecord.from_mapping(record)
            for record in request_logs
        )
        self.module_job_results = tuple(
            record if isinstance(record, ModuleJobResultRecord) else ModuleJobResultRecord.from_mapping(record)
            for record in module_job_results
        )
        self.decision_records = tuple(
            record if isinstance(record, DecisionRecord) else DecisionRecord.from_mapping(record)
            for record in decision_records
        )
        self.decision_explanations = tuple(
            record if isinstance(record, DecisionExplanationRecord) else DecisionExplanationRecord.from_mapping(record)
            for record in decision_explanations
        )
        self.execution_results = tuple(
            record if isinstance(record, ExecutionResultRecord) else ExecutionResultRecord.from_mapping(record)
            for record in execution_results
        )
        self.order_statuses = tuple(
            record if isinstance(record, OrderStatusRecord) else OrderStatusRecord.from_mapping(record)
            for record in order_statuses
        )
        self.portfolio_snapshots = tuple(
            record if isinstance(record, PortfolioSnapshotRecord) else PortfolioSnapshotRecord.from_mapping(record)
            for record in portfolio_snapshots
        )
        self.saved_monitoring_records: list[MonitoringRecord] = []
        self.saved_audit_records: list[AuditRecord] = []

    def list_audit_records(self, from_ts: str, to_ts: str) -> tuple[AuditLogRecord, ...]:
        return _filter_time(self.audit_records, from_ts, to_ts, lambda record: record.created_at)

    def list_request_logs(self, from_ts: str, to_ts: str) -> tuple[RequestLogRecord, ...]:
        return _filter_time(self.request_logs, from_ts, to_ts, lambda record: record.created_at)

    def list_module_job_results(self, from_ts: str, to_ts: str) -> tuple[ModuleJobResultRecord, ...]:
        return _filter_time(self.module_job_results, from_ts, to_ts, lambda record: record.finished_at)

    def list_decision_records(self, from_ts: str, to_ts: str) -> tuple[DecisionRecord, ...]:
        return _filter_time(self.decision_records, from_ts, to_ts, lambda record: record.created_at)

    def list_decision_explanations(self, from_ts: str, to_ts: str) -> tuple[DecisionExplanationRecord, ...]:
        return _filter_time(self.decision_explanations, from_ts, to_ts, lambda record: record.created_at)

    def list_execution_results(self, from_ts: str, to_ts: str) -> tuple[ExecutionResultRecord, ...]:
        return _filter_time(self.execution_results, from_ts, to_ts, lambda record: record.last_update_at or record.submitted_at)

    def list_order_statuses(self, from_ts: str, to_ts: str) -> tuple[OrderStatusRecord, ...]:
        return _filter_time(self.order_statuses, from_ts, to_ts, lambda record: record.status_ts)

    def list_portfolio_snapshots(self, from_ts: str, to_ts: str) -> tuple[PortfolioSnapshotRecord, ...]:
        return _filter_time(self.portfolio_snapshots, from_ts, to_ts, lambda record: record.as_of_ts or record.created_at)

    def save_monitoring_record(self, record: MonitoringRecord) -> str:
        self.saved_monitoring_records.append(record)
        return record.to_ref()

    def save_audit_record(self, record: AuditRecord) -> str:
        self.saved_audit_records.append(record)
        return record.to_ref()


class PostgresMonitoringAuditRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_audit_records(self, from_ts: str, to_ts: str) -> tuple[AuditLogRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT audit_record_id::text, module_name, job_id, severity,
                           event_type, message, object_type, object_ref,
                           reason_codes, created_at, payload
                      FROM audit.audit_record
                     WHERE created_at >= %s AND created_at <= %s
                     ORDER BY created_at, audit_record_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_audit_log_from_row(row) for row in rows)

    def list_request_logs(self, from_ts: str, to_ts: str) -> tuple[RequestLogRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(request_id, external_request_log_id::text),
                           caller_module, provider, request_type, status,
                           latency_ms, cost_units, cache_hit, created_at, payload
                      FROM request_logs.external_request_log
                     WHERE created_at >= %s AND created_at <= %s
                     ORDER BY created_at, external_request_log_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_request_log_from_row(row) for row in rows)

    def list_module_job_results(self, from_ts: str, to_ts: str) -> tuple[ModuleJobResultRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, module_name, status, started_at, finished_at,
                           output_refs, warnings, errors, metrics_written,
                           events_written, data_quality_score, payload
                      FROM audit.module_job_result
                     WHERE finished_at >= %s AND finished_at <= %s
                     ORDER BY finished_at, job_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_module_job_result_from_row(row) for row in rows)

    def list_decision_records(self, from_ts: str, to_ts: str) -> tuple[DecisionRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT decision_record_id::text, decision_set_id, instrument_id,
                           action, confidence_score, risk_score,
                           primary_reason_codes, created_at
                      FROM decisions.decision_record
                     WHERE created_at >= %s AND created_at <= %s
                     ORDER BY created_at, decision_record_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_decision_record_from_row(row) for row in rows)

    def list_decision_explanations(self, from_ts: str, to_ts: str) -> tuple[DecisionExplanationRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT decision_explanation_id::text, decision_record_id::text,
                           created_at, explanation
                      FROM decisions.decision_explanation
                     WHERE created_at >= %s AND created_at <= %s
                     ORDER BY created_at, decision_explanation_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_decision_explanation_from_row(row) for row in rows)

    def list_execution_results(self, from_ts: str, to_ts: str) -> tuple[ExecutionResultRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT execution_result_id, order_intent_id, status,
                           submitted_at, last_update_at, fees, slippage_bps,
                           errors, payload
                      FROM orders.execution_result
                     WHERE COALESCE(last_update_at, submitted_at) >= %s
                       AND COALESCE(last_update_at, submitted_at) <= %s
                     ORDER BY COALESCE(last_update_at, submitted_at), execution_result_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_execution_result_from_row(row) for row in rows)

    def list_order_statuses(self, from_ts: str, to_ts: str) -> tuple[OrderStatusRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT order_status_id::text, order_intent_id, status,
                           status_ts, provider, payload
                      FROM orders.order_status
                     WHERE status_ts >= %s AND status_ts <= %s
                     ORDER BY status_ts, order_status_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_order_status_from_row(row) for row in rows)

    def list_portfolio_snapshots(self, from_ts: str, to_ts: str) -> tuple[PortfolioSnapshotRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT portfolio_snapshot_id, portfolio_id, universe_id,
                           as_of_ts, equity, gross_exposure, net_exposure,
                           source_module, created_at, payload
                      FROM portfolio.portfolio_snapshot
                     WHERE as_of_ts >= %s AND as_of_ts <= %s
                     ORDER BY as_of_ts, portfolio_snapshot_id
                    """,
                    (parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(_portfolio_snapshot_from_row(row) for row in rows)

    def save_monitoring_record(self, record: MonitoringRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.monitoring_record (
                        check_name, status, severity, observed_at, payload
                    ) VALUES (%s, %s, %s, %s, %s)
                    RETURNING monitoring_record_id
                    """,
                    (
                        record.check_name,
                        record.status,
                        record.severity,
                        parse_utc_iso(record.observed_at),
                        Jsonb(dict(record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.monitoring_record:{row[0]}"

    def save_audit_record(self, record: AuditRecord) -> str:
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
                        record.module_name,
                        record.job_id,
                        record.severity,
                        record.event_type,
                        record.message,
                        record.object_type,
                        record.object_ref,
                        list(record.reason_codes),
                        Jsonb(dict(record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _filter_time(records: tuple[Any, ...], from_ts: str, to_ts: str, timestamp_getter: Any) -> tuple[Any, ...]:
    start = parse_utc_iso(from_ts)
    end = parse_utc_iso(to_ts)
    filtered = []
    for record in records:
        timestamp = timestamp_getter(record)
        if not timestamp:
            continue
        current = parse_utc_iso(timestamp)
        if start <= current <= end:
            filtered.append(record)
    return tuple(sorted(filtered, key=lambda record: timestamp_getter(record)))


def _audit_log_from_row(row: tuple[Any, ...]) -> AuditLogRecord:
    return AuditLogRecord(
        audit_record_id=row[0] or "",
        module_name=row[1] or "",
        job_id=row[2] or "",
        severity=row[3] or "info",
        event_type=row[4] or "",
        message=row[5] or "",
        object_type=row[6] or "",
        object_ref=row[7] or "",
        reason_codes=tuple(row[8] or ()),
        created_at=_iso(row[9]),
        payload=row[10] or {},
    )


def _request_log_from_row(row: tuple[Any, ...]) -> RequestLogRecord:
    return RequestLogRecord(
        request_id=row[0] or "",
        caller_module=row[1] or "",
        provider=row[2] or "",
        request_type=row[3] or "",
        status=row[4] or "",
        latency_ms=_optional_float(row[5]),
        cost_units=_optional_float(row[6]) or 0.0,
        cache_hit=bool(row[7]),
        created_at=_iso(row[8]),
        payload=row[9] or {},
    )


def _module_job_result_from_row(row: tuple[Any, ...]) -> ModuleJobResultRecord:
    return ModuleJobResultRecord(
        job_id=row[0] or "",
        module_name=row[1] or "",
        status=row[2] or "",
        started_at=_iso(row[3]),
        finished_at=_iso(row[4]),
        output_refs=tuple(row[5] or ()),
        warnings=tuple(row[6] or ()),
        errors=tuple(row[7] or ()),
        metrics_written=int(row[8] or 0),
        events_written=int(row[9] or 0),
        data_quality_score=_optional_float(row[10]),
        payload=row[11] or {},
    )


def _decision_record_from_row(row: tuple[Any, ...]) -> DecisionRecord:
    return DecisionRecord(
        decision_record_id=row[0] or "",
        decision_set_id=row[1] or "",
        instrument_id=row[2] or "",
        action=row[3] or "",
        confidence_score=_optional_float(row[4]),
        risk_score=_optional_float(row[5]),
        primary_reason_codes=tuple(row[6] or ()),
        created_at=_iso(row[7]),
    )


def _decision_explanation_from_row(row: tuple[Any, ...]) -> DecisionExplanationRecord:
    return DecisionExplanationRecord(
        decision_explanation_id=row[0] or "",
        decision_record_id=row[1] or "",
        created_at=_iso(row[2]),
        explanation=row[3] or {},
    )


def _execution_result_from_row(row: tuple[Any, ...]) -> ExecutionResultRecord:
    return ExecutionResultRecord(
        execution_result_id=row[0] or "",
        order_intent_id=row[1] or "",
        status=row[2] or "",
        submitted_at=_iso(row[3]) if row[3] is not None else "",
        last_update_at=_iso(row[4]) if row[4] is not None else "",
        fees=_optional_float(row[5]) or 0.0,
        slippage_bps=_optional_float(row[6]),
        errors=tuple(row[7] or ()),
        payload=row[8] or {},
    )


def _order_status_from_row(row: tuple[Any, ...]) -> OrderStatusRecord:
    return OrderStatusRecord(
        order_status_id=row[0] or "",
        order_intent_id=row[1] or "",
        status=row[2] or "",
        status_ts=_iso(row[3]),
        provider=row[4] or "",
        payload=row[5] or {},
    )


def _portfolio_snapshot_from_row(row: tuple[Any, ...]) -> PortfolioSnapshotRecord:
    return PortfolioSnapshotRecord(
        portfolio_snapshot_id=row[0] or "",
        portfolio_id=row[1] or "",
        universe_id=row[2] or "",
        as_of_ts=_iso(row[3]),
        equity=_optional_float(row[4]),
        gross_exposure=_optional_float(row[5]),
        net_exposure=_optional_float(row[6]),
        source_module=row[7] or "",
        created_at=_iso(row[8]),
        payload=row[9] or {},
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
