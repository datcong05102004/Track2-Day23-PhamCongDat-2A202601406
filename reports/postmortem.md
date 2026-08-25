# Postmortem - DR Drill Lab 23

Drill date: 2026-08-25. Day la blameless postmortem: muc tieu la tim dieu kien he thong va quy trinh can cai thien.

## 1. Timeline

| ISO time (UTC) | Su kien | Evidence |
|---|---|---|
| 2026-08-25T10:29:08.889Z | Region A bi netblock, outage bat dau | `chaos/chaos-events.jsonl:2` |
| 2026-08-25T10:29:08.976Z | User dau tien nhan 503/ReadTimeout | `reports/drill-2-withdr.jsonl:25` |
| 2026-08-25T10:29:21Z | Operator xac nhan outage va mo incident | `reports/runbook-run.jsonl:1` va `reports/runbook-run.jsonl:2` |
| 2026-08-25T10:29:23.771Z | Health checker danh dau Region A UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-08-25T10:29:29.104Z | Region B ready va DNS cutover duoc xac nhan | `reports/failover-events.jsonl:4` va `reports/failover-events.jsonl:5` |
| 2026-08-25T10:29:34.837Z | Request dau tien thanh cong tu Region B | `reports/drill-2-withdr.jsonl:36` |

## 2. RTO/RPO vs target - gap analysis

- RTO target: 300s; measured: **25.9s**; gap/headroom: **274.1s**. Evidence: `reports/measure-drill-2.json`.
- RPO target: 300s; measured: **2.0s and 1 document lost**; gap/headroom: **298.0s**. Evidence: `reports/failover-events.jsonl:2`.
- Longest stage: health-check detection, 14.9s or 57.5% of RTO. Anti-flapping policy requires three consecutive failures at a five-second interval.
- Raw GPU warm-up was 6.86s. Of that, 1.56s overlapped the final part of detection, so its non-overlapping critical-path contribution was 5.3s.

## 3. Root cause - 5 whys

1. Why did users receive errors? Edge continued routing to Region A while A did not respond.
2. Why did edge not switch immediately? Cutover was correctly gated until outage confirmation and target readiness.
3. Why was B not ready at outage start? B was passive: warm pool, no local weights, and an empty vector DB.
4. Why did recovery need restore? State used periodic snapshots rather than synchronous active-active replication.
5. Why was there still recovery delay? Detection threshold, GPU warm-up, and DNS TTL were all on the critical path. This was a result of anti-flapping, capacity-cost, and caching choices rather than an individual error.

The likely real-outage failure is a missing or stale snapshot without an alert. Failover would correctly abort at `2_restore_snapshot` and avoid unsafe cutover, but RTO could miss its target without another replica.

## 4. Action items

| # | Action item | Owner | Deadline | Expected effect |
|---|---|---|---|---|
| 1 | Alert if no fresh replication event exists for 60s; verify manifest and model version | Storage/ML Platform on-call | 2026-09-01 | Protect RPO <= 30s and avoid restore abort |
| 2 | Test interval 2s, threshold 3 with circuit breaker and canary | SRE | 2026-09-08 | Reduce detection floor from 15s to 6s |
| 3 | Keep one B worker full but out of traffic | ML Serving owner | 2026-09-15 | Remove about 5.3s warm-up contribution |

## 5. Required questions

1. `interval x threshold = 5s x 3 = 15s`. Actual detection was 14.9s, about **57.5%** of the 25.9s RTO.
2. With interval 1s and threshold 3, detection floor becomes 3s, theoretically reducing RTO by about **12s** to 13.9s. The cost is five times more probe load, more transient alerts, and greater flapping risk without hysteresis/circuit breaker.
3. For a six-hour outage with permanent primary loss, `docs_lost` counts documents committed on primary after the newest timestamp present in the restored snapshot. These customer records are absent from B and require source replay/recovery; this can make AI answers incomplete or use stale context.
