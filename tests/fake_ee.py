"""A small, numerical stand-in for the Earth Engine Python API.

This is **test infrastructure**, not application code.  It exists so the real
processing chain in :mod:`app.satellite`, :mod:`app.statistics` and
:mod:`app.timeseries` can be executed and checked without Earth Engine
credentials: bands are numpy arrays, indices are computed with the documented
formulas, masks really remove pixels, and reductions really average them.

Anything the application calls on ``ee`` that is not implemented here raises
``NotImplementedError`` on purpose, so a change in the processing chain shows up
as a failing test instead of silently passing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import numpy as np

# --------------------------------------------------------------------- helpers


def _as_array(value: Any) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value.astype(float)
    if isinstance(value, Image):  # image <op> image (e.g. difference maps)
        if len(value.bands) != 1:
            raise ValueError(f"image arithmetic expects a single band, got {list(value.bands)}")
        return next(iter(value.bands.values())).astype(float)
    return np.full(_SHAPE, float(value))


_SHAPE = (8, 8)


class ComputedObject:
    """Lazy value; ``getInfo()`` materialises it (like the real API)."""

    def __init__(self, producer: Callable[[], Any]) -> None:
        self._producer = producer

    def getInfo(self) -> Any:  # noqa: N802 - mirrors the Earth Engine API
        return self._producer()

    def reduce(self, reducer: "Reducer") -> "ComputedObject":
        return ComputedObject(lambda: reducer.reduce_list(self.getInfo()))

    def gt(self, other: Any) -> "ComputedObject":
        return ComputedObject(lambda: bool(self.getInfo() > other))


class Reducer:
    def __init__(self, ops: tuple[str, ...]) -> None:
        self.ops = ops

    def combine(self, other: "Reducer", sharedInputs: bool = False) -> "Reducer":  # noqa: N803
        return Reducer(tuple(dict.fromkeys(self.ops + other.ops)))

    def reduce_list(self, values: Iterable[Any]) -> Any:
        values = [value for value in values if value is not None]
        if not values:
            return None
        if "max" in self.ops:
            return max(values)
        if "min" in self.ops:
            return min(values)
        return float(np.mean(values))

    @staticmethod
    def mean() -> "Reducer":
        return Reducer(("mean",))

    @staticmethod
    def count() -> "Reducer":
        return Reducer(("count",))

    @staticmethod
    def stdDev() -> "Reducer":  # noqa: N802
        return Reducer(("stdDev",))

    @staticmethod
    def max() -> "Reducer":  # noqa: N802
        return Reducer(("max",))

    @staticmethod
    def min() -> "Reducer":  # noqa: N802
        return Reducer(("min",))


class Image:
    """Bands as numpy arrays plus a valid-pixel mask."""

    def __init__(
        self,
        bands: dict[str, np.ndarray],
        mask: Optional[np.ndarray] = None,
        properties: Optional[dict[str, Any]] = None,
    ) -> None:
        self.bands = {name: np.asarray(array, dtype=float) for name, array in bands.items()}
        shape = next(iter(self.bands.values())).shape if self.bands else _SHAPE
        self.mask = np.ones(shape, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
        self.properties = dict(properties or {})
        self.recorded: dict[str, Any] = {}

    # ------------------------------------------------------------ band access
    def select(self, names: Any) -> "Image":
        if isinstance(names, str):
            names = [names]
        missing = [name for name in names if name not in self.bands]
        if missing and self.bands:
            # A bandless image is what Earth Engine produces for an empty
            # collection; selecting from it only fails when the value is
            # computed, so it is allowed through here.
            raise KeyError(f"band(s) {missing} not present in {list(self.bands)}")
        if missing:
            return Image({}, self.mask.copy(), self.properties)
        return Image({name: self.bands[name] for name in names}, self.mask.copy(), self.properties)

    def rename(self, names: Any) -> "Image":
        if isinstance(names, str):
            names = [names]
        keys = list(self.bands)
        if len(names) != len(keys):
            raise ValueError(f"rename expects {len(keys)} names, got {names}")
        return Image(dict(zip(names, (self.bands[key] for key in keys))), self.mask.copy(), self.properties)

    def bandNames(self) -> ComputedObject:  # noqa: N802
        return ComputedObject(lambda: list(self.bands))

    def addBands(self, others: Iterable["Image"]) -> "Image":  # noqa: N802
        bands = dict(self.bands)
        for other in others:
            bands.update(other.bands)
        return Image(bands, self.mask.copy(), self.properties)

    # --------------------------------------------------------------- masking
    def updateMask(self, mask_image: "Image") -> "Image":  # noqa: N802
        flag = next(iter(mask_image.bands.values()))
        return Image(dict(self.bands), self.mask & (flag > 0), self.properties)

    def bitwiseAnd(self, value: int) -> "Image":  # noqa: N802
        band = next(iter(self.bands.values()))
        return Image({"mask": np.bitwise_and(band.astype(np.int64), int(value)).astype(float)}, self.mask.copy())

    def eq(self, value: float) -> "Image":  # noqa: N802
        band = next(iter(self.bands.values()))
        return Image({"mask": (band == value).astype(float)}, self.mask.copy())

    def neq(self, value: float) -> "Image":  # noqa: N802
        band = next(iter(self.bands.values()))
        return Image({"mask": (band != value).astype(float)}, self.mask.copy())

    def And(self, other: "Image") -> "Image":  # noqa: N802
        left = next(iter(self.bands.values()))
        right = next(iter(other.bands.values()))
        return Image({"mask": ((left > 0) & (right > 0)).astype(float)}, self.mask.copy())

    # ------------------------------------------------------------- arithmetic
    def _binary(self, other: Any, op: Callable[[np.ndarray, np.ndarray], np.ndarray]) -> "Image":
        arrays = {name: op(array, _as_array(other)) for name, array in self.bands.items()}
        mask = self.mask.copy()
        if isinstance(other, Image):
            # A pixel is only valid where both operands are valid (as in Earth Engine).
            mask = mask & other.mask
        return Image(arrays, mask, self.properties)

    def divide(self, value: Any) -> "Image":
        return self._binary(value, lambda a, b: np.divide(a, b, out=np.full_like(a, np.nan), where=b != 0))

    def multiply(self, value: Any) -> "Image":
        return self._binary(value, lambda a, b: a * b)

    def subtract(self, value: Any) -> "Image":
        return self._binary(value, lambda a, b: a - b)

    def add(self, value: Any) -> "Image":
        return self._binary(value, lambda a, b: a + b)

    def normalizedDifference(self, names: list[str]) -> "Image":  # noqa: N802
        first, second = (self.bands[names[0]], self.bands[names[1]])
        denominator = first + second
        with np.errstate(divide="ignore", invalid="ignore"):
            values = np.where(denominator == 0, np.nan, (first - second) / denominator)
        mask = self.mask & ~np.isnan(values)
        return Image({"nd": np.nan_to_num(values, nan=0.0)}, mask, self.properties)

    def expression(self, expression: str, variables: dict[str, "Image"]) -> "Image":
        namespace = {name: next(iter(image.bands.values())) for name, image in variables.items()}
        allowed = set(namespace) | {"np"}
        evaluated = eval(  # noqa: S307 - test fake, expression comes from our own registry
            expression.replace("^", "**"),
            {"__builtins__": {}},
            {**namespace, "np": np},
        )
        if not isinstance(evaluated, np.ndarray):
            evaluated = np.full(_SHAPE, float(evaluated))
        del allowed
        return Image({"expression": evaluated}, self.mask.copy(), self.properties)

    def clip(self, geometry: Any) -> "Image":
        self.recorded["clipped"] = True
        return self

    def visualize(self, **kwargs: Any) -> "Image":
        self.recorded["vis"] = kwargs
        return self

    # -------------------------------------------------------------- reduction
    def reduceRegion(  # noqa: N802
        self,
        reducer: Reducer,
        geometry: Any = None,
        scale: Optional[float] = None,
        bestEffort: bool = False,  # noqa: N803
        maxPixels: float = 1e9,  # noqa: N803
        tileScale: float = 1,  # noqa: N803
    ) -> ComputedObject:
        self.recorded.update({"scale": scale, "bestEffort": bestEffort, "maxPixels": maxPixels})
        bands = dict(self.bands)
        mask = self.mask.copy()

        def compute() -> dict[str, Any]:
            output: dict[str, Any] = {}
            suffix = len(reducer.ops) > 1
            for name, array in bands.items():
                valid = array[mask & ~np.isnan(array)]
                for op in reducer.ops:
                    key = f"{name}_{op}" if suffix else name
                    if op == "mean":
                        output[key] = float(valid.mean()) if valid.size else None
                    elif op == "count":
                        output[key] = int(valid.size)
                    elif op == "stdDev":
                        output[key] = float(valid.std()) if valid.size else None
                    elif op == "max":
                        output[key] = float(valid.max()) if valid.size else None
                    elif op == "min":
                        output[key] = float(valid.min()) if valid.size else None
                    else:  # pragma: no cover - guards the fake, not the app
                        raise NotImplementedError(op)
            return output

        return ComputedObject(compute)

    def getMapId(self, vis: Optional[dict] = None) -> dict[str, Any]:  # noqa: N802
        self.recorded["map_vis"] = vis

        class _Fetcher:
            url_format = "https://earthengine.googleapis.com/v1alpha/fake/maps/{map_id}/tiles/{z}/{x}/{y}"

        return {"map_id": "fake-map", "tile_fetcher": _Fetcher()}

    def get(self, key: str) -> ComputedObject:
        return ComputedObject(lambda: self.properties.get(key))


class ImageCollection:
    def __init__(self, source: Any) -> None:
        if isinstance(source, str):
            self.id = source
            self.images: list[Image] = list(SCENES.get(source, []))
        elif isinstance(source, list):
            self.id = "inline"
            self.images = list(source)
        else:  # pragma: no cover - the app never does this
            raise TypeError(f"unsupported collection source {type(source)}")

    # -------------------------------------------------------------- filtering
    def filterDate(self, start: str, end: str) -> "ImageCollection":  # noqa: N802
        start_ms = _to_millis(start)
        end_ms = _to_millis(end)
        return ImageCollection(
            [
                image
                for image in self.images
                if start_ms <= int(image.properties.get("system:time_start", 0)) < end_ms
            ]
        )

    def filterBounds(self, region: Any) -> "ImageCollection":  # noqa: N802
        return ImageCollection(list(self.images))

    def filter(self, predicate: "Filter") -> "ImageCollection":
        return ImageCollection([image for image in self.images if predicate.matches(image)])

    def select(self, names: Any) -> "ImageCollection":
        return ImageCollection([image.select(names) for image in self.images])

    def map(self, func: Callable[[Image], Image]) -> "ImageCollection":
        return ImageCollection([func(image) for image in self.images])

    def sort(self, key: str, ascending: bool = True) -> "ImageCollection":
        return ImageCollection(
            sorted(self.images, key=lambda image: image.properties.get(key, 0), reverse=not ascending)
        )

    # ------------------------------------------------------------ aggregating
    def size(self) -> ComputedObject:
        return ComputedObject(lambda: len(self.images))

    def first(self) -> Optional[Image]:
        return self.images[0] if self.images else None

    def aggregate_array(self, key: str) -> ComputedObject:
        return ComputedObject(lambda: [image.properties.get(key) for image in self.images])

    def _reduce(self, op: str) -> Image:
        if not self.images:
            return Image({})
        names = list(self.images[0].bands)
        stacked = {
            name: np.stack([image.bands.get(name, np.full(_SHAPE, np.nan)) for image in self.images])
            for name in names
        }
        masks = np.stack([image.mask for image in self.images])
        with np.errstate(invalid="ignore"):
            masked = {
                name: np.where(masks, array, np.nan) for name, array in stacked.items()
            }
            if op == "median":
                result = {name: np.nanmedian(array, axis=0) for name, array in masked.items()}
            elif op == "mean":
                result = {name: np.nanmean(array, axis=0) for name, array in masked.items()}
            elif op == "sum":
                result = {name: np.nansum(masked_array, axis=0) for name, masked_array in masked.items()}
            else:  # pragma: no cover - guards the fake
                raise NotImplementedError(op)
        return Image({name: np.nan_to_num(array, nan=np.nan) for name, array in result.items()})

    def median(self) -> Image:
        return self._reduce("median")

    def mean(self) -> Image:
        return self._reduce("mean")

    def sum(self) -> Image:
        return self._reduce("sum")

    def count(self) -> Image:
        count = np.sum(np.stack([image.mask for image in self.images]), axis=0) if self.images else np.zeros(_SHAPE)
        return Image({"count": count.astype(float)})


class Filter:
    def __init__(self, name: str, op: str, value: Any) -> None:
        self.name = name
        self.op = op
        self.value = value

    def matches(self, image: Image) -> bool:
        actual = image.properties.get(self.name)
        if actual is None:
            return True
        if self.op == "lt":
            return actual < self.value
        if self.op == "gt":
            return actual > self.value
        return actual == self.value

    @staticmethod
    def lt(name: str, value: Any) -> "Filter":
        return Filter(name, "lt", value)

    @staticmethod
    def gt(name: str, value: Any) -> "Filter":
        return Filter(name, "gt", value)


class Feature:
    """``ee.Feature`` stand-in; properties may be lazy ``ee`` objects."""

    def __init__(self, geometry: Any, properties: Any) -> None:
        self.geometry = geometry
        self.properties: dict[str, Any] = properties if isinstance(properties, dict) else {"__computed__": properties}

    def set(self, *args: Any, **kwargs: Any) -> "Feature":
        updates: dict[str, Any] = dict(kwargs)
        for index in range(0, len(args), 2):
            updates[args[index]] = args[index + 1]
        self.properties.update(updates)
        return self

    def getInfo(self) -> dict[str, Any]:  # noqa: N802
        resolved: dict[str, Any] = {}
        for key, value in self.properties.items():
            if key == "__computed__":
                materialised = (
                    value.getInfo() if isinstance(value, (ComputedObject, Dictionary)) else value
                )
                if isinstance(materialised, dict):
                    resolved.update(materialised)
                continue
            resolved[key] = (
                value.getInfo() if isinstance(value, (ComputedObject, Dictionary)) else value
            )
        return {"type": "Feature", "geometry": self.geometry, "properties": resolved}


class FeatureCollection:
    def __init__(self, features: Any) -> None:
        self.features = list(features)

    def getInfo(self) -> dict[str, Any]:  # noqa: N802
        return {"type": "FeatureCollection", "features": [feature.getInfo() for feature in self.features]}


class Geometry:
    def __init__(self, geojson: Any) -> None:
        self.geojson = geojson


class Dictionary:
    """``ee.Dictionary`` stand-in: a lazy dict of properties."""

    def __init__(self, value: Any = None) -> None:
        self._value = value if value is not None else {}

    def getInfo(self) -> dict[str, Any]:  # noqa: N802
        value = self._value.getInfo() if isinstance(self._value, ComputedObject) else self._value
        return dict(value or {})


class Algorithms:
    """Server-side conditional, evaluated lazily like the real API."""

    @staticmethod
    def If(condition: Any, when_true: Any, when_false: Any) -> ComputedObject:  # noqa: N802
        def evaluate() -> Any:
            taken = condition.getInfo() if isinstance(condition, ComputedObject) else bool(condition)
            branch = when_true if taken else when_false
            return branch.getInfo() if isinstance(branch, (ComputedObject, Dictionary)) else branch

        return ComputedObject(evaluate)


def _to_millis(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(text, fmt)) * 1000)
        except ValueError:
            continue
    raise ValueError(f"cannot parse date '{value}'")


# ------------------------------------------------------------------- datasets

@dataclass
class SceneSpec:
    """A synthetic Sentinel-2 scene with known reflectances."""

    date: str
    nir: float = 0.45
    red: float = 0.10
    green: float = 0.12
    blue: float = 0.08
    swir: float = 0.20
    cloud_fraction: float = 0.0
    cloudy_percentage: float = 5.0


def build_sentinel_scene(spec: SceneSpec) -> Image:
    """Create a scene whose indices are analytically known from ``spec``."""

    shape = _SHAPE
    cloudy_pixels = int(shape[0] * shape[1] * spec.cloud_fraction)
    qa60 = np.zeros(shape, dtype=np.int64)
    scl = np.full(shape, 4, dtype=float)  # 4 = vegetation
    if cloudy_pixels:
        qa60[:, : int(np.ceil(cloudy_pixels / shape[0]))] = 1 << 10
        scl[:, : int(np.ceil(cloudy_pixels / shape[0]))] = 9  # cloud high probability

    # S2_SR_HARMONIZED stores surface reflectance scaled by 10 000; the scene
    # spec holds reflectance so the tests read naturally.
    bands = {
        "B2": np.full(shape, spec.blue * 10_000),
        "B3": np.full(shape, spec.green * 10_000),
        "B4": np.full(shape, spec.red * 10_000),
        "B8": np.full(shape, spec.nir * 10_000),
        "B11": np.full(shape, spec.swir * 10_000),
        "QA60": qa60.astype(float),
        "SCL": scl,
    }
    return Image(
        bands,
        properties={
            "system:time_start": _to_millis(spec.date),
            "CLOUDY_PIXEL_PERCENTAGE": spec.cloudy_percentage,
        },
    )


def constant_collection(dataset: str, values: list[tuple[str, float]]) -> list[Image]:
    """Ancillary collection (CHIRPS / MODIS / ERA5 / SMAP) with known values."""

    band = {
        "UCSB-CHG/CHIRPS/DAILY": "precipitation",
        "MODIS/061/MOD11A2": "LST_Day_1km",
        "ECMWF/ERA5_LAND/DAILY_AGGR": "temperature_2m",
        "MODIS/061/MOD16A2GF": "ET",
        "NASA/SMAP/SPL4SMGP/007": "sm_surface",
    }[dataset]
    return [
        Image({band: np.full(_SHAPE, value)}, properties={"system:time_start": _to_millis(date)})
        for date, value in values
    ]


#: Registry consumed by ``ImageCollection(id)``.
SCENES: dict[str, list[Image]] = {}


def register_scenes(dataset: str, images: list[Image]) -> None:
    SCENES[dataset] = list(images)


def reset_scenes() -> None:
    SCENES.clear()


__all__ = [
    "Algorithms",
    "ComputedObject",
    "Dictionary",
    "Feature",
    "FeatureCollection",
    "Filter",
    "Geometry",
    "Image",
    "ImageCollection",
    "Reducer",
    "SCENES",
    "SceneSpec",
    "build_sentinel_scene",
    "constant_collection",
    "register_scenes",
    "reset_scenes",
]
