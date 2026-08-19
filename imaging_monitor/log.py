"""Thin wrapper over stdlib logging so the project stays dependency-free.

Logs go to stderr (stdout is reserved for the CLI's JSON result). Call
``configure(verbose)`` once at startup, then ``get_logger(__name__)`` anywhere.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure(verbose: bool = False) -> None:
    global _CONFIGURED
    level = logging.DEBUG if verbose else logging.INFO
    if _CONFIGURED:
        logging.getLogger("imaging_monitor").setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s", "%H:%M:%S"))
    root = logging.getLogger("imaging_monitor")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("imaging_monitor").getChild(name.split(".")[-1])
