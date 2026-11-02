import { useEffect, useMemo, useState } from "react";
import { api, cap, fmtDuration, fmtNum, fmtShort, fmtTemp, fmtTs, outOfRange } from "../api";
import type { Route } from "../hooks";
import { href, navigate, useDebounced, useFetch } from "../hooks";
import { Pager, State } from "../components/Common";
import { TraceChart, PLOT_LEFT, PLOT_RIGHT } from "../components/TraceChart";
import { CustodyStrip } from "../components/CustodyStrip";

const LIMIT = 50;

export function Shipments({ route }: { route: Route }) {
  const [q, setQ] = useState(route.params.get("q") ?? "");
  const [only, setOnly] = useState(route.params.get("only") === "1");
  const [order, setOrder] = useState(route.params.get("order") ?? "excursions");
  const [offset, setOffset] = useState(0);
  const dq = useDebounced(q);
  const list = useFetch(() => api.shipments({ q: dq, only_excursions: only, order, limit: LIMIT, offset }), [dq, only, order, offset]);
  const selected = route.id;

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Shipments</h1>
          <div className="lede">Select a shipment to see its trace: summary windows, the raw readings kept around excursions, and custody.</div>
        </div>
        <div className="controls">
          <input type="search" placeholder="Search id, product, holder" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
          <label><input type="checkbox" checked={only} onChange={(e) => { setOnly(e.target.checked); setOffset(0); }} /> With excursions</label>
          <select value={order} onChange={(e) => { setOrder(e.target.value); setOffset(0); }} aria-label="Order">
            <option value="excursions">Most excursions</option>
            <option value="peak">Worst peak</option>
            <option value="start">Latest start</option>
          </select>
        </div>
      </div>

      <div className="split list-left">
        <div>
          <State loading={list.loading && !list.data} error={list.error} empty={list.data?.total === 0}>No shipments match.</State>
          {list.data && list.data.items.length > 0 && (
            <div className={list.loading ? "is-stale" : ""}>
              <div className="table-wrap tall">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th><th>Product</th><th>Start</th><th className="num">Exc.</th><th className="num">Peak</th><th className="num">Time out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data.items.map((s) => (
                      <tr key={s.shipment_id} className="clickable" aria-selected={s.shipment_id === selected} onClick={() => navigate(href("shipments", s.shipment_id))} title={`${s.origin} → ${s.destination}`}>
                        <td className="mono">{s.shipment_id}</td>
                        <td>{s.product}</td>
                        <td className="mono">{fmtShort(s.start_ts)}</td>
                        <td className="num">{s.n_excursions || <span className="muted">0</span>}</td>
                        <td className={"num" + (s.peak_c != null && outOfRange(s.peak_c) ? " red" : "")}>{s.peak_c != null ? fmtTemp(s.peak_c) : <span className="muted">—</span>}</td>
                        <td className="num">{s.excursion_s ? fmtDuration(s.excursion_s) : <span className="muted">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </div>
          )}
        </div>
        <div>
          {selected ? <ShipmentDetail id={selected} exParam={route.params.get("ex")} /> : <p className="state">Select a shipment.</p>}
        </div>
      </div>
    </div>
  );
}

export function ShipmentDetail({ id, exParam }: { id: number; exParam?: string | null }) {
  const det = useFetch(() => api.shipment(id), [id]);
  const trace = useFetch(() => api.trace(id), [id]);
  const [showHourly, setShowHourly] = useState(false);
  const [showStore, setShowStore] = useState(false);
  const base = useFetch(() => api.trace(id, { store: "baseline" }), [id], showHourly);
  const rawStore = useFetch(() => api.trace(id, { store: "raw" }), [id], showStore);
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [selEx, setSelEx] = useState<number | null>(exParam ? Number(exParam) : null);

  useEffect(() => { setZoom(null); setSelEx(exParam ? Number(exParam) : null); }, [id, exParam]);

  const d = det.data;
  const full = useMemo<[number, number] | null>(() => (d ? [d.start_ts, d.end_ts] : null), [d]);
  const domain = zoom ?? full;

  // Zoom to a selected excursion with 30 minutes of context on each side.
  useEffect(() => {
    if (!d || selEx == null) return;
    const e = d.excursions.find((x) => x.excursion_id === selEx);
    if (e) setZoom([Math.max(d.start_ts, e.start_ts - 1800), Math.min(d.end_ts, e.end_ts + 1800)]);
  }, [d, selEx]);

  if (!d || !domain) return <State loading={det.loading} error={det.error} />;
  const st = d.storage;
  const summarisedShare = st.readings ? st.summarised / st.readings : 0;
  const activeTs = selEx != null ? d.excursions.find((x) => x.excursion_id === selEx)?.start_ts ?? null : null;

  return (
    <div className={"stack" + (det.loading ? " is-stale" : "")}>
      <div className="row spread wrap">
        <div>
          <h2>Shipment <span className="mono">{d.shipment_id}</span> <span className="text-2" style={{ fontWeight: 400 }}>· {d.product}</span></h2>
          <div className="small text-2">{d.origin} → {d.destination} · {fmtTs(d.start_ts)} → {fmtTs(d.end_ts)} · {fmtDuration(d.end_ts - d.start_ts)}</div>
        </div>
        <div className="controls">
          <label><input type="checkbox" checked={showHourly} onChange={(e) => setShowHourly(e.target.checked)} /> Hourly baseline</label>
          <label><input type="checkbox" checked={showStore} onChange={(e) => setShowStore(e.target.checked)} /> Raw store</label>
          <button onClick={() => { setZoom(null); setSelEx(null); }} disabled={!zoom}>Reset zoom</button>
        </div>
      </div>

      <div className="row wrap small text-2">
        <span>{fmtNum(st.readings)} readings</span>
        <span className="muted">·</span>
        <span>{fmtNum(st.windows)} windows summarise {fmtNum(st.summarised)} ({(summarisedShare * 100).toFixed(1)} %)</span>
        <span className="muted">·</span>
        <span>{fmtNum(st.raw_rows)} raw rows kept</span>
        <span className="muted">·</span>
        <span>{st.rewrites} rewrites, max version {st.max_version}</span>
        <span className="muted">·</span>
        <a href={href("storage", null, { shipment: d.shipment_id })}>bytes</a>
      </div>

      {d.excursions.length > 0 && (
        <div className="chips" role="group" aria-label="Excursions">
          {d.excursions.map((e) => (
            <button key={e.excursion_id} aria-pressed={e.excursion_id === selEx} onClick={() => setSelEx(e.excursion_id === selEx ? null : e.excursion_id)} title={`${fmtTs(e.start_ts)} · ${fmtDuration(e.duration_s)} · peak ${fmtTemp(e.peak_c)}`}>
              <span className={"kind " + e.kind} />{fmtShort(e.start_ts)} · {fmtTemp(e.peak_c)} · {fmtDuration(e.duration_s)}
            </button>
          ))}
          {selEx != null && <a href={href("excursions", selEx)} className="small" style={{ alignSelf: "center" }}>excursion detail</a>}
        </div>
      )}

      <State loading={trace.loading && !trace.data} error={trace.error} />
      {trace.data && (
        <div className={trace.loading || (showHourly && base.loading) || (showStore && rawStore.loading) ? "is-stale" : ""}>
          <TraceChart
            windows={trace.data.windows} raw={trace.data.raw}
            hourly={base.data?.hourly} showHourly={showHourly && !!base.data}
            store={rawStore.data?.raw} showStore={showStore && !!rawStore.data}
            excursions={d.excursions} selectedExcursion={selEx}
            domain={domain} onZoom={setZoom} height={320}
          />
          <CustodyStrip custody={d.custody} domain={domain} padLeft={PLOT_LEFT} padRight={PLOT_RIGHT} activeTs={activeTs} />
          <p className="small muted mt-sm">Drag on the chart to zoom. Shaded spans are excursions.</p>
        </div>
      )}

      <div>
        <h3>Custody</h3>
        <div className="table-wrap mt-sm">
          <table>
            <thead><tr><th>Holder</th><th>Type</th><th>Region</th><th>From</th><th>To</th><th className="num">Held</th><th className="num">Excursions</th></tr></thead>
            <tbody>
              {d.custody.map((c) => {
                const n = d.excursions.filter((e) => e.start_ts < c.valid_to && e.end_ts >= c.valid_from).length;
                return (
                  <tr key={c.valid_from}>
                    <td>{c.name}</td><td>{cap(c.holder_type)}</td><td>{c.region}</td>
                    <td className="mono">{fmtTs(c.valid_from)}</td><td className="mono">{fmtTs(c.valid_to)}</td>
                    <td className="num">{fmtDuration(c.valid_to - c.valid_from)}</td>
                    <td className={"num" + (n ? " red" : "")}>{n || <span className="muted">0</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
