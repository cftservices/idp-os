"""Demo scenario: a sticking steam control valve, for an 8D with a validated root cause.

The valve is modelled with the factory's existing cook_undertemp fault (it caps the reached
cook temperature). Phases, counted in completed batches (verdict set) after the start of a cycle:

  stable       25 batches, no fault                  -> base period for the control limits
  drift        10 batches, magnitude 0.06 (~-1 C)     -> still in spec, a control chart should see it
  out_of_spec   2 batches, magnitude 0.5 (~-9 C)      -> viscosity below 150 cP, verdict REJECTED
  repaired     15 batches, fault cleared              -> verification of the corrective action

Two modes:

  (no argument)  one cycle on SCENARIO_LINE via OPCUA_URL, answer key in SCENARIO_LOG. This is
                 the original 8D run of 17-11-2026 and behaves as before.
  --loop         cycles forever, alternating the lines in SCENARIO_LINES (default L1,L2), so the
                 demo keeps a recurring quality problem that gets solved. Answer key in
                 SCENARIO_LOOP_LOG; every record carries cycle, line and phase, and the cycle
                 number resumes from that file after a restart.

The batch engine and the MES are not told anything: they only see what the factory does. The
answer key is kept apart from the data an investigator works with.

Safety: the fault is cleared on both lines at start, on SIGTERM/SIGINT and on any unexpected
exit. Engine and OPC-UA errors are retried with backoff (5-60 s) and never end the script with a
fault left active. A factory restart wipes its fault from memory, so after the engine has been
unreachable the intended fault of the current phase is set again. Known gap: a factory restart
while the engine stays reachable goes unnoticed until the next phase change (at most one phase,
~25 min); the factory exposes no fault state to check against.

Demo pause: while the engine reports the line paused (GET /demo/pause) the fault is cleared and
the cycle waits; when the line comes back the phase's fault is set again. Only batches the
autopilot started count (created_by), so a presenter's batch never lands in the answer key.
Runs in its own container on the milkdemo network with the factory image (asyncua inside):
compose service `vla-scenario` in docker-compose.vla.yml (profile "scenario").
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from asyncua import Client, ua

ENGINE = os.environ.get("BATCH_ENGINE_URL", "http://vla-batch-engine:8000/api/v1")
LINE = os.environ.get("SCENARIO_LINE", "L1")
LINES = [x.strip() for x in os.environ.get("SCENARIO_LINES", "L1,L2").split(",") if x.strip()]
OPCUA_BY_LINE = {
    "L1": os.environ.get("SCENARIO_OPCUA_L1", "opc.tcp://vla-factory:4840/DairyWorks"),
    "L2": os.environ.get("SCENARIO_OPCUA_L2", "opc.tcp://vla-factory-l2:4840/DairyWorks"),
}
OPCUA = os.environ.get("OPCUA_URL", OPCUA_BY_LINE.get(LINE, OPCUA_BY_LINE["L1"]))
LOG = os.environ.get("SCENARIO_LOG", "/logs/steam-valve-8d.jsonl")
LOOP_LOG = os.environ.get("SCENARIO_LOOP_LOG", "/logs/steam-valve-loop.jsonl")
POLL_S = float(os.environ.get("SCENARIO_POLL_S", "20"))
CALL_TIMEOUT_S = 20.0
BACKOFF_MIN_S, BACKOFF_MAX_S = 5.0, 60.0
BATCH = "ns=2;s=DairyWorks.Vla.Batch"
FAULT = "cook_undertemp"
AUTOPILOT = "autopilot"   # created_by of the batches this scenario counts

PHASES = [("stable", None, 25), ("drift", 0.06, 10), ("out_of_spec", 0.5, 2), ("repaired", "clear", 15)]

stop = threading.Event()
_log_path = LOG


class Stopped(Exception):
    """Raised inside a wait when SIGTERM/SIGINT asked the script to stop."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **kw) -> None:
    rec = {"ts": now(), "event": event, **kw}
    print(json.dumps(rec), flush=True)
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def completed_since(line: str, start: str) -> list[dict]:
    """COMPLETE batches of `line` since `start` whose verdict is already set.

    The engine marks a batch COMPLETE before it writes the verdict, so a batch without one is
    left for the next poll instead of being logged with verdict None.
    """
    with urllib.request.urlopen(f"{ENGINE}/batches?line={line}", timeout=CALL_TIMEOUT_S) as r:
        rows = json.load(r)
    # Only the autopilot's batches: a presenter's batch during a demo pause ran without the
    # fault and does not belong in the answer key. An engine without created_by counts all.
    done = [b for b in rows if b.get("state") == "COMPLETE" and b.get("verdict")
            and (b.get("completed_at") or "") >= start
            and b.get("created_by", AUTOPILOT) == AUTOPILOT]
    return sorted(done, key=lambda b: b["completed_at"])


def line_paused(line: str) -> bool:
    """True while a presenter has the line (engine demo pause). An engine without the
    endpoint (404) has no pause."""
    try:
        with urllib.request.urlopen(f"{ENGINE}/demo/pause", timeout=CALL_TIMEOUT_S) as r:
            return bool((json.load(r).get(line) or {}).get("paused"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


async def _call(url: str, action) -> int:
    async with Client(url, timeout=CALL_TIMEOUT_S) as cl:
        node = cl.get_node(BATCH)
        if action == "clear":
            return await node.call_method(cl.get_node(f"{BATCH}.ClearFault"))
        return await node.call_method(cl.get_node(f"{BATCH}.InjectFault"),
                                      ua.Variant(FAULT, ua.VariantType.String),
                                      ua.Variant(float(action), ua.VariantType.Double))


def factory_call(url: str, action, **ctx) -> int:
    """One OPC-UA call. ClearFault answers 1 when no fault was active: that is a success too."""
    rc = asyncio.run(asyncio.wait_for(_call(url, action), CALL_TIMEOUT_S * 1.5))
    rc = int(rc) if rc is not None else -1
    if action == "clear" and rc not in (0, 1):
        raise RuntimeError(f"ClearFault rc={rc}")
    if action != "clear" and rc != 0:
        raise RuntimeError(f"InjectFault rc={rc}")
    log("factory_call", action=action, rc=rc, **ctx)
    return rc


def with_retry(fn, what: str, on_recover=None, **ctx):
    """Call fn() until it succeeds; back off 5..60 s and log only the first failure of a streak.

    on_recover runs once after a streak of failures ends (used to set the fault again, because
    a factory that was unreachable may have restarted and forgotten it).
    """
    backoff, failures = BACKOFF_MIN_S, 0
    while True:
        if stop.is_set():
            raise Stopped()
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001 - transient by design, logged and retried
            failures += 1
            if failures == 1 or failures % 20 == 0:
                log(f"{what}_unreachable", error=str(e)[:200], failures=failures, **ctx)
            if stop.wait(backoff):
                raise Stopped()
            backoff = min(BACKOFF_MAX_S, backoff * 2)
            continue
        if failures:
            log(f"{what}_recovered", failures=failures, **ctx)
            if on_recover is not None:
                on_recover()
        return out


def clear_all(lines_urls: dict[str, str], deadline_s: float = 20.0) -> None:
    """Best effort: clear the fault on every line, ignoring stop, within a deadline.

    The lines are tried in turn, not one after the other: a factory that stays down must not
    use up the whole deadline while the other line still has its fault active. A failure is
    logged once per line; a line still not reached at the deadline gets `cleanup_gave_up`.
    """
    end = time.monotonic() + deadline_s
    left = dict(lines_urls)
    failed: set[str] = set()
    while left:
        for line, url in list(left.items()):
            try:
                factory_call(url, "clear", line=line, reason="cleanup")
                del left[line]
            except Exception as e:  # noqa: BLE001
                if line not in failed:
                    failed.add(line)
                    log("cleanup_failed", line=line, error=str(e)[:200])
        if not left or time.monotonic() >= end:
            break
        time.sleep(2)
    for line in left:
        log("cleanup_gave_up", line=line, deadline_s=deadline_s)


def run_cycle(line: str, url: str, start: str, cycle: int | None = None) -> int:
    """Run the four phases on one line. Returns the number of batches seen."""
    ctx = {"line": line} if cycle is None else {"line": line, "cycle": cycle}
    intended = {"action": "clear"}

    def assert_intended():
        a = intended["action"]
        with_retry(lambda: factory_call(url, a, reason="reassert", **ctx), "factory", **ctx)

    log("cycle_start" if cycle is not None else "scenario_start", start=start, phases=PHASES, **ctx)
    with_retry(lambda: factory_call(url, "clear", phase="start", **ctx), "factory", **ctx)
    phase_i = 0
    seen: set[str] = set()
    paused = False
    while phase_i < len(PHASES):
        name, _action, _n = PHASES[phase_i]
        # Demo pause: a presenter has this line. Clear the fault once and hold the cycle;
        # set the phase's fault again when the line comes back.
        if with_retry(lambda: line_paused(line), "engine", **ctx):
            if not paused:
                paused = True
                with_retry(lambda: factory_call(url, "clear", reason="demo_pause", **ctx),
                           "factory", **ctx)
                log("demo_paused", phase=name, **ctx)
            if stop.wait(POLL_S):
                raise Stopped()
            continue
        if paused:
            paused = False
            log("demo_resumed", phase=name, **ctx)
            assert_intended()
        done = with_retry(lambda: completed_since(line, start), "engine",
                          on_recover=assert_intended, **ctx)
        for b in done:
            if b["batch_id"] not in seen:
                seen.add(b["batch_id"])
                log("batch_completed", batch_id=b["batch_id"], phase=name, n=len(seen),
                    end_viscosity_cP=b.get("end_viscosity_cP"), verdict=b.get("verdict"), **ctx)
        target = sum(p[2] for p in PHASES[: phase_i + 1])
        if len(done) >= target:
            phase_i += 1
            if phase_i < len(PHASES):
                nxt, nxt_action, _ = PHASES[phase_i]
                magnitude = nxt_action if isinstance(nxt_action, float) else None
                log("phase_change", to=nxt, after_batches=len(done), magnitude=magnitude, **ctx)
                if nxt_action is not None:
                    intended["action"] = nxt_action
                    with_retry(lambda: factory_call(url, nxt_action, phase=nxt, **ctx),
                               "factory", **ctx)
            continue
        if stop.wait(POLL_S):
            raise Stopped()
    log("cycle_end" if cycle is not None else "scenario_end", batches=len(seen), **ctx)
    return len(seen)


def last_cycle(path: str) -> int:
    """Highest cycle number in the loop answer key, 0 when there is none."""
    best = 0
    try:
        with open(path, encoding="utf-8") as f:
            for row in f:
                try:
                    best = max(best, int(json.loads(row).get("cycle") or 0))
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        pass
    return best


def _install_signals() -> None:
    def handler(signum, _frame):
        log("stop_requested", signal=signal.Signals(signum).name)
        stop.set()
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main_single() -> None:
    urls = {LINE: OPCUA}
    try:
        run_cycle(LINE, OPCUA, os.environ.get("SCENARIO_FROM") or now())
    except Stopped:
        log("scenario_stopped", line=LINE)
    finally:
        clear_all(urls)


def main_loop() -> None:
    global _log_path
    _log_path = LOOP_LOG
    urls = {ln: OPCUA_BY_LINE[ln] for ln in LINES}
    cycle = last_cycle(LOOP_LOG)
    line = None
    log("loop_start", lines=LINES, resume_after_cycle=cycle)
    try:
        clear_all(urls)
        while not stop.is_set():
            cycle += 1
            line = LINES[(cycle - 1) % len(LINES)]
            run_cycle(line, urls[line], now(), cycle)
    except Stopped:
        log("loop_stopped", cycle=cycle, line=line)
    finally:
        clear_all(urls)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--loop", action="store_true", help="cycle forever, alternating SCENARIO_LINES")
    args = ap.parse_args()
    _install_signals()
    for ln in LINES:
        if ln not in OPCUA_BY_LINE:
            raise SystemExit(f"no OPC-UA url for line {ln}; set SCENARIO_OPCUA_{ln}")
    main_loop() if args.loop else main_single()


if __name__ == "__main__":
    main()
