import logging as _logging
def log(event, data=None): _logging.getLogger("tools").debug("%s %s", event, data or {})
