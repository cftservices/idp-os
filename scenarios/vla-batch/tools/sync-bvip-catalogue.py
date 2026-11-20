"""Snapshot of the 29 industrial BVIP KPIs for the batch engine and the Grafana generator.

The KPI catalogue lives in another repo (business-value-platform/040_Bron/kpi_catalogue.yaml)
and is the only source of KPI definitions: name, unit, formula, direction and thresholds. The
engine and the dashboards cannot read that repo at runtime, so this tool writes the industrial
part of it (PRD, QUA, MNT, ENE, PAC) to batch-engine/vla/bvip_catalogue.json, with the hash of
the source file. Nothing is typed over by hand.

  python tools/sync-bvip-catalogue.py [--catalogue PATH]          write the snapshot
  python tools/sync-bvip-catalogue.py --check [--catalogue PATH]  exit 1 when it is stale

PATH defaults to $BVIP_CATALOGUE, then to the sibling checkout under c:/tools/techflow-os.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "batch-engine" / "vla" / "bvip_catalogue.json"
DEFAULT_SRC = Path("c:/tools/techflow-os/sub-os/project-os/projects/business-value-platform"
                   "/040_Bron/kpi_catalogue.yaml")
DOMAINS = ("PRD", "QUA", "MNT", "ENE", "PAC")
FIELDS = ("id", "naam", "naam_nl", "eenheid", "formule", "agg", "richting", "doel", "waarsch",
          "krit", "doelstelling", "interpretatie")
FUNCS = {"avg", "lmtd", "pow", "min", "max", "sum", "if"}


def identifiers(formula: str) -> list[str]:
    """Input names in a formula, in order of appearance, without functions or numbers."""
    seen: list[str] = []
    for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula):
        if tok not in FUNCS and tok not in seen:
            seen.append(tok)
    return seen


def build(src: Path) -> dict:
    raw = src.read_bytes()
    doc = yaml.safe_load(raw.decode("utf-8"))
    inputs = {**(doc.get("inputs") or {}), **(doc.get("inputs_proces") or {})}
    kpis = []
    for k in doc["kpis"]:
        if (k.get("cat") or [None])[0] not in DOMAINS:
            continue
        item = {f: k.get(f) for f in FIELDS}
        item["domein"] = k["cat"][0]
        item["inputs"] = []
        for name in identifiers(k["formule"]):
            meta = inputs.get(name) or {}
            item["inputs"].append({"naam": name, "eenheid": meta.get("eenheid"),
                                   "omschrijving": meta.get("omschrijving")})
        kpis.append(item)
    order = {d: i for i, d in enumerate(DOMAINS)}
    kpis.sort(key=lambda x: (order[x["domein"]], x["id"]))
    return {
        "_bron": "business-value-platform/040_Bron/kpi_catalogue.yaml",
        "_sha256": hashlib.sha256(raw).hexdigest(),
        "_let_op": "Gegenereerd door tools/sync-bvip-catalogue.py. Niet met de hand wijzigen.",
        "kpis": kpis,
    }


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalogue", type=Path,
                    default=Path(os.environ.get("BVIP_CATALOGUE") or DEFAULT_SRC))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not args.catalogue.is_file():
        print(f"catalogus niet gevonden: {args.catalogue}", file=sys.stderr)
        return 2
    text = render(build(args.catalogue))
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print(f"{OUT.name} loopt achter op de catalogus: draai tools/sync-bvip-catalogue.py",
                  file=sys.stderr)
            return 1
        print(f"{OUT.name} is actueel")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"{OUT} geschreven: {len(json.loads(text)['kpis'])} KPI's")
    return 0


if __name__ == "__main__":
    sys.exit(main())
