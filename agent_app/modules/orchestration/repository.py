from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent_app.contracts.unified_objects import ModuleJob, ModuleJobResult

from .dependency_graph import DependencyGraph


@dataclass(frozen=True)
class SaveJobResult:
    job: ModuleJob
    created: bool


@dataclass(frozen=True)
class AuditRecord:
    module_name: str
    severity: str
    event_type: str
    message: str
    job_id: str | None = None
    object_type: str | None = None
    object_ref: str | None = None
    reason_codes: tuple[str, ...] = ()
    payload: dict[str, Any] | None = None


class OrchestrationRepository(Protocol):
    def load_dependency_graph(self, graph_ref: str | None = None) -> DependencyGraph:
        ...

    def filter_active_instruments(self, universe_id: str, instrument_ids: tuple[str, ...]) -> tuple[str, ...]:
        ...

    def save_module_job(self, job: ModuleJob) -> SaveJobResult:
        ...

    def save_module_run(self, job: ModuleJob, status: str, payload: dict[str, Any] | None = None) -> None:
        ...

    def save_orchestration_state(self, status: str, payload: dict[str, Any]) -> None:
        ...

    def save_module_job_result(self, result: ModuleJobResult) -> None:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryOrchestrationRepository:
    def __init__(
        self,
        graph: DependencyGraph | None = None,
        active_instruments: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.graph = graph or DependencyGraph.default()
        self.active_instruments = active_instruments or {}
        self.jobs_by_id: dict[str, ModuleJob] = {}
        self.jobs_by_idempotency: dict[str, ModuleJob] = {}
        self.module_runs: list[dict[str, Any]] = []
        self.results_by_job_id: dict[str, ModuleJobResult] = {}
        self.audit_records: list[AuditRecord] = []

    def load_dependency_graph(self, graph_ref: str | None = None) -> DependencyGraph:
        return self.graph

    def filter_active_instruments(self, universe_id: str, instrument_ids: tuple[str, ...]) -> tuple[str, ...]:
        allowed = self.active_instruments.get(universe_id)
        if allowed is None:
            return tuple(dict.fromkeys(instrument_ids))
        allowed_set = set(allowed)
        if not instrument_ids:
            return allowed
        return tuple(instrument_id for instrument_id in dict.fromkeys(instrument_ids) if instrument_id in allowed_set)

    def save_module_job(self, job: ModuleJob) -> SaveJobResult:
        existing = self.jobs_by_idempotency.get(job.idempotency_key)
        if existing is not None:
            return SaveJobResult(existing, created=False)
        if job.job_id in self.jobs_by_id:
            raise ValueError(f"duplicate job_id: {job.job_id}")
        self.jobs_by_id[job.job_id] = job
        self.jobs_by_idempotency[job.idempotency_key] = job
        return SaveJobResult(job, created=True)

    def save_module_run(self, job: ModuleJob, status: str, payload: dict[str, Any] | None = None) -> None:
        self.module_runs.append(
            {
                "job_id": job.job_id,
                "module_name": job.module_name,
                "status": status,
                "payload": payload or {},
            }
        )

    def save_orchestration_state(self, status: str, payload: dict[str, Any]) -> None:
        self.module_runs.append(
            {
                "job_id": None,
                "module_name": "Orchestration Module",
                "status": status,
                "payload": {"object_type": "orchestration_state", **payload},
            }
        )

    def save_module_job_result(self, result: ModuleJobResult) -> None:
        self.results_by_job_id[result.job_id] = result

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"


class PostgresOrchestrationRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def load_dependency_graph(self, graph_ref: str | None = None) -> DependencyGraph:
        if graph_ref:
            query = """
                SELECT graph_payload
                  FROM audit.module_dependency_graph
                 WHERE graph_id = %s
                   AND status = 'active'
                 ORDER BY created_at DESC
                 LIMIT 1
            """
            params: tuple[str, ...] = (graph_ref,)
        else:
            query = """
                SELECT graph_payload
                  FROM audit.module_dependency_graph
                 WHERE status = 'active'
                 ORDER BY created_at DESC
                 LIMIT 1
            """
            params = ()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        if row is None:
            return DependencyGraph.default()
        return DependencyGraph.from_payload(row[0])

    def filter_active_instruments(self, universe_id: str, instrument_ids: tuple[str, ...]) -> tuple[str, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if instrument_ids:
                    cur.execute(
                        """
                        SELECT instrument_id
                          FROM registry.instrument_profile
                         WHERE universe_id = %s
                           AND is_active = true
                           AND instrument_id = ANY(%s)
                         ORDER BY instrument_id
                        """,
                        (universe_id, list(instrument_ids)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT instrument_id
                          FROM registry.instrument_profile
                         WHERE universe_id = %s
                           AND is_active = true
                         ORDER BY instrument_id
                        """,
                        (universe_id,),
                    )
                return tuple(row[0] for row in cur.fetchall())

    def save_module_job(self, job: ModuleJob) -> SaveJobResult:
        from psycopg.types.json import Jsonb

        insert_query = """
            INSERT INTO audit.module_job (
                job_id, module_name, contour, trigger_type, universe_id,
                instrument_ids, horizons, time_range, input_refs, config_ref,
                run_mode, idempotency_key, priority, status
            ) VALUES (
                %(job_id)s, %(module_name)s, %(contour)s, %(trigger_type)s, %(universe_id)s,
                %(instrument_ids)s, %(horizons)s, %(time_range)s, %(input_refs)s, %(config_ref)s,
                %(run_mode)s, %(idempotency_key)s, %(priority)s, %(status)s
            )
            ON CONFLICT (idempotency_key) DO NOTHING
        """
        select_query = """
            SELECT job_id, module_name, contour, trigger_type, universe_id,
                   instrument_ids, horizons, time_range, input_refs, config_ref,
                   run_mode, idempotency_key, priority, status
              FROM audit.module_job
             WHERE idempotency_key = %s
        """
        payload = job.to_dict()
        payload["time_range"] = Jsonb(payload["time_range"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_query, payload)
                created = cur.rowcount == 1
                if created:
                    return SaveJobResult(job, created=True)
                cur.execute(select_query, (job.idempotency_key,))
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("idempotency lookup failed after module_job insert")
        existing = ModuleJob.from_dict(
            {
                "job_id": row[0],
                "module_name": row[1],
                "contour": row[2],
                "trigger_type": row[3],
                "universe_id": row[4],
                "instrument_ids": row[5],
                "horizons": row[6],
                "time_range": row[7],
                "input_refs": row[8],
                "config_ref": row[9],
                "run_mode": row[10],
                "idempotency_key": row[11],
                "priority": row[12],
                "status": row[13],
            }
        )
        return SaveJobResult(existing, created=False)

    def save_module_run(self, job: ModuleJob, status: str, payload: dict[str, Any] | None = None) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.module_run (job_id, module_name, status, payload)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (job.job_id, job.module_name, status, Jsonb(payload or {})),
                )

    def save_orchestration_state(self, status: str, payload: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.module_run (job_id, module_name, status, payload)
                    VALUES (NULL, %s, %s, %s)
                    """,
                    ("Orchestration Module", status, Jsonb({"object_type": "orchestration_state", **payload})),
                )

    def save_module_job_result(self, result: ModuleJobResult) -> None:
        from psycopg.types.json import Jsonb

        payload = result.to_dict()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.module_job_result (
                        job_id, module_name, status, started_at, finished_at,
                        output_refs, warnings, errors, metrics_written,
                        events_written, data_quality_score, payload
                    ) VALUES (
                        %(job_id)s, %(module_name)s, %(status)s, %(started_at)s, %(finished_at)s,
                        %(output_refs)s, %(warnings)s, %(errors)s, %(metrics_written)s,
                        %(events_written)s, %(data_quality_score)s, %(payload)s
                    )
                    ON CONFLICT (job_id) DO UPDATE SET
                        module_name = EXCLUDED.module_name,
                        status = EXCLUDED.status,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at,
                        output_refs = EXCLUDED.output_refs,
                        warnings = EXCLUDED.warnings,
                        errors = EXCLUDED.errors,
                        metrics_written = EXCLUDED.metrics_written,
                        events_written = EXCLUDED.events_written,
                        data_quality_score = EXCLUDED.data_quality_score,
                        payload = EXCLUDED.payload
                    """,
                    {
                        **payload,
                        "payload": Jsonb(payload),
                    },
                )

    def write_audit_record(self, record: AuditRecord) -> str:
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
                        Jsonb(record.payload or {}),
                    ),
                )
                row = cur.fetchone()
        return str(row[0])
