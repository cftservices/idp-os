"""Demo scenario: a sticking steam control valve on line L1, for an 8D with a validated root cause.

The valve is modelled with the factory's existing cook_undertemp fault (it caps the reached
cook temperature). Phases, counted in completed L1 batches after the start of the scenario:

  stable       25 batches, no fault                  -> base period for the control limits
  drift        10 batches, magnitude 0.06 (~-1 C)     -> still in spec, a control chart should see it
  out_of_spec   2 batches, magnitude 0.5 (~-9 C)      -> viscosity below 150 cP, verdict HOLD
  repaired     15 batches, fault cleared              -> verification of the corrective action

The batch engine and the MES are not told anything: they only see what the factory does. This
script's log (stdout, and a JSON lines file) is the answer key, kept apart from the data an
investigator works with.

Runs in its own container on the milkdemo network with the factory image (asyncua inside):
  docker run -d --name vla-scenario-8d --restart on-failure --network idp-network \
    -v /root/idp-os/scenarios/vla-batch/scripts:/s:ro -v /root/vla-scenario-logs:/logs \
    techflow/vla-factory:latest python -u /s/scenario_steam_valve.py
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from asyncua import Client, ua

OPCUA = os.environ.get("OPCUA_URL", "opc.tcp://vla-factory:4840/DairyWorks")
ENGINE = os.environ.get("BATCH_ENGINE_URL", "http://vla-batch-engine:8000/api/v1")
LINE = os.environ.get("SCENARIO_LINE", "L1")
LOG = os.environ.get("SCENARIO_LOG", "/logs/steam-valve-8d.jsonl")
POLL_S = float(os.environ.get("SCENARIO_POLL_S", "20"))
BATCH = "ns=2;s=DairyWorks.Vla.Batch"

PHASES = [("stable", None, 25), ("drift", 0.06, 10), ("out_of_spec", 0.5, 2), ("repaired", "clear", 15)]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(event: str, **kw) -> None:
    rec = {"ts": now(), "event": event, **kw}
    print(json.dumps(rec), flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def completed_since(start: str) -> list[dict]:
    with urllib.request.urlopen(f"{ENGINE}/batches?line={LINE}", timeout=20) as r:
        rows = json.load(r)
    done = [b for b in rows if b.get("state") == "COMPLETE" and (b.get("completed_at") or "") >= start]
    return sorted(done, key=lambda b: b["completed_at"])


async def act(action) -> None:
    async with Client(OPCUA) as cl:
        node = cl.get_node(BATCH)
        if action == "clear":
            rc = await node.call_method(cl.get_node(f"{BATCH}.ClearFault"))
        else:
            rc = await node.call_method(cl.get_node(f"{BATCH}.InjectFault"),
                                        ua.Variant("cook_undertemp", ua.VariantType.String),
                                        ua.Variant(float(action), ua.VariantType.Double))
        log("factory_call", action=action, rc=rc)


def main() -> None:
    start = os.environ.get("SCENARIO_FROM") or now()
    log("scenario_start", line=LINE, start=start, phases=PHASES)
    asyncio.run(act("clear"))  # start from a clean factory
    phase_i = 0
    seen: set[str] = set()
    while phase_i < len(PHASES):
        name, action, n = PHASES[phase_i]
        done = completed_since(start)
        for b in done:
            if b["batch_id"] not in seen:
                seen.add(b["batch_id"])
                log("batch_completed", batch_id=b["batch_id"], phase=name, n=len(seen),
                    end_viscosity_cP=b.get("end_viscosity_cP"), verdict=b.get("verdict"))
        target = sum(p[2] for p in PHASES[: phase_i + 1])
        if len(done) >= target:
            phase_i += 1
            if phase_i < len(PHASES):
                nxt, nxt_action, _ = PHASES[phase_i]
                log("phase_change", to=nxt, after_batches=len(done))
                if nxt_action is not None:
                    asyncio.run(act(nxt_action))
            continue
        time.sleep(POLL_S)
    log("scenario_end", batches=len(seen))


if __name__ == "__main__":
    main()
