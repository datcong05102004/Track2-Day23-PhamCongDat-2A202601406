"""Seven-step semi-automated runbook for a primary-region outage."""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Append one timestamped runbook event."""
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "step": n, "name": name, **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("RUNBOOK", json.dumps(record, ensure_ascii=False), flush=True)
    return record


def confirm(auto: bool, msg: str) -> bool:
    """Auto-confirm only for CI; interactive operation defaults safely to no."""
    if auto:
        return True
    return input(f"{msg} [y/N] ").strip().lower() == "y"


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Execute the required seven operational steps."""
    started = time.time()

    probe_results = []
    next_probe = time.monotonic()
    for attempt in range(1, 4):
        try:
            response = httpx.get(f"{URL[primary]}/readyz", timeout=2.0)
            ready = response.status_code == 200
            reason = "ready" if ready else f"http_{response.status_code}"
        except Exception as exc:
            ready, reason = False, type(exc).__name__
        probe_results.append({"attempt": attempt, "ready": ready, "reason": reason})
        if attempt < 3:
            next_probe += 5.0
            time.sleep(max(0.0, next_probe - time.monotonic()))

    try:
        target_alive = httpx.get(f"{URL[target]}/healthz", timeout=2.0).status_code == 200
    except Exception:
        target_alive = False
    outage_confirmed = all(not item["ready"] for item in probe_results)
    step(1, "xac_nhan_outage", primary=primary, target=target,
         confirmed=outage_confirmed, probes=probe_results, target_alive=target_alive)
    if not outage_confirmed or not target_alive:
        return {"ok": False, "failed_step": "xac_nhan_outage",
                "target_alive": target_alive, "probes": probe_results}

    chaos_log = pathlib.Path("chaos/chaos-events.jsonl")
    outage_event = None
    if chaos_log.exists():
        for line in chaos_log.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("action") == "kill" and event.get("region") == primary:
                outage_event = event
    incident = step(2, "thong_bao_incident", primary=primary, severity="SEV-1",
                    t_outage=outage_event.get("ts") if outage_event else None,
                    t_outage_iso=outage_event.get("iso") if outage_event else None)
    if not confirm(auto, f"Fail over region-{primary} to region-{target}?"):
        step(2, "operator_cancelled", primary=primary, target=target)
        return {"ok": False, "failed_step": "operator_confirmation"}

    result = fo.failover(target, backend, wait=60.0)  # exactly one invocation
    step(3, "scale_gpu_pool", target=target, failover_ok=result.get("ok"),
         failed_step=result.get("failed_step"))
    if not result.get("ok"):
        return {"ok": False, "failed_step": result.get("failed_step"),
                "failover": result}

    replica = result.get("state") or {}
    replica_ok = bool(replica.get("weights")) and int(replica.get("count", 0)) > 0
    step(4, "verify_state_replica", target=target, ok=replica_ok,
         vector_count=replica.get("count"), weights=replica.get("weights"),
         pool_state=replica.get("pool_state"))

    cutover = result.get("cutover") or {}
    cutover_ok = cutover.get("active_region") == target and cutover.get("ok") is True
    step(5, "dns_cutover", target=target, ok=cutover_ok,
         active_region=cutover.get("active_region"))

    latencies = []
    errors = 0
    for request_no in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer",
                                 params={"q": f"golden signal {request_no}"}, timeout=3.0)
            if response.status_code != 200:
                errors += 1
        except Exception:
            errors += 1
        latencies.append((time.monotonic() - t0) * 1000)
    ordered = sorted(latencies)
    p95_index = max(0, int(0.95 * len(ordered) + 0.9999) - 1)
    p95 = round(ordered[p95_index], 1)
    error_rate = round(errors / len(latencies), 3)
    golden_ok = error_rate == 0 and p95 < 1000
    step(6, "verify_golden_signals", target=target, ok=golden_ok,
         requests=len(latencies), p95_ms=p95, error_rate=error_rate,
         thresholds={"p95_ms": 1000, "error_rate": 0})

    elapsed = round(time.time() - started, 2)
    step(7, "post_incident", ok=replica_ok and cutover_ok and golden_ok,
         elapsed_s=elapsed,
         measure_command="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {"ok": replica_ok and cutover_ok and golden_ok, "target": target,
            "incident": incident, "failover": result, "p95_ms": p95,
            "error_rate": error_rate, "elapsed_s": elapsed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", default="a")
    parser.add_argument("--target", default="b")
    parser.add_argument("--backend", default="fs", choices=["fs", "minio"])
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.primary, args.target, args.backend, args.auto), indent=2))
