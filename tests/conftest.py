"""Fixtures for the Experia Box tests."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for every test."""
    yield


@pytest.fixture
def devices_xml() -> str:
    """Router response with two usable clients and one skipped entry."""
    return (FIXTURES / "associate_devs.xml").read_text(encoding="utf-8")


@pytest.fixture
def empty_devices_xml() -> str:
    """Router response with an empty client list."""
    return (FIXTURES / "associate_devs_empty.xml").read_text(encoding="utf-8")
