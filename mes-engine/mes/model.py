"""Load + index the ISA-95 DairyWorks factory model (single source of truth).

Reads isa95-dairyworks.json and exposes helpers for recipes, units, materials,
sample-types and area_of(equipment). No side effects on import.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Default location relative to this repo layout:
#   sub-os/idp-os/mes-engine/mes/model.py
#   sub-os/idp-os/scenarios/dairyworks/factory-model/isa95-dairyworks.json
_DEFAULT_MODEL = (
    Path(__file__).resolve().parents[2]
    / "scenarios"
    / "dairyworks"
    / "factory-model"
    / "isa95-dairyworks.json"
)


def resolve_model_path() -> Path:
    """FACTORY_MODEL env var wins, else the default scenarios path."""
    env = os.environ.get("FACTORY_MODEL")
    if env:
        return Path(env)
    return _DEFAULT_MODEL


class FactoryModel:
    """Indexed view over the ISA-95 factory model JSON."""

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self._units: dict[str, dict] = {}
        self._area_of: dict[str, str] = {}
        self._recipes: dict[str, dict] = {}
        self._materials: dict[str, dict] = {}
        self._index()

    # ---------------------------------------------------------------- indexing

    def _index(self) -> None:
        for site in self.data.get("enterprise", {}).get("sites", []):
            for area in site.get("areas", []):
                area_name = area.get("name")
                for wc in area.get("work_centers", []):
                    eq = wc.get("equipment_id")
                    if not eq:
                        continue
                    self._units[eq] = wc
                    self._area_of[eq] = area_name
        for r in self.data.get("recipes", []):
            self._recipes[r["recipe_id"]] = r
        for m in self.data.get("materials", []):
            self._materials[m["material_id"]] = m

    # ---------------------------------------------------------------- accessors

    @property
    def enterprise_name(self) -> str:
        return self.data.get("enterprise", {}).get("name", "DairyWorks BV")

    @property
    def site_name(self) -> str:
        sites = self.data.get("enterprise", {}).get("sites", [])
        return sites[0].get("name", "DairyWorks Plant") if sites else "DairyWorks Plant"

    @property
    def namespace_root(self) -> str:
        # "DairyWorks/Plant" prefix from the UNS convention.
        conv = self.data.get("namespace_convention", "DairyWorks/Plant/{Area}/{Equipment}/Status/{tag}")
        return "/".join(conv.split("/")[:2])  # DairyWorks/Plant

    def recipes(self) -> dict[str, dict]:
        return self._recipes

    def recipe(self, recipe_id: str) -> Optional[dict]:
        return self._recipes.get(recipe_id)

    def units(self) -> dict[str, dict]:
        return self._units

    def unit(self, equipment_id: str) -> Optional[dict]:
        return self._units.get(equipment_id)

    def materials(self) -> dict[str, dict]:
        return self._materials

    def material(self, material_id: str) -> Optional[dict]:
        return self._materials.get(material_id)

    def sample_types(self) -> list[dict]:
        return self.data.get("sample_types", [])

    def equipment_states(self) -> list[str]:
        return self.data.get("equipment_states", [])

    def oee_targets(self) -> dict:
        return self.data.get("oee", {}).get("targets", {})

    def sscc_prefix(self) -> str:
        return str(self.data.get("sscc", {}).get("prefix", "80"))

    def solve_event(self) -> dict:
        return self.data.get("solve_event", {})

    def area_of(self, equipment_id: str) -> Optional[str]:
        """Return the Area name (Storage/Preparation/Processing/Packaging) for a unit."""
        return self._area_of.get(equipment_id)

    def status_topic(self, equipment_id: str, tag: str) -> str:
        """DairyWorks/Plant/{Area}/{equipment}/Status/{tag}."""
        area = self.area_of(equipment_id) or "Processing"
        return f"{self.namespace_root}/{area}/{equipment_id}/Status/{tag}"

    def command_topic(self, equipment_id: str, cmd: str) -> str:
        """DairyWorks/Plant/{Area}/{equipment}/Command/{cmd}."""
        area = self.area_of(equipment_id) or "Processing"
        return f"{self.namespace_root}/{area}/{equipment_id}/Command/{cmd}"


def load_model(path: Optional[str | Path] = None) -> FactoryModel:
    p = Path(path) if path else resolve_model_path()
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return FactoryModel(data)
