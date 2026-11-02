import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { TooltipProps } from "recharts";
import { api, cap, fmtNum } from "../api";
import type { RouteRiskRow } from "../api";
import type { Route } from "../hooks";
import { href, navigate, useChartTheme, useFetch } from "../hooks";
import { Segmented, State } from "../components/Common";

type Group = "holder" | "type";
const TOP = 10;

/** Scenario 3: legs ranked by excursion-minutes per shipment. */
export function RouteRisk({ route }: { route: Route }) {
  const group = (route.params.get("group") === "type" ? "type" : "holder") as Group;
  const [limit, setLimit] = useState(50);
  const { data, error, loading } = useFetch(() => api.routeRisk({ group, limit }), [group, limit]);
  const theme = useChartTheme();
  const top = (data ?? []).slice(0, TOP);
  const label = (name: string) => (group === "type" ? cap(name) : name);

  const Tip = ({ active, payload }: TooltipProps<number, string>) => {
    if (!active || !payload?.length) return null;
    const r = payload[0].payload as RouteRiskRow;
    return (
      <div className="tip">
        <div className="t">{label(r.name)}{r.region ? ` · ${r.region}` : ""}</div>
        <div><span className="v">{fmtNum(r.minutes_per_shipment, 1)}</span> excursion-min per shipment</div>
        <div className="muted">{fmtNum(r.excursion_minutes)} min over {fmtNum(r.shipments)} shipments · {fmtNum(r.excursions)} excursions</div>
      </div>
    );
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Route risk</h1>
          <div className="lede">Excursion-minutes attributed to each holder while it had custody, per shipment handled. Click a row to list its excursions.</div>
        </div>
        <div className="controls">
          <Segmented<Group> value={group} options={[{ value: "holder", label: "By holder" }, { value: "type", label: "By holder type" }]} onChange={(g) => navigate(href("route-risk", null, { group: g }))} />
          {group === "holder" && (
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Rows">
              <option value={20}>Top 20</option><option value={50}>Top 50</option><option value={100}>Top 100</option><option value={500}>All</option>
            </select>
          )}
        </div>
      </div>

      <State loading={loading && !data} error={error} empty={data?.length === 0}>No custody data.</State>
      {data && data.length > 0 && (
        <div className={"stack gap-lg" + (loading ? " is-stale" : "")}>
          <div>
            <h3 className="mb">Top {top.length} by excursion-minutes per shipment</h3>
            <div style={{ height: top.length * 26 + 30 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={top} layout="vertical" margin={{ top: 0, right: 56, bottom: 0, left: 0 }}>
                  <CartesianGrid horizontal={false} stroke={theme.grid} />
                  <XAxis type="number" tickLine={false} axisLine={{ stroke: theme.border2 }} tickFormatter={(v: number) => fmtNum(v)} />
                  <YAxis type="category" dataKey="name" width={group === "type" ? 80 : 240} tickLine={false} axisLine={false} interval={0} tickFormatter={label} />
                  <Tooltip content={<Tip />} cursor={{ fill: theme.grid }} isAnimationActive={false} />
                  <Bar dataKey="minutes_per_shipment" fill={theme.accent} barSize={14} radius={[0, 4, 4, 0]} isAnimationActive={false}>
                    <LabelList dataKey="minutes_per_shipment" position="right" formatter={(v: number) => fmtNum(v)} style={{ fill: theme.text2, fontSize: 11 }} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="num">#</th><th>{group === "type" ? "Holder type" : "Holder"}</th>
                  {group === "holder" && <><th>Type</th><th>Region</th></>}
                  <th className="num">Shipments</th><th className="num">Excursions</th><th className="num">Excursion min</th><th className="num">Custody h</th><th className="num">Min / shipment</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r, i) => (
                  <tr
                    key={r.holder_id ?? r.name} className="clickable"
                    onClick={() => navigate(href("excursions", null, group === "holder" ? { holder_id: r.holder_id } : { holder_type: r.type }))}
                  >
                    <td className="num muted">{i + 1}</td>
                    <td>{label(r.name)}</td>
                    {group === "holder" && <><td>{cap(r.type)}</td><td>{r.region}</td></>}
                    <td className="num">{fmtNum(r.shipments)}</td>
                    <td className={"num" + (r.excursions ? "" : " muted")}>{fmtNum(r.excursions)}</td>
                    <td className="num">{fmtNum(r.excursion_minutes)}</td>
                    <td className="num">{fmtNum(r.custody_hours)}</td>
                    <td className="num"><b>{fmtNum(r.minutes_per_shipment, 1)}</b></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">Minutes of excursion overlapping the holder's custody, divided by shipments handled. The holder type filter in Excursions uses the holder at excursion start.</p>
        </div>
      )}
    </div>
  );
}
