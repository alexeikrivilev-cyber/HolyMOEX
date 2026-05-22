from __future__ import annotations

from agent_app.main import main


if __name__ == "__main__":
    raise SystemExit(main(["--trigger-type", "scheduled", "--source", "Raw Market Data Store"]))

