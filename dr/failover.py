"""Ordered restore, readiness gate, and DNS cutover for regional failover."""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append one timestamped JSONL event and mirror it to stdout."""
    record = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("FAILOVER", json.dumps(record, ensure_ascii=False), flush=True)
    return record


def state_of(region: str) -> dict:
    response = httpx.get(f"{URL[region]}/v1/state", timeout=2.0)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    """Restore, warm, verify, then cut over to ``target`` in the required order."""
    if target not in URL:
        raise ValueError(f"unknown target region: {target}")
    if wait <= 0:
        raise ValueError("wait must be positive")

    primary = "b" if target == "a" else "a"
    target_dir = pathlib.Path(f"state/region-{target}")
    pool_file = target_dir / "pool_state"
    original_pool = pool_file.read_text().strip() if pool_file.exists() else "cold"

    try:
        before = state_of(target)
    except Exception as exc:
        before = {"region": target, "error": type(exc).__name__}
    emit(step="1_verify_target", target=target, state=before)

    try:
        snapshot_meta = snapshot.get(target, backend)
        rpo = snapshot.rpo(pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
                           target_dir / "vectors.sqlite")
    except BaseException as exc:
        emit(step="2_restore_snapshot", target=target, ok=False,
             error=type(exc).__name__, detail=str(exc))
        return {"ok": False, "target": target, "failed_step": "2_restore_snapshot",
                "error": type(exc).__name__}

    restore_event = emit(
        step="2_restore_snapshot", target=target, ok=True,
        snapshot_at=snapshot_meta.get("snapshot_at"),
        restored_at=snapshot_meta.get("restored_at"),
        rpo_seconds=rpo.get("rpo_seconds"), docs_lost=rpo.get("docs_lost"),
        embed_model_version=snapshot_meta.get("embed_model_version"),
    )

    target_dir.mkdir(parents=True, exist_ok=True)
    pool_file.write_text("full", encoding="utf-8")
    emit(step="3_scale_pool", target=target, from_state=original_pool, to_state="full")

    started = time.monotonic()
    deadline = started + wait
    last_reason = "not_probed"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, wait))
            body = response.json()
            if response.status_code == 200 and body.get("ready") is True:
                ready_state = state_of(target)
                waited = round(time.monotonic() - started, 2)
                emit(step="4_wait_ready", target=target, ok=True,
                     waited_s=waited, state=ready_state)
                pathlib.Path("edge/active_region").write_text(target, encoding="utf-8")
                cutover = emit(step="5_dns_cutover", target=target, ok=True,
                               active_region=target)
                return {
                    "ok": True, "target": target, "before": before,
                    "state": ready_state, "restore": restore_event,
                    "cutover": cutover, "waited_s": waited,
                }
            last_reason = ",".join(body.get("reasons") or []) or f"http_{response.status_code}"
        except Exception as exc:
            last_reason = type(exc).__name__
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    # Abort safely: do not touch DNS, and restore the previous pool intent.
    pool_file.write_text(original_pool, encoding="utf-8")
    waited = round(time.monotonic() - started, 2)
    emit(step="4_wait_ready", target=target, ok=False,
         waited_s=waited, reason=last_reason, aborted=True)
    return {"ok": False, "target": target, "failed_step": "4_wait_ready",
            "reason": last_reason, "waited_s": waited}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="b", choices=["a", "b"])
    parser.add_argument("--backend", default="fs", choices=["fs", "minio"])
    parser.add_argument("--wait", type=float, default=60)
    args = parser.parse_args()
    print(json.dumps(failover(args.target, args.backend, args.wait), indent=2))
