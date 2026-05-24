from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    to_utc_iso,
    utc_now,
)

from .metrics import (
    coverage_ratio,
    data_quality_score,
    duplicate_record_count,
    expired_record_count,
    missing_field_count,
    outlier_count,
    quality_flags_from_counts,
    source_conflict_count,
    stale_record_count,
)
from .repository import (
    AuditRecord,
    DataQualityRecord,
    DataQualityRepository,
    InMemoryDataQualityRepository,
)


MODULE_NAME = "Data Quality Module"
CALCULATION_VERSION = "data_quality_v1"
OUTLIER_Z_THRESHOLD = 3.0
SOURCE_CONFLICT_TOLERANCE = 0.0

VALID_CHECK_LEVELS = {"raw", "feature", "decision", "execution"}
ALLOWED_INPUT_REF_PREFIXES = (
    "raw_market.raw_candle:",
    "raw_market.raw_trade:",
    "raw_market.raw_orderbook:",
    "raw_market.raw_index_value:",
    "raw_text.raw_text_item:",
    "features.feature_record:",
    "features.feature_vector:",
    "portfolio.portfolio_snapshot:",
)
VALID_CONTOURS = {
    "realtime_contour",
    "intraday_contour",
    "global_contour",
    "daily_contour",
    "event_contour",
    "decision_contour",
    "execution_contour",
    "monitoring_contour",
    "research_contour",
    "service_contour",
}


class DataQualityError(ValueError):
    """Raised when a quality check would violate module documentation."""


@dataclass(frozen=True)
class DataQualityRequest:
    input_refs: tuple[str, ...]
    check_level: str
    required_freshness_seconds: int
    required_coverage_ratio: float
    critical_fields: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "DataQualityRequest":
        request_payload = payload.get("quality_check_request")
        if not isinstance(request_payload, Mapping):
            raise DataQualityError("payload must contain quality_check_request")
        allowed_fields = {
            "input_refs",
            "check_level",
            "required_freshness_seconds",
            "required_coverage_ratio",
            "critical_fields",
        }
        extra_fields = sorted(set(request_payload) - allowed_fields)
        if extra_fields:
            raise DataQualityError(f"quality_check_request has undocumented fields: {extra_fields}")
        input_refs = tuple(
            str(ref)
            for ref in (request_payload.get("input_refs") or job.input_refs or ())
            if str(ref).strip()
        )
        invalid_refs = tuple(ref for ref in input_refs if not ref.startswith(ALLOWED_INPUT_REF_PREFIXES))
        if invalid_refs:
            raise DataQualityError(f"input_refs point outside documented stores: {list(invalid_refs)}")
        check_level = str(request_payload.get("check_level") or "").strip()
        if check_level not in VALID_CHECK_LEVELS:
            raise DataQualityError(f"invalid check_level: {check_level}")

        required_freshness_seconds = int(request_payload.get("required_freshness_seconds") or 0)
        if required_freshness_seconds < 0:
            raise DataQualityError("required_freshness_seconds must be non-negative")

        required_coverage_ratio = float(request_payload.get("required_coverage_ratio", 1.0))
        if required_coverage_ratio < 0 or required_coverage_ratio > 1:
            raise DataQualityError("required_coverage_ratio must be within 0..1")

        return cls(
            input_refs=input_refs,
            check_level=check_level,
            required_freshness_seconds=required_freshness_seconds,
            required_coverage_ratio=required_coverage_ratio,
            critical_fields=tuple(str(field_name) for field_name in (request_payload.get("critical_fields") or ())),
        )


DataQualityInput = DataQualityRequest


@dataclass(frozen=True)
class QualityCheckedObject:
    object_ref: str
    object_type: str
    payload: Mapping[str, Any]

    def to_metric_mapping(self) -> dict[str, Any]:
        return {
            **dict(self.payload),
            "object_ref": self.object_ref,
            "object_type": self.object_type,
        }


@dataclass(frozen=True)
class DataQualityReport:
    quality_report_id: str
    input_refs: tuple[str, ...]
    check_level: str
    data_quality_score: float
    coverage_ratio: float
    freshness_status: str
    quality_flags: tuple[str, ...]
    blocking_errors: tuple[str, ...]
    created_at: str
    source_module: str = MODULE_NAME
    calculation_version: str = CALCULATION_VERSION
    confidence_score: float = 1.0
    ttl_seconds: int = 0
    metrics: Mapping[str, float | int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_report_id": self.quality_report_id,
            "input_refs": list(self.input_refs),
            "check_level": self.check_level,
            "data_quality_score": self.data_quality_score,
            "coverage_ratio": self.coverage_ratio,
            "freshness_status": self.freshness_status,
            "quality_flags": list(self.quality_flags),
            "blocking_errors": list(self.blocking_errors),
            "created_at": self.created_at,
            "timestamp": self.created_at,
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "confidence_score": self.confidence_score,
            "ttl_seconds": self.ttl_seconds,
            "metrics": dict(self.metrics),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DataQualityExecutionResult:
    module_job_result: ModuleJobResult
    data_quality_report: DataQualityReport
    data_quality_feature_records: tuple[DataQualityRecord, ...]
    data_quality_record_refs: tuple[str, ...]
    audit_ref: str
    metrics: Mapping[str, float | int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "data_quality_report": self.data_quality_report.to_dict(),
            "quality_flags": list(self.data_quality_report.quality_flags),
            "data_quality_feature_records": [
                record.to_dict() for record in self.data_quality_feature_records
            ],
            "data_quality_record_refs": list(self.data_quality_record_refs),
            "audit_ref": self.audit_ref,
            "metrics": dict(self.metrics),
        }


class DataQualityService:
    module_name = MODULE_NAME

    def __init__(self, repository: DataQualityRepository | None = None) -> None:
        self.repository = repository or InMemoryDataQualityRepository()

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> DataQualityExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> DataQualityExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> DataQualityExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    severity="error",
                    event_type="module_job_missing",
                    message="Data Quality Module requires module_job",
                    object_type="module_job",
                    reason_codes=("module_job_required",),
                    payload={"source_module": self.module_name},
                )
            )
            raise DataQualityError("Data Quality Module requires module_job")

        try:
            self.validate_module_job(job)
            request = self._coerce_input(payload, job)
            checked_objects, load_warnings = self.load_quality_objects(request, payload)
            report, metrics = self.build_quality_report(
                job=job,
                request=request,
                checked_objects=checked_objects,
                warnings=load_warnings,
            )
            records, record_refs = self.write_data_quality_records(report, checked_objects)
            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="warning" if report.quality_flags or report.blocking_errors else "info",
                    event_type="data_quality_checked",
                    message="Data quality checks completed",
                    object_type="data_quality_report",
                    object_ref=report.quality_report_id,
                    reason_codes=tuple(report.quality_flags or ("quality_report_written",)),
                    payload=report.to_dict(),
                )
            )
            return DataQualityExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    report=report,
                    output_refs=record_refs,
                    status="partial_success" if report.quality_flags or report.blocking_errors else "success",
                    warnings=tuple(report.warnings),
                    errors=tuple(report.blocking_errors),
                    metrics=metrics,
                ),
                data_quality_report=report,
                data_quality_feature_records=records,
                data_quality_record_refs=record_refs,
                audit_ref=audit_ref,
                metrics=metrics,
            )
        except (DataQualityError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise DataQualityError("Data Quality Module requires module_job")
        if job.module_name != self.module_name:
            raise DataQualityError("module_job.module_name must be Data Quality Module")
        if job.contour not in VALID_CONTOURS:
            raise DataQualityError(f"invalid module_job.contour for Data Quality Module: {job.contour}")
        if not job.input_refs:
            raise DataQualityError("module_job.input_refs is required")
        if not job.run_mode:
            raise DataQualityError("module_job.run_mode is required")

    def load_quality_objects(
        self,
        request: DataQualityRequest,
        payload: Mapping[str, Any],
    ) -> tuple[tuple[QualityCheckedObject, ...], tuple[str, ...]]:
        objects: list[QualityCheckedObject] = []
        warnings: list[str] = []
        for ref in request.input_refs:
            loaded = self.repository.load_quality_object(ref)
            if loaded is None:
                warnings.append(f"missing_input_ref:{ref}")
                continue
            objects.append(
                QualityCheckedObject(
                    object_ref=ref,
                    object_type=_object_type_from_ref(ref),
                    payload=_to_mapping(loaded),
                )
            )

        return tuple(objects), tuple(warnings)

    def build_quality_report(
        self,
        job: ModuleJob,
        request: DataQualityRequest,
        checked_objects: tuple[QualityCheckedObject, ...],
        warnings: tuple[str, ...],
    ) -> tuple[DataQualityReport, Mapping[str, float | int]]:
        now = utc_now()
        records = tuple(checked_object.to_metric_mapping() for checked_object in checked_objects)

        expected_records = len(request.input_refs) if request.input_refs else len(records)
        available_records = min(len(records), expected_records) if expected_records else len(records)
        missing_records = max(0, expected_records - available_records)
        coverage = coverage_ratio(available_records, expected_records)

        missing_fields = missing_field_count(records, request.critical_fields)
        stale_by_freshness = stale_record_count(records, now, request.required_freshness_seconds)
        expired_by_ttl = expired_record_count(records, now)
        stale_count = max(stale_by_freshness, expired_by_ttl)
        duplicates = duplicate_record_count(records, request.input_refs)
        outliers = outlier_count(records, threshold=OUTLIER_Z_THRESHOLD)
        conflicts = source_conflict_count(records, tolerance=SOURCE_CONFLICT_TOLERANCE)

        expected_field_count = max(1, len(records) * len(request.critical_fields))
        missing_rate = max(
            _rate(missing_records, max(1, expected_records)),
            _rate(missing_fields, expected_field_count),
        )
        stale_rate = _rate(stale_count, max(1, len(records)))
        outlier_rate = _rate(outliers, max(1, len(records)))
        conflict_rate = _rate(conflicts, max(1, len(records)))

        score = data_quality_score(
            missing_rate=missing_rate,
            stale_rate=stale_rate,
            outlier_rate=outlier_rate,
            conflict_rate=conflict_rate,
        )
        flags = quality_flags_from_counts(
            missing_count=missing_fields + missing_records,
            stale_count=stale_count,
            duplicate_count=duplicates,
            outliers=outliers,
            conflicts=conflicts,
            coverage=coverage,
            required_coverage=request.required_coverage_ratio,
        )
        blocking_errors = self._blocking_errors(
            request=request,
            score=score,
            coverage=coverage,
            missing_records=missing_records,
            missing_fields=missing_fields,
            stale_count=stale_count,
            conflicts=conflicts,
        )
        freshness_status = "fresh"
        if expired_by_ttl:
            freshness_status = "expired"
        elif stale_count:
            freshness_status = "stale"

        metrics: dict[str, float | int] = {
            "data_quality_score": score,
            "coverage_ratio": coverage,
            "stale_record_count": stale_count,
            "missing_field_count": missing_fields,
            "outlier_count": outliers,
            "source_conflict_count": conflicts,
            "duplicate_record_count": duplicates,
            "missing_record_count": missing_records,
            "missing_rate": missing_rate,
            "stale_rate": stale_rate,
            "outlier_rate": outlier_rate,
            "conflict_rate": conflict_rate,
        }
        created_at = to_utc_iso(now)
        report_payload = {
            "job_id": job.job_id,
            "input_refs": request.input_refs,
            "check_level": request.check_level,
            "created_at": created_at,
            "metrics": metrics,
            "quality_flags": flags,
        }
        report = DataQualityReport(
            quality_report_id=f"data_quality_report_{_stable_hash(report_payload)[:24]}",
            input_refs=request.input_refs,
            check_level=request.check_level,
            data_quality_score=score,
            coverage_ratio=coverage,
            freshness_status=freshness_status,
            quality_flags=flags,
            blocking_errors=blocking_errors,
            created_at=created_at,
            confidence_score=score,
            ttl_seconds=self._report_ttl_seconds(request, records),
            metrics=metrics,
            warnings=warnings,
        )
        return report, metrics

    def write_data_quality_records(
        self,
        report: DataQualityReport,
        checked_objects: tuple[QualityCheckedObject, ...],
    ) -> tuple[tuple[DataQualityRecord, ...], tuple[str, ...]]:
        records: list[DataQualityRecord] = [
            DataQualityRecord(
                object_type="data_quality_report",
                object_ref=report.quality_report_id,
                quality_score=report.data_quality_score,
                quality_flags=report.quality_flags,
                checked_at=report.created_at,
                source_module=self.module_name,
                calculation_version=CALCULATION_VERSION,
                payload=report.to_dict(),
            )
        ]
        for checked_object in checked_objects:
            records.append(
                DataQualityRecord(
                    object_type=checked_object.object_type,
                    object_ref=checked_object.object_ref,
                    quality_score=report.data_quality_score,
                    quality_flags=report.quality_flags,
                    checked_at=report.created_at,
                    source_module=self.module_name,
                    calculation_version=CALCULATION_VERSION,
                    payload={
                        "quality_report_id": report.quality_report_id,
                        "check_level": report.check_level,
                        "input_refs": list(report.input_refs),
                        "metrics": dict(report.metrics),
                    },
                )
            )

        refs = tuple(self.repository.save_data_quality_record(record) for record in records)
        return tuple(records), refs

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def _coerce_input(self, payload: Mapping[str, Any], job: ModuleJob) -> DataQualityRequest:
        request = DataQualityRequest.from_dict(payload, job)
        if not request.input_refs:
            raise DataQualityError("quality_check_request.input_refs is required")
        if tuple(job.input_refs) and not set(request.input_refs).issubset(set(job.input_refs)):
            raise DataQualityError("quality_check_request.input_refs must be present in module_job.input_refs")
        return request

    def _blocking_errors(
        self,
        *,
        request: DataQualityRequest,
        score: float,
        coverage: float,
        missing_records: int,
        missing_fields: int,
        stale_count: int,
        conflicts: int,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        if missing_records:
            errors.append(f"missing_input_records:{missing_records}")
        if coverage < request.required_coverage_ratio:
            errors.append(
                f"coverage_ratio_below_required:{coverage:.6f}<"
                f"{request.required_coverage_ratio:.6f}"
            )
        if missing_fields:
            errors.append(f"missing_critical_fields:{missing_fields}")
        if conflicts:
            errors.append(f"source_conflicts_detected:{conflicts}")
        if request.check_level in {"decision", "execution"} and stale_count:
            errors.append(f"stale_data_blocks_{request.check_level}:{stale_count}")
        return tuple(errors)

    def _report_ttl_seconds(
        self,
        request: DataQualityRequest,
        records: tuple[Mapping[str, Any], ...],
    ) -> int:
        object_ttls = tuple(
            ttl
            for ttl in (_optional_int(record.get("ttl_seconds")) for record in records)
            if ttl is not None and ttl >= 0
        )
        candidates = object_ttls
        if request.required_freshness_seconds > 0:
            candidates = (*candidates, request.required_freshness_seconds)
        if candidates:
            return min(candidates)
        return request.required_freshness_seconds or 0

    def _module_job_result(
        self,
        job: ModuleJob,
        started_at: str,
        report: DataQualityReport,
        output_refs: tuple[str, ...],
        status: str,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        metrics: Mapping[str, float | int],
    ) -> ModuleJobResult:
        return ModuleJobResult(
            job_id=job.job_id,
            module_name=self.module_name,
            status=status,
            started_at=started_at,
            finished_at=to_utc_iso(utc_now()),
            output_refs=output_refs,
            warnings=warnings,
            errors=errors,
            metrics_written=len(metrics),
            events_written=0,
            data_quality_score=report.data_quality_score,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> DataQualityExecutionResult:
        created_at = to_utc_iso(utc_now())
        report = DataQualityReport(
            quality_report_id=f"data_quality_report_{_stable_hash({'job_id': job.job_id, 'error': str(error)})[:24]}",
            input_refs=tuple(job.input_refs),
            check_level="raw",
            data_quality_score=0.0,
            coverage_ratio=0.0,
            freshness_status="expired",
            quality_flags=("missing_data",),
            blocking_errors=(str(error),),
            created_at=created_at,
            confidence_score=0.0,
            metrics={
                "data_quality_score": 0.0,
                "coverage_ratio": 0.0,
                "stale_record_count": 0,
                "missing_field_count": 0,
                "outlier_count": 0,
                "source_conflict_count": 0,
            },
        )
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity="error",
                event_type="data_quality_failed",
                message="Data quality checks failed",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=("data_quality_failed",),
                payload=report.to_dict(),
            )
        )
        module_job_result = ModuleJobResult(
            job_id=job.job_id,
            module_name=self.module_name,
            status="failed",
            started_at=started_at,
            finished_at=to_utc_iso(utc_now()),
            output_refs=(),
            warnings=(),
            errors=(str(error),),
            metrics_written=0,
            events_written=0,
            data_quality_score=0.0,
        )
        return DataQualityExecutionResult(
            module_job_result=module_job_result,
            data_quality_report=report,
            data_quality_feature_records=(),
            data_quality_record_refs=(),
            audit_ref=audit_ref,
            metrics=report.metrics,
        )


def _object_type_from_ref(ref: str) -> str:
    if ":" not in ref:
        return "unknown"
    return ref.split(":", 1)[0]


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _to_mapping(item: Any) -> Mapping[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if isinstance(item, Mapping):
        return item
    return dict(getattr(item, "__dict__", {}))


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, count / total))


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
