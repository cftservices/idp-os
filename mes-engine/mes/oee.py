"""OEE = Availability x Performance x Quality (ISA-95 factory-model formula)."""

from __future__ import annotations


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def oee(availability: float, performance: float, quality: float) -> dict:
    """Compute OEE from three factors.

    Factors may be given as fractions (0..1) or percentages (0..100); any value
    > 1 is treated as a percentage. Returns fractions + rounded percentages.
    """

    def norm(v: float) -> float:
        v = float(v)
        return _clamp01(v / 100.0 if v > 1.0 else v)

    a, p, q = norm(availability), norm(performance), norm(quality)
    o = a * p * q
    return {
        "availability": round(a, 4),
        "performance": round(p, 4),
        "quality": round(q, 4),
        "oee": round(o, 4),
        "availability_pct": round(a * 100, 2),
        "performance_pct": round(p * 100, 2),
        "quality_pct": round(q * 100, 2),
        "oee_pct": round(o * 100, 2),
    }
