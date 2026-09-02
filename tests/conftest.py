"""
Shared test setup.

The server modules build their SQLAlchemy engine and read JWT_SECRET at import
time, so both have to be in place before anything under `catena.server` is
imported. Hence a conftest rather than fixtures.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("JWT_SECRET", "test-secret-not-used-anywhere-else")
os.environ.setdefault(
    "CATENA_DB", str(Path(tempfile.mkdtemp(prefix="catena-tests-")) / "test.db")
)
