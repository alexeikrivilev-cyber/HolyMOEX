from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult
from agent_app.contracts.unified_objects.module_job import to_utc_iso, utc_now


class ModuleExecutor(Protocol):
    def execute(self, job: ModuleJob, payload: Mapping[str, Any]) -> ModuleJobResult:
        ...


@dataclass(frozen=True)
class ServiceTarget:
    module_path: str
    class_name: str


DEFAULT_SERVICE_TARGETS: Mapping[str, ServiceTarget] = {
    "External Request Gateway Module": ServiceTarget(
        "agent_app.modules.external_request_gateway.service", "ExternalRequestGatewayService"
    ),
    "Selected Instruments Registry Module": ServiceTarget(
        "agent_app.modules.selected_instruments_registry.service", "SelectedInstrumentsRegistryService"
    ),
    "Data Intake & Routing Module": ServiceTarget(
        "agent_app.modules.data_intake_routing.service", "DataIntakeRoutingService"
    ),
    "Data Quality Module": ServiceTarget("agent_app.modules.data_quality.service", "DataQualityService"),
    "Market Data Metrics Module": ServiceTarget(
        "agent_app.modules.market_data_metrics.service", "MarketDataMetricsService"
    ),
    "Liquidity & Microstructure Module": ServiceTarget(
        "agent_app.modules.liquidity_microstructure.service", "LiquidityMicrostructureService"
    ),
    "Volatility & Risk Metrics Module": ServiceTarget(
        "agent_app.modules.volatility_risk_metrics.service", "VolatilityRiskMetricsService"
    ),
    "Market Context Module": ServiceTarget("agent_app.modules.market_context.service", "MarketContextService"),
    "Fundamental & Valuation Module": ServiceTarget(
        "agent_app.modules.fundamental_valuation.service", "FundamentalValuationService"
    ),
    "Event & News Intelligence Module": ServiceTarget(
        "agent_app.modules.event_news_intelligence.service", "EventNewsIntelligenceService"
    ),
    "Earnings & Dividend Intelligence Module": ServiceTarget(
        "agent_app.modules.earnings_dividend_intelligence.service", "EarningsDividendIntelligenceService"
    ),
    "Corporate Actions Adjustment Module": ServiceTarget(
        "agent_app.modules.corporate_actions_adjustment.service", "CorporateActionsAdjustmentService"
    ),
    "Derivatives & Positioning Module": ServiceTarget(
        "agent_app.modules.derivatives_positioning.service", "DerivativesPositioningService"
    ),
    "Normalization & Feature Vector Module": ServiceTarget(
        "agent_app.modules.normalization_feature_vector.service", "NormalizationFeatureVectorService"
    ),
    "Feature Validation & Research Module": ServiceTarget(
        "agent_app.modules.feature_validation_research.service", "FeatureValidationResearchService"
    ),
    "Decision Engine Module": ServiceTarget("agent_app.modules.decision_engine.service", "DecisionEngineService"),
    "Risk Control Module": ServiceTarget("agent_app.modules.risk_control.service", "RiskControlService"),
    "Execution Engine Module": ServiceTarget("agent_app.modules.execution_engine.service", "ExecutionEngineService"),
    "Portfolio State Module": ServiceTarget("agent_app.modules.portfolio_state.service", "PortfolioStateService"),
    "Backtesting & Paper Trading Module": ServiceTarget(
        "agent_app.modules.backtesting_paper_trading.service", "BacktestingPaperTradingService"
    ),
    "Monitoring & Audit Module": ServiceTarget("agent_app.modules.monitoring_audit.service", "MonitoringAuditService"),
}


class LocalModuleExecutor:
    def __init__(
        self,
        service_targets: Mapping[str, ServiceTarget] | None = None,
        service_instances: Mapping[str, Any] | None = None,
    ) -> None:
        self.service_targets = dict(service_targets or DEFAULT_SERVICE_TARGETS)
        self.service_instances = dict(service_instances or {})

    def execute(self, job: ModuleJob, payload: Mapping[str, Any]) -> ModuleJobResult:
        started_at = to_utc_iso(utc_now())
        try:
            service = self._service_for(job.module_name)
            raw_result = self._call_service(service, payload, job)
            return self._coerce_result(raw_result, job, started_at)
        except Exception as error:
            finished_at = to_utc_iso(utc_now())
            return ModuleJobResult(
                job_id=job.job_id,
                module_name=job.module_name,
                status="failed",
                started_at=started_at,
                finished_at=finished_at,
                output_refs=(),
                warnings=(),
                errors=(str(error),),
                metrics_written=0,
                events_written=0,
                data_quality_score=None,
            )

    def _service_for(self, module_name: str) -> Any:
        if module_name in self.service_instances:
            return self.service_instances[module_name]
        target = self.service_targets.get(module_name)
        if target is None:
            raise ValueError(f"module executor target is not configured: {module_name}")
        module = importlib.import_module(target.module_path)
        service_class = getattr(module, target.class_name)
        service = service_class()
        self.service_instances[module_name] = service
        return service

    def _call_service(self, service: Any, payload: Mapping[str, Any], job: ModuleJob) -> Any:
        if hasattr(service, "process"):
            return service.process(payload, job)
        if hasattr(service, "run"):
            return service.run(payload, job)
        raise ValueError(f"module service has no process/run method: {job.module_name}")

    def _coerce_result(self, raw_result: Any, job: ModuleJob, started_at: str) -> ModuleJobResult:
        if isinstance(raw_result, ModuleJobResult):
            return raw_result
        nested = getattr(raw_result, "module_job_result", None)
        if isinstance(nested, ModuleJobResult):
            return nested
        if isinstance(nested, Mapping):
            return ModuleJobResult.from_dict(dict(nested))
        if hasattr(raw_result, "to_dict"):
            payload = raw_result.to_dict()
            if isinstance(payload, Mapping) and isinstance(payload.get("module_job_result"), Mapping):
                return ModuleJobResult.from_dict(dict(payload["module_job_result"]))
        finished_at = to_utc_iso(utc_now())
        output_refs = _output_refs(raw_result)
        return ModuleJobResult(
            job_id=job.job_id,
            module_name=job.module_name,
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            output_refs=output_refs,
            warnings=(),
            errors=(),
            metrics_written=0,
            events_written=0,
            data_quality_score=None,
        )


def _output_refs(raw_result: Any) -> tuple[str, ...]:
    if isinstance(raw_result, Mapping):
        value = raw_result.get("output_refs") or raw_result.get("output_ref")
    else:
        value = getattr(raw_result, "output_refs", None) or getattr(raw_result, "output_ref", None)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()
