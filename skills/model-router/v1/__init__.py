import logging as _logging
def log(event, data=None): _logging.getLogger("phoenix").debug("%s %s", event, data or {})
def load_cfg(section): return {}
def get_agents(): return []
def get_chains(): return {}
def get_credentials_loader(): return None
def take_snapshot(*a, **kw): pass
