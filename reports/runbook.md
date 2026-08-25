# One-page Runbook - Primary Region Down

Scope: Region A primary, Region B standby, Windows bare mode. RTO/RPO target: 300 seconds. Never cut over before B is ready.

| # | Step | Copy-paste PowerShell command | Completion signal | Owner |
|---|---|---|---|---|
| 1 | Confirm outage | <code>1..3 \| ForEach-Object { curl.exe -sS --max-time 3 http://127.0.0.1:8001/readyz; Start-Sleep 5 }</code> | All three A probes fail/timeout; B healthz reports alive true | SRE on-call |
| 2 | Open incident, start RTO clock, launch one-click failover | <code>python dr\runbook.py --primary a --target b --backend fs</code> | Operator enters y; runbook log contains thong_bao_incident after t_outage | Incident commander |
| 3 | Verify snapshot restore | <code>Select-String -Path reports\failover-events.jsonl -Pattern '2_restore_snapshot' \| Select-Object -Last 1</code> | Event has ok true, rpo_seconds, docs_lost, and embed_model_version | Storage/ML Platform on-call |
| 4 | Verify B pool and state | <code>curl.exe -sS http://127.0.0.1:8002/v1/state</code> | pool_state full, weights true, count above zero; readyz returns HTTP 200 | ML Serving on-call |
| 5 | Verify DNS/LB cutover | <code>curl.exe -sS http://127.0.0.1:8080/edge/state</code> | Within one 5s TTL, active_region is b; step 5 follows step 4 in log | SRE on-call |
| 6 | Verify golden signals | <code>Select-String -Path reports\runbook-run.jsonl -Pattern 'verify_golden_signals' \| Select-Object -Last 1</code> | Ten real requests, error_rate 0.0, p95 below 1000ms | SRE and ML Serving |
| 7 | Measure RTO/RPO and open postmortem | <code>python tools\measure_rto.py --loadgen reports\drill-2-withdr.jsonl --target-rto 300</code> | valid true, empty warnings, RTO PASS, recovery served by B | Incident commander |

## Abort and rollback

- **Abort before cutover** if snapshot/model version is missing, restore fails, B has no weights or vectors, or readyz does not return 200 within 60 seconds. Failover must leave edge unchanged and return ok false.
- **Do not return traffic merely because A restarted.** Keep B active until A state is at least as new as B, readyz is stable for three probes, and golden signals show zero errors with p95 below 1000ms.
- **B-to-A rollback authority:** only the Incident Commander may approve after SRE and ML Serving owners verify A. Snapshot from the correct source, restore and verify A, scale A full, wait for readyz 200, then write a to edge/active_region and monitor for at least two TTL periods.
- If regions flap or the newest state source is unclear, freeze cutover, retain the stable serving region, and open SEV-1. Do not use automatic reverse failover.
