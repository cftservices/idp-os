"""EBR (Electronic Batch Record) assembly + rendering.

assemble_ebr() builds a per-order dict (header, consumed, produced, samples,
alarms, verdict, critical_alarm_during_batch). render_html() renders it via
Jinja2. render_pdf() uses weasyprint if available, else returns the HTML bytes.
"""

from __future__ import annotations

import logging

from jinja2 import Environment, select_autoescape

log = logging.getLogger("mes.ebr")

_EBR_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>EBR {{ header.order_id }} - {{ header.product_name }}</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; }
  h1 { font-size: 1.4rem; margin-bottom: 0; }
  .sub { color: #666; margin-top: .2rem; }
  .verdict { display: inline-block; padding: .3rem .8rem; border-radius: 6px; font-weight: 700; color: #fff; }
  .APPROVED { background: #1a7f37; }
  .HOLD { background: #bf8700; }
  .REJECTED { background: #cf222e; }
  .PENDING { background: #57606a; }
  .crit-yes { color: #cf222e; font-weight: 700; }
  .crit-no { color: #1a7f37; font-weight: 700; }
  table { border-collapse: collapse; width: 100%; margin: .6rem 0 1.4rem; font-size: .85rem; }
  th, td { border: 1px solid #d0d7de; padding: .35rem .5rem; text-align: left; }
  th { background: #f6f8fa; }
  td.oot { background: #ffe9e9; }
  h2 { font-size: 1.05rem; border-bottom: 2px solid #d0d7de; padding-bottom: .2rem; margin-top: 1.6rem; }
  .meta td:first-child { font-weight: 600; width: 220px; background: #f6f8fa; }
  .sev-Critical { color: #cf222e; font-weight: 700; }
  .sev-High { color: #bf3989; }
  .sev-Medium { color: #bf8700; }
</style>
</head>
<body>
  <h1>Electronic Batch Record</h1>
  <div class="sub">{{ header.enterprise }} &middot; {{ header.site }}</div>

  <h2>Header</h2>
  <table class="meta">
    <tr><td>Order</td><td>{{ header.order_id }}</td></tr>
    <tr><td>Product</td><td>{{ header.product_name }} ({{ header.product_id }})</td></tr>
    <tr><td>Recipe</td><td>{{ header.recipe_id }}</td></tr>
    <tr><td>Planned qty</td><td>{{ header.planned_qty }} kg</td></tr>
    <tr><td>Routing</td><td>{{ header.routing | join(" &rarr; ") | safe }}</td></tr>
    <tr><td>Status</td><td>{{ header.status }}</td></tr>
    <tr><td>Created / Completed</td><td>{{ header.created_at }} / {{ header.completed_at }}</td></tr>
    <tr><td>Verdict</td><td><span class="verdict {{ verdict }}">{{ verdict }}</span></td></tr>
    <tr><td>Critical Alarm During Batch</td>
      <td class="{{ 'crit-yes' if critical_alarm_during_batch else 'crit-no' }}">
        {{ "Yes" if critical_alarm_during_batch else "No" }}</td></tr>
  </table>

  <h2>Consumed ({{ consumed | length }})</h2>
  <table>
    <tr><th>Material</th><th>Target (kg)</th><th>Actual (kg)</th><th>Tol</th><th>In tol?</th><th>Source</th></tr>
    {% for c in consumed %}
    <tr>
      <td>{{ c.material_id }}</td>
      <td>{{ c.qty_target }}</td>
      <td class="{{ '' if c.in_tolerance else 'oot' }}">{{ c.qty_actual }}</td>
      <td>{{ c.tol_min if c.tol_min is not none else '-' }} - {{ c.tol_max if c.tol_max is not none else '-' }}</td>
      <td>{{ "OK" if c.in_tolerance else "OUT" }}</td>
      <td>{{ c.source }}</td>
    </tr>
    {% endfor %}
  </table>

  <h2>Produced ({{ produced | length }})</h2>
  <table>
    <tr><th>Item</th><th>Lot</th><th>Qty produced</th><th>Rejects</th><th>Grade</th></tr>
    {% for p in produced %}
    <tr><td>{{ p.item_id }}</td><td>{{ p.lot_no }}</td><td>{{ p.qty_produced }}</td>
        <td>{{ p.reject_count }}</td><td>{{ p.grade }}</td></tr>
    {% endfor %}
  </table>

  <h2>Handling Units ({{ handling_units | length }})</h2>
  <table>
    <tr><th>HU</th><th>SSCC</th><th>Packs</th><th>Pallet #</th><th>Expiry</th><th>Status</th></tr>
    {% for h in handling_units %}
    <tr><td>{{ h.hu_id }}</td><td>{{ h.sscc_code }}</td><td>{{ h.pack_count }}</td>
        <td>{{ h.pallet_seq }}</td><td>{{ h.expiry_date }}</td><td>{{ h.status }}</td></tr>
    {% endfor %}
  </table>

  <h2>Samples ({{ samples | length }})</h2>
  <table>
    <tr><th>Type</th><th>Phase</th><th>Location</th><th>Status</th><th>Result</th></tr>
    {% for s in samples %}
    <tr><td>{{ s.sample_type }}</td><td>{{ s.phase }}</td><td>{{ s.location }}</td>
        <td>{{ s.status }}</td><td>{{ s.result if s.result is not none else '-' }}</td></tr>
    {% endfor %}
  </table>

  <h2>Alarms ({{ alarms | length }})</h2>
  {% if alarms %}
  <table>
    <tr><th>Equipment</th><th>Type</th><th>Severity</th><th>Message</th><th>Resolved</th></tr>
    {% for a in alarms %}
    <tr><td>{{ a.equipment_id }}</td><td>{{ a.alarm_type }}</td>
        <td class="sev-{{ a.severity }}">{{ a.severity }}</td>
        <td>{{ a.message }}</td><td>{{ "Yes" if a.resolved else "No" }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<p>No alarms during batch.</p>{% endif %}

  <h2>OEE</h2>
  <table>
    <tr><th>Availability</th><th>Performance</th><th>Quality</th><th>OEE</th></tr>
    {% for o in oee %}
    <tr><td>{{ o.availability_pct }}%</td><td>{{ o.performance_pct }}%</td>
        <td>{{ o.quality_pct }}%</td><td>{{ o.oee_pct }}%</td></tr>
    {% endfor %}
  </table>
</body>
</html>"""


def assemble_ebr(order_bundle: dict, model=None) -> dict:
    """Assemble the EBR dict from an OrderRunner.get_order() bundle."""
    order = order_bundle["order"]
    ebr = {
        "header": {
            "order_id": order.get("order_id"),
            "product_id": order.get("product_id"),
            "product_name": order.get("product_name"),
            "recipe_id": order.get("recipe_id"),
            "planned_qty": order.get("planned_qty"),
            "routing": order.get("routing", []),
            "status": order.get("status"),
            "created_at": order.get("created_at"),
            "completed_at": order.get("completed_at"),
            "enterprise": model.enterprise_name if model else "DairyWorks BV",
            "site": model.site_name if model else "DairyWorks Plant",
        },
        "consumed": order_bundle.get("consumptions", []),
        "produced": order_bundle.get("productions", []),
        "handling_units": order_bundle.get("handling_units", []),
        "samples": order_bundle.get("samples", []),
        "alarms": order_bundle.get("alarms", []),
        "oee": order_bundle.get("oee", []),
        "verdict": order.get("verdict") or "PENDING",
        "critical_alarm_during_batch": order.get("critical_alarm_during_batch", False),
    }
    # enrich consumed rows with tolerances from job_bom
    tol_by_pos = {r["bom_pos"]: r for r in order_bundle.get("job_bom", [])}
    for c in ebr["consumed"]:
        jb = tol_by_pos.get(c.get("bom_pos"), {})
        c.setdefault("tol_min", jb.get("tol_min"))
        c.setdefault("tol_max", jb.get("tol_max"))
    return ebr


def render_html(ebr: dict) -> str:
    env = Environment(autoescape=select_autoescape(["html", "xml"]))
    template = env.from_string(_EBR_TEMPLATE)
    return template.render(**ebr)


def render_pdf(ebr: dict) -> tuple[bytes, str]:
    """Return (bytes, media_type). PDF via weasyprint if available, else HTML."""
    html = render_html(ebr)
    try:
        from weasyprint import HTML  # noqa

        pdf = HTML(string=html).write_pdf()
        return pdf, "application/pdf"
    except Exception as e:
        log.info("weasyprint unavailable (%s) — returning HTML instead of PDF", e)
        return html.encode("utf-8"), "text/html"
