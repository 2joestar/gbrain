# Standalone shims so gatekeeper.py runs outside Apple's package tree.
import logging as _logging


def log(event: str, data: dict = None) -> None:
    _logging.getLogger("gatekeeper").debug("%s %s", event, data or {})


def load_cfg(section: str) -> dict:
    return {}


from .gatekeeper import Gatekeeper  # noqa: E402

# Back-compat alias — old import sites still work during Run 4
Hermes = Gatekeeper
