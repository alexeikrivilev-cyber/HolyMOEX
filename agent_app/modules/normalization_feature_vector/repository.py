from __future__ import annotations

import hashlib
import json
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
    allowed_horizons: tuple[str, ...] = ("intraday", "swing", "position")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "InstrumentProfile":
        return cls(
            instrument_id=str(payload.get("instrument_id") or ""),
            universe_id=str(payload.get("universe_id") or ""),
            ticker=str(payload.get("ticker") or payload.get("secid") or ""),
            sector=_optional_text(payload.get("sector")),
            is_active=_optional_bool(payload.get("is_active"), default=True),
            allowed_horizons=_string_tuple(payload.get("allowed_horizons"))
            or ("intraday", "swing", "position"),
        )


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    instrument_id: str
    metric_name: str
    metric_group: str
    metric_type: str
    raw_value: float | None
    normalized_value: float | None
    unit: str
    horizon: str
    contour: str
    timestamp: str
    ttl_seconds: int | None
    confidence_score: float
    source_module: str
    source_refs: tuple[str, ...]
    calculation_version: str
    quality_flags: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FeatureRecord":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        confidence_score = _optional_float(payload.get("confidence_score"))
        return cls(
            feature_id=str(payload.get("feature_id") or payload.get("id") or ""),
            instrument_id=str(payload.get("instrument_id") or ""),
            metric_name=str(payload.get("metric_name") or ""),
            metric_group=str(payload.get("metric_group") or ""),
            metric_type=str(payload.get("metric_type") or ""),
            raw_value=_optional_float(payload.get("raw_value")),
            normalized_value=_optional_float(payload.get("normalized_value")),
            unit=str(payload.get("unit") or ""),
            horizon=str(payload.get("horizon") or ""),
            contour=str(payload.get("contour") or ""),
            timestamp=str(payload.get("timestamp") or ""),
            ttl_seconds=_optional_int(payload.get("ttl_seconds")),
            confidence_score=1.0 if confidence_score is None else confidence_score,
            source_module=str(payload.get("source_module") or ""),
            source_refs=_string_tuple(payload.get("source_refs")),
            calculation_version=str(payload.get("calculation_version") or ""),
            quality_flags=_string_tuple(payload.get("quality_flags")),
            payload=source_payload,
        )

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
class DataQualityRecord:
    data_quality_record_id: str
    object_type: str
    object_ref: str
    quality_score: float | None
    quality_flags: tuple[str, ...]
    checked_at: str
    source_module: str
    calculation_version: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DataQualityRecord":
        source_payload = payload.get("payload") or {}
        if not isinstance(source_payload, Mapping):
            source_payload = {}
        return cls(
            data_quality_record_id=str(payload.get("data_quality_record_id") or payload.get("id") or ""),
            object_type=str(payload.get("object_type") or ""),
            object_ref=str(payload.get("object_ref") or ""),
            quality_score=_optional_float(payload.get("quality_score")),
            quality_flags=_string_tuple(payload.get("quality_flags")),
            checked_at=str(payload.get("checked_at") or ""),
            source_module=str(payload.get("source_module") or ""),
            calculation_version=str(payload.get("calculation_version") or ""),
            payload=source_payload,
        )


@dataclass(frozen=True)
class FeatureVector:
    feature_vector_id: str
    instrument_id: str
    horizon: str
    as_of_ts: str
    features: Mapping[str, Mapping[str, Any]]
    coverage_ratio: float
    data_quality_score: float
    build_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_vector_id": self.feature_vector_id,
            "instrument_id": self.instrument_id,
            "horizon": self.horizon,
            "as_of_ts": self.as_of_ts,
            "features": {key: dict(value) for key, value in self.features.items()},
            "coverage_ratio": self.coverage_ratio,
            "data_quality_score": self.data_quality_score,
            "build_version": self.build_version,
        }


class NormalizationFeatureVectorRepository(Protocol):
    def list_feature_records(
        self,
        feature_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        as_of_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        ...

    def list_data_quality_records(self, object_refs: tuple[str, ...]) -> tuple[DataQualityRecord, ...]:
        ...

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        ...

    def save_feature_record(self, record: FeatureRecord) -> str:
        ...

    def save_feature_vector(self, vector: FeatureVector) -> str:
        ...


class InMemoryNormalizationFeatureVectorRepository:
    def __init__(
        self,
        feature_records: tuple[Mapping[str, Any] | FeatureRecord, ...] = (),
        data_quality_records: tuple[Mapping[str, Any] | DataQualityRecord, ...] = (),
        profiles: tuple[Mapping[str, Any] | InstrumentProfile, ...] = (),
    ) -> None:
        self.feature_records = [
            record if isinstance(record, FeatureRecord) else FeatureRecord.from_mapping(record)
            for record in feature_records
        ]
        self.data_quality_records = tuple(
            record if isinstance(record, DataQualityRecord) else DataQualityRecord.from_mapping(record)
            for record in data_quality_records
        )
        self.profiles = tuple(
            profile if isinstance(profile, InstrumentProfile) else InstrumentProfile.from_mapping(profile)
            for profile in profiles
        )
        self.saved_feature_vectors: list[FeatureVector] = []

    def list_feature_records(
        self,
        feature_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        as_of_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        requested_feature_ids = _feature_ids_from_refs(feature_refs)
        requested_ids = set(instrument_ids)
        requested_horizons = set(horizons)
        from_dt = parse_utc_iso(from_ts)
        to_dt = parse_utc_iso(as_of_ts)
        records = [
            record
            for record in self.feature_records
            if record.instrument_id in requested_ids
            and record.horizon in requested_horizons
            and (not requested_feature_ids or record.feature_id in requested_feature_ids)
            and record.timestamp
            and from_dt <= parse_utc_iso(record.timestamp) <= to_dt
        ]
        return tuple(sorted(records, key=lambda item: (item.instrument_id, item.horizon, item.metric_name, item.timestamp, item.feature_id)))

    def list_data_quality_records(self, object_refs: tuple[str, ...]) -> tuple[DataQualityRecord, ...]:
        requested = set(object_refs)
        requested_tails = {_ref_tail(ref) for ref in object_refs}
        records = [
            record
            for record in self.data_quality_records
            if record.object_ref in requested or _ref_tail(record.object_ref) in requested_tails
        ]
        return tuple(sorted(records, key=lambda item: (item.object_ref, item.checked_at, item.data_quality_record_id)))

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
        return tuple(sorted(profiles, key=lambda item: item.instrument_id))

    def save_feature_record(self, record: FeatureRecord) -> str:
        self.feature_records.append(record)
        return f"features.feature_record:{record.feature_id}"

    def save_feature_vector(self, vector: FeatureVector) -> str:
        self.saved_feature_vectors.append(vector)
        return f"features.feature_vector:{vector.feature_vector_id}"


class PostgresNormalizationFeatureVectorRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def list_feature_records(
        self,
        feature_refs: tuple[str, ...],
        instrument_ids: tuple[str, ...],
        horizons: tuple[str, ...],
        from_ts: str,
        as_of_ts: str,
    ) -> tuple[FeatureRecord, ...]:
        requested_feature_ids = _feature_ids_from_refs(feature_refs)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT feature_id, instrument_id, metric_name, metric_group,
                           metric_type, raw_value, normalized_value, unit, horizon,
                           contour, timestamp, ttl_seconds, confidence_score,
                           source_module, source_refs, calculation_version,
                           quality_flags, payload
                      FROM features.feature_record
                     WHERE instrument_id = ANY(%s)
                       AND horizon = ANY(%s)
                       AND timestamp BETWEEN %s AND %s
                       AND (%s OR feature_id = ANY(%s))
                     ORDER BY instrument_id, horizon, metric_name, timestamp, feature_id
                    """,
                    (
                        list(instrument_ids),
                        list(horizons),
                        parse_utc_iso(from_ts),
                        parse_utc_iso(as_of_ts),
                        not requested_feature_ids,
                        list(requested_feature_ids),
                    ),
                )
                rows = cur.fetchall()
        return tuple(_feature_record_from_row(row) for row in rows)

    def list_data_quality_records(self, object_refs: tuple[str, ...]) -> tuple[DataQualityRecord, ...]:
        if not object_refs:
            return ()
        lookup = tuple(dict.fromkeys((*object_refs, *(_ref_tail(ref) for ref in object_refs))))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT data_quality_record_id, object_type, object_ref,
                           quality_score, quality_flags, checked_at,
                           source_module, calculation_version, payload
                      FROM features.data_quality_record
                     WHERE object_ref = ANY(%s)
                     ORDER BY object_ref, checked_at, data_quality_record_id
                    """,
                    (list(lookup),),
                )
                rows = cur.fetchall()
        return tuple(_data_quality_record_from_row(row) for row in rows)

    def list_instrument_profiles(
        self,
        universe_id: str,
        instrument_ids: tuple[str, ...],
    ) -> tuple[InstrumentProfile, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, sector,
                           is_active, allowed_horizons
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
                allowed_horizons=tuple(row[5] or ()),
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
                        normalized_value = EXCLUDED.normalized_value,
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

    def save_feature_vector(self, vector: FeatureVector) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO features.feature_vector (
                        feature_vector_id, instrument_id, horizon, as_of_ts,
                        features, coverage_ratio, data_quality_score,
                        build_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (feature_vector_id) DO UPDATE SET
                        features = EXCLUDED.features,
                        coverage_ratio = EXCLUDED.coverage_ratio,
                        data_quality_score = EXCLUDED.data_quality_score,
                        build_version = EXCLUDED.build_version
                    """,
                    (
                        vector.feature_vector_id,
                        vector.instrument_id,
                        vector.horizon,
                        parse_utc_iso(vector.as_of_ts),
                        Jsonb({key: dict(value) for key, value in vector.features.items()}),
                        vector.coverage_ratio,
                        vector.data_quality_score,
                        vector.build_version,
                    ),
                )
        return f"features.feature_vector:{vector.feature_vector_id}"


def stable_record_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _feature_record_from_row(row: tuple[Any, ...]) -> FeatureRecord:
    confidence_score = _optional_float(row[12])
    return FeatureRecord(
        feature_id=str(row[0]),
        instrument_id=row[1] or "",
        metric_name=row[2] or "",
        metric_group=row[3] or "",
        metric_type=row[4] or "",
        raw_value=_optional_float(row[5]),
        normalized_value=_optional_float(row[6]),
        unit=row[7] or "",
        horizon=row[8] or "",
        contour=row[9] or "",
        timestamp=_iso(row[10]),
        ttl_seconds=_optional_int(row[11]),
        confidence_score=1.0 if confidence_score is None else confidence_score,
        source_module=row[13] or "",
        source_refs=tuple(row[14] or ()),
        calculation_version=row[15] or "",
        quality_flags=tuple(row[16] or ()),
        payload=row[17] or {},
    )


def _data_quality_record_from_row(row: tuple[Any, ...]) -> DataQualityRecord:
    return DataQualityRecord(
        data_quality_record_id=str(row[0]),
        object_type=row[1] or "",
        object_ref=row[2] or "",
        quality_score=_optional_float(row[3]),
        quality_flags=tuple(row[4] or ()),
        checked_at=_iso(row[5]),
        source_module=row[6] or "",
        calculation_version=row[7] or "",
        payload=row[8] or {},
    )


def _ref_tail(ref: str) -> str:
    return str(ref).rsplit(":", 1)[-1] if ":" in str(ref) else str(ref)


def _feature_ids_from_refs(feature_refs: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for ref in feature_refs:
        text = str(ref).strip()
        if not text:
            continue
        values.append(text)
        values.append(_ref_tail(text))
    return tuple(dict.fromkeys(values))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "active"}:
        return True
    if text in {"false", "0", "no", "n", "inactive"}:
        return False
    return default


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        iterator = iter(value)
    except TypeError:
        return (str(value),)
    return tuple(str(item) for item in iterator if item not in (None, ""))


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)
