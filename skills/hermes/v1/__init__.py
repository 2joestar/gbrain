# Standalone shims so hermes.py runs outside Apple's package tree.
import logging as _logging


def log(event: str, data: dict = None) -> None:
    _logging.getLogger("hermes").debug("%s %s", event, data or {})


def load_cfg(section: str) -> dict:
    return {}
