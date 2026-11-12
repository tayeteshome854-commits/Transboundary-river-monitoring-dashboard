"""Hydrological monitoring integration point (gauges, telemetry, institutional feeds).

Satellite indices are **not** river stage or discharge, so this module keeps the
two evidence streams apart.  No live gauge feed is connected to this deployment,
therefore:

* :func:`configured_provider` returns ``None`` unless ``HYDROLOGY_PROVIDER`` is
  set to a registered adapter,
* the API returns HTTP 501 with the exact contract a provider must satisfy,
* nothing is simulated: no synthetic stage, discharge or rainfall values are
  ever produced here.

To connect a real feed, implement :class:`HydrologyProvider` (for example an
HTTP client for a national telemetry service), register it in ``PROVIDERS`` and
set ``HYDROLOGY_PROVIDER=<name>``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from .errors import HydrologyNotConnectedError
from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class Station:
    """A gauge/telemetry station as reported by its data provider."""

    id: str
    name: str
    latitude: float
    longitude: float
    variables: list[str]
    units: dict[str, str]
    reporting_interval: str
    provider: str
    quality_flag: Optional[str] = None


@dataclass
class Reading:
    station_id: str
    variable: str
    timestamp: str
    value: Optional[float]
    unit: str
    quality_flag: str
    source: str


class HydrologyProvider(Protocol):
    """Contract every hydrological data source must implement."""

    name: str

    def stations(self) -> list[Station]: ...

    def observations(self, station_id: str, start: str, end: str) -> list[Reading]: ...


PROVIDERS: dict[str, HydrologyProvider] = {}


def configured_provider() -> Optional[HydrologyProvider]:
    name = os.getenv("HYDROLOGY_PROVIDER", "").strip().lower()
    if not name or name in {"none", "null"}:
        return None
    provider = PROVIDERS.get(name)
    if provider is None:
        logger.warning("HYDROLOGY_PROVIDER=%s is not a registered adapter; hydrology stays offline", name)
    return provider


def require_provider() -> HydrologyProvider:
    provider = configured_provider()
    if provider is None:
        raise HydrologyNotConnectedError(
            "Operational hydrological monitoring is not connected",
            detail={
                "status": "not_connected",
                "explanation": (
                    "No gauge, telemetry or institutional feed is configured for this "
                    "deployment. Satellite indicators shown elsewhere in this dashboard are "
                    "not river stage or discharge."
                ),
                "how_to_connect": (
                    "Implement app.hydrology.HydrologyProvider (stations() and "
                    "observations(station_id, start, end)), register it in app.hydrology.PROVIDERS "
                    "and set the HYDROLOGY_PROVIDER environment variable."
                ),
                "required_reading_fields": [
                    "station_id",
                    "variable",
                    "timestamp",
                    "value",
                    "unit",
                    "quality_flag",
                    "source",
                ],
                "simulated_data": "never produced by this application",
            },
        )
    return provider


def integration_contract() -> dict[str, Any]:
    """Machine readable description of the hydrology integration point."""

    provider = configured_provider()
    return {
        "connected": provider is not None,
        "provider": getattr(provider, "name", None),
        "variables_expected": ["river_stage", "discharge", "rainfall", "water_level"],
        "station_fields": [
            "id",
            "name",
            "latitude",
            "longitude",
            "variables",
            "units",
            "reporting_interval",
            "provider",
            "quality_flag",
        ],
        "reading_fields": ["station_id", "variable", "timestamp", "value", "unit", "quality_flag", "source"],
        "quality_flags": ["good", "suspect", "missing", "delayed", "sensor_failure"],
        "note": (
            "When no provider is connected the dashboard states that operational gauge "
            "monitoring is unavailable instead of displaying simulated values."
        ),
    }


__all__ = ["HydrologyProvider", "PROVIDERS", "Reading", "Station", "configured_provider", "integration_contract", "require_provider"]
