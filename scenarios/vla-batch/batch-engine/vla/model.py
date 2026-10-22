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

# ---------------------------------------------------------------------------
# Equipment-state classification — THE single source of truth.
#
# Dirty is GEEN stilstand. Een vervuilde kookketel kookt gewoon door; hij warmt
# alleen trager op, en de batch komt er af. Volgens het ISO 22400-tijdmodel is
# dat waarde-toevoegende tijd en hoort het in APT.
#
# Hem als stilstand tellen straft bovendien dubbel: de vervuiling verlaagt al de
# performance via de opwarmtrend, en dezelfde tijd zou daarnaast als
# onbeschikbaar meetellen. Dat is precies de dubbeltelling die het verliesblok
# elders verbiedt. In productie leverde het een benuttingsgraad van 0,68 procent
# op voor een lijn die de hele week batches maakte: geen meting maar een
# artefact.
#
# These live HERE and not in kpi.py because /oee (equipment.py) and
# /kpi/summary (kpi.py) both classify the same state history. When the sets were
# duplicated, the reclassification landed in kpi.py only and the two endpoints
# disagreed on screen: 0 % availability next to 100 % utilization for the same
# cook unit. tests/test_oee_health.py fails if they diverge again.
#
# Openstaand en bewust niet meegenomen: de tijd dat de lijn WACHT op een CIP
# omdat Dirty nieuwe batches blokkeert, is wel echte stilstand. Die is nu niet
# apart zichtbaar; daarvoor moet de statehistorie onderscheiden of er in dat
# interval een batch liep.
DOWNTIME_STATES = {"Down", "Error"}
PRODUCTIVE_STATES = {"Running", "Dirty"}
NEUTRAL_STATES = {"Idle", "Allocated"}

# Alarm severities
CRITICAL, HIGH, MEDIUM, LOW = "Critical", "High", "Medium", "Low"

# Order lifecycle (PR-24)
ORDER_OPEN, ORDER_RUNNING, ORDER_DONE = "OPEN", "RUNNING", "DONE"

# HandlingUnit (PR-35, light packaging & expedition)
HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED = (
    "wrapped", "stored", "awaiting_shipment", "shipped",
)
HU_STATUSES = [HU_WRAPPED, HU_STORED, HU_AWAITING, HU_SHIPPED]
LOC_COLDSTORE, LOC_EXPEDITION = "koelmagazijn", "expeditie"

# SSCC placeholder prefix — NEVER a real GS1 company prefix (anonymization rule).
SSCC_PREFIX = "80"


def sscc_check_digit(d17: str) -> int:
    """GS1 mod-10 check digit over a 17-digit base (rightmost digit weight 3)."""
    total = sum(int(c) * (3 if i % 2 == 0 else 1)
                for i, c in enumerate(reversed(d17)))
    return (10 - total % 10) % 10


def new_hu_id() -> str:
    """18-digit SSCC-placeholder: '80' + date + random serial + check digit."""
    import random as _random
    from datetime import datetime as _dt
    base = f"{SSCC_PREFIX}{_dt.now().strftime('%y%m%d')}{_random.randint(0, 10**9 - 1):09d}"
    return base + str(sscc_check_digit(base))

# ISA-88 operations context layer (PR-23) — derived from the batch FSM.
OPERATION_OF_STATE = {
    DOSING: "Preparation",
    COOKING: "Processing",
    COOLING: "Processing",
    FILLING: "Packaging",
}


# ------------------------------------------------------------------ ISA-95 map
# Area -> Equipment for topic building (§ISA-95 model + equipment + tags).
# equipment_id is the OPC-UA browsename / last-two UNS segments' equipment.
AREA_OF: dict[str, str] = {
    "receiving-tank-01": "Receiving",
    "process-tank-01": "Mixing",
    "cook-unit-01": "Cook",
    "cooler-01": "Cooling",
    "filler-01": "Filling",
    "receiving-tank-02": "Receiving",
    "process-tank-02": "Mixing",
    "cook-unit-02": "Cook",
    "cooler-02": "Cooling",
    "filler-02": "Filling",
    "Batch": "Batch",  # line-level object (no Area segment)
}

# The dose materials in the Mixing tank and their command-target short names
# used by SetSetpoint("dose.<name>", value) — §OPC-UA methods.
DOSE_MATERIALS = ["milk", "sugar", "starch", "cocoa",
                  "vanilla", "banana", "caramel", "strawberry"]
# Basisgrondstoffen; alles daarbuiten in een recept is de smaak.
BASE_MATERIALS = ("milk", "sugar", "starch")


# ------------------------------------------------------------------ lijnen
# Twee lijnen, elk met een eigen fabriek (OPC-UA) en een eigen UNS-root.
#
# L1 houdt bewust exact zijn oude namen: root DairyWorks/Vla en de -01
# equipment-ids. Historian, Grafana en het archief kijken daar al naar; een
# hernoeming zou een datamigratie kosten zonder dat iemand er iets aan heeft.
# L2 publiceert onder DairyWorks/Vla-L2, een eigen niveau, zodat de
# subscriptie DairyWorks/Vla/# van L1 hem niet per ongeluk oppikt.
#
# Equipment-rollen: de code spreekt in rollen (cook, filler, ...), het
# register vertaalt per lijn naar het id. Zo staat "cook-unit-01" nog maar op
# één plek en kan een derde lijn erbij zonder zoektocht.
ROLES = ("receiving", "process", "cook", "cooler", "filler")


@dataclass(frozen=True)
class Line:
    line_id: str
    name: str
    hall: str
    uns_root: str
    opcua_env: str
    opcua_default: str
    equipment: dict
    recipes: tuple

    @property
    def equipment_ids(self) -> list[str]:
        return [self.equipment[r] for r in ROLES]

    def eq(self, role: str) -> str:
        return self.equipment[role]


LINES: dict[str, Line] = {
    "L1": Line(
        line_id="L1", name="Lijn 1 · chocolade", hall="Hal 1",
        uns_root=UNS_ROOT,
        opcua_env="OPCUA_URL", opcua_default="opc.tcp://vla-factory:4840/DairyWorks",
        equipment={"receiving": "receiving-tank-01", "process": "process-tank-01",
                   "cook": "cook-unit-01", "cooler": "cooler-01",
                   "filler": "filler-01"},
        recipes=("chocolate-vla-1L",),
    ),
    "L2": Line(
        line_id="L2", name="Lijn 2 · smaken", hall="Hal 2",
        uns_root=f"{SITE}/{LINE}-L2",
        opcua_env="OPCUA_URL_L2",
        opcua_default="opc.tcp://vla-factory-l2:4840/DairyWorks",
        equipment={"receiving": "receiving-tank-02", "process": "process-tank-02",
                   "cook": "cook-unit-02", "cooler": "cooler-02",
                   "filler": "filler-02"},
        recipes=("vanilla-vla-1L", "banana-vla-1L", "caramel-vla-1L",
                 "strawberry-vla-1L"),
    ),
}
DEFAULT_LINE = "L1"
ALL_EQUIPMENT_IDS = [e for ln in LINES.values() for e in ln.equipment_ids]


def get_line(line_id: Optional[str]) -> Line:
    return LINES.get(line_id or DEFAULT_LINE) or LINES[DEFAULT_LINE]


def line_of_equipment(equipment_id: str) -> str:
    for ln in LINES.values():
        if equipment_id in ln.equipment_ids:
            return ln.line_id
    return DEFAULT_LINE


def line_of_recipe(recipe_id: Optional[str]) -> str:
    for ln in LINES.values():
        if recipe_id in ln.recipes:
            return ln.line_id
    return DEFAULT_LINE


def line_of_doc(doc: Optional[dict]) -> str:
    """Lijn van een batch/order/alarm. Oude documenten hebben geen veld en
    zijn per definitie van L1: die bestond als enige."""
    if not doc:
        return DEFAULT_LINE
    if doc.get("line"):
        return doc["line"]
    if doc.get("recipe_id"):
        return line_of_recipe(doc["recipe_id"])
    if doc.get("equipment_id"):
        return line_of_equipment(doc["equipment_id"])
    return DEFAULT_LINE


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

FINISHED_GOOD_ID = "vla-1L"  # chocolade; de naam is ouder dan de andere smaken


def _flavour(mid: str, name: str, stock: float, reorder: float,
             shelf: int = 365) -> Material:
    return Material(mid, name, "kg", category="DryPowder",
                    tolerance_pos_pct=1.0, tolerance_neg_pct=1.0,
                    shelf_life_days=shelf, stock_qty=stock, reorder_level=reorder)


def _fg(mid: str, name: str) -> Material:
    return Material(mid, name, "pack", category="FinishedGood",
                    shelf_life_days=21, stock_qty=0.0, reorder_level=0.0)


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
    # Smaakgrondstoffen voor L2. Hoeveelheden zijn demowaarden, niet geverifieerd.
    "vanilla":    _flavour("vanilla", "Vanilla extract", 80.0, 30.0),
    "banana":     _flavour("banana", "Banana puree", 400.0, 150.0, shelf=180),
    "caramel":    _flavour("caramel", "Caramel syrup", 800.0, 300.0),
    "strawberry": _flavour("strawberry", "Strawberry puree", 1000.0, 350.0, shelf=180),
    FINISHED_GOOD_ID:   _fg(FINISHED_GOOD_ID, "Chocolate Vla 1L"),
    "fg-vanilla-1L":    _fg("fg-vanilla-1L", "Vanilla Vla 1L"),
    "fg-banana-1L":     _fg("fg-banana-1L", "Banana Vla 1L"),
    "fg-caramel-1L":    _fg("fg-caramel-1L", "Caramel Vla 1L"),
    "fg-strawberry-1L": _fg("fg-strawberry-1L", "Strawberry Vla 1L"),
}

SAMPLE_TYPES = ["dose_check", "cook_temp", "hold", "viscosity"]  # 06-Model B.1


def _recipe(rid: str, name: str, flavour: str, flavour_kg: float,
            sugar_kg: float = 500.0) -> Recipe:
    return Recipe(
        recipe_id=rid, product_name=name, basis_L=5000.0,
        doses=[Dose(material_id="milk", qty_target=5000.0),
               Dose(material_id="sugar", qty_target=sugar_kg),
               Dose(material_id="starch", qty_target=250.0),
               Dose(material_id=flavour, qty_target=flavour_kg)],
        cook_setpoint_C=88.0, hold_sec=300.0, cool_target_C=22.0,
        spec_min_cP=SPEC_MIN_CP, spec_max_cP=SPEC_MAX_CP, agitator_rpm=60.0,
    )


RECIPE_CHOCOLATE_VLA_1L = _recipe("chocolate-vla-1L", "Chocolate Vla (1L)", "cocoa", 100.0)

RECIPES: dict[str, Recipe] = {r.recipe_id: r for r in (
    RECIPE_CHOCOLATE_VLA_1L,
    _recipe("vanilla-vla-1L", "Vanilla Vla (1L)", "vanilla", 12.0),
    _recipe("banana-vla-1L", "Banana Vla (1L)", "banana", 60.0),
    _recipe("caramel-vla-1L", "Caramel Vla (1L)", "caramel", 120.0, sugar_kg=400.0),
    _recipe("strawberry-vla-1L", "Strawberry Vla (1L)", "strawberry", 150.0),
)}

# Gereed product per recept. Chocolade houdt zijn oude id vla-1L, zodat de
# bestaande voorraad, HU's en verzendingen niet hoeven te verhuizen.
FG_OF_RECIPE: dict[str, str] = {
    "chocolate-vla-1L": FINISHED_GOOD_ID,
    "vanilla-vla-1L": "fg-vanilla-1L",
    "banana-vla-1L": "fg-banana-1L",
    "caramel-vla-1L": "fg-caramel-1L",
    "strawberry-vla-1L": "fg-strawberry-1L",
}
FINISHED_GOOD_IDS = frozenset(FG_OF_RECIPE.values())

# Productwissel: van licht naar donker mag direct, omgekeerd niet. Een
# donkere of allergene smaak (aardbei: kleur en allergeen-claim, chocolade:
# kleur) laat resten achter die je in een lichter product terugziet.
# Een lagere rang na een hogere vraagt dus een CIP, reden "productwissel".
RECIPE_RANK: dict[str, int] = {
    "vanilla-vla-1L": 0,
    "banana-vla-1L": 1,
    "caramel-vla-1L": 2,
    "strawberry-vla-1L": 3,
    "chocolate-vla-1L": 4,
}


def fg_of_recipe(recipe_id: Optional[str]) -> str:
    return FG_OF_RECIPE.get(recipe_id or "", FINISHED_GOOD_ID)


def is_finished_good(material_id: Optional[str]) -> bool:
    return material_id in FINISHED_GOOD_IDS


def flavour_of(recipe) -> Optional[str]:
    if isinstance(recipe, str):
        recipe = get_recipe(recipe)
    if recipe is None:
        return None
    for d in recipe.doses:
        if d.material_id not in BASE_MATERIALS:
            return d.material_id
    return None


def changeover_needs_cip(prev_recipe: Optional[str], next_recipe: Optional[str]) -> bool:
    if not prev_recipe or not next_recipe or prev_recipe == next_recipe:
        return False
    return RECIPE_RANK.get(next_recipe, 0) < RECIPE_RANK.get(prev_recipe, 0)


def get_recipe(recipe_id: str) -> Optional[Recipe]:
    return RECIPES.get(recipe_id)


# ------------------------------------------------------------- topic helpers
# §UNS topics (MonsterMQ, LOCK)

def area_of(equipment_id: str) -> str:
    return AREA_OF.get(equipment_id, "Processing")


def status_topic(equipment_id: str, tag: str, root: Optional[str] = None) -> str:
    """{root}/{Area}/{Equipment}/Status/{tag}; root volgt uit de lijn van het
    equipment, behalve voor het lijnobject 'Batch' (dat geeft de bus mee).

    The line-level 'Batch' object has no Area segment:
    DairyWorks/Vla/Batch/Status/{tag}.
    """
    if equipment_id == "Batch":
        return f"{root or UNS_ROOT}/Batch/Status/{tag}"
    root = root or get_line(line_of_equipment(equipment_id)).uns_root
    area = area_of(equipment_id)
    return f"{root}/{area}/{equipment_id}/Status/{tag}"


def command_topic(equipment_id: str, cmd: str, root: Optional[str] = None) -> str:
    """{root}/{Area}/{Equipment}/Command/{cmd}.

    Line-level Batch commands:
    DairyWorks/Vla/Batch/Command/{StartBatch|Stop|InjectFault|ClearFault|TakeSample}.
    """
    if equipment_id == "Batch":
        return f"{root or UNS_ROOT}/Batch/Command/{cmd}"
    root = root or get_line(line_of_equipment(equipment_id)).uns_root
    area = area_of(equipment_id)
    return f"{root}/{area}/{equipment_id}/Command/{cmd}"


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
