#!/usr/bin/env python3
"""Compatibility shim for the renamed connector runner.

The real runner now lives at scripts/agentludum_connector.py. Keep this file so
older local imports and tests can still load the module path they already know.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_REAL = Path(__file__).with_name("agentludum_connector.py")
_SPEC = spec_from_file_location("agentludum_connector", _REAL)
assert _SPEC and _SPEC.loader
_MOD = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MOD
_SPEC.loader.exec_module(_MOD)

for _name, _value in vars(_MOD).items():
    if _name.startswith("__"):
        continue
    globals()[_name] = _value

if __name__ == "__main__":
    _MOD.main()
