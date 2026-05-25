from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult, TimeRange
from agent_app.contracts.unified_objects.module_job import VALID_PRIORITIES, utc_now

from .constants import FULL_RECALC_ALLOWED_CONTOURS, FULL_RECALC_ALLOWED_RUN_MODES, MODULE_SPECS
from .dependency_graph import DependencyGraph
from .executor import LocalModuleExecutor, ModuleExecutor
from .repository import AuditRecord, OrchestrationRepository


VALID_SYSTEM_MODES = {"analysis_only", "paper_trading", "live_trading", "maintenance"}
ANALYSIS_ONLY_SKIPPED_MODULES = {"Execution Engine Module"}


class OrchestrationError(ValueError):
    """Raised when orchestration would violate module documentation."""


@dataclass(frozen=True)
class IncomingTrigger:
    trigger_type: str
    source_module: str
    payload_ref: str
    priority: str = "normal"

    def __post_init__(self) -> None:
        if self.trigger_type not in {"scheduled", "event", "dependency", "manual", "replay"}:
            raise OrchestrationError(f"invalid trigger_type: {self.trigger_type}")
        if not self.source_module:
            raise OrchestrationError("incoming_trigger.source_module is required")
        if self.priority not in VALID_PRIORITIES:
            raise OrchestrationError(f"invalid priority: {self.priority}")


@dataclass(frozen=True)
class OrchestrationInput:
    schedule_config_ref: str | None
    dependency_graph_ref: str | None
    incoming_trigger: IncomingTrigger
    system_mode: str

    def __post_init__(self) -> None:
        if self.system_mode not in VALID_SYSTEM_MODES:
            raise OrchestrationError(f"invalid system_mode: {self.system_mode}")


@dataclass(frozen=True)
class PipelineContext:
    universe_id: str
    instrument_ids: tuple[str, ...] = ()
    horizons: tuple[str, ...] = ("intraday", "swing", "position")
    time_range: TimeRange = field(default_factory=TimeRange.instant)
    input_refs: tuple[str, ...] = ()
    config_ref: str | None = None
    system_mode: str = "paper_trading"
    run_mode_override: str | None = None
    retry_policy: Mapping[str, int] = field(default_factory=dict)
    retry_attempt: int = 0
    transitive_dependencies: bool = True
    module_payloads: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.system_mode not in VALID_SYSTEM_MODES:
            raise OrchestrationError(f"invalid context.system_mode: {self.system_mode}")
        if self.run_mode_override is not None and self.run_mode_override not in {
            "analysis_only",
            "paper_trading",
            "live_trading",
            "backtest",
            "replay",
        }:
            raise OrchestrationError(f"invalid context.run_mode_override: {self.run_mode_override}")
        if self.retry_attempt < 0:
            raise OrchestrationError("context.retry_attempt must be non-negative")
        invalid_retry_limits = {
            module_name: max_retries
            for module_name, max_retries in self.retry_policy.items()
            if module_name not in MODULE_SPECS or max_retries < 0
        }
        if invalid_retry_limits:
            raise OrchestrationError(f"invalid retry_policy: {invalid_retry_limits}")


@dataclass(frozen=True)
class PipelineRun:
    pipeline_run_id: str
    created_jobs: tuple[ModuleJob, ...]
    status: str
    critical_path: tuple[str, ...]
    skipped_modules: tuple[str, ...]
    audit_ref: str
    executed_results: tuple[ModuleJobResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "created_jobs": [job.to_dict() for job in self.created_jobs],
            "executed_results": [result.to_dict() for result in self.executed_results],
            "status": self.status,
            "critical_path": list(self.critical_path),
            "skipped_modules": list(self.skipped_modules),
            "audit_ref": self.audit_ref,
        }


@dataclass(frozen=True)
class OrchestrationState:
    pipeline_run_id: str
    status: str
    updated_at: str
    active_job_ids: tuple[str, ...]
    skipped_modules: tuple[str, ...]
    source_module: str = "Orchestration Module"
    calculation_version: str = "orchestration_state_v1"
    confidence_score: float = 1.0
    ttl_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "status": self.status,
            "updated_at": self.updated_at,
            "timestamp": self.updated_at,
            "active_job_ids": list(self.active_job_ids),
            "skipped_modules": list(self.skipped_modules),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "confidence_score": self.confidence_score,
            "ttl_seconds": self.ttl_seconds,
        }


class OrchestrationService:
    module_name = "Orchestration Module"

    def __init__(
        self,
        repository: OrchestrationRepository,
        executor: ModuleExecutor | None = None,
        execute_jobs: bool = True,
    ) -> None:
        self.repository = repository
        self.executor = executor or LocalModuleExecutor()
        self.execute_jobs = execute_jobs

    def start(self, request: OrchestrationInput, context: PipelineContext) -> PipelineRun:
        """Start the root orchestration service without an incoming module_job."""
        return self.run_pipeline(request, context)

    def run(self, request: OrchestrationInput, context: PipelineContext) -> PipelineRun:
        """Alias for root service execution; no module_job is required."""
        return self.start(request, context)

    def run_pipeline(self, request: OrchestrationInput, context: PipelineContext) -> PipelineRun:
        graph = self.repository.load_dependency_graph(request.dependency_graph_ref)
        pipeline_run_id = self._pipeline_run_id(request, context)

        if self._is_full_recalculation(request):
            self._assert_full_recalculation_allowed(request, context)

        targets = self.resolve_dependencies(request, graph, context)
        if request.system_mode == "maintenance":
            skipped = tuple(target for target in targets if not MODULE_SPECS[target].service_only)
            targets = tuple(target for target in targets if MODULE_SPECS[target].service_only)
        elif request.system_mode == "analysis_only":
            skipped = tuple(target for target in targets if target in ANALYSIS_ONLY_SKIPPED_MODULES)
            targets = tuple(target for target in targets if target not in ANALYSIS_ONLY_SKIPPED_MODULES)
        else:
            skipped = ()

        active_instruments = self.enforce_universe_filter(context)
        dropped_instruments = tuple(
            instrument_id for instrument_id in context.instrument_ids if instrument_id not in active_instruments
        )

        created_jobs: list[ModuleJob] = []
        executed_results: list[ModuleJobResult] = []
        cycle_refs = list(self._input_refs(request, context))
        cycle_config_ref = context.config_ref or self._default_runtime_config_ref(request.system_mode)
        for target in targets:
            job_context = replace(
                context,
                input_refs=tuple(dict.fromkeys(cycle_refs)),
                config_ref=cycle_config_ref,
            )
            job = self.build_module_job(
                module_name=target,
                request=request,
                context=job_context,
                active_instruments=active_instruments,
            )
            self.check_idempotency_key(job)
            save_result = self.repository.save_module_job(job)
            result = self.route_job_to_module(
                save_result.job,
                save_result.created,
                pipeline_run_id,
                request,
                job_context,
            )
            created_jobs.append(save_result.job)
            if result is not None:
                executed_results.append(result)
                cycle_refs.extend(result.output_refs)

        critical_path = self._critical_path(targets, graph, request)
        status = self._pipeline_status(
            created_jobs,
            skipped,
            dropped_instruments,
            tuple(executed_results),
            critical_path,
        )
        self.update_orchestration_state(
            pipeline_run_id=pipeline_run_id,
            status=status,
            jobs=tuple(created_jobs),
            skipped_modules=skipped,
        )
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                severity="info" if status in {"running", "completed"} else "warning",
                event_type="pipeline_run_created",
                message="Orchestration pipeline run created",
                object_type="pipeline_run",
                object_ref=pipeline_run_id,
                reason_codes=("module_dependency_graph_respected", "idempotency_enforced"),
                payload={
                    "status": status,
                    "created_job_ids": [job.job_id for job in created_jobs],
                    "executed_job_ids": [result.job_id for result in executed_results],
                    "skipped_modules": list(skipped),
                    "dropped_instrument_ids": list(dropped_instruments),
                    "source_module": request.incoming_trigger.source_module,
                    "payload_ref": request.incoming_trigger.payload_ref,
                },
            )
        )

        return PipelineRun(
            pipeline_run_id=pipeline_run_id,
            created_jobs=tuple(created_jobs),
            executed_results=tuple(executed_results),
            status=status,
            critical_path=critical_path,
            skipped_modules=skipped,
            audit_ref=audit_ref,
        )

    def collect_module_job_result(
        self,
        result: ModuleJobResult,
        context: PipelineContext,
        critical_dependency: bool | None = None,
    ) -> PipelineRun:
        self.repository.save_module_job_result(result)
        severity = "error" if result.status == "failed" else "warning" if result.status == "partial_success" else "info"
        self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=result.job_id,
                severity=severity,
                event_type="module_job_result_collected",
                message=f"Collected module_job_result for {result.module_name}",
                object_type="module_job_result",
                object_ref=result.job_id,
                reason_codes=(f"module_status_{result.status}",),
                payload=result.to_dict(),
            )
        )

        graph = self.repository.load_dependency_graph(None)
        is_critical = critical_dependency if critical_dependency is not None else result.status == "failed"
        if result.status == "failed" and is_critical:
            return self.stop_pipeline_on_critical_dependency_failure(result, graph)

        if result.status == "failed":
            retry_run = self.apply_retry_policy(result, context)
            if retry_run is not None:
                return retry_run

        if result.status in {"success", "partial_success"} and result.output_refs:
            return self.trigger_downstream_jobs(result, context)

        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=result.job_id,
                severity="warning" if result.status == "failed" else "info",
                event_type="pipeline_not_extended",
                message="No downstream jobs were created from module_job_result",
                object_type="module_job",
                object_ref=result.job_id,
                reason_codes=("non_critical_failure" if result.status == "failed" else "no_output_refs",),
                payload=result.to_dict(),
            )
        )
        return PipelineRun(
            pipeline_run_id=f"pipeline_result_{self._stable_hash(result.to_dict())[:24]}",
            created_jobs=(),
            status="degraded" if result.status == "failed" else "completed",
            critical_path=(),
            skipped_modules=(),
            audit_ref=audit_ref,
        )

    def build_module_job(
        self,
        module_name: str,
        request: OrchestrationInput,
        context: PipelineContext,
        active_instruments: tuple[str, ...],
    ) -> ModuleJob:
        if module_name == self.module_name:
            raise OrchestrationError("Orchestration Module is a root service and is not launched via module_job")
        spec = MODULE_SPECS.get(module_name)
        if spec is None:
            raise OrchestrationError(f"unknown module_name: {module_name}")
        run_mode = self._module_run_mode(request.system_mode, context)
        input_refs = self._input_refs(request, context)
        horizons = tuple(horizon for horizon in context.horizons if horizon in spec.default_horizons)
        if not horizons and spec.default_horizons:
            horizons = spec.default_horizons

        idempotency_payload = {
            "module_name": module_name,
            "trigger_type": request.incoming_trigger.trigger_type,
            "source_module": request.incoming_trigger.source_module,
            "payload_ref": request.incoming_trigger.payload_ref,
            "universe_id": context.universe_id,
            "instrument_ids": active_instruments,
            "horizons": horizons,
            "time_range": context.time_range.to_dict(),
            "input_refs": input_refs,
            "config_ref": context.config_ref or request.schedule_config_ref or request.dependency_graph_ref,
            "run_mode": run_mode,
            "retry_attempt": context.retry_attempt,
        }
        idempotency_key = f"orchestration:{self._stable_hash(idempotency_payload)}"
        job_id = f"job_{self._stable_hash({'idempotency_key': idempotency_key})[:24]}"
        return ModuleJob(
            job_id=job_id,
            module_name=module_name,
            contour=spec.primary_contour,
            trigger_type=request.incoming_trigger.trigger_type,
            universe_id=context.universe_id,
            instrument_ids=active_instruments,
            horizons=horizons,
            time_range=context.time_range,
            input_refs=input_refs,
            config_ref=context.config_ref or request.schedule_config_ref or request.dependency_graph_ref,
            run_mode=run_mode,
            idempotency_key=idempotency_key,
            priority=request.incoming_trigger.priority,
            status="pending",
        )

    def resolve_dependencies(
        self,
        request: OrchestrationInput,
        graph: DependencyGraph,
        context: PipelineContext,
    ) -> tuple[str, ...]:
        return self._resolve_targets(request, graph, context)

    def check_idempotency_key(self, job: ModuleJob) -> None:
        if not job.idempotency_key:
            raise OrchestrationError("module_job.idempotency_key is required")

    def enforce_universe_filter(self, context: PipelineContext) -> tuple[str, ...]:
        return self.repository.filter_active_instruments(
            context.universe_id,
            context.instrument_ids,
        )

    def route_job_to_module(
        self,
        job: ModuleJob,
        created: bool,
        pipeline_run_id: str,
        request: OrchestrationInput,
        context: PipelineContext,
    ) -> ModuleJobResult | None:
        if not created:
            self.repository.save_module_run(
                job,
                status="skipped",
                payload={
                    "pipeline_run_id": pipeline_run_id,
                    "route_policy": "idempotency_reused",
                    "idempotency_reused": True,
                },
            )
            return None
        if not self.execute_jobs:
            self.repository.save_module_run(
                job,
                status="pending",
                payload={
                    "pipeline_run_id": pipeline_run_id,
                    "route_policy": "execution_deferred_by_configuration",
                    "idempotency_reused": False,
                },
            )
            return None

        self.repository.save_module_run(
            job,
            status="running",
            payload={
                "pipeline_run_id": pipeline_run_id,
                "route_policy": "execute_through_module_executor",
                "idempotency_reused": False,
            },
        )
        result = self.execute_module_job(job, request, context)
        self.persist_module_job_result(job, result, pipeline_run_id)
        return result

    def execute_module_job(
        self,
        job: ModuleJob,
        request: OrchestrationInput,
        context: PipelineContext,
    ) -> ModuleJobResult:
        payload = self._module_payload(job, request, context)
        return self.executor.execute(job, payload)

    def persist_module_job_result(
        self,
        job: ModuleJob,
        result: ModuleJobResult,
        pipeline_run_id: str,
    ) -> None:
        self.repository.save_module_job_result(result)
        self.repository.save_module_run(
            job,
            status=result.status,
            payload={
                "pipeline_run_id": pipeline_run_id,
                "route_policy": "execute_through_module_executor",
                "module_job_result": result.to_dict(),
            },
        )
        severity = "error" if result.status == "failed" else "warning" if result.status == "partial_success" else "info"
        self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=job.job_id,
                severity=severity,
                event_type="module_job_executed",
                message=f"Executed module_job for {job.module_name}",
                object_type="module_job",
                object_ref=job.job_id,
                reason_codes=(f"module_status_{result.status}", "created_jobs_are_executed_through_module_executor"),
                payload={
                    "pipeline_run_id": pipeline_run_id,
                    "module_job_result": result.to_dict(),
                },
            )
        )

    def collect_job_result(self, result: ModuleJobResult, context: PipelineContext) -> PipelineRun:
        return self.collect_module_job_result(result, context)

    def write_audit_record(self, record: AuditRecord) -> str:
        return self.repository.write_audit_record(record)

    def trigger_downstream_jobs(self, result: ModuleJobResult, context: PipelineContext) -> PipelineRun:
        trigger = IncomingTrigger(
            trigger_type="dependency",
            source_module=result.module_name,
            payload_ref=",".join(result.output_refs),
            priority="normal",
        )
        request = OrchestrationInput(
            schedule_config_ref=None,
            dependency_graph_ref=None,
            incoming_trigger=trigger,
            system_mode=contextual_run_mode(context),
        )
        dependency_context = PipelineContext(
            universe_id=context.universe_id,
            instrument_ids=context.instrument_ids,
            horizons=context.horizons,
            time_range=context.time_range,
            input_refs=result.output_refs,
            config_ref=context.config_ref,
            system_mode=context.system_mode,
            run_mode_override=context.run_mode_override,
            retry_policy=context.retry_policy,
            retry_attempt=context.retry_attempt,
            transitive_dependencies=context.transitive_dependencies,
            module_payloads=context.module_payloads,
        )
        return self.run_pipeline(request, dependency_context)

    def apply_retry_policy(self, result: ModuleJobResult, context: PipelineContext) -> PipelineRun | None:
        max_retries = context.retry_policy.get(result.module_name, 0)
        if context.retry_attempt >= max_retries:
            self.write_audit_record(
                AuditRecord(
                    module_name=self.module_name,
                    job_id=result.job_id,
                    severity="warning",
                    event_type="retry_not_scheduled",
                    message="Retry policy does not allow another attempt",
                    object_type="module_job",
                    object_ref=result.job_id,
                    reason_codes=("retry_policy_exhausted",),
                    payload={
                        "module_name": result.module_name,
                        "retry_attempt": context.retry_attempt,
                        "max_retries": max_retries,
                    },
                )
            )
            return None

        request = OrchestrationInput(
            schedule_config_ref=None,
            dependency_graph_ref=None,
            incoming_trigger=IncomingTrigger(
                trigger_type="replay",
                source_module=result.module_name,
                payload_ref=f"module:{result.module_name}",
                priority="normal",
            ),
            system_mode=contextual_run_mode(context),
        )
        retry_context = PipelineContext(
            universe_id=context.universe_id,
            instrument_ids=context.instrument_ids,
            horizons=context.horizons,
            time_range=context.time_range,
            input_refs=context.input_refs,
            config_ref=context.config_ref,
            system_mode=context.system_mode,
            run_mode_override=context.run_mode_override,
            retry_policy=context.retry_policy,
            retry_attempt=context.retry_attempt + 1,
            transitive_dependencies=False,
            module_payloads=context.module_payloads,
        )
        self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=result.job_id,
                severity="warning",
                event_type="retry_scheduled",
                message="Retry job scheduled after failed module_job_result",
                object_type="module_job",
                object_ref=result.job_id,
                reason_codes=("apply_retry_policy",),
                payload={
                    "module_name": result.module_name,
                    "retry_attempt": retry_context.retry_attempt,
                    "max_retries": max_retries,
                },
            )
        )
        return self.run_pipeline(request, retry_context)

    def stop_pipeline_on_critical_dependency_failure(
        self,
        result: ModuleJobResult,
        graph: DependencyGraph,
    ) -> PipelineRun:
        skipped = graph.critical_targets_from(result.module_name, transitive=True)
        audit_ref = self.write_audit_record(
            AuditRecord(
                module_name=self.module_name,
                job_id=result.job_id,
                severity="critical",
                event_type="critical_dependency_failed",
                message="Critical dependency failed; downstream jobs blocked",
                object_type="module_job",
                object_ref=result.job_id,
                reason_codes=("critical_failures_block_downstream",),
                payload={"blocked_modules": list(skipped), "errors": list(result.errors)},
            )
        )
        pipeline_run_id = f"pipeline_result_{self._stable_hash(result.to_dict())[:24]}"
        self.update_orchestration_state(
            pipeline_run_id=pipeline_run_id,
            status="failed",
            jobs=(),
            skipped_modules=skipped,
        )
        return PipelineRun(
            pipeline_run_id=pipeline_run_id,
            created_jobs=(),
            status="failed",
            critical_path=skipped,
            skipped_modules=skipped,
            audit_ref=audit_ref,
        )

    def update_orchestration_state(
        self,
        pipeline_run_id: str,
        status: str,
        jobs: tuple[ModuleJob, ...],
        skipped_modules: tuple[str, ...],
    ) -> OrchestrationState:
        state = OrchestrationState(
            pipeline_run_id=pipeline_run_id,
            status=status,
            updated_at=utc_now().isoformat().replace("+00:00", "Z"),
            active_job_ids=tuple(job.job_id for job in jobs),
            skipped_modules=skipped_modules,
        )
        self.repository.save_orchestration_state(status=state.status, payload=state.to_dict())
        return state

    def _resolve_targets(
        self,
        request: OrchestrationInput,
        graph: DependencyGraph,
        context: PipelineContext,
    ) -> tuple[str, ...]:
        trigger = request.incoming_trigger
        if trigger.trigger_type in {"manual", "replay"} and trigger.payload_ref.startswith("module:"):
            module_name = trigger.payload_ref.removeprefix("module:")
            if module_name == self.module_name:
                return ()
            if module_name not in MODULE_SPECS:
                raise OrchestrationError(f"manual trigger references unknown module: {module_name}")
            return (module_name,)

        sources = [trigger.source_module]
        if trigger.payload_ref:
            sources.extend(ref.strip() for ref in trigger.payload_ref.split(",") if ref.strip())
        targets = graph.module_targets(sources, transitive=context.transitive_dependencies)
        if not targets:
            return ("Monitoring & Audit Module",)
        return targets

    def _critical_path(
        self,
        targets: Iterable[str],
        graph: DependencyGraph,
        request: OrchestrationInput,
    ) -> tuple[str, ...]:
        source = request.incoming_trigger.source_module
        critical_targets = set(graph.critical_targets_from(source, transitive=True))
        return tuple(target for target in targets if target in critical_targets)

    def _is_full_recalculation(self, request: OrchestrationInput) -> bool:
        payload_ref = request.incoming_trigger.payload_ref
        return payload_ref in {"full_recalculation", "full_system_recalculation"} or (
            request.incoming_trigger.trigger_type == "manual"
            and request.incoming_trigger.source_module == "full_system"
        )

    def _assert_full_recalculation_allowed(self, request: OrchestrationInput, context: PipelineContext) -> None:
        run_mode = self._module_run_mode(request.system_mode, context)
        if run_mode in FULL_RECALC_ALLOWED_RUN_MODES:
            return
        if context.config_ref and "calculation_version" in context.config_ref:
            return
        if context.config_ref and any(contour in context.config_ref for contour in FULL_RECALC_ALLOWED_CONTOURS):
            return
        raise OrchestrationError("full system recalculation is allowed only in research_contour/backtest/replay")

    def _module_run_mode(self, system_mode: str, context: PipelineContext) -> str:
        if context.run_mode_override:
            return context.run_mode_override
        if system_mode == "maintenance":
            return "analysis_only"
        return system_mode

    def _input_refs(self, request: OrchestrationInput, context: PipelineContext) -> tuple[str, ...]:
        refs = list(context.input_refs)
        payload_ref = request.incoming_trigger.payload_ref
        if payload_ref and not payload_ref.startswith("module:") and payload_ref not in refs:
            refs.append(payload_ref)
        if request.incoming_trigger.trigger_type == "scheduled":
            refs.extend(self._autonomous_cycle_refs(context))
        elif not refs and request.incoming_trigger.source_module.endswith(" Store"):
            refs.extend(self._autonomous_cycle_refs(context))
        return tuple(dict.fromkeys(refs))

    def _autonomous_cycle_refs(self, context: PipelineContext) -> tuple[str, ...]:
        run_mode = context.run_mode_override or context.system_mode
        risk_policy_ref = (
            "risk.risk_policy:risk_policy:live_autonomous_turnover:v1"
            if run_mode == "live_trading"
            else "risk.risk_policy:risk_policy:paper_trading:v1"
        )
        return (
            "raw_market.raw_candle:scheduled",
            "raw_market.raw_trade:scheduled",
            "raw_market.raw_index_value:IMOEX",
            "raw_market.raw_index_value:sector",
            "raw_text.raw_text_item:scheduled",
            "raw_text.event_routing_message:scheduled",
            "raw_macro.raw_macro_point:scheduled",
            "events.structured_event:scheduled",
            "features.feature_record:latest",
            "features.feature_vector:latest",
            "features.data_quality_report:latest",
            "features.market_state_record:latest",
            "portfolio.portfolio_snapshot:latest",
            "decisions.decision_set:latest",
            "orders.order_intent:latest",
            "orders.fill_report:latest",
            "broker.snapshot:scheduled",
            "price.snapshot:scheduled",
            risk_policy_ref,
            "execution_policy:arena_go:live:v1" if run_mode == "live_trading" else "execution_policy:arena_go:paper:v1",
        )

    def _pipeline_status(
        self,
        created_jobs: list[ModuleJob],
        skipped: tuple[str, ...],
        dropped_instruments: tuple[str, ...],
        executed_results: tuple[ModuleJobResult, ...],
        critical_path: tuple[str, ...],
    ) -> str:
        failed_modules = {result.module_name for result in executed_results if result.status == "failed"}
        if failed_modules.intersection(critical_path):
            return "failed"
        if failed_modules or any(result.status in {"partial_success", "skipped"} for result in executed_results):
            return "degraded"
        if skipped or dropped_instruments:
            return "degraded"
        if self.execute_jobs and created_jobs and len(executed_results) == len(created_jobs):
            return "completed"
        if created_jobs:
            return "running"
        return "completed"

    def _pipeline_run_id(self, request: OrchestrationInput, context: PipelineContext) -> str:
        payload = {
            "trigger": request.incoming_trigger.__dict__,
            "system_mode": request.system_mode,
            "context": {
                "universe_id": context.universe_id,
                "instrument_ids": context.instrument_ids,
                "horizons": context.horizons,
                "time_range": context.time_range.to_dict(),
                "input_refs": context.input_refs,
                "config_ref": context.config_ref,
                "system_mode": context.system_mode,
                "run_mode_override": context.run_mode_override,
            },
            "created_at_bucket": utc_now().strftime("%Y%m%dT%H%M%S"),
        }
        return f"pipeline_{self._stable_hash(payload)[:24]}"


    def _default_runtime_config_ref(self, system_mode: str) -> str:
        if system_mode == "live_trading":
            return "runtime_config:live_autonomous:v1"
        if system_mode == "paper_trading":
            return "runtime_config:paper_trading:v1"
        if system_mode == "maintenance":
            return "runtime_config:maintenance:v1"
        return "runtime_config:analysis_only:v1"


    def _current_input_ref(self, refs: Iterable[str], prefix: str, fallback: str = "") -> str:
        for ref in reversed(tuple(refs)):
            text = str(ref)
            if text.startswith(prefix + ":") and not text.endswith((":scheduled", ":latest")):
                return text
        return fallback

    def _latest_input_ref(self, refs: Iterable[str], prefix: str, fallback: str) -> str:
        for ref in reversed(tuple(refs)):
            if str(ref).startswith(prefix + ":") and not str(ref).endswith(":scheduled"):
                return str(ref)
        for ref in reversed(tuple(refs)):
            if str(ref).startswith(prefix + ":"):
                return str(ref)
        return fallback

    def _input_refs_by_prefix(self, refs: Iterable[str], prefix: str, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
        matches = tuple(str(ref) for ref in refs if str(ref).startswith(prefix + ":") and not str(ref).endswith((":scheduled", ":latest")))
        return matches or fallback

    def _module_payload(
        self,
        job: ModuleJob,
        request: OrchestrationInput,
        context: PipelineContext,
    ) -> Mapping[str, Any]:
        payloads = context.module_payloads or {}
        for key in (job.job_id, job.module_name, request.incoming_trigger.payload_ref):
            if key and key in payloads:
                return payloads[key]
        for input_ref in job.input_refs:
            if input_ref in payloads:
                return payloads[input_ref]
        if "__default__" in payloads:
            return payloads["__default__"]
        return self._autonomous_default_payload(job, context)

    def _autonomous_default_payload(self, job: ModuleJob, context: PipelineContext) -> Mapping[str, Any]:
        instrument_ids = tuple(job.instrument_ids or context.instrument_ids)
        horizons = tuple(job.horizons or context.horizons)
        horizon = horizons[0] if horizons else "intraday"
        as_of_ts = job.time_range.to_ts
        run_mode = job.run_mode
        portfolio_id = __import__("os").getenv("ARENA_GO_PORTFOLIO") or __import__("os").getenv("ARENA_GO_BOT_NAME") or "arena_go_default"
        risk_policy_id = (
            "risk_policy:live_autonomous_turnover:v1"
            if run_mode == "live_trading"
            else "risk_policy:paper_trading:v1"
        )
        weights_profile_id = (
            f"weights:live_autonomous:{horizon}:v1"
            if run_mode == "live_trading"
            else f"weights:product_baseline:{horizon}:v1"
        )
        feature_vector_ref = self._latest_input_ref(job.input_refs, "features.feature_vector", "features.feature_vector:latest")
        feature_record_ref = self._latest_input_ref(job.input_refs, "features.feature_record", "features.feature_record:latest")
        feature_vector_refs = self._input_refs_by_prefix(job.input_refs, "features.feature_vector", (feature_vector_ref,))
        feature_record_refs = self._input_refs_by_prefix(job.input_refs, "features.feature_record", (feature_record_ref,))
        portfolio_snapshot_ref = self._latest_input_ref(job.input_refs, "portfolio.portfolio_snapshot", "portfolio.portfolio_snapshot:latest")
        # Risk must check the decision set produced in the current cycle.
        # Falling back to decisions.decision_set:latest could approve stale orders.
        decision_set_ref = self._current_input_ref(job.input_refs, "decisions.decision_set")
        market_state_ref = self._latest_input_ref(job.input_refs, "features.market_state_record", "features.market_state_record:latest")
        data_quality_report_ref = self._latest_input_ref(job.input_refs, "features.data_quality_report", "features.data_quality_report:latest")
        raw_text_refs = self._input_refs_by_prefix(job.input_refs, "raw_text.raw_text_item", ("raw_text.raw_text_item:scheduled",))
        routing_message_refs = self._input_refs_by_prefix(job.input_refs, "raw_text.event_routing_message", ("raw_text.event_routing_message:scheduled",))
        structured_event_refs = self._input_refs_by_prefix(job.input_refs, "events.structured_event", ("events.structured_event:scheduled",))
        order_intent_refs = self._input_refs_by_prefix(job.input_refs, "orders.order_intent", ())
        fill_report_refs = self._input_refs_by_prefix(job.input_refs, "orders.fill_report", ())
        quality_input_refs = tuple(
            ref
            for ref in job.input_refs
            if str(ref).startswith((
                "raw_market.raw_candle:",
                "raw_market.raw_trade:",
                "raw_market.raw_orderbook:",
                "raw_market.raw_index_value:",
                "raw_text.raw_text_item:",
                "features.feature_record:",
                "features.feature_vector:",
                "portfolio.portfolio_snapshot:",
            ))
        ) or ("raw_market.raw_candle:scheduled",)
        monitoring_payload_ref = self._latest_input_ref(job.input_refs, "portfolio.portfolio_snapshot", self._latest_input_ref(job.input_refs, "orders.execution_result", "audit.module_run:scheduled"))
        payload_by_module: dict[str, Mapping[str, Any]] = {
            "Data Intake & Routing Module": {
                "intake_request": {
                    "universe_id": job.universe_id,
                    "instrument_ids": list(instrument_ids),
                    "source_types": ["rbc_news", "finam_news", "smartlab_news"],
                    "discovery_mode": "scheduled",
                    "per_instrument_discovery": True,
                    "time_range": job.time_range.to_dict(),
                    "routing_targets": [
                        "Event & News Intelligence Module",
                        "Earnings & Dividend Intelligence Module",
                        "Corporate Actions Adjustment Module",
                        "Market Context Module",
                    ],
                    "routing_ttl_seconds": 3600,
                }
            },
            "Data Quality Module": {
                "quality_check_request": {
                    "input_refs": list(quality_input_refs),
                    "check_level": "raw",
                    "required_freshness_seconds": 300 if run_mode == "live_trading" else 3600,
                    "required_coverage_ratio": 0.80 if run_mode == "live_trading" else 0.50,
                    "critical_fields": ["timestamp", "source_module", "calculation_version"],
                }
            },
            "Market Data Metrics Module": {
                "market_data_metrics_input": {
                    "instrument_ids": list(instrument_ids),
                    "candles_ref": "raw_market.raw_candle:scheduled",
                    "trades_ref": "raw_market.raw_trade:scheduled",
                    "market_index_ref": "raw_market.raw_index_value:IMOEX",
                    "sector_index_ref": "raw_market.raw_index_value:sector",
                    "timeframes": ["1m", "5m", "1d"],
                    "horizons": list(horizons),
                }
            },
            "Liquidity & Microstructure Module": {
                "liquidity_input": {
                    "instrument_ids": list(instrument_ids),
                    "orderbook_ref": "raw_market.raw_orderbook:scheduled",
                    "trades_ref": "raw_market.raw_trade:scheduled",
                    "quotes_ref": "raw_market.raw_quote:scheduled",
                    "depth_levels": ["10bps", "30bps", "50bps", "100bps"],
                    "notional_scenarios": [100000, 1000000],
                }
            },
            "Derivatives & Positioning Module": {
                "derivatives_input": {
                    "instrument_ids": list(instrument_ids),
                    "futures_refs": ["raw_market.raw_futures:scheduled"],
                    "options_refs": ["raw_market.raw_options:scheduled"],
                    "spot_refs": ["raw_market.raw_candle:scheduled", "raw_market.raw_trade:scheduled"],
                    "liquidity_thresholds": {
                        "min_turnover": 0.0,
                        "min_open_interest": 0.0,
                    },
                }
            },
            "Volatility & Risk Metrics Module": {
                "risk_metrics_input": {
                    "instrument_ids": list(instrument_ids),
                    "candles_ref": "raw_market.raw_candle:scheduled",
                    "market_index_ref": "raw_market.raw_index_value:IMOEX",
                    "sector_index_ref": "raw_market.raw_index_value:sector",
                    "macro_refs": ["raw_macro.raw_macro_point:scheduled"],
                    "windows": [5, 20, 60],
                    "horizons": list(horizons),
                }
            },
            "Market Context Module": {
                "market_context_input": {
                    "universe_id": job.universe_id,
                    "instrument_ids": list(instrument_ids),
                    "macro_refs": ["raw_macro.raw_macro_point:scheduled"],
                    "index_refs": ["raw_market.raw_index_value:IMOEX", "raw_market.raw_index_value:RTSI", "raw_market.raw_index_value:RGBI"],
                    "sector_refs": ["raw_market.raw_index_value:sector"],
                    "event_refs": ["events.structured_event:scheduled"],
                    "windows": [5, 20, 60],
                }
            },
            "Fundamental & Valuation Module": {
                "fundamental_input": {
                    "instrument_ids": list(instrument_ids),
                    "financial_statement_refs": list(raw_text_refs),
                    "market_cap_ref": "raw_market.raw_candle:scheduled",
                    "peer_group_ref": "features.fundamental_snapshot:peer_group",
                    "reporting_standard": "unknown",
                    "period": "latest",
                }
            },
            "Event & News Intelligence Module": {
                "event_news_input": {
                    "routing_message_refs": list(routing_message_refs),
                    "raw_text_refs": list(raw_text_refs),
                    "instrument_ids": list(instrument_ids),
                    "event_ontology_version": "event_ontology:moex:v1",
                    "llm_prompt_version": "event_news_extraction:v1",
                    "market_reaction_window": ["5m", "1h", "1d"],
                }
            },
            "Earnings & Dividend Intelligence Module": {
                "earnings_dividend_input": {
                    "instrument_ids": list(instrument_ids),
                    "report_refs": list(raw_text_refs),
                    "dividend_event_refs": list(structured_event_refs),
                    "financial_expectation_ref": "features.fundamental_snapshot:expectations",
                    "historical_gap_ref": "raw_market.raw_candle:dividend_gap_history",
                    "llm_prompt_version": "earnings_dividend_extraction:v1",
                }
            },
            "Normalization & Feature Vector Module": {
                "normalization_input": {
                    "instrument_ids": list(instrument_ids),
                    "horizons": list(horizons),
                    "as_of_ts": as_of_ts,
                    "feature_refs": list(feature_record_refs),
                    "normalization_profile_id": "normalization:live_autonomous:v1" if run_mode == "live_trading" else "normalization:product_baseline:v1",
                }
            },
            "Decision Engine Module": {
                "decision_request": {
                    "decision_request_id": f"decision_request:{job.job_id}",
                    "universe_id": job.universe_id,
                    "instrument_ids": list(instrument_ids),
                    "horizon": horizon,
                    "as_of_ts": as_of_ts,
                    "feature_vector_refs": list(feature_vector_refs),
                    "portfolio_state_ref": portfolio_snapshot_ref,
                    "weights_profile_id": weights_profile_id,
                    "run_mode": run_mode,
                    "decision_mode": "normal",
                }
            },
            "Risk Control Module": {
                "risk_check_request": {
                    "decision_set_id": decision_set_ref,
                    "portfolio_state_ref": portfolio_snapshot_ref,
                    "risk_policy_id": risk_policy_id,
                    "market_state_ref": market_state_ref,
                    "data_quality_report_ref": data_quality_report_ref,
                    "run_mode": run_mode,
                }
            },
            "Execution Engine Module": {
                "execution_request": {
                    "order_intent_refs": list(order_intent_refs),
                    "market_session_status_ref": market_state_ref,
                    "execution_policy_id": "execution_policy:arena_go:live:v1" if run_mode == "live_trading" else "execution_policy:arena_go:paper:v1",
                    "run_mode": run_mode if run_mode in {"paper_trading", "live_trading"} else "paper_trading",
                    "idempotency_key": job.idempotency_key,
                }
            },
            "Portfolio State Module": {
                "portfolio_update_request": {
                    "portfolio_id": portfolio_id,
                    "fill_report_refs": list(fill_report_refs),
                    "broker_snapshot_ref": "broker.snapshot:scheduled",
                    "price_snapshot_ref": "price.snapshot:scheduled",
                    "run_mode": run_mode if run_mode in {"analysis_only", "paper_trading", "live_trading"} else "paper_trading",
                    "as_of_ts": as_of_ts,
                }
            },
            "Monitoring & Audit Module": {
                "monitoring_event": {
                    "event_id": f"monitoring_event:{job.job_id}",
                    "event_type": "system",
                    "severity": "info",
                    "source_module": "Orchestration Module",
                    "payload_ref": monitoring_payload_ref,
                    "created_at": as_of_ts,
                }
            },
        }
        return payload_by_module.get(job.module_name, {})

    def _stable_hash(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def contextual_run_mode(context: PipelineContext) -> str:
    return context.system_mode
