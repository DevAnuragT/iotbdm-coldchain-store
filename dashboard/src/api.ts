// Typed client for the cold-chain query API plus the formatters used across views.
// All timestamps are integer epoch seconds (UTC); temperatures in °C.

export const LO = 2;
export const HI = 8;

export type ShipmentRow = {
  shipment_id: number; product: string; start_ts: number; end_ts: number;
  origin: string; destination: string; region: string;
  n_excursions: number; peak_c: number | null; excursion_s: number;
};
export type Custody = {
  holder_id: number; holder_type: HolderType; name: string; region: string;
  valid_from: number; valid_to: number;
};
export type HolderType = "factory" | "depot" | "truck" | "airport" | "clinic";
export type ExcursionRow = {
  excursion_id: number; shipment_id: number; start_ts: number; end_ts: number;
  peak_c: number; duration_s: number; detected_at: number; kind: "hot" | "cold";
  holder?: string | null; holder_type?: HolderType | null; holder_id?: number | null; region?: string | null;
  product?: string;
};
export type ShipmentDetail = {
  shipment_id: number; product: string; origin: string; destination: string;
  start_ts: number; end_ts: number; custody: Custody[]; excursions: ExcursionRow[];
  storage: { readings: number; windows: number; summarised: number; raw_rows: number; rewrites: number; max_version: number };
};
export type Window = {
  window_id: number; start: number; end: number; n: number;
  min: number; max: number; mean: number; sd: number; last: number; version: number;
};
export type RawReading = { ts: number; temp: number; humidity?: number | null; excursion_id?: number | null };
export type Hourly = { ts: number; n: number; mean: number; min: number; max: number };
export type Trace = {
  shipment_id: number; from_ts?: number; to_ts?: number; store?: string;
  windows: Window[]; raw: RawReading[]; hourly: Hourly[];
};
export type CustodyAt = Custody & { at_ts: number };
export type Rewrite = {
  id: number; window_id: number; shipment_id: number; old_version: number; new_version: number;
  reason: string; late_reading_ts: number; rewritten_at: number; detail: string;
};
export type ExcursionDetail = ExcursionRow & {
  peak_ts: number | null;
  custody: { start: CustodyAt | null; peak: CustodyAt | null; end: CustodyAt | null };
  raw: RawReading[]; hourly: Hourly[]; hidden_from_hourly_mean: boolean; rewrites: Rewrite[];
};
export type HiddenScenario = {
  summary: {
    total: number; hidden: number; visible_minmax: number; hidden_shipments: number;
    baseline_mean_recall: number | null; baseline_minmax_recall: number | null;
  };
  items: ExcursionRow[];
};
export type RouteRiskRow = {
  holder_id: number | null; name: string; type: HolderType; region: string | null;
  shipments: number; excursions: number; excursion_minutes: number; custody_hours: number; minutes_per_shipment: number;
};
export type PolicyKey = "raw" | "uniform_hourly" | "excursion_aware" | "audit_trail";
export type BenchMetric = { value: number | null; unit: string | null; detail: string | null };
export type Storage = {
  tables: Record<string, { bytes: number; rows: number }>;
  policies: Record<PolicyKey, number>;
  ratio: Record<PolicyKey, number | null>;
  benchmark: Record<string, Record<string, BenchMetric>>;
  benchmark_run: string | null;
  per_shipment: null | {
    readings: number; raw: number; uniform_hourly: number; excursion_aware: number;
    rows: { raw: number; uniform_hourly: number; excursion_aware_raw: number; windows: number; excursions: number };
  };
};
export type Overview = {
  run: null | { run_id: string; seed: number; readings: number | null; seconds: number | null; stats: unknown; config: unknown };
  counts: {
    shipments: number; excursions: number; windows: number; raw_rows: number; rewrites: number; holders: number;
    period_start: string | null; period_end: string | null;
  };
  excursions: { hot: number; cold: number; total_s: number; longest_s: number; hottest: number; coldest: number };
  storage: Storage;
};
export type Holder = { holder_id: number; name: string; type: HolderType; region: string };
export type Paged<T> = { total: number; items: T[] };
export type RewritesPage = Paged<Rewrite> & { by_reason: Record<string, number> };
export type SnapWindow = Pick<Window, "window_id" | "start" | "end" | "n" | "min" | "max" | "mean" | "version">;
export type SnapExcursion = Pick<ExcursionRow, "excursion_id" | "start_ts" | "end_ts" | "peak_c" | "duration_s">;
export type SnapRewrite = Omit<Rewrite, "id" | "shipment_id">;
export type Snapshot = { windows: SnapWindow[]; excursions: SnapExcursion[]; raw_rows: number; rewrites: SnapRewrite[] };
export type LateDataResult = {
  shipment_id: number; delay_hours: number;
  excursion: { start_ts: number; end_ts: number; peak_c: number };
  late_window: { from_ts: number; to_ts: number; readings: number; arrived_at: number };
  before: Snapshot; after: Snapshot; engine: Record<string, number>;
};

// ------------------------------------------------------------------ client
type Params = Record<string, string | number | boolean | null | undefined>;

function qs(params?: Params): string {
  if (!params) return "";
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, params?: Params): Promise<T> {
  const res = await fetch(`/api${path}${qs(params)}`, { method });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") msg = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => request<Overview>("GET", "/overview"),
  shipments: (p: Params) => request<Paged<ShipmentRow>>("GET", "/shipments", p),
  shipment: (id: number) => request<ShipmentDetail>("GET", `/shipments/${id}`),
  trace: (id: number, p?: Params) => request<Trace>("GET", `/shipments/${id}/trace`, p),
  excursions: (p: Params) => request<Paged<ExcursionRow>>("GET", "/excursions", p),
  excursion: (id: number) => request<ExcursionDetail>("GET", `/excursions/${id}`),
  hidden: (p?: Params) => request<HiddenScenario>("GET", "/scenarios/hidden", p),
  routeRisk: (p?: Params) => request<RouteRiskRow[]>("GET", "/scenarios/route-risk", p),
  storage: (p?: Params) => request<Storage>("GET", "/storage", p),
  rewrites: (p?: Params) => request<RewritesPage>("GET", "/rewrites", p),
  holders: (p?: Params) => request<Holder[]>("GET", "/holders", p),
  lateData: (delayHours: number) => request<LateDataResult>("POST", "/demo/late-data", { delay_hours: delayHours }),
};

// -------------------------------------------------------------- formatters
const pad2 = (n: number) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** 2025-11-17 14:03:40 (UTC) */
export function fmtTs(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const d = new Date(ts * 1000);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}
/** 17 Nov 14:03 */
export function fmtShort(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const d = new Date(ts * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}
/** 17 Nov 2025 */
export function fmtDate(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const d = new Date(ts * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
/** 14:03 or 14:03:40 */
export function fmtClock(ts: number, seconds = false): string {
  const d = new Date(ts * 1000);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}${seconds ? ":" + pad2(d.getUTCSeconds()) : ""}`;
}
/** ISO date string from the API (period_start) → 17 Nov 2025 */
export function fmtIsoDate(s: string | null | undefined): string {
  if (!s) return "—";
  return fmtDate(Math.floor(Date.parse(s) / 1000));
}

/** 40s · 35m 10s · 2h 05m · 3d 4h */
export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const sec = Math.round(s);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return sec % 60 ? `${m}m ${pad2(sec % 60)}s` : `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${pad2(m % 60)}m` : `${h}h`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}d ${h % 24}h` : `${d}d`;
}
/** minutes as 1,234 min */
export function fmtMinutes(min: number): string {
  return `${fmtNum(min, min < 10 ? 1 : 0)} min`;
}

export function fmtBytes(b: number | null | undefined, digits = 1): string {
  if (b === null || b === undefined) return "—";
  if (b < 1024) return `${Math.round(b)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = b / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : digits)} ${units[i]}`;
}
export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export function fmtPct(r: number | null | undefined, digits = 1): string {
  if (r === null || r === undefined) return "—";
  return `${(r * 100).toFixed(digits)} %`;
}
export function fmtTemp(c: number | null | undefined, digits = 1): string {
  if (c === null || c === undefined) return "—";
  return `${c.toFixed(digits)} °C`;
}
/** 1 : 13.7 */
export function fmtRatio(r: number | null | undefined): string {
  if (!r) return "—";
  return `1 : ${(1 / r).toFixed(1)}`;
}
export const outOfRange = (t: number) => t > HI || t < LO;
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
