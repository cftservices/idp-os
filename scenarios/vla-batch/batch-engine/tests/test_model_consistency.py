"""factory-model/isa95-vla.json describes the factory; the code implements it. The
two drifted apart unnoticed (1200 packs per pallet against 480 in the warehouse,
StartCip and SetAutostart missing from the method list, a verdict rule that still
said HOLD/re-cook). These checks read the model next to the code, so the next
drift fails a test instead of a demo."""
import json
import os
import re

from vla import model as M
from vla import warehouse

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIO = os.path.normpath(os.path.join(HERE, "..", ".."))
MODEL_FILE = os.path.join(SCENARIO, "factory-model", "isa95-vla.json")
SERVER = os.path.join(SCENARIO, "factory", "server.py")
PHYSICS = os.path.join(SCENARIO, "factory", "physics.py")


def _model():
    with open(MODEL_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def _batch_object(node):
    if isinstance(node, dict):
        if node.get("id") == "LINE-VLA-BATCH":
            return node
        node = list(node.values())
    if isinstance(node, list):
        for x in node:
            found = _batch_object(x)
            if found is not None:
                return found
    return None


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_packs_per_pallet_matches_the_warehouse():
    # line.py estimates pallets with the model value; the warehouse builds them
    assert _model()["packs_per_pallet"] == warehouse.PACKS_PER_PALLET


def test_method_list_matches_the_factory_server():
    declared = {m["name"] for m in _batch_object(_model()["enterprise"])["methods"]}
    built = set(re.findall(r'QualifiedName\("(\w+)", idx\), \w+', _src(SERVER)))
    assert built, "no methods found in factory/server.py"
    assert declared == built


def test_fault_ids_match_the_physics():
    methods = _batch_object(_model()["enterprise"])["methods"]
    declared = set(next(m for m in methods if m["name"] == "InjectFault")["fault_ids"])
    built = set(re.findall(r'fid == "(\w+)"', _src(PHYSICS)))
    assert declared == built


def test_verdict_rule_matches_the_engine():
    rule = _model()["verdict_rule"]
    assert (rule["spec_min_cP"], rule["spec_max_cP"]) == (M.SPEC_MIN_CP, M.SPEC_MAX_CP)
    text = (rule["logic"] + json.dumps(_model()["solve_event"], ensure_ascii=False)).lower()
    assert "herkoken" not in text and "rework" not in text   # no re-cook in this demo
    assert "spec_max" in rule["logic"]                       # both sides of the band
