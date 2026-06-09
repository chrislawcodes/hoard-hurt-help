#!/usr/bin/env python3
"""OpenClaw-specific entry point for the shared Hoard-Hurt-Help runner.

OpenClaw uses the same polling runner pattern as the shared connector. This
file exists so the setup flow can hand OpenClaw users a provider-specific
download without switching them to the MCP path.
"""

from __future__ import annotations

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).with_name("agentludum_connector.py")), run_name="__main__"
    )
