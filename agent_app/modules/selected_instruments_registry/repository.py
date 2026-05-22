from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class InstrumentUniverse:
    universe_id: str
    universe_name: str
    max_active_instruments: int = 20
    status: str = "active"


@dataclass(frozen=True)
class InstrumentAlias:
    instrument_id: str
    alias: str
    alias_type: str = "manual"
    source: str = "manual"


@dataclass(frozen=True)
class InstrumentMapping:
    instrument_id: str
    provider: str
    provider_symbol: str
    provider_payload: Mapping[str, Any] = field(default_factory=dict)


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
    payload: dict[str, Any] | None = None


class SelectedInstrumentsRegistryRepository(Protocol):
    def load_universe(self, universe_id: str) -> InstrumentUniverse | None:
        ...

    def ensure_universe(self, universe: InstrumentUniverse) -> None:
        ...

    def list_instrument_profiles(self, universe_id: str) -> tuple[Any, ...]:
        ...

    def save_instrument_profile(self, profile: Any) -> None:
        ...

    def replace_aliases(self, instrument_id: str, aliases: tuple[InstrumentAlias, ...]) -> None:
        ...

    def save_instrument_mapping(self, mapping: InstrumentMapping) -> None:
        ...

    def save_universe_snapshot(self, snapshot: Any) -> str:
        ...

    def write_audit_record(self, record: AuditRecord) -> str:
        ...


class InMemorySelectedInstrumentsRegistryRepository:
    def __init__(
        self,
        universes: Mapping[str, InstrumentUniverse] | None = None,
        profiles: tuple[Any, ...] = (),
    ) -> None:
        self.universes: dict[str, InstrumentUniverse] = dict(universes or {})
        self.profiles_by_id: dict[str, Any] = {}
        self.aliases_by_instrument_id: dict[str, tuple[InstrumentAlias, ...]] = {}
        self.mappings_by_key: dict[tuple[str, str], InstrumentMapping] = {}
        self.snapshots: list[Any] = []
        self.audit_records: list[AuditRecord] = []

        for profile in profiles:
            self.save_instrument_profile(profile)

    def load_universe(self, universe_id: str) -> InstrumentUniverse | None:
        return self.universes.get(universe_id)

    def ensure_universe(self, universe: InstrumentUniverse) -> None:
        if universe.universe_id not in self.universes:
            self.universes[universe.universe_id] = universe

    def list_instrument_profiles(self, universe_id: str) -> tuple[Any, ...]:
        profiles = [
            profile
            for profile in self.profiles_by_id.values()
            if getattr(profile, "universe_id", None) == universe_id
        ]
        return tuple(sorted(profiles, key=lambda profile: profile.instrument_id))

    def save_instrument_profile(self, profile: Any) -> None:
        if profile.universe_id not in self.universes:
            self.ensure_universe(
                InstrumentUniverse(
                    universe_id=profile.universe_id,
                    universe_name=profile.universe_id,
                )
            )
        self.profiles_by_id[profile.instrument_id] = profile

    def replace_aliases(self, instrument_id: str, aliases: tuple[InstrumentAlias, ...]) -> None:
        self.aliases_by_instrument_id[instrument_id] = aliases

    def save_instrument_mapping(self, mapping: InstrumentMapping) -> None:
        self.mappings_by_key[(mapping.instrument_id, mapping.provider)] = mapping

    def save_universe_snapshot(self, snapshot: Any) -> str:
        self.snapshots.append(snapshot)
        return f"registry.universe_snapshot:{snapshot.universe_snapshot_id}"

    def write_audit_record(self, record: AuditRecord) -> str:
        self.audit_records.append(record)
        return f"audit:memory:{len(self.audit_records)}"


class PostgresSelectedInstrumentsRegistryRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def load_universe(self, universe_id: str) -> InstrumentUniverse | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT universe_id, universe_name, max_active_instruments, status
                      FROM registry.instrument_universe
                     WHERE universe_id = %s
                    """,
                    (universe_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return InstrumentUniverse(
            universe_id=row[0],
            universe_name=row[1],
            max_active_instruments=int(row[2]),
            status=row[3],
        )

    def ensure_universe(self, universe: InstrumentUniverse) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry.instrument_universe (
                        universe_id, universe_name, max_active_instruments, status
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (universe_id) DO NOTHING
                    """,
                    (
                        universe.universe_id,
                        universe.universe_name,
                        universe.max_active_instruments,
                        universe.status,
                    ),
                )

    def list_instrument_profiles(self, universe_id: str) -> tuple[Any, ...]:
        from .service import InstrumentProfile

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT instrument_id, universe_id, ticker, figi, isin, class_code,
                           board_id, lot_size, min_price_increment, currency, sector,
                           issuer_name, aliases, related_entities, is_active, tradable,
                           allowed_horizons, arena_go_secid, arena_go_quantity_mode,
                           metadata, created_at, updated_at
                      FROM registry.instrument_profile
                     WHERE universe_id = %s
                     ORDER BY instrument_id
                    """,
                    (universe_id,),
                )
                rows = cur.fetchall()
        profiles = []
        for row in rows:
            metadata = dict(row[19] or {})
            profiles.append(
                InstrumentProfile(
                    instrument_id=row[0],
                    universe_id=row[1],
                    ticker=row[2],
                    figi=row[3],
                    isin=row[4],
                    class_code=row[5],
                    board_id=row[6],
                    lot_size=row[7],
                    min_price_increment=float(row[8]) if row[8] is not None else None,
                    currency=row[9],
                    sector=row[10],
                    issuer_name=row[11],
                    aliases=tuple(row[12] or ()),
                    related_entities=tuple(row[13] or ()),
                    is_active=bool(row[14]),
                    tradable=bool(row[15]),
                    allowed_horizons=tuple(row[16] or ()),
                    arena_go_secid=row[17],
                    arena_go_quantity_mode=row[18],
                    max_trade_quantity=metadata.get("max_trade_quantity"),
                    execution_enabled=bool(metadata.get("execution_enabled", True)),
                    metadata=metadata,
                    created_at=row[20].isoformat().replace("+00:00", "Z") if row[20] else None,
                    updated_at=row[21].isoformat().replace("+00:00", "Z") if row[21] else None,
                )
            )
        return tuple(profiles)

    def save_instrument_profile(self, profile: Any) -> None:
        from psycopg.types.json import Jsonb

        metadata = {
            **dict(profile.metadata),
            "max_trade_quantity": profile.max_trade_quantity,
            "execution_enabled": profile.execution_enabled,
        }
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry.instrument_profile (
                        instrument_id, universe_id, ticker, figi, isin, class_code,
                        board_id, lot_size, min_price_increment, currency, sector,
                        issuer_name, aliases, related_entities, is_active, tradable,
                        allowed_horizons, arena_go_secid, arena_go_quantity_mode,
                        metadata
                    ) VALUES (
                        %(instrument_id)s, %(universe_id)s, %(ticker)s, %(figi)s, %(isin)s,
                        %(class_code)s, %(board_id)s, %(lot_size)s, %(min_price_increment)s,
                        %(currency)s, %(sector)s, %(issuer_name)s, %(aliases)s,
                        %(related_entities)s, %(is_active)s, %(tradable)s,
                        %(allowed_horizons)s, %(arena_go_secid)s,
                        %(arena_go_quantity_mode)s, %(metadata)s
                    )
                    ON CONFLICT (instrument_id) DO UPDATE SET
                        ticker = EXCLUDED.ticker,
                        figi = EXCLUDED.figi,
                        isin = EXCLUDED.isin,
                        class_code = EXCLUDED.class_code,
                        board_id = EXCLUDED.board_id,
                        lot_size = EXCLUDED.lot_size,
                        min_price_increment = EXCLUDED.min_price_increment,
                        currency = EXCLUDED.currency,
                        sector = EXCLUDED.sector,
                        issuer_name = EXCLUDED.issuer_name,
                        aliases = EXCLUDED.aliases,
                        related_entities = EXCLUDED.related_entities,
                        is_active = EXCLUDED.is_active,
                        tradable = EXCLUDED.tradable,
                        allowed_horizons = EXCLUDED.allowed_horizons,
                        arena_go_secid = EXCLUDED.arena_go_secid,
                        arena_go_quantity_mode = EXCLUDED.arena_go_quantity_mode,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    """,
                    {
                        **profile.to_dict(),
                        "metadata": Jsonb(metadata),
                    },
                )

    def replace_aliases(self, instrument_id: str, aliases: tuple[InstrumentAlias, ...]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM registry.instrument_alias WHERE instrument_id = %s",
                    (instrument_id,),
                )
                for alias in aliases:
                    cur.execute(
                        """
                        INSERT INTO registry.instrument_alias (
                            instrument_id, alias, alias_type, source
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (instrument_id, alias, alias_type) DO NOTHING
                        """,
                        (alias.instrument_id, alias.alias, alias.alias_type, alias.source),
                    )

    def save_instrument_mapping(self, mapping: InstrumentMapping) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registry.instrument_mapping (
                        instrument_id, provider, provider_symbol, provider_payload
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (instrument_id, provider) DO UPDATE SET
                        provider_symbol = EXCLUDED.provider_symbol,
                        provider_payload = EXCLUDED.provider_payload
                    """,
                    (
                        mapping.instrument_id,
                        mapping.provider,
                        mapping.provider_symbol,
                        Jsonb(dict(mapping.provider_payload)),
                    ),
                )

    def save_universe_snapshot(self, snapshot: Any) -> str:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit.audit_record (
                        module_name, severity, event_type, message, object_type,
                        object_ref, reason_codes, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING audit_record_id
                    """,
                    (
                        "Selected Instruments Registry Module",
                        "info" if snapshot.validation_status == "valid" else "warning",
                        "universe_snapshot_published",
                        "Selected instruments universe snapshot published",
                        "universe_snapshot",
                        snapshot.universe_snapshot_id,
                        ["universe_snapshot_versioned", snapshot.validation_status],
                        Jsonb(snapshot.to_dict()),
                    ),
                )
                row = cur.fetchone()
        return f"audit.audit_record:{row[0]}"

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
                        Jsonb(record.payload or {}),
                    ),
                )
                row = cur.fetchone()
        return str(row[0])
