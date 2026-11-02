"""Shared record types.

All timestamps are integer epoch seconds (UTC). Readings arrive every 10 s, so
second precision is exact and integer arithmetic keeps the hot path cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def to_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def from_dt(dt: datetime) -> int:
    return int(dt.timestamp())


class HolderType(str, Enum):
    FACTORY = "factory"
    DEPOT = "depot"
    TRUCK = "truck"
    AIRPORT = "airport"
    CLINIC = "clinic"


@dataclass(slots=True, frozen=True)
class Holder:
    holder_id: int
    name: str
    type: HolderType
    region: str


@dataclass(slots=True, frozen=True)
class CustodyInterval:
    """Half-open [valid_from, valid_to) interval during which holder had the shipment."""

    shipment_id: int
    holder_id: int
    holder_type: HolderType
    valid_from: int
    valid_to: int


@dataclass(slots=True)
class Reading:
    shipment_id: int
    ts: int  # event time
    temp_c: float
    humidity_pct: float
    lat: float
    lon: float
    arrival_ts: int  # processing time; > ts when the reading is late


@dataclass(slots=True, frozen=True)
class Shipment:
    shipment_id: int
    origin_id: int
    destination_id: int
    start_ts: int
    end_ts: int
    product: str


@dataclass(slots=True, frozen=True)
class GroundTruthExcursion:
    """An excursion as it exists in the complete, in-order telemetry.

    Computed by running the reference detector over the full series, so that the
    benchmark measures whether a storage policy *preserves* what was there.
    """

    shipment_id: int
    start_ts: int
    end_ts: int
    peak_c: float
    duration_s: int


# --- events emitted by the simulator stream -----------------------------------


@dataclass(slots=True, frozen=True)
class ShipmentStart:
    shipment: Shipment
    custody: tuple[CustodyInterval, ...]


@dataclass(slots=True, frozen=True)
class ShipmentEnd:
    shipment_id: int
    arrival_ts: int


StreamEvent = ShipmentStart | Reading | ShipmentEnd


# --- artifacts produced by the compaction engine ------------------------------


@dataclass(slots=True)
class SummaryWindow:
    window_id: int
    shipment_id: int
    window_start: int
    window_end: int  # ts of the last reading included (inclusive)
    n: int
    min_c: float
    max_c: float
    mean_c: float
    sd_c: float
    last_c: float
    version: int = 1


@dataclass(slots=True)
class Excursion:
    excursion_id: int
    shipment_id: int
    start_ts: int
    end_ts: int
    peak_c: float
    duration_s: int
    detected_at: int  # arrival time at which the detector confirmed it
    kind: str = "hot"  # or "cold"


@dataclass(slots=True, frozen=True)
class RawRow:
    shipment_id: int
    ts: int
    temp_c: float
    humidity_pct: float
    lat: float
    lon: float
    excursion_id: int | None


@dataclass(slots=True, frozen=True)
class RewriteEntry:
    window_id: int
    shipment_id: int
    old_version: int
    new_version: int
    reason: str
    late_reading_ts: int
    rewritten_at: int
    detail: str = ""


@dataclass(slots=True)
class EngineConfig:
    """Tunables of the excursion-aware policy. Defaults follow the PRD."""

    hi_enter_c: float = 8.0
    lo_enter_c: float = 2.0
    hi_exit_c: float = 7.5
    lo_exit_c: float = 2.5
    min_excursion_s: int = 60
    pre_buffer_s: int = 600
    post_buffer_s: int = 600
    max_window_s: int = 6 * 3600
    close_on_custody_change: bool = True
    # late data can be reconciled by replay only while its enclosing window's
    # start is still staged: max late delay (6 h) + max window span (6 h)
    staging_horizon_s: int = 12 * 3600
    reconcile_delay_s: int = 30  # a burst of late readings is settled once it has been quiet this long …
    reconcile_max_wait_s: int = 60  # … or at the latest this long after its first reading (N5)

    @staticmethod
    def from_dict(d: dict) -> "EngineConfig":
        cfg = EngineConfig()
        for k, v in d.items():
            if hasattr(cfg, k):
                setattr(cfg, k, type(getattr(cfg, k))(v))
        return cfg


@dataclass(slots=True)
class EngineStats:
    readings: int = 0
    late_readings: int = 0
    windows_closed: int = 0
    excursions: int = 0
    raw_rows: int = 0
    rewrites: int = 0
    replays: int = 0
    replayed_readings: int = 0
    unreconstructable: int = 0
    extra: dict = field(default_factory=dict)
