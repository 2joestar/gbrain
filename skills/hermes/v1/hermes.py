"""
Deprecated. hermes has been renamed to gatekeeper.
This shim re-exports for one release; remove in Run 5.
"""

import warnings
import sys
from pathlib import Path

_gk_path = str(Path(__file__).parents[2] / "gatekeeper" / "v1")
if _gk_path not in sys.path:
    sys.path.insert(0, _gk_path)

warnings.warn(
    "phoenix_skills.hermes is deprecated; import from phoenix_skills.gatekeeper instead. "
    "This shim will be removed in Run 5.",
    DeprecationWarning,
    stacklevel=2,
)

from gatekeeper import Gatekeeper as _Gatekeeper  # noqa: E402

# Back-compat: old name still works during Run 4
Hermes = _Gatekeeper
