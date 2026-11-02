import { useEffect, useState } from "react";
import { api, cap, fmtDuration, fmtShort, fmtTemp } from "../api";
import type { ExcursionRow } from "../api";
import type { Route } from "../hooks";
import { href, navigate, useFetch } from "../hooks";
import { Pager, State } from "../components/Common";
import { ExcursionDetail } from "../components/ExcursionDetail";

const LIMIT = 50;

export function Excursions({ route }: { route: Route }) {
  const scenario = route.view === "custody";
  const view = route.view;
  const holderId = route.params.get("holder_id");
  const shipmentId = route.params.get("shipment_id");
  const [kind, setKind] = useState(route.params.get("kind") ?? "");
  const [holderType, setHolderType] = useState(route.params.get("holder_type") ?? "");
  const [minDur, setMinDur] = useState(route.params.get("min_duration") ?? "");
  const [minDev, setMinDev] = useState("");
  const [order, setOrder] = useState(route.params.get("order") ?? "peak");
  const [offset, setOffset] = useState(0);

  const list = useFetch(
    () => api.excursions({
      kind, holder_type: holderType, holder_id: holderId, shipment_id: shipmentId,
      min_duration: minDur ? Number(minDur) * 60 : undefined, min_peak_dev: minDev || undefined,
      order, limit: LIMIT, offset,
    }),
    [kind, holderType, holderId, shipmentId, minDur, minDev, order, offset],
  );

  // Scenario 2 opens with the first excursion selected so the custody panel is populated.
  useEffect(() => {
    if (scenario && route.id == null && list.data?.items.length) navigate(href(view, list.data.items[0].excursion_id));
  }, [scenario, route.id, list.data, view]);

  const keep = { holder_id: holderId, shipment_id: shipmentId };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>{scenario ? "Custody attribution" : "Excursions"}</h1>
          <div className="lede">
            {scenario
              ? "For an excursion: who held the shipment at its start, peak and end, with handover timestamps."
              : "Every period outside 2–8 °C for at least 60 s. Select one for the full-resolution trace and custody."}
            {holderId && <> · filtered to holder <span className="mono">{holderId}</span> <a href={href(view)}>clear</a></>}
            {shipmentId && <> · shipment <span className="mono">{shipmentId}</span> <a href={href(view)}>clear</a></>}
          </div>
        </div>
        <div className="controls">
          <select value={kind} onChange={(e) => { setKind(e.target.value); setOffset(0); }} aria-label="Kind">
            <option value="">Hot and cold</option><option value="hot">Hot</option><option value="cold">Cold</option>
          </select>
          <select value={holderType} onChange={(e) => { setHolderType(e.target.value); setOffset(0); }} aria-label="Holder type">
            <option value="">Any holder</option>
            {["factory", "depot", "truck", "airport", "clinic"].map((t) => <option key={t} value={t}>{cap(t)}</option>)}
          </select>
          <label>≥ <input type="number" min={1} value={minDur} placeholder="min" onChange={(e) => { setMinDur(e.target.value); setOffset(0); }} style={{ width: 64 }} /> min</label>
          <label>≥ <input type="number" min={0} step={0.5} value={minDev} placeholder="°C" onChange={(e) => { setMinDev(e.target.value); setOffset(0); }} style={{ width: 64 }} /> °C beyond</label>
          <select value={order} onChange={(e) => { setOrder(e.target.value); setOffset(0); }} aria-label="Order">
            <option value="peak">Worst peak</option><option value="duration">Longest</option><option value="start">Latest</option>
          </select>
        </div>
      </div>

      <div className="split">
        <div>
          <State loading={list.loading && !list.data} error={list.error} empty={list.data?.total === 0}>No excursions match.</State>
          {list.data && list.data.items.length > 0 && (
            <div className={list.loading ? "is-stale" : ""}>
              <ExcursionTable items={list.data.items} selected={route.id} onSelect={(id) => navigate(href(view, id, keep))} />
              <Pager total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </div>
          )}
        </div>
        <div className="sticky">
          {route.id ? <ExcursionDetail id={route.id} /> : <p className="state">Select an excursion.</p>}
        </div>
      </div>
    </div>
  );
}

export function ExcursionTable({ items, selected, onSelect }: { items: ExcursionRow[]; selected: number | null; onSelect: (id: number) => void }) {
  return (
    <div className="table-wrap tall">
      <table>
        <thead>
          <tr><th>ID</th><th>Shipment</th><th>Start</th><th className="num">Duration</th><th className="num">Peak</th><th>Holder</th></tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.excursion_id} className="clickable" aria-selected={e.excursion_id === selected} onClick={() => onSelect(e.excursion_id)}>
              <td className="mono"><span className={"kind " + e.kind} />{e.excursion_id}</td>
              <td className="mono">{e.shipment_id}</td>
              <td className="mono">{fmtShort(e.start_ts)}</td>
              <td className="num">{fmtDuration(e.duration_s)}</td>
              <td className="num red">{fmtTemp(e.peak_c)}</td>
              <td><div className="ellipsis" style={{ maxWidth: 170 }} title={`${e.holder ?? ""} · ${e.product ?? ""}`}>{e.holder ? <>{e.holder} <span className="muted">{e.holder_type}</span></> : <span className="muted">—</span>}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
