from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from .metrics import (
    bucket_returns,
    clip,
    feature_decay,
    mean_optional,
    pearson_correlation,
    population_stability_index,
    rank_values,
    rolling_rank_ic,
    spearman_correlation,
    stability_score,
    turnover_impact_from_rank_paths,
    variance,
)
from .repository import (
    FeatureRecord,
    FeatureValidationResearchRepository,
    InMemoryFeatureValidationResearchRepository,
    MarketStateRecord,
    MetricWeightRuleDraft,
    OrderExecutionRecord,
    ResearchReportRecord,
    WeightsProfileDraft,
    stable_record_id,
)


MODULE_NAME = "Feature Validation & Research Module"
CALCULATION_VERSION = "feature_validation_research_v1"
VALID_CONTOURS = {"research_contour", "daily_contour"}
VALID_HORIZONS = {"intraday", "swing", "position"}
VALID_RETURN_TARGETS = {
    "forward_return_1d",
    "forward_return_5d",
    "forward_volatility",
    "max_drawdown",
}
VALID_RECOMMENDATIONS = {"keep", "reduce_weight", "disable", "research_more"}
VALID_RUN_MODES = {"analysis_only", "backtest", "replay"}
CONTRACT_REPORT_METRIC_FIELDS = (
    "feature_ic",
    "rank_ic",
    "feature_decay",
    "hit_rate_by_quantile",
    "stability_score",
)
RETURN_TARGET_DECAY_ORDER = (
    "forward_return_1d",
    "forward_return_5d",
    "forward_volatility",
    "max_drawdown",
)
INPUT_FIELDS = {
    "feature_set_ref",
    "return_targets",
    "horizons",
    "time_range",
    "regime_filters",
}
MIN_SAMPLES = 3


class FeatureValidationResearchError(ValueError):
    """Raised when module 16 would violate its documented contract."""


@dataclass(frozen=True)
class ValidationTimeRange:
    from_ts: str
    to_ts: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ValidationTimeRange":
        from_ts = str(payload.get("from_ts") or "")
        to_ts = str(payload.get("to_ts") or "")
        parse_utc_iso(from_ts)
        parse_utc_iso(to_ts)
        if parse_utc_iso(from_ts) > parse_utc_iso(to_ts):
            raise FeatureValidationResearchError("validation_input.time_range.from_ts must be <= to_ts")
        return cls(from_ts=from_ts, to_ts=to_ts)

    def to_dict(self) -> dict[str, str]:
        return {"from_ts": self.from_ts, "to_ts": self.to_ts}


@dataclass(frozen=True)
class FeatureValidationResearchInput:
    feature_set_ref: str
    return_targets: tuple[str, ...]
    horizons: tuple[str, ...]
    time_range: ValidationTimeRange
    regime_filters: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "FeatureValidationResearchInput":
        extra_top_level = sorted(set(payload) - {"validation_input"})
        if extra_top_level:
            raise FeatureValidationResearchError(f"payload has undocumented fields: {extra_top_level}")
        input_payload = payload.get("validation_input")
        if not isinstance(input_payload, Mapping):
            raise FeatureValidationResearchError("payload must contain validation_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise FeatureValidationResearchError(f"validation_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise FeatureValidationResearchError(f"validation_input has undocumented fields: {extra_fields}")

        feature_set_ref = str(input_payload.get("feature_set_ref") or "")
        if not feature_set_ref:
            raise FeatureValidationResearchError("validation_input.feature_set_ref is required")

        return_targets = tuple(str(item) for item in (input_payload.get("return_targets") or ()))
        if not return_targets:
            raise FeatureValidationResearchError("validation_input.return_targets is required")
        invalid_targets = sorted(set(return_targets) - VALID_RETURN_TARGETS)
        if invalid_targets:
            raise FeatureValidationResearchError(f"invalid return_targets: {invalid_targets}")

        horizons = tuple(str(item) for item in (input_payload.get("horizons") or ()))
        if not horizons:
            raise FeatureValidationResearchError("validation_input.horizons is required")
        invalid_horizons = sorted(set(horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise FeatureValidationResearchError(f"invalid horizons: {invalid_horizons}")
        if tuple(job.horizons) and horizons != tuple(job.horizons):
            raise FeatureValidationResearchError("validation_input.horizons must match module_job.horizons")

        time_range_payload = input_payload.get("time_range")
        if not isinstance(time_range_payload, Mapping):
            raise FeatureValidationResearchError("validation_input.time_range is required")
        time_range = ValidationTimeRange.from_mapping(time_range_payload)
        if parse_utc_iso(time_range.from_ts) < parse_utc_iso(job.time_range.from_ts):
            raise FeatureValidationResearchError("validation_input.time_range.from_ts must be within module_job.time_range")
        if parse_utc_iso(time_range.to_ts) > parse_utc_iso(job.time_range.to_ts):
            raise FeatureValidationResearchError("validation_input.time_range.to_ts must be within module_job.time_range")

        return cls(
            feature_set_ref=feature_set_ref,
            return_targets=return_targets,
            horizons=horizons,
            time_range=time_range,
            regime_filters=tuple(str(item) for item in (input_payload.get("regime_filters") or ())),
        )


@dataclass(frozen=True)
class ValidationSample:
    feature_record: FeatureRecord
    target_record: FeatureRecord
    feature_value: float
    target_value: float
    market_regime: str

    @property
    def instrument_id(self) -> str:
        return self.feature_record.instrument_id

    @property
    def timestamp(self) -> str:
        return self.feature_record.timestamp


@dataclass(frozen=True)
class FeatureQualityRecord:
    feature_quality_record_id: str
    metric_name: str
    metric_group: str
    horizon: str
    return_target: str
    sample_count: int
    feature_ic: float
    rank_ic: float
    feature_decay: float
    hit_rate_by_quantile: float
    return_by_feature_bucket: Mapping[str, float]
    stability_score: float
    turnover_impact: float
    feature_drift_score: float
    regime_sensitivity_score: float
    recommendation: str
    validation_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_quality_record_id": self.feature_quality_record_id,
            "metric_name": self.metric_name,
            "metric_group": self.metric_group,
            "horizon": self.horizon,
            "return_target": self.return_target,
            "sample_count": self.sample_count,
            "feature_ic": self.feature_ic,
            "rank_ic": self.rank_ic,
            "feature_decay": self.feature_decay,
            "hit_rate_by_quantile": self.hit_rate_by_quantile,
            "return_by_feature_bucket": dict(self.return_by_feature_bucket),
            "stability_score": self.stability_score,
            "turnover_impact": self.turnover_impact,
            "feature_drift_score": self.feature_drift_score,
            "regime_sensitivity_score": self.regime_sensitivity_score,
            "recommendation": self.recommendation,
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True)
class ValidationReport:
    validation_report_id: str
    feature_set_ref: str
    time_range: ValidationTimeRange
    metrics: Mapping[str, float]
    recommendation: str
    created_at: str
    validation_status: str
    calculation_version: str
    invalid_reason_codes: tuple[str, ...] = ()

    def to_contract(self) -> dict[str, Any]:
        return {
            "validation_report_id": self.validation_report_id,
            "feature_set_ref": self.feature_set_ref,
            "time_range": self.time_range.to_dict(),
            "metrics": {
                metric_name: float(self.metrics.get(metric_name, 0.0))
                for metric_name in CONTRACT_REPORT_METRIC_FIELDS
            },
            "recommendation": self.recommendation,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.to_contract()
        payload.update(
            {
                "validation_status": self.validation_status,
                "calculation_version": self.calculation_version,
                "invalid_reason_codes": list(self.invalid_reason_codes),
            }
        )
        return payload


@dataclass(frozen=True)
class FeatureValidationResearchExecutionResult:
    module_job_result: ModuleJobResult
    validation_report: ValidationReport | None
    feature_quality_records: tuple[FeatureQualityRecord, ...]
    weights_profile_drafts: tuple[WeightsProfileDraft, ...]
    metric_weight_rule_drafts: tuple[MetricWeightRuleDraft, ...]
    validation_report_ref: str | None = None
    weights_profile_refs: tuple[str, ...] = ()
    metric_weight_rule_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_job_result": self.module_job_result.to_dict(),
            "validation_report": self.validation_report.to_contract() if self.validation_report else None,
            "validation_status": self.validation_report.validation_status if self.validation_report else "invalid",
            "feature_quality_records": [record.to_dict() for record in self.feature_quality_records],
            "weights_profile_drafts": [_weights_profile_to_dict(profile) for profile in self.weights_profile_drafts],
            "metric_weight_rule_drafts": [_metric_weight_rule_to_dict(rule) for rule in self.metric_weight_rule_drafts],
            "validation_report_ref": self.validation_report_ref,
            "weights_profile_refs": list(self.weights_profile_refs),
            "metric_weight_rule_refs": list(self.metric_weight_rule_refs),
        }


class FeatureValidationResearchService:
    module_name = MODULE_NAME

    def __init__(self, repository: FeatureValidationResearchRepository | None = None) -> None:
        self.repository = repository or InMemoryFeatureValidationResearchRepository()

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> FeatureValidationResearchExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> FeatureValidationResearchExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> FeatureValidationResearchExecutionResult:
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
            return FeatureValidationResearchExecutionResult(result, None, (), (), ())

        try:
            self.validate_module_job(job)
            validation_input = FeatureValidationResearchInput.from_dict(payload, job)
            feature_records = self.repository.list_feature_records(
                feature_set_ref=validation_input.feature_set_ref,
                return_targets=validation_input.return_targets,
                horizons=validation_input.horizons,
                from_ts=validation_input.time_range.from_ts,
                to_ts=validation_input.time_range.to_ts,
            )
            market_states = self.repository.list_market_state_records(
                universe_id=job.universe_id,
                from_ts=validation_input.time_range.from_ts,
                to_ts=validation_input.time_range.to_ts,
            )
            orders = self.repository.list_order_execution_records(
                from_ts=validation_input.time_range.from_ts,
                to_ts=validation_input.time_range.to_ts,
            )
            report, quality_records, warnings = self.build_validation_report(
                job=job,
                validation_input=validation_input,
                feature_records=feature_records,
                market_states=market_states,
                orders=orders,
            )
            validation_report_record = self._research_report_record(job, report, quality_records, validation_input)
            validation_report_ref = self.repository.save_research_report(validation_report_record)

            weights_profiles: tuple[WeightsProfileDraft, ...] = ()
            weight_rules: tuple[MetricWeightRuleDraft, ...] = ()
            weight_profile_refs: tuple[str, ...] = ()
            weight_rule_refs: tuple[str, ...] = ()
            if report.validation_status == "valid":
                weights_profiles, weight_rules = self.propose_weight_profile_drafts(
                    report=report,
                    report_ref=validation_report_ref,
                    quality_records=quality_records,
                    horizons=validation_input.horizons,
                    config_ref=str(job.config_ref or ""),
                )
                weight_profile_refs = tuple(self.repository.save_weights_profile_draft(profile) for profile in weights_profiles)
                weight_rule_refs = tuple(self.repository.save_metric_weight_rule_draft(rule) for rule in weight_rules)
            elif report.invalid_reason_codes:
                warnings = tuple(dict.fromkeys((*warnings, *report.invalid_reason_codes)))

            output_refs = tuple(ref for ref in (validation_report_ref, *weight_profile_refs, *weight_rule_refs) if ref)
            status = "success" if report.validation_status == "valid" and quality_records else "partial_success"
            return FeatureValidationResearchExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(quality_records) + len(weight_rules),
                ),
                validation_report=report,
                feature_quality_records=quality_records,
                weights_profile_drafts=weights_profiles,
                metric_weight_rule_drafts=weight_rules,
                validation_report_ref=validation_report_ref,
                weights_profile_refs=weight_profile_refs,
                metric_weight_rule_refs=weight_rule_refs,
            )
        except (FeatureValidationResearchError, ContractValidationError, ValueError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise FeatureValidationResearchError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise FeatureValidationResearchError("module_job.module_name must be Feature Validation & Research Module")
        if job.contour not in VALID_CONTOURS:
            raise FeatureValidationResearchError("module_job.contour must be research_contour or daily_contour")
        if not job.time_range:
            raise FeatureValidationResearchError("module_job.time_range is required")
        if not job.horizons:
            raise FeatureValidationResearchError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise FeatureValidationResearchError(f"invalid module_job.horizons: {invalid_horizons}")
        if job.run_mode not in VALID_RUN_MODES:
            raise FeatureValidationResearchError("research module run_mode must be analysis_only, backtest, or replay")
        if not job.config_ref:
            raise FeatureValidationResearchError("module_job.config_ref is required")

    def build_validation_report(
        self,
        *,
        job: ModuleJob,
        validation_input: FeatureValidationResearchInput,
        feature_records: tuple[FeatureRecord, ...],
        market_states: tuple[MarketStateRecord, ...],
        orders: tuple[OrderExecutionRecord, ...],
    ) -> tuple[ValidationReport, tuple[FeatureQualityRecord, ...], tuple[str, ...]]:
        warnings: list[str] = []
        invalid_reason_codes: list[str] = []
        predictors = tuple(record for record in feature_records if record.metric_name not in validation_input.return_targets)
        targets = tuple(record for record in feature_records if record.metric_name in validation_input.return_targets)
        if not predictors:
            warnings.append("historical_feature_records_missing")
        if not targets:
            warnings.append("historical_return_targets_missing")
        if self.detect_lookahead_bias(predictors):
            invalid_reason_codes.append("lookahead_bias_detected")

        market_regime_by_ts = _market_regime_lookup(market_states)
        order_turnover_multiplier = 1.0 + _order_turnover_score(orders)
        quality_records: list[FeatureQualityRecord] = []
        all_ic_by_feature: dict[tuple[str, str], dict[str, float | None]] = {}

        for horizon in validation_input.horizons:
            horizon_predictors = tuple(record for record in predictors if record.horizon == horizon and record.value is not None)
            horizon_targets = tuple(record for record in targets if record.horizon == horizon and record.value is not None)
            if not horizon_predictors or not horizon_targets:
                warnings.append(f"feature_quality_missing:{horizon}")
                continue
            grouped_predictors = _group_by_metric(horizon_predictors)
            for metric_name, records in grouped_predictors.items():
                metric_group = records[0].metric_group if records else "research"
                target_ic_values: dict[str, float | None] = {}
                records_by_target: dict[str, FeatureQualityRecord] = {}
                for return_target in validation_input.return_targets:
                    samples = self.align_features_and_targets(
                        feature_records=records,
                        target_records=tuple(record for record in horizon_targets if record.metric_name == return_target),
                        market_regime_by_ts=market_regime_by_ts,
                        regime_filters=validation_input.regime_filters,
                    )
                    if any(parse_utc_iso(sample.target_record.timestamp) < parse_utc_iso(sample.feature_record.timestamp) for sample in samples):
                        invalid_reason_codes.append("data_leakage_detected")
                    quality_record = self.compute_feature_quality_record(
                        feature_set_ref=validation_input.feature_set_ref,
                        metric_name=metric_name,
                        metric_group=metric_group,
                        horizon=horizon,
                        return_target=return_target,
                        samples=samples,
                        target_ic_by_target=target_ic_values,
                        order_turnover_multiplier=order_turnover_multiplier,
                        config_ref=str(job.config_ref or ""),
                    )
                    target_ic_values[return_target] = quality_record.feature_ic
                    records_by_target[return_target] = quality_record
                all_ic_by_feature[(horizon, metric_name)] = target_ic_values
                decay_value = feature_decay(_ordered_ic_by_target(target_ic_values))
                for return_target in validation_input.return_targets:
                    quality_record = records_by_target[return_target]
                    quality_records.append(_with_decay(quality_record, decay_value))

        if invalid_reason_codes:
            validation_status = "invalid"
            recommendation = "disable"
        else:
            validation_status = "valid" if quality_records else "insufficient_data"
            recommendation = self.aggregate_recommendation(tuple(quality_records), validation_status)
            if validation_status != "valid":
                warnings.append("insufficient_validation_samples")

        report = ValidationReport(
            validation_report_id=stable_record_id(
                "validation_report",
                {
                    "feature_set_ref": validation_input.feature_set_ref,
                    "time_range": validation_input.time_range.to_dict(),
                    "return_targets": validation_input.return_targets,
                    "horizons": validation_input.horizons,
                    "config_ref": job.config_ref,
                    "calculation_version": CALCULATION_VERSION,
                },
            ),
            feature_set_ref=validation_input.feature_set_ref,
            time_range=validation_input.time_range,
            metrics=self.aggregate_metrics(tuple(quality_records)),
            recommendation=recommendation,
            created_at=to_utc_iso(utc_now()),
            validation_status=validation_status,
            calculation_version=f"{CALCULATION_VERSION}:{job.config_ref}",
            invalid_reason_codes=tuple(dict.fromkeys(invalid_reason_codes)),
        )
        return report, tuple(quality_records), tuple(dict.fromkeys(warnings))

    def align_features_and_targets(
        self,
        *,
        feature_records: tuple[FeatureRecord, ...],
        target_records: tuple[FeatureRecord, ...],
        market_regime_by_ts: Mapping[str, str],
        regime_filters: tuple[str, ...],
    ) -> tuple[ValidationSample, ...]:
        targets_by_instrument: dict[str, list[FeatureRecord]] = {}
        for target_record in target_records:
            targets_by_instrument.setdefault(target_record.instrument_id, []).append(target_record)
        for records in targets_by_instrument.values():
            records.sort(key=lambda item: (item.timestamp, item.feature_id))

        samples: list[ValidationSample] = []
        allowed_regimes = set(regime_filters)
        for feature_record in sorted(feature_records, key=lambda item: (item.timestamp, item.instrument_id, item.feature_id)):
            if feature_record.value is None:
                continue
            target_record = _nearest_forward_target(feature_record, targets_by_instrument.get(feature_record.instrument_id, ()))
            if target_record is None or target_record.value is None:
                continue
            regime = _regime_for_timestamp(feature_record.timestamp, market_regime_by_ts)
            if allowed_regimes and regime not in allowed_regimes:
                continue
            samples.append(
                ValidationSample(
                    feature_record=feature_record,
                    target_record=target_record,
                    feature_value=float(feature_record.value),
                    target_value=float(target_record.value),
                    market_regime=regime,
                )
            )
        return tuple(samples)

    def compute_feature_quality_record(
        self,
        *,
        feature_set_ref: str,
        metric_name: str,
        metric_group: str,
        horizon: str,
        return_target: str,
        samples: tuple[ValidationSample, ...],
        target_ic_by_target: Mapping[str, float | None],
        order_turnover_multiplier: float,
        config_ref: str,
    ) -> FeatureQualityRecord:
        feature_values = tuple(sample.feature_value for sample in samples)
        forward_returns = tuple(sample.target_value for sample in samples)
        feature_ic = pearson_correlation(feature_values, forward_returns)
        rank_ic = spearman_correlation(feature_values, forward_returns)
        hit_rate_by_bucket, return_by_bucket = bucket_returns(feature_values, forward_returns)
        rolling_values = rolling_rank_ic(feature_values, forward_returns)
        stability = stability_score(rolling_values)
        drift = _drift_score(feature_values)
        regime_sensitivity = _regime_sensitivity(samples)
        turnover = clip(_rank_turnover(samples) * order_turnover_multiplier)
        sample_count = len(samples)
        validation_status = "valid" if sample_count >= MIN_SAMPLES else "insufficient_data"
        recommendation = _recommend_feature(
            validation_status=validation_status,
            feature_ic=feature_ic,
            rank_ic=rank_ic,
            stability=stability,
            turnover=turnover,
            regime_sensitivity=regime_sensitivity,
        )
        provisional_decay = feature_decay(_ordered_ic_by_target(target_ic_by_target)) if target_ic_by_target else 0.0
        return FeatureQualityRecord(
            feature_quality_record_id=stable_record_id(
                "feature_quality",
                {
                    "feature_set_ref": feature_set_ref,
                    "metric_name": metric_name,
                    "horizon": horizon,
                    "return_target": return_target,
                    "config_ref": config_ref,
                    "calculation_version": CALCULATION_VERSION,
                },
            ),
            metric_name=metric_name,
            metric_group=metric_group,
            horizon=horizon,
            return_target=return_target,
            sample_count=sample_count,
            feature_ic=float(feature_ic or 0.0),
            rank_ic=float(rank_ic or 0.0),
            feature_decay=provisional_decay,
            hit_rate_by_quantile=mean(hit_rate_by_bucket.values()) if hit_rate_by_bucket else 0.0,
            return_by_feature_bucket=return_by_bucket,
            stability_score=stability,
            turnover_impact=turnover,
            feature_drift_score=drift,
            regime_sensitivity_score=regime_sensitivity,
            recommendation=recommendation,
            validation_status=validation_status,
        )

    def aggregate_metrics(self, quality_records: tuple[FeatureQualityRecord, ...]) -> dict[str, float]:
        return {
            "feature_ic": mean_optional(record.feature_ic for record in quality_records),
            "rank_ic": mean_optional(record.rank_ic for record in quality_records),
            "feature_decay": mean_optional(record.feature_decay for record in quality_records),
            "hit_rate_by_quantile": mean_optional(record.hit_rate_by_quantile for record in quality_records),
            "stability_score": mean_optional(record.stability_score for record in quality_records),
            "turnover_impact": mean_optional(record.turnover_impact for record in quality_records),
            "feature_drift_score": mean_optional(record.feature_drift_score for record in quality_records),
            "regime_sensitivity_score": mean_optional(record.regime_sensitivity_score for record in quality_records),
        }

    def aggregate_recommendation(
        self,
        quality_records: tuple[FeatureQualityRecord, ...],
        validation_status: str,
    ) -> str:
        if validation_status == "invalid":
            return "disable"
        valid_records = tuple(record for record in quality_records if record.validation_status == "valid")
        if not valid_records:
            return "research_more"
        recommendations = [record.recommendation for record in valid_records]
        if recommendations.count("keep") >= max(1, len(recommendations) // 2):
            return "keep"
        if "reduce_weight" in recommendations:
            return "reduce_weight"
        if "disable" in recommendations:
            return "disable"
        return "research_more"

    def detect_lookahead_bias(self, predictors: tuple[FeatureRecord, ...]) -> bool:
        for record in predictors:
            flags = {flag.lower() for flag in record.quality_flags}
            if {"lookahead_bias", "data_leakage", "uses_future_data"} & flags:
                return True
            if any(bool(record.payload.get(key)) for key in ("lookahead_bias", "data_leakage", "uses_future_data")):
                return True
            available_at = record.payload.get("available_at") or record.payload.get("known_at")
            if available_at and parse_utc_iso(str(available_at)) > parse_utc_iso(record.timestamp):
                return True
        return False

    def propose_weight_profile_drafts(
        self,
        *,
        report: ValidationReport,
        report_ref: str,
        quality_records: tuple[FeatureQualityRecord, ...],
        horizons: tuple[str, ...],
        config_ref: str,
    ) -> tuple[tuple[WeightsProfileDraft, ...], tuple[MetricWeightRuleDraft, ...]]:
        profiles: list[WeightsProfileDraft] = []
        rules: list[MetricWeightRuleDraft] = []
        for horizon in horizons:
            eligible = tuple(
                record
                for record in quality_records
                if record.horizon == horizon and record.validation_status == "valid" and record.recommendation in {"keep", "reduce_weight"}
            )
            if not eligible:
                continue
            profile_id = stable_record_id(
                "weights_profile_draft",
                {
                    "validation_report_id": report.validation_report_id,
                    "horizon": horizon,
                    "config_ref": config_ref,
                    "calculation_version": CALCULATION_VERSION,
                },
            )
            profile = WeightsProfileDraft(
                weights_profile_id=profile_id,
                profile_name=f"research_draft_{report.feature_set_ref}",
                version=f"{CALCULATION_VERSION}:{config_ref}:{report.validation_report_id}",
                horizon=horizon,
                run_mode_allowed=("analysis_only", "paper_trading"),
                validation_report_ref=report_ref,
            )
            profiles.append(profile)
            raw_scores = {
                record.metric_name: abs(record.rank_ic) * record.stability_score * (1.0 - record.turnover_impact)
                for record in eligible
            }
            total_score = sum(raw_scores.values()) or 1.0
            for record in eligible:
                direction = "positive" if record.feature_ic >= 0 else "negative"
                weight = raw_scores[record.metric_name] / total_score
                if record.recommendation == "reduce_weight":
                    weight *= 0.5
                rules.append(
                    MetricWeightRuleDraft(
                        metric_weight_rule_id=stable_record_id(
                            "metric_weight_rule_draft",
                            {
                                "weights_profile_id": profile_id,
                                "metric_name": record.metric_name,
                                "horizon": horizon,
                                "return_target": record.return_target,
                            },
                        ),
                        weights_profile_id=profile_id,
                        metric_name=record.metric_name,
                        metric_group=record.metric_group,
                        horizon=horizon,
                        instrument_scope="all",
                        instrument_ids=(),
                        sector=None,
                        weight=clip(weight, 0.0, 1.0),
                        direction=direction,
                        transform="rank",
                        min_confidence_score=0.5,
                        stale_policy="block_decision",
                        calculation_version=f"{CALCULATION_VERSION}:{config_ref}",
                    )
                )
        return tuple(profiles), tuple(rules)

    def _research_report_record(
        self,
        job: ModuleJob,
        report: ValidationReport,
        quality_records: tuple[FeatureQualityRecord, ...],
        validation_input: FeatureValidationResearchInput,
    ) -> ResearchReportRecord:
        horizon = validation_input.horizons[0] if len(validation_input.horizons) == 1 else None
        payload = {
            "validation_report": report.to_dict(),
            "validation_status": report.validation_status,
            "invalid_reason_codes": list(report.invalid_reason_codes),
            "feature_quality_records": [record.to_dict() for record in quality_records],
            "feature_set_ref": validation_input.feature_set_ref,
            "return_targets": list(validation_input.return_targets),
            "horizons": list(validation_input.horizons),
            "regime_filters": list(validation_input.regime_filters),
            "config_ref": job.config_ref,
            "module_job_ref": f"audit.module_job:{job.job_id}",
            "calculation_version": report.calculation_version,
            "active_weights_changed": False,
        }
        return ResearchReportRecord(
            validation_report_id=report.validation_report_id,
            report_type="validation_report",
            universe_id=job.universe_id,
            horizon=horizon,
            as_of_ts=report.created_at,
            payload=payload,
        )

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
    ) -> FeatureValidationResearchExecutionResult:
        return FeatureValidationResearchExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
            ),
            validation_report=None,
            feature_quality_records=(),
            weights_profile_drafts=(),
            metric_weight_rule_drafts=(),
        )


def _with_decay(record: FeatureQualityRecord, decay_value: float) -> FeatureQualityRecord:
    return FeatureQualityRecord(
        feature_quality_record_id=record.feature_quality_record_id,
        metric_name=record.metric_name,
        metric_group=record.metric_group,
        horizon=record.horizon,
        return_target=record.return_target,
        sample_count=record.sample_count,
        feature_ic=record.feature_ic,
        rank_ic=record.rank_ic,
        feature_decay=decay_value,
        hit_rate_by_quantile=record.hit_rate_by_quantile,
        return_by_feature_bucket=record.return_by_feature_bucket,
        stability_score=record.stability_score,
        turnover_impact=record.turnover_impact,
        feature_drift_score=record.feature_drift_score,
        regime_sensitivity_score=record.regime_sensitivity_score,
        recommendation=record.recommendation,
        validation_status=record.validation_status,
    )


def _group_by_metric(records: tuple[FeatureRecord, ...]) -> dict[str, tuple[FeatureRecord, ...]]:
    grouped: dict[str, list[FeatureRecord]] = {}
    for record in records:
        grouped.setdefault(record.metric_name, []).append(record)
    return {key: tuple(values) for key, values in grouped.items()}


def _ordered_ic_by_target(ic_by_target: Mapping[str, float | None]) -> dict[str, float | None]:
    ordered = {
        target_name: ic_by_target[target_name]
        for target_name in RETURN_TARGET_DECAY_ORDER
        if target_name in ic_by_target
    }
    for target_name, value in ic_by_target.items():
        if target_name not in ordered:
            ordered[target_name] = value
    return ordered


def _nearest_forward_target(feature_record: FeatureRecord, targets: tuple[FeatureRecord, ...] | list[FeatureRecord]) -> FeatureRecord | None:
    feature_ts = parse_utc_iso(feature_record.timestamp)
    candidates = [
        target
        for target in targets
        if target.value is not None and parse_utc_iso(target.timestamp) >= feature_ts
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item.timestamp, item.feature_id))[0]


def _market_regime_lookup(records: tuple[MarketStateRecord, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for record in records:
        if not record.as_of_ts:
            continue
        result[record.as_of_ts] = record.market_regime or "unknown"
    return result


def _regime_for_timestamp(timestamp: str, market_regime_by_ts: Mapping[str, str]) -> str:
    candidates = [
        (regime_ts, regime)
        for regime_ts, regime in market_regime_by_ts.items()
        if parse_utc_iso(regime_ts) <= parse_utc_iso(timestamp)
    ]
    if not candidates:
        return "unknown"
    return sorted(candidates, key=lambda item: item[0])[-1][1]


def _order_turnover_score(records: tuple[OrderExecutionRecord, ...]) -> float:
    quantities = [
        abs(float(record.filled_quantity if record.filled_quantity is not None else record.quantity or 0.0))
        for record in records
        if record.status in (None, "filled", "partially_filled", "submitted")
    ]
    if not quantities:
        return 0.0
    total = sum(quantities)
    if total <= 0:
        return 0.0
    return clip(mean(quantities) / total)


def _drift_score(feature_values: tuple[float, ...]) -> float:
    if len(feature_values) < 4:
        return 0.0
    midpoint = len(feature_values) // 2
    psi = population_stability_index(feature_values[:midpoint], feature_values[midpoint:])
    return clip(psi / (1.0 + psi))


def _regime_sensitivity(samples: tuple[ValidationSample, ...]) -> float:
    grouped: dict[str, list[ValidationSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.market_regime, []).append(sample)
    values = [
        pearson_correlation(
            tuple(sample.feature_value for sample in regime_samples),
            tuple(sample.target_value for sample in regime_samples),
        )
        for regime_samples in grouped.values()
        if len(regime_samples) >= MIN_SAMPLES
    ]
    return variance(values)


def _rank_turnover(samples: tuple[ValidationSample, ...]) -> float:
    by_timestamp: dict[str, list[ValidationSample]] = {}
    for sample in samples:
        by_timestamp.setdefault(sample.timestamp, []).append(sample)
    rank_paths: dict[str, list[float]] = {}
    for timestamp in sorted(by_timestamp):
        timestamp_samples = by_timestamp[timestamp]
        ranks = rank_values(tuple(sample.feature_value for sample in timestamp_samples))
        for sample, rank in zip(timestamp_samples, ranks):
            rank_paths.setdefault(sample.instrument_id, []).append(rank)
    return turnover_impact_from_rank_paths(rank_paths)


def _recommend_feature(
    *,
    validation_status: str,
    feature_ic: float | None,
    rank_ic: float | None,
    stability: float,
    turnover: float,
    regime_sensitivity: float,
) -> str:
    if validation_status != "valid":
        return "research_more"
    effective_ic = abs(rank_ic if rank_ic is not None else feature_ic or 0.0)
    if effective_ic >= 0.05 and stability >= 0.4 and turnover <= 0.6 and regime_sensitivity <= 0.25:
        return "keep"
    if effective_ic >= 0.02 and stability >= 0.2:
        return "reduce_weight"
    if effective_ic < 0.005 and stability < 0.1:
        return "disable"
    return "research_more"


def _weights_profile_to_dict(profile: WeightsProfileDraft) -> dict[str, Any]:
    return {
        "weights_profile_id": profile.weights_profile_id,
        "profile_name": profile.profile_name,
        "version": profile.version,
        "status": profile.status,
        "horizon": profile.horizon,
        "run_mode_allowed": list(profile.run_mode_allowed),
        "approved_by": profile.approved_by,
        "validation_report_ref": profile.validation_report_ref,
    }


def _metric_weight_rule_to_dict(rule: MetricWeightRuleDraft) -> dict[str, Any]:
    return {
        "metric_weight_rule_id": rule.metric_weight_rule_id,
        "weights_profile_id": rule.weights_profile_id,
        "metric_name": rule.metric_name,
        "metric_group": rule.metric_group,
        "horizon": rule.horizon,
        "instrument_scope": rule.instrument_scope,
        "instrument_ids": list(rule.instrument_ids),
        "sector": rule.sector,
        "weight": rule.weight,
        "direction": rule.direction,
        "transform": rule.transform,
        "min_confidence_score": rule.min_confidence_score,
        "stale_policy": rule.stale_policy,
        "calculation_version": rule.calculation_version,
    }
