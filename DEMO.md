# Demo script

Target length: 8 minutes of talking, 2 minutes of questions. Scenarios 1, 2 and 4
are the core (PRD order); 3 and 5 only if there is time. Rehearse once with a
timer — the clicks are fast, the risk is talking too long on the problem.

## Before the demo (10 minutes, do it before you walk in)

```
cd IoTBDM
docker compose up -d            # db, api, dashboard — wait for "healthy"
# first time only, or if the volume is empty:
docker compose --profile load run loader      # ~3 min, 500 shipments
coldchain bench --compress                    # ~1 min; fills the Storage view's benchmark table
```

Open these tabs, in order, and leave them open:

1. http://localhost:3100/?theme=light#/hidden
2. http://localhost:3100/?theme=light#/custody
3. http://localhost:3100/?theme=light#/storage
4. http://localhost:3100/?theme=light#/route-risk?group=type
5. http://localhost:3100/?theme=light#/late-data

`?theme=light` is for projectors; drop it on a good screen. Check tab 1 shows a
number in the hundreds (not "Could not load") — if not, `docker compose restart api`.

Fallback if anything is down: `reports/benchmark-*.md` has the numbers,
`reports/*.png` the plots, and `coldchain demo late-data` prints scenario 5 in
the terminal in 10 seconds without the UI.

## 0. Opening (45 s) — no slides, just say it

> Vaccines must stay between 2 and 8 °C. A sensor logs every 10 seconds, so a
> national programme produces billions of readings a year, and almost all of
> them say "4 °C, still fine". Today you either keep everything, which never
> stops growing, or you keep one average per hour, which destroys the evidence
> of the one shipment that spoiled. We built a store that keeps under a tenth of
> the raw data and provably never loses an excursion. The idea: let the *domain
> event* decide what is kept, not the clock.

## 1. Hidden excursion (2 min) — tab 1

**Show:** the big number at the top: *850 of 1,216 excursions are invisible to
an hourly-mean store*. Then the list, sorted by severity.

**Say:**
> Every row here is a shipment whose hourly averages are all inside 2–8 °C. The
> hourly store says they are fine. They are not.

**Click** the first row. Point at the chart: red full-resolution spike, flat gray
hourly line underneath.

> This one spent 47 minutes above 12 °C. Averaged into its hour that reads as
> 6 °C — safe. We kept every 10-second reading from 10 minutes before it started
> to 10 minutes after it ended, and summarised everything else into windows:
> the translucent band is a window — start, end, count, min, max, mean, sd.

**If asked "but hourly min/max would catch it":** yes, min/max flags the *hour*
for 100 % of them (we report that number too) — but it cannot tell you when it
started, how long it lasted, or the peak, which is what a regulator asks.

## 2. Custody attribution (2 min) — tab 2

**Show:** the excursion detail panel: holder at **start**, **peak**, **end**
with "held from / until" timestamps; the custody strip under the chart.

**Say:**
> Second question an insurer asks: who had it. Custody is stored as half-open
> time ranges with a GiST range index, so "who held shipment S at instant T" is
> one index lookup. Start was on the truck, peak was on the truck, the end was
> in the depot — so the handover happened during the excursion, and the
> timestamps say exactly when.

**Click** `Route risk` tab (tab 4) only if ahead of time:
> Across all 500 shipments, trucks account for 42 excursion-minutes per
> shipment, factories one. That query runs in 10 ms.

## 3. Storage audit (2 min) — tab 3

**Show:** the three bars (raw / uniform hourly / excursion-aware) with the
dashed 10 % line, the exact bytes, and recall next to each.

**Say:**
> This is the trade-off in one picture. Raw: 1.3 GB, recall 100 %. Hourly:
> 4.8 MB, recall 30 %. Ours: 77 MB — 6 % of raw — recall 100 %, peak error zero.
> Storage grows with excursion-minutes, not shipment-minutes, so it is bounded
> by how bad the cold chain is, not by how long you run it.

**Be upfront (graders like it):**
> Two honest numbers. With TimescaleDB's columnar compression applied to both
> stores, the gap narrows to 8.5× — a perfectly regular 10-second series
> compresses extremely well. And at a 10 % excursion rate we would store 18 %
> of raw; the sensitivity sweep is in the report.

Scroll to the benchmark table if someone asks about latency: every scenario
query is under 20 ms at p95 against a 2 s budget.

## 4. Late data (1.5 min, if time) — tab 5

**Click** `Run scenario` (3 h delay). ~5 seconds.

**Say while it runs:**
> A truck loses connectivity and re-uploads three hours later, and the upload
> contains an excursion inside a summary window we already closed. Uniform
> downsampling cannot do anything with that. We stage the last 12 hours per
> active shipment, reopen the window, replay the readings through the detector,
> split it, and write an audit entry.

**Show:** before: 4 windows, 0 excursions. After: the reopened window at
version 3, the excursion found, raw rows written, and the two rewrite-log lines
(`merge`, `split`). 

> Every rewrite is logged with old and new version — that is the audit trail a
> regulator reads months later.

## 5. Close (30 s)

> What is new here is not the detector — alerting products have that. It is the
> storage lifecycle: compaction driven by domain semantics, buffered raw
> evidence, late-arrival reconciliation with an audit trail, and query-time
> stitching. Ten percent of the bytes, none of the evidence lost.

## Questions you should expect

- **"This already exists."** Commercial cold-chain products alert in real time
  and dump history in a bucket; no time-series database compacts by domain
  event. Lead with the storage-vs-recall benchmark and the rewrite log.
- **"Why 60 seconds / 10 minutes?"** WHO guidance and configurable; the sweep
  shows buffer length moves storage between 2.5 % and 6.7 %, recall stays 100 %.
- **"What about data later than 12 hours?"** It is stored raw and logged as
  `beyond_horizon`; the window's min/max widens so it is visible, but we do not
  invent an excursion whose extent we cannot know. The simulator's 6 h bound
  never triggers it (0 in every run).
- **"Does the simulator cheat?"** Ground truth is the detector run over the
  complete in-order series; the engine has to reproduce it from an
  arrival-ordered stream with 5 % of readings late in bursts. The simulator is
  seeded, so everything in the report reproduces exactly.
- **"Throughput?"** 70 k readings/s for all three policies in one pass
  including Postgres writes, 200 k+/s for the engine alone, in pure Python.
