"""Root conftest: configure headless Qt for all tests."""

import os

# Must be set before any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication.  One instance is enough for all Qt tests."""
    app = QApplication.instance() or QApplication([])
    yield app
