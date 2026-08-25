"""Readiness-based health checker with consecutive-failure anti-flapping."""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Return readiness and a compact, log-friendly reason."""
    try:
        response = httpx.get(f"{URL[region]}/readyz", timeout=timeout)
        body = response.json()
        if response.status_code == 200 and body.get("ready") is True:
            return True, "ready"
        reasons = body.get("reasons") or []
        detail = ",".join(str(reason) for reason in reasons) or f"http_{response.status_code}"
        return False, detail
    except Exception as exc:
        return False, type(exc).__name__


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Poll both regions and log only thresholded state transitions."""
    if interval <= 0 or timeout <= 0 or threshold <= 0 or duration < 0:
        raise ValueError("interval, timeout, threshold must be positive; duration cannot be negative")

    out.parent.mkdir(parents=True, exist_ok=True)
    state = {region: "HEALTHY" for region in URL}
    consecutive_fails = {region: 0 for region in URL}
    deadline = time.monotonic() + duration
    next_poll = time.monotonic()

    with out.open("a", encoding="utf-8") as log:
        while time.monotonic() < deadline:
            for region in URL:
                ready, reason = probe(region, timeout)
                consecutive_fails[region] = 0 if ready else consecutive_fails[region] + 1
                target_state = "HEALTHY" if ready else (
                    "UNHEALTHY" if consecutive_fails[region] >= threshold else state[region]
                )
                if target_state != state[region]:
                    record = {
                        "ts": time.time(), "region": region, "event": "state_change",
                        "from": state[region], "to": target_state, "reason": reason,
                        "consecutive_fails": consecutive_fails[region],
                        "interval_s": interval, "threshold": threshold,
                    }
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
                    log.flush()
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                    state[region] = target_state

            next_poll += interval
            sleep_for = min(max(0.0, next_poll - time.monotonic()),
                            max(0.0, deadline - time.monotonic()))
            if sleep_for:
                time.sleep(sleep_for)
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--threshold", type=int, default=3)
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--out", default="reports/health-events.jsonl")
    args = parser.parse_args()
    run(args.interval, args.timeout, args.threshold, args.duration, pathlib.Path(args.out))
