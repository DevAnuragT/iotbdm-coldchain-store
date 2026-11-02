import { fmtBytes, fmtPct } from "../api";

export type PolicyBar = { key: string; label: string; bytes: number; ours?: boolean; note?: string };

/** One horizontal bar per storage policy as a share of raw. Linear scale, exact
 *  bytes printed, 10 % target drawn as a reference line. */
export function StorageBars({ bars, target = 0.1 }: { bars: PolicyBar[]; target?: number }) {
  const base = bars.find((b) => b.key === "raw")?.bytes ?? Math.max(...bars.map((b) => b.bytes), 1);
  return (
    <div>
      <div className="hbars">
        {bars.map((b) => {
          const r = base ? b.bytes / base : 0;
          return (
            <div className="hbar" key={b.key}>
              <div className="ellipsis" title={b.note}>{b.label}</div>
              <div className="track" aria-label={`${b.label}: ${fmtBytes(b.bytes)}, ${fmtPct(r)} of raw`}>
                <div className={"fill" + (b.ours ? " ours" : "")} style={{ width: `${Math.min(100, r * 100)}%` }} />
                <div className="target" style={{ left: `${target * 100}%` }} title={`${fmtPct(target, 0)} target`} />
              </div>
              <div className="val">
                <b>{fmtBytes(b.bytes)}</b> · {fmtPct(r, r < 0.01 ? 2 : 1)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="hbar-foot">
        <div />
        <div className="ticks">
          <span className="target-label" style={{ left: `${target * 100}%` }}>{fmtPct(target, 0)} target</span>
          <span className="end-label" style={{ left: "100%" }}>100 % of raw</span>
        </div>
        <div />
      </div>
    </div>
  );
}
