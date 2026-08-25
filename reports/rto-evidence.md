# RTO/RPO Evidence - Lab 23

Moi so lieu duoi day lay tu drill thuc chay ngay 2026-08-25 va truy duoc ve log JSON/JSONL.

## 1. Drill 1 - khong co DR

| Chi so | Gia tri | Cach do | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T10:21:49Z | Chaos event `action:kill` | `chaos/chaos-events.jsonl:1` |
| Request fail dau tien | +0.0s | Dong `ok:false` dau tien sau t_outage | `reports/drill-1-nodr.jsonl:17` |
| Request thanh cong sau do | Khong co | 14 request sau outage deu fail | `reports/measure-drill-1.json` |
| RTO | NO_RECOVERY | Do bang `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 - co DR

| Moc | +giay tu t_outage | Cach do | Evidence |
|---|---:|---|---|
| t_outage | 0.0s | Event `action:kill`, Region A | `chaos/chaos-events.jsonl:2` |
| User thay loi dau tien | 0.1s | Dong `ok:false` dau tien | `reports/drill-2-withdr.jsonl:25` |
| Health checker phat hien A unhealthy | 14.9s | Loi lien tiep thu ba, `to:UNHEALTHY` | `reports/health-events.jsonl:2` |
| Snapshot restore hoan tat | 13.3s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region B ready | 20.2s | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover sang B | 20.2s | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **Request dau tien phuc hoi tu B** | **25.9s** | Dong `ok:true`, `served_by:b` dau tien sau chuoi loi | `reports/drill-2-withdr.jsonl:36` |

| Chi so | Do duoc | Muc tieu | Verdict | Evidence |
|---|---:|---:|---|---|
| RTO - Inference API | 25.9s | 300s | PASS | `reports/measure-drill-2.json` |
| RPO - Vector DB | 2.0s / 1 document | 300s | PASS | `reports/failover-events.jsonl:2` |

## 3. RTO critical-path breakdown

| Thanh phan | Giay dong gop | Nguon do | Cach giam |
|---|---:|---|---|
| Health-check detection | 14.9s | `interval_s:5.0 x threshold:3`, detect floor 15.0s tai `reports/health-events.jsonl:2` | Giam interval, giu threshold va circuit breaker |
| Snapshot restore | 0.0s | `2_restore_snapshot` den `3_scale_pool`, cung timestamp sau lam tron tai `reports/failover-events.jsonl:2` va `reports/failover-events.jsonl:3` | Incremental snapshot, hot replica, storage nhanh hon |
| GPU warm-up tren critical path | 5.3s | t_cutover 20.2s - t_detect 14.9s; raw `waited_s` 6.86s tai `reports/failover-events.jsonl:4`, co 1.56s overlap detection | Warm capacity hoac preload model |
| DNS/LB TTL cache | 5.7s | t_recovered 25.9s - t_cutover 20.2s, tu `reports/failover-events.jsonl:5` va `reports/drill-2-withdr.jsonl:36` | Giam TTL hoac health-aware global LB |
| **Tong critical path** | **25.9s** | Khop `rto_measured_s` | Duoi muc tieu 300s |

Health checker, runbook va failover co mot phan chay song song. Vi vay raw warm-up 6.86s khong duoc cong lap vao detection; bang dung cac dong gop khong chong lan de tong khop RTO user thuc su trai nghiem.
