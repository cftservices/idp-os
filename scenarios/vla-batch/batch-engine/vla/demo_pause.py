"""Demo mode: pause one production line for a live presentation.

On the VPS the line never stands still: the autopilot starts a batch every few minutes,
cleans when the cook unit is dirty, the steam-valve scenario injects faults and the CMMS
reports random breakdowns. That is what makes the demo look alive, and it is also what
makes a presenter's own batch impossible to follow. A pause hands one line to a person:

  autopilot   starts no batch and no CIP on that line, and holds the factory's
              self-chaining off (SetAutostart false), so no run starts outside the MES
  scenario    clears its fault on that line and stops counting until the pause ends
  CMMS        reports no random breakdown on that line

What keeps running: the other line, the warehouse and logistics of autopilot pallets
(a presenter's own batch is never palletised automatically), KPI history, retention.

A pause always ends: by a resume, or when its time is up (default 45, at most 180
minutes), so a forgotten pause cannot leave the demo dead. It is stored in Mongo
(dw_autopilot, key "demo-pause") so an engine restart in the middle of a demo keeps it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import model as M

KEY = "demo-pause"
DEFAULT_MINUTES = 45
MAX_MINUTES = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lines(db) -> dict:
    doc = db.dw_autopilot.find_one({"key": KEY}) or {}
    return dict(doc.get("lines") or {})


def _save(db, lines: dict) -> None:
    db.dw_autopilot.update_one({"key": KEY}, {"$set": {"key": KEY, "lines": lines}},
                               upsert=True)


def _check_line(line: str) -> str:
    if line not in M.LINES:
        raise ValueError(f"unknown line {line!r} (known: {', '.join(M.LINES)})")
    return line


def paused_until(db, line: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """End of the pause on `line`, or None when the line is not paused (or it ran out)."""
    row = _lines(db).get(line) or {}
    try:
        until = datetime.fromisoformat(row["until"])
    except (KeyError, TypeError, ValueError):
        return None
    return until if until > (now or _now()) else None


def is_paused(db, line: str, now: Optional[datetime] = None) -> bool:
    return paused_until(db, line, now) is not None


def pause(db, line: str, minutes: float = DEFAULT_MINUTES,
          operator_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    _check_line(line)
    minutes = float(minutes)
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"minutes must be between 1 and {MAX_MINUTES}, got {minutes:g}")
    now = now or _now()
    lines = _lines(db)
    lines[line] = {"until": (now + timedelta(minutes=minutes)).isoformat(),
                   "since": now.isoformat(), "by": operator_id}
    _save(db, lines)
    return status(db, now)[line]


def resume(db, line: str, operator_id: Optional[str] = None,
           now: Optional[datetime] = None) -> dict:
    _check_line(line)
    lines = _lines(db)
    lines.pop(line, None)
    _save(db, lines)
    return status(db, now)[line]


def status(db, now: Optional[datetime] = None) -> dict:
    """Per line: paused or not, until when and by whom. Expired pauses read as not paused."""
    now = now or _now()
    rows = _lines(db)
    out = {}
    for lid in M.LINES:
        until = paused_until(db, lid, now)
        row = rows.get(lid) or {}
        out[lid] = {"line": lid, "paused": until is not None,
                    "until": until.isoformat() if until else None,
                    "since": row.get("since") if until else None,
                    "by": row.get("by") if until else None,
                    "minutes_left": round((until - now).total_seconds() / 60, 1)
                    if until else 0.0}
    return out
