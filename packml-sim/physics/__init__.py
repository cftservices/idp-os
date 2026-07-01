from .base import PhysicsBase, PhysicsRegistry

# Eager-import all modules so the registry is populated
from . import batch_mixer  # noqa: F401
from . import tunnel_oven  # noqa: F401
from . import packaging_line  # noqa: F401
from . import bulk_fermenter  # noqa: F401
from . import former  # noqa: F401
from . import proofer  # noqa: F401
from . import spiral_cooler  # noqa: F401
from . import cip_station  # noqa: F401
from . import storage_tank  # noqa: F401
from . import separator  # noqa: F401
from . import pasteurizer  # noqa: F401
from . import homogenizer  # noqa: F401
from . import bottler  # noqa: F401
# DairyWorks batch-demo modules
from . import fermenter  # noqa: F401
from . import fill_line  # noqa: F401
from . import palletizer  # noqa: F401
from . import preheater  # noqa: F401

__all__ = ["PhysicsBase", "PhysicsRegistry"]
