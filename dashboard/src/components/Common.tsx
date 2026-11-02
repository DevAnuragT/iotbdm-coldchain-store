import type { ReactNode } from "react";

export function Stat({ label, value, unit, note, hero }: { label: string; value: ReactNode; unit?: string; note?: ReactNode; hero?: boolean }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={"value" + (hero ? " hero" : "")}>
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {note && <div className="note">{note}</div>}
    </div>
  );
}

/** Quiet loading / error / empty text. Returns null when there is nothing to say. */
export function State({ loading, error, empty, children }: { loading?: boolean; error?: string; empty?: boolean; children?: ReactNode }) {
  if (error) return <p className="state error">Could not load: {error}</p>;
  if (loading) return <p className="state">Loading…</p>;
  if (empty) return <p className="state">{children ?? "Nothing to show."}</p>;
  return null;
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit && offset === 0) return <div className="pager">{total.toLocaleString()} rows</div>;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="pager">
      <button onClick={() => onChange(Math.max(0, offset - limit))} disabled={offset === 0}>Previous</button>
      <button onClick={() => onChange(offset + limit)} disabled={offset + limit >= total}>Next</button>
      <span className="tnum">{from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}</span>
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <span className="seg" role="group">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

export type LegendItem = { label: string; color: string; kind?: "line" | "thin" | "step" | "swatch" };

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="legend" aria-label="Legend">
      {items.map((it) => (
        <span className="key" key={it.label}>
          {it.kind === "swatch" ? (
            <span className="swatch" style={{ background: it.color }} />
          ) : (
            <span className={"line " + (it.kind ?? "")} style={{ borderTopColor: it.color }} />
          )}
          {it.label}
        </span>
      ))}
    </div>
  );
}
