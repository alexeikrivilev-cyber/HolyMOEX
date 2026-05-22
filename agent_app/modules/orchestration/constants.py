from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleSpec:
    module_name: str
    module_type: str
    primary_contour: str
    default_horizons: tuple[str, ...] = ("intraday", "swing", "position")
    service_only: bool = False


MODULE_SPECS: dict[str, ModuleSpec] = {
    "Orchestration Module": ModuleSpec(
        "Orchestration Module", "service", "service_contour", service_only=True
    ),
    "External Request Gateway Module": ModuleSpec(
        "External Request Gateway Module", "service", "service_contour", service_only=True
    ),
    "Selected Instruments Registry Module": ModuleSpec(
        "Selected Instruments Registry Module", "service/storage", "service_contour", service_only=True
    ),
    "Data Intake & Routing Module": ModuleSpec(
        "Data Intake & Routing Module", "service", "intraday_contour", ("intraday", "swing")
    ),
    "Data Quality Module": ModuleSpec(
        "Data Quality Module", "service", "service_contour", service_only=True
    ),
    "Market Data Metrics Module": ModuleSpec(
        "Market Data Metrics Module", "analytical", "realtime_contour", ("intraday", "swing")
    ),
    "Liquidity & Microstructure Module": ModuleSpec(
        "Liquidity & Microstructure Module", "analytical", "realtime_contour", ("intraday",)
    ),
    "Volatility & Risk Metrics Module": ModuleSpec(
        "Volatility & Risk Metrics Module", "analytical", "realtime_contour", ("intraday", "swing")
    ),
    "Market Context Module": ModuleSpec(
        "Market Context Module", "analytical", "global_contour", ("intraday", "swing", "position")
    ),
    "Fundamental & Valuation Module": ModuleSpec(
        "Fundamental & Valuation Module", "analytical", "daily_contour", ("swing", "position")
    ),
    "Event & News Intelligence Module": ModuleSpec(
        "Event & News Intelligence Module", "hybrid_llm", "event_contour", ("intraday", "swing")
    ),
    "Earnings & Dividend Intelligence Module": ModuleSpec(
        "Earnings & Dividend Intelligence Module", "hybrid_llm", "event_contour", ("swing", "position")
    ),
    "Corporate Actions Adjustment Module": ModuleSpec(
        "Corporate Actions Adjustment Module", "analytical", "event_contour", ("swing", "position")
    ),
    "Derivatives & Positioning Module": ModuleSpec(
        "Derivatives & Positioning Module", "analytical", "intraday_contour", ("intraday", "swing")
    ),
    "Normalization & Feature Vector Module": ModuleSpec(
        "Normalization & Feature Vector Module", "analytical/service", "decision_contour"
    ),
    "Feature Validation & Research Module": ModuleSpec(
        "Feature Validation & Research Module", "research", "research_contour"
    ),
    "Decision Engine Module": ModuleSpec(
        "Decision Engine Module", "decision", "decision_contour"
    ),
    "Risk Control Module": ModuleSpec(
        "Risk Control Module", "risk", "decision_contour"
    ),
    "Execution Engine Module": ModuleSpec(
        "Execution Engine Module", "execution", "execution_contour", ("intraday", "swing")
    ),
    "Portfolio State Module": ModuleSpec(
        "Portfolio State Module", "service", "execution_contour", service_only=True
    ),
    "Backtesting & Paper Trading Module": ModuleSpec(
        "Backtesting & Paper Trading Module", "research/execution_sim", "research_contour"
    ),
    "Monitoring & Audit Module": ModuleSpec(
        "Monitoring & Audit Module", "service", "monitoring_contour", service_only=True
    ),
}


# This graph includes the minimal README dependency graph and the documented
# execution path that routes feature updates through quality, normalization,
# decision, risk, execution, portfolio state, and monitoring.
DEFAULT_DEPENDENCY_EDGES: tuple[dict[str, object], ...] = (
    {"source": "Selected Instruments DB", "target": "Data Intake & Routing Module", "critical": True},
    {"source": "Selected Instruments DB", "target": "Event & News Intelligence Module", "critical": False},
    {"source": "Selected Instruments DB", "target": "Earnings & Dividend Intelligence Module", "critical": False},
    {"source": "Selected Instruments DB", "target": "Corporate Actions Adjustment Module", "critical": True},
    {"source": "Raw Market Data Store", "target": "Data Quality Module", "critical": True},
    {"source": "Raw Market Data Store", "target": "Market Data Metrics Module", "critical": True},
    {"source": "Raw Market Data Store", "target": "Liquidity & Microstructure Module", "critical": True},
    {"source": "Raw Market Data Store", "target": "Volatility & Risk Metrics Module", "critical": True},
    {"source": "Raw Market Data Store", "target": "Derivatives & Positioning Module", "critical": False},
    {"source": "Raw Macro Data Store", "target": "Market Context Module", "critical": True},
    {"source": "Raw Text Store", "target": "Data Intake & Routing Module", "critical": False},
    {"source": "Raw Text Store", "target": "Event & News Intelligence Module", "critical": False},
    {"source": "Raw Text Store", "target": "Earnings & Dividend Intelligence Module", "critical": False},
    {"source": "Raw Text Store", "target": "Fundamental & Valuation Module", "critical": False},
    {"source": "Event Store", "target": "Event & News Intelligence Module", "critical": False},
    {"source": "Event Store", "target": "Earnings & Dividend Intelligence Module", "critical": False},
    {"source": "Event Store", "target": "Market Context Module", "critical": False},
    {"source": "Event Store", "target": "Corporate Actions Adjustment Module", "critical": True},
    {"source": "Feature Store", "target": "Data Quality Module", "critical": True},
    {"source": "Feature Store", "target": "Normalization & Feature Vector Module", "critical": True},
    {"source": "Feature Store", "target": "Decision Engine Module", "critical": True},
    {"source": "Feature Store", "target": "Risk Control Module", "critical": True},
    {"source": "Feature Store", "target": "Execution Engine Module", "critical": True},
    {"source": "Feature Store", "target": "Portfolio State Module", "critical": False},
    {"source": "Data Quality Module", "target": "Normalization & Feature Vector Module", "critical": True},
    {"source": "Normalization & Feature Vector Module", "target": "Decision Engine Module", "critical": True},
    {"source": "Decision Engine Module", "target": "Risk Control Module", "critical": True},
    {"source": "Risk Control Module", "target": "Execution Engine Module", "critical": True},
    {"source": "Execution Engine Module", "target": "Portfolio State Module", "critical": False},
    {"source": "Portfolio State Module", "target": "Monitoring & Audit Module", "critical": False},
    {"source": "Request Log Store", "target": "Monitoring & Audit Module", "critical": False},
    {"source": "Audit Log Store", "target": "Monitoring & Audit Module", "critical": False},
    {"source": "Order Store", "target": "Portfolio State Module", "critical": True},
    {"source": "Order Store", "target": "Monitoring & Audit Module", "critical": False},
)

FULL_RECALC_ALLOWED_CONTOURS = {"research_contour"}
FULL_RECALC_ALLOWED_RUN_MODES = {"backtest", "replay"}
