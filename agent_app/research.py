from __future__ import annotations

from agent_app.main import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "--trigger-type",
                "manual",
                "--source",
                "Feature Store",
                "--payload-ref",
                "module:Feature Validation & Research Module",
                "--system-mode",
                "analysis_only",
            ]
        )
    )

