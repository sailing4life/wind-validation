import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv_file() -> None:
    dotenv_path = Path(__file__).resolve().parent.parent / ".env"
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_file()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    app_name: str = "Wind Validation"
    default_hours_back: int = 48
    default_radius_km: float = 50.0
    max_radius_km: float = 150.0
    min_samples: int = 30
    time_tolerance_minutes: int = 30
    cache_ttl_seconds: int = 3600
    refresh_interval_seconds: int = _env_int("REFRESH_INTERVAL_SECONDS", 600)
    italy_regional_enabled: bool = False
    live_observations_enabled: bool = _env_bool("LIVE_OBSERVATIONS_ENABLED", True)
    live_forecasts_enabled: bool = _env_bool("LIVE_FORECASTS_ENABLED", True)
    request_timeout_seconds: float = _env_float("REQUEST_TIMEOUT_SECONDS", 8.0)
    knmi_api_key: str | None = os.getenv("KNMI_API_KEY")
    knmi_api_base_url: str = os.getenv("KNMI_API_BASE_URL", "https://api.dataplatform.knmi.nl/open-data/v1")
    knmi_dataset_name: str = os.getenv("KNMI_DATASET_NAME", "10-minute-in-situ-meteorological-observations")
    knmi_dataset_version: str = os.getenv("KNMI_DATASET_VERSION", "1.0")
    meteofrance_observation_url: str | None = os.getenv("METEOFRANCE_OBSERVATION_URL")
    meteofrance_api_key: str | None = os.getenv("METEOFRANCE_API_KEY")
    ncei_api_base_url: str = os.getenv("NCEI_API_BASE_URL", "https://www.ncei.noaa.gov/access/services/data/v1")
    ncei_dataset: str = os.getenv("NCEI_DATASET", "global-hourly")
    ncei_token: str | None = os.getenv("NCEI_TOKEN")
    openmeteo_knmi_url: str = os.getenv("OPENMETEO_KNMI_URL", "https://api.open-meteo.com/v1/forecast")
    openmeteo_knmi_model: str = os.getenv("OPENMETEO_KNMI_MODEL", "knmi_seamless")
    openmeteo_arome_hd_url: str = os.getenv("OPENMETEO_AROME_HD_URL", "https://api.open-meteo.com/v1/forecast")
    openmeteo_arome_hd_model: str = os.getenv("OPENMETEO_AROME_HD_MODEL", "meteofrance_arome_france_hd")
    openmeteo_dwd_url: str = os.getenv("OPENMETEO_DWD_URL", "https://api.open-meteo.com/v1/forecast")
    openmeteo_dwd_model: str = os.getenv("OPENMETEO_DWD_MODEL", "icon_seamless")
    openmeteo_ecmwf_url: str = os.getenv("OPENMETEO_ECMWF_URL", "https://api.open-meteo.com/v1/ecmwf")
    openmeteo_ecmwf_model: str = os.getenv("OPENMETEO_ECMWF_MODEL", "")
    openmeteo_icon_eu_url: str = os.getenv("OPENMETEO_ICON_EU_URL", "https://api.open-meteo.com/v1/forecast")
    openmeteo_icon_eu_model: str = os.getenv("OPENMETEO_ICON_EU_MODEL", "icon_eu")
    openmeteo_arpege_url: str = os.getenv("OPENMETEO_ARPEGE_URL", "https://api.open-meteo.com/v1/forecast")
    openmeteo_arpege_model: str = os.getenv("OPENMETEO_ARPEGE_MODEL", "meteofrance_arpege_europe")
    # Previous runs API — returns actual archived forecast runs (true forecast skill)
    openmeteo_previous_runs_url: str = os.getenv(
        "OPENMETEO_PREVIOUS_RUNS_URL", "https://previous-runs-api.open-meteo.com/v1/forecast"
    )
    # ECMWF needs an explicit model param on the unified previous-runs endpoint
    openmeteo_ecmwf_previous_runs_model: str = os.getenv("OPENMETEO_ECMWF_PREVIOUS_RUNS_MODEL", "ecmwf_ifs04")


SETTINGS = Settings()
