from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from agent_app.contracts.unified_objects import (
    CachePolicy,
    ExternalRequest,
    ModuleJob,
    ModuleJobResult,
    RetryPolicy,
)
from agent_app.contracts.unified_objects.module_job import (
    ContractValidationError,
    parse_utc_iso,
    to_utc_iso,
    utc_now,
)

from .metrics import (
    growth_yoy,
    margin_change,
    relative_to_history,
    relative_to_sector,
    safe_divide,
    weighted_average,
    z_to_unit,
    zscore,
)
from .repository import (
    FeatureRecord,
    FinancialStatement,
    FundamentalSnapshot,
    FundamentalValuationRepository,
    InMemoryFundamentalValuationRepository,
    MarketDataRecord,
    PeerGroupRecord,
    stable_record_id,
    stable_uuid_id,
)


MODULE_NAME = "Fundamental & Valuation Module"
CALCULATION_VERSION = "fundamental_valuation_v1"

VALID_CONTOURS = {"daily_contour", "event_contour"}
VALID_HORIZONS = {"swing", "position"}
VALID_REPORTING_STANDARDS = {"ifrs", "ras", "mixed", "unknown"}
INPUT_FIELDS = {
    "instrument_ids",
    "financial_statement_refs",
    "market_cap_ref",
    "peer_group_ref",
    "reporting_standard",
    "period",
}

FUNDAMENTAL_TTL_SECONDS = 90 * 24 * 60 * 60
MARKET_DEPENDENT_TTL_SECONDS = 24 * 60 * 60
MARKET_CAP_MAX_AGE_SECONDS = 24 * 60 * 60

DEFAULT_QUALITY_WEIGHTS = {
    "roe_z": 1.0,
    "roic_z": 1.0,
    "negative_net_debt_ebitda_z": 1.0,
    "interest_coverage_z": 1.0,
    "margin_change_z": 1.0,
}
DEFAULT_VALUATION_WEIGHTS = {
    "negative_pe_relative_to_history": 1.0,
    "negative_ev_ebitda_relative_to_sector": 1.0,
    "earnings_yield_z": 1.0,
    "fcf_yield_z": 1.0,
}

METRIC_NAMES = (
    "pe_relative_to_history",
    "ev_ebitda_relative_to_sector",
    "pb_relative",
    "ps_relative",
    "earnings_yield",
    "fcf_yield",
    "roe",
    "roic",
    "net_debt_ebitda",
    "interest_coverage",
    "revenue_growth_yoy",
    "ebitda_growth_yoy",
    "net_income_growth_yoy",
    "margin_change",
    "fundamental_quality_score",
    "valuation_attractiveness_score",
)


class FundamentalValuationError(ValueError):
    """Raised when fundamental valuation processing violates the module contract."""


@dataclass(frozen=True)
class FundamentalValuationInput:
    instrument_ids: tuple[str, ...]
    financial_statement_refs: tuple[str, ...]
    market_cap_ref: str
    peer_group_ref: str
    reporting_standard: str
    period: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], job: ModuleJob) -> "FundamentalValuationInput":
        input_payload = payload.get("fundamental_input")
        if not isinstance(input_payload, Mapping):
            raise FundamentalValuationError("payload must contain fundamental_input")

        missing_fields = sorted(INPUT_FIELDS - set(input_payload))
        if missing_fields:
            raise FundamentalValuationError(f"fundamental_input missing required fields: {missing_fields}")
        extra_fields = sorted(set(input_payload) - INPUT_FIELDS)
        if extra_fields:
            raise FundamentalValuationError(f"fundamental_input has undocumented fields: {extra_fields}")

        instrument_ids = tuple(str(item) for item in (input_payload.get("instrument_ids") or ()))
        if not instrument_ids:
            raise FundamentalValuationError("fundamental_input.instrument_ids is required")
        if tuple(job.instrument_ids) and instrument_ids != tuple(job.instrument_ids):
            raise FundamentalValuationError("fundamental_input.instrument_ids must match module_job.instrument_ids")

        reporting_standard = str(input_payload.get("reporting_standard") or "").lower()
        if reporting_standard not in VALID_REPORTING_STANDARDS:
            raise FundamentalValuationError("fundamental_input.reporting_standard must be ifrs, ras, mixed or unknown")

        period = str(input_payload.get("period") or "")
        if not period:
            raise FundamentalValuationError("fundamental_input.period is required")

        return cls(
            instrument_ids=instrument_ids,
            financial_statement_refs=tuple(str(item) for item in (input_payload.get("financial_statement_refs") or ())),
            market_cap_ref=str(input_payload.get("market_cap_ref") or ""),
            peer_group_ref=str(input_payload.get("peer_group_ref") or ""),
            reporting_standard=reporting_standard,
            period=period,
        )


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_type: str
    raw_value: float
    normalized_value: float | None
    unit: str
    ttl_seconds: int
    source_refs: tuple[str, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()
    confidence_score: float = 1.0


@dataclass(frozen=True)
class FundamentalValuationExecutionResult:
    module_job_result: ModuleJobResult
    feature_records: tuple[FeatureRecord, ...]
    fundamental_snapshots: tuple[FundamentalSnapshot, ...]
    feature_record_refs: tuple[str, ...]
    fundamental_snapshot_refs: tuple[str, ...]

    @property
    def fundamental_snapshot(self) -> FundamentalSnapshot | None:
        if len(self.fundamental_snapshots) == 1:
            return self.fundamental_snapshots[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "module_job_result": self.module_job_result.to_dict(),
            "feature_records": [record.to_dict() for record in self.feature_records],
            "fundamental_snapshots": [record.to_dict() for record in self.fundamental_snapshots],
            "feature_record_refs": list(self.feature_record_refs),
            "fundamental_snapshot_refs": list(self.fundamental_snapshot_refs),
        }
        if len(self.fundamental_snapshots) == 1:
            payload["fundamental_snapshot"] = self.fundamental_snapshots[0].to_dict()
        return payload


class FundamentalValuationService:
    module_name = MODULE_NAME

    def __init__(
        self,
        repository: FundamentalValuationRepository | None = None,
        gateway: Any | None = None,
        quality_weights: Mapping[str, float] | None = None,
        valuation_weights: Mapping[str, float] | None = None,
    ) -> None:
        self.repository = repository or InMemoryFundamentalValuationRepository()
        self.gateway = gateway
        self.quality_weights = dict(quality_weights or DEFAULT_QUALITY_WEIGHTS)
        self.valuation_weights = dict(valuation_weights or DEFAULT_VALUATION_WEIGHTS)

    def run(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> FundamentalValuationExecutionResult:
        return self.execute(payload, job)

    def process(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob,
    ) -> FundamentalValuationExecutionResult:
        return self.execute(payload, job)

    def execute(
        self,
        payload: Mapping[str, Any],
        job: ModuleJob | None,
    ) -> FundamentalValuationExecutionResult:
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
            return FundamentalValuationExecutionResult(result, (), (), (), ())

        try:
            self.validate_module_job(job)
            fundamental_input = FundamentalValuationInput.from_dict(payload, job)
            statements = self.repository.list_financial_statements(
                fundamental_input.financial_statement_refs,
                job.universe_id,
                fundamental_input.instrument_ids,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            market_data = self.repository.list_market_data(
                fundamental_input.market_cap_ref,
                job.universe_id,
                fundamental_input.instrument_ids,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )
            peer_groups = self.repository.list_peer_groups(
                fundamental_input.peer_group_ref,
                job.universe_id,
                fundamental_input.instrument_ids,
                job.time_range.from_ts,
                job.time_range.to_ts,
            )

            warnings: list[str] = []
            external_requests = self.create_external_requests_for_missing_raw_data(
                fundamental_input=fundamental_input,
                job=job,
                statements=statements,
                market_data=market_data,
                peer_groups=peer_groups,
            )
            for request in external_requests:
                if self.gateway is not None:
                    self.gateway.process(request)
            warnings.extend(f"external_request_created:{request.request_id}" for request in external_requests)

            feature_records, snapshots, compute_warnings = self.compute_outputs(
                fundamental_input=fundamental_input,
                job=job,
                statements=statements,
                market_data=market_data,
                peer_groups=peer_groups,
            )
            warnings.extend(compute_warnings)

            feature_refs = tuple(self.write_feature_record(record) for record in feature_records)
            snapshot_refs = tuple(self.write_fundamental_snapshot(record) for record in snapshots)
            output_refs = feature_refs + snapshot_refs
            if not output_refs:
                warnings.append("fundamental_data_missing")

            status = "success" if feature_records and snapshots and not warnings else "partial_success" if output_refs else "skipped"
            return FundamentalValuationExecutionResult(
                module_job_result=self._module_job_result(
                    job=job,
                    started_at=started_at,
                    status=status,
                    output_refs=output_refs,
                    warnings=tuple(dict.fromkeys(warnings)),
                    errors=(),
                    metrics_written=len(feature_records),
                ),
                feature_records=feature_records,
                fundamental_snapshots=snapshots,
                feature_record_refs=feature_refs,
                fundamental_snapshot_refs=snapshot_refs,
            )
        except (FundamentalValuationError, ContractValidationError) as error:
            return self._failed_result(job, started_at, error)

    def validate_module_job(self, job: ModuleJob | None) -> None:
        if job is None:
            raise FundamentalValuationError(f"{self.module_name} requires module_job")
        if job.module_name != self.module_name:
            raise FundamentalValuationError("module_job.module_name must be Fundamental & Valuation Module")
        if job.contour not in VALID_CONTOURS:
            raise FundamentalValuationError("module_job.contour must be daily_contour or event_contour")
        if not job.instrument_ids:
            raise FundamentalValuationError("module_job.instrument_ids is required")
        if not job.horizons:
            raise FundamentalValuationError("module_job.horizons is required")
        invalid_horizons = sorted(set(job.horizons) - VALID_HORIZONS)
        if invalid_horizons:
            raise FundamentalValuationError(f"invalid module_job.horizons: {invalid_horizons}")
        if not job.run_mode:
            raise FundamentalValuationError("module_job.run_mode is required")

    def compute_outputs(
        self,
        *,
        fundamental_input: FundamentalValuationInput,
        job: ModuleJob,
        statements: tuple[FinancialStatement, ...],
        market_data: tuple[MarketDataRecord, ...],
        peer_groups: tuple[PeerGroupRecord, ...],
    ) -> tuple[tuple[FeatureRecord, ...], tuple[FundamentalSnapshot, ...], tuple[str, ...]]:
        feature_records: list[FeatureRecord] = []
        snapshots: list[FundamentalSnapshot] = []
        warnings: list[str] = []

        for instrument_id in fundamental_input.instrument_ids:
            selected_statements, statement_warnings = self.select_statements(
                statements=statements,
                instrument_id=instrument_id,
                period=fundamental_input.period,
                reporting_standard=fundamental_input.reporting_standard,
            )
            warnings.extend(statement_warnings)
            if not selected_statements:
                warnings.append(f"financial_statement_missing:{instrument_id}")
                continue

            merged_statement = _merge_statements(selected_statements, fundamental_input.reporting_standard)
            market_record = _latest_market_record(market_data, instrument_id)
            peer_record = _latest_peer_group(peer_groups, instrument_id)
            metric_values, metric_context, metric_warnings = self.compute_metric_values(
                statement=merged_statement,
                market_record=market_record,
                peer_record=peer_record,
                job=job,
                fundamental_input=fundamental_input,
            )
            warnings.extend(metric_warnings)

            timestamp = _as_of_timestamp(job, merged_statement, market_record)
            quality_flags = tuple(dict.fromkeys((*statement_warnings, *metric_warnings)))
            if len(metric_values) < len(METRIC_NAMES):
                quality_flags = tuple(dict.fromkeys((*quality_flags, "low_fundamental_coverage")))

            instrument_feature_ids: list[str] = []
            for metric_value in metric_values:
                for horizon in job.horizons:
                    feature_record = self.build_feature_record(
                        instrument_id=instrument_id,
                        metric_value=metric_value,
                        horizon=horizon,
                        contour=job.contour,
                        timestamp=timestamp,
                        period=fundamental_input.period,
                        reporting_standard=fundamental_input.reporting_standard,
                        quality_flags=tuple(dict.fromkeys((*quality_flags, *metric_value.quality_flags))),
                    )
                    feature_records.append(feature_record)
                    instrument_feature_ids.append(feature_record.feature_id)

            source_refs = tuple(
                dict.fromkeys(
                    (
                        *merged_statement.source_refs,
                        *(ref for metric_value in metric_values for ref in metric_value.source_refs),
                    )
                )
            )
            if source_refs:
                snapshots.append(
                    self.build_fundamental_snapshot(
                        job=job,
                        instrument_id=instrument_id,
                        period=fundamental_input.period,
                        reporting_standard=fundamental_input.reporting_standard,
                        source_refs=source_refs,
                        as_of_ts=timestamp,
                        feature_ids=tuple(instrument_feature_ids),
                        metric_context=metric_context,
                        quality_flags=quality_flags,
                    )
                )
            else:
                warnings.append(f"source_refs_missing:{instrument_id}")

        return tuple(feature_records), tuple(snapshots), tuple(dict.fromkeys(warnings))

    def select_statements(
        self,
        *,
        statements: tuple[FinancialStatement, ...],
        instrument_id: str,
        period: str,
        reporting_standard: str,
    ) -> tuple[tuple[FinancialStatement, ...], tuple[str, ...]]:
        warnings: list[str] = []
        candidates = [statement for statement in statements if statement.instrument_id == instrument_id]
        period_matches = [statement for statement in candidates if statement.period == period]
        if not period_matches:
            if candidates:
                warnings.append(f"financial_period_mismatch:{instrument_id}")
            return (), tuple(warnings)

        accepted: list[FinancialStatement] = []
        standards = {statement.reporting_standard for statement in period_matches if statement.reporting_standard}
        if reporting_standard in {"ifrs", "ras"}:
            for statement in period_matches:
                if statement.reporting_standard == reporting_standard:
                    accepted.append(statement)
                else:
                    warnings.append(f"reporting_standard_mismatch:{instrument_id}:{statement.reporting_standard}")
        elif reporting_standard == "unknown":
            accepted = period_matches
        else:
            accepted = period_matches
            if "unknown" in standards:
                warnings.append(f"accounting_standard_missing:{instrument_id}")

        accepted_standards = {statement.reporting_standard for statement in accepted if statement.reporting_standard != "unknown"}
        if len(accepted_standards) > 1 and reporting_standard != "mixed":
            raise FundamentalValuationError(f"mixed IFRS/RAS statements without accounting_standard: {instrument_id}")
        if not any(statement.source_refs for statement in accepted):
            warnings.append(f"source_refs_missing:{instrument_id}")
        return tuple(accepted), tuple(dict.fromkeys(warnings))

    def compute_metric_values(
        self,
        *,
        statement: FinancialStatement,
        market_record: MarketDataRecord | None,
        peer_record: PeerGroupRecord | None,
        job: ModuleJob,
        fundamental_input: FundamentalValuationInput,
    ) -> tuple[tuple[MetricValue, ...], Mapping[str, Any], tuple[str, ...]]:
        del fundamental_input
        fields = dict(statement.fields)
        warnings: list[str] = []
        metrics: list[MetricValue] = []
        source_refs = tuple(dict.fromkeys(statement.source_refs))
        if not source_refs:
            return (), {"metric_names": (), "missing_metric_names": list(METRIC_NAMES)}, (
                f"source_refs_missing:{statement.instrument_id}",
            )

        market_refs: tuple[str, ...] = ()
        market_cap = None
        enterprise_value = None
        pe_current = None
        pb_current = None
        ps_current = None
        ev_ebitda_current = None
        stale_market_cap = False
        if market_record is None:
            warnings.append(f"market_cap_missing:{statement.instrument_id}")
        else:
            for key, value in market_record.source_payload.items():
                fields.setdefault(key, value)
            market_refs = tuple(dict.fromkeys(market_record.source_refs))
            market_cap = market_record.market_cap
            enterprise_value = market_record.enterprise_value
            pe_current = market_record.pe_current
            pb_current = market_record.pb_current
            ps_current = market_record.ps_current
            ev_ebitda_current = market_record.ev_ebitda_current
            if _market_cap_stale(market_record, job):
                stale_market_cap = True
                warnings.append(f"stale_market_cap:{statement.instrument_id}")

        debt = _field_float(fields, "debt", "total_debt")
        cash = _field_float(fields, "cash_and_equivalents", "cash", "cash_equivalents")
        revenue_ttm = _field_float(fields, "revenue_ttm", "ttm_revenue")
        ebitda_ttm = _field_float(fields, "ebitda_ttm", "ttm_ebitda")
        net_income_ttm = _field_float(fields, "net_income_ttm", "ttm_net_income")
        free_cash_flow_ttm = _field_float(fields, "free_cash_flow_ttm", "fcf_ttm")
        average_equity = _field_float(fields, "average_equity", "avg_equity", "equity_avg", "equity")
        nopat = _field_float(fields, "nopat")
        invested_capital = _field_float(fields, "invested_capital")
        ebit = _field_float(fields, "ebit")
        interest_expense = _field_float(fields, "interest_expense")

        if enterprise_value is None and market_cap is not None and debt is not None and cash is not None:
            enterprise_value = market_cap + debt - cash
        if pe_current is None:
            pe_current = safe_divide(market_cap, net_income_ttm)
        if pb_current is None:
            pb_current = safe_divide(market_cap, average_equity)
        if ps_current is None:
            ps_current = safe_divide(market_cap, revenue_ttm)
        if ev_ebitda_current is None:
            ev_ebitda_current = safe_divide(enterprise_value, ebitda_ttm)

        pe_relative = relative_to_history(pe_current, _history(fields, "pe_history"))
        pb_relative = relative_to_history(pb_current, _history(fields, "pb_history"))
        ps_relative = relative_to_history(ps_current, _history(fields, "ps_history"))
        peer_ev_values = _peer_values(peer_record, "ev_ebitda")
        ev_ebitda_relative = relative_to_sector(ev_ebitda_current, peer_ev_values)

        market_source_refs = tuple(dict.fromkeys((*source_refs, *market_refs)))
        peer_source_refs = tuple(dict.fromkeys((*source_refs, *(peer_record.source_refs if peer_record else ()))))

        self._append_metric(
            metrics,
            "pe_relative_to_history",
            "derived_metric",
            pe_relative,
            z_to_unit(pe_relative),
            "zscore",
            MARKET_DEPENDENT_TTL_SECONDS,
            market_source_refs,
            {"formula": "(PE_current - median(PE_history)) / std(PE_history)", "pe_current": pe_current},
            stale_market_cap=stale_market_cap,
        )
        self._append_metric(
            metrics,
            "ev_ebitda_relative_to_sector",
            "derived_metric",
            ev_ebitda_relative,
            z_to_unit(ev_ebitda_relative),
            "multiple_difference",
            MARKET_DEPENDENT_TTL_SECONDS,
            peer_source_refs,
            {
                "formula": "EV_EBITDA_current - median(EV_EBITDA_sector)",
                "ev_ebitda_current": ev_ebitda_current,
                "peer_value_count": len(peer_ev_values),
            },
            stale_market_cap=stale_market_cap,
        )
        self._append_metric(
            metrics,
            "pb_relative",
            "derived_metric",
            pb_relative,
            z_to_unit(pb_relative),
            "zscore",
            MARKET_DEPENDENT_TTL_SECONDS,
            market_source_refs,
            {"formula": "(PB_current - median(PB_history)) / std(PB_history)", "pb_current": pb_current},
            stale_market_cap=stale_market_cap,
        )
        self._append_metric(
            metrics,
            "ps_relative",
            "derived_metric",
            ps_relative,
            z_to_unit(ps_relative),
            "zscore",
            MARKET_DEPENDENT_TTL_SECONDS,
            market_source_refs,
            {"formula": "(PS_current - median(PS_history)) / std(PS_history)", "ps_current": ps_current},
            stale_market_cap=stale_market_cap,
        )

        earnings_yield = safe_divide(net_income_ttm, market_cap)
        fcf_yield = safe_divide(free_cash_flow_ttm, market_cap)
        roe = safe_divide(net_income_ttm, average_equity)
        roic = safe_divide(nopat, invested_capital)
        net_debt_ebitda = safe_divide(None if debt is None or cash is None else debt - cash, ebitda_ttm)
        interest_coverage = safe_divide(ebit, interest_expense)
        revenue_growth = growth_yoy(
            _field_float(fields, "revenue_period", "revenue_current_period"),
            _field_float(fields, "revenue_same_period_prev_year", "revenue_prev_year"),
        )
        ebitda_growth = growth_yoy(
            _field_float(fields, "ebitda_period", "ebitda_current_period"),
            _field_float(fields, "ebitda_same_period_prev_year", "ebitda_prev_year"),
        )
        net_income_growth = growth_yoy(
            _field_float(fields, "net_income_period", "net_income_current_period"),
            _field_float(fields, "net_income_same_period_prev_year", "net_income_prev_year"),
        )
        current_margin = _field_float(fields, "current_margin", "margin_current", "ebitda_margin_current")
        previous_margin = _field_float(fields, "margin_same_period_prev_year", "margin_prev_year", "ebitda_margin_prev_year")
        if current_margin is None:
            current_margin = safe_divide(
                _field_float(fields, "ebitda_period", "ebitda_current_period"),
                _field_float(fields, "revenue_period", "revenue_current_period"),
            )
        if previous_margin is None:
            previous_margin = safe_divide(
                _field_float(fields, "ebitda_same_period_prev_year", "ebitda_prev_year"),
                _field_float(fields, "revenue_same_period_prev_year", "revenue_prev_year"),
            )
        margin_delta = margin_change(current_margin, previous_margin)

        for metric_name, value, unit, ttl, refs, formula, stale_flag in (
            ("earnings_yield", earnings_yield, "ratio", MARKET_DEPENDENT_TTL_SECONDS, market_source_refs, "net_income_ttm / market_cap", stale_market_cap),
            ("fcf_yield", fcf_yield, "ratio", MARKET_DEPENDENT_TTL_SECONDS, market_source_refs, "free_cash_flow_ttm / market_cap", stale_market_cap),
            ("roe", roe, "ratio", FUNDAMENTAL_TTL_SECONDS, source_refs, "net_income_ttm / average_equity", False),
            ("roic", roic, "ratio", FUNDAMENTAL_TTL_SECONDS, source_refs, "NOPAT / invested_capital", False),
            ("net_debt_ebitda", net_debt_ebitda, "multiple", FUNDAMENTAL_TTL_SECONDS, source_refs, "(debt - cash_and_equivalents) / EBITDA_ttm", False),
            ("interest_coverage", interest_coverage, "multiple", FUNDAMENTAL_TTL_SECONDS, source_refs, "EBIT / interest_expense", False),
            ("revenue_growth_yoy", revenue_growth, "ratio", FUNDAMENTAL_TTL_SECONDS, source_refs, "revenue_period / revenue_same_period_prev_year - 1", False),
            ("ebitda_growth_yoy", ebitda_growth, "ratio", FUNDAMENTAL_TTL_SECONDS, source_refs, "EBITDA_period / EBITDA_same_period_prev_year - 1", False),
            ("net_income_growth_yoy", net_income_growth, "ratio", FUNDAMENTAL_TTL_SECONDS, source_refs, "net_income_period / net_income_same_period_prev_year - 1", False),
            ("margin_change", margin_delta, "ratio_delta", FUNDAMENTAL_TTL_SECONDS, source_refs, "current_margin - margin_same_period_prev_year", False),
        ):
            self._append_metric(
                metrics,
                metric_name,
                "derived_metric",
                value,
                None,
                unit,
                ttl,
                refs,
                {"formula": formula},
                stale_market_cap=stale_flag,
            )

        component_z = {
            "roe_z": _metric_z(fields, peer_record, "roe", roe),
            "roic_z": _metric_z(fields, peer_record, "roic", roic),
            "negative_net_debt_ebitda_z": None if (value := _metric_z(fields, peer_record, "net_debt_ebitda", net_debt_ebitda)) is None else -value,
            "interest_coverage_z": _metric_z(fields, peer_record, "interest_coverage", interest_coverage),
            "margin_change_z": _metric_z(fields, peer_record, "margin_change", margin_delta),
        }
        quality_score = weighted_average(component_z, self.quality_weights)
        self._append_metric(
            metrics,
            "fundamental_quality_score",
            "composite_score",
            quality_score,
            z_to_unit(quality_score),
            "score",
            FUNDAMENTAL_TTL_SECONDS,
            source_refs,
            {
                "formula": "WAvg([roe_z, roic_z, -net_debt_ebitda_z, interest_coverage_z, margin_change_z], active_weights)",
                "component_values": {key: value for key, value in component_z.items() if value is not None},
                "weight_source": "service_config_default_or_injected",
            },
        )

        valuation_component_z = {
            "negative_pe_relative_to_history": None if pe_relative is None else -pe_relative,
            "negative_ev_ebitda_relative_to_sector": None if ev_ebitda_relative is None else -ev_ebitda_relative,
            "earnings_yield_z": _metric_z(fields, peer_record, "earnings_yield", earnings_yield),
            "fcf_yield_z": _metric_z(fields, peer_record, "fcf_yield", fcf_yield),
        }
        valuation_score = weighted_average(valuation_component_z, self.valuation_weights)
        self._append_metric(
            metrics,
            "valuation_attractiveness_score",
            "composite_score",
            valuation_score,
            z_to_unit(valuation_score),
            "score",
            MARKET_DEPENDENT_TTL_SECONDS,
            tuple(dict.fromkeys((*market_source_refs, *(peer_record.source_refs if peer_record else ())))),
            {
                "formula": "WAvg([-pe_relative_to_history, -ev_ebitda_relative_to_sector, earnings_yield_z, fcf_yield_z], active_weights)",
                "component_values": {key: value for key, value in valuation_component_z.items() if value is not None},
                "weight_source": "service_config_default_or_injected",
            },
            stale_market_cap=stale_market_cap,
        )

        present_metrics = {metric.metric_name for metric in metrics}
        for metric_name in METRIC_NAMES:
            if metric_name not in present_metrics:
                warnings.append(f"metric_missing:{statement.instrument_id}:{metric_name}")
        if "ev_ebitda_relative_to_sector" not in present_metrics and peer_record is None:
            warnings.append(f"peer_group_missing:{statement.instrument_id}")

        context = {
            "metric_names": sorted(present_metrics),
            "missing_metric_names": [metric for metric in METRIC_NAMES if metric not in present_metrics],
            "market_cap": market_cap,
            "enterprise_value": enterprise_value,
            "stale_market_cap": stale_market_cap,
            "source_statement_ids": [statement.statement_id],
            "calculation_version": CALCULATION_VERSION,
        }
        return tuple(metrics), context, tuple(dict.fromkeys(warnings))

    def build_feature_record(
        self,
        *,
        instrument_id: str,
        metric_value: MetricValue,
        horizon: str,
        contour: str,
        timestamp: str,
        period: str,
        reporting_standard: str,
        quality_flags: tuple[str, ...],
    ) -> FeatureRecord:
        if not metric_value.source_refs:
            raise FundamentalValuationError(f"source_refs required for feature: {metric_value.metric_name}")
        feature_payload = {
            "instrument_id": instrument_id,
            "metric_name": metric_value.metric_name,
            "horizon": horizon,
            "timestamp": timestamp,
            "period": period,
            "reporting_standard": reporting_standard,
            "calculation_version": CALCULATION_VERSION,
        }
        flags = tuple(dict.fromkeys(quality_flags))
        return FeatureRecord(
            feature_id=stable_record_id("feature", feature_payload),
            instrument_id=instrument_id,
            metric_name=metric_value.metric_name,
            metric_group="fundamental",
            metric_type=metric_value.metric_type,
            raw_value=float(metric_value.raw_value),
            normalized_value=metric_value.normalized_value,
            unit=metric_value.unit,
            horizon=horizon,
            contour=contour,
            timestamp=timestamp,
            ttl_seconds=metric_value.ttl_seconds,
            confidence_score=_confidence_score(flags, metric_value.confidence_score),
            source_module=self.module_name,
            source_refs=metric_value.source_refs,
            calculation_version=CALCULATION_VERSION,
            quality_flags=flags,
            payload={
                **dict(metric_value.payload),
                "period": period,
                "reporting_standard": reporting_standard,
                "formula_version": CALCULATION_VERSION,
            },
        )

    def build_fundamental_snapshot(
        self,
        *,
        job: ModuleJob,
        instrument_id: str,
        period: str,
        reporting_standard: str,
        source_refs: tuple[str, ...],
        as_of_ts: str,
        feature_ids: tuple[str, ...],
        metric_context: Mapping[str, Any],
        quality_flags: tuple[str, ...],
    ) -> FundamentalSnapshot:
        if not source_refs:
            raise FundamentalValuationError("source_refs required for fundamental_snapshot")
        snapshot_payload = {
            "instrument_id": instrument_id,
            "period": period,
            "reporting_standard": reporting_standard,
            "calculation_version": CALCULATION_VERSION,
        }
        feature_set_id = stable_record_id("feature_set", snapshot_payload)
        coverage_ratio = len(metric_context.get("metric_names") or ()) / len(METRIC_NAMES)
        return FundamentalSnapshot(
            fundamental_snapshot_id=stable_uuid_id(snapshot_payload),
            instrument_id=instrument_id,
            period=period,
            reporting_standard=reporting_standard,
            source_refs=source_refs,
            features_ref=f"features.feature_record:{feature_set_id}",
            confidence_score=_snapshot_confidence(coverage_ratio, quality_flags),
            calculation_version=CALCULATION_VERSION,
            as_of_ts=as_of_ts,
            quality_flags=quality_flags,
            payload={
                "job_id": job.job_id,
                "horizons": list(job.horizons),
                "feature_ids": list(feature_ids),
                "coverage_ratio": coverage_ratio,
                "metric_context": dict(metric_context),
            },
        )

    def create_external_requests_for_missing_raw_data(
        self,
        *,
        fundamental_input: FundamentalValuationInput,
        job: ModuleJob,
        statements: tuple[FinancialStatement, ...],
        market_data: tuple[MarketDataRecord, ...],
        peer_groups: tuple[PeerGroupRecord, ...],
    ) -> tuple[ExternalRequest, ...]:
        requests: list[ExternalRequest] = []
        if not statements:
            requests.append(self._external_request(job, fundamental_input, "issuer_disclosure", "text_fetch"))
        if not market_data or any(_market_cap_stale(record, job) for record in market_data):
            requests.append(self._external_request(job, fundamental_input, "moex_iss", "market_data"))
        if not peer_groups and fundamental_input.peer_group_ref:
            requests.append(self._external_request(job, fundamental_input, "moex_iss", "market_data", suffix="peer_group"))
        return tuple(requests)

    def write_feature_record(self, record: FeatureRecord) -> str:
        return self.repository.save_feature_record(record)

    def write_fundamental_snapshot(self, record: FundamentalSnapshot) -> str:
        return self.repository.save_fundamental_snapshot(record)

    def _append_metric(
        self,
        values: list[MetricValue],
        metric_name: str,
        metric_type: str,
        raw_value: float | None,
        normalized_value: float | None,
        unit: str,
        ttl_seconds: int,
        source_refs: tuple[str, ...],
        payload: Mapping[str, Any],
        *,
        stale_market_cap: bool = False,
    ) -> None:
        if raw_value is None:
            return
        if not source_refs:
            return
        quality_flags = ("stale_market_cap",) if stale_market_cap else ()
        values.append(
            MetricValue(
                metric_name=metric_name,
                metric_type=metric_type,
                raw_value=float(raw_value),
                normalized_value=None if normalized_value is None else float(normalized_value),
                unit=unit,
                ttl_seconds=ttl_seconds,
                source_refs=source_refs,
                payload={
                    **dict(payload),
                    "calculation_version": CALCULATION_VERSION,
                },
                quality_flags=quality_flags,
                confidence_score=0.75 if stale_market_cap else 1.0,
            )
        )

    def _external_request(
        self,
        job: ModuleJob,
        fundamental_input: FundamentalValuationInput,
        provider: str,
        request_type: str,
        *,
        suffix: str | None = None,
    ) -> ExternalRequest:
        request_suffix = suffix or request_type
        payload = {
            "financial_statement_refs": list(fundamental_input.financial_statement_refs),
            "market_cap_ref": fundamental_input.market_cap_ref,
            "peer_group_ref": fundamental_input.peer_group_ref,
            "reporting_standard": fundamental_input.reporting_standard,
            "period": fundamental_input.period,
            "time_range": job.time_range.to_dict(),
            "gateway_only": True,
        }
        idempotency_key = f"{job.idempotency_key}:{request_suffix}"
        return ExternalRequest(
            request_id=stable_record_id("request", {"idempotency_key": idempotency_key}),
            caller_module=self.module_name,
            provider=provider,
            request_type=request_type,
            universe_id=job.universe_id,
            instrument_ids=fundamental_input.instrument_ids,
            payload=payload,
            cache_policy=CachePolicy(use_cache=True, max_age_seconds=MARKET_DEPENDENT_TTL_SECONDS, write_cache=True),
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2, backoff_ms=250),
            idempotency_key=idempotency_key,
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
            data_quality_score=1.0 if not warnings and not errors else 0.75 if output_refs else 0.0,
        )

    def _failed_result(
        self,
        job: ModuleJob,
        started_at: str,
        error: Exception,
    ) -> FundamentalValuationExecutionResult:
        return FundamentalValuationExecutionResult(
            module_job_result=self._module_job_result(
                job=job,
                started_at=started_at,
                status="failed",
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
            ),
            feature_records=(),
            fundamental_snapshots=(),
            feature_record_refs=(),
            fundamental_snapshot_refs=(),
        )


def _merge_statements(statements: tuple[FinancialStatement, ...], requested_standard: str) -> FinancialStatement:
    if not statements:
        raise FundamentalValuationError("cannot merge empty statement set")
    sorted_statements = tuple(sorted(statements, key=lambda item: item.published_at or ""))
    fields: dict[str, Any] = {}
    source_refs: list[str] = []
    standards: list[str] = []
    for statement in sorted_statements:
        fields.update({key: value for key, value in statement.fields.items() if value is not None})
        source_refs.extend(statement.source_refs)
        if statement.reporting_standard:
            standards.append(statement.reporting_standard)
    selected_standard = requested_standard if requested_standard != "mixed" else "mixed"
    if selected_standard == "mixed" and "unknown" in set(standards):
        raise FundamentalValuationError("mixed IFRS/RAS statements require accounting_standard")
    latest = sorted_statements[-1]
    return FinancialStatement(
        statement_id=latest.statement_id,
        instrument_id=latest.instrument_id,
        period=latest.period,
        reporting_standard=selected_standard,
        source_refs=tuple(dict.fromkeys(source_refs)),
        fields=fields,
        published_at=latest.published_at,
        confidence_score=min(statement.confidence_score for statement in sorted_statements),
    )


def _latest_market_record(records: tuple[MarketDataRecord, ...], instrument_id: str) -> MarketDataRecord | None:
    candidates = [record for record in records if record.instrument_id == instrument_id]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.as_of_ts or "")[-1]


def _latest_peer_group(records: tuple[PeerGroupRecord, ...], instrument_id: str) -> PeerGroupRecord | None:
    candidates = [record for record in records if not record.instrument_ids or instrument_id in record.instrument_ids]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item.as_of_ts or "")[-1]


def _as_of_timestamp(job: ModuleJob, statement: FinancialStatement, market_record: MarketDataRecord | None) -> str:
    candidates = [statement.published_at, market_record.as_of_ts if market_record else None, job.time_range.to_ts]
    clean = [timestamp for timestamp in candidates if timestamp]
    return max(clean, key=lambda timestamp: parse_utc_iso(timestamp))


def _market_cap_stale(record: MarketDataRecord, job: ModuleJob) -> bool:
    if not record.as_of_ts:
        return True
    return (parse_utc_iso(job.time_range.to_ts) - parse_utc_iso(record.as_of_ts)).total_seconds() > MARKET_CAP_MAX_AGE_SECONDS


def _field_float(fields: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(fields.get(key))
        if value is not None:
            return value
    return None


def _history(fields: Mapping[str, Any], key: str) -> tuple[float, ...]:
    value = fields.get(key)
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = value.values()
    if isinstance(value, (str, bytes)):
        parsed = _optional_float(value)
        return () if parsed is None else (parsed,)
    try:
        iterator = iter(value)
    except TypeError:
        parsed = _optional_float(value)
        return () if parsed is None else (parsed,)
    parsed_values = tuple(_optional_float(item) for item in iterator)
    return tuple(item for item in parsed_values if item is not None)


def _peer_values(peer_record: PeerGroupRecord | None, metric_name: str) -> tuple[float, ...]:
    if peer_record is None:
        return ()
    return tuple(peer_record.values_by_metric.get(metric_name, ()))


def _metric_z(
    fields: Mapping[str, Any],
    peer_record: PeerGroupRecord | None,
    metric_name: str,
    metric_value: float | None,
) -> float | None:
    direct = _field_float(fields, f"{metric_name}_z")
    if direct is not None:
        return direct
    history_value = zscore(metric_value, _history(fields, f"{metric_name}_history"))
    if history_value is not None:
        return history_value
    return zscore(metric_value, _peer_values(peer_record, metric_name))


def _confidence_score(quality_flags: tuple[str, ...], base_confidence: float) -> float:
    confidence = float(base_confidence)
    if "stale_market_cap" in quality_flags:
        confidence = min(confidence, 0.75)
    if "low_fundamental_coverage" in quality_flags:
        confidence = min(confidence, 0.65)
    return max(0.0, min(1.0, confidence))


def _snapshot_confidence(coverage_ratio: float, quality_flags: tuple[str, ...]) -> float:
    confidence = max(0.0, min(1.0, 0.35 + 0.65 * coverage_ratio))
    if "stale_market_cap" in quality_flags:
        confidence = min(confidence, 0.75)
    if "low_fundamental_coverage" in quality_flags:
        confidence = min(confidence, 0.65)
    return confidence


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
