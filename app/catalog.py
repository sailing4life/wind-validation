from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from .domain import ModelDefinition
from .geo import detect_country, in_bbox


def default_model_catalog() -> list[ModelDefinition]:
    return [
        ModelDefinition(
            model_id="harmonie_nl",
            provider="KNMI",
            category="regional",
            coverage_bbox={"min_lat": 49.5, "max_lat": 54.5, "min_lon": 2.0, "max_lon": 8.5},
            priority_by_country={"NL": 1, "FR": 50, "IT": 50, "OTHER": 90},
        ),
        ModelDefinition(
            model_id="arome_fr",
            provider="Meteo-France",
            category="regional",
            coverage_bbox={"min_lat": 37.5, "max_lat": 52.0, "min_lon": -6.0, "max_lon": 11.0},
            priority_by_country={"FR": 1, "NL": 40, "IT": 35, "OTHER": 80},
        ),
        ModelDefinition(
            model_id="arome_hd",
            provider="Meteo-France",
            category="regional",
            coverage_bbox={"min_lat": 35.0, "max_lat": 55.0, "min_lon": -10.0, "max_lon": 15.0},
            priority_by_country={"FR": 2, "IT": 30, "NL": 45, "OTHER": 75},
        ),
        ModelDefinition(
            model_id="icon_it",
            provider="DWD",
            category="regional",
            coverage_bbox={"min_lat": 35.0, "max_lat": 48.0, "min_lon": 5.0, "max_lon": 20.0},
            priority_by_country={"IT": 1, "FR": 45, "NL": 60, "OTHER": 90},
        ),
        ModelDefinition(
            model_id="icon_eu",
            provider="DWD",
            category="regional",
            coverage_bbox={"min_lat": 29.5, "max_lat": 70.5, "min_lon": -23.5, "max_lon": 62.5},
            priority_by_country={"NL": 3, "FR": 3, "IT": 3, "OTHER": 3},
        ),
        ModelDefinition(
            model_id="arpege",
            provider="Meteo-France",
            category="regional",
            coverage_bbox={"min_lat": 20.0, "max_lat": 72.0, "min_lon": -32.0, "max_lon": 42.0},
            priority_by_country={"NL": 4, "FR": 2, "IT": 4, "OTHER": 4},
        ),
        ModelDefinition(
            model_id="ecmwf_global",
            provider="ECMWF",
            category="global",
            coverage_bbox={"min_lat": -90.0, "max_lat": 90.0, "min_lon": -180.0, "max_lon": 180.0},
            priority_by_country={"NL": 10, "FR": 10, "IT": 10, "OTHER": 10},
            is_global_baseline=True,
        ),
    ]


def catalog_as_dict(catalog: Iterable[ModelDefinition]) -> list[dict]:
    return [asdict(item) for item in catalog]


def select_candidate_models(
    *,
    lat: float,
    lon: float,
    catalog: list[ModelDefinition],
    coverage_availability: dict[str, float],
    missing_threshold: float = 0.25,
) -> tuple[list[ModelDefinition], dict[str, str]]:
    country = detect_country(lat, lon)
    reasons: dict[str, str] = {}
    covered = [m for m in catalog if in_bbox(lat, lon, m.coverage_bbox) and m.status == "ACTIVE"]
    covered.sort(key=lambda m: m.priority_by_country.get(country, 99))

    selected: list[ModelDefinition] = []
    baseline = next((m for m in catalog if m.is_global_baseline and m.status == "ACTIVE"), None)

    for model in covered:
        availability = coverage_availability.get(model.model_id, 0.0)
        if availability < (1.0 - missing_threshold):
            reasons[model.model_id] = "excluded_missing_coverage"
            continue
        if model.is_global_baseline:
            continue
        selected.append(model)

    if baseline:
        baseline_availability = coverage_availability.get(baseline.model_id, 0.0)
        if baseline_availability >= (1.0 - missing_threshold):
            selected.append(baseline)
        else:
            reasons[baseline.model_id] = "excluded_missing_coverage"

    return selected, reasons