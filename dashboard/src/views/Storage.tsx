import { useState } from "react";
import { api, cap, fmtBytes, fmtNum, fmtPct, fmtRatio } from "../api";
import type { Route } from "../hooks";
import { href, navigate, useFetch } from "../hooks";
import { Stat, State } from "../components/Common";
import { StorageBars } from "../components/StorageBars";

const TABLE_POLICY: Record<string, string> = {
  readings_rawstore: "raw",
  baseline_hourly: "uniform hourly",
  readings_raw: "excursion-aware",
  summary_windows: "excursion-aware",
  excursions: "excursion-aware",
  rewrite_log: "audit trail",
  custody: "shared",
};

/** Scenario 4: bytes per policy, ratio, recall, and the same for one shipment. */
export function StorageView({ route }: { route: Route }) {
  const shipment = route.params.get("shipment");
  const [input, setInput] = useState(shipment ?? "");
  const st = useFetch(() => api.storage({ shipment_id: shipment || undefined }), [shipment]);
  const hid = useFetch(() => api.hidden({ limit: 1 }), []);
  const d = st.data;
  const s = hid.data?.summary;
  const bench = d?.benchmark ?? {};
  const benchPolicies = Object.keys(bench);
  const benchRecall = (p: string) => bench[p]?.recall?.value ?? null;

  return (
    <div className="stack gap-lg">
      <div className="page-head">
        <div>
          <h1>Storage audit</h1>
          <div className="lede">Bytes on disk per policy for the whole dataset, and for one shipment. Target: ≤ 10 % of raw with every excursion ≥ 60 s preserved.</div>
        </div>
        <form className="controls" onSubmit={(e) => { e.preventDefault(); navigate(href("storage", null, { shipment: input.trim() })); }}>
          <label>Shipment <input type="number" min={1} value={input} onChange={(e) => setInput(e.target.value)} placeholder="id" /></label>
          <button type="submit">Show</button>
        </form>
      </div>

      <State loading={st.loading && !d} error={st.error} />
      {d && (
        <div className={"split even" + (st.loading ? " is-stale" : "")}>
          <div className="panel">
            <div className="panel-head"><h2>Whole dataset</h2><span className="small muted">{fmtNum(d.tables.readings_rawstore?.rows ?? 0)} readings</span></div>
            <div className="panel-body stack">
              <div className="stats">
                <Stat hero label="Excursion-aware, share of raw" value={fmtPct(d.ratio.excursion_aware)} note={fmtRatio(d.ratio.excursion_aware)} />
                <Stat label="Uniform hourly, share of raw" value={fmtPct(d.ratio.uniform_hourly, 2)} note={fmtRatio(d.ratio.uniform_hourly)} />
                <Stat label="Audit trail" value={fmtBytes(d.policies.audit_trail)} note={`${fmtNum(d.tables.rewrite_log?.rows ?? 0)} rewrite entries`} />
              </div>
              <StorageBars
                bars={[
                  { key: "raw", label: "Raw (everything)", bytes: d.policies.raw, note: "readings_rawstore" },
                  { key: "uniform_hourly", label: "Uniform hourly", bytes: d.policies.uniform_hourly, note: "baseline_hourly" },
                  { key: "excursion_aware", label: "Excursion-aware", bytes: d.policies.excursion_aware, ours: true, note: "readings_raw + summary_windows + excursions" },
                ]}
              />
              <div>
                <h3>Excursion recall</h3>
                <table className="kv mt-sm">
                  <tbody>
                    <tr><td>Raw (everything)</td><td className="num"><b>{fmtPct(benchRecall("raw") ?? 1)}</b></td></tr>
                    <tr><td>Uniform hourly — hour mean outside 2–8 °C</td><td className="num"><b>{benchRecall("uniform_hourly") != null ? fmtPct(benchRecall("uniform_hourly")) : s ? fmtPct(s.baseline_mean_recall) : "…"}</b></td></tr>
                    <tr><td>Uniform hourly — hour min/max outside 2–8 °C</td><td className="num"><b>{s ? fmtPct(s.baseline_minmax_recall) : "…"}</b> <span className="muted">no onset, duration or peak timing</span></td></tr>
                    <tr><td>Excursion-aware</td><td className="num"><b>{fmtPct(benchRecall("excursion_aware") ?? 1)}</b> <span className="muted">{s ? `${fmtNum(s.total)} of ${fmtNum(s.total)}` : ""}</span></td></tr>
                  </tbody>
                </table>
                <p className="small muted mt-sm">Recall is measured against the excursion table, which is derived from the full-resolution stream{d.benchmark_run ? "; values from the latest benchmark run" : ""}.</p>
              </div>
            </div>
          </div>

          <div className="stack">
            <div className="panel">
              <div className="panel-head"><h2>Per shipment</h2>{d.per_shipment && <span className="small muted">shipment <a className="mono" href={href("shipments", Number(shipment))}>{shipment}</a> · {fmtNum(d.per_shipment.readings)} readings</span>}</div>
              <div className="panel-body stack">
                {!d.per_shipment && <p className="state" style={{ padding: 0 }}>Enter a shipment id to see its bytes under each policy.</p>}
                {d.per_shipment && (
                  <>
                    <StorageBars
                      bars={[
                        { key: "raw", label: "Raw (everything)", bytes: d.per_shipment.raw, note: `${fmtNum(d.per_shipment.rows.raw)} rows` },
                        { key: "uniform_hourly", label: "Uniform hourly", bytes: d.per_shipment.uniform_hourly, note: `${fmtNum(d.per_shipment.rows.uniform_hourly)} rows` },
                        { key: "excursion_aware", label: "Excursion-aware", bytes: d.per_shipment.excursion_aware, ours: true },
                      ]}
                    />
                    <table className="kv">
                      <tbody>
                        <tr><td>Raw rows kept</td><td className="num">{fmtNum(d.per_shipment.rows.excursion_aware_raw)} <span className="muted">of {fmtNum(d.per_shipment.rows.raw)}</span></td></tr>
                        <tr><td>Summary windows</td><td className="num">{fmtNum(d.per_shipment.rows.windows)}</td></tr>
                        <tr><td>Excursions</td><td className="num">{fmtNum(d.per_shipment.rows.excursions)}</td></tr>
                        <tr><td>Hourly rows (baseline)</td><td className="num">{fmtNum(d.per_shipment.rows.uniform_hourly)}</td></tr>
                        <tr><td>Share of raw</td><td className="num"><b>{fmtPct(d.per_shipment.raw ? d.per_shipment.excursion_aware / d.per_shipment.raw : null)}</b></td></tr>
                      </tbody>
                    </table>
                    <p className="small muted">Estimated from average row width per table × rows for this shipment.</p>
                  </>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><h2>Tables</h2></div>
              <div className="table-wrap" style={{ border: 0, borderRadius: 0 }}>
                <table>
                  <thead><tr><th>Table</th><th>Policy</th><th className="num">Rows</th><th className="num">Bytes</th><th className="num">B / row</th></tr></thead>
                  <tbody>
                    {Object.entries(d.tables).map(([name, t]) => (
                      <tr key={name}>
                        <td className="mono">{name}</td>
                        <td className={TABLE_POLICY[name] === "excursion-aware" ? "accent" : "text-2"}>{TABLE_POLICY[name] ?? "—"}</td>
                        <td className="num">{fmtNum(t.rows)}</td>
                        <td className="num">{fmtBytes(t.bytes)}</td>
                        <td className="num">{t.rows ? fmtNum(t.bytes / t.rows) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {d && (
        <div className="panel">
          <div className="panel-head"><h2>Benchmark</h2><span className="small muted">{d.benchmark_run ? <>run <span className="mono">{d.benchmark_run}</span></> : "no benchmark run recorded"}</span></div>
          <div className="panel-body">
            {benchPolicies.length === 0 ? (
              <p className="state" style={{ padding: 0 }}>No benchmark results yet. Run <code>coldchain benchmark</code> to record bytes, recall and p95 query latency per policy.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Policy</th><th className="num">Bytes</th><th className="num">Share of raw</th><th className="num">Compressed</th><th className="num">Recall</th><th className="num">Peak error (MAE)</th><th>Query latency, p95</th>
                    </tr>
                  </thead>
                  <tbody>
                    {benchPolicies.map((p) => {
                      const m = bench[p];
                      const latencies = Object.entries(m).filter(([k, v]) => k.startsWith("p95_") && v.value != null);
                      return (
                        <tr key={p}>
                          <td>{cap(p.replace(/_/g, " "))}</td>
                          <td className="num">{fmtBytes(m.bytes?.value)}</td>
                          <td className="num">{fmtPct(m.ratio?.value, 2)}</td>
                          <td className="num">{fmtBytes(m.bytes_compressed?.value)}</td>
                          <td className="num">{fmtPct(m.recall?.value)}</td>
                          <td className="num">{m.peak_mae?.value != null ? `${m.peak_mae.value.toFixed(2)} °C` : "—"}</td>
                          <td className="small text-2" style={{ whiteSpace: "normal" }}>
                            {latencies.length === 0 ? "—" : latencies.map(([k, v]) => `${k.replace(/^p95_/, "").replace(/_/g, " ")} ${fmtNum(v.value, 1)} ms`).join(" · ")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
