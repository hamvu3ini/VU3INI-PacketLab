"""VU3INI PacketLab - offline LAN/MAN/WAN design, simulation and test lab."""
import os as _os

APP_NAME = "VU3INI PacketLab"
AUTHOR = "Sasanka Boruah"
CALLSIGN = "VU3INI"
CONTACT_EMAIL = "hamvu3ini@gmail.com"


def _read_version():
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (_os.path.join(here, "VERSION"), _os.path.join(here, "..", "..", "version.txt")):
        try:
            with open(cand, "r", encoding="utf-8") as f:
                v = f.read().strip()
                if v:
                    return v
        except OSError:
            pass
    return "1.0.0"


__version__ = _read_version()
