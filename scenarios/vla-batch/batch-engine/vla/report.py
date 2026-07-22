"""BIRT-stand-in batch report — JSON + PDF (reportlab).

assemble_report() builds a per-batch Electronic Batch Record dict:
  header + doses + peak_temp/hold/viscosity + packs + verdict + samples +
  alarms + handling_units + order context + production bookings + events +
  verdict acknowledgment.
render_pdf() renders it to a PDF via reportlab (BIRT stand-in). render_json()
returns the dict as-is. Both are offline (no external service).

Contract: §batch-engine REST GET /report/{id}?format=pdf|json — "BIRT-stijl
batch-rapport (PDF via reportlab/weasyprint als BIRT-stand-in; JSON altijd).
Header + doses + peak_temp/hold/viscosity + packs + verdict."
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("vla.report")

SITE = "DairyWorks"
LINE = "Vla"

_VERDICT_COLOR = {
    "APPROVED": "#1a7f37",
    "HOLD": "#bf8700",
    "REJECTED": "#cf222e",
    "PENDING": "#57606a",
}


def assemble_report(batch: dict) -> dict:
    """Assemble the BIRT-style report dict from a BatchRunner.get_batch() bundle."""
    order = batch.get("order")
    return {
        "report_type": "Electronic Batch Record (BIRT-style)",
        "site": SITE,
        "line": LINE,
        "header": {
            "batch_id": batch.get("batch_id"),
            "recipe_id": batch.get("recipe_id"),
            "product_name": batch.get("product_name"),
            "planned_L": batch.get("planned_L"),
            "state": batch.get("state"),
            "created_at": batch.get("created_at"),
            "started_at": batch.get("started_at"),
            "completed_at": batch.get("completed_at"),
        },
        "doses": batch.get("doses", []),
        "cook": {
            "peak_cook_temp_C": batch.get("peak_cook_temp_C"),
            "cook_setpoint_C": batch.get("cook_setpoint_C"),
            "hold_sec": batch.get("hold_sec"),
            "hold_elapsed_sec": batch.get("hold_elapsed_sec"),
        },
        "quality": {
            "end_viscosity_cP": batch.get("end_viscosity_cP"),
            "spec_min_cP": batch.get("spec_min_cP"),
            "spec_max_cP": batch.get("spec_max_cP"),
        },
        "packs": {
            "packs_total": batch.get("packs_total", 0),
            "reject_count": batch.get("reject_count", 0),
        },
        "samples": batch.get("samples", []),
        "alarms": batch.get("alarms", []),
        "handling_units": [
            {"hu_id": h.get("hu_id"), "packs_count": h.get("packs_count"),
             "location": h.get("location"), "status": h.get("status"),
             "ts": h.get("ts")}
            for h in batch.get("handling_units", [])
        ],
        "verdict": batch.get("verdict") or "PENDING",
        "critical_alarm_during_batch": batch.get("critical_alarm_during_batch", False),
        "order": ({"order_id": order.get("order_id"),
                    "target_qty_L": order.get("target_qty_L"),
                    "due_date": order.get("due_date"),
                    "status": order.get("status")} if order else None),
        "production": [
            {"packs": p.get("packs"), "source": p.get("source"),
             "operator_id": p.get("operator_id"), "ts": p.get("ts")}
            for p in batch.get("production_bookings", [])
        ],
        "events": [
            {"event_type": e.get("event_type"), "ts": e.get("ts")}
            for e in batch.get("events", [])
        ],
        "verdict_ack": batch.get("verdict_ack"),
    }


def render_json(batch: dict) -> dict:
    return assemble_report(batch)


def render_pdf(batch: dict) -> bytes:
    """Render the batch report to PDF bytes via reportlab (BIRT stand-in)."""
    report = assemble_report(batch)
    try:
        return _reportlab_pdf(report)
    except Exception as e:  # pragma: no cover - reportlab always present in image
        log.warning("reportlab unavailable (%s) — returning text fallback", e)
        return _text_fallback(report).encode("utf-8")


def _reportlab_pdf(report: dict) -> bytes:
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
        title=f"Batch Report {report['header'].get('batch_id')}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=16, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9,
                         textColor=colors.HexColor("#666666"), spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=11, spaceBefore=10,
                        spaceAfter=4)
    story = []

    hdr = report["header"]
    story.append(Paragraph("Electronic Batch Record", h1))
    story.append(Paragraph(
        f"{report['site']} &middot; {report['line']} &middot; BIRT-style", sub))

    verdict = report["verdict"]
    vcolor = colors.HexColor(_VERDICT_COLOR.get(verdict, "#57606a"))

    def kv_table(rows):
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

    # Header
    story.append(Paragraph("Header", h2))
    story.append(kv_table([
        ["Batch", str(hdr.get("batch_id"))],
        ["Product", str(hdr.get("product_name"))],
        ["Recipe", str(hdr.get("recipe_id"))],
        ["Planned", f"{hdr.get('planned_L')} L"],
        ["State", str(hdr.get("state"))],
        ["Started / Completed",
         f"{hdr.get('started_at')} / {hdr.get('completed_at')}"],
    ]))

    # Verdict banner
    story.append(Spacer(1, 6))
    vt = Table([[f"VERDICT: {verdict}"]], colWidths=[165 * mm])
    vt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), vcolor),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(vt)

    # Doses
    story.append(Paragraph(f"Doses ({len(report['doses'])})", h2))
    drows = [["Material", "Target (kg)", "Actual (kg)", "Tol min", "Tol max", "In tol?"]]
    dose_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
    ]
    for i, d in enumerate(report["doses"], start=1):
        in_tol = d.get("in_tolerance")
        drows.append([
            str(d.get("material_id")),
            _fmt(d.get("qty_target")),
            _fmt(d.get("qty_actual")),
            _fmt(d.get("tol_min")),
            _fmt(d.get("tol_max")),
            "OK" if in_tol else ("OUT" if in_tol is False else "-"),
        ])
        if in_tol is False:
            dose_styles.append(
                ("BACKGROUND", (2, i), (2, i), colors.HexColor("#ffe9e9")))
    dt = Table(drows, colWidths=[35 * mm, 28 * mm, 28 * mm, 26 * mm, 26 * mm, 22 * mm])
    dt.setStyle(TableStyle(dose_styles))
    story.append(dt)

    # Process (cook/hold/viscosity/packs)
    q = report["quality"]
    c = report["cook"]
    p = report["packs"]
    story.append(Paragraph("Process & Quality", h2))
    story.append(kv_table([
        ["Peak cook temp", f"{c.get('peak_cook_temp_C')} C "
                           f"(setpoint {c.get('cook_setpoint_C')} C)"],
        ["Hold", f"{c.get('hold_elapsed_sec')} / {c.get('hold_sec')} s"],
        ["End viscosity", f"{q.get('end_viscosity_cP')} cP "
                          f"(spec {q.get('spec_min_cP')}-{q.get('spec_max_cP')} cP)"],
        ["Packs total", str(p.get("packs_total"))],
        ["Rejects", str(p.get("reject_count"))],
    ]))

    # Samples
    story.append(Paragraph(f"Samples ({len(report['samples'])})", h2))
    if report["samples"]:
        srows = [["Type", "Phase", "Value", "Status", "Result"]]
        for s in report["samples"]:
            srows.append([
                str(s.get("sample_type")), str(s.get("phase")),
                f"{s.get('value')} {s.get('unit') or ''}".strip()
                if s.get("value") is not None else "-",
                str(s.get("status")), str(s.get("result") or "-"),
            ])
        st = Table(srows, colWidths=[45 * mm, 28 * mm, 32 * mm, 30 * mm, 30 * mm])
        st.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(st)
    else:
        story.append(Paragraph("No samples.", styles["Normal"]))

    # Alarms
    story.append(Paragraph(f"Alarms ({len(report['alarms'])})", h2))
    if report["alarms"]:
        arows = [["Equipment", "Type", "Severity", "Message", "Resolved"]]
        for a in report["alarms"]:
            arows.append([
                str(a.get("equipment_id")), str(a.get("alarm_type")),
                str(a.get("severity")), str(a.get("message")),
                "Yes" if a.get("resolved") else "No",
            ])
        at = Table(arows, colWidths=[30 * mm, 32 * mm, 20 * mm, 63 * mm, 20 * mm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(at)
    else:
        story.append(Paragraph("No alarms during batch.", styles["Normal"]))

    # Handling units (PR-35)
    story.append(Paragraph(f"Handling units ({len(report['handling_units'])})", h2))
    if report["handling_units"]:
        hrows = [["HU (SSCC-placeholder)", "Packs", "Location", "Status"]]
        for h in report["handling_units"]:
            hrows.append([
                str(h.get("hu_id")), str(h.get("packs_count")),
                str(h.get("location") or "-"), str(h.get("status")),
            ])
        ht = Table(hrows, colWidths=[50 * mm, 25 * mm, 40 * mm, 50 * mm])
        ht.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(ht)
    else:
        story.append(Paragraph("No handling units.", styles["Normal"]))

    # Order context
    story.append(Paragraph("Order context", h2))
    order = report.get("order")
    if order:
        story.append(kv_table([
            ["Order", str(order.get("order_id"))],
            ["Target qty", f"{order.get('target_qty_L')} L"],
            ["Due date", str(order.get("due_date") or "-")],
            ["Status", str(order.get("status"))],
        ]))
    else:
        story.append(Paragraph("No order linked to this batch.", styles["Normal"]))

    # Production bookings
    production = report.get("production", [])
    story.append(Paragraph(f"Production bookings ({len(production)})", h2))
    if production:
        prows = [["Packs", "Source", "Operator", "Timestamp"]]
        for p in production:
            prows.append([
                str(p.get("packs")), str(p.get("source")),
                str(p.get("operator_id") or "-"), str(p.get("ts")),
            ])
        pt = Table(prows, colWidths=[25 * mm, 40 * mm, 35 * mm, 65 * mm])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(pt)
    else:
        story.append(Paragraph("No production bookings.", styles["Normal"]))

    # Events (audit trail, capped at the most recent 30)
    events = report.get("events", [])
    events_shown = events[-30:]
    story.append(Paragraph(f"Events ({len(events)})", h2))
    if events_shown:
        erows = [["Event type", "Timestamp"]]
        for e in events_shown:
            erows.append([str(e.get("event_type")), str(e.get("ts"))])
        et = Table(erows, colWidths=[60 * mm, 105 * mm])
        et.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7de")),
        ]))
        story.append(et)
    else:
        story.append(Paragraph("No events recorded.", styles["Normal"]))

    # Verdict acknowledgment
    story.append(Paragraph("Verdict acknowledgment", h2))
    ack = report.get("verdict_ack")
    if ack:
        story.append(kv_table([
            ["Acknowledged by", str(ack.get("operator_id"))],
            ["Acknowledged at", str(ack.get("ts"))],
        ]))
    else:
        story.append(Paragraph("Verdict not yet acknowledged.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()


def _fmt(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _text_fallback(report: dict) -> str:
    h = report["header"]
    lines = [
        "BATCH PRODUCTION REPORT (BIRT-style)",
        f"{report['site']} / {report['line']}",
        f"Batch:   {h.get('batch_id')}",
        f"Product: {h.get('product_name')} ({h.get('recipe_id')})",
        f"Verdict: {report['verdict']}",
        f"Peak cook temp: {report['cook'].get('peak_cook_temp_C')} C",
        f"End viscosity:  {report['quality'].get('end_viscosity_cP')} cP",
        f"Packs total:    {report['packs'].get('packs_total')}",
    ]
    return "\n".join(lines)
