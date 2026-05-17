#!/usr/bin/env python3
"""Launch the Pipeline Designer application."""

import sys
from pathlib import Path

# Ensure the package is importable when run from this directory
sys.path.insert(0, str(Path(__file__).parent))

from pipeline_designer.main import main

sys.exit(main())
