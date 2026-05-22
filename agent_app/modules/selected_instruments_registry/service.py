from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ExternalRequest, ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.external_request import CachePolicy, RetryPolicy
from agent_app.contracts.unified_objects.module_job import ContractValidationError, to_utc_iso, utc_now

from .metrics import (
    active_instrument_count,
    inactive_instrument_count,
    mapping_conflict_count,
    metadata_completeness_score,
)
from .repository import (
    AuditRecord,
    InMemorySelectedInstrumentsRegistryRepository,
    InstrumentAlias,
    InstrumentMapping,
    InstrumentUniverse,
    SelectedInstrumentsRegistryRepository,
)


MODULE_NAME = "Selected Instruments Registry Module"
CALCULATION_VERSION = "selected_instruments_registry_v1"
VALID_QUANTITY_MODES = {"shares", "lots"}
VALID_HORIZONS = {"intraday", "swing", "position"}
BOARD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class SelectedInstrumentsRegistryError(ValueError):
    """Raised when registry execution would violate module documentation."""


@dataclass(frozen=True)
class InstrumentProfile:
    instrument_id: str
    universe_id: str
    ticker: str
    figi: str | None = None
    isin: str | None = None
    class_code: str | None = None
    board_id: str | None = None
    lot_size: int | None = None
    min_price_increment: float | None = None
    currency: str = "RUB"
    sector: str | None = None
    issuer_name: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    related_entities: tuple[str, ...] = field(default_factory=tuple)
    is_active: bool = True
    tradable: bool = True
    allowed_horizons: tuple[str, ...] = ("intraday", "swing", "position")
    arena_go_secid: str | None = None
    arena_go_quantity_mode: str = "shares"
    max_trade_quantity: int | None = None
    execution_enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        required = {
            "instrument_id": self.instrument_id,
            "universe_id": self.universe_id,
            "ticker": self.ticker,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise SelectedInstrumentsRegistryError(f"instrument_profile missing required fields: {missing}")
        if self.arena_go_quantity_mode not in VALID_QUANTITY_MODES:
            raise SelectedInstrumentsRegistryError(
                f"invalid arena_go_quantity_mode for {self.instrument_id}: {self.arena_go_quantity_mode}"
            )
        invalid_horizons = sorted(set(self.allowed_horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise SelectedInstrumentsRegistryError(
                f"invalid allowed_horizons for {self.instrument_id}: {invalid_horizons}"
            )
        if self.lot_size is not None and self.lot_size <= 0:
            raise SelectedInstrumentsRegistryError(f"lot_size must be positive for {self.instrument_id}")
        if self.min_price_increment is not None and self.min_price_increment <= 0:
            raise SelectedInstrumentsRegistryError(
                f"min_price_increment must be positive for {self.instrument_id}"
            )
        if self.max_trade_quantity is not None and self.max_trade_quantity < 0:
            raise SelectedInstrumentsRegistryError(
                f"max_trade_quantity must be non-negative for {self.instrument_id}"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], universe_id: str | None = None) -> "InstrumentProfile":
        if "instrument_profile" in payload and isinstance(payload["instrument_profile"], Mapping):
            payload = payload["instrument_profile"]  # type: ignore[assignment]
        metadata = dict(payload.get("metadata") or {})
        max_trade_quantity = payload.get("max_trade_quantity", metadata.get("max_trade_quantity"))
        execution_enabled = payload.get("execution_enabled", metadata.get("execution_enabled", True))
        return cls(
            instrument_id=str(payload.get("instrument_id", "")).strip(),
            universe_id=str(payload.get("universe_id") or universe_id or "").strip(),
            ticker=str(payload.get("ticker", "")).strip().upper(),
            figi=_optional_text(payload.get("figi")),
            isin=_optional_text(payload.get("isin")),
            class_code=_optional_text(payload.get("class_code")),
            board_id=_optional_text(payload.get("board_id")),
            lot_size=_optional_int(payload.get("lot_size")),
            min_price_increment=_optional_float(payload.get("min_price_increment")),
            currency=str(payload.get("currency") or "RUB").strip().upper(),
            sector=_optional_text(payload.get("sector")),
            issuer_name=_optional_text(payload.get("issuer_name")),
            aliases=tuple(str(alias) for alias in (payload.get("aliases") or ())),
            related_entities=tuple(str(entity) for entity in (payload.get("related_entities") or ())),
            is_active=bool(payload.get("is_active", True)),
            tradable=bool(payload.get("tradable", True)),
            allowed_horizons=tuple(payload.get("allowed_horizons") or ("intraday", "swing", "position")),
            arena_go_secid=_optional_text(payload.get("arena_go_secid")),
            arena_go_quantity_mode=str(payload.get("arena_go_quantity_mode") or "shares"),
            max_trade_quantity=_optional_int(max_trade_quantity, default=0),
            execution_enabled=bool(execution_enabled),
            metadata=metadata,
            created_at=_optional_text(payload.get("created_at")),
            updated_at=_optional_text(payload.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "universe_id": self.universe_id,
            "ticker": self.ticker,
            "figi": self.figi,
            "isin": self.isin,
            "class_code": self.class_code,
            "board_id": self.board_id,
            "lot_size": self.lot_size,
            "min_price_increment": self.min_price_increment,
            "currency": self.currency,
            "sector": self.sector,
            "issuer_name": self.issuer_name,
            "aliases": list(self.aliases),
            "related_entities": list(self.related_entities),
            "is_active": self.is_active,
            "tradable": self.tradable,
            "allowed_horizons": list(self.allowed_horizons),
            "arena_go_secid": self.arena_go_secid,
            "arena_go_quantity_mode": self.arena_go_quantity_mode,
            "max_trade_quantity": self.max_trade_quantity,
            "execution_enabled": self.execution_enabled,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SelectedInstrumentsRegistryInput:
    universe_id: str
    instrument_updates: tuple[InstrumentProfile, ...] = field(default_factory=tuple)
    metadata_refresh: bool = False
    metadata_provider: str = "moex_iss"

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        job_universe_id: str | None = None,
    ) -> "SelectedInstrumentsRegistryInput":
        if "selected_instruments_registry_input" in payload and isinstance(
            payload["selected_instruments_registry_input"], Mapping
        ):
            payload = payload["selected_instruments_registry_input"]  # type: ignore[assignment]
        universe_id = str(payload.get("universe_id") or job_universe_id or "").strip()
        updates = tuple(
            InstrumentProfile.from_dict(update, universe_id=universe_id)
            for update in (payload.get("instrument_updates") or ())
        )
        return cls(
            universe_id=universe_id,
            instrument_updates=updates,
            metadata_refresh=bool(payload.get("metadata_refresh", False)),
            metadata_provider=str(payload.get("metadata_provider") or "moex_iss"),
        )


@dataclass(frozen=True)
class UniverseSnapshot:
    universe_snapshot_id: str
    universe_id: str
    active_instrument_ids: tuple[str, ...]
    instrument_profiles_ref: str
    created_at: str
    validation_status: str
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metrics: Mapping[str, float | int] = field(default_factory=dict)
    source_module: str = MODULE_NAME
    calculation_version: str = CALCULATION_VERSION
    confidence_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_snapshot_id": self.universe_snapshot_id,
            "universe_id": self.universe_id,
            "active_instrument_ids": list(self.active_instrument_ids),
            "instrument_profiles_ref": self.instrument_profiles_ref,
            "created_at": self.created_at,
            "timestamp": self.created_at,
            "validation_status": self.validation_status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "confidence_score": self.confidence_score,
        }


@dataclass(frozen=True)
class RegistryExecutionResult:
    snapshot: UniverseSnapshot
    module_job_result: ModuleJobResult
    audit_ref: str
    snapshot_ref: str
    metadata_request: ExternalRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_snapshot": self.snapshot.to_dict(),
            "module_job_result": self.module_job_result.to_dict(),
            "audit_ref": self.audit_ref,
            "snapshot_ref": self.snapshot_ref,
            "metadata_request": self.metadata_request.to_dict() if self.metadata_request else None,
        }


class SelectedInstrumentsRegistryService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: SelectedInstrumentsRegistryRepository | None = None,
        gateway: Any | None = None,
    ) -> None:
        self.repository = repository or InMemorySelectedInstrumentsRegistryRepository()
        self.gateway = gateway

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> RegistryExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> RegistryExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> RegistryExecutionResult:
        started_at = to_utc_iso(utc_now())
        metadata_request: ExternalRequest | None = None
        if job is None:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    severity="error",
                    event_type="module_job_missing",
                    message="Selected Instruments Registry Module requires module_job",
                    object_type="module_job",
                    object_ref=None,
                    reason_codes=("module_job_required",),
                    payload={"source_module": self.module_name},
                )
            )
            raise SelectedInstrumentsRegistryError("Selected Instruments Registry Module requires module_job")
        try:
            self.validate_module_job(job)
            registry_input = self._coerce_input(payload, job)
            universe = self._load_or_create_universe(registry_input.universe_id)
            existing_profiles = self.repository.list_instrument_profiles(registry_input.universe_id)
            candidate_profiles = self._candidate_profiles(existing_profiles, registry_input.instrument_updates)

            validation_errors = self.validate_unique_instrument_id(registry_input.instrument_updates)
            validation_errors += self.validate_manual_approval_for_new_instruments(
                existing_profiles,
                registry_input.instrument_updates,
                job,
            )
            validation_errors += self.validate_active_count_max_20(candidate_profiles, universe.max_active_instruments)
            validation_errors += self.validate_lot_size(registry_input.instrument_updates)
            validation_errors += self.validate_board_id(registry_input.instrument_updates)
            validation_errors += self.detect_duplicate_aliases(candidate_profiles)

            if registry_input.metadata_refresh:
                metadata_request = self.create_metadata_refresh_request(registry_input, job)
                if self.gateway is not None:
                    self.gateway.process(metadata_request)

            if validation_errors:
                profiles_for_snapshot = tuple(
                    profile.to_dict() if hasattr(profile, "to_dict") else dict(profile)
                    for profile in candidate_profiles
                )
                snapshot = self.publish_universe_snapshot(
                    universe_id=registry_input.universe_id,
                    profiles_payload=profiles_for_snapshot,
                    errors=tuple(validation_errors),
                    warnings=(),
                )
                snapshot_ref = self.repository.save_universe_snapshot(snapshot)
                audit_ref = self.write_audit_record(
                    AuditRecord(
                        module_name=self.module_name,
                        job_id=job.job_id,
                        severity="error",
                        event_type="registry_validation_failed",
                        message="Selected instruments registry validation failed",
                        object_type="universe_snapshot",
                        object_ref=snapshot.universe_snapshot_id,
                        reason_codes=("registry_validation_failed",),
                        payload=snapshot.to_dict(),
                    )
                )
                return self._execution_result(
                    job=job,
                    started_at=started_at,
                    snapshot=snapshot,
                    snapshot_ref=snapshot_ref,
                    audit_ref=audit_ref,
                    metadata_request=metadata_request,
                    status="failed",
                    warnings=(),
                    errors=tuple(validation_errors),
                )

            normalized_updates, warnings = self.prepare_profiles_for_write(registry_input.instrument_updates)
            for profile in normalized_updates:
                self.write_instrument_profile(profile)

            profiles_after_write = self.repository.list_instrument_profiles(registry_input.universe_id)
            profiles_payload = tuple(profile.to_dict() for profile in profiles_after_write)
            snapshot_errors = self._snapshot_errors(profiles_payload, universe.max_active_instruments)
            snapshot = self.publish_universe_snapshot(
                universe_id=registry_input.universe_id,
                profiles_payload=profiles_payload,
                errors=snapshot_errors,
                warnings=warnings,
            )
            snapshot_ref = self.repository.save_universe_snapshot(snapshot)
            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=job.job_id,
                    severity="info" if snapshot.validation_status == "valid" else "warning",
                    event_type="universe_snapshot_published",
                    message="Selected instruments universe snapshot published",
                    object_type="universe_snapshot",
                    object_ref=snapshot.universe_snapshot_id,
                    reason_codes=("universe_snapshot_versioned", snapshot.validation_status),
                    payload=snapshot.to_dict(),
                )
            )
            status = "success" if snapshot.validation_status == "valid" else "partial_success"
            return self._execution_result(
                job=job,
                started_at=started_at,
                snapshot=snapshot,
                snapshot_ref=snapshot_ref,
                audit_ref=audit_ref,
                metadata_request=metadata_request,
                status=status,
                warnings=tuple(snapshot.warnings),
                errors=tuple(snapshot.errors),
            )
        except (SelectedInstrumentsRegistryError, ContractValidationError) as error:
            fallback_universe_id = getattr(job, "universe_id", "") or str(payload.get("universe_id") or "")
            snapshot = self.publish_universe_snapshot(
                universe_id=fallback_universe_id,
                profiles_payload=(),
                errors=(str(error),),
                warnings=(),
            )
            snapshot_ref = self.repository.save_universe_snapshot(snapshot)
            audit_ref = self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=getattr(job, "job_id", None),
                    severity="error",
                    event_type="registry_execution_failed",
                    message="Selected instruments registry execution failed",
                    object_type="universe_snapshot",
                    object_ref=snapshot.universe_snapshot_id,
                    reason_codes=("registry_execution_failed",),
                    payload=snapshot.to_dict(),
                )
            )
            return self._execution_result(
                job=job,
                started_at=started_at,
                snapshot=snapshot,
                snapshot_ref=snapshot_ref,
                audit_ref=audit_ref,
                metadata_request=metadata_request,
                status="failed",
                warnings=(),
                errors=(str(error),),
            )

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise SelectedInstrumentsRegistryError("Selected Instruments Registry Module requires module_job")
        if job.module_name != self.module_name:
            raise SelectedInstrumentsRegistryError("module_job.module_name must be Selected Instruments Registry Module")
        if job.contour != "service_contour":
            raise SelectedInstrumentsRegistryError("module_job.contour must be service_contour")
        if not job.universe_id:
            raise SelectedInstrumentsRegistryError("module_job.universe_id is required")
        if not job.run_mode:
            raise SelectedInstrumentsRegistryError("module_job.run_mode is required")
        if not job.idempotency_key:
            raise SelectedInstrumentsRegistryError("module_job.idempotency_key is required")

    def validate_unique_instrument_id(self, profiles: tuple[InstrumentProfile, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for profile in profiles:
            if profile.instrument_id in seen:
                duplicates.append(f"duplicate_instrument_id:{profile.instrument_id}")
            seen.add(profile.instrument_id)
        return tuple(duplicates)

    def validate_active_count_max_20(
        self,
        profiles: tuple[InstrumentProfile, ...],
        max_active_instruments: int = 20,
    ) -> tuple[str, ...]:
        count = active_instrument_count(profile.to_dict() for profile in profiles)
        if count > min(max_active_instruments, 20):
            return (f"active_instrument_count_exceeds_20:{count}",)
        return ()

    def validate_manual_approval_for_new_instruments(
        self,
        existing_profiles: tuple[InstrumentProfile, ...],
        updates: tuple[InstrumentProfile, ...],
        job: ModuleJob,
    ) -> tuple[str, ...]:
        if job.trigger_type == "manual":
            return ()
        existing_ids = {profile.instrument_id for profile in existing_profiles}
        new_ids = tuple(
            profile.instrument_id
            for profile in updates
            if profile.instrument_id not in existing_ids
        )
        return tuple(f"manual_approval_required_for_new_instrument:{instrument_id}" for instrument_id in new_ids)

    def validate_lot_size(self, profiles: tuple[InstrumentProfile, ...]) -> tuple[str, ...]:
        errors = []
        for profile in profiles:
            if profile.is_active and (profile.lot_size is None or profile.lot_size <= 0):
                errors.append(f"invalid_lot_size:{profile.instrument_id}")
        return tuple(errors)

    def validate_board_id(self, profiles: tuple[InstrumentProfile, ...]) -> tuple[str, ...]:
        errors = []
        for profile in profiles:
            board_id = profile.board_id or ""
            if profile.is_active and not BOARD_ID_RE.match(board_id):
                errors.append(f"invalid_board_id:{profile.instrument_id}")
        return tuple(errors)

    def detect_duplicate_aliases(self, profiles: tuple[InstrumentProfile, ...]) -> tuple[str, ...]:
        owner_by_alias: dict[str, str] = {}
        errors: list[str] = []
        for profile in profiles:
            aliases = self.normalize_aliases((profile.ticker, profile.figi, profile.isin, *profile.aliases))
            for alias in aliases:
                owner = owner_by_alias.get(alias)
                if owner is not None and owner != profile.instrument_id:
                    errors.append(f"duplicate_alias:{alias}:{owner}:{profile.instrument_id}")
                owner_by_alias[alias] = profile.instrument_id
        return tuple(errors)

    def normalize_aliases(self, aliases: tuple[str | None, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for alias in aliases:
            text = str(alias or "").strip().upper()
            if text and text not in normalized:
                normalized.append(text)
        return tuple(normalized)

    def map_provider_ids(self, profile: InstrumentProfile) -> tuple[InstrumentMapping, ...]:
        mappings: list[InstrumentMapping] = []
        if profile.arena_go_secid:
            mappings.append(
                InstrumentMapping(
                    instrument_id=profile.instrument_id,
                    provider="arena_go",
                    provider_symbol=profile.arena_go_secid,
                    provider_payload={
                        "quantity_mode": profile.arena_go_quantity_mode,
                        "lot_size": profile.lot_size,
                        "max_trade_quantity": profile.max_trade_quantity,
                    },
                )
            )
        if profile.ticker:
            mappings.append(
                InstrumentMapping(
                    instrument_id=profile.instrument_id,
                    provider="moex_iss",
                    provider_symbol=profile.ticker,
                    provider_payload={
                        "board_id": profile.board_id,
                        "class_code": profile.class_code,
                        "isin": profile.isin,
                        "figi": profile.figi,
                    },
                )
            )
        return tuple(mappings)

    def prepare_profiles_for_write(
        self,
        profiles: tuple[InstrumentProfile, ...],
    ) -> tuple[tuple[InstrumentProfile, ...], tuple[str, ...]]:
        normalized_profiles: list[InstrumentProfile] = []
        warnings: list[str] = []
        for profile in profiles:
            aliases = self.normalize_aliases((profile.ticker, profile.figi, profile.isin, *profile.aliases))
            tradable = profile.tradable
            execution_enabled = profile.execution_enabled
            if profile.is_active and not self._has_required_mapping(profile):
                tradable = False
                execution_enabled = False
                warnings.append(f"mapping_incomplete_tradable_false:{profile.instrument_id}")
            if profile.is_active and not profile.arena_go_secid:
                tradable = False
                execution_enabled = False
                warnings.append(f"arena_go_secid_missing_execution_disabled:{profile.instrument_id}")

            metadata = {
                **dict(profile.metadata),
                "max_trade_quantity": profile.max_trade_quantity,
                "execution_enabled": execution_enabled,
                "source_module": self.module_name,
                "calculation_version": CALCULATION_VERSION,
            }
            normalized_profiles.append(
                replace(
                    profile,
                    ticker=profile.ticker.upper(),
                    aliases=aliases,
                    arena_go_secid=profile.arena_go_secid.upper() if profile.arena_go_secid else None,
                    tradable=tradable,
                    execution_enabled=execution_enabled,
                    metadata=metadata,
                    updated_at=to_utc_iso(utc_now()),
                )
            )
        return tuple(normalized_profiles), tuple(dict.fromkeys(warnings))

    def write_instrument_profile(self, profile: InstrumentProfile) -> None:
        self.repository.save_instrument_profile(profile)
        aliases = tuple(
            InstrumentAlias(
                instrument_id=profile.instrument_id,
                alias=alias,
                alias_type="identifier" if alias in {profile.figi, profile.isin} else "ticker",
                source="selected_instruments_registry",
            )
            for alias in profile.aliases
        )
        self.repository.replace_aliases(profile.instrument_id, aliases)
        for mapping in self.map_provider_ids(profile):
            self.repository.save_instrument_mapping(mapping)

    def publish_universe_snapshot(
        self,
        universe_id: str,
        profiles_payload: tuple[Mapping[str, Any], ...],
        errors: tuple[str, ...],
        warnings: tuple[str, ...],
    ) -> UniverseSnapshot:
        created_at = to_utc_iso(utc_now())
        active_ids = tuple(
            str(profile["instrument_id"])
            for profile in sorted(profiles_payload, key=lambda item: str(item.get("instrument_id") or ""))
            if bool(profile.get("is_active", False))
        )
        metrics: dict[str, float | int] = {
            "active_instrument_count": active_instrument_count(profiles_payload),
            "metadata_completeness_score": metadata_completeness_score(profiles_payload),
            "mapping_conflict_count": mapping_conflict_count(profiles_payload),
            "inactive_instrument_count": inactive_instrument_count(profiles_payload),
        }
        snapshot_payload = {
            "universe_id": universe_id,
            "active_instrument_ids": active_ids,
            "instrument_profiles_ref": f"registry.instrument_profile:{universe_id}",
            "created_at": created_at,
            "errors": errors,
            "warnings": warnings,
            "metrics": metrics,
            "calculation_version": CALCULATION_VERSION,
        }
        snapshot_id = f"universe_snapshot_{self._stable_hash(snapshot_payload)[:24]}"
        validation_status = "valid" if not errors and metrics["mapping_conflict_count"] == 0 else "invalid"
        if validation_status == "valid" and warnings:
            validation_status = "invalid"
        return UniverseSnapshot(
            universe_snapshot_id=snapshot_id,
            universe_id=universe_id,
            active_instrument_ids=active_ids,
            instrument_profiles_ref=f"registry.instrument_profile:{universe_id}",
            created_at=created_at,
            validation_status=validation_status,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
            confidence_score=1.0 if validation_status == "valid" else 0.0,
        )

    def create_metadata_refresh_request(
        self,
        registry_input: SelectedInstrumentsRegistryInput,
        job: ModuleJob,
    ) -> ExternalRequest:
        request_payload = {
            "operation": "instrument_metadata_refresh",
            "board_ids": sorted(
                {
                    profile.board_id
                    for profile in registry_input.instrument_updates
                    if profile.board_id
                }
            ),
            "class_codes": sorted(
                {
                    profile.class_code
                    for profile in registry_input.instrument_updates
                    if profile.class_code
                }
            ),
        }
        idempotency_key = f"{job.idempotency_key}:instrument_metadata"
        request_id = f"request_{self._stable_hash({'idempotency_key': idempotency_key})[:24]}"
        return ExternalRequest(
            request_id=request_id,
            caller_module=self.module_name,
            provider=registry_input.metadata_provider,
            request_type="instruments",
            universe_id=registry_input.universe_id,
            instrument_ids=tuple(profile.instrument_id for profile in registry_input.instrument_updates)
            or job.instrument_ids,
            payload=request_payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=86400, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
        )

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def _coerce_input(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> SelectedInstrumentsRegistryInput:
        registry_input = SelectedInstrumentsRegistryInput.from_dict(payload, job_universe_id=job.universe_id)
        if not registry_input.universe_id:
            raise SelectedInstrumentsRegistryError("universe_id is required")
        if registry_input.universe_id != job.universe_id:
            raise SelectedInstrumentsRegistryError("input.universe_id must match module_job.universe_id")
        return registry_input

    def _load_or_create_universe(self, universe_id: str) -> InstrumentUniverse:
        universe = self.repository.load_universe(universe_id)
        if universe is not None:
            return universe
        universe = InstrumentUniverse(
            universe_id=universe_id,
            universe_name=universe_id,
            max_active_instruments=20,
            status="active",
        )
        self.repository.ensure_universe(universe)
        return universe

    def _candidate_profiles(
        self,
        existing_profiles: tuple[InstrumentProfile, ...],
        updates: tuple[InstrumentProfile, ...],
    ) -> tuple[InstrumentProfile, ...]:
        by_id = {profile.instrument_id: profile for profile in existing_profiles}
        for update in updates:
            by_id[update.instrument_id] = update
        return tuple(sorted(by_id.values(), key=lambda profile: profile.instrument_id))

    def _snapshot_errors(
        self,
        profiles_payload: tuple[Mapping[str, Any], ...],
        max_active_instruments: int,
    ) -> tuple[str, ...]:
        errors: list[str] = []
        active_count = active_instrument_count(profiles_payload)
        if active_count > min(max_active_instruments, 20):
            errors.append(f"active_instrument_count_exceeds_20:{active_count}")
        completeness = metadata_completeness_score(profiles_payload)
        if active_count and completeness < 1.0:
            errors.append(f"metadata_completeness_score_below_1:{completeness:.6f}")
        conflicts = mapping_conflict_count(profiles_payload)
        if conflicts:
            errors.append(f"mapping_conflict_count:{conflicts}")
        return tuple(errors)

    def _has_required_mapping(self, profile: InstrumentProfile) -> bool:
        return all(
            (
                profile.instrument_id,
                profile.ticker,
                profile.figi,
                profile.isin,
                profile.board_id,
                profile.lot_size,
                profile.min_price_increment,
                profile.currency,
                profile.arena_go_secid,
                profile.arena_go_quantity_mode,
            )
        )

    def _execution_result(
        self,
        job: ModuleJob,
        started_at: str,
        snapshot: UniverseSnapshot,
        snapshot_ref: str,
        audit_ref: str,
        metadata_request: ExternalRequest | None,
        status: str,
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
    ) -> RegistryExecutionResult:
        finished_at = to_utc_iso(utc_now())
        result = ModuleJobResult(
            job_id=job.job_id,
            module_name=self.module_name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            output_refs=(snapshot_ref,),
            warnings=warnings,
            errors=errors,
            metrics_written=4,
            events_written=0,
            data_quality_score=float(snapshot.metrics.get("metadata_completeness_score") or 0.0),
        )
        return RegistryExecutionResult(
            snapshot=snapshot,
            module_job_result=result,
            audit_ref=audit_ref,
            snapshot_ref=snapshot_ref,
            metadata_request=metadata_request,
        )

    def _stable_hash(self, payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
