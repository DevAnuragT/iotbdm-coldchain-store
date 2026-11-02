"""Optional real stream (F4): Redpanda/Kafka transport for the same events.

    coldchain produce --shipments 100          # simulator → topic `readings`
    coldchain compactor                         # topic → engine → TimescaleDB

Messages are JSON, keyed by shipment_id so a shipment always lands on one
partition — which is exactly the per-shipment state the engine keeps. The
in-process path (`coldchain load`) is the default; this module is the
"real stream semantics" variant and needs `pip install coldchain[kafka]` and
`docker compose --profile kafka up`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

from coldchain.models import CustodyInterval, HolderType, Reading, Shipment, ShipmentEnd, ShipmentStart, StreamEvent

TOPIC = os.environ.get("COLDCHAIN_TOPIC", "readings")
BOOTSTRAP = os.environ.get("COLDCHAIN_KAFKA", "localhost:19092")


def encode(ev: StreamEvent) -> tuple[bytes, bytes]:
    if type(ev) is Reading:
        key = ev.shipment_id
        body = {"t": "r", "s": ev.shipment_id, "ts": ev.ts, "c": ev.temp_c, "h": ev.humidity_pct, "la": ev.lat, "lo": ev.lon, "a": ev.arrival_ts}
    elif type(ev) is ShipmentStart:
        key = ev.shipment.shipment_id
        s = ev.shipment
        body = {
            "t": "s",
            "ship": [s.shipment_id, s.origin_id, s.destination_id, s.start_ts, s.end_ts, s.product],
            "custody": [[c.holder_id, c.holder_type.value, c.valid_from, c.valid_to] for c in ev.custody],
        }
    else:
        key = ev.shipment_id
        body = {"t": "e", "s": ev.shipment_id, "a": ev.arrival_ts}
    return str(key).encode(), json.dumps(body, separators=(",", ":")).encode()


def decode(payload: bytes) -> StreamEvent:
    d = json.loads(payload)
    if d["t"] == "r":
        return Reading(d["s"], d["ts"], d["c"], d["h"], d["la"], d["lo"], d["a"])
    if d["t"] == "s":
        sid, o, dest, a, b, prod = d["ship"]
        custody = tuple(CustodyInterval(sid, h, HolderType(ht), f, t) for h, ht, f, t in d["custody"])
        return ShipmentStart(Shipment(sid, o, dest, a, b, prod), custody)
    return ShipmentEnd(d["s"], d["a"])


def produce(events, bootstrap: str = BOOTSTRAP, topic: str = TOPIC, progress=None) -> int:
    from kafka import KafkaProducer

    p = KafkaProducer(bootstrap_servers=bootstrap, linger_ms=20, batch_size=256 * 1024, acks=1)
    n = 0
    for ev in events:
        key, body = encode(ev)
        p.send(topic, key=key, value=body)
        n += 1
        if progress and n % 100_000 == 0:
            progress(n)
    p.flush()
    p.close()
    return n


def consume(bootstrap: str = BOOTSTRAP, topic: str = TOPIC, group: str = "compactor", idle_timeout_s: float = 10.0) -> Iterator[StreamEvent]:
    """Yield events until the topic has been quiet for `idle_timeout_s`."""
    from kafka import KafkaConsumer

    c = KafkaConsumer(
        topic, bootstrap_servers=bootstrap, group_id=group, auto_offset_reset="earliest",
        enable_auto_commit=True, consumer_timeout_ms=int(idle_timeout_s * 1000), max_poll_records=5000,
        fetch_max_bytes=64 * 1024 * 1024,
    )
    for msg in c:
        yield decode(msg.value)
    c.close()
