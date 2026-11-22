"""How each of the 29 industrial BVIP KPIs is supplied by the vla demo. Pure data.

The definitions (name, unit, formula, direction, thresholds) come from bvip_catalogue.json,
generated from the BVIP catalogue; this module only says where the inputs come from and what is
missing. Rule: a KPI without a source is "niet_aantoonbaar" and names the missing input. It never
gets a number, and it never gets a substitute that looks like the real thing.

status
  ok                every formula input comes from demo data
  deels             computed, but one input is missing or approximated (see `let_op`)
  niet_aantoonbaar  an input does not exist in the demo; `fase` None = not in the demo at all
drempels
  catalogus         colour by doel / waarsch / krit from the catalogue
  model:<key>       colour by kpi_targets[<key>] of the factory model (a real line norm)
  geen              catalogue thresholds are on another time basis (per year); no colour
additief
  True when every input can be summed over 5-minute buckets, so Grafana can evaluate the
  catalogue formula exactly over any chosen period from idp.kpi_input.
"""
from __future__ import annotations

OK, DEELS, NA = "ok", "deels", "niet_aantoonbaar"

BVIP_MAP: dict[str, dict] = {
    # ---------------------------------------------------------------- productie
    "KPI-PRD-001": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Batches per lijn: looptijd (unie van batchspans), geplande tijd "
                            "(lijn 24/7 gepland), gevulde en goedgekeurde pakken; ideaal tempo "
                            "per lijn uit KPI_IDEAL_PACKS_PER_H. Uren in procestijd."},
    "KPI-PRD-002": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Unie van Down/Error-intervallen van de machines van de lijn, "
                            "gedeeld door de geplande tijd. Procesuren."},
    "KPI-PRD-003": {"status": OK, "drempels": "model:throughput_rate", "additief": True,
                    "bron": "Gevulde pakken / looptijd. De catalogusnorm (6/u) is generiek; de "
                            "kleur volgt de lijnnorm uit het fabrieksmodel."},
    "KPI-PRD-004": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Gevulde pakken / (geplande uren x ideaal tempo)."},
    "KPI-PRD-005": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Goede pakken van APPROVED batches / gevulde pakken.",
                    "let_op": "Er bestaat geen herbewerking in de demo, dus 'goed zonder "
                              "herbewerking' is 'goed in een goedgekeurde batch'."},
    "KPI-PRD-006": {"status": NA, "fase": None, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["units_reworked"],
                    "bron": "Er wordt geen herbewerking geboekt: HOLD en REJECTED gaan niet terug "
                            "het proces in."},
    "KPI-PRD-007": {"status": OK, "drempels": "geen", "additief": True,
                    "bron": "Tijd tussen het einde van een batch en de start van de volgende "
                            "batch met een ander recept op dezelfde lijn (inclusief reiniging), "
                            "geteld bij de start van de nieuwe batch. Procesuren.",
                    "let_op": "L1 maakt één product en heeft dus geen omstellingen; de "
                              "catalogusdrempels (500 h) gelden per jaar."},
    "KPI-PRD-008": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Orders van de lijn met een leverdatum in de periode die al verstreken "
                            "is: op tijd = afgerond voor de leverdatum. Een order die nog kan "
                            "halen telt nog niet mee."},
    # ---------------------------------------------------------------- kwaliteit
    "KPI-QUA-001": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Afgekeurde pakken (vuller) / gevulde pakken."},
    "KPI-QUA-002": {"status": DEELS, "drempels": "geen", "additief": True,
                    "ontbreekt": ["other_quality_cost"],
                    "bron": "Afkeur x waarde per pak + HOLD/REJECTED-batches x herwerkkosten, "
                            "uit het kostenmodel van de fabriek (demo-aanname).",
                    "let_op": "Overige kwaliteitskosten (klachten, audits) bestaan niet in de "
                              "demo; de catalogusdrempels zijn per jaar."},
    "KPI-QUA-003": {"status": NA, "fase": None, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["components_defective", "components_received"],
                    "bron": "Geen ingangskeuring: goederenontvangst boekt alleen hoeveelheden."},
    "KPI-QUA-004": {"status": NA, "fase": None, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["audit_findings"],
                    "bron": "Er is geen kwaliteitssysteem met audits in de demo."},
    "KPI-QUA-005": {"status": NA, "fase": None, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["corrective_actions_closed", "corrective_actions_total"],
                    "bron": "Correctieve acties (8D) leven in de CI-module van aios; er is geen "
                            "koppeling terug."},
    # ---------------------------------------------------------------- onderhoud
    "KPI-MNT-001": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Productieve tijd (Running/Dirty) van de machines van de lijn / "
                            "storingswerkorders in de periode. Procesuren. Zonder storing geen "
                            "waarde (niet oneindig).",
                    "let_op": "Gepoold over de machines van de lijn; de catalogus rekent per "
                              "asset. De storingsfrequentie is die van DEMO_BREAKDOWNS."},
    "KPI-MNT-002": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Kosten per storingswerkorder (reparatietijd x uurtarief + onderdelen "
                            "per machinerol) gedeeld door het naar tijd evenredige deel van de "
                            "vervangingswaarde (waarde x procesuren / 8760). Opgeteld over een "
                            "periode is dat het onderhoudskostenpercentage per jaar.",
                    "let_op": "Tarieven en vervangingswaarden zijn demo-aannames uit cost_model; "
                              "gepland en CBM-onderhoud hebben nog geen kosten."},
    "KPI-MNT-003": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Een storing is een herhaling als dezelfde machine binnen 7 "
                            "procesdagen eerder met dezelfde oorzaak uitviel.",
                    "let_op": "Gesimuleerd: acht storingsmodi per machinerol, en een herhaling "
                              "ontstaat alleen na een noodoplossing (werkorderveld fix, kans "
                              "DEMO_WORKAROUND_P, standaard 40%). Verwacht rond 17%."},
    "KPI-MNT-004": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Kosten per storingswerkorder (zie MNT-002) / gevulde pakken.",
                    "let_op": "Demo-aannames uit cost_model; alleen storingswerkorders."},
    # ---------------------------------------------------------------- energie
    # Sinds fase 3 (2026-11-19) heeft de gesimuleerde fabriek energie- en watermeters.
    # Verbruik = de som van de positieve stappen van de tellers in de periode.
    "KPI-ENE-001": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Gas naar de ketel plus elektriciteit (tellers energy-meter) / "
                            "gevulde pakken.",
                    "let_op": "Gesimuleerde meters: warmte uit massa x cp x temperatuurstijging, "
                              "motoren over procestijd (door de versnelling aan de lage kant)."},
    "KPI-ENE-002": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Gas x gasprijs + elektriciteit x stroomprijs, gedeeld door goede pakken "
                            "x verkoopprijs.",
                    "let_op": "Prijzen en verkoopprijs zijn demo-aannames uit cost_model; omzet is "
                              "goedgekeurde pakken x prijs, geen gefactureerde omzet."},
    "KPI-ENE-003": {"status": OK, "drempels": "catalogus", "additief": True,
                    "bron": "Water (teller water-meter, vooral CIP) / gevulde pakken."},
    "KPI-ENE-004": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Gas x 0,202 + elektriciteit x 0,35 kg CO2 per kWh, gedeeld door gevulde "
                            "pakken.",
                    "let_op": "Emissiefactoren zijn demo-aannames uit cost_model."},
    "KPI-ENE-005": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Afval = (afgekeurde pakken + pakken van HOLD/REJECTED-batches) x "
                            "dichtheid; grondstof = gedoseerde kg.",
                    "let_op": "Spoelverliezen van de CIP zijn niet als product meegeteld."},
    # ---------------------------------------------------------------- procesvoering
    # Intake-pasteur met regeneratie (fase 3). Gemiddeld over de metingen terwijl er melk
    # stroomt; U en Rf rekent de engine zelf uit temperaturen en debiet (vla/meters.py).
    "KPI-PAC-100": {"status": OK, "drempels": "geen", "additief": False,
                    "bron": "Debiet x cp x (houdtemperatuur - temperatuur na regeneratie): de "
                            "warmte die de verwarmingssectie levert.",
                    "let_op": "Hangt af van de grootte van de installatie; de catalogusnorm (600 kW) "
                              "geldt voor een grotere pasteur, dus geen kleur."},
    "KPI-PAC-101": {"status": OK, "drempels": "catalogus", "additief": False,
                    "bron": "Warmtedoorgangscoefficient U = duty / (oppervlak x LMTD), uit de "
                            "gemeten temperaturen aan beide zijden en het debiet."},
    "KPI-PAC-102": {"status": OK, "drempels": "catalogus", "additief": False,
                    "bron": "Rf = 1/U - 1/U_schoon, met U_schoon de U van de eerste metingen na "
                            "de laatste CIP. Stuurt ook het reinigingsmoment (grens 0,06)."},
    "KPI-PAC-103": {"status": OK, "drempels": "catalogus", "additief": False,
                    "bron": "Drukval x (referentiedebiet / debiet)^2."},
    "KPI-PAC-104": {"status": OK, "drempels": "catalogus", "additief": False,
                    "bron": "(temperatuur na regeneratie - melk in) / (houdtemperatuur - melk in)."},
    "KPI-PAC-105": {"status": OK, "drempels": "catalogus", "additief": False,
                    "bron": "Draaiuren van de pasteur tussen twee reinigingen, uit de CIP-cycli die "
                            "in de periode afliepen.",
                    "let_op": "De demo reinigt na ~12 batches = ~1,5 procesuur pasteurtijd; een echte "
                              "pasteur draait 8 tot 12 uur, dus deze KPI staat rood."},
    "KPI-PAC-106": {"status": DEELS, "drempels": "catalogus", "additief": False,
                    "bron": "Per CIP-cyclus: loog en zuur x kiloprijs + water x m3-prijs + gas en "
                            "elektriciteit x prijs + 0,5 uur arbeid.",
                    "let_op": "Prijzen zijn demo-aannames uit cost_model."},
}
