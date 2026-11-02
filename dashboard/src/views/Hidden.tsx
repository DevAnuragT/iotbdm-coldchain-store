import { useEffect, useState } from "react";
import { api, fmtNum, fmtPct } from "../api";
import type { Route } from "../hooks";
import { href, navigate, useFetch } from "../hooks";
import { Pager, Stat, State } from "../components/Common";
import { ExcursionDetail } from "../components/ExcursionDetail";
import { ExcursionTable } from "./Excursions";

const LIMIT = 50;

/** Scenario 1: excursions that an hourly-mean store cannot see. */
export function Hidden({ route }: { route: Route }) {
  const [offset, setOffset] = useState(0);
  const { data, error, loading } = useFetch(() => api.hidden({ limit: LIMIT, offset }), [offset]);
  const s = data?.summary;

  useEffect(() => {
    if (route.id == null && data?.items.length) navigate(href("hidden", data.items[0].excursion_id));
  }, [route.id, data]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Hidden excursions</h1>
          <div className="lede">Excursions whose overlapping hourly means all sit inside 2–8 °C. A uniform-hourly store would report these shipments as safe.</div>
        </div>
      </div>

      <State loading={loading && !data} error={error} />
      {s && (
        <div className={"stats mb" + (loading ? " is-stale" : "")}>
          <Stat hero label="Hidden from hourly mean" value={fmtNum(s.hidden)} unit={`of ${fmtNum(s.total)} excursions`} note={`${fmtNum(s.hidden_shipments)} shipments look safe hour by hour`} />
          <Stat label="Hourly-mean recall" value={fmtPct(s.baseline_mean_recall)} note="uniform hourly store" />
          <Stat label="Hourly min/max recall" value={fmtPct(s.baseline_minmax_recall)} note="flags the hour, loses timing and duration" />
          <Stat label="Excursion-aware recall" value="100 %" note="full resolution kept around every excursion" />
        </div>
      )}

      <div className="split">
        <div>
          {data && data.items.length === 0 && <p className="state">No hidden excursions.</p>}
          {data && data.items.length > 0 && (
            <div className={loading ? "is-stale" : ""}>
              <ExcursionTable items={data.items} selected={route.id} onSelect={(id) => navigate(href("hidden", id))} />
              <Pager total={s?.hidden ?? data.items.length} limit={LIMIT} offset={offset} onChange={setOffset} />
            </div>
          )}
        </div>
        <div className="sticky">
          {route.id ? (
            <>
              <p className="small text-2 mb">Gray step: what the hourly store keeps. Blue and red: what we keep.</p>
              <ExcursionDetail id={route.id} />
            </>
          ) : (
            <p className="state">Select an excursion.</p>
          )}
        </div>
      </div>
    </div>
  );
}
