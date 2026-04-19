import logging as _logging
def log(event, data=None): _logging.getLogger("memory").debug("%s %s", event, data or {})
def load_cfg(section): return {}
