import type { SnapWindow } from "../api";
import { fmtClock, fmtNum, fmtShort } from "../api";

/** Compact table of summary windows; rows whose (id, version) is in `changed` are marked. */
export function WindowsTable({ windows, changed, removed }: { windows: SnapWindow[]; changed?: Set<string>; removed?: Set<number> }) {
  if (windows.length === 0) return <p className="state">No summary windows.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Window</th>
            <th className="num">v</th>
            <th>Start</th>
            <th>End</th>
            <th className="num">n</th>
            <th className="num">Mean</th>
            <th className="num">Max</th>
          </tr>
        </thead>
        <tbody>
          {windows.map((w) => {
            const key = `${w.window_id}:${w.version}`;
            const isChanged = changed?.has(key);
            const isRemoved = removed?.has(w.window_id);
            return (
              <tr key={key} className={isRemoved ? "dim" : undefined}>
                <td className="mono">
                  {w.window_id}
                  {isChanged && <> <span className="mark accent">{w.version > 1 ? "rewritten" : "new"}</span></>}
                  {isRemoved && <> <span className="mark">reopened</span></>}
                </td>
                <td className="num">{w.version}</td>
                <td className="mono">{fmtShort(w.start)}</td>
                <td className="mono">{fmtClock(w.end)}</td>
                <td className="num">{fmtNum(w.n)}</td>
                <td className="num">{w.mean.toFixed(2)}</td>
                <td className={"num" + (w.max > 8 ? " red" : "")}>{w.max.toFixed(2)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
