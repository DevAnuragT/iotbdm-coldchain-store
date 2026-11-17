import { api, fmtDuration, fmtIsoDate, fmtNum, fmtTemp } from "../api";
import { useFetch } from "../hooks";
import { Stat, State } from "../components/Common";

type RunConfig = null | {
  sim?: { period_days?: number };
  engine?: { pre_buffer_s?: number; post_buffer_s?: number };
};

/** Opening page: what the data is and why it needs a different storage policy.
 *  The numbers for storage and recall live on their own pages. */
export function Overview() {
  const ov = useFetch(() => api.overview(), []);
  const d = ov.data;
  const run = d?.run;
  const cfg = (run?.config ?? null) as RunConfig;
  const mins = (s?: number) => (s ? fmtNum(s / 60) : "10");
  const readings = d?.storage.tables.readings_rawstore?.rows ?? run?.readings ?? null;

  return (
    <div className="stack gap-lg">
      <div className="page-head">
        <div>
          <h1>Excursion-aware cold-chain data store</h1>
          <div className="lede">
            Vaccine shipments, factory to clinic, kept between 2 and 8 °C
            {cfg?.sim?.period_days ? ` · ${cfg.sim.period_days}-day simulation` : ""}
            {d?.counts.period_start ? ` · ${fmtIsoDate(d.counts.period_start)} → ${fmtIsoDate(d.counts.period_end)}` : ""}
          </div>
        </div>
      </div>

      <State loading={ov.loading && !d} error={ov.error} />

      {d && (
        <div className={ov.loading ? "is-stale" : ""}>
          <div className="stats lead-wide">
            <Stat hero label="Readings" value={fmtNum(readings)} note="one every 10 s per shipment" />
            <Stat label="Shipments" value={fmtNum(d.counts.shipments)} />
            <Stat label="Excursions" value={fmtNum(d.counts.excursions)} note={`${fmtNum(d.excursions.hot)} hot · ${fmtNum(d.excursions.cold)} cold`} />
            <Stat label="Time out of range" value={fmtDuration(d.excursions.total_s)} note={`longest ${fmtDuration(d.excursions.longest_s)}`} />
            <Stat label="Hottest / coldest" value={<>{fmtTemp(d.excursions.hottest)}<small>/ {fmtTemp(d.excursions.coldest)}</small></>} />
          </div>
        </div>
      )}

      <div className="split even">
        <div className="prose">
          <h3>The problem</h3>
          <p>
            Almost every reading says “still fine”. Keeping all of them grows forever. Keeping one average per hour
            is cheap, but a spike to 11 °C disappears into its hour and reads as safe. The evidence a regulator asks
            for months later is gone.
          </p>
        </div>
        <div className="prose">
          <h3>The idea</h3>
          <p>
            What is kept depends on what happened, not on the clock. Steady stretches collapse into summary windows.
            Every reading from {mins(cfg?.engine?.pre_buffer_s)} minutes before an excursion to{" "}
            {mins(cfg?.engine?.post_buffer_s)} minutes after it stays at full resolution, so nothing about a breach
            is lost.
          </p>
        </div>
      </div>

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
