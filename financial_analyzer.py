#!/usr/bin/env python3
"""Entry point for the interactive analyzer and its CLI commands."""
from __future__ import annotations

import sys

from fa.cli import main

if __name__ == "__main__":
    sys.exit(main())
