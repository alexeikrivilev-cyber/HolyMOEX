from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from .metrics import (
    coverage_ratio,
    fresh_feature_ratio,
    percentile_rank,
    signed_to_unit,
    winsorize,
    zscore,
)
from .repository import (
    DataQualityRecord,
    FeatureRecord,
    FeatureVector,
    InMemoryNormalizationFeatureVectorRepository,
    InstrumentProfile,
    NormalizationFeatureVectorRepository,
    stable_record_id,
)


MODULE_NAME = "Normalization & Feature Vector Module"
CALCULATION_VERSION = "normalization_feature_vector_v1"
DEFAULT_MARKET_TIMEZONE = "Europe/Moscow"
DEFAULT_MOEX_CLOSE_TIME = time(18, 50)
DEFAULT_ARENA_GO_CLOSE_TIME = time(23, 50)
DEFAULT_ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS = 6 * 60 * 60

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
VALID_HORIZONS = {"intraday", "swing", "position"}
INPUT_FIELDS = {
    "instrument_ids",
    "horizons",
    "feature_refs",
    "normalization_profile_id",
    "as_of_ts",
}
PERMANENT_TTL_SECONDS = 0
DEFAULT_STALE_POLICY = "block_decision"
NATURAL_UNIT_METRICS = {
    "recovery_ratio",
    "gap_risk_score",
    "jump_risk_score",
    "volatility_risk_score",
    "liquidity_risk_score",
    "volatility_percentile",
    "intraday_range_percentile",
    "market_breadth",
    "risk_on_risk_off_score",
    "macro_pressure_score",
    "sector_pressure_score",
    "sector_strength_rank",
    "churn_penalty_score",
    "turnover_deficit_score",
    "trade_urgency_score",
}
SIGNED_RATIO_BOUNDS = {
    "intraday_return": 0.03,
    "market_return_1d": 0.03,
    "currency_return_1d": 0.05,
    "currency_return_5d": 0.08,
    "oil_return_1d": 0.06,
    "oil_return_5d": 0.12,
}


class NormalizationFeatureVectorError(ValueError):
    """Raised when module 15 would violate its documented contract."""


@dataclass(frozen=True)
class NormalizationProfile:
    profile_id: str
    build_version: str
    stale_policy: str = DEFAULT_STALE_POLICY
    winsor_lower_percentile: float = 0.01
    winsor_upper_percentile: float = 0.99
    zscore_bound: float = 3.0
    feature_vector_ttl_seconds: int | None = None
    required_feature_names: tuple[str, ...] = ()

    @classmethod
    def from_profile_id(cls, profile_id: str) -> "NormalizationProfile":
        if not profile_id:
            raise NormalizationFeatureVectorError("normalization_profile_id is required")
        return cls(
            profile_id=profile_id,
            build_version=f"{CALCULATION_VERSION}:{profile_id}",
        )


@dataclass(frozen=True)
class NormalizationFeatureVectorInput:
    instrument_ids: tuple[str, ...]
    horizons: tuple[str, ...]
    feature_refs: tuple[str, ...]
    normalization_profile_id: str
    as_of_ts: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "NormalizationFeatureVectorInput":
        input_payload = payload.get("normalization_input")
        if not isinstance(input_payload, Mapping):
            raise NormalizationFeatureVectorError("payload must contain normalization_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise NormalizationFeatureVectorError(f"normalization_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise NormalizationFeatureVectorError(f"normalization_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise NormalizationFeatureVectorError("normalization_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise NormalizationFeatureVectorError("normalization_input.instrument_ids must match module_job.instrument_ids")

        horizons = tuple(str(item) for item in (input_payload.get("horizons") or ()))
        if not horizons:
            raise NormalizationFeatureVectorError("normalization_input.horizons is required")
        invalid_horizons = sorted(set(horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise NormalizationFeatureVectorError(f"invalid horizons: {invalid_horizons}")
        if tuple(job.horizons) and horizons != tuple(job.horizons):
            raise NormalizationFeatureVectorError("normalization_input.horizons must match module_job.horizons")

        as_of_ts = str(input_payload.get("as_of_ts") or "")
        parse_utc_iso(as_of_ts)
        if parse_utc_iso(as_of_ts) > parse_utc_iso(job.time_range.to_ts):
            raise NormalizationFeatureVectorError("normalization_input.as_of_ts must be <= module_job.time_range.to_ts")

        profile_id = str(input_payload.get("normalization_profile_id") or "")
        if not profile_id:
            raise NormalizationFeatureVectorError("normalization_input.normalization_profile_id is required")

        return cls(
            instrument_ids=instrument_ids,
            horizons=horizons,
            feature_refs=tuple(str(item) for item in (input_payload.get("feature_refs") or ())),
            normalization_profile_id=profile_id,
            as_of_ts=as_of_ts,
        )


@dataclass(frozen=True)
class NormalizedCandidate:
    source_record: FeatureRecord
    normalized_record: FeatureRecord
    ttl_status: str
    data_quality_score: float
    metric_zscore: float | None
    metric_percentile: float | None
    market_rank: float | None
    sector_rank: float | None
    historical_rank: float | None
    winsorized_value: float
    confidence_score: float


@dataclass(frozen=True)
class VectorDiagnostics:
    required_feature_names: tuple[str, ...]
    available_feature_count: int
    required_feature_count: int
    fresh_feature_count: int
    expired_feature_count: int
    fresh_feature_ratio: float
    stale_policy: str


@dataclass(frozen=True)
class NormalizationFeatureVectorExecutionResult:
    module_job_result: ModuleJobResult
    normalized_feature_records: tuple[FeatureRecord, ...]
    feature_vectors: tuple[FeatureVector, ...]
    normalized_feature_record_refs: tuple[str, ...]
    feature_vector_refs: tuple[str, ...]
    diagnostics: Mapping[str, VectorDiagnostics] = field(default_factory=dict)

    @property
    def feature_vector(self) -> FeatureVector | None:
        return self.feature_vectors[0] if len(self.feature_vectors) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "normalized_feature_records": [record.to_dict() for record in self.normalized_feature_records],
            "feature_vectors": [vector.to_dict() for vector in self.feature_vectors],
            "normalized_feature_record_refs": list(self.normalized_feature_record_refs),
            "feature_vector_refs": list(self.feature_vector_refs),
            "diagnostics": {
                key: {
                    "required_feature_names": list(value.required_feature_names),
                    "available_feature_count": value.available_feature_count,
                    "required_feature_count": value.required_feature_count,
                    "fresh_feature_count": value.fresh_feature_count,
                    "expired_feature_count": value.expired_feature_count,
                    "fresh_feature_ratio": value.fresh_feature_ratio,
                    "stale_policy": value.stale_policy,
                }
                for key, value in self.diagnostics.items()
            },
        }
        if len(self.feature_vectors) == 1:
            payload["feature_vector"] = self.feature_vectors[0].to_dict()
        return payload


class NormalizationFeatureVectorService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: NormalizationFeatureVectorRepository | None = None,
    ) -> None:
        self.repository = repository or InMemoryNormalizationFeatureVectorRepository()

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> NormalizationFeatureVectorExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> NormalizationFeatureVectorExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> NormalizationFeatureVectorExecutionResult:
        started_at = to_utc_iso(utc_now())
        if job is None:
            result = ModuleJobResult(
                job_id="missing",
                module_name=self.module_name,
                status="failed",
                started_at=started_at,
                finished_at=to_utc_iso(utc_now()),
                errors=(f"{self.module_name} requires module_job",),
            )
            return NormalizationFeatureVectorExecutionResult(result, (), (), (), ())

        try:
            self.validate_module_job(job)
            normalization_input = NormalizationFeatureVectorInput.from_dict(payload, job)
            profile = NormalizationProfile.from_profile_id(normalization_input.normalization_profile_id)
            feature_records = self.repository.list_feature_records(
                feature_refs=normalization_input.feature_refs,
                instrument_ids=normalization_input.instrument_ids,
                horizons=normalization_input.horizons,
                from_ts=job.time_range.from_ts,
                as_of_ts=normalization_input.as_of_ts,
            )
            source_feature_records = tuple(record for record in feature_records if not _is_normalized_feature_record(record))
            source_refs = tuple(f"features.feature_record:{record.feature_id}" for record in source_feature_records)
            data_quality_records = self.repository.list_data_quality_records(source_refs)
            profiles = self.repository.list_instrument_profiles(job.universe_id, normalization_input.instrument_ids)

            normalized_records, vectors, diagnostics, warnings = self.build_outputs(
                normalization_input=normalization_input,
                job=job,
                profile=profile,
                feature_records=source_feature_records,
                data_quality_records=data_quality_records,
                instrument_profiles=profiles,
            )
            if feature_records and not source_feature_records:
                warnings = tuple(dict.fromkeys((*warnings, "only_normalized_feature_records_loaded")))

            normalized_refs = tuple(self.repository.save_feature_record(record) for record in normalized_records)
            vector_refs = tuple(self.repository.save_feature_vector(vector) for vector in vectors)
            output_refs = normalized_refs + vector_refs
            status = "success" if vectors and normalized_records and not warnings else "partial_success" if vectors else "skipped"

            return NormalizationFeatureVectorExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(normalized_records),
                ),
                normalized_feature_records=normalized_records,
                feature_vectors=vectors,
                normalized_feature_record_refs=normalized_refs,
                feature_vector_refs=vector_refs,
                diagnostics=diagnostics,
            )
        except (NormalizationFeatureVectorError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise NormalizationFeatureVectorError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise NormalizationFeatureVectorError("module_job.module_name must be Normalization & Feature Vector Module")
        if job.contour not in VALID_CONTOURS:
            raise NormalizationFeatureVectorError("module_job.contour is not valid for normalization")
        if not job.universe_id:
            raise NormalizationFeatureVectorError("module_job.universe_id is required")
        if not job.instrument_ids:
            raise NormalizationFeatureVectorError("module_job.instrument_ids is required")
        if not job.horizons:
            raise NormalizationFeatureVectorError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise NormalizationFeatureVectorError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise NormalizationFeatureVectorError("module_job.run_mode is required")

    def build_outputs(
        self,
        *,
        normalization_input: NormalizationFeatureVectorInput,
        job: ModuleJob,
        profile: NormalizationProfile,
        feature_records: tuple[FeatureRecord, ...],
        data_quality_records: tuple[DataQualityRecord, ...],
        instrument_profiles: tuple[InstrumentProfile, ...],
    ) -> tuple[tuple[FeatureRecord, ...], tuple[FeatureVector, ...], Mapping[str, VectorDiagnostics], tuple[str, ...]]:
        del job
        warnings: list[str] = []
        profile_by_id = {profile_item.instrument_id: profile_item for profile_item in instrument_profiles}
        data_quality_by_ref = _latest_data_quality_by_ref(data_quality_records)
        required_by_horizon = self.required_features_by_horizon(feature_records, profile)
        latest_by_key = _latest_records_by_instrument_horizon_metric(feature_records, normalization_input.as_of_ts)
        normalized_records: list[FeatureRecord] = []
        vectors: list[FeatureVector] = []
        diagnostics: dict[str, VectorDiagnostics] = {}

        if not feature_records:
            warnings.append("feature_records_missing")
        missing_profiles = tuple(item for item in normalization_input.instrument_ids if item not in profile_by_id)
        warnings.extend(f"instrument_profile_missing:{instrument_id}" for instrument_id in missing_profiles)

        for instrument_id in normalization_input.instrument_ids:
            instrument_profile = profile_by_id.get(instrument_id)
            for horizon in normalization_input.horizons:
                if instrument_profile is not None and horizon not in instrument_profile.allowed_horizons:
                    warnings.append(f"horizon_not_allowed_for_instrument:{instrument_id}:{horizon}")
                source_candidates = tuple(
                    record
                    for (record_instrument, record_horizon, _), record in latest_by_key.items()
                    if record_instrument == instrument_id and record_horizon == horizon
                )
                normalized_candidates: list[NormalizedCandidate] = []
                expired_feature_count = 0
                for source_record in sorted(source_candidates, key=lambda item: item.metric_name):
                    ttl_status = check_ttl_status(source_record, normalization_input.as_of_ts)
                    is_expired = ttl_status == "expired"
                    if is_expired:
                        expired_feature_count += 1
                    candidate = self.normalize_feature_record(
                        source_record=source_record,
                        all_records=feature_records,
                        latest_by_key=latest_by_key,
                        profile=profile,
                        instrument_profile=instrument_profile,
                        profiles_by_instrument=profile_by_id,
                        data_quality_by_ref=data_quality_by_ref,
                        as_of_ts=normalization_input.as_of_ts,
                    )
                    if candidate is None:
                        warnings.append(f"feature_not_normalizable:{source_record.feature_id}")
                        continue
                    normalized_records.append(candidate.normalized_record)
                    if is_expired and profile.stale_policy == "block_decision":
                        continue
                    normalized_candidates.append(candidate)

                required_features = required_by_horizon.get(horizon, ())
                vector = self.build_feature_vector(
                    instrument_id=instrument_id,
                    horizon=horizon,
                    as_of_ts=normalization_input.as_of_ts,
                    profile=profile,
                    candidates=tuple(normalized_candidates),
                    required_feature_names=required_features,
                )
                vectors.append(vector)
                fresh_count = sum(1 for candidate in normalized_candidates if candidate.ttl_status == "fresh")
                diagnostic_key = f"{instrument_id}:{horizon}"
                diagnostics[diagnostic_key] = VectorDiagnostics(
                    required_feature_names=required_features,
                    available_feature_count=len(vector.features),
                    required_feature_count=len(required_features),
                    fresh_feature_count=fresh_count,
                    expired_feature_count=expired_feature_count,
                    fresh_feature_ratio=fresh_feature_ratio(fresh_count, len(vector.features)),
                    stale_policy=profile.stale_policy,
                )
                if vector.coverage_ratio < 1.0:
                    warnings.append(f"coverage_below_required:{instrument_id}:{horizon}:{vector.coverage_ratio:.6f}")

        return tuple(normalized_records), tuple(vectors), diagnostics, tuple(dict.fromkeys(warnings))

    def normalize_feature_record(
        self,
        *,
        source_record: FeatureRecord,
        all_records: tuple[FeatureRecord, ...],
        latest_by_key: Mapping[tuple[str, str, str], FeatureRecord],
        profile: NormalizationProfile,
        instrument_profile: InstrumentProfile | None,
        profiles_by_instrument: Mapping[str, InstrumentProfile],
        data_quality_by_ref: Mapping[str, DataQualityRecord],
        as_of_ts: str,
    ) -> NormalizedCandidate | None:
        if source_record.raw_value is None:
            return None

        history = _raw_history(all_records, source_record)
        winsorized = winsorize(
            source_record.raw_value,
            history,
            profile.winsor_lower_percentile,
            profile.winsor_upper_percentile,
        )
        if winsorized is None:
            return None

        metric_zscore = zscore(source_record.raw_value, history)
        metric_percentile = percentile_rank(source_record.raw_value, history)
        historical_rank = metric_percentile
        market_rank = percentile_rank(
            source_record.raw_value,
            _same_timestamp_peer_values(
                all_records,
                source_record.horizon,
                source_record.metric_name,
                source_record.timestamp,
            ),
        )
        sector_rank = None
        if instrument_profile is not None and instrument_profile.sector:
            sector_rank = percentile_rank(
                source_record.raw_value,
                _same_timestamp_peer_values(
                    all_records,
                    source_record.horizon,
                    source_record.metric_name,
                    source_record.timestamp,
                    sector=instrument_profile.sector,
                    profiles_by_instrument=profiles_by_instrument,
                ),
            )
        normalized_value = self.metric_specific_normalized_value(source_record)
        if normalized_value is None:
            normalized_value = historical_rank
        if normalized_value is None and metric_zscore is not None:
            normalized_value = signed_to_unit(metric_zscore, profile.zscore_bound)
        if normalized_value is None:
            normalized_value = market_rank if market_rank is not None else source_record.normalized_value
        if normalized_value is None:
            return None

        ttl_status = check_ttl_status(source_record, as_of_ts)
        data_quality_score = _data_quality_score_for_feature(source_record, data_quality_by_ref)
        data_quality_flags = list(_data_quality_flags_for_feature(source_record, data_quality_by_ref))
        data_quality_score = min(data_quality_score, _quality_score_from_flags(data_quality_flags))
        if _extended_session_market_data_grace_applies(source_record, as_of_ts):
            data_quality_flags.append("arena_go_extended_session_market_data_grace")
        if "future_timestamp" in data_quality_flags:
            ttl_status = "invalid"
            data_quality_score = min(data_quality_score, 0.0)
        confidence = _clip01(source_record.confidence_score * data_quality_score)
        if ttl_status == "stale" and profile.stale_policy == "downweight":
            confidence = _clip01(confidence * 0.5)
        elif ttl_status == "stale" and profile.stale_policy == "block_decision":
            confidence = _clip01(confidence * 0.75)

        normalized_record = self.build_normalized_feature_record(
            source_record=source_record,
            normalized_value=normalized_value,
            winsorized_value=winsorized,
            metric_zscore=metric_zscore,
            metric_percentile=metric_percentile,
            market_rank=market_rank,
            sector_rank=sector_rank,
            historical_rank=historical_rank,
            ttl_status=ttl_status,
            confidence_score=confidence,
            data_quality_score=data_quality_score,
            data_quality_flags=tuple(dict.fromkeys(data_quality_flags)),
            profile=profile,
            as_of_ts=as_of_ts,
        )
        return NormalizedCandidate(
            source_record=source_record,
            normalized_record=normalized_record,
            ttl_status=ttl_status,
            data_quality_score=data_quality_score,
            metric_zscore=metric_zscore,
            metric_percentile=metric_percentile,
            market_rank=market_rank,
            sector_rank=sector_rank,
            historical_rank=historical_rank,
            winsorized_value=winsorized,
            confidence_score=confidence,
        )

    def metric_specific_normalized_value(self, source_record: FeatureRecord) -> float | None:
        metric_name = str(source_record.metric_name)
        if source_record.raw_value is None:
            return None
        if metric_name in NATURAL_UNIT_METRICS:
            return _clip01(source_record.raw_value)
        signed_bound = SIGNED_RATIO_BOUNDS.get(metric_name)
        if signed_bound is not None:
            return signed_to_unit(source_record.raw_value, signed_bound)
        return None

    def build_normalized_feature_record(
        self,
        *,
        source_record: FeatureRecord,
        normalized_value: float,
        winsorized_value: float,
        metric_zscore: float | None,
        metric_percentile: float | None,
        market_rank: float | None,
        sector_rank: float | None,
        historical_rank: float | None,
        ttl_status: str,
        confidence_score: float,
        data_quality_score: float,
        data_quality_flags: tuple[str, ...],
        profile: NormalizationProfile,
        as_of_ts: str,
    ) -> FeatureRecord:
        feature_id = stable_record_id(
            "feature_norm",
            {
                "source_feature_id": source_record.feature_id,
                "normalization_profile_id": profile.profile_id,
                "as_of_ts": as_of_ts,
                "build_version": profile.build_version,
            },
        )
        quality_flags = tuple(
            dict.fromkeys(
                (
                    *source_record.quality_flags,
                    *data_quality_flags,
                    f"ttl_status:{ttl_status}",
                    "normalized_feature_record",
                )
            )
        )
        return FeatureRecord(
            feature_id=feature_id,
            instrument_id=source_record.instrument_id,
            metric_name=source_record.metric_name,
            metric_group=source_record.metric_group,
            metric_type=source_record.metric_type,
            raw_value=source_record.raw_value,
            normalized_value=_clip01(normalized_value),
            unit=source_record.unit,
            horizon=source_record.horizon,
            contour="decision_contour",
            timestamp=as_of_ts,
            ttl_seconds=source_record.ttl_seconds if source_record.ttl_seconds is not None else PERMANENT_TTL_SECONDS,
            confidence_score=confidence_score,
            source_module=self.module_name,
            source_refs=(f"features.feature_record:{source_record.feature_id}", *source_record.source_refs),
            calculation_version=profile.build_version,
            quality_flags=quality_flags,
            payload={
                "normalization_profile_id": profile.profile_id,
                "normalized_feature_record": True,
                "build_version": profile.build_version,
                "original_feature_id": source_record.feature_id,
                "original_timestamp": source_record.timestamp,
                "raw_value_preserved": source_record.raw_value,
                "winsorized_value": winsorized_value,
                "metric_zscore": metric_zscore,
                "metric_percentile": metric_percentile,
                "market_rank": market_rank,
                "sector_rank": sector_rank,
                "historical_rank": historical_rank,
                "ttl_status": ttl_status,
                "stale_policy": profile.stale_policy,
                "data_quality_score": data_quality_score,
                "calculation_version": CALCULATION_VERSION,
            },
        )

    def build_feature_vector(
        self,
        *,
        instrument_id: str,
        horizon: str,
        as_of_ts: str,
        profile: NormalizationProfile,
        candidates: tuple[NormalizedCandidate, ...],
        required_feature_names: tuple[str, ...],
    ) -> FeatureVector:
        feature_items = {
            candidate.source_record.metric_name: {
                "normalized_value": candidate.normalized_record.normalized_value,
                "raw_value": candidate.source_record.raw_value,
                "confidence_score": candidate.confidence_score,
                "ttl_status": candidate.ttl_status,
                "source_feature_id": candidate.normalized_record.feature_id,
                "source_refs": list(candidate.normalized_record.source_refs),
                "quality_flags": list(candidate.normalized_record.quality_flags),
                "data_quality_score": candidate.data_quality_score,
                "calculation_version": candidate.normalized_record.calculation_version,
            }
            for candidate in sorted(candidates, key=lambda item: item.source_record.metric_name)
            if candidate.normalized_record.normalized_value is not None
        }
        vector_id = stable_record_id(
            "feature_vector",
            {
                "instrument_id": instrument_id,
                "horizon": horizon,
                "as_of_ts": as_of_ts,
                "normalization_profile_id": profile.profile_id,
                "build_version": profile.build_version,
            },
        )
        quality_scores = tuple(candidate.data_quality_score for candidate in candidates)
        data_quality_score = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        available_feature_count = len(feature_items)
        source_refs = tuple(
            dict.fromkeys(
                ref
                for candidate in candidates
                for ref in candidate.normalized_record.source_refs
            )
        )
        quality_flags = tuple(
            dict.fromkeys(
                flag
                for candidate in candidates
                for flag in candidate.normalized_record.quality_flags
            )
        )
        data_quality_score = min(_clip01(data_quality_score), _quality_score_from_flags(quality_flags))
        meta_ttl_status = "invalid" if "future_timestamp" in quality_flags else (
            "expired" if any(candidate.ttl_status == "expired" for candidate in candidates) else (
                "stale" if any(candidate.ttl_status == "stale" for candidate in candidates) else "fresh"
            )
        )
        feature_items["_meta"] = {
            "coverage_ratio": coverage_ratio(available_feature_count, len(required_feature_names)),
            "data_quality_score": _clip01(data_quality_score),
            "source_refs": list(source_refs),
            "ttl_status": meta_ttl_status,
            "quality_flags": list(quality_flags),
            "calculation_version": profile.build_version,
        }
        return FeatureVector(
            feature_vector_id=vector_id,
            instrument_id=instrument_id,
            horizon=horizon,
            as_of_ts=as_of_ts,
            features=feature_items,
            coverage_ratio=coverage_ratio(available_feature_count, len(required_feature_names)),
            data_quality_score=_clip01(data_quality_score),
            build_version=profile.build_version,
        )

    def required_features_by_horizon(
        self,
        records: tuple[FeatureRecord, ...],
        profile: NormalizationProfile,
    ) -> dict[str, tuple[str, ...]]:
        if profile.required_feature_names:
            return {
                horizon: tuple(sorted(profile.required_feature_names))
                for horizon in VALID_HORIZONS
            }
        result: dict[str, tuple[str, ...]] = {}
        for horizon in VALID_HORIZONS:
            metric_names = sorted({record.metric_name for record in records if record.horizon == horizon})
            result[horizon] = tuple(metric_names)
        return result

    def _module_job_result(
        self,
        *,
        job: ModuleJob,
        started_at: str,
        status: str,
        output_refs: tuple[str, ...],
        warnings: tuple[str, ...],
        errors: tuple[str, ...],
        metrics_written: int,
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
            metrics_written=metrics_written,
            events_written=0,
            data_quality_score=1.0 if not warnings and not errors else 0.8 if output_refs else 0.0,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> NormalizationFeatureVectorExecutionResult:
        return NormalizationFeatureVectorExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
            ),
            normalized_feature_records=(),
            feature_vectors=(),
            normalized_feature_record_refs=(),
            feature_vector_refs=(),
        )


def check_ttl_status(record: FeatureRecord, as_of_ts: str) -> str:
    ttl_seconds = record.ttl_seconds
    if ttl_seconds is None or ttl_seconds <= 0:
        return "fresh"
    age_seconds = (parse_utc_iso(as_of_ts) - parse_utc_iso(record.timestamp)).total_seconds()
    if age_seconds <= ttl_seconds:
        return "fresh"
    if _extended_session_market_data_grace_applies(record, as_of_ts, age_seconds=age_seconds):
        return "fresh"
    if age_seconds <= ttl_seconds * 2:
        return "stale"
    return "expired"


def _extended_session_market_data_grace_applies(
    record: FeatureRecord,
    as_of_ts: str,
    *,
    age_seconds: float | None = None,
) -> bool:
    if not _env_bool("ARENA_GO_SANDBOX", False):
        return False
    if not _env_bool("ARENA_GO_MARKET_EXTENDED_SESSION", True):
        return False
    if not _env_bool("ALLOW_ARENA_GO_EXTENDED_MARKET_DATA_GRACE", True):
        return False
    if record.horizon != "intraday":
        return False
    if record.metric_group not in {"price", "liquidity"}:
        return False
    if record.source_module not in {"Market Data Metrics Module", "Liquidity & Microstructure Module"}:
        return False
    ttl_seconds = record.ttl_seconds
    if ttl_seconds is None or ttl_seconds <= 0:
        return False
    age = age_seconds
    if age is None:
        age = (parse_utc_iso(as_of_ts) - parse_utc_iso(record.timestamp)).total_seconds()
    if age <= ttl_seconds:
        return False
    grace_seconds = _env_float(
        "ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS",
        DEFAULT_ARENA_GO_EXTENDED_MARKET_DATA_GRACE_SECONDS,
    )
    if age > max(ttl_seconds, grace_seconds):
        return False
    as_of = parse_utc_iso(as_of_ts)
    if record.timestamp and parse_utc_iso(record.timestamp) > as_of:
        return False
    zone = ZoneInfo(os.getenv("ARENA_GO_MARKET_TIMEZONE") or os.getenv("MOEX_MARKET_TIMEZONE") or DEFAULT_MARKET_TIMEZONE)
    local_time = as_of.astimezone(zone).time()
    moex_close = _env_time("MOEX_MARKET_CLOSE_TIME", DEFAULT_MOEX_CLOSE_TIME)
    arena_close = _env_time("ARENA_GO_MARKET_CLOSE_TIME", DEFAULT_ARENA_GO_CLOSE_TIME)
    return moex_close <= local_time < arena_close


def _latest_records_by_instrument_horizon_metric(
    records: tuple[FeatureRecord, ...],
    as_of_ts: str,
) -> dict[tuple[str, str, str], FeatureRecord]:
    as_of = parse_utc_iso(as_of_ts)
    latest: dict[tuple[str, str, str], FeatureRecord] = {}
    for record in records:
        if not record.timestamp or parse_utc_iso(record.timestamp) > as_of:
            continue
        key = (record.instrument_id, record.horizon, record.metric_name)
        current = latest.get(key)
        if current is None or (record.timestamp, record.feature_id) > (current.timestamp, current.feature_id):
            latest[key] = record
    return latest


def _is_normalized_feature_record(record: FeatureRecord) -> bool:
    if record.source_module == MODULE_NAME:
        return True
    if "normalized_feature_record" in record.quality_flags:
        return True
    return bool(record.payload.get("normalized_feature_record"))


def _raw_history(records: tuple[FeatureRecord, ...], source_record: FeatureRecord) -> tuple[float, ...]:
    values = [
        float(record.raw_value)
        for record in records
        if record.instrument_id == source_record.instrument_id
        and record.horizon == source_record.horizon
        and record.metric_name == source_record.metric_name
        and record.raw_value is not None
        and record.timestamp <= source_record.timestamp
    ]
    return tuple(values)


def _same_timestamp_peer_values(
    records: tuple[FeatureRecord, ...],
    horizon: str,
    metric_name: str,
    timestamp: str,
    sector: str | None = None,
    profiles_by_instrument: Mapping[str, InstrumentProfile] | None = None,
) -> tuple[float, ...]:
    values: list[float] = []
    for record in records:
        if record.horizon != horizon or record.metric_name != metric_name or record.raw_value is None:
            continue
        if record.timestamp != timestamp:
            continue
        if sector is not None:
            profile = (profiles_by_instrument or {}).get(record.instrument_id)
            if profile is None or profile.sector != sector:
                continue
        values.append(float(record.raw_value))
    return tuple(values)


def _latest_data_quality_by_ref(records: tuple[DataQualityRecord, ...]) -> dict[str, DataQualityRecord]:
    latest: dict[str, DataQualityRecord] = {}
    for record in records:
        keys = {record.object_ref, _ref_tail(record.object_ref)}
        for key in keys:
            current = latest.get(key)
            if current is None or (record.checked_at, record.data_quality_record_id) > (
                current.checked_at,
                current.data_quality_record_id,
            ):
                latest[key] = record
    return latest


def _data_quality_score_for_feature(
    feature_record: FeatureRecord,
    data_quality_by_ref: Mapping[str, DataQualityRecord],
) -> float:
    keys = (
        f"features.feature_record:{feature_record.feature_id}",
        feature_record.feature_id,
        *feature_record.source_refs,
        *(_ref_tail(ref) for ref in feature_record.source_refs),
    )
    scores: list[float] = []
    for key in keys:
        record = data_quality_by_ref.get(key)
        if record is not None and record.quality_score is not None:
            scores.append(_clip01(record.quality_score))
    record_score = min(scores) if scores else 1.0
    return min(record_score, _quality_score_from_flags(feature_record.quality_flags))


def _data_quality_flags_for_feature(
    feature_record: FeatureRecord,
    data_quality_by_ref: Mapping[str, DataQualityRecord],
) -> tuple[str, ...]:
    keys = (
        f"features.feature_record:{feature_record.feature_id}",
        feature_record.feature_id,
        *feature_record.source_refs,
        *(_ref_tail(ref) for ref in feature_record.source_refs),
    )
    flags: list[str] = []
    for key in keys:
        record = data_quality_by_ref.get(key)
        if record is not None:
            flags.extend(record.quality_flags)
    return tuple(dict.fromkeys(flags))


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _clip01(value: float | None) -> float:
    if value is None:
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _quality_score_from_flags(flags: tuple[str, ...] | list[str]) -> float:
    score = 1.0
    for raw_flag in flags:
        flag = str(raw_flag or "").strip()
        lowered = flag.lower()
        if not lowered:
            continue
        if lowered in {"normalized_feature_record", "ttl_status:fresh"}:
            continue
        if "future_timestamp" in lowered:
            score = min(score, 0.0)
        elif "expired" in lowered or "invalid" in lowered:
            score = min(score, 0.25)
        elif "stale" in lowered:
            score = min(score, 0.65)
        elif "missing_orderbook_using_candle_liquidity_proxy" in lowered:
            score = min(score, 0.72)
        elif "quote_proxy_orderbook" in lowered or "top_of_book_only" in lowered:
            score = min(score, 0.82)
        elif "low_trade_coverage" in lowered:
            score = min(score, 0.82)
        elif "low_context_coverage" in lowered:
            score = min(score, 0.80)
        elif "macro_points_missing" in lowered or "raw_macro_missing" in lowered:
            score = min(score, 0.70)
        elif "degraded_macro_context" in lowered or "missing_macro_series" in lowered:
            score = min(score, 0.78)
        elif "market_breadth_missing" in lowered or "sector_mapping_missing" in lowered:
            score = min(score, 0.85)
        elif "insufficient_history" in lowered or "insufficient_correlation_history" in lowered:
            score = min(score, 0.78)
        elif "missing_data" in lowered or "low_coverage" in lowered:
            score = min(score, 0.65)
        elif "source_missing_endpoint" in lowered or "source_unavailable" in lowered:
            score = min(score, 0.75)
    return _clip01(score)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_time(name: str, default: time) -> time:
    value = str(os.getenv(name) or "").strip()
    if not value:
        return default
    parts = value.split(":")
    try:
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
        if len(parts) == 3:
            return time(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return default
    return default
