from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from agent_app.contracts.unified_objects.module_job import parse_utc_iso


@dataclass(frozen=True)
class InstrumentProfile:
    instrument_id: str
    universe_id: str
    ticker: str
    sector: str | None = None
    is_active: bool = True
    tradable: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfile":
        metadata = payload.get("metadata") or payload.get("payload") or {}
        if not isinstance(metadata, Mapping):
            metadata = {}
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or payload.get("secid") or ""),
            sector=_optional_text(payload.get("sector")),
            is_active=_optional_bool(payload.get("is_active"), default=True),
            tradable=_optional_bool(payload.get("tradable"), default=True),
            metadata=metadata,
        )

    @property
    def derivatives_enabled(self) -> bool:
        return _optional_bool(self.metadata.get("derivatives_enabled"), default=False)

    @property
    def is_index_level_context(self) -> bool:
        if _optional_bool(self.metadata.get("index_level_context"), default=False):
            return True
        if _optional_bool(self.metadata.get("is_index"), default=False):
            return True
        return self.instrument_id.startswith("index:") or self.ticker.upper().startswith("IMOEX")


@dataclass(frozen=True)
class RawFuturesPoint:
    raw_id: str
    instrument_id: str
    derivative_id: str
    timestamp: str
    price: float | None
    volume: float | None
    turnover: float | None
    open_interest: float | None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawFuturesPoint":
        source_payload = _source_payload(payload)
        instrument_id = _first_text(
            payload,
            source_payload,
            "underlying_instrument_id",
            "underlying_id",
            "base_instrument_id",
            "instrument_id",
        )
        derivative_id = _first_text(
            payload,
            source_payload,
            "derivative_id",
            "futures_id",
            "contract_id",
            "secid",
            "instrument_id",
        )
        return cls(
            raw_id=_first_text(payload, source_payload, "raw_id", "raw_candle_id", "id"),
            instrument_id=instrument_id,
            derivative_id=derivative_id,
            timestamp=_first_text(payload, source_payload, "timestamp", "close_ts", "open_ts", "trade_ts", "as_of_ts"),
            price=_first_float(payload, source_payload, "price", "close_price", "settlement_price", "last_price"),
            volume=_first_float(payload, source_payload, "volume", "derivative_volume"),
            turnover=_first_float(payload, source_payload, "turnover", "derivative_turnover"),
            open_interest=_first_float(payload, source_payload, "open_interest", "oi"),
            provider=_first_text(payload, source_payload, "provider"),
            source_payload=source_payload,
            received_at=_optional_text(payload.get("received_at") or source_payload.get("received_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "instrument_id": self.instrument_id,
            "derivative_id": self.derivative_id,
            "timestamp": self.timestamp,
            "price": self.price,
            "volume": self.volume,
            "turnover": self.turnover,
            "open_interest": self.open_interest,
            "provider": self.provider,
            "source_payload": dict(self.source_payload),
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class RawOptionPoint:
    raw_id: str
    instrument_id: str
    option_id: str
    option_type: str
    timestamp: str
    price: float | None
    volume: float | None
    turnover: float | None
    open_interest: float | None
    implied_volatility: float | None
    delta: float | None
    strike_price: float | None = None
    expiry_date: str | None = None
    risk_free_rate: float | None = None
    dividend_yield: float | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "RawOptionPoint":
        source_payload = _source_payload(payload)
        option_type = _first_text(payload, source_payload, "option_type", "put_call", "side").lower()
        if option_type in {"p", "put_option"}:
            option_type = "put"
        elif option_type in {"c", "call_option"}:
            option_type = "call"
        return cls(
            raw_id=_first_text(payload, source_payload, "raw_id", "raw_candle_id", "id"),
            instrument_id=_first_text(
                payload,
                source_payload,
                "underlying_instrument_id",
                "underlying_id",
                "base_instrument_id",
                "instrument_id",
            ),
            option_id=_first_text(payload, source_payload, "option_id", "contract_id", "secid", "instrument_id"),
            option_type=option_type,
            timestamp=_first_text(payload, source_payload, "timestamp", "close_ts", "open_ts", "trade_ts", "as_of_ts"),
            price=_first_float(payload, source_payload, "price", "close_price", "settlement_price", "last_price", "option_price"),
            volume=_first_float(payload, source_payload, "volume", "option_volume"),
            turnover=_first_float(payload, source_payload, "turnover", "option_turnover"),
            open_interest=_first_float(payload, source_payload, "open_interest", "oi"),
            implied_volatility=_first_float(payload, source_payload, "implied_volatility", "iv"),
            delta=_first_float(payload, source_payload, "delta"),
            strike_price=_first_float(payload, source_payload, "strike_price", "strike"),
            expiry_date=_optional_text(source_payload.get("expiry_date") or payload.get("expiry_date")),
            risk_free_rate=_first_float(payload, source_payload, "risk_free_rate"),
            dividend_yield=_first_float(payload, source_payload, "dividend_yield"),
            provider=_first_text(payload, source_payload, "provider"),
            source_payload=source_payload,
            received_at=_optional_text(payload.get("received_at") or source_payload.get("received_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "instrument_id": self.instrument_id,
            "option_id": self.option_id,
            "option_type": self.option_type,
            "timestamp": self.timestamp,
            "price": self.price,
            "volume": self.volume,
            "turnover": self.turnover,
            "open_interest": self.open_interest,
            "implied_volatility": self.implied_volatility,
            "delta": self.delta,
            "strike_price": self.strike_price,
            "expiry_date": self.expiry_date,
            "risk_free_rate": self.risk_free_rate,
            "dividend_yield": self.dividend_yield,
            "provider": self.provider,
            "source_payload": dict(self.source_payload),
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class SpotMarketPoint:
    raw_id: str
    instrument_id: str
    timestamp: str
    price: float | None
    volume: float | None = None
    turnover: float | None = None
    provider: str = ""
    source_payload: Mapping[str, Any] = field(default_factory=dict)
    received_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SpotMarketPoint":
        source_payload = _source_payload(payload)
        return cls(
            raw_id=_first_text(payload, source_payload, "raw_id", "raw_candle_id", "id"),
            instrument_id=_first_text(payload, source_payload, "instrument_id"),
            timestamp=_first_text(payload, source_payload, "timestamp", "close_ts", "open_ts", "trade_ts", "as_of_ts"),
            price=_first_float(payload, source_payload, "price", "close_price", "last_price"),
            volume=_first_float(payload, source_payload, "volume"),
            turnover=_first_float(payload, source_payload, "turnover"),
            provider=_first_text(payload, source_payload, "provider"),
            source_payload=source_payload,
            received_at=_optional_text(payload.get("received_at") or source_payload.get("received_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "instrument_id": self.instrument_id,
            "timestamp": self.timestamp,
            "price": self.price,
            "volume": self.volume,
            "turnover": self.turnover,
            "provider": self.provider,
            "source_payload": dict(self.source_payload),
            "received_at": self.received_at,
        }


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
class DerivativesAvailabilityRecord:
    availability_id: str
    instrument_id: str
    derivatives_enabled: bool
    skip_reason: str
    as_of_ts: str
    has_liquid_futures: bool
    has_liquid_options: bool
    source_module: str
    calculation_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "derivatives_availability_record": {
                "instrument_id": self.instrument_id,
                "derivatives_enabled": self.derivatives_enabled,
                "skip_reason": self.skip_reason,
                "as_of_ts": self.as_of_ts,
            },
            "availability_id": self.availability_id,
            "instrument_id": self.instrument_id,
            "derivatives_enabled": self.derivatives_enabled,
            "skip_reason": self.skip_reason,
            "as_of_ts": self.as_of_ts,
            "has_liquid_futures": self.has_liquid_futures,
            "has_liquid_options": self.has_liquid_options,
            "source_module": self.source_module,
            "calculation_version": self.calculation_version,
            "payload": dict(self.payload),
        }


class DerivativesPositioningRepository(Protocol):
    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def list_futures_data(
        self,
        futures_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawFuturesPoint, ...]:
        ...

    def list_options_data(
        self,
        options_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOptionPoint, ...]:
        ...

    def list_spot_market_data(
        self,
        spot_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[SpotMarketPoint, ...]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_derivatives_availability_record(self, record: DerivativesAvailabilityRecord) -> str:
        ...


class InMemoryDerivativesPositioningRepository:
    def __init__(
        self,
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
        futures: tuple[Mapping[str, Any] | RawFuturesPoint, ...] = (),
        options: tuple[Mapping[str, Any] | RawOptionPoint, ...] = (),
        spots: tuple[Mapping[str, Any] | SpotMarketPoint, ...] = (),
        spot_points: tuple[Mapping[str, Any] | SpotMarketPoint, ...] = (),
    ) -> None:
        self.profiles = tuple(
            profile if isinstance(profile, InstrumentProfile) else InstrumentProfile.from_mapping(profile)
            for profile in profiles
        )
        self.futures = tuple(
            point if isinstance(point, RawFuturesPoint) else RawFuturesPoint.from_mapping(point)
            for point in futures
        )
        self.options = tuple(
            point if isinstance(point, RawOptionPoint) else RawOptionPoint.from_mapping(point)
            for point in options
        )
        self.spots = tuple(
            point if isinstance(point, SpotMarketPoint) else SpotMarketPoint.from_mapping(point)
            for point in (*spots, *spot_points)
        )
        self.feature_records: list[FeatureRecord] = []
        self.availability_records: list[DerivativesAvailabilityRecord] = []

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        requested = set(instrument_ids)
        profiles = [
            profile
            for profile in self.profiles
            if profile.universe_id == universe_id and (not requested or profile.instrument_id in requested)
        ]
        return tuple(sorted(profiles, key=lambda profile: profile.instrument_id))

    def list_futures_data(
        self,
        futures_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawFuturesPoint, ...]:
        del universe_id
        requested = set(instrument_ids)
        requested_refs = {_ref_tail(ref) for ref in futures_refs}
        points = [
            point
            for point in self.futures
            if point.instrument_id in requested
            and _matches_ref(point.raw_id, point.derivative_id, requested_refs)
            and _timestamp_in_range(point.timestamp, from_ts, to_ts)
        ]
        return tuple(sorted(points, key=lambda point: (point.instrument_id, point.timestamp, point.derivative_id)))

    def list_options_data(
        self,
        options_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOptionPoint, ...]:
        del universe_id
        requested = set(instrument_ids)
        requested_refs = {_ref_tail(ref) for ref in options_refs}
        points = [
            point
            for point in self.options
            if point.instrument_id in requested
            and _matches_ref(point.raw_id, point.option_id, requested_refs)
            and _timestamp_in_range(point.timestamp, from_ts, to_ts)
        ]
        return tuple(sorted(points, key=lambda point: (point.instrument_id, point.timestamp, point.option_id)))

    def list_spot_market_data(
        self,
        spot_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[SpotMarketPoint, ...]:
        del universe_id
        requested = set(instrument_ids)
        requested_refs = {_ref_tail(ref) for ref in spot_refs}
        points = [
            point
            for point in self.spots
            if point.instrument_id in requested
            and _matches_ref(point.raw_id, point.instrument_id, requested_refs)
            and _timestamp_in_range(point.timestamp, from_ts, to_ts)
        ]
        return tuple(sorted(points, key=lambda point: (point.instrument_id, point.timestamp)))

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_derivatives_availability_record(self, record: DerivativesAvailabilityRecord) -> str:
        self.availability_records.append(record)
        return f"raw_market.derivatives_availability:{record.availability_id}"


class PostgresDerivativesPositioningRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, sector, is_active,
                           tradable, metadata
                      FROM registry.instrument_profile
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                     ORDER BY instrument_id
                    """,
                    (universe_id, list(instrument_ids)),
                )
                rows = cur.fetchall()
        return tuple(
            InstrumentProfile(
                instrument_id=row[0] or "",
                universe_id=row[1] or "",
                ticker=row[2] or "",
                sector=row[3],
                is_active=bool(row[4]),
                tradable=bool(row[5]),
                metadata=row[6] or {},
            )
            for row in rows
        )

    def list_futures_data(
        self,
        futures_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawFuturesPoint, ...]:
        rows = self._list_derivative_candles(
            refs=futures_refs,
            universe_id=universe_id,
            instrument_ids=instrument_ids,
            from_ts=from_ts,
            to_ts=to_ts,
            derivative_filter="futures",
        )
        return tuple(RawFuturesPoint.from_mapping(row) for row in rows)

    def list_options_data(
        self,
        options_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[RawOptionPoint, ...]:
        rows = self._list_derivative_candles(
            refs=options_refs,
            universe_id=universe_id,
            instrument_ids=instrument_ids,
            from_ts=from_ts,
            to_ts=to_ts,
            derivative_filter="options",
        )
        return tuple(RawOptionPoint.from_mapping(row) for row in rows)

    def list_spot_market_data(
        self,
        spot_refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
    ) -> tuple[SpotMarketPoint, ...]:
        del spot_refs
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT raw_candle_id, instrument_id, close_ts, open_ts,
                           close_price, volume, turnover, provider, source_payload,
                           received_at
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND instrument_id = ANY(%s)
                       AND close_price IS NOT NULL
                       AND COALESCE(close_ts, open_ts) BETWEEN %s AND %s
                     ORDER BY instrument_id, COALESCE(close_ts, open_ts)
                    """,
                    (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        return tuple(
            SpotMarketPoint(
                raw_id=str(row[0]),
                instrument_id=row[1] or "",
                timestamp=_iso(row[2]) if row[2] else _iso(row[3]),
                price=_optional_float(row[4]),
                volume=_optional_float(row[5]),
                turnover=_optional_float(row[6]),
                provider=row[7] or "",
                source_payload=row[8] or {},
                received_at=_iso(row[9]) if row[9] else None,
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

    def save_derivatives_availability_record(self, record: DerivativesAvailabilityRecord) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_market.derivatives_availability (
                        derivatives_availability_id, instrument_id, as_of_ts,
                        has_liquid_futures, has_liquid_options, payload,
                        source_module, calculation_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (derivatives_availability_id) DO UPDATE SET
                        as_of_ts = EXCLUDED.as_of_ts,
                        has_liquid_futures = EXCLUDED.has_liquid_futures,
                        has_liquid_options = EXCLUDED.has_liquid_options,
                        payload = EXCLUDED.payload,
                        source_module = EXCLUDED.source_module,
                        calculation_version = EXCLUDED.calculation_version
                    """,
                    (
                        uuid.UUID(record.availability_id),
                        record.instrument_id,
                        parse_utc_iso(record.as_of_ts),
                        record.has_liquid_futures,
                        record.has_liquid_options,
                        Jsonb(
                            {
                                **dict(record.payload),
                                "derivatives_enabled": record.derivatives_enabled,
                                "skip_reason": record.skip_reason,
                            }
                        ),
                        record.source_module,
                        record.calculation_version,
                    ),
                )
        return f"raw_market.derivatives_availability:{record.availability_id}"

    def _list_derivative_candles(
        self,
        *,
        refs: tuple[str, ...],
        universe_id: str,
        instrument_ids: tuple[str, ...],
        from_ts: str,
        to_ts: str,
        derivative_filter: str,
    ) -> tuple[dict[str, Any], ...]:
        del refs
        if derivative_filter == "futures":
            type_predicate = """
              AND lower(COALESCE(source_payload->>'instrument_type', source_payload->>'security_type', '')) IN ('future', 'futures')
            """
        else:
            type_predicate = """
              AND (
                    lower(COALESCE(source_payload->>'instrument_type', source_payload->>'security_type', '')) IN ('option', 'options')
                 OR lower(COALESCE(source_payload->>'option_type', source_payload->>'put_call', '')) IN ('put', 'call', 'p', 'c')
              )
            """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT raw_candle_id, instrument_id, close_ts, open_ts,
                           close_price, volume, turnover, provider, source_payload,
                           received_at
                      FROM raw_market.raw_candle
                     WHERE universe_id = %s
                       AND COALESCE(source_payload->>'underlying_instrument_id',
                                    source_payload->>'underlying_id',
                                    instrument_id) = ANY(%s)
                       AND COALESCE(close_ts, open_ts) BETWEEN %s AND %s
                       {type_predicate}
                     ORDER BY COALESCE(source_payload->>'underlying_instrument_id',
                                       source_payload->>'underlying_id',
                                       instrument_id),
                              COALESCE(close_ts, open_ts)
                    """,
                    (universe_id, list(instrument_ids), parse_utc_iso(from_ts), parse_utc_iso(to_ts)),
                )
                rows = cur.fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows:
            source_payload = row[8] or {}
            payloads.append(
                {
                    "raw_candle_id": str(row[0]),
                    "instrument_id": row[1] or "",
                    "close_ts": _iso(row[2]) if row[2] else None,
                    "open_ts": _iso(row[3]),
                    "close_price": _optional_float(row[4]),
                    "volume": _optional_float(row[5]),
                    "turnover": _optional_float(row[6]),
                    "provider": row[7] or "",
                    "source_payload": source_payload,
                    "received_at": _iso(row[9]) if row[9] else None,
                    "underlying_instrument_id": source_payload.get("underlying_instrument_id")
                    or source_payload.get("underlying_id")
                    or row[1],
                }
            )
        return tuple(payloads)


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def stable_uuid_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, encoded))


def _timestamp_in_range(timestamp: str, from_ts: str, to_ts: str) -> bool:
    if not timestamp:
        return False
    parsed = parse_utc_iso(timestamp)
    return parse_utc_iso(from_ts) <= parsed <= parse_utc_iso(to_ts)


def _matches_ref(raw_id: str, contract_id: str, requested_refs: set[str]) -> bool:
    if not requested_refs:
        return True
    if raw_id in requested_refs or contract_id in requested_refs:
        return True
    # Module input refs usually identify a store snapshot, not individual raw rows.
    return not any(ref == raw_id or ref == contract_id for ref in requested_refs)


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _source_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    source_payload = payload.get("source_payload") or payload.get("payload") or {}
    return source_payload if isinstance(source_payload, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(primary: Mapping[str, Any], secondary: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _optional_text(primary.get(key))
        if value is not None:
            return value
        value = _optional_text(secondary.get(key))
        if value is not None:
            return value
    return ""


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(primary: Mapping[str, Any], secondary: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(primary.get(key))
        if value is not None:
            return value
        value = _optional_float(secondary.get(key))
        if value is not None:
            return value
    return None


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "enabled", "active"}:
        return True
    if text in {"false", "0", "no", "n", "disabled", "inactive"}:
        return False
    return default


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
