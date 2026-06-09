#!/usr/bin/env python3
"""Hermes-specific entry point for the shared Hoard-Hurt-Help runner.

Hermes uses the same low-token polling loop as the shared connector. This file
exists so the setup flow can hand Hermes users a provider-specific download
without switching them to the MCP path.
"""

from __future__ import annotations

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).with_name("agentludum_connector.py")), run_name="__main__"
    )
