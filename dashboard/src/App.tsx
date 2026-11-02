import { useRoute } from "./hooks";
import { Overview } from "./views/Overview";
import { Shipments } from "./views/Shipments";
import { Excursions } from "./views/Excursions";
import { Hidden } from "./views/Hidden";
import { RouteRisk } from "./views/RouteRisk";
import { StorageView } from "./views/Storage";
import { LateData } from "./views/LateData";

const NAV: { group: string; items: { view: string; label: string }[] }[] = [
  { group: "", items: [{ view: "overview", label: "Overview" }] },
  {
    group: "Browse",
    items: [
      { view: "shipments", label: "Shipments" },
      { view: "excursions", label: "Excursions" },
    ],
  },
  {
    group: "Scenarios",
    items: [
      { view: "hidden", label: "Hidden excursions" },
      { view: "custody", label: "Custody attribution" },
      { view: "storage", label: "Storage audit" },
      { view: "route-risk", label: "Route risk" },
      { view: "late-data", label: "Late data" },
    ],
  },
];

export function App() {
  const route = useRoute();
  // "custody" is scenario 2; it is the excursion browser with the detail panel open.
  const view = route.view === "custody" ? "excursions" : route.view;
  return (
    <div className="app">
      <nav className="nav" aria-label="Views">
        <div className="brand">
          Cold-chain store
          <span className="sub">excursion-aware</span>
        </div>
        {NAV.map((g) => (
          <div key={g.group || "root"}>
            {g.group && <div className="group">{g.group}</div>}
            {g.items.map((it) => (
              <a key={it.view} href={`#/${it.view}`} aria-current={isActive(route.view, it.view) ? "page" : undefined}>
                {it.label}
              </a>
            ))}
          </div>
        ))}
        <div className="foot">2–8 °C · UTC</div>
      </nav>
      <main className="main">
        {view === "overview" && <Overview />}
        {view === "shipments" && <Shipments route={route} />}
        {view === "excursions" && <Excursions route={route} />}
        {view === "hidden" && <Hidden route={route} />}
        {view === "route-risk" && <RouteRisk route={route} />}
        {view === "storage" && <StorageView route={route} />}
        {view === "late-data" && <LateData route={route} />}
        {!["overview", "shipments", "excursions", "hidden", "route-risk", "storage", "late-data"].includes(view) && (
          <p className="state">No such view. <a href="#/overview">Overview</a></p>
        )}
      </main>
    </div>
  );
}

function isActive(current: string, view: string): boolean {
  if (current === view) return true;
  if (view === "custody" && current === "custody") return true;
  return false;
}
