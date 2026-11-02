import { useMemo } from "react";
import { api, cap, fmtClock, fmtDuration, fmtTemp, fmtTs, fmtShort } from "../api";
import type { CustodyAt } from "../api";
import { href, useFetch } from "../hooks";
import { State } from "./Common";
import { TraceChart } from "./TraceChart";

/** Scenario 2: who held the shipment at the start, peak and end of an excursion,
 *  plus the full-resolution trace against the hourly mean (scenario 1). */
export function ExcursionDetail({ id }: { id: number }) {
  const { data, error, loading } = useFetch(() => api.excursion(id), [id]);
  const domain = useMemo<[number, number] | null>(() => {
    if (!data) return null;
    const raw = data.raw;
    if (raw.length) return [raw[0].ts, raw[raw.length - 1].ts];
    return [data.start_ts - 600, data.end_ts + 600];
  }, [data]);

  if (!data) return <State loading={loading} error={error} />;
  const d = data;
  const latency = d.detected_at - d.start_ts;
  const rows: { at: string; ts: number; c: CustodyAt | null }[] = [
    { at: "Start", ts: d.start_ts, c: d.custody.start },
    { at: "Peak", ts: d.peak_ts ?? d.start_ts, c: d.custody.peak },
    { at: "End", ts: d.end_ts, c: d.custody.end },
  ];
  const sameHolder = rows.every((r) => r.c && r.c.holder_id === rows[0].c?.holder_id);

  return (
    <div className={"stack" + (loading ? " is-stale" : "")}>
      <div className="row spread wrap">
        <div>
          <h2>
            <span className={"kind " + d.kind} />Excursion <span className="mono">{d.excursion_id}</span>
            <span className="text-2" style={{ fontWeight: 400 }}> · {d.kind} · {d.product}</span>
          </h2>
          <div className="small text-2">
            Shipment <a href={href("shipments", d.shipment_id, { ex: d.excursion_id })} className="mono">{d.shipment_id}</a> · open full trace
          </div>
        </div>
        {d.hidden_from_hourly_mean ? (
          <span className="mark red" title="Every hourly mean overlapping this excursion is within 2–8 °C">hidden from hourly mean</span>
        ) : (
          <span className="mark" title="At least one hourly mean overlapping this excursion is outside 2–8 °C">visible in hourly mean</span>
        )}
      </div>

      <div className="stats">
        <div className="stat"><div className="label">Peak</div><div className="value">{fmtTemp(d.peak_c)}</div><div className="note">at {d.peak_ts ? fmtClock(d.peak_ts, true) : "—"}</div></div>
        <div className="stat"><div className="label">Duration</div><div className="value">{fmtDuration(d.duration_s)}</div><div className="note">{fmtShort(d.start_ts)} → {fmtClock(d.end_ts)}</div></div>
        <div className="stat"><div className="label">Detected</div><div className="value">{fmtDuration(latency)}</div><div className="note">after onset</div></div>
        <div className="stat"><div className="label">Raw readings kept</div><div className="value">{d.raw.length}</div><div className="note">incl. 10 min buffers</div></div>
      </div>

      {domain && (
        <TraceChart
          windows={[]} raw={d.raw} hourly={d.hourly} showHourly
          excursions={[{ excursion_id: d.excursion_id, start_ts: d.start_ts, end_ts: d.end_ts }]}
          selectedExcursion={d.excursion_id} peakTs={d.peak_ts} domain={domain} height={240}
        />
      )}

      <div>
        <h3>Custody</h3>
        <div className="table-wrap mt-sm">
          <table>
            <thead>
              <tr><th>At</th><th>Time</th><th>Holder</th><th>Held from</th><th>Until</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.at}>
                  <td>{r.at}</td>
                  <td className="mono">{fmtTs(r.ts)}</td>
                  <td>{r.c ? <>{r.c.name} <span className="muted">{cap(r.c.holder_type)} · {r.c.region}</span></> : <span className="muted">unknown</span>}</td>
                  <td className="mono">{r.c ? fmtShort(r.c.valid_from) : "—"}</td>
                  <td className="mono">{r.c ? fmtShort(r.c.valid_to) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted mt-sm">
          {sameHolder && rows[0].c
            ? `Single holder throughout: ${rows[0].c.name}.`
            : "Custody changed during the excursion; handover timestamps above."}
        </p>
      </div>

      {d.rewrites.length > 0 && (
        <div>
          <h3>Rewrites near this excursion</h3>
          <div className="table-wrap mt-sm">
            <table>
              <thead><tr><th>Window</th><th>Version</th><th>Reason</th><th>Late reading</th><th>Detail</th></tr></thead>
              <tbody>
                {d.rewrites.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.window_id}</td>
                    <td className="num">v{r.old_version} → v{r.new_version}</td>
                    <td>{r.reason}</td>
                    <td className="mono">{fmtTs(r.late_reading_ts)}</td>
                    <td style={{ whiteSpace: "normal" }} className="small text-2">{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
