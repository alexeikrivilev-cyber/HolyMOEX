from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class RawCandle:
    raw_candle_id: str
    instrument_id: str
    universe_id: str
    timeframe: str
    open_ts: str
    close_ts: str | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    volume: float | None = None
    turnover: float | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawCandle":
        source_payload = payload.get("source_payload") or {}
        return cls(
            raw_candle_id=str(payload.get("raw_candle_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            timeframe=str(payload.get("timeframe") or ""),
            open_ts=str(payload.get("open_ts") or ""),
            close_ts=_optional_text(payload.get("close_ts")),
            open_price=_optional_float(payload.get("open_price")),
            high_price=_optional_float(payload.get("high_price")),
            low_price=_optional_float(payload.get("low_price")),
            close_price=_optional_float(payload.get("close_price")),
            volume=_optional_float(payload.get("volume")),
            turnover=_optional_float(payload.get("turnover")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )

    @property
    def adjusted_close_price(self) -> float | None:
        for key in (
            "adjusted_close_price",
            "adjusted_close",
            "adj_close",
            "corporate_action_adjusted_close",
        ):
            if key in self.source_payload:
                return _optional_float(self.source_payload.get(key))
        return self.close_price

    @property
    def has_adjusted_close_price(self) -> bool:
        for key in (
            "adjusted_close_price",
            "adjusted_close",
            "adj_close",
            "corporate_action_adjusted_close",
        ):
            if key in self.source_payload and _optional_float(self.source_payload.get(key)) is not None:
                return True
        return False

    @property
    def adjustment_source(self) -> str:
        for key in (
            "adjusted_close_price",
            "adjusted_close",
            "adj_close",
            "corporate_action_adjusted_close",
        ):
            if key in self.source_payload and _optional_float(self.source_payload.get(key)) is not None:
                return f"source_payload.{key}"
        return "close_price_as_adjusted_proxy"


@dataclass(frozen=True)
class RawIndexValue:
    raw_index_value_id: str
    index_id: str
    value_ts: str
    value: float | None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawIndexValue":
        source_payload = payload.get("source_payload") or {}
        return cls(
            raw_index_value_id=str(payload.get("raw_index_value_id") or payload.get("id") or ""),
            index_id=str(payload.get("index_id") or ""),
            value_ts=str(payload.get("value_ts") or ""),
            value=_optional_float(payload.get("value")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class RawMacroPoint:
    raw_macro_point_id: str
    series_id: str
    point_ts: str
    value: float | None
    unit: str | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawMacroPoint":
        source_payload = payload.get("source_payload") or {}
        return cls(
            raw_macro_point_id=str(payload.get("raw_macro_point_id") or payload.get("id") or ""),
            series_id=str(payload.get("series_id") or ""),
            point_ts=str(payload.get("point_ts") or payload.get("value_ts") or ""),
            value=_optional_float(payload.get("value")),
            unit=_optional_text(payload.get("unit")),
            provider=str(payload.get("provider") or ""),
            source_payload=source_payload if isinstance(source_payload, Mapping) else {},
            received_at=_optional_text(payload.get("received_at")),
        )


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float
    normalized_value: float | None
    unit: str
    horizon: str
    contour: str
    timestamp: str
    ttl_seconds: int
    confidence_score: float
    source_module: str
    source_refs: tuple[str, ...]
    calculation_version: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "instrument_id": self.instrument_id,
            "metric_name": self.metric_name,
            "metric_group": self.metric_group,
            "metric_type": self.metric_type,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "unit": self.unit,
            "horizon": self.horizon,
            "contour": self.contour,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "confidence_score": self.confidence_score,
            "source_module": self.source_module,
            "source_refs": list(self.source_refs),
            "calculation_version": self.calculation_version,
            "quality_flags": list(self.quality_flags),
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class RiskContextRecord:
    risk_context_record_id: str
    universe_id: str
    instrument_id: str
    as_of_ts: str
    payload: Mapping[str, Any]
    source_module: str
    calculation_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_context_record_id": self.risk_context_record_id,
            "universe_id": self.universe_id,
            "instrument_id": self.instrument_id,
            "as_of_ts": self.as_of_ts,
            "payload": dict(self.payload),
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
        }


class VolatilityRiskMetricsRepository(Protocol):
    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        ...

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        ...

    def list_macro_points(self, macro_refs: tuple[str, ...], from_ts: str, to_ts: str) -> tuple[RawMacroPoint, ...]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_risk_context_record(self, record: RiskContextRecord) -> str:
        ...


class InMemoryVolatilityRiskMetricsRepository:
    def __init__(
        self,
        candles: tuple[Mapping[str, Any] | RawCandle, ...] = (),
        index_values: Mapping[str, tuple[Mapping[str, Any] | RawIndexValue, ...]] | None = None,
        macro_points: Mapping[str, tuple[Mapping[str, Any] | RawMacroPoint, ...]] | None = None,
    ) -> None:
        self.candles = tuple(
            candle if isinstance(candle, RawCandle) else RawCandle.from_mapping(candle)
            for candle in candles
        )
        self.index_values = {
            ref: tuple(item if isinstance(item, RawIndexValue) else RawIndexValue.from_mapping(item) for item in values)
            for ref, values in dict(index_values or {}).items()
        }
        self.macro_points = {
            ref: tuple(item if isinstance(item, RawMacroPoint) else RawMacroPoint.from_mapping(item) for item in values)
            for ref, values in dict(macro_points or {}).items()
        }
        self.feature_records: list[FeatureRecord] = []
        self.risk_context_records: list[RiskContextRecord] = []

    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        requested = set(instrument_ids)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        candles = [
            candle
            for candle in self.candles
            if candle.universe_id == universe_id
            and candle.instrument_id in requested
            and candle.close_ts
            and from_dt <= parse_utc_iso(candle.close_ts) <= to_dt
        ]
        return tuple(sorted(candles, key=lambda candle: (candle.instrument_id, candle.timeframe, candle.close_ts or "")))

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        if not index_ref:
            return ()
        requested_key = _ref_tail(index_ref)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values = list(self.index_values.get(index_ref, ())) + list(self.index_values.get(requested_key, ()))
        if not values:
            for stored_values in self.index_values.values():
                values.extend(item for item in stored_values if item.index_id == requested_key)
        filtered = [
            item
            for item in values
            if item.value is not None and item.value_ts and from_dt <= parse_utc_iso(item.value_ts) <= to_dt
        ]
        return tuple(sorted(filtered, key=lambda item: item.value_ts))

    def list_macro_points(self, macro_refs: tuple[str, ...], from_ts: str, to_ts: str) -> tuple[RawMacroPoint, ...]:
        if not macro_refs:
            return ()
        requested = {_ref_tail(ref) for ref in macro_refs}
        explicit_values: list[RawMacroPoint] = []
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(to_ts)
        values: list[RawMacroPoint] = []
        for ref in macro_refs:
            explicit_values.extend(self.macro_points.get(ref, ()))
            explicit_values.extend(self.macro_points.get(_ref_tail(ref), ()))
        values.extend(explicit_values)
        if not values:
            for stored_values in self.macro_points.values():
                values.extend(item for item in stored_values if item.series_id in requested)
        filtered = [
            item
            for item in values
            if item.value is not None
            and (item in explicit_values or item.series_id in requested)
            and from_dt <= parse_utc_iso(item.point_ts) <= to_dt
        ]
        return tuple(sorted(filtered, key=lambda item: (item.series_id, item.point_ts)))

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_risk_context_record(self, record: RiskContextRecord) -> str:
        self.risk_context_records.append(record)
        return f"risk.risk_context_record:{record.risk_context_record_id}"


class PostgresVolatilityRiskMetricsRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_candles(
        self,
        candles_ref: str,
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawCandle, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_candle_id, instrument_id, universe_id, timeframe,
                           open_ts, close_ts, open_price, high_price, low_price,
                           close_price, volume, turnover, provider, source_payload,
                           received_at
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND close_ts IS NOT NULL
                       AND close_ts BETWEEN %s AND %s
                     ORDER BY instrument_id, timeframe, close_ts
                    """,
                    (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            RawCandle(
                raw_candle_id=str(row[0]),
                instrument_id=row[1] or "",
                universe_id=row[2] or "",
                timeframe=row[3] or "",
                open_ts=_iso(row[4]),
                close_ts=_iso(row[5]) if row[5] else None,
                open_price=_optional_float(row[6]),
                high_price=_optional_float(row[7]),
                low_price=_optional_float(row[8]),
                close_price=_optional_float(row[9]),
                volume=_optional_float(row[10]),
                turnover=_optional_float(row[11]),
                provider=row[12] or "",
                source_payload=row[13] or {},
                received_at=_iso(row[14]) if row[14] else None,
            )
            for row in rows
        )

    def list_index_values(self, index_ref: str, from_ts: str, to_ts: str) -> tuple[RawIndexValue, ...]:
        if not index_ref:
            return ()
        index_id = _ref_tail(index_ref)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_index_value_id, index_id, value_ts, value,
                           provider, source_payload, received_at
                      FROM raw_market.raw_index_value
                     WHERE (index_id = %s OR raw_index_value_id::text = %s)
                       AND value_ts BETWEEN %s AND %s
                     ORDER BY value_ts
                    """,
                    (index_id, index_id, parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            RawIndexValue(
                raw_index_value_id=str(row[0]),
                index_id=row[1] or "",
                value_ts=_iso(row[2]),
                value=_optional_float(row[3]),
                provider=row[4] or "",
                source_payload=row[5] or {},
                received_at=_iso(row[6]) if row[6] else None,
            )
            for row in rows
        )

    def list_macro_points(self, macro_refs: tuple[str, ...], from_ts: str, to_ts: str) -> tuple[RawMacroPoint, ...]:
        if not macro_refs:
            return ()
        series_ids = [_ref_tail(ref) for ref in macro_refs]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_macro_point_id, series_id, point_ts, value, unit,
                           provider, source_payload, received_at
                      FROM raw_macro.raw_macro_point
                     WHERE series_id = ANY(%s)
                       AND point_ts BETWEEN %s AND %s
                     ORDER BY series_id, point_ts
                    """,
                    (series_ids, parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            RawMacroPoint(
                raw_macro_point_id=str(row[0]),
                series_id=row[1] or "",
                point_ts=_iso(row[2]),
                value=_optional_float(row[3]),
                unit=_optional_text(row[4]),
                provider=row[5] or "",
                source_payload=row[6] or {},
                received_at=_iso(row[7]) if row[7] else None,
            )
            for row in rows
        )

    def save_feature_record(self, record: FeatureRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.feature_record (
                        feature_id, instrument_id, metric_name, metric_group,
                        metric_type, raw_value, normalized_value, unit, horizon,
                        contour, timestamp, ttl_seconds, confidence_score,
                        source_module, source_refs, calculation_version,
                        quality_flags, payload
                    ) VALUES (
                        %(feature_id)s, %(instrument_id)s, %(metric_name)s,
                        %(metric_group)s, %(metric_type)s, %(raw_value)s,
                        %(normalized_value)s, %(unit)s, %(horizon)s,
                        %(contour)s, %(timestamp)s, %(ttl_seconds)s,
                        %(confidence_score)s, %(source_module)s, %(source_refs)s,
                        %(calculation_version)s, %(quality_flags)s, %(payload)s
                    )
                    ON CONFLICT (feature_id) DO UPDATE SET
                        raw_value = EXCLUDED.raw_value,
                        normalized_value = EXCLUDED.normalized_value,
                        timestamp = EXCLUDED.timestamp,
                        ttl_seconds = EXCLUDED.ttl_seconds,
                        confidence_score = EXCLUDED.confidence_score,
                        source_refs = EXCLUDED.source_refs,
                        calculation_version = EXCLUDED.calculation_version,
                        quality_flags = EXCLUDED.quality_flags,
                        payload = EXCLUDED.payload
                    """,
                    {
                        **record.to_dict(),
                        "timestamp": parse_utc_iso(record.timestamp),
                        "payload": Jsonb(dict(record.payload)),
                    },
                )
        return f"features.feature_record:{record.feature_id}"

    def save_risk_context_record(self, record: RiskContextRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO risk.risk_context_record (
                        universe_id, instrument_id, as_of_ts, payload,
                        source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING risk_context_record_id
                    """,
                    (
                        record.universe_id,
                        record.instrument_id,
                        parse_utc_iso(record.as_of_ts),
                        Jsonb(dict(record.payload)),
                        record.source_module,
                        record.calculation_version,
                    ),
                )
                row = cur.fetchone()
        return f"risk.risk_context_record:{row[0]}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
