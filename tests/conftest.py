"""Make the toolkit package importable when tests run from the workspace root."""

from __future__ import annotations

import sys
from pathlib import Path


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))
