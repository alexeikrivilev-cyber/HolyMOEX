from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)
from agent_app.runtime_calendar import current_market_session

from . import metrics
from .repository import (
    AuditRecord,
    AuditLogRecord,
    DecisionExplanationRecord,
    DecisionRecord,
    ExecutionResultRecord,
    InMemoryMonitoringAuditRepository,
    ModuleJobResultRecord,
    MonitoringAuditRepository,
    MonitoringRecord,
    OrderStatusRecord,
    PortfolioSnapshotRecord,
    RequestLogRecord,
    stable_record_id,
)


MODULE_NAME = "Monitoring & Audit Module"
CALCULATION_VERSION = "monitoring_audit_v1"
VALID_CONTOURS = {
    "monitoring_contour",
    "research_contour",
    "execution_contour",
    "decision_contour",
    "intraday_contour",
    "daily_contour",
    "event_contour",
    "all_contours",
}
VALID_RUN_MODES = {"analysis_only", "paper_trading", "live_trading", "backtest", "replay"}
DEFAULT_EXPECTED_SOURCE_MODULES = (
    "Orchestration Module",
    "External Request Gateway Module",
    "Selected Instruments Registry Module",
    "Data Intake & Routing Module",
    "Data Quality Module",
    "Market Data Metrics Module",
    "Liquidity & Microstructure Module",
    "Volatility & Risk Metrics Module",
    "Market Context Module",
    "Fundamental & Valuation Module",
    "Event & News Intelligence Module",
    "Earnings & Dividend Intelligence Module",
    "Corporate Actions Adjustment Module",
    "Derivatives & Positioning Module",
    "Normalization & Feature Vector Module",
    "Feature Validation & Research Module",
    "Decision Engine Module",
    "Risk Control Module",
    "Execution Engine Module",
    "Portfolio State Module",
    "Backtesting & Paper Trading Module",
    "Monitoring & Audit Module",
)
VALID_EVENT_TYPES = {
    "module_run",
    "request",
    "data_quality",
    "decision",
    "risk",
    "execution",
    "portfolio",
    "system",
}
VALID_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
MONITORING_EVENT_FIELDS = {"event_id", "event_type", "severity", "source_module", "payload_ref", "created_at"}
SECRET_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "authorization",
    "auth_header",
    "auth_value",
    "bearer",
)


class MonitoringAuditError(ValueError):
    """Raised when module 22 would violate its documented contract."""


@dataclass(frozen=True)
class MonitoringEvent:
    event_id: str
    event_type: str
    severity: str
    source_module: str
    payload_ref: str
    created_at: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "MonitoringEvent":
        extra_top_level = sorted(set(payload) - {"monitoring_event"})
        if extra_top_level:
            raise MonitoringAuditError(f"payload has undocumented fields: {extra_top_level}")
        event_payload = payload.get("monitoring_event")
        if not isinstance(event_payload, Mapping):
            raise MonitoringAuditError("payload must contain monitoring_event")
        missing_fields = sorted(MONITORING_EVENT_FIELDS - set(event_payload))
        if missing_fields:
            raise MonitoringAuditError(f"monitoring_event missing required fields: {missing_fields}")
        extra_fields = sorted(set(event_payload) - MONITORING_EVENT_FIELDS)
        if extra_fields:
            raise MonitoringAuditError(f"monitoring_event has undocumented fields: {extra_fields}")

        event_type = str(event_payload.get("event_type") or "")
        if event_type not in VALID_EVENT_TYPES:
            raise MonitoringAuditError("monitoring_event.event_type is invalid")
        severity = str(event_payload.get("severity") or "")
        if severity not in VALID_SEVERITIES:
            raise MonitoringAuditError("monitoring_event.severity is invalid")
        created_at = str(event_payload.get("created_at") or "")
        parse_utc_iso(created_at)
        if parse_utc_iso(created_at) > parse_utc_iso(job.time_range.to_ts):
            raise MonitoringAuditError("monitoring_event.created_at must be <= module_job.time_range.to_ts")

        text_fields = {
            "event_id": event_payload.get("event_id"),
            "source_module": event_payload.get("source_module"),
            "payload_ref": event_payload.get("payload_ref"),
        }
        missing_text = [name for name, value in text_fields.items() if not str(value or "")]
        if missing_text:
            raise MonitoringAuditError(f"monitoring_event missing text fields: {missing_text}")

        return cls(
            event_id=str(event_payload.get("event_id") or ""),
            event_type=event_type,
            severity=severity,
            source_module=str(event_payload.get("source_module") or ""),
            payload_ref=str(event_payload.get("payload_ref") or ""),
            created_at=created_at,
        )


@dataclass(frozen=True)
class MonitoringAuditConfig:
    sla_latency_warning_ms: float = 60_000.0
    sla_latency_critical_ms: float = 300_000.0
    data_staleness_warning_seconds: float = 3_600.0
    data_staleness_critical_seconds: float = 86_400.0
    module_error_rate_warning: float = 0.05
    module_error_rate_critical: float = 0.20
    gateway_error_rate_warning: float = 0.05
    gateway_error_rate_critical: float = 0.20
    execution_error_rate_warning: float = 0.02
    execution_error_rate_critical: float = 0.10
    llm_cost_warning_units: float = 100.0
    llm_cost_critical_units: float = 500.0
    alert_delivery_enabled: bool = False
    governance_global_kill_switch_allowed: bool = False
    expected_source_modules: tuple[str, ...] = DEFAULT_EXPECTED_SOURCE_MODULES

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None = None) -> "MonitoringAuditConfig":
        payload = payload or {}
        return cls(
            sla_latency_warning_ms=_float(payload.get("sla_latency_warning_ms"), 60_000.0),
            sla_latency_critical_ms=_float(payload.get("sla_latency_critical_ms"), 300_000.0),
            data_staleness_warning_seconds=_float(payload.get("data_staleness_warning_seconds"), 3_600.0),
            data_staleness_critical_seconds=_float(payload.get("data_staleness_critical_seconds"), 86_400.0),
            module_error_rate_warning=_float(payload.get("module_error_rate_warning"), 0.05),
            module_error_rate_critical=_float(payload.get("module_error_rate_critical"), 0.20),
            gateway_error_rate_warning=_float(payload.get("gateway_error_rate_warning"), 0.05),
            gateway_error_rate_critical=_float(payload.get("gateway_error_rate_critical"), 0.20),
            execution_error_rate_warning=_float(payload.get("execution_error_rate_warning"), 0.02),
            execution_error_rate_critical=_float(payload.get("execution_error_rate_critical"), 0.10),
            llm_cost_warning_units=_float(payload.get("llm_cost_warning_units"), 100.0),
            llm_cost_critical_units=_float(payload.get("llm_cost_critical_units"), 500.0),
            alert_delivery_enabled=bool(payload.get("alert_delivery_enabled", False)),
            governance_global_kill_switch_allowed=bool(payload.get("governance_global_kill_switch_allowed", False)),
            expected_source_modules=_string_tuple(payload.get("expected_source_modules")) or DEFAULT_EXPECTED_SOURCE_MODULES,
        )


@dataclass(frozen=True)
class MonitoringSnapshot:
    audit_records: tuple[AuditLogRecord, ...]
    request_logs: tuple[RequestLogRecord, ...]
    module_job_results: tuple[ModuleJobResultRecord, ...]
    decision_records: tuple[DecisionRecord, ...]
    decision_explanations: tuple[DecisionExplanationRecord, ...]
    execution_results: tuple[ExecutionResultRecord, ...]
    order_statuses: tuple[OrderStatusRecord, ...]
    portfolio_snapshots: tuple[PortfolioSnapshotRecord, ...]


@dataclass(frozen=True)
class MonitoringBuild:
    health_report: Mapping[str, Any]
    health_record: MonitoringRecord
    alert_records: tuple[MonitoringRecord, ...]
    replay_index_record: MonitoringRecord
    audit_record: AuditRecord
    warnings: tuple[str, ...]
    data_quality_score: float


@dataclass(frozen=True)
class MonitoringAuditRunResult:
    module_job_result: ModuleJobResult
    health_report: Mapping[str, Any] | None
    health_report_ref: str | None = None
    alert_refs: tuple[str, ...] = ()
    audit_record_ref: str | None = None
    replay_index_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "health_report": dict(self.health_report) if self.health_report else None,
            "health_report_ref": self.health_report_ref,
            "alert_refs": list(self.alert_refs),
            "audit_record_ref": self.audit_record_ref,
            "replay_index_ref": self.replay_index_ref,
        }


class MonitoringAuditService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: MonitoringAuditRepository | None = None,
        config: MonitoringAuditConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.repository = repository or InMemoryMonitoringAuditRepository()
        self.config = config if isinstance(config, MonitoringAuditConfig) else MonitoringAuditConfig.from_mapping(config)

    def run(self, payload: Mapping[str, Any], job: ModuleJob) -> MonitoringAuditRunResult:
        return self.execute(payload, job)

    def process(self, payload: Mapping[str, Any], job: ModuleJob) -> MonitoringAuditRunResult:
        return self.execute(payload, job)

    def execute(self, payload: Mapping[str, Any], job: ModuleJob | None) -> MonitoringAuditRunResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            return self._missing_job_result(started_at)

        try:
            self.validate_module_job(job)
            event = MonitoringEvent.from_dict(payload, job)
            snapshot = self.load_monitoring_snapshot(job)
            build = self.build_monitoring_outputs(event, job, snapshot)

            health_ref = self.repository.save_monitoring_record(build.health_record)
            alert_refs = tuple(self.repository.save_monitoring_record(record) for record in build.alert_records)
            replay_ref = self.repository.save_monitoring_record(build.replay_index_record)
            audit_ref = self.repository.save_audit_record(build.audit_record)
            output_refs = (health_ref, *alert_refs, replay_ref, audit_ref)
            status = "partial_success" if build.warnings else "success"
            return MonitoringAuditRunResult(
                module_job_result=ModuleJobResult(
                    job_id=job.job_id,
                    module_name=self.module_name,
                    status=status,
                    started_at=started_at,
                    finished_at=to_utc_iso(utc_now()),
                    output_refs=output_refs,
                    warnings=build.warnings,
                    errors=(),
                    metrics_written=1 + len(alert_refs) + 1,
                    events_written=1,
                    data_quality_score=build.data_quality_score,
                ),
                health_report=build.health_report,
                health_report_ref=health_ref,
                alert_refs=alert_refs,
                audit_record_ref=audit_ref,
                replay_index_ref=replay_ref,
            )
        except (MonitoringAuditError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise MonitoringAuditError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise MonitoringAuditError("module_job.module_name must be Monitoring & Audit Module")
        if job.contour not in VALID_CONTOURS:
            raise MonitoringAuditError("module_job.contour must match monitoring_contour or documented all_contours")
        if job.run_mode not in VALID_RUN_MODES:
            raise MonitoringAuditError("module_job.run_mode is invalid")

    def load_monitoring_snapshot(self, job: ModuleJob) -> MonitoringSnapshot:
        from_ts = job.time_range.from_ts
        to_ts = job.time_range.to_ts
        return MonitoringSnapshot(
            audit_records=self.repository.list_audit_records(from_ts, to_ts),
            request_logs=self.repository.list_request_logs(from_ts, to_ts),
            module_job_results=self.repository.list_module_job_results(from_ts, to_ts),
            decision_records=self.repository.list_decision_records(from_ts, to_ts),
            decision_explanations=self.repository.list_decision_explanations(from_ts, to_ts),
            execution_results=self.repository.list_execution_results(from_ts, to_ts),
            order_statuses=self.repository.list_order_statuses(from_ts, to_ts),
            portfolio_snapshots=self.repository.list_portfolio_snapshots(from_ts, to_ts),
        )

    def build_monitoring_outputs(
        self,
        event: MonitoringEvent,
        job: ModuleJob,
        snapshot: MonitoringSnapshot,
    ) -> MonitoringBuild:
        now_ts = job.time_range.to_ts
        computed = self.compute_health_metrics(event, job, snapshot, now_ts)
        module_statuses = self.module_statuses(event, snapshot.audit_records)
        alerts, sla_breaches, warnings = self.detect_alerts(event, computed, snapshot, now_ts)
        computed["alert_count"] = metrics.alert_count(len(alerts))
        coverage = self.monitoring_event_coverage(event, snapshot.audit_records)
        acceptance_criteria = {
            "all_modules_emit_monitoring_events": not coverage["missing_modules"],
            "decision_replay_possible": bool(snapshot.decision_records),
            "critical_alerts_generated": any(alert["severity"] == "critical" for alert in alerts),
            "request_costs_tracked": computed["llm_cost_units"] >= 0.0,
            "kill_switch_audited": any(alert.get("kill_switch_audited") for alert in alerts) or event.severity != "critical",
            "health_report_available": True,
        }
        health_reason_codes = self.health_reason_codes(
            job=job,
            snapshot=snapshot,
            computed=computed,
            coverage=coverage,
            acceptance_criteria=acceptance_criteria,
        )
        system_status = self.system_status(event, module_statuses, alerts, health_reason_codes)
        health_report_id = stable_record_id(
            "health_report",
            {
                "job_id": job.job_id,
                "event_id": event.event_id,
                "as_of_ts": now_ts,
                "calculation_version": CALCULATION_VERSION,
            },
        )
        health_report = {
            "health_report_id": health_report_id,
            "as_of_ts": now_ts,
            "system_status": system_status,
            "module_statuses": module_statuses,
            "active_alerts": [alert["alert_id"] for alert in alerts],
            "sla_breaches": sla_breaches,
            "reason_codes": health_reason_codes,
        }
        health_payload = {
            "health_report": health_report,
            "metrics": computed,
            "event": self.event_payload(event),
            "monitoring_event_coverage": coverage,
            "acceptance_criteria": acceptance_criteria,
            "reason_codes": health_reason_codes,
            "source_module": self.module_name,
            "calculation_version": CALCULATION_VERSION,
            "timestamp": now_ts,
            "llm_usage": "none",
        }
        health_record = MonitoringRecord(
            monitoring_record_id=health_report_id,
            check_name="health_report",
            status=system_status,
            severity=self.status_to_severity(system_status),
            observed_at=now_ts,
            payload=redact_secrets(health_payload),
        )
        alert_records = tuple(self.alert_record(alert, event, job, now_ts) for alert in alerts)
        replay_index_record = self.replay_index_record(snapshot, event, job, now_ts)
        audit_record = self.audit_record(event, job, health_report, alerts, warnings)
        return MonitoringBuild(
            health_report=health_report,
            health_record=health_record,
            alert_records=alert_records,
            replay_index_record=replay_index_record,
            audit_record=audit_record,
            warnings=tuple(dict.fromkeys(warnings)),
            data_quality_score=self.data_quality_score(system_status, warnings),
        )

    def compute_health_metrics(
        self,
        event: MonitoringEvent,
        job: ModuleJob,
        snapshot: MonitoringSnapshot,
        now_ts: str,
    ) -> dict[str, Any]:
        observation_seconds = max(
            1.0,
            (parse_utc_iso(job.time_range.to_ts) - parse_utc_iso(job.time_range.from_ts)).total_seconds(),
        )
        if snapshot.module_job_results:
            latencies = [self.module_job_result_latency(record) for record in snapshot.module_job_results]
            latencies = [latency for latency in latencies if latency > 0]
            failed_jobs = sum(1 for record in snapshot.module_job_results if record.status == "failed")
            total_jobs = len(snapshot.module_job_results)
        else:
            module_run_records = self.module_run_records(snapshot.audit_records)
            latencies = [self.audit_record_latency(record) for record in module_run_records]
            latencies = [latency for latency in latencies if latency > 0]
            failed_jobs = sum(1 for record in module_run_records if self.audit_record_failed(record))
            total_jobs = len(module_run_records)
        if event.event_type == "module_run":
            total_jobs += 1
            if event.severity in {"error", "critical"}:
                failed_jobs += 1
        request_failures = sum(1 for record in snapshot.request_logs if record.status not in {"success", "ok", "cached"})
        execution_failures = sum(1 for record in snapshot.execution_results if self.execution_failed(record))
        audit_risk_events = tuple(record for record in snapshot.audit_records if self.audit_record_is_risk_event(record))
        rejected_risks = sum(1 for record in audit_risk_events if self.audit_record_risk_rejected(record))
        risk_events_total = len(audit_risk_events)
        if event.event_type == "risk":
            risk_events_total += 1
            if event.severity in {"warning", "error", "critical"}:
                rejected_risks += 1

        latest_by_store = self.latest_record_timestamps(snapshot, event)
        data_staleness_by_store = {
            store: metrics.data_staleness_seconds(now_ts, timestamp)
            for store, timestamp in latest_by_store.items()
        }
        latest_by_module = self.latest_module_timestamps(snapshot, event)
        data_staleness_by_module = {
            module: metrics.data_staleness_seconds(now_ts, timestamp)
            for module, timestamp in latest_by_module.items()
        }
        polza_costs = [
            record.cost_units
            for record in snapshot.request_logs
            if "polza" in record.provider.lower()
        ]
        return {
            "system_uptime": metrics.system_uptime(observation_seconds if event.severity != "critical" else 0.0, observation_seconds),
            "module_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "module_error_rate": metrics.module_error_rate(failed_jobs, total_jobs),
            "data_staleness_seconds": {
                "by_store": data_staleness_by_store,
                "by_module": data_staleness_by_module,
            },
            "decision_count": metrics.decision_count(len(snapshot.decision_records)),
            "risk_rejection_rate": metrics.risk_rejection_rate(rejected_risks, risk_events_total),
            "execution_error_rate": metrics.execution_error_rate(execution_failures, len(snapshot.execution_results)),
            "llm_cost_units": metrics.llm_cost_units(polza_costs),
            "gateway_error_rate": metrics.gateway_error_rate(request_failures, len(snapshot.request_logs)),
            "alert_count": 0,
            "raw_counts": {
                "audit_records": len(snapshot.audit_records),
                "request_logs": len(snapshot.request_logs),
                "module_job_results": len(snapshot.module_job_results),
                "decision_records": len(snapshot.decision_records),
                "decision_explanations": len(snapshot.decision_explanations),
                "execution_results": len(snapshot.execution_results),
                "order_statuses": len(snapshot.order_statuses),
                "portfolio_snapshots": len(snapshot.portfolio_snapshots),
            },
        }

    def detect_alerts(
        self,
        event: MonitoringEvent,
        computed: Mapping[str, Any],
        snapshot: MonitoringSnapshot,
        now_ts: str,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        del snapshot
        alerts: list[dict[str, Any]] = []
        sla_breaches: list[str] = []
        warnings: list[str] = []

        self.detect_sla_breach(computed, alerts, sla_breaches)
        self.detect_data_staleness(computed, alerts, sla_breaches)
        self.detect_llm_cost_anomaly(computed, alerts, sla_breaches)
        self.detect_execution_anomaly(computed, alerts, sla_breaches)

        if event.severity in {"error", "critical"}:
            alerts.append(
                self.alert_payload(
                    check_name=f"monitoring_event:{event.event_type}",
                    severity=event.severity,
                    message=f"{event.event_type} event from {event.source_module}",
                    observed_at=event.created_at,
                    source_ref=event.payload_ref,
                    reason_codes=(f"event_severity:{event.severity}",),
                    kill_switch_candidate=event.severity == "critical",
                )
            )
        for alert in alerts:
            if alert["severity"] in {"warning", "error", "critical"}:
                warnings.append(alert["check_name"])
            if alert["severity"] == "critical":
                alert["kill_switch_audited"] = True
                alert["governance_global_kill_switch_allowed"] = self.config.governance_global_kill_switch_allowed
                alert["risk_policy_store_write_performed"] = False
                alert["risk_policy_store_write_reason"] = "not_in_module_write_stores"
                alert["kill_switch_audit_ts"] = now_ts
        computed_alert_count = metrics.alert_count(len(alerts))
        for alert in alerts:
            alert["alert_count_after_raise"] = computed_alert_count
            alert["alert_delivery_enabled"] = self.config.alert_delivery_enabled
            alert["alert_delivery_attempted"] = False
            alert["alert_delivery_reason"] = "gateway_not_called_by_monitoring_runtime"
        return alerts, sla_breaches, warnings

    def detect_sla_breach(
        self,
        computed: Mapping[str, Any],
        alerts: list[dict[str, Any]],
        sla_breaches: list[str],
    ) -> None:
        latency = _float(computed.get("module_latency_ms"), 0.0)
        module_error_rate = _float(computed.get("module_error_rate"), 0.0)
        gateway_error_rate = _float(computed.get("gateway_error_rate"), 0.0)
        if latency >= self.config.sla_latency_critical_ms:
            sla_breaches.append("module_latency_ms")
            alerts.append(self.alert_payload("module_latency_ms", "critical", "Module latency breached critical SLA"))
        elif latency >= self.config.sla_latency_warning_ms:
            sla_breaches.append("module_latency_ms")
            alerts.append(self.alert_payload("module_latency_ms", "warning", "Module latency breached warning SLA"))

        if module_error_rate >= self.config.module_error_rate_critical:
            sla_breaches.append("module_error_rate")
            alerts.append(self.alert_payload("module_error_rate", "critical", "Module error rate breached critical SLA"))
        elif module_error_rate >= self.config.module_error_rate_warning:
            sla_breaches.append("module_error_rate")
            alerts.append(self.alert_payload("module_error_rate", "warning", "Module error rate breached warning SLA"))

        if gateway_error_rate >= self.config.gateway_error_rate_critical:
            sla_breaches.append("gateway_error_rate")
            alerts.append(self.alert_payload("gateway_error_rate", "critical", "Gateway error rate breached critical SLA"))
        elif gateway_error_rate >= self.config.gateway_error_rate_warning:
            sla_breaches.append("gateway_error_rate")
            alerts.append(self.alert_payload("gateway_error_rate", "warning", "Gateway error rate breached warning SLA"))

    def detect_data_staleness(
        self,
        computed: Mapping[str, Any],
        alerts: list[dict[str, Any]],
        sla_breaches: list[str],
    ) -> None:
        staleness = computed.get("data_staleness_seconds")
        if not isinstance(staleness, Mapping):
            return
        sections = staleness if any(isinstance(value, Mapping) for value in staleness.values()) else {"by_store": staleness}
        for scope, values in sections.items():
            if not isinstance(values, Mapping):
                continue
            for name, seconds in values.items():
                value = _float(seconds, 0.0)
                check_name = f"data_staleness_seconds:{scope}:{name}"
                if value >= self.config.data_staleness_critical_seconds:
                    sla_breaches.append(check_name)
                    alerts.append(
                        self.alert_payload(
                            check_name,
                            "critical",
                            f"{name} staleness breached critical threshold",
                        )
                    )
                elif value >= self.config.data_staleness_warning_seconds:
                    sla_breaches.append(check_name)
                    alerts.append(
                        self.alert_payload(
                            check_name,
                            "warning",
                            f"{name} staleness breached warning threshold",
                        )
                    )

    def detect_llm_cost_anomaly(
        self,
        computed: Mapping[str, Any],
        alerts: list[dict[str, Any]],
        sla_breaches: list[str],
    ) -> None:
        cost = _float(computed.get("llm_cost_units"), 0.0)
        if cost >= self.config.llm_cost_critical_units:
            sla_breaches.append("llm_cost_units")
            alerts.append(self.alert_payload("llm_cost_units", "critical", "PolzaAI cost breached critical threshold"))
        elif cost >= self.config.llm_cost_warning_units:
            sla_breaches.append("llm_cost_units")
            alerts.append(self.alert_payload("llm_cost_units", "warning", "PolzaAI cost breached warning threshold"))

    def detect_execution_anomaly(
        self,
        computed: Mapping[str, Any],
        alerts: list[dict[str, Any]],
        sla_breaches: list[str],
    ) -> None:
        error_rate = _float(computed.get("execution_error_rate"), 0.0)
        if error_rate >= self.config.execution_error_rate_critical:
            sla_breaches.append("execution_error_rate")
            alerts.append(self.alert_payload("execution_error_rate", "critical", "Execution error rate breached critical threshold"))
        elif error_rate >= self.config.execution_error_rate_warning:
            sla_breaches.append("execution_error_rate")
            alerts.append(self.alert_payload("execution_error_rate", "warning", "Execution error rate breached warning threshold"))

    def alert_payload(
        self,
        check_name: str,
        severity: str,
        message: str,
        observed_at: str | None = None,
        source_ref: str | None = None,
        reason_codes: tuple[str, ...] = (),
        kill_switch_candidate: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "alert_id": stable_record_id(
                "alert",
                {
                    "check_name": check_name,
                    "severity": severity,
                    "message": message,
                    "observed_at": observed_at or "",
                    "calculation_version": CALCULATION_VERSION,
                },
            ),
            "check_name": check_name,
            "severity": severity,
            "message": message,
            "observed_at": observed_at,
            "source_ref": source_ref,
            "reason_codes": list(reason_codes or (check_name,)),
            "kill_switch_candidate": kill_switch_candidate,
        }
        return payload

    def alert_record(
        self,
        alert: Mapping[str, Any],
        event: MonitoringEvent,
        job: ModuleJob,
        observed_at: str,
    ) -> MonitoringRecord:
        return MonitoringRecord(
            monitoring_record_id=str(alert["alert_id"]),
            check_name="alert",
            status="active",
            severity=str(alert["severity"]),
            observed_at=observed_at,
            payload=redact_secrets(
                {
                    "alert": dict(alert),
                    "monitoring_event_id": event.event_id,
                    "job_id": job.job_id,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "technical_alert_delivery": {
                        "enabled": self.config.alert_delivery_enabled,
                        "gateway_called": False,
                    },
                }
            ),
        )

    def replay_index_record(
        self,
        snapshot: MonitoringSnapshot,
        event: MonitoringEvent,
        job: ModuleJob,
        observed_at: str,
    ) -> MonitoringRecord:
        explanations_by_decision = {
            explanation.decision_record_id: explanation
            for explanation in snapshot.decision_explanations
        }
        decisions = []
        for decision in snapshot.decision_records:
            explanation = explanations_by_decision.get(decision.decision_record_id)
            decisions.append(
                {
                    "decision_record_id": decision.decision_record_id,
                    "decision_set_id": decision.decision_set_id,
                    "instrument_id": decision.instrument_id,
                    "action": decision.action,
                    "created_at": decision.created_at,
                    "decision_record_ref": f"decisions.decision_record:{decision.decision_record_id}",
                    "decision_explanation_ref": (
                        f"decisions.decision_explanation:{explanation.decision_explanation_id}"
                        if explanation
                        else None
                    ),
                }
            )
        replay_index_id = stable_record_id(
            "decision_replay_index",
            {
                "job_id": job.job_id,
                "event_id": event.event_id,
                "decision_ids": [decision["decision_record_id"] for decision in decisions],
                "calculation_version": CALCULATION_VERSION,
            },
        )
        return MonitoringRecord(
            monitoring_record_id=replay_index_id,
            check_name="decision_replay_index",
            status="available" if decisions else "empty",
            severity="info",
            observed_at=observed_at,
            payload=redact_secrets(
                {
                    "replay_index_record": {
                        "replay_index_id": replay_index_id,
                        "decision_replay_possible": bool(decisions),
                        "decision_records": decisions,
                    },
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                    "monitoring_event_id": event.event_id,
                }
            ),
        )

    def audit_record(
        self,
        event: MonitoringEvent,
        job: ModuleJob,
        health_report: Mapping[str, Any],
        alerts: list[Mapping[str, Any]],
        warnings: tuple[str, ...] | list[str],
    ) -> AuditRecord:
        severity = "critical" if any(alert["severity"] == "critical" for alert in alerts) else "warning" if warnings else "info"
        return AuditRecord(
            audit_record_id=stable_record_id(
                "audit_monitoring_event",
                {
                    "job_id": job.job_id,
                    "event_id": event.event_id,
                    "health_report_id": health_report.get("health_report_id"),
                    "calculation_version": CALCULATION_VERSION,
                },
            ),
            module_name=self.module_name,
            job_id=job.job_id,
            severity=severity,
            event_type="monitoring_event_ingested",
            message="Monitoring event ingested and health report written",
            object_type="health_report",
            object_ref=f"audit.monitoring_record:{health_report.get('health_report_id')}",
            reason_codes=tuple(dict.fromkeys(("monitoring_event_ingested", *warnings))),
            payload=redact_secrets(
                {
                    "monitoring_event": self.event_payload(event),
                    "health_report_id": health_report.get("health_report_id"),
                    "alert_ids": [alert["alert_id"] for alert in alerts],
                    "kill_switch_audited": any(alert.get("kill_switch_audited") for alert in alerts) or event.severity != "critical",
                    "trading_decisions_changed": False,
                    "orders_sent": False,
                    "audit_records_deleted": False,
                    "source_module": self.module_name,
                    "calculation_version": CALCULATION_VERSION,
                }
            ),
        )

    def module_statuses(
        self,
        event: MonitoringEvent,
        audit_records: tuple[AuditLogRecord, ...],
    ) -> dict[str, str]:
        statuses: dict[str, str] = {}
        for record in audit_records:
            payload = record.payload if isinstance(record.payload, Mapping) else {}
            module_name = record.module_name or str(payload.get("module_name") or "")
            module_name = str(module_name or "")
            if not module_name:
                continue
            current = statuses.get(module_name, "healthy")
            next_status = "failed" if self.audit_record_failed(record) or record.severity in {"error", "critical"} else "degraded" if record.severity == "warning" else "healthy"
            statuses[module_name] = self.worst_module_status(current, next_status)
        event_status = "failed" if event.severity in {"error", "critical"} else "degraded" if event.severity == "warning" else "healthy"
        statuses[event.source_module] = self.worst_module_status(statuses.get(event.source_module, "healthy"), event_status)
        if not statuses:
            statuses[self.module_name] = "healthy"
        return dict(sorted(statuses.items()))

    def system_status(
        self,
        event: MonitoringEvent,
        module_statuses: Mapping[str, str],
        alerts: list[Mapping[str, Any]],
        reason_codes: tuple[str, ...] = (),
    ) -> str:
        if event.event_type == "system" and event.severity == "critical":
            return "stopped"
        if any(alert["severity"] == "critical" for alert in alerts):
            return "critical"
        if any(status == "failed" for status in module_statuses.values()):
            return "critical"
        if "off_market_no_current_decision_chain" in reason_codes:
            return "off_market"
        if reason_codes:
            return "degraded"
        if alerts or any(status == "degraded" for status in module_statuses.values()):
            return "degraded"
        return "healthy"

    def status_to_severity(self, status: str) -> str:
        if status in {"critical", "stopped"}:
            return "critical"
        if status == "degraded":
            return "warning"
        return "info"

    def health_reason_codes(
        self,
        *,
        job: ModuleJob,
        snapshot: MonitoringSnapshot,
        computed: Mapping[str, Any],
        coverage: Mapping[str, Any],
        acceptance_criteria: Mapping[str, Any],
    ) -> tuple[str, ...]:
        reason_codes: list[str] = []
        market_status = current_market_session().market_session_status
        raw_counts = computed.get("raw_counts") if isinstance(computed.get("raw_counts"), Mapping) else {}
        if not acceptance_criteria.get("decision_replay_possible"):
            reason_codes.append("decision_replay_not_possible")
        if job.run_mode == "live_trading":
            if not snapshot.decision_records:
                reason_codes.append("decision_records_missing")
            if not snapshot.execution_results:
                reason_codes.append("execution_results_missing")
            if not snapshot.portfolio_snapshots:
                reason_codes.append("portfolio_snapshot_missing")
            if not raw_counts.get("decision_records"):
                reason_codes.append("current_window_decision_chain_missing")
            if market_status in {"closed", "premarket", "postmarket"} and {
                "decision_replay_not_possible",
                "decision_records_missing",
                "execution_results_missing",
            }.issubset(set(reason_codes)):
                reason_codes.append("off_market_no_current_decision_chain")
            elif market_status == "unknown":
                reason_codes.append("market_session_unknown")
        missing_modules = set(str(item) for item in (coverage.get("missing_modules") or ()))
        critical_modules = {
            "Market Data Metrics Module",
            "Liquidity & Microstructure Module",
            "Volatility & Risk Metrics Module",
            "Market Context Module",
            "Normalization & Feature Vector Module",
            "Decision Engine Module",
            "Risk Control Module",
            "Execution Engine Module",
            "Portfolio State Module",
        }
        if missing_modules & critical_modules:
            reason_codes.append("critical_module_monitoring_coverage_missing")
        return tuple(dict.fromkeys(reason_codes))

    def latest_record_timestamps(
        self,
        snapshot: MonitoringSnapshot,
        event: MonitoringEvent,
    ) -> dict[str, str | None]:
        return {
            "Audit Log Store": _latest((record.created_at for record in snapshot.audit_records), event.created_at),
            "Module Job Result Store": _latest(record.finished_at for record in snapshot.module_job_results),
            "Request Log Store": _latest(record.created_at for record in snapshot.request_logs),
            "Decision Store": _latest(record.created_at for record in snapshot.decision_records),
            "Order Store": _latest(
                record.last_update_at or record.submitted_at
                for record in snapshot.execution_results
            ),
            "Portfolio State Store": _latest(record.as_of_ts for record in snapshot.portfolio_snapshots),
        }

    def latest_module_timestamps(
        self,
        snapshot: MonitoringSnapshot,
        event: MonitoringEvent,
    ) -> dict[str, str]:
        latest: dict[str, str] = {event.source_module: event.created_at}
        for record in snapshot.audit_records:
            if record.module_name and record.created_at:
                latest[record.module_name] = _latest((latest.get(record.module_name), record.created_at)) or record.created_at
        for record in snapshot.module_job_results:
            if record.module_name and record.finished_at:
                latest[record.module_name] = _latest((latest.get(record.module_name), record.finished_at)) or record.finished_at
        for record in snapshot.request_logs:
            if record.caller_module and record.created_at:
                latest[record.caller_module] = _latest((latest.get(record.caller_module), record.created_at)) or record.created_at
        for record in snapshot.portfolio_snapshots:
            if record.source_module and record.as_of_ts:
                latest[record.source_module] = _latest((latest.get(record.source_module), record.as_of_ts)) or record.as_of_ts
        return dict(sorted(latest.items()))

    def monitoring_event_coverage(
        self,
        event: MonitoringEvent,
        audit_records: tuple[AuditLogRecord, ...],
    ) -> dict[str, Any]:
        observed = {event.source_module}
        for record in audit_records:
            if record.module_name:
                observed.add(record.module_name)
            payload = record.payload if isinstance(record.payload, Mapping) else {}
            source_module = str(payload.get("source_module") or payload.get("module_name") or "")
            if source_module:
                observed.add(source_module)
        expected = set(self.config.expected_source_modules)
        missing = sorted(expected - observed)
        return {
            "expected_modules": sorted(expected),
            "observed_modules": sorted(observed),
            "missing_modules": missing,
        }

    def module_run_records(self, audit_records: tuple[AuditLogRecord, ...]) -> tuple[AuditLogRecord, ...]:
        return tuple(
            record
            for record in audit_records
            if record.job_id
            and (
                "module" in record.event_type
                or "module_job_result" in record.object_type
                or str(record.payload.get("status") if isinstance(record.payload, Mapping) else "")
            )
        )

    def audit_record_latency(self, record: AuditLogRecord) -> float:
        payload = record.payload if isinstance(record.payload, Mapping) else {}
        result_payload = payload.get("module_job_result") if isinstance(payload.get("module_job_result"), Mapping) else payload
        started_at = str(result_payload.get("started_at") or payload.get("started_at") or "")
        finished_at = str(result_payload.get("finished_at") or payload.get("finished_at") or "")
        if not started_at or not finished_at:
            return 0.0
        return metrics.module_latency_ms(started_at, finished_at)

    def module_job_result_latency(self, record: ModuleJobResultRecord) -> float:
        if not record.started_at or not record.finished_at:
            return 0.0
        return metrics.module_latency_ms(record.started_at, record.finished_at)

    def audit_record_failed(self, record: AuditLogRecord) -> bool:
        payload = record.payload if isinstance(record.payload, Mapping) else {}
        status = str(payload.get("status") or "")
        result_payload = payload.get("module_job_result") if isinstance(payload.get("module_job_result"), Mapping) else {}
        result_status = str(result_payload.get("status") or "")
        return (
            record.severity in {"error", "critical"}
            or status == "failed"
            or result_status == "failed"
            or "failed" in record.event_type
        )

    def audit_record_is_risk_event(self, record: AuditLogRecord) -> bool:
        payload = record.payload if isinstance(record.payload, Mapping) else {}
        return (
            "risk" in record.event_type
            or "risk" in record.object_type
            or str(payload.get("event_type") or "") == "risk"
            or any("risk" in code for code in record.reason_codes)
        )

    def audit_record_risk_rejected(self, record: AuditLogRecord) -> bool:
        payload = record.payload if isinstance(record.payload, Mapping) else {}
        return (
            record.severity in {"warning", "error", "critical"}
            or str(payload.get("status") or "") in {"rejected", "failed", "blocked"}
            or any("reject" in code or "block" in code for code in record.reason_codes)
        )

    def execution_failed(self, record: ExecutionResultRecord) -> bool:
        status = record.status.lower()
        return bool(record.errors) or status in {"failed", "rejected", "error", "cancelled", "timeout"}

    def event_payload(self, event: MonitoringEvent) -> dict[str, Any]:
        return redact_secrets(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "severity": event.severity,
                "source_module": event.source_module,
                "payload_ref": event.payload_ref,
                "created_at": event.created_at,
            }
        )

    def worst_module_status(self, current: str, next_status: str) -> str:
        order = {"healthy": 0, "degraded": 1, "failed": 2}
        return next_status if order.get(next_status, 0) > order.get(current, 0) else current

    def data_quality_score(self, system_status: str, warnings: tuple[str, ...] | list[str]) -> float:
        if system_status in {"critical", "stopped"}:
            return 0.25
        score = 1.0
        score -= min(0.5, 0.05 * len(set(warnings)))
        if system_status == "degraded":
            score = min(score, 0.75)
        return max(0.0, min(1.0, score))

    def _failed_result(self, job: ModuleJob, started_at: str, error: Exception) -> MonitoringAuditRunResult:
        audit_ref: str | None = None
        try:
            audit_ref = self.repository.save_audit_record(
                AuditRecord(
                    audit_record_id=stable_record_id(
                        "audit_monitoring_failed",
                        {"job_id": job.job_id, "error": str(error), "calculation_version": CALCULATION_VERSION},
                    ),
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="error",
                    event_type="monitoring_audit_failed",
                    message=str(error),
                    object_type="module_job",
                    object_ref=f"audit.module_job:{job.job_id}",
                    reason_codes=("monitoring_audit_failed",),
                    payload=redact_secrets(
                        {
                            "source_module": self.module_name,
                            "calculation_version": CALCULATION_VERSION,
                            "trading_decisions_changed": False,
                            "orders_sent": False,
                            "audit_records_deleted": False,
                        }
                    ),
                )
            )
        except Exception:
            audit_ref = None
        output_refs = (audit_ref,) if audit_ref else ()
        return MonitoringAuditRunResult(
            module_job_result=ModuleJobResult(
                job_id=job.job_id,
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=output_refs,
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                events_written=1 if audit_ref else 0,
                data_quality_score=0.0,
            ),
            health_report=None,
            audit_record_ref=audit_ref,
        )

    def _missing_job_result(self, started_at: str) -> MonitoringAuditRunResult:
        return MonitoringAuditRunResult(
            module_job_result=ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                output_refs=(),
                warnings=(),
                errors=(f"{self.module_name} requires module_job",),
                metrics_written=0,
                events_written=0,
                data_quality_score=0.0,
            ),
            health_report=None,
        )


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if _secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_secrets(item)
        return result
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            return "[REDACTED]"
    return value


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def _latest(values: Any, fallback: str | None = None) -> str | None:
    timestamps = [str(value) for value in values if value]
    if fallback:
        timestamps.append(fallback)
    if not timestamps:
        return None
    return max(timestamps, key=lambda item: parse_utc_iso(item))


def _float(value: Any, default: float) -> float:
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
