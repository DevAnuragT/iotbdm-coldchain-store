import { api, fmtBytes, fmtDuration, fmtIsoDate, fmtNum, fmtPct, fmtRatio, fmtTemp } from "../api";
import { useFetch } from "../hooks";
import { Stat, State } from "../components/Common";
import { StorageBars } from "../components/StorageBars";

export function Overview() {
  const ov = useFetch(() => api.overview(), []);
  const hid = useFetch(() => api.hidden({ limit: 1 }), []);
  const d = ov.data;
  const s = hid.data?.summary;
  const run = d?.run;
  const cfg = (run?.config ?? null) as null | { sim?: { n_shipments?: number; period_days?: number; excursion_rate?: number; late_fraction?: number } };

  return (
    <div className="stack gap-lg">
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <div className="lede">
            {run ? (
              <>
                Run <span className="mono">{run.run_id}</span> · seed {run.seed}
                {cfg?.sim?.period_days ? ` · ${cfg.sim.period_days}-day simulation` : ""}
                {d?.counts.period_start ? ` · ${fmtIsoDate(d.counts.period_start)} → ${fmtIsoDate(d.counts.period_end)}` : ""}
              </>
            ) : (
              "Excursion-aware cold-chain data store"
            )}
          </div>
        </div>
      </div>

      <State loading={ov.loading && !d} error={ov.error} />

      {d && (
        <div className={ov.loading ? "is-stale" : ""}>
          <div className="stats">
            <Stat label="Shipments" value={fmtNum(d.counts.shipments)} />
            <Stat label="Excursions" value={fmtNum(d.counts.excursions)} note={`${d.excursions.hot} hot · ${d.excursions.cold} cold`} />
            <Stat label="Time out of range" value={fmtDuration(d.excursions.total_s)} note={`longest ${fmtDuration(d.excursions.longest_s)}`} />
            <Stat label="Hottest / coldest" value={<>{fmtTemp(d.excursions.hottest)}<small>/ {fmtTemp(d.excursions.coldest)}</small></>} />
            <Stat label="Summary windows" value={fmtNum(d.counts.windows)} />
            <Stat label="Raw readings kept" value={fmtNum(d.counts.raw_rows)} note="around excursions only" />
            <Stat label="Rewrites" value={fmtNum(d.counts.rewrites)} note="late data reopened a window" />
            <Stat label="Holders" value={fmtNum(d.counts.holders)} />
          </div>
        </div>
      )}

      {d && (
        <div className="split even">
          <div className="panel">
            <div className="panel-head"><h2>Storage per policy</h2><span className="small muted">whole dataset · {fmtNum(d.storage.tables.readings_rawstore?.rows ?? 0)} raw readings</span></div>
            <div className="panel-body stack">
              <div className="stats">
                <Stat hero label="Excursion-aware store, share of raw" value={fmtPct(d.storage.ratio.excursion_aware)} note={`${fmtRatio(d.storage.ratio.excursion_aware)} · target ≤ 10 %`} />
                <Stat label="Uniform hourly, share of raw" value={fmtPct(d.storage.ratio.uniform_hourly, 2)} note="cheap, but lossy" />
              </div>
              <StorageBars
                bars={[
                  { key: "raw", label: "Raw (everything)", bytes: d.storage.policies.raw },
                  { key: "uniform_hourly", label: "Uniform hourly", bytes: d.storage.policies.uniform_hourly },
                  { key: "excursion_aware", label: "Excursion-aware", bytes: d.storage.policies.excursion_aware, ours: true },
                ]}
              />
              <p className="small muted">Audit trail (rewrite log) adds {fmtBytes(d.storage.policies.audit_trail)}. <a href="#/storage">Storage audit</a></p>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head"><h2>Excursion recall per policy</h2><span className="small muted">excursions ≥ 60 s</span></div>
            <div className="panel-body stack">
              <State loading={hid.loading && !s} error={hid.error} />
              {s && (
                <>
                  <div className="stats">
                    <Stat hero label="Hidden from the hourly store" value={fmtNum(s.hidden)} unit={`of ${fmtNum(s.total)}`} note={`${fmtNum(s.hidden_shipments)} shipments look safe hour by hour`} />
                  </div>
                  <table className="kv">
                    <tbody>
                      <tr><td>Excursion-aware (ours)</td><td className="num"><b>100 %</b> <span className="muted">kept at full resolution by construction</span></td></tr>
                      <tr><td>Raw (everything)</td><td className="num"><b>100 %</b></td></tr>
                      <tr><td>Uniform hourly, mean out of range</td><td className="num"><b>{fmtPct(s.baseline_mean_recall)}</b></td></tr>
                      <tr><td>Uniform hourly, min/max out of range</td><td className="num"><b>{fmtPct(s.baseline_minmax_recall)}</b> <span className="muted">no duration, no peak timing</span></td></tr>
                    </tbody>
                  </table>
                  <p className="small muted"><a href="#/hidden">Hidden excursions</a> lists them with the full-resolution trace.</p>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      <div>
        <h3 className="mb">Demo scenarios</h3>
        <div className="scenarios">
          <a href="#/hidden"><div className="n">1</div><div className="t">Hidden excursion</div><div className="d">Hourly averages look safe; the full trace does not.</div></a>
          <a href="#/custody"><div className="n">2</div><div className="t">Custody attribution</div><div className="d">Who held the shipment at the start, peak and end.</div></a>
          <a href="#/storage"><div className="n">4</div><div className="t">Storage audit</div><div className="d">Bytes per policy, ratio to raw, recall.</div></a>
          <a href="#/route-risk"><div className="n">3</div><div className="t">Route risk</div><div className="d">Legs ranked by excursion-minutes per shipment.</div></a>
          <a href="#/late-data"><div className="n">5</div><div className="t">Late data</div><div className="d">A truck reconnects; a closed window is reopened and split.</div></a>
        </div>
      </div>
    </div>
  );
}
