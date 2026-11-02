# Benchmark s42-n500-x0.02-l0.05

500 shipments, 10,562,566 readings, 1216 injected excursions ≥ 60 s. Ingest 69,679 readings/s (all three policies written in the same pass).

| Metric | Raw | Uniform hourly | Excursion-aware |
|---|---|---|---|
| Bytes stored | 1.3 GB (100%) | 4.8 MB (0.37%) | 77.5 MB (5.99%) |
| Bytes, TimescaleDB compressed | 115.9 MB (100%) | 1.7 MB (1.48%) | 13.5 MB (11.65%) |
| Excursion recall (start, end, peak within 10 s) | 100% | 30.1% by hourly mean, 100.0% by hourly min/max (hour-level only) | 100.0% |
| Peak error (MAE) | 0.00 °C | 3.55 °C (hourly mean vs true peak) | 0.0000 °C |
| p95 latency, scenario 1 (hidden excursions) | – | – | 16 ms |
| p95 latency, scenario 2 (excursion list + custody) | – | – | 9 ms |
| p95 latency, scenario 2 (excursion detail, custody at start/peak/end) | – | – | 4 ms |
| p95 latency, scenario 3 (route risk ranking) | – | – | 10 ms |
| p95 latency, full shipment trace | 32 ms | 1 ms | 3 ms |
| Late-data visibility bound | n/a | n/a | ≤ 60 s after arrival (30 s quiet / 60 s max); 224 replays, 862 rewrites logged, 0 unreconstructable |
| Ingestion throughput | 69,679 readings/s (single pass, all policies) | same | same |
