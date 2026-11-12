"""API contracts: request validation models and the error envelope.

Response bodies are plain dictionaries built by the service layer (they carry a
variable number of indicators), but every *input* is validated here and every
error leaves the API in the same shape, so the frontend can rely on it.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .indicators import normalise_indicator


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)


class ErrorResponse(BaseModel):
    error: ErrorBody


class TimeSeriesQuery(BaseModel):
    """Query model for ``/api/basin/{basin_id}/timeseries/{indicator}``."""

    start_year: int = Field(default=2017, ge=2000, le=2100)
    end_year: int = Field(default=2026, ge=2000, le=2100)
    aggregation: str = Field(default="year")
    region: str = Field(default="basin")

    @field_validator("aggregation")
    @classmethod
    def _check_aggregation(cls, value: str) -> str:
        if value not in {"year", "month"}:
            raise ValueError("aggregation must be 'year' or 'month'")
        return value

    @field_validator("region")
    @classmethod
    def _check_region(cls, value: str) -> str:
        if value not in {"basin", "buffer"}:
            raise ValueError("region must be 'basin' or 'buffer'")
        return value


class ChangeRequest(BaseModel):
    """Body model for ``/api/basin/{basin_id}/change``."""

    indicator: str = "ndvi"
    baseline_start: str
    baseline_end: str
    comparison_start: str
    comparison_end: str
    region: str = "basin"
    include_map: bool = True

    @field_validator("indicator")
    @classmethod
    def _check_indicator(cls, value: str) -> str:
        resolved = normalise_indicator(value)
        if resolved is None:
            raise ValueError(f"unknown indicator '{value}'")
        return resolved

    @field_validator("region")
    @classmethod
    def _check_region(cls, value: str) -> str:
        if value not in {"basin", "buffer"}:
            raise ValueError("region must be 'basin' or 'buffer'")
        return value


class RefreshRequest(BaseModel):
    basin_id: Optional[str] = None
    indicator: Optional[str] = None


__all__ = [
    "ChangeRequest",
    "ErrorBody",
    "ErrorResponse",
    "RefreshRequest",
    "TimeSeriesQuery",
    "utc_now",
]
