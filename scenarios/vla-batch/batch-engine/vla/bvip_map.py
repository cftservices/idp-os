"""How each of the 29 industrial BVIP KPIs is supplied by the vla demo. Pure data.

The definitions (name, unit, formula, direction, thresholds) come from bvip_catalogue.json,
generated from the BVIP catalogue; this module only says where the inputs come from and what is
missing. Rule: a KPI without a source is "niet_aantoonbaar" and names the missing input. It never
gets a number, and it never gets a substitute that looks like the real thing.

status
  ok                every formula input comes from demo data
  deels             computed, but one input is missing or approximated (see `let_op`)
  niet_aantoonbaar  an input does not exist in the demo yet; `fase` says when it is planned
                    (3 = factory simulation), None = not in the demo at all
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
                    "let_op": "De demo kent twee storingsmodi per machinerol en storingen komen "
                              "vaak voor (DEMO_BREAKDOWNS), dus het aandeel herhalingen is hoog."},
    "KPI-MNT-004": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Kosten per storingswerkorder (zie MNT-002) / gevulde pakken.",
                    "let_op": "Demo-aannames uit cost_model; alleen storingswerkorders."},
    # ---------------------------------------------------------------- energie
    "KPI-ENE-001": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["energy_kwh"], "bron": "De fabriek heeft nog geen energiemeters."},
    "KPI-ENE-002": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["energy_costs", "revenue"],
                    "bron": "Geen energiemeters en geen verkoopprijs in het kostenmodel."},
    "KPI-ENE-003": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["water_l"], "bron": "De fabriek heeft nog geen watermeter."},
    "KPI-ENE-004": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": True,
                    "ontbreekt": ["co2_kg"], "bron": "Geen energiemeters, dus geen CO2."},
    "KPI-ENE-005": {"status": DEELS, "drempels": "catalogus", "additief": True,
                    "bron": "Afval = (afgekeurde pakken + pakken van HOLD/REJECTED-batches) x "
                            "dichtheid; grondstof = gedoseerde kg.",
                    "let_op": "Spoel- en CIP-verliezen ontbreken; die komen met de CIP-simulatie."},
    # ---------------------------------------------------------------- procesvoering
    "KPI-PAC-100": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["product_flow_kg_s", "product_t_in", "product_t_out"],
                    "bron": "Er is nog geen warmtewisselaar met debietmeting."},
    "KPI-PAC-101": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["product_flow_kg_s", "hot_t_in", "hot_t_out", "hx_area_m2"],
                    "bron": "Er is nog geen warmtewisselaar met debietmeting."},
    "KPI-PAC-102": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["u_actual", "u_clean_at_flow"],
                    "bron": "De opwarmtijd van de kookunit is een vervuilingssignaal, geen "
                            "U-waarde; die komt met de pasteur."},
    "KPI-PAC-103": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["dp_bar", "product_flow_kg_s"],
                    "bron": "Er is nog geen drukverschilmeting."},
    "KPI-PAC-104": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["regen_t_out", "cold_t_in", "hold_t"],
                    "bron": "Er is nog geen regeneratiesectie."},
    "KPI-PAC-105": {"status": DEELS, "drempels": "catalogus", "additief": False,
                    "bron": "Productieve uren van de kookunit tussen twee CIP's "
                            "(cip_performed), gemiddeld over de afgeronde cycli in de periode. "
                            "Procesuren.",
                    "let_op": "Kookunit in plaats van een warmtewisselaar; de pasteur volgt."},
    "KPI-PAC-106": {"status": NA, "fase": 3, "drempels": "catalogus", "additief": False,
                    "ontbreekt": ["cip_chemicals_eur", "cip_water_eur", "cip_energy_eur",
                                  "cip_labor_eur"],
                    "bron": "CIP heeft nog geen verbruik of duur."},
}
