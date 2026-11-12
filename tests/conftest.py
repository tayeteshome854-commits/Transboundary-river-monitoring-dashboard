"""Shared pytest fixtures.

``fake_ee`` installs the numerical Earth Engine stand-in from
``tests/fake_ee.py`` into the application's client, so the real processing code
path runs against data whose correct answers are known analytically.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import service  # noqa: E402
from app.ee_client import ee_client  # noqa: E402
from app.geometry import clear_geometry_cache  # noqa: E402
from tests import fake_ee  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_caches():
    service.clear_caches()
    yield
    service.clear_caches()


@pytest.fixture
def fake(fake_ee_scenes=None):
    """Install the fake ``ee`` module for one test."""

    fake_ee.reset_scenes()
    if fake_ee_scenes:
        for dataset, images in fake_ee_scenes.items():
            fake_ee.register_scenes(dataset, images)
    ee_client.set_module(fake_ee, project="test-project")
    yield fake_ee
    ee_client.reset()


@pytest.fixture
def no_earth_engine():
    """Guarantee Earth Engine is reported as unavailable."""

    ee_client.reset()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def geometry_cache_reset():
    clear_geometry_cache()
    yield
