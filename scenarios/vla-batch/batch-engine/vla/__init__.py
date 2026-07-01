"""vla batch-engine package — MES-laag voor de Vla Batch v2 demo.

Offline-first: draait volledig zonder Mongo/MQTT. Reframe van de v1 mes-engine
(order -> batch, EBR -> BIRT-stijl batch-rapport).
"""

__all__ = ["model", "bus", "db", "batches", "report"]
