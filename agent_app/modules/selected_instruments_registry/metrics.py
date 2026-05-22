from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


REQUIRED_METADATA_FIELDS = (
    "instrument_id",
    "ticker",
    "figi",
    "isin",
    "class_code",
    "board_id",
    "lot_size",
    "min_price_increment",
    "currency",
    "sector",
    "arena_go_secid",
    "arena_go_quantity_mode",
    "max_trade_quantity",
    "execution_enabled",
)


def active_instrument_count(profiles: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for profile in profiles if bool(profile.get("is_active", False)))


def inactive_instrument_count(profiles: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for profile in profiles if not bool(profile.get("is_active", False)))


def metadata_completeness_score(
    profiles: Iterable[Mapping[str, Any]],
    required_fields: tuple[str, ...] = REQUIRED_METADATA_FIELDS,
) -> float:
    active_profiles = [profile for profile in profiles if bool(profile.get("is_active", False))]
    if not active_profiles:
        return 1.0

    total_score = 0.0
    for profile in active_profiles:
        filled = sum(1 for field in required_fields if _is_filled(profile.get(field)))
        total_score += filled / len(required_fields)
    return total_score / len(active_profiles)


def mapping_conflict_count(profiles: Iterable[Mapping[str, Any]]) -> int:
    keys: list[tuple[str, str]] = []
    for profile in profiles:
        instrument_id = str(profile.get("instrument_id") or "")
        for field in ("ticker", "isin", "figi"):
            value = _normalized_mapping_value(profile.get(field))
            if value:
                keys.append((field, value))
        for alias in profile.get("aliases") or ():
            value = _normalized_mapping_value(alias)
            if value:
                keys.append(("alias", value))

        # A profile using the same non-empty value twice for one mapping type
        # is harmless after normalization; cross-instrument conflicts are not.
        if not instrument_id:
            keys.append(("instrument_id", ""))

    counts = Counter(keys)
    return sum(count - 1 for count in counts.values() if count > 1)


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _normalized_mapping_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()
