from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


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
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataQualityRecord:
    object_type: str
    object_ref: str
    quality_score: float
    quality_flags: tuple[str, ...]
    checked_at: str
    source_module: str
    calculation_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type,
            "object_ref": self.object_ref,
            "quality_score": self.quality_score,
            "quality_flags": list(self.quality_flags),
            "checked_at": self.checked_at,
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "payload": dict(self.payload),
        }


class DataQualityRepository(Protocol):
    def load_quality_object(self, ref: str) -> Mapping[str, Any] | None:
        ...

    def save_data_quality_record(self, record: DataQualityRecord) -> str:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemoryDataQualityRepository:
    def __init__(self, objects: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.objects_by_ref: dict[str, Mapping[str, Any]] = dict(objects or {})
        self.data_quality_records: list[DataQualityRecord] = []
        self.data_quality_refs: dict[str, DataQualityRecord] = {}
        self.audit_records: list[AuditRecord] = []

    def add_quality_object(self, ref: str, payload: Mapping[str, Any]) -> None:
        self.objects_by_ref[ref] = payload

    def load_quality_object(self, ref: str) -> Mapping[str, Any] | None:
        return self.objects_by_ref.get(ref)

    def save_data_quality_record(self, record: DataQualityRecord) -> str:
        self.data_quality_records.append(record)
        ref = f"features.data_quality_record:{len(self.data_quality_records)}"
        self.data_quality_refs[ref] = record
        return ref

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"


class PostgresDataQualityRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def load_quality_object(self, ref: str) -> Mapping[str, Any] | None:
        table, object_id = _parse_ref(ref)
        if _is_placeholder_ref(object_id):
            return None
        if table.startswith(("raw_market.", "raw_text.")) and not _is_uuid_text(object_id):
            return None
        if table == "raw_market.raw_candle":
            return self._fetch_one(
                """
                SELECT raw_candle_id, instrument_id, universe_id, board_id, timeframe,
                       open_ts, close_ts, open_price, high_price, low_price,
                       close_price, volume, turnover, provider, source_payload,
                       received_at
                  FROM raw_market.raw_candle
                 WHERE raw_candle_id = %s
                """,
                object_id,
                (
                    "raw_candle_id",
                    "instrument_id",
                    "universe_id",
                    "board_id",
                    "timeframe",
                    "open_ts",
                    "close_ts",
                    "open_price",
                    "high_price",
                    "low_price",
                    "close_price",
                    "volume",
                    "turnover",
                    "provider",
                    "source_payload",
                    "received_at",
                ),
            )
        if table == "raw_market.raw_trade":
            return self._fetch_one(
                """
                SELECT raw_trade_id, instrument_id, universe_id, trade_ts, price,
                       quantity, side, trade_value, provider, provider_trade_id,
                       source_payload, received_at
                  FROM raw_market.raw_trade
                 WHERE raw_trade_id = %s
                """,
                object_id,
                (
                    "raw_trade_id",
                    "instrument_id",
                    "universe_id",
                    "trade_ts",
                    "price",
                    "quantity",
                    "side",
                    "trade_value",
                    "provider",
                    "provider_trade_id",
                    "source_payload",
                    "received_at",
                ),
            )
        if table == "raw_market.raw_orderbook":
            return self._fetch_one(
                """
                SELECT raw_orderbook_id, instrument_id, universe_id, snapshot_ts,
                       bids, asks, provider, source_payload, received_at
                  FROM raw_market.raw_orderbook
                 WHERE raw_orderbook_id = %s
                """,
                object_id,
                (
                    "raw_orderbook_id",
                    "instrument_id",
                    "universe_id",
                    "snapshot_ts",
                    "bids",
                    "asks",
                    "provider",
                    "source_payload",
                    "received_at",
                ),
            )
        if table == "raw_market.raw_index_value":
            return self._fetch_one(
                """
                SELECT raw_index_value_id, index_id, value_ts, value, provider,
                       source_payload, received_at
                  FROM raw_market.raw_index_value
                 WHERE raw_index_value_id = %s
                """,
                object_id,
                (
                    "raw_index_value_id",
                    "index_id",
                    "value_ts",
                    "value",
                    "provider",
                    "source_payload",
                    "received_at",
                ),
            )
        if table == "raw_text.raw_text_item":
            return self._fetch_one(
                """
                SELECT raw_text_item_id, universe_id, instrument_ids, source,
                       source_url, title, body, language, published_at, fetched_at,
                       content_hash, source_payload
                  FROM raw_text.raw_text_item
                 WHERE raw_text_item_id = %s
                """,
                object_id,
                (
                    "raw_text_item_id",
                    "universe_id",
                    "instrument_ids",
                    "source",
                    "source_url",
                    "title",
                    "body",
                    "language",
                    "published_at",
                    "fetched_at",
                    "content_hash",
                    "source_payload",
                ),
            )
        if table == "features.feature_record":
            return self._fetch_one(
                """
                SELECT feature_id, instrument_id, metric_name, metric_group,
                       metric_type, raw_value, normalized_value, unit, horizon,
                       contour, timestamp, ttl_seconds, confidence_score,
                       source_module, source_refs, calculation_version,
                       quality_flags, payload
                  FROM features.feature_record
                 WHERE feature_id = %s
                """,
                object_id,
                (
                    "feature_id",
                    "instrument_id",
                    "metric_name",
                    "metric_group",
                    "metric_type",
                    "raw_value",
                    "normalized_value",
                    "unit",
                    "horizon",
                    "contour",
                    "timestamp",
                    "ttl_seconds",
                    "confidence_score",
                    "source_module",
                    "source_refs",
                    "calculation_version",
                    "quality_flags",
                    "payload",
                ),
            )
        if table == "features.feature_vector":
            return self._fetch_one(
                """
                SELECT feature_vector_id, instrument_id, horizon, as_of_ts,
                       features, coverage_ratio, data_quality_score,
                       build_version, created_at
                  FROM features.feature_vector
                 WHERE feature_vector_id = %s
                """,
                object_id,
                (
                    "feature_vector_id",
                    "instrument_id",
                    "horizon",
                    "as_of_ts",
                    "features",
                    "coverage_ratio",
                    "data_quality_score",
                    "build_version",
                    "created_at",
                ),
            )
        if table == "portfolio.portfolio_snapshot":
            return self._fetch_one(
                """
                SELECT portfolio_snapshot_id, portfolio_id, universe_id, as_of_ts,
                       initial_capital_rub, cash, equity, gross_exposure,
                       net_exposure, realized_pnl, unrealized_pnl, source_module,
                       source_refs, payload, created_at
                  FROM portfolio.portfolio_snapshot
                 WHERE portfolio_snapshot_id = %s
                """,
                object_id,
                (
                    "portfolio_snapshot_id",
                    "portfolio_id",
                    "universe_id",
                    "as_of_ts",
                    "initial_capital_rub",
                    "cash",
                    "equity",
                    "gross_exposure",
                    "net_exposure",
                    "realized_pnl",
                    "unrealized_pnl",
                    "source_module",
                    "source_refs",
                    "payload",
                    "created_at",
                ),
            )
        if table == "portfolio.position_state":
            return self._fetch_one(
                """
                SELECT position_state_id, portfolio_id, instrument_id, as_of_ts,
                       quantity, average_price, market_price, market_value,
                       unrealized_pnl, source_module, source_refs, payload
                  FROM portfolio.position_state
                 WHERE position_state_id = %s
                """,
                object_id,
                (
                    "position_state_id",
                    "portfolio_id",
                    "instrument_id",
                    "as_of_ts",
                    "quantity",
                    "average_price",
                    "market_price",
                    "market_value",
                    "unrealized_pnl",
                    "source_module",
                    "source_refs",
                    "payload",
                ),
            )
        return None

    def save_data_quality_record(self, record: DataQualityRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.data_quality_record (
                        object_type, object_ref, quality_score, quality_flags,
                        checked_at, source_module, calculation_version, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING data_quality_record_id
                    """,
                    (
                        record.object_type,
                        record.object_ref,
                        record.quality_score,
                        list(record.quality_flags),
                        parse_utc_iso(record.checked_at),
                        record.source_module,
                        record.calculation_version,
                        Jsonb(dict(record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"features.data_quality_record:{row[0]}"

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
                        Jsonb(dict(record.payload)),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"

    def _fetch_one(
        self,
        query: str,
        object_id: str,
        columns: tuple[str, ...],
    ) -> Mapping[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (object_id,))
                row = cur.fetchone()
        if row is None:
            return None
        return {
            column: _serialize_value(value)
            for column, value in zip(columns, row, strict=True)
        }


def _parse_ref(ref: str) -> tuple[str, str]:
    if ":" not in ref:
        return "", ref
    table, object_id = ref.split(":", 1)
    return table, object_id


def _is_placeholder_ref(value: str) -> bool:
    return value in {"scheduled", "latest", "peer_group", "expectations", "dividend_gap_history", ""}


def _is_uuid_text(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _serialize_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    if hasattr(value, "__float__") and not isinstance(value, (str, bytes, bool)):
        return float(value)
    if isinstance(value, tuple):
        return list(value)
    return value
