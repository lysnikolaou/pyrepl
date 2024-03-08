import os

TREACE_FILENAME = os.environ.get("PYREPL_TRACE")
if TREACE_FILENAME is not None:
    TRACE_FILE = open(TREACE_FILENAME, "a")
else:
    TRACE_FILE = None


def trace(line, *k, **kw):
    if TRACE_FILE is None:
        return
    if k or kw:
        line = line.format(*k, **kw)
    TRACE_FILE.write(line + "\n")
    TRACE_FILE.flush()
