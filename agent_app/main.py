from __future__ import annotations

import argparse
import json
import os
from typing import Sequence

from agent_app.contracts.unified_objects import TimeRange
from agent_app.modules.orchestration.repository import (
    InMemoryOrchestrationRepository,
    PostgresOrchestrationRepository,
)
from agent_app.modules.orchestration.service import (
    IncomingTrigger,
    OrchestrationInput,
    OrchestrationService,
    PipelineContext,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MOEX AI trading agent runtime entrypoint")
    parser.add_argument("--source", default="Raw Market Data Store")
    parser.add_argument("--payload-ref", default="")
    parser.add_argument("--trigger-type", default="manual")
    parser.add_argument("--priority", default="normal")
    parser.add_argument("--system-mode", default=os.getenv("RUN_MODE", "paper_trading"))
    parser.add_argument("--universe-id", default=os.getenv("SELECTED_UNIVERSE_ID", "moex_top20_manual"))
    parser.add_argument("--instrument-id", action="append", default=[])
    parser.add_argument("--horizon", action="append", default=[])
    parser.add_argument("--use-postgres", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv("DATABASE_URL", "")
    repository = (
        PostgresOrchestrationRepository(database_url)
        if args.use_postgres and database_url
        else InMemoryOrchestrationRepository()
    )
    service = OrchestrationService(repository)
    request = OrchestrationInput(
        schedule_config_ref=None,
        dependency_graph_ref=None,
        incoming_trigger=IncomingTrigger(
            trigger_type=args.trigger_type,
            source_module=args.source,
            payload_ref=args.payload_ref,
            priority=args.priority,
        ),
        system_mode=args.system_mode,
    )
    context = PipelineContext(
        universe_id=args.universe_id,
        instrument_ids=tuple(args.instrument_id),
        horizons=tuple(args.horizon) if args.horizon else ("intraday", "swing", "position"),
        time_range=TimeRange.instant(),
        system_mode=args.system_mode,
    )
    pipeline_run = service.run_pipeline(request, context)
    print(json.dumps(pipeline_run.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

