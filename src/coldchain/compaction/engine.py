"""Excursion-aware compaction engine (F5–F8).

One `CompactionEngine` consumes the arrival-ordered stream and keeps a small
state object per active shipment. Per reading, the hot path is:

  1. append to the ring buffer (a delay line of `pre_buffer_s`), and to staging;
  2. evict readings older than the delay line into the open summary window —
     so a window always lags the stream by the pre-buffer and never overlaps the
     raw segment that a later excursion flushes out of the ring;
  3. run the hysteresis detector (SUMMARY → candidate → EXCURSION → POST).

Mode transitions

  SUMMARY     in-range; evicted readings accumulate into the open window.
              An out-of-range reading starts a *candidate*; if the candidate
              spans >= min_excursion_s it is confirmed, otherwise it is simply
              folded into the window (its min/max will show the blip).
  EXCURSION   the open window closed when the candidate was confirmed, the ring
              buffer was flushed raw as the pre-buffer, every reading is raw.
  POST        the excursion ended (a reading returned inside the exit band);
              readings stay raw for post_buffer_s, then SUMMARY resumes.

Late data (ts <= watermark) never flows through the state machine directly:

  - in-range late reading inside a window      → merge into its stats, bump
                                                 version, log "merge"
  - late reading inside a raw segment          → write raw, extend peak/end
  - out-of-range late reading inside a window  → mark the shipment dirty; after
                                                 a short debounce, retract every
                                                 artifact from that window's start
                                                 and *replay* staged readings
                                                 through the state machine
                                                 ("split"); or, if the window
                                                 predates staging, merge
                                                 conservatively and keep the
                                                 reading raw ("beyond_horizon")

Every rewrite is logged (rewrite_log) with old/new version and reason.
"""

from __future__ import annotations

from bisect import bisect_right, insort
from dataclasses import dataclass, field
from math import sqrt

from coldchain.models import (
    CustodyInterval,
    EngineConfig,
    EngineStats,
    Excursion,
    RawRow,
    Reading,
    RewriteEntry,
    Shipment,
    ShipmentEnd,
    ShipmentStart,
    StreamEvent,
    SummaryWindow,
)
from coldchain.storage.base import Store

SUMMARY, EXCURSION, POST = 0, 1, 2
OUT, GRAY, BAND = 2, 1, 0

ID_STRIDE = 10_000  # artifact ids are shipment_id * ID_STRIDE + seq


class _Acc:
    """Welford accumulator for an open summary window. Order-independent, so
    late in-range readings can be merged after the fact."""

    __slots__ = ("n", "mean", "m2", "min", "max", "last", "last_ts", "start", "end")

    def __init__(self, ts: int, t: float):
        self.n = 1
        self.mean = t
        self.m2 = 0.0
        self.min = t
        self.max = t
        self.last = t
        self.last_ts = ts
        self.start = ts
        self.end = ts

    def add(self, ts: int, t: float) -> None:
        self.n += 1
        d = t - self.mean
        self.mean += d / self.n
        self.m2 += d * (t - self.mean)
        if t < self.min:
            self.min = t
        elif t > self.max:
            self.max = t
        if ts >= self.last_ts:
            self.last, self.last_ts = t, ts
        if ts > self.end:
            self.end = ts
        elif ts < self.start:
            self.start = ts

    def sd(self) -> float:
        return sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0


def merge_point(w: SummaryWindow, ts: int, t: float) -> None:
    """Fold one late reading into a closed window's stats (Welford update)."""
    n0 = w.n
    m2 = w.sd_c * w.sd_c * (n0 - 1) if n0 > 1 else 0.0
    n = n0 + 1
    d = t - w.mean_c
    mean = w.mean_c + d / n
    m2 += d * (t - mean)
    w.n, w.mean_c = n, mean
    w.sd_c = sqrt(m2 / (n - 1)) if n > 1 else 0.0
    w.min_c = min(w.min_c, t)
    w.max_c = max(w.max_c, t)
    if ts >= w.window_end:
        w.window_end, w.last_c = ts, t
    if ts < w.window_start:
        w.window_start = ts


@dataclass(slots=True)
class _Ring:
    """Delay line. Entries are lists [ts, temp, hum, lat, lon, done]; `done`
    marks readings already written raw so eviction will not summarise them."""

    items: list = field(default_factory=list)
    head: int = 0

    def append(self, e: list) -> None:
        if self.items and e[0] < self.items[-1][0]:
            # jittered reading: keep the delay line ordered
            insort(self.items, e, lo=self.head, key=lambda x: x[0])
        else:
            self.items.append(e)

    def oldest_ts(self) -> int | None:
        return self.items[self.head][0] if self.head < len(self.items) else None

    def pop_oldest(self) -> list:
        e = self.items[self.head]
        self.head += 1
        if self.head > 4096:
            del self.items[: self.head]
            self.head = 0
        return e

    def live(self) -> list:
        return self.items[self.head :]

    def clear(self) -> None:
        self.items = []
        self.head = 0


@dataclass(slots=True)
class _ShipmentState:
    shipment: Shipment
    custody: list[CustodyInterval]
    boundaries: list[int]  # custody handover timestamps
    boundary_idx: int = 0
    watermark: int = -1
    mode: int = SUMMARY
    ring: _Ring = field(default_factory=_Ring)
    acc: _Acc | None = None
    window_id: int = 0  # id of the open window (assigned when it opens)
    cand_start: int = -1
    cand_last: int = -1
    cand_peak: float = 0.0
    exc: Excursion | None = None
    post_until: int = -1
    raw_until: int = -1  # last ts written raw
    raw_from: int = -1  # first ts of the current raw segment
    raw_exc_id: int | None = None  # excursion the current raw segment belongs to
    pending_merges: dict[int, list[int]] = field(default_factory=dict)  # window_id -> [count, min_ts, max_ts]
    # memory of this shipment's artifacts (sorted by start)
    windows: list[SummaryWindow] = field(default_factory=list)
    excursions: list[Excursion] = field(default_factory=list)
    raw_spans: dict[int, list[int]] = field(default_factory=dict)  # excursion_id -> [from, to]
    # staging for replay
    staging: list[Reading] = field(default_factory=list)  # sorted by ts
    dirty_from: int = -1
    dirty_since: int = -1  # arrival of the first unsettled late reading
    dirty_last: int = -1  # arrival of the latest one
    win_seq: int = 0
    exc_seq: int = 0
    reuse_window: tuple[int, int] | None = None  # (window_id, version) for the first replayed window


class CompactionEngine:
    def __init__(self, store: Store, cfg: EngineConfig | None = None):
        self.store = store
        self.cfg = cfg or EngineConfig()
        self.states: dict[int, _ShipmentState] = {}
        self.stats = EngineStats()
        c = self.cfg
        self._hi, self._lo, self._hix, self._lox = c.hi_enter_c, c.lo_enter_c, c.hi_exit_c, c.lo_exit_c
        self._ring_s = c.pre_buffer_s + c.min_excursion_s

    # ------------------------------------------------------------------ public
    def process(self, ev: StreamEvent) -> None:
        if type(ev) is Reading:
            self.feed(ev)
        elif type(ev) is ShipmentStart:
            self.start(ev.shipment, ev.custody)
        elif type(ev) is ShipmentEnd:
            self.finish(ev.shipment_id, ev.arrival_ts)

    def start(self, shipment: Shipment, custody) -> None:
        custody = sorted(custody, key=lambda c: c.valid_from)
        boundaries = [c.valid_from for c in custody[1:]] if self.cfg.close_on_custody_change else []
        self.states[shipment.shipment_id] = _ShipmentState(shipment, custody, boundaries)
        self.store.shipment_started(shipment, custody)

    def feed(self, r: Reading) -> None:
        st = self.states.get(r.shipment_id)
        if st is None:
            self._orphan(r)
            return
        self.stats.readings += 1
        if r.ts <= st.watermark:
            self._late(st, r)
        else:
            st.watermark = r.ts
            st.staging.append(r)
            self._ingest(st, r.ts, r.temp_c, r.humidity_pct, r.lat, r.lon, r.arrival_ts)
        if st.dirty_since >= 0 and self._due(st, r.arrival_ts):
            self._settle(st, r.arrival_ts)

    def tick(self, arrival_ts: int) -> None:
        """Reconcile shipments whose late-data debounce has elapsed (call periodically)."""
        for st in self.states.values():
            if st.dirty_since >= 0 and self._due(st, arrival_ts):
                self._settle(st, arrival_ts)

    def _due(self, st: _ShipmentState, now: int) -> bool:
        """A burst is settled once it has gone quiet, or when the max wait is up."""
        return now - st.dirty_last >= self.cfg.reconcile_delay_s or now - st.dirty_since >= self.cfg.reconcile_max_wait_s

    def _touch(self, st: _ShipmentState, arrival: int) -> None:
        if st.dirty_since < 0:
            st.dirty_since = arrival
        st.dirty_last = arrival

    def _settle(self, st: _ShipmentState, now: int) -> None:
        """Debounce elapsed: publish coalesced merges, then replay if needed."""
        if st.pending_merges:
            by_id = {w.window_id: w for w in st.windows}
            for wid, (count, lo, hi) in st.pending_merges.items():
                w = by_id.get(wid)
                if w is None:
                    continue
                w.version += 1
                self.store.update_window(w)
                self._log(st, wid, w.version - 1, w.version, "merge", lo, now,
                          f"{count} late in-range readings ({lo}..{hi}) folded into closed window")
            st.pending_merges.clear()
        if st.dirty_from >= 0:
            self._reconcile(st, now)
        st.dirty_since = st.dirty_last = -1

    def finish(self, shipment_id: int, arrival_ts: int) -> None:
        st = self.states.pop(shipment_id, None)
        if st is None:
            return
        if st.dirty_since >= 0:
            self._settle(st, arrival_ts)
        self._drain(st, arrival_ts)
        self.store.shipment_finished(shipment_id)

    def active_shipments(self) -> int:
        return len(self.states)

    # ---------------------------------------------------------------- hot path
    def _dev(self, t: float) -> float:
        return max(t - self._hi, self._lo - t)

    def _cls(self, t: float) -> int:
        if t > self._hi or t < self._lo:
            return OUT
        if self._lox <= t <= self._hix:
            return BAND
        return GRAY

    def _ingest(self, st: _ShipmentState, ts: int, t: float, hum: float, lat: float, lon: float, arrival: int) -> None:
        cls = self._cls(t)
        if st.mode == EXCURSION and cls == BAND:
            # decide the excursion's end *before* classifying this reading as
            # raw, so a hole in the stream cannot stretch the raw segment past
            # end + post_buffer
            self._close_excursion(st)
        if st.mode == POST and st.cand_start < 0 and ts > st.post_until:
            st.mode = SUMMARY
        mode = st.mode
        raw_now = mode != SUMMARY
        e = [ts, t, hum, lat, lon, raw_now]
        st.ring.append(e)
        if raw_now:
            self._raw(st, ts, t, hum, lat, lon, st.raw_exc_id)

        # evict the delay line into the open window; the line is one minimum
        # excursion longer than the pre-buffer because a candidate is only
        # confirmed min_excursion_s after it started — and while a candidate is
        # open (gray-zone readings can stretch it) nothing of its pre-buffer leaves
        cutoff = self._cutoff(st, ts)
        ring = st.ring
        while ring.head < len(ring.items) and ring.items[ring.head][0] < cutoff:
            old = ring.pop_oldest()
            if not old[5]:
                self._summarise(st, old[0], old[1])

        if mode == EXCURSION:
            exc = st.exc
            if cls == OUT:
                exc.end_ts = ts
                if self._dev(t) > self._dev(exc.peak_c):
                    exc.peak_c = t
            return

        # SUMMARY or POST: candidate tracking
        if cls == OUT:
            if st.cand_start < 0:
                st.cand_start = ts
                st.cand_peak = t
            elif self._dev(t) > self._dev(st.cand_peak):
                st.cand_peak = t
            st.cand_last = ts
            if ts - st.cand_start >= self.cfg.min_excursion_s:
                self._confirm(st, arrival)
                return
        elif cls == BAND and st.cand_start >= 0:
            st.cand_start = -1  # blip: forget it, the window keeps the readings

    def _cutoff(self, st: _ShipmentState, ts: int) -> int:
        """Readings older than this have left the delay line (or would have)."""
        cutoff = ts - self._ring_s
        if st.cand_start >= 0 and st.cand_start - self.cfg.pre_buffer_s < cutoff:
            cutoff = st.cand_start - self.cfg.pre_buffer_s
        return cutoff

    def _summarise(self, st: _ShipmentState, ts: int, t: float) -> None:
        acc = st.acc
        if acc is not None:
            # close on custody handover or max span
            if (st.boundary_idx < len(st.boundaries) and ts >= st.boundaries[st.boundary_idx]) or (
                ts - acc.start >= self.cfg.max_window_s
            ):
                self._close_window(st)
                acc = None
        while st.boundary_idx < len(st.boundaries) and ts >= st.boundaries[st.boundary_idx]:
            st.boundary_idx += 1
        if acc is None:
            st.acc = _Acc(ts, t)
            if st.reuse_window is not None:
                st.window_id, _ = st.reuse_window
            else:
                st.win_seq += 1
                st.window_id = st.shipment.shipment_id * ID_STRIDE + st.win_seq
        else:
            acc.add(ts, t)

    def _close_window(self, st: _ShipmentState) -> None:
        acc = st.acc
        if acc is None:
            return
        version = 1
        if st.reuse_window is not None and st.reuse_window[0] == st.window_id:
            version = st.reuse_window[1]
            st.reuse_window = None
        w = SummaryWindow(
            st.window_id, st.shipment.shipment_id, acc.start, acc.end, acc.n,
            acc.min, acc.max, acc.mean, acc.sd(), acc.last, version,
        )
        st.windows.append(w)
        self.store.write_window(w)
        self.stats.windows_closed += 1
        st.acc = None

    def _raw(self, st, ts, t, hum, lat, lon, excursion_id) -> None:
        self.store.write_raw((RawRow(st.shipment.shipment_id, ts, t, hum, lat, lon, excursion_id),))
        self.stats.raw_rows += 1
        if ts > st.raw_until:
            st.raw_until = ts

    def _confirm(self, st: _ShipmentState, arrival: int) -> None:
        """Candidate reached min duration: open an excursion, flush the pre-buffer."""
        sid = st.shipment.shipment_id
        st.exc_seq += 1
        kind = "hot" if st.cand_peak > self._hi else "cold"
        exc = Excursion(sid * ID_STRIDE + st.exc_seq, sid, st.cand_start, st.cand_last, st.cand_peak,
                        st.cand_last - st.cand_start, arrival, kind)
        st.exc = exc
        st.excursions.append(exc)
        # the segment's extent is a matter of policy, not of which readings
        # happened to be available: [start - pre_buffer, end + post_buffer]
        st.raw_spans[exc.excursion_id] = [st.cand_start - self.cfg.pre_buffer_s, 1 << 62]
        self._close_window(st)
        rows = []
        for e in st.ring.live():
            if not e[5]:
                e[5] = True
                rows.append(RawRow(sid, e[0], e[1], e[2], e[3], e[4], exc.excursion_id))
        if rows:
            self.store.write_raw(rows)
            self.stats.raw_rows += len(rows)
            if rows[-1].ts > st.raw_until:
                st.raw_until = rows[-1].ts
        if st.mode == SUMMARY:
            st.raw_from = st.cand_start - self.cfg.pre_buffer_s
        st.raw_exc_id = exc.excursion_id
        st.mode = EXCURSION
        st.cand_start = -1
        self.stats.excursions += 1

    def _close_excursion(self, st: _ShipmentState) -> None:
        exc = st.exc
        exc.duration_s = exc.end_ts - exc.start_ts
        self.store.write_excursion(exc)
        st.post_until = exc.end_ts + self.cfg.post_buffer_s
        st.raw_spans[exc.excursion_id][1] = st.post_until
        st.mode = POST
        st.exc = None

    def _drain(self, st: _ShipmentState, arrival: int) -> None:
        """Shipment ended: flush the delay line and close whatever is open."""
        for e in st.ring.live():
            if not e[5]:
                self._summarise(st, e[0], e[1])
        st.ring.clear()
        if st.mode == EXCURSION:
            self._close_excursion(st)
        self._close_window(st)

    # ------------------------------------------------------------- late data
    def _stage(self, st: _ShipmentState, r: Reading) -> bool:
        horizon = st.watermark - self.cfg.staging_horizon_s
        if r.ts < horizon:
            return False
        if st.staging and r.ts < st.staging[-1].ts:
            insort(st.staging, r, key=lambda x: x.ts)
        else:
            st.staging.append(r)
        # trim the front of staging occasionally
        if len(st.staging) > 2 and st.staging[0].ts < horizon - 3600:
            i = bisect_right(st.staging, horizon, key=lambda x: x.ts)
            del st.staging[:i]
        return True

    def _locate(self, st: _ShipmentState, ts: int):
        """Return ("raw", excursion_id) | ("window", SummaryWindow) | ("open", None) | ("gap", None)."""
        for eid, (a, b) in st.raw_spans.items():
            if a <= ts <= b:
                return "raw", eid
        starts = [w.window_start for w in st.windows]
        i = bisect_right(starts, ts) - 1
        if i >= 0 and st.windows[i].window_end >= ts:
            return "window", st.windows[i]
        if st.acc is not None and st.acc.start <= ts:
            return "open", None
        # between artifacts (the stream had a hole): an in-range reading joins
        # the *nearest* following run, else the nearest preceding one; a raw
        # segment in between means it belongs to neither
        nxt: list = []
        if i + 1 < len(st.windows):
            nxt.append((st.windows[i + 1].window_start, "window", st.windows[i + 1]))
        if st.acc is not None and st.acc.start > ts:
            nxt.append((st.acc.start, "open", None))
        nxt_raw = min((a for a, _ in st.raw_spans.values() if a > ts), default=None)
        if nxt_raw is not None:
            nxt.append((nxt_raw, "raw", None))
        if nxt:
            _, kind, obj = min(nxt, key=lambda x: x[0])
            if kind != "raw":
                return kind, obj
        prev_raw = max((b for _, b in st.raw_spans.values() if b < ts), default=None)
        if i >= 0 and (prev_raw is None or prev_raw < st.windows[i].window_end):
            return "window", st.windows[i]
        return "gap", None

    def _late(self, st: _ShipmentState, r: Reading) -> None:
        self.stats.late_readings += 1
        ts, t = r.ts, r.temp_c
        staged = self._stage(st, r)
        cls = self._cls(t)

        # still inside the delay line (by time, not by ring contents: the line
        # may have a hole where a burst was missing): nothing summarised yet
        if ts >= self._cutoff(st, st.watermark):
            if st.raw_from >= 0 and ts >= st.raw_from and (st.mode == EXCURSION or ts <= st.post_until):
                # by timestamp it belongs to the raw segment that is (or was
                # just) being written, whatever mode the stream has moved on to
                st.ring.append([ts, t, r.humidity_pct, r.lat, r.lon, True])
                self._raw(st, ts, t, r.humidity_pct, r.lat, r.lon, st.raw_exc_id)
                exc = st.exc or next((e for e in st.excursions if e.excursion_id == st.raw_exc_id), None)
                if exc is not None:
                    self._late_in_excursion(st, exc, r, cls)
                return
            st.ring.append([ts, t, r.humidity_pct, r.lat, r.lon, False])
            if cls == OUT:
                self._mark_dirty(st, ts, r.arrival_ts)
            return

        kind, obj = self._locate(st, ts)
        if kind == "raw":
            self._raw(st, ts, t, r.humidity_pct, r.lat, r.lon, obj)
            exc = next((e for e in st.excursions if e.excursion_id == obj), None)
            if exc is not None:
                self._late_in_excursion(st, exc, r, cls)
            return
        if kind == "open":
            if cls == OUT:
                self._mark_dirty(st, ts, r.arrival_ts)
            else:
                st.acc.add(ts, t)
            return
        if kind == "gap":
            if staged:
                self._mark_dirty(st, ts, r.arrival_ts)
            else:
                self._unreconstructable(st, r, None)
            return
        w: SummaryWindow = obj
        if cls != OUT:
            merge_point(w, ts, t)
            pm = st.pending_merges.get(w.window_id)
            self._touch(st, r.arrival_ts)
            if pm is None:
                st.pending_merges[w.window_id] = [1, ts, ts]
            else:
                pm[0] += 1
                if ts < pm[1]:
                    pm[1] = ts
                elif ts > pm[2]:
                    pm[2] = ts
            return
        if staged and st.staging[0].ts <= w.window_start:
            self._mark_dirty(st, ts, r.arrival_ts)
        else:
            self._unreconstructable(st, r, w)

    def _late_in_excursion(self, st: _ShipmentState, exc: Excursion, r: Reading, cls: int) -> None:
        """A late reading landed in an excursion's raw segment (already stored
        raw). Out-of-range: widen the excursion. In the exit band *inside* the
        excursion: the run may actually be two runs, so replay."""
        ts, t = r.ts, r.temp_c
        if cls == OUT:
            if self._extend_excursion(exc, ts, t):
                # bounds moved, so the buffers around them move too: replay
                self._mark_dirty(st, ts, r.arrival_ts)
            elif exc is not st.exc:
                self.store.update_excursion(exc)
        elif cls == BAND and exc.start_ts < ts < exc.end_ts:
            self._mark_dirty(st, ts, r.arrival_ts)

    def _extend_excursion(self, exc: Excursion, ts: int, t: float) -> bool:
        """Update peak, and widen bounds; return True when the bounds changed."""
        if self._dev(t) > self._dev(exc.peak_c):
            exc.peak_c = t
        if ts > exc.end_ts:
            exc.end_ts = ts
        elif ts < exc.start_ts:
            exc.start_ts = ts
        else:
            return False
        exc.duration_s = exc.end_ts - exc.start_ts
        return True

    def _mark_dirty(self, st: _ShipmentState, ts: int, arrival: int) -> None:
        if st.dirty_from < 0 or ts < st.dirty_from:
            st.dirty_from = ts
        self._touch(st, arrival)

    def _unreconstructable(self, st: _ShipmentState, r: Reading, w: SummaryWindow | None) -> None:
        """Out-of-range late reading older than staging: keep the evidence raw and
        make the enclosing window's min/max reflect it, but do not invent an
        excursion whose extent we cannot know."""
        self.stats.unreconstructable += 1
        self._raw(st, r.ts, r.temp_c, r.humidity_pct, r.lat, r.lon, None)
        if w is not None:
            merge_point(w, r.ts, r.temp_c)
            w.version += 1
            self.store.update_window(w)
            self._log(st, w.window_id, w.version - 1, w.version, "beyond_horizon", r.ts, r.arrival_ts,
                      "out-of-range late reading predates staging; stored raw, window stats widened")
        else:
            self._log(st, None, 0, 0, "beyond_horizon", r.ts, r.arrival_ts, "out-of-range late reading in a gap; stored raw")

    def _log(self, st, window_id, old_v, new_v, reason, late_ts, now, detail="") -> None:
        self.store.write_rewrite(RewriteEntry(window_id, st.shipment.shipment_id, old_v, new_v, reason, late_ts, now, detail))
        self.stats.rewrites += 1

    def _reconcile(self, st: _ShipmentState, now: int) -> None:
        """Retract everything from the checkpoint window onwards and replay staging."""
        from_ts = st.dirty_from
        st.dirty_from = -1
        sid = st.shipment.shipment_id

        # checkpoint = start of the run (closed or open window) that contains the
        # earliest reading which could become raw: a pre-buffer before from_ts
        anchor = from_ts - self.cfg.pre_buffer_s
        starts = [w.window_start for w in st.windows]
        i = bisect_right(starts, anchor) - 1
        cp_ts, cp_window = st.shipment.start_ts, None
        if i >= 0:
            cp_ts, cp_window = st.windows[i].window_start, st.windows[i]
        if st.acc is not None and cp_ts <= st.acc.start <= anchor:
            cp_ts, cp_window = st.acc.start, None
        if st.staging and st.staging[0].ts > cp_ts and cp_ts > st.shipment.start_ts:
            # staging no longer covers the checkpoint window: give up on replay
            self.stats.unreconstructable += 1
            self._log(st, cp_window.window_id if cp_window else None, cp_window.version if cp_window else 0,
                      cp_window.version if cp_window else 0, "beyond_horizon", from_ts, now,
                      "checkpoint window predates staging; replay skipped")
            return

        # retract
        retracted_w = [w for w in st.windows if w.window_start >= cp_ts]
        for w in retracted_w:
            self.store.delete_window(sid, w.window_id)
        st.windows = [w for w in st.windows if w.window_start < cp_ts]
        retracted_e = [e for e in st.excursions if e.start_ts >= cp_ts]
        for e in retracted_e:
            if e is not st.exc:
                self.store.delete_excursion(sid, e.excursion_id)
            st.raw_spans.pop(e.excursion_id, None)
        st.excursions = [e for e in st.excursions if e.start_ts < cp_ts]
        self.store.delete_raw(sid, cp_ts)

        old_version = cp_window.version if cp_window else 0
        window_id = cp_window.window_id if cp_window else None

        # reset state to "just before cp_ts"
        st.ring.clear()
        for r in st.staging:
            if cp_ts - self.cfg.pre_buffer_s <= r.ts < cp_ts:
                st.ring.append([r.ts, r.temp_c, r.humidity_pct, r.lat, r.lon, True])
        st.acc = None
        st.mode = SUMMARY
        st.cand_start = -1
        st.exc = None
        st.post_until = -1
        st.raw_until = cp_ts - 1
        st.raw_from = -1
        st.raw_exc_id = None
        for w in retracted_w:
            st.pending_merges.pop(w.window_id, None)
        st.boundary_idx = bisect_right(st.boundaries, cp_ts - 1)
        st.reuse_window = (window_id, old_version + 1) if window_id is not None else None
        n_exc_before = len(st.excursions)

        # replay
        j = bisect_right(st.staging, cp_ts - 1, key=lambda x: x.ts)
        replayed = 0
        for r in st.staging[j:]:
            self._ingest(st, r.ts, r.temp_c, r.humidity_pct, r.lat, r.lon, now)
            replayed += 1
        new_exc = len(st.excursions) - n_exc_before
        reason = "split" if new_exc else "replay"
        self._log(st, window_id, old_version, old_version + 1, reason, from_ts, now,
                  f"retracted {len(retracted_w)} windows, {len(retracted_e)} excursions; replayed {replayed} readings; {new_exc} excursions found")
        self.stats.replays += 1
        self.stats.replayed_readings += replayed

    def _orphan(self, r: Reading) -> None:
        """Reading for a shipment the engine no longer holds (arrived after its end)."""
        self.stats.late_readings += 1
        w = self.store.find_window(r.shipment_id, r.ts)
        cls = self._cls(r.temp_c)
        if w is not None and cls != OUT:
            merge_point(w, r.ts, r.temp_c)
            w.version += 1
            self.store.update_window(w)
            self.store.write_rewrite(RewriteEntry(w.window_id, r.shipment_id, w.version - 1, w.version, "merge", r.ts, r.arrival_ts, "late reading after shipment end"))
        else:
            self.stats.unreconstructable += 1
            self.store.write_raw((RawRow(r.shipment_id, r.ts, r.temp_c, r.humidity_pct, r.lat, r.lon, None),))
            self.store.write_rewrite(RewriteEntry(w.window_id if w else None, r.shipment_id, w.version if w else 0, w.version if w else 0, "beyond_horizon", r.ts, r.arrival_ts, "late reading after shipment end; stored raw"))
        self.stats.rewrites += 1
