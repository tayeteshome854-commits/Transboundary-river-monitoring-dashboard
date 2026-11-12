"""Error model and machine-readable data-status vocabulary.

The dashboard must never turn a failure into a plausible-looking number.  Every
product therefore carries a ``data_status`` and, when something went wrong, the
API returns an error response that explains what is missing instead of an empty
or zero-filled payload.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class DataStatus(str, Enum):
    """Lifecycle state of an observation product.

    ``current``      valid observation, acquired within ``FRESH_DAYS``
    ``delayed``      valid observation, but older than ``FRESH_DAYS``
    ``stale``        valid observation, older than ``STALE_DAYS``
    ``insufficient_observations``  the window contained too few usable scenes
    ``unavailable``  the data source could not be reached (no value returned)
    ``demonstration``  synthetic data, clearly labelled as such
    """

    CURRENT = "current"
    DELAYED = "delayed"
    STALE = "stale"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    UNAVAILABLE = "unavailable"
    DEMONSTRATION = "demonstration"


STATUS_LABELS = {
    DataStatus.CURRENT: "Current observation",
    DataStatus.DELAYED: "Delayed — observation older than the freshness window",
    DataStatus.STALE: "Stale — no recent observation available",
    DataStatus.INSUFFICIENT_OBSERVATIONS: "Insufficient valid observations in the selected window",
    DataStatus.UNAVAILABLE: "Data source unavailable",
    DataStatus.DEMONSTRATION: "DEMONSTRATION DATA — not an observation",
}


class DashboardError(Exception):
    """Base class for application errors that map onto HTTP responses."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class UnknownBasinError(DashboardError):
    status_code = 404
    code = "unknown_basin"


class UnknownIndicatorError(DashboardError):
    status_code = 404
    code = "unknown_indicator"


class InvalidRequestError(DashboardError):
    status_code = 400
    code = "invalid_request"


class EarthEngineUnavailableError(DashboardError):
    status_code = 503
    code = "earth_engine_unavailable"


class EarthEngineError(DashboardError):
    status_code = 502
    code = "earth_engine_error"


class InsufficientObservationsError(DashboardError):
    status_code = 422
    code = "insufficient_observations"


class HydrologyNotConnectedError(DashboardError):
    status_code = 501
    code = "hydrology_not_connected"


def classify_freshness(age_days: Optional[float], fresh_days: int, stale_days: int) -> DataStatus:
    """Map the age of the newest valid observation onto a data status."""

    if age_days is None:
        return DataStatus.UNAVAILABLE
    if age_days <= fresh_days:
        return DataStatus.CURRENT
    if age_days <= stale_days:
        return DataStatus.DELAYED
    return DataStatus.STALE
