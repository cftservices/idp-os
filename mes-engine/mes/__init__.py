"""mes-engine — order-centric MES layer for the DairyWorks batch-dairy demo.

Sits on top of the packml-sim (libremfg PackML-MQTT simulator). Drives orders
through recipes, books consumptions/productions, generates HUs + SSCC, schedules
samples, computes OEE, applies the batch-verdict rule and renders EBRs.

All logic works fully offline (pure-simulation mode) so demos/tests run without a
live broker or Mongo.
"""

__version__ = "1.0.0"
