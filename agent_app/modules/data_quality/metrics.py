from __future__ import annotations

import math
from numbers import Number
from datetime import datetime
from statistics import mean, pstdev
from typing import Any, Iterable, Mapping


STANDARD_QUALITY_FLAGS = (
    "missing_data",
    "stale_data",
    "duplicate_data",
    "outlier_data",
    "source_conflict",
    "low_coverage",
)


def data_quality_score(
    missing_rate: float,
    stale_rate: float,
    outlier_rate: float,
    conflict_rate: float,
) -> float:
    return _clip01(
        1
        - 0.35 * float(missing_rate)
        - 0.25 * float(stale_rate)
        - 0.2 * float(outlier_rate)
        - 0.2 * float(conflict_rate)
    )


def coverage_ratio(available_required_records: int, expected_required_records: int) -> float:
    if expected_required_records <= 0:
        return 1.0
    return _clip01(available_required_records / expected_required_records)


def missing_field_count(
    records: Iterable[Mapping[str, Any]],
    critical_fields: tuple[str, ...],
) -> int:
    if not critical_fields:
        return 0
    count = 0
    for record in records:
        for field_name in critical_fields:
            if not _is_filled(_nested_value(record, field_name)):
                count += 1
    return count


def stale_record_count(
    records: Iterable[Mapping[str, Any]],
    now_ts: datetime,
    required_freshness_seconds: int,
) -> int:
    if required_freshness_seconds <= 0:
        return 0
    count = 0
    for record in records:
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        age_seconds = (now_ts - timestamp).total_seconds()
        if age_seconds > required_freshness_seconds:
            count += 1
    return count


def expired_record_count(records: Iterable[Mapping[str, Any]], now_ts: datetime) -> int:
    count = 0
    for record in records:
        ttl_seconds = _optional_int(record.get("ttl_seconds"))
        if ttl_seconds is None:
            ttl_seconds = _optional_int(_nested_value(record, "payload.ttl_seconds"))
        if ttl_seconds is None or ttl_seconds < 0:
            continue
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        if (now_ts - timestamp).total_seconds() > ttl_seconds:
            count += 1
    return count


def duplicate_record_count(records: Iterable[Mapping[str, Any]], refs: tuple[str, ...] = ()) -> int:
    ref_set = {ref for ref in refs if ref}
    keys: list[str] = [ref for ref in refs if ref]
    for record in records:
        key = _record_identity(record)
        if key not in ref_set:
            keys.append(key)
    seen: set[str] = set()
    duplicates = 0
    for key in keys:
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


def outlier_count(
    records: Iterable[Mapping[str, Any]],
    threshold: float = 3.0,
) -> int:
    records_tuple = tuple(records)
    flagged = sum(1 for record in records_tuple if _has_quality_flag(record, "outlier_data", "outlier"))
    numeric_fields = _numeric_series_by_field(records_tuple)
    zscore_outliers = 0
    for values in numeric_fields.values():
        if len(values) < 3:
            continue
        values_mean = mean(values)
        std = pstdev(values)
        if std <= 0:
            continue
        zscore_outliers += sum(1 for value in values if abs((value - values_mean) / std) > threshold)
    return flagged + zscore_outliers


def source_conflict_count(
    records: Iterable[Mapping[str, Any]],
    tolerance: float = 0.0,
) -> int:
    groups: dict[tuple[str, ...], list[tuple[str, float]]] = {}
    for record in records:
        value = _record_numeric_value(record)
        if value is None:
            continue
        source = _record_source(record)
        key = _record_conflict_key(record)
        groups.setdefault(key, []).append((source, value))

    conflicts = 0
    for values in groups.values():
        sources = {source for source, _value in values}
        if len(sources) < 2:
            continue
        numeric_values = [value for _source, value in values]
        if max(numeric_values) - min(numeric_values) > tolerance:
            conflicts += 1
    return conflicts


def quality_flags_from_counts(
    *,
    missing_count: int,
    stale_count: int,
    duplicate_count: int,
    outliers: int,
    conflicts: int,
    coverage: float,
    required_coverage: float,
) -> tuple[str, ...]:
    flags: list[str] = []
    if missing_count > 0:
        flags.append("missing_data")
    if stale_count > 0:
        flags.append("stale_data")
    if duplicate_count > 0:
        flags.append("duplicate_data")
    if outliers > 0:
        flags.append("outlier_data")
    if conflicts > 0:
        flags.append("source_conflict")
    if coverage < required_coverage:
        flags.append("low_coverage")
    return tuple(flag for flag in STANDARD_QUALITY_FLAGS if flag in flags)


def _clip01(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _nested_value(record: Mapping[str, Any], field_name: str) -> Any:
    current: Any = record
    for part in str(field_name).split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _record_timestamp(record: Mapping[str, Any]) -> datetime | None:
    for field_name in (
        "timestamp",
        "as_of_ts",
        "open_ts",
        "close_ts",
        "trade_ts",
        "snapshot_ts",
        "value_ts",
        "published_at",
        "fetched_at",
        "received_at",
        "created_at",
        "checked_at",
    ):
        value = record.get(field_name)
        if value:
            return _parse_datetime(value)
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        return _record_timestamp(payload)
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _record_identity(record: Mapping[str, Any]) -> str:
    object_ref = record.get("object_ref")
    if object_ref:
        return str(object_ref)
    for field_name in (
        "feature_id",
        "feature_vector_id",
        "raw_candle_id",
        "raw_trade_id",
        "raw_orderbook_id",
        "raw_index_value_id",
        "raw_text_item_id",
        "raw_macro_point_id",
        "portfolio_snapshot_id",
        "position_state_id",
        "data_quality_record_id",
        "id",
    ):
        value = record.get(field_name)
        if value:
            return f"{field_name}:{value}"
    return str(sorted(record.items(), key=lambda item: item[0]))


def _has_quality_flag(record: Mapping[str, Any], *flags: str) -> bool:
    record_flags = tuple(str(flag) for flag in (record.get("quality_flags") or ()))
    payload = record.get("payload")
    if isinstance(payload, Mapping):
        record_flags += tuple(str(flag) for flag in (payload.get("quality_flags") or ()))
    return bool(set(record_flags) & set(flags))


def _numeric_series_by_field(records: tuple[Mapping[str, Any], ...]) -> dict[str, list[float]]:
    series: dict[str, list[float]] = {}
    for record in records:
        for field_name, value in _iter_numeric_fields(record):
            series.setdefault(field_name, []).append(value)
    return series


def _iter_numeric_fields(record: Mapping[str, Any], prefix: str = "") -> Iterable[tuple[str, float]]:
    for field_name, value in record.items():
        full_name = f"{prefix}.{field_name}" if prefix else str(field_name)
        if isinstance(value, Mapping) and field_name == "payload":
            continue
        if isinstance(value, Mapping):
            yield from _iter_numeric_fields(value, full_name)
        elif isinstance(value, Number) and not isinstance(value, bool):
            yield full_name, float(value)


def _record_numeric_value(record: Mapping[str, Any]) -> float | None:
    for field_name in (
        "raw_value",
        "normalized_value",
        "value",
        "close_price",
        "price",
        "market_price",
        "cash",
        "equity",
    ):
        value = record.get(field_name)
        if isinstance(value, Number) and not isinstance(value, bool):
            return float(value)
    return None


def _record_source(record: Mapping[str, Any]) -> str:
    for field_name in ("provider", "source_module", "source", "caller_module"):
        value = record.get(field_name)
        if value:
            return str(value)
    return "unknown"


def _record_conflict_key(record: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(record.get("instrument_id") or record.get("index_id") or record.get("series_id") or ""),
        str(record.get("metric_name") or record.get("timeframe") or record.get("record_type") or ""),
        str(
            record.get("horizon")
            or record.get("timestamp")
            or record.get("open_ts")
            or record.get("trade_ts")
            or record.get("value_ts")
            or record.get("as_of_ts")
            or ""
        ),
    )
