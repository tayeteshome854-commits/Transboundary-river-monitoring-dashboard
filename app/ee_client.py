"""Earth Engine access: lazy initialisation, availability, timeouts, retries.

Design rules
------------
* ``ee`` is imported lazily.  The dashboard must boot, serve its vector data and
  explain its own state even when ``earthengine-api`` is missing or unauthorised.
* ``ee.Authenticate()`` is **never** called from the server: it is interactive
  and would hang a production process.  The operator authenticates once
  (documented in ``docs/DEPLOYMENT.md``) or supplies a service-account key.
* All Earth Engine work runs in a worker thread with an explicit timeout, so the
  asyncio event loop is never blocked by a long ``getInfo()``.
* Failures are surfaced as ``EarthEngineUnavailableError`` (HTTP 503) — never as
  a zero, an empty list or a fabricated statistic.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

from .config import settings
from .errors import EarthEngineError, EarthEngineUnavailableError
from .logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

_RECHECK_SECONDS = 120.0
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="ee")


@dataclass
class EEState:
    """Current state of the Earth Engine connection."""

    initialised: bool = False
    available: bool = False
    checked_at: Optional[float] = None
    project: Optional[str] = None
    credential_source: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    calls: int = 0
    failures: int = 0
    total_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initialised": self.initialised,
            "available": self.available,
            "checked_at": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.checked_at)) if self.checked_at else None
            ),
            "project": self.project,
            "credential_source": self.credential_source,
            "error": self.error,
            "attempts": self.attempts,
            "calls": self.calls,
            "failures": self.failures,
            "average_call_seconds": round(self.total_seconds / self.calls, 3) if self.calls else None,
            "notes": list(self.notes),
        }


class EarthEngineClient:
    """Thin, testable wrapper around the ``ee`` module."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ee: Any = None
        self.state = EEState()
        self._override: Any = None

    # ------------------------------------------------------------- injection
    def set_module(self, module: Any, *, project: str = "test-project", note: str = "injected module") -> None:
        """Install an ``ee``-compatible module (used by the test suite)."""

        self._override = module
        self._ee = module
        self.state = EEState(
            initialised=True,
            available=True,
            checked_at=time.time(),
            project=project,
            credential_source="injected",
            notes=[note],
        )

    def reset(self) -> None:
        with self._lock:
            self._override = None
            self._ee = None
            self.state = EEState()

    @property
    def using_injected_module(self) -> bool:
        return self._override is not None

    # ------------------------------------------------------- initialisation
    def module(self) -> Any:
        """Return the ``ee`` module, raising ``EarthEngineUnavailableError`` if unusable."""

        if self._ee is not None:
            return self._ee
        with self._lock:
            if self._ee is not None:
                return self._ee
            self._initialise_locked()
            if self._ee is None:
                raise EarthEngineUnavailableError(
                    "Earth Engine is not available",
                    detail={"reason": self.state.error, "project": self.state.project},
                )
            return self._ee

    def _initialise_locked(self) -> None:
        self.state.attempts += 1
        self.state.checked_at = time.time()
        self.state.project = settings.ee_project_id
        try:
            import ee  # type: ignore
        except Exception as exc:
            self.state.error = (
                f"earthengine-api could not be imported ({exc}). Install the dependency and "
                "authenticate before enabling satellite products."
            )
            self._ee = None
            logger.warning(self.state.error)
            return

        try:
            if settings.ee_credentials_file:
                import json
                from pathlib import Path

                key_path = Path(settings.ee_credentials_file)
                if not key_path.exists():
                    raise FileNotFoundError(f"EE_SERVICE_ACCOUNT_KEY not found: {key_path}")
                info = json.loads(key_path.read_text())
                credentials = ee.ServiceAccountCredentials(info["client_email"], str(key_path))
                ee.Initialize(credentials=credentials, project=settings.ee_project_id)
                self.state.credential_source = "service_account_key"
            else:
                ee.Initialize(project=settings.ee_project_id)
                self.state.credential_source = "user_default_credentials"

            self._ee = ee
            self.state.initialised = True
            self.state.available = True
            self.state.error = None
            logger.info("Earth Engine initialised (project=%s)", settings.ee_project_id)
        except Exception as exc:
            self._ee = None
            self.state.initialised = False
            self.state.available = False
            self.state.error = _friendly_auth_error(exc)
            logger.warning("Earth Engine initialisation failed: %s", self.state.error)

    # ----------------------------------------------------------- availability
    def is_available(self, *, force: bool = False) -> bool:
        """Best-effort availability check (throttled, never raises)."""

        if self.using_injected_module:
            return True
        state = self.state
        if (
            not force
            and state.checked_at is not None
            and time.time() - state.checked_at < _RECHECK_SECONDS
        ):
            return state.available
        try:
            self.module()
            return True
        except EarthEngineUnavailableError:
            return False

    def describe(self) -> dict[str, Any]:
        return self.state.to_dict()

    # -------------------------------------------------------------- execution
    def call(self, func: Callable[..., T], *args: Any, timeout: Optional[float] = None, **kwargs: Any) -> T:
        """Run a blocking Earth Engine callable in a worker thread with timeout."""

        limit = settings.ee_timeout_seconds if timeout is None else timeout
        attempt = 0
        last_error: Optional[BaseException] = None
        while attempt <= settings.ee_max_retries:
            attempt += 1
            started = time.time()
            future = _EXECUTOR.submit(func, *args, **kwargs)
            try:
                result = future.result(timeout=limit)
                self.state.calls += 1
                self.state.total_seconds += time.time() - started
                return result
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                self.state.failures += 1
                raise EarthEngineError(
                    f"Earth Engine call exceeded the {limit:.0f}s timeout",
                    detail={"timeout_seconds": limit, "attempt": attempt},
                ) from exc
            except Exception as exc:  # noqa: BLE001 - translated below
                self.state.failures += 1
                last_error = exc
                elapsed = time.time() - started
                self.state.total_seconds += elapsed
                if _is_transient(exc) and attempt <= settings.ee_max_retries:
                    backoff = settings.ee_retry_backoff_seconds * attempt
                    logger.warning(
                        "Earth Engine call failed (%s); retry %d/%d in %.1fs",
                        exc,
                        attempt,
                        settings.ee_max_retries,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise EarthEngineError(str(exc), detail={"attempt": attempt}) from exc
        raise EarthEngineError(str(last_error) if last_error else "Earth Engine call failed")


def _is_transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "429",
        "rate",
        "timeout",
        "timed out",
        "503",
        "502",
        "temporarily",
        "connection reset",
        "eof occurred",
    )
    return any(marker in text for marker in markers)


def _friendly_auth_error(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    if "please authorize" in lowered or "credentials" in lowered or "oauth" in lowered:
        return (
            "Earth Engine credentials were not found. Run `ee.Authenticate()` once on this "
            "machine (or set EE_SERVICE_ACCOUNT_KEY to a service-account JSON file), then "
            "restart the API. See docs/DEPLOYMENT.md."
        )
    if "project" in lowered and ("not found" in lowered or "access" in lowered):
        return (
            f"The configured Earth Engine project '{settings.ee_project_id}' could not be used. "
            "Check EE_PROJECT_ID and that the account has access to the project and its assets."
        )
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "unreachable" in lowered:
        return "Earth Engine servers could not be reached from this host (no outbound network access)."
    return text


#: Process-wide client instance used by the services and the API.
ee_client = EarthEngineClient()
