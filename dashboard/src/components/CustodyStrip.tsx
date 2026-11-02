import type { Custody } from "../api";
import { fmtShort, fmtTs, fmtDuration, cap } from "../api";

const TRANSIT = new Set(["truck", "airport"]);

/** Holder segments on the same time axis as the trace chart above it.
 *  `padLeft`/`padRight` must match the chart's plot-area insets. */
export function CustodyStrip({
  custody, domain, padLeft, padRight, activeTs,
}: { custody: Custody[]; domain: [number, number]; padLeft: number; padRight: number; activeTs?: number | null }) {
  const [from, to] = domain;
  const span = Math.max(1, to - from);
  const visible = custody.filter((c) => c.valid_to > from && c.valid_from < to);
  return (
    <div style={{ marginLeft: padLeft, marginRight: padRight }}>
      <div className="custody" role="list" aria-label="Custody">
        {visible.map((c) => {
          const a = Math.max(from, c.valid_from);
          const b = Math.min(to, c.valid_to);
          const left = ((a - from) / span) * 100;
          const width = ((b - a) / span) * 100;
          const active = activeTs != null && activeTs >= c.valid_from && activeTs < c.valid_to;
          const title = `${c.name} (${c.holder_type}, ${c.region})\n${fmtTs(c.valid_from)} → ${fmtTs(c.valid_to)} · ${fmtDuration(c.valid_to - c.valid_from)}`;
          return (
            <span
              key={c.valid_from}
              role="listitem"
              className={"seg" + (TRANSIT.has(c.holder_type) ? " transit" : "") + (active ? " active" : "")}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={title}
            >
              {c.name.toLowerCase().includes(c.holder_type) ? <b>{c.name}</b> : <><b>{cap(c.holder_type)}</b> {c.name}</>}
            </span>
          );
        })}
      </div>
      <div className="custody-axis">
        <span>{fmtShort(from)}</span>
        <span>{fmtShort(to)}</span>
      </div>
    </div>
  );
}
