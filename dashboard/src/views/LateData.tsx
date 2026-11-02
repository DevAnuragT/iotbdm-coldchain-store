import { useState } from "react";
import { api, fmtDuration, fmtNum, fmtShort, fmtTemp, fmtTs } from "../api";
import type { LateDataResult } from "../api";
import type { Route } from "../hooks";
import { errMsg, href, useFetch } from "../hooks";
import { Pager, State } from "../components/Common";
import { WindowsTable } from "../components/WindowsTable";

const LIMIT = 50;

/** Scenario 5: run the live late-data demo and browse the rewrite log. */
export function LateData({ route }: { route: Route }) {
  const [delay, setDelay] = useState(3);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [result, setResult] = useState<LateDataResult | null>(null);
  const [reason, setReason] = useState("");
  const [shipment, setShipment] = useState(route.params.get("shipment_id") ?? "");
  const [offset, setOffset] = useState(0);
  const log = useFetch(() => api.rewrites({ reason, shipment_id: shipment || undefined, limit: LIMIT, offset }), [reason, shipment, offset]);

  const run = async () => {
    setRunning(true); setRunError(null);
    try {
      setResult(await api.lateData(delay));
      log.reload();
    } catch (e) {
      setRunError(errMsg(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="stack gap-lg">
      <div className="page-head">
        <div>
          <h1>Late data</h1>
          <div className="lede">A truck reconnects hours later and uploads readings that include an excursion inside a summary window that was already closed. The engine reopens, splits and re-compacts it.</div>
        </div>
        <div className="controls">
          <label>Delay
            <select value={delay} onChange={(e) => setDelay(Number(e.target.value))}>
              {[1, 2, 3, 4, 6].map((h) => <option key={h} value={h}>{h} h</option>)}
            </select>
          </label>
          <button className="primary" onClick={run} disabled={running}>{running ? "Running…" : "Run scenario"}</button>
        </div>
      </div>

      {runError && <p className="state error" style={{ padding: 0 }}>{runError}</p>}
      {running && !result && <p className="state" style={{ padding: 0 }}>Simulating one shipment and feeding it through the engine…</p>}
      {result && <RunResult r={result} stale={running} />}

      <div>
        <div className="row spread wrap mb">
          <div>
            <h2>Rewrite log</h2>
            <div className="small text-2">
              Every time late-arriving data changed a closed window.
              {log.data && <> {Object.entries(log.data.by_reason).map(([k, n]) => <span key={k} className="nowrap"> · {k} <b className="tnum">{fmtNum(n)}</b></span>)}</>}
            </div>
          </div>
          <div className="controls">
            <select value={reason} onChange={(e) => { setReason(e.target.value); setOffset(0); }} aria-label="Reason">
              <option value="">Any reason</option>
              {Object.keys(log.data?.by_reason ?? {}).map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <label>Shipment <input type="number" min={1} value={shipment} placeholder="id" onChange={(e) => { setShipment(e.target.value); setOffset(0); }} /></label>
          </div>
        </div>
        <State loading={log.loading && !log.data} error={log.error} empty={log.data?.total === 0}>No rewrites recorded.</State>
        {log.data && log.data.items.length > 0 && (
          <div className={log.loading ? "is-stale" : ""}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Rewritten at</th><th>Shipment</th><th>Window</th><th className="num">Version</th><th>Reason</th><th>Late reading</th><th className="num">Arrived late by</th><th>Detail</th></tr>
                </thead>
                <tbody>
                  {log.data.items.map((r) => (
                    <tr key={r.id}>
                      <td className="mono">{fmtTs(r.rewritten_at)}</td>
                      <td className="mono"><a href={href("shipments", r.shipment_id)}>{r.shipment_id}</a></td>
                      <td className="mono">{r.window_id ?? <span className="muted">open window</span>}</td>
                      <td className="num">v{r.old_version} → v{r.new_version}</td>
                      <td>{r.reason}</td>
                      <td className="mono">{fmtTs(r.late_reading_ts)}</td>
                      <td className="num">{fmtDuration(r.rewritten_at - r.late_reading_ts)}</td>
                      <td className="small text-2" style={{ whiteSpace: "normal", minWidth: 260 }}>{r.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={log.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </div>
        )}
      </div>
    </div>
  );
}

function RunResult({ r, stale }: { r: LateDataResult; stale: boolean }) {
  const beforeKeys = new Set(r.before.windows.map((w) => `${w.window_id}:${w.version}`));
  const beforeIds = new Set(r.before.windows.map((w) => w.window_id));
  const afterIds = new Set(r.after.windows.map((w) => w.window_id));
  // Windows the upload created or rewrote: not in the "before" snapshot and not merely
  // the shipment's normal progress after the late period.
  const changed = new Set(
    r.after.windows
      .filter((w) => !beforeKeys.has(`${w.window_id}:${w.version}`) && (w.version > 1 || w.start <= r.late_window.to_ts + 600))
      .map((w) => `${w.window_id}:${w.version}`),
  );
  const removed = new Set([...beforeIds].filter((id) => !afterIds.has(id)));
  const touched = r.before.windows.filter((w) => w.start <= r.late_window.to_ts && w.end >= r.late_window.from_ts);
  for (const w of touched) if (!removed.has(w.window_id) && !r.after.windows.some((a) => a.window_id === w.window_id && a.version === w.version)) removed.add(w.window_id);
  const newRewrites = r.after.rewrites.slice(r.before.rewrites.length);

  return (
    <div className={"panel" + (stale ? " is-stale" : "")}>
      <div className="panel-head">
        <h2>Shipment <a className="mono" href={href("shipments", r.shipment_id)}>{r.shipment_id}</a> <span className="text-2" style={{ fontWeight: 400 }}>· {r.delay_hours} h late</span></h2>
        <span className="small text-2">
          {fmtNum(r.late_window.readings)} readings {fmtShort(r.late_window.from_ts)} → {fmtShort(r.late_window.to_ts)} arrived at {fmtShort(r.late_window.arrived_at)} · excursion {fmtShort(r.excursion.start_ts)} → {fmtShort(r.excursion.end_ts)}, peak {fmtTemp(r.excursion.peak_c)}
        </span>
      </div>
      <div className="panel-body stack">
        <div className="split even">
          <div className="stack-sm">
            <div className="row spread"><h3>Before the upload</h3><span className="small muted">{r.before.windows.length} windows · {r.before.excursions.length} excursions · {fmtNum(r.before.raw_rows)} raw rows</span></div>
            <WindowsTable windows={r.before.windows} removed={removed} />
            {r.before.excursions.length === 0 && <p className="small muted">No excursion is visible: the hot readings have not arrived, so the window over that period was closed as a steady, in-range summary.</p>}
          </div>
          <div className="stack-sm">
            <div className="row spread"><h3>After the upload</h3><span className="small muted">{r.after.windows.length} windows · {r.after.excursions.length} excursions · {fmtNum(r.after.raw_rows)} raw rows</span></div>
            <WindowsTable windows={r.after.windows} changed={changed} />
            {r.after.excursions.map((e) => (
              <p key={e.excursion_id} className="small">
                <span className="kind hot" />Excursion <a className="mono" href={href("excursions", e.excursion_id)}>{e.excursion_id}</a> {fmtShort(e.start_ts)} → {fmtShort(e.end_ts)} · peak <span className="red">{fmtTemp(e.peak_c)}</span> · {fmtDuration(e.duration_s)}
              </p>
            ))}
          </div>
        </div>

        <div>
          <h3>Rewrite entries produced</h3>
          {newRewrites.length === 0 ? (
            <p className="small muted mt-sm">No rewrite entries: the late readings fell outside any closed window.</p>
          ) : (
            <div className="table-wrap mt-sm">
              <table>
                <thead><tr><th>Window</th><th className="num">Version</th><th>Reason</th><th>Late reading</th><th>Rewritten at</th><th>Detail</th></tr></thead>
                <tbody>
                  {newRewrites.map((x, i) => (
                    <tr key={i}>
                      <td className="mono">{x.window_id ?? <span className="muted">open window</span>}</td>
                      <td className="num">v{x.old_version} → v{x.new_version}</td>
                      <td>{x.reason}</td>
                      <td className="mono">{fmtTs(x.late_reading_ts)}</td>
                      <td className="mono">{fmtTs(x.rewritten_at)}</td>
                      <td className="small text-2" style={{ whiteSpace: "normal" }}>{x.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <p className="small muted">
          Engine: {Object.entries(r.engine).filter(([, v]) => typeof v === "number").map(([k, v]) => `${k.replace(/_/g, " ")} ${fmtNum(v as number)}`).join(" · ")}
        </p>
      </div>
    </div>
  );
}
