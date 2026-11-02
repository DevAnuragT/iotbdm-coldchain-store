import { useCallback, useEffect, useMemo, useRef, useState } from "react";

// ------------------------------------------------------------ data fetching
export type Fetched<T> = { data?: T; error?: string; loading: boolean; reload: () => void };

/** Fetches on mount and whenever `deps` change. Keeps the previous data while
 *  reloading so views hold their frame instead of flashing. */
export function useFetch<T>(loader: () => Promise<T>, deps: unknown[], enabled = true): Fetched<T> {
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: enabled });
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  useEffect(() => {
    if (!enabled) { setState({ loading: false }); return; }
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    loaderRef.current().then(
      (d) => { if (alive) setState({ data: d, loading: false }); },
      (e: unknown) => { if (alive) setState((s) => ({ ...s, loading: false, error: errMsg(e) })); },
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

export function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

// ------------------------------------------------------------------ routing
// Hash routes: #/shipments/82  #/excursions/820002  #/route-risk?group=type
export type Route = { view: string; id: number | null; params: URLSearchParams; hash: string };

export function parseHash(hash: string): Route {
  const s = hash.replace(/^#\/?/, "");
  const [path, query = ""] = s.split("?");
  const [view = "", idStr] = path.split("/");
  const id = idStr && /^\d+$/.test(idStr) ? Number(idStr) : null;
  return { view: view || "overview", id, params: new URLSearchParams(query), hash };
}

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return useMemo(() => parseHash(hash), [hash]);
}

export function href(view: string, id?: number | null, params?: Record<string, string | number | null | undefined>): string {
  let h = `#/${view}`;
  if (id !== undefined && id !== null) h += `/${id}`;
  if (params) {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
    const q = u.toString();
    if (q) h += `?${q}`;
  }
  return h;
}

export function navigate(to: string) {
  if (window.location.hash !== to) window.location.hash = to;
}

// --------------------------------------------------------------- chart theme
export type ChartTheme = {
  accent: string; red: string; gray: string; grid: string; muted: string; text2: string; surface: string; border2: string;
};

function readTheme(): ChartTheme {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    accent: v("--accent"), red: v("--red"), gray: v("--series-gray"), grid: v("--grid"),
    muted: v("--muted"), text2: v("--text-2"), surface: v("--surface"), border2: v("--border-2"),
  };
}

/** Chart colours resolved from the CSS custom properties, re-read when the OS theme flips. */
export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(readTheme);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setTheme(readTheme());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return theme;
}

/** Debounced string state for search inputs. */
export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
