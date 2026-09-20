"""Prepared forecasts for followed locations, refreshed independently of page views."""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone

from .catalog import select_candidate_models

logger = logging.getLogger("wind_validation.monitoring")
UTC = timezone.utc


def _time(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def monitoring_active(location: dict, now: datetime) -> bool:
    if not location.get("monitoring_enabled"):
        return False
    today = now.date()
    start, end = location.get("monitoring_start"), location.get("monitoring_end")
    return (not start or date.fromisoformat(str(start)) <= today) and (not end or date.fromisoformat(str(end)) >= today)


def compare_forecasts(previous: dict | None, current: dict, now: datetime) -> dict | None:
    """Compare the same raw model and valid time, never blend/weight changes."""
    if not previous or not previous.get("forecast"):
        return None
    old_models = {m["model_id"]: m for m in previous["forecast"].get("models", [])}
    changes = []
    meaningful = False
    for model in current["forecast"].get("models", []):
        prior = old_models.get(model["model_id"])
        if not prior:
            continue
        old_hours = {_time(h["time_utc"]): h for h in prior.get("hours", [])}
        hours = []
        for hour in model.get("hours", []):
            valid = _time(hour["time_utc"])
            old = old_hours.get(valid)
            if not old or valid < now or any(r.get(k) is None for r in (old, hour) for k in ("ws_ms", "wd_deg")):
                continue
            delta = hour["ws_ms"] - old["ws_ms"]
            direction_delta = (hour["wd_deg"] - old["wd_deg"] + 180) % 360 - 180
            meaningful |= abs(delta) >= 0.1 or abs(direction_delta) >= 1
            hours.append({"time_utc": valid.isoformat(), "previous_ws_ms": old["ws_ms"],
                          "current_ws_ms": hour["ws_ms"], "ws_change_ms": delta,
                          "previous_wd_deg": old["wd_deg"], "current_wd_deg": hour["wd_deg"],
                          "wd_change_deg": direction_delta})
        if hours:
            changes.append({"model_id": model["model_id"], "overlap_hours": len(hours),
                            "mean_ws_change_ms": sum(h["ws_change_ms"] for h in hours) / len(hours),
                            "max_abs_ws_change_ms": max(abs(h["ws_change_ms"]) for h in hours),
                            "mean_wd_change_deg": sum(h["wd_change_deg"] for h in hours) / len(hours),
                            "hours": hours})
    if not meaningful:
        # Keep the most recent meaningful change across identical polling cycles.
        return previous.get("run_comparison")
    return {"kind": "forecast_snapshots", "previous_computed_at_utc": previous["computed_at_utc"],
            "current_computed_at_utc": current["computed_at_utc"], "models": changes}


class LocationMonitoringService:
    def __init__(self, repo, forecast_broker, observation_broker, validation_service, store) -> None:
        self.repo = repo
        self.forecast_broker = forecast_broker
        self.observation_broker = observation_broker
        self.validation_service = validation_service
        self.store = store
        self._lock = threading.Lock()
        self._errors: dict[int, str] = {}

    def snapshot(self, location_id: int) -> dict:
        location = self.store.get_location(location_id)
        if location is None:
            raise KeyError(location_id)
        saved = self.store.read_location_snapshot(location_id)
        result = dict(saved) if saved else {
            "status": "error" if location_id in self._errors else "pending",
            "computed_at_utc": None, "validation": None, "forecast": None, "run_comparison": None,
        }
        result["location"] = location
        if location_id in self._errors:
            result["last_error"] = self._errors[location_id]
        return result

    def refresh(self) -> int:
        if not self.store.enabled:
            return 0
        active = [loc for loc in self.store.list_locations() if monitoring_active(loc, datetime.now(UTC))]
        for location in active:
            self.refresh_location(location["id"])
        return len(active)

    def refresh_location(self, location_id: int) -> dict:
        # A local lock also protects repository buffers when a refresh overlaps a
        # manual request. Deploy one collector process; web processes only read DB.
        with self._lock:
            location = self.store.get_location(location_id)
            if location is None:
                raise KeyError(location_id)
            settings = getattr(self.forecast_broker, "settings", None)
            if settings is not None and not settings.live_forecasts_enabled:
                return self.snapshot(location_id)
            try:
                return self._prepare(location)
            except Exception:
                logger.exception("Monitoring refresh failed for location %s; retaining last complete result", location_id)
                self._errors[location_id] = "Verversen mislukt; de laatste complete verwachting blijft beschikbaar."
                try:
                    self.store.record_location_refresh_error(location_id, self._errors[location_id])
                except Exception:
                    logger.warning("Could not persist collector failure", exc_info=True)
                return self.snapshot(location_id)

    def _prepare(self, location: dict) -> dict:
        now = datetime.now(UTC)
        lat, lon, radius = location["lat"], location["lon"], location["radius_km"]
        country = self.repo.point_country(lat, lon)
        stations = self.observation_broker.list_stations(country, lat, lon, radius)
        known = {s.station_id for s in self.repo.stations}
        self.repo.stations.extend(s for s in stations if s.station_id not in known)
        coords = sorted({(lat, lon)} | {(s.lat, s.lon) for s in stations})
        candidates, _ = select_candidate_models(lat=lat, lon=lon, catalog=self.repo.models,
                                                coverage_availability={}, missing_threshold=1.0)
        start = now.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=48)
        for model in candidates:
            try:
                if model.model_id == "openwrf":
                    rows = self.forecast_broker.openwrf.fetch_forecast_with_extras(model, coords, start, end)
                elif model.model_id == "aladin_cz":
                    from .services import _fetch_aladin_cz_at_coords
                    rows = _fetch_aladin_cz_at_coords(coords, start, end)
                else:
                    rows = self.forecast_broker.openmeteo.fetch_forecast_with_extras(model, coords, start, end)
                # Future snapshots establish availability BEFORE verifying incoming
                # observations. Already-valid backfilled values cannot prove skill.
                rows = [r for r in rows if r.valid_time_utc > now]
                self.store.save_forecasts(rows)
            except Exception:
                logger.warning("Future archive failed for %s at %s", model.model_id, location["id"], exc_info=True)
        # All sources' older archived snapshots are loaded by validate_point and
        # paired only when their actual availability predates the observation.
        validation = self.validation_service.validate_point(lat, lon, 48, radius, force_refresh=True)
        winner = validation.get("winner_model_id")
        bias = next((m.get("bias_ws") or 0.0 for m in validation.get("models", []) if m["model_id"] == winner), 0.0)
        forecast = self.validation_service.forecast_point(lat, lon, winner or "", bias,
                                                          validation.get("query_id"), 48, radius_km=radius)
        if not any(m.get("hours") for m in forecast.get("models", [])):
            raise RuntimeError("No forecast source returned usable hours")
        result = {"location": location, "status": "ready", "computed_at_utc": datetime.now(UTC).isoformat(),
                  "validation": validation, "forecast": forecast, "run_comparison": None}
        previous = self.store.read_location_snapshot(location["id"])
        result["run_comparison"] = compare_forecasts(previous, result, now)
        # Normalize all datetimes once, so in-memory tests and JSONB agree.
        result = json.loads(json.dumps(result, default=lambda value: value.isoformat()))
        self.store.save_location_snapshot(location["id"], result)
        self._errors.pop(location["id"], None)
        return result
