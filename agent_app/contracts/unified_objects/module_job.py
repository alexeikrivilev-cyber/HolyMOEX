from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


VALID_TRIGGER_TYPES = {"scheduled", "event", "dependency", "manual", "replay"}
VALID_RUN_MODES = {"analysis_only", "paper_trading", "live_trading", "backtest", "replay"}
VALID_PRIORITIES = {"low", "normal", "high", "critical"}
VALID_HORIZONS = {"intraday", "swing", "position"}
VALID_JOB_STATUSES = {"pending", "running", "success", "partial_success", "skipped", "failed"}
VALID_RESULT_STATUSES = {"success", "partial_success", "skipped", "failed"}


class ContractValidationError(ValueError):
    """Raised when a unified object violates the documented contract."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ContractValidationError("timestamps must be timezone-aware UTC values")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str) -> datetime:
    if not value:
        raise ContractValidationError("timestamp must be non-empty")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContractValidationError("timestamp must include timezone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class TimeRange:
    from_ts: str
    to_ts: str
    timezone: str = "UTC"

    @classmethod
    def instant(cls, at: datetime | None = None) -> "TimeRange":
        now = at or utc_now()
        ts = to_utc_iso(now)
        return cls(from_ts=ts, to_ts=ts, timezone="UTC")

    def __post_init__(self) -> None:
        if self.timezone != "UTC":
            raise ContractValidationError("time_range.timezone must be UTC")
        from_dt = parse_utc_iso(self.from_ts)
        to_dt = parse_utc_iso(self.to_ts)
        if from_dt > to_dt:
            raise ContractValidationError("time_range.from_ts must be <= time_range.to_ts")

    def to_dict(self) -> dict[str, str]:
        return {
            "from_ts": self.from_ts,
            "to_ts": self.to_ts,
            "timezone": self.timezone,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TimeRange":
        return cls(
            from_ts=str(payload.get("from_ts", "")),
            to_ts=str(payload.get("to_ts", "")),
            timezone=str(payload.get("timezone", "UTC")),
        )


@dataclass(frozen=True)
class ModuleJob:
    job_id: str
    module_name: str
    contour: str
    trigger_type: str
    universe_id: str
    instrument_ids: tuple[str, ...]
    horizons: tuple[str, ...]
    time_range: TimeRange
    input_refs: tuple[str, ...] = field(default_factory=tuple)
    config_ref: str | None = None
    run_mode: str = "analysis_only"
    idempotency_key: str = ""
    priority: str = "normal"
    status: str = "pending"

    def __post_init__(self) -> None:
        required = {
            "job_id": self.job_id,
            "module_name": self.module_name,
            "contour": self.contour,
            "trigger_type": self.trigger_type,
            "universe_id": self.universe_id,
            "run_mode": self.run_mode,
            "idempotency_key": self.idempotency_key,
            "priority": self.priority,
            "status": self.status,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ContractValidationError(f"module_job missing required fields: {missing}")
        if self.trigger_type not in VALID_TRIGGER_TYPES:
            raise ContractValidationError(f"invalid trigger_type: {self.trigger_type}")
        if self.run_mode not in VALID_RUN_MODES:
            raise ContractValidationError(f"invalid run_mode: {self.run_mode}")
        if self.priority not in VALID_PRIORITIES:
            raise ContractValidationError(f"invalid priority: {self.priority}")
        if self.status not in VALID_JOB_STATUSES:
            raise ContractValidationError(f"invalid module_job status: {self.status}")
        invalid_horizons = sorted(set(self.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise ContractValidationError(f"invalid horizons: {invalid_horizons}")
        if not isinstance(self.time_range, TimeRange):
            raise ContractValidationError("time_range must be a TimeRange")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "module_name": self.module_name,
            "contour": self.contour,
            "trigger_type": self.trigger_type,
            "universe_id": self.universe_id,
            "instrument_ids": list(self.instrument_ids),
            "horizons": list(self.horizons),
            "time_range": self.time_range.to_dict(),
            "input_refs": list(self.input_refs),
            "config_ref": self.config_ref,
            "run_mode": self.run_mode,
            "idempotency_key": self.idempotency_key,
            "priority": self.priority,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModuleJob":
        return cls(
            job_id=str(payload.get("job_id", "")),
            module_name=str(payload.get("module_name", "")),
            contour=str(payload.get("contour", "")),
            trigger_type=str(payload.get("trigger_type", "")),
            universe_id=str(payload.get("universe_id", "")),
            instrument_ids=tuple(payload.get("instrument_ids") or ()),
            horizons=tuple(payload.get("horizons") or ()),
            time_range=TimeRange.from_dict(payload.get("time_range") or {}),
            input_refs=tuple(payload.get("input_refs") or ()),
            config_ref=payload.get("config_ref"),
            run_mode=str(payload.get("run_mode", "")),
            idempotency_key=str(payload.get("idempotency_key", "")),
            priority=str(payload.get("priority", "normal")),
            status=str(payload.get("status", "pending")),
        )


@dataclass(frozen=True)
class ModuleJobResult:
    job_id: str
    module_name: str
    status: str
    started_at: str
    finished_at: str
    output_refs: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)
    metrics_written: int = 0
    events_written: int = 0
    data_quality_score: float | None = None

    def __post_init__(self) -> None:
        required = {
            "job_id": self.job_id,
            "module_name": self.module_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ContractValidationError(f"module_job_result missing required fields: {missing}")
        if self.status not in VALID_RESULT_STATUSES:
            raise ContractValidationError(f"invalid module_job_result status: {self.status}")
        started_at = parse_utc_iso(self.started_at)
        finished_at = parse_utc_iso(self.finished_at)
        if started_at > finished_at:
            raise ContractValidationError("started_at must be <= finished_at")
        if self.metrics_written < 0 or self.events_written < 0:
            raise ContractValidationError("written counters must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "module_name": self.module_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_refs": list(self.output_refs),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metrics_written": self.metrics_written,
            "events_written": self.events_written,
            "data_quality_score": self.data_quality_score,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ModuleJobResult":
        return cls(
            job_id=str(payload.get("job_id", "")),
            module_name=str(payload.get("module_name", "")),
            status=str(payload.get("status", "")),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            output_refs=tuple(payload.get("output_refs") or ()),
            warnings=tuple(payload.get("warnings") or ()),
            errors=tuple(payload.get("errors") or ()),
            metrics_written=int(payload.get("metrics_written") or 0),
            events_written=int(payload.get("events_written") or 0),
            data_quality_score=payload.get("data_quality_score"),
        )

