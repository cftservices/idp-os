"""Domain model + recipe seed for the Vla batch-engine.

Reframe of the v1 mes-engine model: order -> Batch, BOM -> Doses, plus the
chocolate-vla-1L recipe seed and the verdict constants from the BUILD CONTRACT.

Single source of truth for tags/topics comes from the ISA-95 model json
(factory-model/isa95-vla.json, owned by another block). This module does NOT
require that file to run: the recipe seed + UNS topic helpers are self-contained
so the batch-engine is offline-first and independently testable.

Contract references:
  * §UNS topics — Status/Command topic builders below match exactly.
  * §Recept + physics — RECIPE_CHOCOLATE_VLA_1L seed.
  * §verdict-regel — SPEC_MIN_CP / SPEC_MAX_CP + verdict constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------- constants

SITE = "DairyWorks"
LINE = "Vla"
UNS_ROOT = f"{SITE}/{LINE}"  # DairyWorks/Vla

# Viscosity spec (cP) — §verdict-regel
SPEC_MIN_CP = 150.0
SPEC_MAX_CP = 300.0

# Batch verdicts — §batch-engine REST verdict-regel
APPROVED, HOLD, REJECTED, PENDING = "APPROVED", "HOLD", "REJECTED", "PENDING"

# Batch lifecycle states (line-level Batch.state) — §Recept + physics
IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE = (
    "IDLE", "DOSING", "COOKING", "COOLING", "FILLING", "COMPLETE",
)
BATCH_STATES = [IDLE, DOSING, COOKING, COOLING, FILLING, COMPLETE]

# Alarm severities
CRITICAL, HIGH, MEDIUM, LOW = "Critical", "High", "Medium", "Low"


# ------------------------------------------------------------------ ISA-95 map
# Area -> Equipment for topic building (§ISA-95 model + equipment + tags).
# equipment_id is the OPC-UA browsename / last-two UNS segments' equipment.
AREA_OF: dict[str, str] = {
    "receiving-tank-01": "Receiving",
    "process-tank-01": "Mixing",
    "cook-unit-01": "Cook",
    "cooler-01": "Cooling",
    "filler-01": "Filling",
    "Batch": "Batch",  # line-level object (no Area segment)
}

# The dose materials in the Mixing tank and their command-target short names
# used by SetSetpoint("dose.<name>", value) — §OPC-UA methods.
DOSE_MATERIALS = ["milk", "sugar", "starch", "cocoa"]


# --------------------------------------------------------------------- dataclasses

@dataclass
class Material:
    """Material-master row (06-Model B.1, MES practice pattern 1)."""
    material_id: str
    name: str
    uom: str = "kg"
    category: str = "DryPowder"          # LiquidBase | DryPowder | FinishedGood
    tolerance_pos_pct: float = 2.0       # percent, per material
    tolerance_neg_pct: float = 2.0
    density_kg_L: Optional[float] = None # liquids only (kg<->L)
    whole_bag: bool = False              # bagged goods: book n x bag_size_kg
    bag_size_kg: Optional[float] = None
    shelf_life_days: Optional[int] = None
    stock_qty: float = 0.0               # PR-27 inventory
    reorder_level: float = 0.0


@dataclass
class Dose:
    """One recipe dose (target) and its booked actual, tolerances from the master."""
    material_id: str
    qty_target: float
    qty_actual: Optional[float] = None
    tol_pos_pct: float = 2.0
    tol_neg_pct: float = 2.0
    uom: str = "kg"
    source_equipment: Optional[str] = None
    lot_no: Optional[str] = None
    operator_id: Optional[str] = None

    @property
    def tol_min(self) -> float:
        return round(self.qty_target * (1.0 - self.tol_neg_pct / 100.0), 4)

    @property
    def tol_max(self) -> float:
        return round(self.qty_target * (1.0 + self.tol_pos_pct / 100.0), 4)

    def in_tolerance(self) -> bool:
        if self.qty_actual is None:
            return True
        return self.tol_min <= self.qty_actual <= self.tol_max


@dataclass
class Sample:
    sample_id: str
    batch_id: str
    sample_type: str
    phase: str
    status: str = "pending"          # pending|completed|approved|failed
    result: Optional[str] = None     # pass|fail|None
    value: Optional[float] = None
    unit: Optional[str] = None
    ts: Optional[str] = None


@dataclass
class Recipe:
    recipe_id: str
    product_name: str
    basis_L: float
    doses: list[Dose]
    cook_setpoint_C: float
    hold_sec: float
    cool_target_C: float
    spec_min_cP: float = SPEC_MIN_CP
    spec_max_cP: float = SPEC_MAX_CP
    agitator_rpm: float = 60.0
    status: str = "released"  # draft|approved|released (pattern 2)

    def scaled_doses(self, planned_L: float) -> list[Dose]:
        """Scale the recipe doses to planned_L (linear to basis_L)."""
        scale = float(planned_L) / float(self.basis_L) if self.basis_L else 1.0
        out: list[Dose] = []
        for d in self.doses:
            mat = MATERIALS.get(d.material_id)
            out.append(Dose(
                material_id=d.material_id,
                qty_target=round(d.qty_target * scale, 4),
                tol_pos_pct=mat.tolerance_pos_pct if mat else 2.0,
                tol_neg_pct=mat.tolerance_neg_pct if mat else 2.0,
                uom=d.uom,
            ))
        return out


@dataclass
class Batch:
    batch_id: str
    recipe_id: str
    product_name: str
    planned_L: float
    state: str = IDLE
    verdict: Optional[str] = None
    doses: list[Dose] = field(default_factory=list)
    # process readings captured from UNS telemetry
    peak_cook_temp_C: Optional[float] = None
    hold_sec: Optional[float] = None
    hold_elapsed_sec: Optional[float] = None
    end_viscosity_cP: Optional[float] = None
    packs_total: int = 0
    reject_count: int = 0
    cook_setpoint_C: Optional[float] = None
    cool_target_C: Optional[float] = None
    spec_min_cP: float = SPEC_MIN_CP
    spec_max_cP: float = SPEC_MAX_CP
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    critical_alarm_during_batch: bool = False


# --------------------------------------------------------------- recipe seed

FINISHED_GOOD_ID = "vla-1L"

MATERIALS = {
    "milk":   Material("milk", "Milk", "kg", category="LiquidBase",
                       tolerance_pos_pct=1.0, tolerance_neg_pct=1.0,
                       density_kg_L=1.03, stock_qty=20000.0, reorder_level=6000.0),
    "sugar":  Material("sugar", "Sugar", "kg", category="DryPowder",
                       tolerance_pos_pct=0.5, tolerance_neg_pct=0.5,
                       shelf_life_days=365, stock_qty=2000.0, reorder_level=600.0),
    "starch": Material("starch", "Modified Starch", "kg", category="DryPowder",
                       tolerance_pos_pct=0.5, tolerance_neg_pct=0.5,
                       whole_bag=True, bag_size_kg=25.0,
                       shelf_life_days=365, stock_qty=1000.0, reorder_level=300.0),
    "cocoa":  Material("cocoa", "Cocoa", "kg", category="DryPowder",
                       tolerance_pos_pct=1.0, tolerance_neg_pct=1.0,
                       shelf_life_days=540, stock_qty=400.0, reorder_level=120.0),
    FINISHED_GOOD_ID: Material(FINISHED_GOOD_ID, "Chocolate Vla 1L", "pack",
                               category="FinishedGood", shelf_life_days=21,
                               stock_qty=0.0, reorder_level=0.0),
}

SAMPLE_TYPES = ["dose_check", "cook_temp", "hold", "viscosity"]  # 06-Model B.1

RECIPE_CHOCOLATE_VLA_1L = Recipe(
    recipe_id="chocolate-vla-1L",
    product_name="Chocolate Vla (1L)",
    basis_L=5000.0,
    doses=[
        Dose(material_id="milk", qty_target=5000.0),
        Dose(material_id="sugar", qty_target=500.0),
        Dose(material_id="starch", qty_target=250.0),
        Dose(material_id="cocoa", qty_target=100.0),
    ],
    cook_setpoint_C=88.0,
    hold_sec=300.0,
    cool_target_C=22.0,
    spec_min_cP=SPEC_MIN_CP,
    spec_max_cP=SPEC_MAX_CP,
    agitator_rpm=60.0,
)

RECIPES: dict[str, Recipe] = {
    RECIPE_CHOCOLATE_VLA_1L.recipe_id: RECIPE_CHOCOLATE_VLA_1L,
}


def get_recipe(recipe_id: str) -> Optional[Recipe]:
    return RECIPES.get(recipe_id)


# ------------------------------------------------------------- topic helpers
# §UNS topics (MonsterMQ, LOCK)

def area_of(equipment_id: str) -> str:
    return AREA_OF.get(equipment_id, "Processing")


def status_topic(equipment_id: str, tag: str) -> str:
    """DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}.

    The line-level 'Batch' object has no Area segment:
    DairyWorks/Vla/Batch/Status/{tag}.
    """
    if equipment_id == "Batch":
        return f"{UNS_ROOT}/Batch/Status/{tag}"
    area = area_of(equipment_id)
    return f"{UNS_ROOT}/{area}/{equipment_id}/Status/{tag}"


def command_topic(equipment_id: str, cmd: str) -> str:
    """DairyWorks/Vla/{Area}/{Equipment}/Command/{cmd}.

    Line-level Batch commands:
    DairyWorks/Vla/Batch/Command/{StartBatch|Stop|InjectFault|ClearFault|TakeSample}.
    """
    if equipment_id == "Batch":
        return f"{UNS_ROOT}/Batch/Command/{cmd}"
    area = area_of(equipment_id)
    return f"{UNS_ROOT}/{area}/{equipment_id}/Command/{cmd}"


def physics_viscosity(peak_temp_C: float, hold_elapsed: float, hold_sec: float) -> float:
    """§Recept + physics — gelatinisation-driven end viscosity (cP).

    g = clamp((peak_temp-70)/(88-70),0,1) * clamp(hold_elapsed/hold_sec,0,1)
    end_viscosity_cP = 30 + g*230   (≈260 at full gelatinisation)
    """
    def clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    temp_term = clamp01((float(peak_temp_C) - 70.0) / (88.0 - 70.0))
    hold_term = clamp01(float(hold_elapsed) / float(hold_sec)) if hold_sec else 0.0
    g = temp_term * hold_term
    return round(30.0 + g * 230.0, 2)
