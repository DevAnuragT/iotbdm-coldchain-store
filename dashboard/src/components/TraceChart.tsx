import { useMemo, useRef } from "react";
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ReferenceArea, ResponsiveContainer,
} from "recharts";
import type { TooltipProps } from "recharts";
import type { Window, RawReading, Hourly } from "../api";
import { HI, LO, fmtTs, fmtTemp, fmtNum, fmtClock, outOfRange } from "../api";
import { useChartTheme } from "../hooks";
import { Legend } from "./Common";

/** Plot-area insets, shared with CustodyStrip so both sit on one time axis. */
export const PLOT_LEFT = 44;
export const PLOT_RIGHT = 12;
const Y_WIDTH = 40;
const MARGIN = { top: 8, right: PLOT_RIGHT, bottom: 0, left: PLOT_LEFT - Y_WIDTH };

type Pt = {
  ts: number;
  raw?: number | null; rawOut?: number | null;
  mean?: number | null; band?: [number, number] | null;
  hourly?: number | null; store?: number | null;
  w?: Window; r?: RawReading;
};

export type ExcursionSpan = { excursion_id: number; start_ts: number; end_ts: number };

export type TraceChartProps = {
  windows: Window[];
  raw: RawReading[];
  hourly?: Hourly[];
  store?: RawReading[];
  excursions?: ExcursionSpan[];
  selectedExcursion?: number | null;
  peakTs?: number | null;
  domain: [number, number];
  onZoom?: (d: [number, number]) => void;
  height?: number;
  showHourly?: boolean;
  showStore?: boolean;
};

const STEPS = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 1209600];

function timeTicks(from: number, to: number, max = 8): { ticks: number[]; step: number } {
  const span = Math.max(1, to - from);
  let step = STEPS[STEPS.length - 1];
  for (const s of STEPS) if (span / s <= max) { step = s; break; }
  const ticks: number[] = [];
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) ticks.push(t);
  return { ticks, step };
}

function buildData(windows: Window[], raw: RawReading[], hourly: Hourly[] | undefined, store: RawReading[] | undefined): Pt[] {
  const map = new Map<number, Pt>();
  const at = (ts: number) => {
    let p = map.get(ts);
    if (!p) { p = { ts }; map.set(ts, p); }
    return p;
  };
  const ws = [...windows].sort((a, b) => a.start - b.start);
  ws.forEach((w, i) => {
    const a = at(w.start); a.mean = w.mean; a.band = [w.min, w.max]; a.w = w;
    const b = at(w.end); b.mean = w.mean; b.band = [w.min, w.max]; b.w = w;
    const next = ws[i + 1];
    if (next && next.start - w.end > 60) { const g = at(w.end + 1); g.mean = null; g.band = null; }
  });
  const addSeries = (rows: RawReading[], key: "raw" | "store") => {
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      const p = at(r.ts);
      p[key] = r.temp;
      if (key === "raw") p.r = r;
      const next = rows[i + 1];
      if (next && next.ts - r.ts > 60) at(r.ts + 1)[key] = null;
    }
  };
  addSeries(raw, "raw");
  if (store) addSeries(store, "store");
  if (hourly && hourly.length) {
    for (const h of hourly) at(h.ts).hourly = h.mean;
    const last = hourly[hourly.length - 1];
    const end = at(last.ts + 3600);
    if (end.hourly === undefined) end.hourly = last.mean;
  }
  const pts = [...map.values()].sort((a, b) => a.ts - b.ts);

  // Points inside a window interval (raw/hourly ticks) must not break the window series.
  let wi = 0;
  for (const p of pts) {
    while (wi < ws.length && ws[wi].end < p.ts) wi++;
    const w = ws[wi];
    if (w && p.ts > w.start && p.ts < w.end && p.mean === undefined) { p.mean = w.mean; p.band = [w.min, w.max]; p.w = w; }
  }
  // Interpolate a raw/store series across foreign points that sit between two of its readings.
  const fill = (key: "raw" | "store") => {
    let lastIdx = -1;
    for (let i = 0; i < pts.length; i++) {
      const v = pts[i][key];
      if (v === undefined) continue;
      if (v === null) { lastIdx = -1; continue; }
      if (lastIdx >= 0 && i - lastIdx > 1) {
        const a = pts[lastIdx], b = pts[i];
        const va = a[key] as number;
        if (b.ts - a.ts <= 60) {
          for (let j = lastIdx + 1; j < i; j++) {
            const t = (pts[j].ts - a.ts) / (b.ts - a.ts);
            pts[j][key] = va + (v - va) * t;
          }
        }
      }
      lastIdx = i;
    }
  };
  fill("raw");
  if (store) fill("store");
  // Out-of-range overlay: the reading itself plus one neighbour on each side so the red
  // segment reaches the threshold crossing.
  const idx: number[] = [];
  pts.forEach((p, i) => { if (p.raw !== undefined && p.raw !== null) idx.push(i); });
  for (let k = 0; k < idx.length; k++) {
    const i = idx[k];
    const t = pts[i].raw as number;
    const out = outOfRange(t);
    const prevOut = k > 0 && idx[k - 1] === i - 1 ? outOfRange(pts[idx[k - 1]].raw as number) : false;
    const nextOut = k < idx.length - 1 && idx[k + 1] === i + 1 ? outOfRange(pts[idx[k + 1]].raw as number) : false;
    if (out || prevOut || nextOut) pts[i].rawOut = t;
  }
  return pts;
}

export function TraceChart(props: TraceChartProps) {
  const { windows, raw, hourly, store, excursions = [], selectedExcursion, peakTs, domain, onZoom, height = 300, showHourly, showStore } = props;
  const theme = useChartTheme();
  const data = useMemo(
    () => buildData(windows, raw, showHourly ? hourly : undefined, showStore ? store : undefined),
    [windows, raw, hourly, store, showHourly, showStore],
  );
  const hourlyByHour = useMemo(() => {
    const m = new Map<number, Hourly>();
    (hourly ?? []).forEach((h) => m.set(h.ts, h));
    return m;
  }, [hourly]);

  const [from, to] = domain;
  const { ticks, step } = timeTicks(from, to);
  const yDomain = useMemo<[number, number]>(() => {
    let lo = LO, hi = HI;
    for (const p of data) {
      if (p.ts < from || p.ts > to) continue;
      for (const v of [p.raw, p.store, p.hourly, p.mean]) if (typeof v === "number") { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      if (p.band) { lo = Math.min(lo, p.band[0]); hi = Math.max(hi, p.band[1]); }
    }
    return [Math.floor(lo - 0.5), Math.ceil(hi + 0.5)];
  }, [data, from, to]);
  // Integer ticks only; Recharts' automatic ticks can land on long decimals for odd domains.
  const yTicks = useMemo(() => {
    const [lo, hi] = yDomain;
    const step = Math.max(1, Math.ceil((hi - lo) / 6));
    const t: number[] = [];
    for (let v = lo; v <= hi; v += step) t.push(v);
    return t;
  }, [yDomain]);

  const tickFmt = (t: number) => {
    const d = new Date(t * 1000);
    const midnight = d.getUTCHours() === 0 && d.getUTCMinutes() === 0;
    if (step >= 86400 || midnight) return `${d.getUTCDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()]}`;
    return fmtClock(t);
  };

  // Drag-to-zoom. The selection rectangle is driven through refs so the (large) chart
  // does not re-render on every pointer move.
  const drag = useRef<{ x0: number; ts0: number; x1: number; ts1: number } | null>(null);
  const overlay = useRef<HTMLDivElement>(null);
  type ChartState = { activeLabel?: string | number; chartX?: number } | null | undefined;
  const onDown = (s: ChartState) => {
    if (!onZoom || !s || s.activeLabel == null || s.chartX == null) return;
    drag.current = { x0: s.chartX, ts0: Number(s.activeLabel), x1: s.chartX, ts1: Number(s.activeLabel) };
  };
  const onMove = (s: ChartState) => {
    const d = drag.current;
    if (!d || !s || s.chartX == null) return;
    d.x1 = s.chartX;
    if (s.activeLabel != null) d.ts1 = Number(s.activeLabel);
    const el = overlay.current;
    if (el) { el.style.display = "block"; el.style.left = `${Math.min(d.x0, d.x1)}px`; el.style.width = `${Math.abs(d.x1 - d.x0)}px`; }
  };
  const finish = (commit: boolean) => {
    const d = drag.current;
    drag.current = null;
    if (overlay.current) overlay.current.style.display = "none";
    if (!commit || !d || !onZoom) return;
    if (Math.abs(d.x1 - d.x0) < 8) return;
    const a = Math.min(d.ts0, d.ts1), b = Math.max(d.ts0, d.ts1);
    if (b - a >= 120) onZoom([a, b]);
  };

  const legend = [
    { label: "Raw readings (kept)", color: theme.accent, kind: "line" as const },
    { label: "Out of range", color: theme.red, kind: "line" as const },
    { label: "Summary window mean · min–max band", color: theme.accent, kind: "thin" as const },
    ...(showHourly ? [{ label: "Hourly mean (baseline store)", color: theme.gray, kind: "line" as const }] : []),
    ...(showStore ? [{ label: "Raw store (everything)", color: theme.gray, kind: "thin" as const }] : []),
  ];

  const TipContent = ({ active, payload }: TooltipProps<number, string>) => {
    if (!active || !payload || !payload.length) return null;
    const p = payload[0].payload as Pt;
    const hb = hourlyByHour.get(Math.floor(p.ts / 3600) * 3600);
    return (
      <div className="tip">
        <div className="t">{fmtTs(p.ts)}</div>
        {p.raw != null && (
          <div><span className="k" style={{ borderTopColor: outOfRange(p.raw) ? theme.red : theme.accent }} /><span className="v">{fmtTemp(p.raw, 2)}</span> raw reading{p.r?.excursion_id ? ` · excursion ${p.r.excursion_id}` : ""}</div>
        )}
        {p.w && p.raw == null && (
          <div>
            <span className="k" style={{ borderTopColor: theme.accent }} /><span className="v">{fmtTemp(p.w.mean, 2)}</span> summarised · window {p.w.window_id} v{p.w.version}
            <div className="muted" style={{ marginLeft: 16 }}>min {p.w.min.toFixed(2)} · max {p.w.max.toFixed(2)} · n {fmtNum(p.w.n)}</div>
          </div>
        )}
        {showHourly && hb && (
          <div><span className="k" style={{ borderTopColor: theme.gray }} /><span className="v">{fmtTemp(hb.mean, 2)}</span> hourly mean <span className="muted">(min {hb.min.toFixed(1)} · max {hb.max.toFixed(1)})</span></div>
        )}
        {showStore && p.store != null && (
          <div><span className="k" style={{ borderTopColor: theme.gray }} /><span className="v">{fmtTemp(p.store, 2)}</span> raw store</div>
        )}
      </div>
    );
  };

  return (
    <div className="stack-sm">
      <Legend items={legend} />
      <div className="chart" style={{ height, cursor: onZoom ? "crosshair" : undefined }}>
        <div className="zoom-overlay" ref={overlay} />
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={MARGIN}
            onMouseDown={onDown}
            onMouseMove={onMove}
            onMouseUp={() => finish(true)}
            onMouseLeave={() => finish(false)}
          >
            <CartesianGrid vertical={false} stroke={theme.grid} />
            <XAxis
              dataKey="ts" type="number" domain={[from, to]} ticks={ticks} tickFormatter={tickFmt}
              allowDataOverflow axisLine={{ stroke: theme.border2 }} tickLine={false} minTickGap={24}
            />
            <YAxis
              domain={yDomain} ticks={yTicks} width={Y_WIDTH} axisLine={false} tickLine={false} allowDecimals={false}
              tickFormatter={(v: number) => `${v}°`}
            />
            {excursions.map((e) => {
              const sel = e.excursion_id === selectedExcursion;
              return (
                <ReferenceArea
                  key={e.excursion_id} x1={e.start_ts} x2={e.end_ts} ifOverflow="hidden"
                  fill={theme.red} fillOpacity={sel ? 0.07 : 0.05} stroke={sel ? theme.red : undefined} strokeOpacity={0.5}
                />
              );
            })}
            <ReferenceLine y={HI} stroke={theme.muted} strokeDasharray="4 3" strokeWidth={1} ifOverflow="extendDomain" />
            <ReferenceLine y={LO} stroke={theme.muted} strokeDasharray="4 3" strokeWidth={1} ifOverflow="extendDomain" />
            {peakTs != null && <ReferenceLine x={peakTs} stroke={theme.red} strokeDasharray="2 3" ifOverflow="hidden" label={{ value: "peak", position: "insideTopRight", fill: theme.text2, fontSize: 11 }} />}
            <Area dataKey="band" stroke="none" fill={theme.accent} fillOpacity={0.12} isAnimationActive={false} connectNulls={false} activeDot={false} />
            <Line dataKey="mean" stroke={theme.accent} strokeWidth={1} dot={false} isAnimationActive={false} connectNulls={false} activeDot={{ r: 3, strokeWidth: 0 }} />
            {showStore && <Line dataKey="store" stroke={theme.gray} strokeWidth={1} strokeOpacity={0.8} dot={false} isAnimationActive={false} connectNulls={false} activeDot={false} />}
            {showHourly && <Line dataKey="hourly" type="stepAfter" stroke={theme.gray} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls activeDot={false} />}
            <Line dataKey="raw" stroke={theme.accent} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} activeDot={{ r: 3, strokeWidth: 0 }} />
            <Line dataKey="rawOut" stroke={theme.red} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} activeDot={{ r: 3, strokeWidth: 0, fill: theme.red }} />
            <Tooltip content={<TipContent />} isAnimationActive={false} cursor={{ stroke: theme.border2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
