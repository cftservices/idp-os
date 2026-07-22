"""Period management report + equipment maintenance report — JSON + PDF
(reportlab), same style patterns as report.py (BIRT stand-in).

assemble_period_report() aggregates dw_batches completed within a rolling
`days`-day window into a plant-wide management summary: verdict mix, yield,
hold/reject ratio, downtime events (from dw_equipment_state) and CBM alerts
(from dw_cbm_alerts). assemble_equipment_report() builds a per-equipment
maintenance dossier: running hours (EquipmentMonitor), state history, CBM
alerts and CIP events (dw_batch_events, event_type cip_performed), all
windowed the same way and raises ValueError on an unknown equipment_id.

Contract: §batch-engine REST GET /report/period?days=N&format=pdf|json and
GET /report/equipment/{equipment_id}?days=N&format=pdf|json.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone

from .equipment import EQUIPMENT_IDS, EquipmentMonitor

log = logging.getLogger("vla.period_reports")

SITE = "DairyWorks"
LINE = "Vla"

_DOWNTIME_STATES = {"Down", "Error", "Dirty"}


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts) -> datetime:
    return datetime.fromisoformat(ts)


def _in_window(ts, cutoff: datetime) -> bool:
    if not ts:
        return False
    try:
        return _parse(ts) >= cutoff
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------- assemblers

def assemble_period_report(db, days: int) -> dict:
    """Plant-wide management report over batches completed in the last
    `days` days (window keyed on dw_batches.completed_at)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    batches = [b for b in db.dw_batches.find({})
               if _in_window(b.get("completed_at"), cutoff)]
    batches_by_verdict = {"APPROVED": 0, "HOLD": 0, "REJECTED": 0, "PENDING": 0}
    packs_sum = 0.0
    planned_sum = 0.0
    for b in batches:
        verdict = b.get("verdict") or "PENDING"
        if verdict not in batches_by_verdict:
            verdict = "PENDING"
        batches_by_verdict[verdict] += 1
        packs_sum += float(b.get("packs_total") or 0)
        planned_sum += float(b.get("planned_L") or 0)

    total = len(batches)
    yield_pct = round(packs_sum / planned_sum * 100, 2) if planned_sum > 0 else 0.0
    hold_reject = batches_by_verdict["HOLD"] + batches_by_verdict["REJECTED"]
    hold_reject_ratio = round(hold_reject / total, 4) if total > 0 else 0.0

    downtime_events = sum(
        1 for r in db.dw_equipment_state.find({})
        if r.get("state") in _DOWNTIME_STATES and _in_window(r.get("ts"), cutoff)
    )
    cbm_alerts = [a for a in db.dw_cbm_alerts.find({})
                  if _in_window(a.get("ts"), cutoff)]

    return {
        "report_type": "Management Report",
        "window_days": days,
        "batches_total": total,
        "batches_by_verdict": batches_by_verdict,
        "yield_pct": yield_pct,
        "hold_reject_ratio": hold_reject_ratio,
        "downtime_events": downtime_events,
        "cbm_alerts": cbm_alerts,
        "generated_at": _iso(),
    }


def assemble_equipment_report(db, equipment_id: str, days: int) -> dict:
    """Per-equipment maintenance dossier over the last `days` days. Raises
    ValueError on an unknown equipment_id."""
    if equipment_id not in EQUIPMENT_IDS:
        raise ValueError(f"unknown equipment {equipment_id!r}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    mon = EquipmentMonitor(db, bus=None)
    running_hours = mon.running_hours(equipment_id)

    state_history = [
        r for r in db.dw_equipment_state.find({"equipment_id": equipment_id})
        if _in_window(r.get("ts"), cutoff)
    ][-100:]

    cbm_alerts = [
        a for a in db.dw_cbm_alerts.find({"equipment_id": equipment_id})
        if _in_window(a.get("ts"), cutoff)
    ]

    cip_events = [
        e for e in db.dw_batch_events.find({"event_type": "cip_performed"})
        if _in_window(e.get("ts"), cutoff)
        and (e.get("payload") or {}).get("equipment_id") == equipment_id
    ]

    return {
        "report_type": "Maintenance Report",
        "equipment_id": equipment_id,
        "window_days": days,
        "running_hours": running_hours,
        "state_history": state_history,
        "cbm_alerts": cbm_alerts,
        "cip_events": cip_events,
        "generated_at": _iso(),
    }


# ---------------------------------------------------------------------- pdf

def _kv_table(rows, colors, mm, Table, TableStyle):
    t = Table(rows, colWidths=[55 * mm, 110 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f6f8fa")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def render_period_pdf(rep: dict) -> bytes:
    """Render the period management report to PDF bytes via reportlab."""
    try:
        return _period_reportlab_pdf(rep)
    except Exception as e:  # pragma: no cover - reportlab always present in image
        log.warning("reportlab unavailable (%s) — returning text fallback", e)
        return _period_text_fallback(rep).encode("utf-8")


def _period_reportlab_pdf(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Management Report",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9,
                         textColor=colors.HexColor("#666666"), spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=10,
                        spaceAfter=4)
    story = []

    story.append(Paragraph("Management Report", h1))
    story.append(Paragraph(
        f"{SITE} &middot; {LINE} &middot; last {rep.get('window_days')} days", sub))

    bv = rep.get("batches_by_verdict", {})
    story.append(Paragraph("Summary", h2))
    story.append(_kv_table([
        ["Batches total", str(rep.get("batches_total"))],
        ["Approved / Hold / Rejected / Pending",
         f"{bv.get('APPROVED', 0)} / {bv.get('HOLD', 0)} / "
         f"{bv.get('REJECTED', 0)} / {bv.get('PENDING', 0)}"],
        ["Yield", f"{rep.get('yield_pct')} %"],
        ["Hold+Reject ratio", str(rep.get("hold_reject_ratio"))],
        ["Downtime events", str(rep.get("downtime_events"))],
    ], colors, mm, Table, TableStyle))

    alerts = rep.get("cbm_alerts", [])
    story.append(Paragraph(f"CBM alerts ({len(alerts)})", h2))
    if alerts:
        arows = [["Equipment", "Type", "Message", "Resolved", "Timestamp"]]
        for a in alerts:
            arows.append([
                str(a.get("equipment_id")), str(a.get("alert_type")),
                str(a.get("message")), "Yes" if a.get("resolved") else "No",
                str(a.get("ts")),
            ])
        at = Table(arows, colWidths=[30 * mm, 28 * mm, 62 * mm, 20 * mm, 25 * mm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(at)
    else:
        story.append(Paragraph("No CBM alerts in this window.", styles["Normal"]))

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Generated at {rep.get('generated_at')}", sub))

    doc.build(story)
    return buf.getvalue()


def _period_text_fallback(rep: dict) -> str:
    lines = [
        "MANAGEMENT REPORT",
        f"{SITE} / {LINE} - last {rep.get('window_days')} days",
        f"Batches total: {rep.get('batches_total')}",
        f"Yield: {rep.get('yield_pct')} %",
        f"Hold+Reject ratio: {rep.get('hold_reject_ratio')}",
        f"Downtime events: {rep.get('downtime_events')}",
    ]
    return "\n".join(lines)


def render_equipment_pdf(rep: dict) -> bytes:
    """Render the equipment maintenance report to PDF bytes via reportlab."""
    try:
        return _equipment_reportlab_pdf(rep)
    except Exception as e:  # pragma: no cover - reportlab always present in image
        log.warning("reportlab unavailable (%s) — returning text fallback", e)
        return _equipment_text_fallback(rep).encode("utf-8")


def _equipment_reportlab_pdf(rep: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Maintenance Report {rep.get('equipment_id')}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9,
                         textColor=colors.HexColor("#666666"), spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=10,
                        spaceAfter=4)
    story = []

    story.append(Paragraph("Maintenance Report", h1))
    story.append(Paragraph(
        f"{SITE} &middot; {rep.get('equipment_id')} &middot; "
        f"last {rep.get('window_days')} days", sub))

    story.append(Paragraph("Summary", h2))
    story.append(_kv_table([
        ["Equipment", str(rep.get("equipment_id"))],
        ["Running hours", f"{rep.get('running_hours')} h"],
    ], colors, mm, Table, TableStyle))

    hist = rep.get("state_history", [])
    story.append(Paragraph(f"State history ({len(hist)})", h2))
    if hist:
        hrows = [["State", "Timestamp"]]
        for r in hist:
            hrows.append([str(r.get("state")), str(r.get("ts"))])
        ht = Table(hrows, colWidths=[60 * mm, 105 * mm])
        ht.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(ht)
    else:
        story.append(Paragraph("No state changes in this window.", styles["Normal"]))

    alerts = rep.get("cbm_alerts", [])
    story.append(Paragraph(f"CBM alerts ({len(alerts)})", h2))
    if alerts:
        arows = [["Type", "Message", "Resolved", "Timestamp"]]
        for a in alerts:
            arows.append([
                str(a.get("alert_type")), str(a.get("message")),
                "Yes" if a.get("resolved") else "No", str(a.get("ts")),
            ])
        at = Table(arows, colWidths=[30 * mm, 80 * mm, 25 * mm, 30 * mm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(at)
    else:
        story.append(Paragraph("No CBM alerts in this window.", styles["Normal"]))

    cips = rep.get("cip_events", [])
    story.append(Paragraph(f"CIP events ({len(cips)})", h2))
    if cips:
        crows = [["Operator", "Timestamp"]]
        for e in cips:
            payload = e.get("payload") or {}
            crows.append([str(payload.get("operator_id") or "-"), str(e.get("ts"))])
        ct = Table(crows, colWidths=[60 * mm, 105 * mm])
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(ct)
    else:
        story.append(Paragraph("No CIP events in this window.", styles["Normal"]))

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Generated at {rep.get('generated_at')}", sub))

    doc.build(story)
    return buf.getvalue()


def _equipment_text_fallback(rep: dict) -> str:
    lines = [
        "MAINTENANCE REPORT",
        f"Equipment: {rep.get('equipment_id')}",
        f"Running hours: {rep.get('running_hours')} h",
        f"State history rows: {len(rep.get('state_history', []))}",
        f"CBM alerts: {len(rep.get('cbm_alerts', []))}",
        f"CIP events: {len(rep.get('cip_events', []))}",
    ]
    return "\n".join(lines)
