import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "app"), os.path.join(ROOT, "installer")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.mkdtemp(prefix="packetlab-test-")
os.environ.setdefault("APPDATA", os.path.join(_tmp, "appdata"))
os.environ.setdefault("LOCALAPPDATA", os.path.join(_tmp, "localappdata"))
SAMPLES = os.path.join(ROOT, "app", "packetlab", "samples")
