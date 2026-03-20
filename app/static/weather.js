/* weather.js - Weather tab: structured regime detection + tactical report */

let wxRangeState = {
  total: 0,
  start: 0,
  end: 0,
};

const WX_REGIMES = {
  gradient: { label: 'Gradient / persistent', color: '#2563eb' },
  thermal: { label: 'Thermal / sea-breeze style', color: '#f59e0b' },
  frontal: { label: 'Frontal / showery', color: '#dc2626' },
  local: { label: 'Locally driven / terrain style', color: '#0f766e' },
  mixed_transition: { label: 'Mixed transition', color: '#64748b' },
};

function wxParseUtc(isoStr) {
  return new Date(isoStr.endsWith('Z') ? isoStr : `${isoStr}Z`);
}

function wxLocalISO(isoStr) {
  const d = wxParseUtc(isoStr);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function wxFmtHour(isoStr) {
  const d = wxParseUtc(isoStr);
  const pad = n => String(n).padStart(2, '0');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${pad(d.getDate())} ${months[d.getMonth()]} ${pad(d.getHours())}:00`;
}

function wxCardinal(deg) {
  if (deg == null) return '-';
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return dirs[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}

function wxCircularMean(values) {
  const valid = values.filter(v => v != null);
  if (!valid.length) return null;
  const s = valid.reduce((acc, value) => acc + Math.sin((value * Math.PI) / 180), 0);
  const c = valid.reduce((acc, value) => acc + Math.cos((value * Math.PI) / 180), 0);
  return ((Math.atan2(s, c) * 180) / Math.PI + 360) % 360;
}

function wxCircularDiff(fromDeg, toDeg) {
  return ((toDeg - fromDeg + 540) % 360) - 180;
}

function wxCircularSpread(values, meanDeg) {
  const valid = values.filter(v => v != null);
  if (!valid.length || meanDeg == null) return null;
  const diffs = valid.map(v => Math.abs(wxCircularDiff(meanDeg, v)));
  return diffs.reduce((acc, value) => acc + value, 0) / diffs.length;
}

function wxCircularStd(values) {
  const valid = values.filter(v => v != null);
  if (valid.length < 2) return 0;
  const s = valid.reduce((acc, value) => acc + Math.sin((value * Math.PI) / 180), 0);
  const c = valid.reduce((acc, value) => acc + Math.cos((value * Math.PI) / 180), 0);
  const r = Math.sqrt(s * s + c * c) / valid.length;
  if (r <= 0) return 180;
  return Math.sqrt(-2 * Math.log(r)) * (180 / Math.PI);
}

function wxDirectionRange(values) {
  const valid = values.filter(v => v != null).map(v => ((v % 360) + 360) % 360);
  if (valid.length < 2) return 0;
  let maxGap = 0;
  const sorted = [...valid].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i += 1) {
    maxGap = Math.max(maxGap, sorted[i] - sorted[i - 1]);
  }
  maxGap = Math.max(maxGap, sorted[0] + 360 - sorted[sorted.length - 1]);
  return 360 - maxGap;
}

function wxStd(values) {
  const valid = values.filter(v => v != null);
  if (valid.length < 2) return 0;
  const mean = valid.reduce((acc, value) => acc + value, 0) / valid.length;
  const variance = valid.reduce((acc, value) => acc + (value - mean) ** 2, 0) / (valid.length - 1);
  return Math.sqrt(variance);
}

function wxMean(values) {
  const valid = values.filter(v => v != null);
  if (!valid.length) return null;
  return valid.reduce((acc, value) => acc + value, 0) / valid.length;
}

function wxSum(values) {
  return values.filter(v => v != null).reduce((acc, value) => acc + value, 0);
}

function wxMin(values) {
  const valid = values.filter(v => v != null);
  return valid.length ? Math.min(...valid) : null;
}

function wxMax(values) {
  const valid = values.filter(v => v != null);
  return valid.length ? Math.max(...valid) : null;
}

function wxClamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function wxRise(x, a, b) {
  if (x == null) return null;
  if (x <= a) return 0;
  if (x >= b) return 1;
  return (x - a) / (b - a);
}

function wxFall(x, a, b) {
  const rise = wxRise(x, a, b);
  return rise == null ? null : 1 - rise;
}

function wxTri(x, a, b, c) {
  if (x == null) return null;
  if (x <= a || x >= c) return 0;
  if (x === b) return 1;
  if (x < b) return (x - a) / (b - a);
  return (c - x) / (c - b);
}

function wxWeightedScore(parts) {
  const usable = parts.filter(part => part.value != null);
  if (!usable.length) return 50;
  const totalWeight = usable.reduce((acc, part) => acc + part.weight, 0);
  if (!totalWeight) return 50;
  return 100 * usable.reduce((acc, part) => acc + part.value * part.weight, 0) / totalWeight;
}

function wxUnwrapDirections(values) {
  const out = [];
  let last = null;
  values.forEach(value => {
    if (value == null) {
      out.push(null);
      return;
    }
    if (last == null) {
      out.push(value);
      last = value;
      return;
    }
    const delta = ((value - last + 540) % 360) - 180;
    const next = last + delta;
    out.push(next);
    last = next;
  });
  return out;
}

function wxFirstValid(values) {
  return values.find(value => value != null) ?? null;
}

function wxLastValid(values) {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (values[i] != null) return values[i];
  }
  return null;
}

function wxAxisDiff(aDeg, axisDeg) {
  if (aDeg == null || axisDeg == null) return null;
  return Math.abs((((aDeg - axisDeg + 90) % 180) + 180) % 180 - 90);
}

function wxFingerprint() {
  return forecastData?.location_fingerprint ?? null;
}

function wxSeriesCoverage(rows, key) {
  if (!rows.length) return 0;
  return rows.filter(row => row[key] != null).length / rows.length;
}

function wxBaseHours() {
  return forecastData?.models?.[0]?.hours ?? [];
}

function wxSetWindowSummary() {
  const startLabel = document.getElementById('wxStartLabel');
  const endLabel = document.getElementById('wxEndLabel');
  const summary = document.getElementById('wxRangeSummary');
  const hours = wxBaseHours();
  if (!hours.length) {
    if (startLabel) startLabel.textContent = '-';
    if (endLabel) endLabel.textContent = '-';
    if (summary) summary.textContent = 'Full loaded forecast window';
    return;
  }

  const startHour = hours[wxRangeState.start];
  const endHour = hours[wxRangeState.end];
  if (startLabel) startLabel.textContent = wxFmtHour(startHour.time_utc);
  if (endLabel) endLabel.textContent = wxFmtHour(endHour.time_utc);
  if (summary) {
    summary.textContent = `${wxFmtHour(startHour.time_utc)} to ${wxFmtHour(endHour.time_utc)}  -  ${wxRangeState.end - wxRangeState.start + 1}h window`;
  }
}

function wxApplyRangeInputs(changed) {
  const startInput = document.getElementById('wxStartRange');
  const endInput = document.getElementById('wxEndRange');
  if (!startInput || !endInput) return;

  let start = parseInt(startInput.value, 10) || 0;
  let end = parseInt(endInput.value, 10) || 0;
  if (changed === 'start' && start > end) end = start;
  if (changed === 'end' && end < start) start = end;

  wxRangeState.start = start;
  wxRangeState.end = end;
  startInput.value = String(start);
  endInput.value = String(end);
  wxSetWindowSummary();
}

function wxInitRangeControls(forceReset = false) {
  const startInput = document.getElementById('wxStartRange');
  const endInput = document.getElementById('wxEndRange');
  const hours = wxBaseHours();
  if (!startInput || !endInput || !hours.length) {
    wxSetWindowSummary();
    return;
  }

  const max = hours.length - 1;
  startInput.min = '0';
  endInput.min = '0';
  startInput.max = String(max);
  endInput.max = String(max);

  const shouldReset = forceReset || wxRangeState.total !== hours.length || wxRangeState.end > max;
  if (shouldReset) {
    wxRangeState = { total: hours.length, start: 0, end: max };
  }

  startInput.value = String(wxRangeState.start);
  endInput.value = String(wxRangeState.end);

  if (!startInput.dataset.bound) {
    startInput.addEventListener('input', () => {
      wxApplyRangeInputs('start');
      renderWeatherTab();
    });
    startInput.dataset.bound = '1';
  }

  if (!endInput.dataset.bound) {
    endInput.addEventListener('input', () => {
      wxApplyRangeInputs('end');
      renderWeatherTab();
    });
    endInput.dataset.bound = '1';
  }

  wxApplyRangeInputs();
}

function wxWindowBounds() {
  const hours = wxBaseHours();
  if (!hours.length) return { startTime: null, endTime: null };
  return {
    startTime: hours[wxRangeState.start]?.time_utc ?? null,
    endTime: hours[wxRangeState.end]?.time_utc ?? null,
  };
}

function wxFilterHours(hours) {
  const { startTime, endTime } = wxWindowBounds();
  if (!startTime || !endTime) return hours;
  return hours.filter(hour => hour.time_utc >= startTime && hour.time_utc <= endTime);
}

function wxSelectedModels() {
  if (!forecastData?.models?.length) return [];
  const active = _selectedModels && _selectedModels.size
    ? forecastData.models.filter(model => _selectedModels.has(model.model_id))
    : forecastData.models;
  return active
    .map(model => ({ ...model, hours: wxFilterHours(model.hours || []) }))
    .filter(model => Array.isArray(model.hours) && model.hours.length > 0);
}

function wxBuildConsensus(models) {
  const times = [...new Set(models.flatMap(model => model.hours.map(hour => hour.time_utc)))].sort();
  const modelMaps = models.map(model => ({
    model_id: model.model_id,
    hours: new Map(model.hours.map(hour => [hour.time_utc, hour])),
  }));

  return times.map(time_utc => {
    const rows = modelMaps.map(model => model.hours.get(time_utc)).filter(Boolean);
    const wsMeanMs = wxMean(rows.map(row => row.ws_ms));
    const gustMeanMs = wxMean(rows.map(row => row.gust_ms));
    const wdMean = wxCircularMean(rows.map(row => row.wd_deg));
    return {
      time_utc,
      ws_kt: wsMeanMs != null ? wsMeanMs * MS_TO_KT : null,
      gust_kt: gustMeanMs != null ? gustMeanMs * MS_TO_KT : null,
      wd_deg: wdMean,
      temp_c: wxMean(rows.map(row => row.temp_c)),
      precip_mm: wxMean(rows.map(row => row.precip_mm)),
      cloud_cover_pct: wxMean(rows.map(row => row.cloud_cover_pct)),
      pressure_msl_hpa: wxMean(rows.map(row => row.pressure_msl_hpa)),
      shortwave_wm2: wxMean(rows.map(row => row.shortwave_wm2)),
      cape_jkg: wxMean(rows.map(row => row.cape_jkg)),
      boundary_layer_height_m: wxMean(rows.map(row => row.boundary_layer_height_m)),
      ws_spread_kt: wxStd(rows.map(row => row.ws_ms)) * MS_TO_KT,
      dir_spread_deg: wxCircularSpread(rows.map(row => row.wd_deg), wdMean),
      model_count: rows.length,
    };
  });
}

function wxWindowMetrics(rows) {
  if (!rows.length) return null;
  const wsVals = rows.map(row => row.ws_kt).filter(v => v != null);
  const gustVals = rows.map(row => row.gust_kt).filter(v => v != null);
  const dirVals = rows.map(row => row.wd_deg).filter(v => v != null);
  const tempVals = rows.map(row => row.temp_c).filter(v => v != null);
  const precipVals = rows.map(row => row.precip_mm).filter(v => v != null);
  const cloudVals = rows.map(row => row.cloud_cover_pct).filter(v => v != null);
  const pressureVals = rows.map(row => row.pressure_msl_hpa);
  const shortwaveVals = rows.map(row => row.shortwave_wm2).filter(v => v != null);
  const capeVals = rows.map(row => row.cape_jkg).filter(v => v != null);
  const blhVals = rows.map(row => row.boundary_layer_height_m).filter(v => v != null);
  const wsSpreadVals = rows.map(row => row.ws_spread_kt).filter(v => v != null);
  const dirSpreadVals = rows.map(row => row.dir_spread_deg).filter(v => v != null);

  const unwrapped = wxUnwrapDirections(rows.map(row => row.wd_deg));
  const dirTurns = [];
  for (let i = 1; i < unwrapped.length; i += 1) {
    if (unwrapped[i] == null || unwrapped[i - 1] == null) continue;
    dirTurns.push(Math.abs(unwrapped[i] - unwrapped[i - 1]));
  }

  const firstDir = wxFirstValid(rows.map(row => row.wd_deg));
  const lastDir = wxLastValid(rows.map(row => row.wd_deg));
  const firstPressure = wxFirstValid(pressureVals);
  const lastPressure = wxLastValid(pressureVals);

  let maxPressureSwing = null;
  if (pressureVals.filter(v => v != null).length >= 2) {
    maxPressureSwing = 0;
    for (let i = 1; i < pressureVals.length; i += 1) {
      if (pressureVals[i] == null || pressureVals[i - 1] == null) continue;
      maxPressureSwing = Math.max(maxPressureSwing, Math.abs(pressureVals[i] - pressureVals[i - 1]));
    }
  }

  return {
    windMeanKt: wxMean(wsVals),
    windAmpKt: wsVals.length ? Math.max(...wsVals) - Math.min(...wsVals) : null,
    gustMaxKt: wxMax(gustVals),
    gustExcessMeanKt: wxMean(rows.map(row => (
      row.gust_kt != null && row.ws_kt != null ? Math.max(row.gust_kt - row.ws_kt, 0) : null
    ))),
    dirMeanDeg: wxCircularMean(dirVals),
    dirStdDeg: wxCircularStd(dirVals),
    dirRangeDeg: wxDirectionRange(dirVals),
    dirTurnMeanDeg: wxMean(dirTurns),
    dirShiftAbsDeg: firstDir != null && lastDir != null ? Math.abs(wxCircularDiff(firstDir, lastDir)) : null,
    dirShiftSignedDeg: firstDir != null && lastDir != null ? wxCircularDiff(firstDir, lastDir) : null,
    tempLowC: wxMin(tempVals),
    tempHighC: wxMax(tempVals),
    precipTotalMm: wxSum(precipVals),
    precipPeakMm: wxMax(precipVals),
    cloudMeanPct: wxMean(cloudVals),
    cloudMaxPct: wxMax(cloudVals),
    pressureTrendHpa: firstPressure != null && lastPressure != null ? lastPressure - firstPressure : null,
    pressureSwingHpa: maxPressureSwing,
    shortwaveMeanWm2: wxMean(shortwaveVals),
    shortwaveMaxWm2: wxMax(shortwaveVals),
    heatingHours: shortwaveVals.filter(v => v >= 120).length,
    capeMeanJkg: wxMean(capeVals),
    capeMaxJkg: wxMax(capeVals),
    blhMeanM: wxMean(blhVals),
    blhMaxM: wxMax(blhVals),
    wsSpreadMeanKt: wxMean(wsSpreadVals),
    dirSpreadMeanDeg: wxMean(dirSpreadVals),
    minModelCount: rows.length ? Math.min(...rows.map(row => row.model_count || 0)) : 0,
    maxModelCount: rows.length ? Math.max(...rows.map(row => row.model_count || 0)) : 0,
  };
}

function wxCoverageSummary(rows) {
  const fields = [
    { key: 'cloud_cover_pct', label: 'cloud' },
    { key: 'pressure_msl_hpa', label: 'pressure' },
    { key: 'shortwave_wm2', label: 'radiation' },
    { key: 'cape_jkg', label: 'cape' },
    { key: 'boundary_layer_height_m', label: 'blh' },
  ];
  const byField = {};
  fields.forEach(field => {
    byField[field.label] = wxSeriesCoverage(rows, field.key);
  });
  const overall = wxMean(Object.values(byField).map(value => value * 100)) ?? 0;
  let sentence = `Diagnostic field coverage ${overall.toFixed(0)}%.`;
  if (overall >= 80) {
    sentence = `Diagnostic field coverage is strong at ${overall.toFixed(0)}%, so the regime call is using the fuller signal set.`;
  } else if (overall >= 50) {
    sentence = `Diagnostic field coverage is usable at ${overall.toFixed(0)}%, but some models are thinner on the advanced fields.`;
  } else {
    sentence = `Diagnostic field coverage is limited at ${overall.toFixed(0)}%, so the call leans more on wind, rain, and spread than on boundary-layer diagnostics.`;
  }
  return { byField, overall, sentence };
}

function wxScoreRegimes(metrics, fingerprint = null) {
  const coast = fingerprint?.coast ?? {};
  const terrain = fingerprint?.terrain ?? {};
  const coastalInfluence = wxClamp(Math.max(
    coast.exposure_score ?? 0,
    wxFall(coast.nearest_open_water_km, 6, 40) ?? 0,
  ), 0, 1);
  const shorelineNormalDiff = wxAxisDiff(metrics.dirMeanDeg, coast.dominant_sea_bearing_deg);
  const onshoreAlignment = wxFall(shorelineNormalDiff, 18, 70) ?? null;
  const terrainAlignment = wxFall(wxAxisDiff(metrics.dirMeanDeg, terrain.terrain_axis_deg), 16, 65) ?? null;
  const topoPotential = terrain.topo_potential ?? 0;

  const gradient = wxWeightedScore([
    { value: wxRise(metrics.windMeanKt, 7, 18), weight: 0.18 },
    { value: wxFall(metrics.dirStdDeg, 14, 35), weight: 0.18 },
    { value: wxFall(metrics.dirTurnMeanDeg, 8, 26), weight: 0.14 },
    { value: wxFall(metrics.wsSpreadMeanKt, 1.2, 4.5), weight: 0.16 },
    { value: wxFall(metrics.dirSpreadMeanDeg, 12, 40), weight: 0.12 },
    { value: wxFall(metrics.precipTotalMm, 0.2, 4.0), weight: 0.10 },
    { value: wxFall(Math.abs(metrics.pressureTrendHpa ?? 0), 0.8, 5.0), weight: 0.12 },
    { value: wxFall(coastalInfluence, 0.2, 0.8), weight: 0.08 },
    { value: wxFall(topoPotential, 0.1, 0.55), weight: 0.06 },
  ]);

  const thermal = wxWeightedScore([
    { value: wxRise(metrics.shortwaveMaxWm2, 160, 550), weight: 0.18 },
    { value: wxFall(metrics.cloudMeanPct, 45, 90), weight: 0.16 },
    { value: wxTri(metrics.blhMeanM, 350, 1100, 2200), weight: 0.14 },
    { value: wxTri(metrics.dirShiftAbsDeg, 12, 38, 95), weight: 0.14 },
    { value: wxRise(metrics.windAmpKt, 2.0, 8.0), weight: 0.12 },
    { value: wxFall(metrics.precipTotalMm, 0.2, 3.0), weight: 0.10 },
    { value: wxFall(Math.abs(metrics.pressureTrendHpa ?? 0), 0.5, 3.5), weight: 0.08 },
    { value: wxFall(metrics.wsSpreadMeanKt, 1.4, 5.0), weight: 0.08 },
    { value: coast.coastal ? coastalInfluence : 0, weight: 0.14 },
    { value: coast.coastal ? onshoreAlignment : null, weight: 0.10 },
  ]);

  const frontal = wxWeightedScore([
    { value: wxRise(metrics.precipTotalMm, 0.4, 6.0), weight: 0.18 },
    { value: wxRise(metrics.cloudMeanPct, 55, 95), weight: 0.12 },
    { value: wxRise(Math.abs(metrics.pressureTrendHpa ?? 0), 1.0, 6.0), weight: 0.16 },
    { value: wxRise(metrics.dirShiftAbsDeg, 18, 100), weight: 0.16 },
    { value: wxRise(metrics.gustExcessMeanKt, 2.5, 10.0), weight: 0.10 },
    { value: wxRise(metrics.wsSpreadMeanKt, 1.4, 5.0), weight: 0.10 },
    { value: wxRise(metrics.dirSpreadMeanDeg, 15, 55), weight: 0.08 },
    { value: wxRise(metrics.capeMaxJkg, 80, 1000), weight: 0.10 },
    { value: wxFall(coastalInfluence, 0.4, 1.0), weight: 0.06 },
  ]);

  const local = wxWeightedScore([
    { value: wxFall(Math.abs(metrics.pressureTrendHpa ?? 0), 0.5, 3.0), weight: 0.16 },
    { value: wxFall(metrics.precipTotalMm, 0.1, 3.0), weight: 0.10 },
    { value: wxFall(metrics.wsSpreadMeanKt, 1.0, 4.0), weight: 0.12 },
    { value: wxTri(metrics.windMeanKt, 5.0, 11.0, 20.0), weight: 0.12 },
    { value: wxFall(metrics.dirStdDeg, 10, 26), weight: 0.18 },
    { value: wxTri(metrics.blhMeanM, 250, 900, 1700), weight: 0.12 },
    { value: wxRise(metrics.shortwaveMeanWm2, 80, 320), weight: 0.10 },
    { value: wxTri(metrics.windAmpKt, 1.0, 5.0, 10.0), weight: 0.10 },
    { value: topoPotential, weight: 0.18 },
    { value: topoPotential > 0.08 ? terrainAlignment : null, weight: 0.12 },
  ]);

  const scores = {
    gradient: wxClamp(gradient + (metrics.windAmpKt != null && metrics.windAmpKt < 3 ? 4 : 0), 0, 100),
    thermal: wxClamp(thermal + (
      metrics.heatingHours >= 3
      && (metrics.dirShiftAbsDeg ?? 0) >= 18
      && (metrics.precipTotalMm ?? 0) < 1.5
      && coast.coastal
      ? 8 : 0
    ), 0, 100),
    frontal: wxClamp(frontal + (
      (metrics.precipTotalMm ?? 0) >= 1.0 && ((metrics.capeMaxJkg ?? 0) >= 250 || (metrics.gustExcessMeanKt ?? 0) >= 5) ? 6 : 0
    ), 0, 100),
    local: wxClamp(local + (
      (metrics.windAmpKt ?? 0) >= 3 && (metrics.windAmpKt ?? 0) <= 8 && (metrics.dirShiftAbsDeg ?? 0) < 25 ? 4 : 0
    ) + (
      topoPotential >= 0.12 ? 8 : 0
    ), 0, 100),
  };

  const ordered = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  const [topKey, topScore] = ordered[0];
  const secondScore = ordered[1]?.[1] ?? 0;
  const separation = topScore - secondScore;
  const selectedKey = topScore < 46 || separation < 7 ? 'mixed_transition' : topKey;
  return { scores, ordered, topKey, topScore, secondScore, separation, selectedKey };
}

function wxBuildDrivers(regimeKey, metrics, coverage, scoreInfo, fingerprint = null) {
  const drivers = [];
  const coast = fingerprint?.coast ?? {};
  const terrain = fingerprint?.terrain ?? {};
  if (regimeKey === 'gradient') {
    drivers.push(`Directional variability stays limited at ${metrics.dirStdDeg.toFixed(0)} deg with mean model spread near ${(metrics.wsSpreadMeanKt ?? 0).toFixed(1)} kt.`);
    drivers.push(`Pressure trend is ${metrics.pressureTrendHpa != null ? metrics.pressureTrendHpa.toFixed(1) : '0.0'} hPa across the window and precipitation stays light, which supports a cleaner synoptic signal.`);
  } else if (regimeKey === 'thermal') {
    drivers.push(`Heating peaks near ${metrics.shortwaveMaxWm2 != null ? metrics.shortwaveMaxWm2.toFixed(0) : '-'} W/m2 with BLH around ${metrics.blhMeanM != null ? metrics.blhMeanM.toFixed(0) : '-'} m, which is the right shape for thermal forcing.`);
    drivers.push(`The wind turns about ${metrics.dirShiftAbsDeg != null ? metrics.dirShiftAbsDeg.toFixed(0) : '-'} deg through the window while cloud and rain stay limited enough for a thermal response to show through.`);
    if (coast.coastal && coast.dominant_sea_bearing_deg != null) {
      drivers.push(`Open water sits about ${coast.nearest_open_water_km?.toFixed(0) ?? '-'} km away toward ${wxCardinal(coast.dominant_sea_bearing_deg)}, which materially strengthens the coastal thermal signal.`);
    }
  } else if (regimeKey === 'frontal') {
    drivers.push(`Pressure trend of ${metrics.pressureTrendHpa != null ? metrics.pressureTrendHpa.toFixed(1) : '-'} hPa and a direction change near ${metrics.dirShiftAbsDeg != null ? metrics.dirShiftAbsDeg.toFixed(0) : '-'} deg line up with a boundary or disturbed pattern.`);
    drivers.push(`Rain totals near ${(metrics.precipTotalMm ?? 0).toFixed(1)} mm, gust excess near ${(metrics.gustExcessMeanKt ?? 0).toFixed(1)} kt, and CAPE up to ${metrics.capeMaxJkg != null ? metrics.capeMaxJkg.toFixed(0) : '-'} J/kg all push the signal away from a clean steady flow.`);
  } else if (regimeKey === 'local') {
    drivers.push(`Pressure is relatively flat while direction stays constrained at ${metrics.dirStdDeg.toFixed(0)} deg, which points toward local forcing rather than a strong synoptic transition.`);
    drivers.push(`Heating and BLH growth are present, but the turn signal is weaker and less clean than a textbook thermal or frontal regime.`);
    if ((terrain.topo_potential ?? 0) > 0.08 && terrain.terrain_axis_deg != null) {
      drivers.push(`Terrain relief is about ${terrain.relief_m?.toFixed(0) ?? '-'} m with a preferred flow axis near ${Math.round(terrain.terrain_axis_deg)} deg, which supports channeling or side-biased local effects.`);
    }
  } else {
    const second = scoreInfo.ordered[1];
    drivers.push(`The top regime scores sit too close together to call one cleanly. ${WX_REGIMES[scoreInfo.topKey].label} leads, but only by ${scoreInfo.separation.toFixed(0)} points.`);
    if (second) {
      drivers.push(`The competing signal is ${WX_REGIMES[second[0]].label}, so timing and hazards matter more than the headline label.`);
    }
  }

  if (coverage.overall < 55) {
    drivers.push(`Advanced diagnostic coverage is only ${coverage.overall.toFixed(0)}%, so this call leans more heavily on wind, rain, and pressure than on CAPE or boundary-layer structure.`);
  }

  return drivers.slice(0, 4);
}

function wxEstimateConfidence(metrics, coverage, scoreInfo, dominantShare) {
  const meanWsSpread = metrics.wsSpreadMeanKt ?? 0;
  const meanDirSpread = metrics.dirSpreadMeanDeg ?? 0;
  const coveragePct = coverage.overall;
  const separation = scoreInfo.separation ?? 0;

  let score = 0.45 * (scoreInfo.topScore ?? 50)
    + 0.25 * separation
    + 0.20 * (dominantShare * 100)
    + 0.10 * coveragePct
    - meanWsSpread * 6
    - meanDirSpread * 0.28;

  if ((metrics.minModelCount ?? 0) < 2) score -= 18;
  score = wxClamp(score, 18, 96);

  let label = 'High';
  let sentence = 'The models are aligned tightly enough that the regime call should carry well through the window.';
  if (score < 78) {
    label = 'Medium';
    sentence = 'The regime call is usable, but the timing details can still wobble because the spread is not fully locked down.';
  }
  if (score < 56) {
    label = 'Low';
    sentence = 'The regime label is only a loose guide here; treat the report as a set of risks and tendencies rather than a precise scenario.';
  }

  return { score, label, sentence, meanWsSpread, meanDirSpread };
}

function wxClassifyTimeline(consensus, fingerprint = null) {
  const raw = [];
  for (let i = 0; i < consensus.length; i += 1) {
    const start = Math.max(0, i - 2);
    const end = Math.min(consensus.length, i + 3);
    const slice = consensus.slice(start, end);
    const metrics = wxWindowMetrics(slice);
    const scoreInfo = wxScoreRegimes(metrics, fingerprint);
    raw.push({
      time_utc: consensus[i].time_utc,
      key: scoreInfo.selectedKey,
      topKey: scoreInfo.topKey,
      topScore: scoreInfo.topScore,
      separation: scoreInfo.separation,
      scores: scoreInfo.scores,
    });
  }

  for (let i = 1; i < raw.length - 1; i += 1) {
    const prev = raw[i - 1];
    const cur = raw[i];
    const next = raw[i + 1];
    if (prev.key === next.key && cur.key !== prev.key && cur.topScore < 72) {
      cur.key = prev.key;
    }
  }

  for (let i = 1; i < raw.length; i += 1) {
    const prev = raw[i - 1];
    const cur = raw[i];
    if (cur.key !== prev.key && cur.topScore < 60 && cur.separation < 10) {
      cur.key = prev.key;
    }
  }

  return raw;
}

function wxTimelineSummary(timeline) {
  const counts = {};
  timeline.forEach(point => {
    counts[point.key] = (counts[point.key] || 0) + 1;
  });

  const ordered = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const dominantKey = ordered[0]?.[0] ?? 'mixed_transition';
  const dominantShare = timeline.length ? (ordered[0]?.[1] ?? 0) / timeline.length : 0;
  const transitions = [];
  for (let i = 1; i < timeline.length; i += 1) {
    if (timeline[i].key !== timeline[i - 1].key) {
      transitions.push({
        time_utc: timeline[i].time_utc,
        from: timeline[i - 1].key,
        to: timeline[i].key,
      });
    }
  }

  return { dominantKey, dominantShare, counts, transitions };
}

function wxAggregateScores(timeline) {
  const totals = { gradient: 0, thermal: 0, frontal: 0, local: 0 };
  const count = timeline.length || 1;
  timeline.forEach(point => {
    Object.entries(point.scores).forEach(([key, value]) => {
      totals[key] += value;
    });
  });

  Object.keys(totals).forEach(key => {
    totals[key] /= count;
  });
  return totals;
}

function wxFindPeakRow(rows, key, threshold = null) {
  let best = null;
  rows.forEach(row => {
    const value = row[key];
    if (value == null) return;
    if (threshold != null && value < threshold) return;
    if (!best || value > best[key]) best = row;
  });
  return best;
}

function wxFindBiggestTurn(rows) {
  let best = null;
  for (let i = 1; i < rows.length; i += 1) {
    if (rows[i - 1].wd_deg == null || rows[i].wd_deg == null) continue;
    const delta = Math.abs(wxCircularDiff(rows[i - 1].wd_deg, rows[i].wd_deg));
    if (!best || delta > best.delta_deg) {
      best = {
        start: rows[i - 1].time_utc,
        end: rows[i].time_utc,
        delta_deg: delta,
      };
    }
  }
  return best;
}

function wxFindBiggestPressureMove(rows) {
  let best = null;
  for (let i = 1; i < rows.length; i += 1) {
    if (rows[i - 1].pressure_msl_hpa == null || rows[i].pressure_msl_hpa == null) continue;
    const delta = rows[i].pressure_msl_hpa - rows[i - 1].pressure_msl_hpa;
    if (!best || Math.abs(delta) > Math.abs(best.delta_hpa)) {
      best = {
        start: rows[i - 1].time_utc,
        end: rows[i].time_utc,
        delta_hpa: delta,
      };
    }
  }
  return best;
}

function wxSegmentStats(rows) {
  if (!rows.length) return null;
  const metrics = wxWindowMetrics(rows);
  return {
    start: rows[0].time_utc,
    end: rows[rows.length - 1].time_utc,
    meanDir: metrics.dirMeanDeg,
    minWind: wxMin(rows.map(row => row.ws_kt)),
    maxWind: wxMax(rows.map(row => row.ws_kt)),
    maxGust: wxMax(rows.map(row => row.gust_kt)),
    tempLow: metrics.tempLowC,
    tempHigh: metrics.tempHighC,
    precipTotal: metrics.precipTotalMm,
  };
}

function wxWindowSegments(consensus) {
  if (!consensus.length) return [];
  const size = Math.max(1, Math.ceil(consensus.length / 3));
  const labels = ['Early', 'Mid', 'Late'];
  return labels.map((label, idx) => {
    const rows = consensus.slice(idx * size, (idx + 1) * size);
    if (!rows.length) return null;
    return { label, stats: wxSegmentStats(rows) };
  }).filter(Boolean);
}

function wxTimingLine(segment) {
  if (!segment?.stats) return '';
  const stats = segment.stats;
  const rainText = (stats.precipTotal ?? 0) >= 0.2
    ? `, rain signal ${stats.precipTotal.toFixed(1)} mm`
    : ', mainly dry';
  return `- ${segment.label} (${wxFmtHour(stats.start)} to ${wxFmtHour(stats.end)}): ${stats.minWind != null ? stats.minWind.toFixed(0) : '-'}-${stats.maxWind != null ? stats.maxWind.toFixed(0) : '-'} kt from ${wxCardinal(stats.meanDir)}${stats.maxGust != null ? `, gusts ${stats.maxGust.toFixed(0)} kt` : ''}${rainText}.`;
}

function wxBuildHazards(metrics, confidence, events, timelineSummary) {
  const hazards = [];
  if ((metrics.gustMaxKt ?? 0) >= 22) {
    hazards.push(`Gusts reach ${metrics.gustMaxKt.toFixed(0)} kt${events.windPeak ? ` near ${wxFmtHour(events.windPeak.time_utc)}` : ''}.`);
  }
  if ((metrics.dirShiftAbsDeg ?? 0) >= 25 && events.turn) {
    hazards.push(`The cleanest direction shift is about ${events.turn.delta_deg.toFixed(0)} deg between ${wxFmtHour(events.turn.start)} and ${wxFmtHour(events.turn.end)}.`);
  }
  if ((metrics.precipTotalMm ?? 0) >= 0.5) {
    hazards.push(`Rain signal totals about ${(metrics.precipTotalMm ?? 0).toFixed(1)} mm${events.wetPeak ? ` with the wettest hour near ${wxFmtHour(events.wetPeak.time_utc)}` : ''}.`);
  }
  if ((metrics.capeMaxJkg ?? 0) >= 300) {
    hazards.push(`CAPE rises to about ${metrics.capeMaxJkg.toFixed(0)} J/kg, so short convective bursts could distort the cleaner wind story.`);
  }
  if (confidence.label === 'Low') {
    hazards.push(`Model spread remains meaningful at ${confidence.meanWsSpread.toFixed(1)} kt and ${confidence.meanDirSpread.toFixed(0)} deg, so timing should stay flexible.`);
  }
  if (timelineSummary.transitions.length >= 2) {
    hazards.push(`The timeline flips regime ${timelineSummary.transitions.length} times, so the window is probably transitional rather than cleanly single-pattern.`);
  }
  if (!hazards.length) {
    hazards.push('No single hazard dominates the window beyond normal hour-to-hour variability.');
  }
  return hazards;
}

function wxEstimateOscillation(metrics, dominantKey, confidence, timelineSummary, fingerprint = null) {
  const coast = fingerprint?.coast ?? {};
  const terrain = fingerprint?.terrain ?? {};
  let score = wxWeightedScore([
    { value: wxFall(metrics.dirStdDeg, 10, 24), weight: 0.18 },
    { value: wxFall(metrics.wsSpreadMeanKt, 1.0, 4.0), weight: 0.14 },
    { value: wxFall(metrics.dirSpreadMeanDeg, 10, 35), weight: 0.14 },
    { value: wxTri(metrics.shortwaveMaxWm2, 120, 360, 650), weight: 0.12 },
    { value: wxTri(metrics.blhMeanM, 300, 950, 1800), weight: 0.10 },
    { value: wxTri(metrics.windMeanKt, 4, 10, 20), weight: 0.10 },
    { value: wxFall(metrics.precipTotalMm, 0.1, 2.5), weight: 0.08 },
    { value: wxFall(metrics.capeMaxJkg, 80, 500), weight: 0.06 },
    { value: wxRise(confidence.score, 45, 85), weight: 0.08 },
  ]) / 100;

  if (dominantKey === 'thermal') score += 0.10;
  if (dominantKey === 'local') score += 0.08;
  if (dominantKey === 'gradient') score += 0.05;
  if (dominantKey === 'mixed_transition') score -= 0.08;
  if (dominantKey === 'frontal') score -= 0.18;
  if (coast.coastal && dominantKey === 'thermal') score += 0.08;
  if ((terrain.topo_potential ?? 0) >= 0.12 && dominantKey === 'local') score += 0.08;
  if (timelineSummary.transitions.length >= 2) score -= 0.06;
  score = wxClamp(score, 0.05, 0.95);

  let amplitudeDeg = 2.5 + 3.5 * score;
  let periodMin = 12;
  let periodMax = 24;
  let label = 'No reliable repeatable oscillation signal';
  let tradable = false;

  if (dominantKey === 'thermal') {
    amplitudeDeg = 3 + 4.5 * score;
    periodMin = score >= 0.70 ? 8 : 10;
    periodMax = score >= 0.70 ? 14 : 18;
    label = score >= 0.58 ? 'Thermal oscillations possible' : 'Small thermal pulses possible';
  } else if (dominantKey === 'local') {
    amplitudeDeg = 3 + 4.0 * score;
    periodMin = score >= 0.68 ? 10 : 12;
    periodMax = score >= 0.68 ? 16 : 20;
    label = score >= 0.58 ? 'Local oscillations possible' : 'Small local pulses possible';
  } else if (dominantKey === 'gradient') {
    amplitudeDeg = 2 + 3.0 * score;
    periodMin = 14;
    periodMax = 28;
    label = score >= 0.62 ? 'Mostly steady with small wiggles' : 'Steady mean, little repeatable oscillation';
  } else if (dominantKey === 'frontal') {
    amplitudeDeg = 4 + 5.0 * score;
    periodMin = 6;
    periodMax = 16;
    label = 'Erratic bursts, not clean oscillations';
  } else {
    amplitudeDeg = 2.5 + 3.5 * score;
    periodMin = 12;
    periodMax = 24;
    label = score >= 0.60 ? 'Mixed small oscillation signal' : 'No reliable repeatable oscillation signal';
  }

  tradable = (
    score >= 0.60
    && confidence.score >= 60
    && (metrics.wsSpreadMeanKt ?? 99) <= 2.5
    && (metrics.dirSpreadMeanDeg ?? 99) <= 22
    && timelineSummary.transitions.length <= 1
    && ['thermal', 'local'].includes(dominantKey)
  );
  const bandLow = Math.max(2, Math.round(amplitudeDeg - 1));
  const bandHigh = Math.max(bandLow + 1, Math.round(amplitudeDeg + 1));
  const swingLow = bandLow * 2;
  const swingHigh = bandHigh * 2;

  let note = `Heuristic only: expect roughly +/-${bandLow}-${bandHigh} deg around the mean with cycles around ${periodMin}-${periodMax} min if the local regime sets up cleanly.`;
  if (!tradable && dominantKey === 'frontal') {
    note = `Direction can flick ${swingLow}-${swingHigh} deg in short bursts, but it is more likely noise or boundary-driven than a repeatable race oscillation.`;
  } else if (!tradable) {
    note = `Any oscillation signal looks weak or phasey. Use ${swingLow}-${swingHigh} deg as a loose envelope, not a trading rhythm.`;
  }

  return {
    score: Math.round(score * 100),
    tradable,
    label,
    bandLowDeg: bandLow,
    bandHighDeg: bandHigh,
    swingLowDeg: swingLow,
    swingHighDeg: swingHigh,
    periodMin,
    periodMax,
    note,
  };
}

function wxEstimateShiftWindow(consensus, shiftAbsDeg) {
  if (!consensus.length || !shiftAbsDeg || shiftAbsDeg < 10) return null;
  const firstDir = wxFirstValid(consensus.map(row => row.wd_deg));
  if (firstDir == null) return null;
  const onsetThreshold = Math.max(8, shiftAbsDeg * 0.4);
  const fullThreshold = Math.max(12, shiftAbsDeg * 0.7);
  let onset = null;
  let mature = null;
  consensus.forEach(row => {
    if (row.wd_deg == null) return;
    const delta = Math.abs(wxCircularDiff(firstDir, row.wd_deg));
    if (!onset && delta >= onsetThreshold) onset = row.time_utc;
    if (!mature && delta >= fullThreshold) mature = row.time_utc;
  });
  if (!onset) return null;
  return { onset, mature: mature ?? consensus[consensus.length - 1].time_utc };
}

function wxEstimateBend(metrics, dominantKey, consensus, fingerprint = null) {
  const coast = fingerprint?.coast ?? {};
  const terrain = fingerprint?.terrain ?? {};
  const firstDir = wxFirstValid(consensus.map(row => row.wd_deg));
  const lastDir = wxLastValid(consensus.map(row => row.wd_deg));
  const meanDir = metrics.dirMeanDeg;
  const coastalInfluence = wxClamp(Math.max(
    coast.exposure_score ?? 0,
    wxFall(coast.nearest_open_water_km, 6, 40) ?? 0,
  ), 0, 1);
  const terrainInfluence = wxClamp(Math.max(
    terrain.topo_potential ?? 0,
    terrain.channel_strength ?? 0,
  ), 0, 1);
  const heatingSupport = wxWeightedScore([
    { value: wxRise(metrics.shortwaveMaxWm2, 160, 550), weight: 0.40 },
    { value: wxTri(metrics.blhMeanM, 350, 1100, 2200), weight: 0.35 },
    { value: wxFall(metrics.cloudMeanPct, 45, 90), weight: 0.25 },
  ]) / 100;
  const stableSupport = wxWeightedScore([
    { value: wxFall(metrics.precipTotalMm, 0.1, 2.5), weight: 0.28 },
    { value: wxFall(Math.abs(metrics.pressureTrendHpa ?? 0), 0.8, 4.0), weight: 0.24 },
    { value: wxFall(metrics.wsSpreadMeanKt, 1.2, 4.2), weight: 0.24 },
    { value: wxFall(metrics.dirSpreadMeanDeg, 12, 34), weight: 0.24 },
  ]) / 100;

  const coastScore = wxWeightedScore([
    { value: coast.coastal ? coastalInfluence : 0, weight: 0.32 },
    { value: heatingSupport, weight: 0.24 },
    { value: stableSupport, weight: 0.16 },
    { value: dominantKey === 'thermal' ? 1 : (dominantKey === 'local' ? 0.55 : 0.20), weight: 0.18 },
    { value: coast.dominant_sea_bearing_deg != null && firstDir != null ? wxRise(Math.abs(wxCircularDiff(firstDir, coast.dominant_sea_bearing_deg)), 6, 38) : null, weight: 0.10 },
  ]) / 100;
  const terrainScore = wxWeightedScore([
    { value: terrainInfluence, weight: 0.30 },
    { value: terrain.channel_strength ?? 0, weight: 0.20 },
    { value: stableSupport, weight: 0.18 },
    { value: dominantKey === 'local' ? 1 : (dominantKey === 'gradient' ? 0.55 : 0.20), weight: 0.18 },
    { value: terrain.terrain_axis_deg != null && firstDir != null ? wxRise(Math.abs(wxCircularDiff(firstDir, terrain.terrain_axis_deg)), 6, 32) : null, weight: 0.14 },
  ]) / 100;

  let source = null;
  let sourceScore = 0;
  let targetDir = null;
  if (coastScore >= terrainScore && coastScore >= 0.38 && coast.dominant_sea_bearing_deg != null) {
    source = 'coast';
    sourceScore = coastScore;
    targetDir = coast.dominant_sea_bearing_deg;
  } else if (terrainScore >= 0.35 && terrain.terrain_axis_deg != null) {
    source = 'terrain';
    sourceScore = terrainScore;
    targetDir = terrain.terrain_axis_deg;
  }

  if (!source || targetDir == null) {
    return {
      label: 'Little local bend',
      note: 'No strong coastline or terrain bend stands out above the broader flow.',
      source: null,
      sourceScore: 0,
      targetDir: null,
      active: false,
      holding: false,
      shiftWord: null,
      absBendDeg: 0,
    };
  }

  const startDiff = firstDir != null ? Math.abs(wxCircularDiff(firstDir, targetDir)) : null;
  const endDiff = lastDir != null ? Math.abs(wxCircularDiff(lastDir, targetDir)) : null;
  const meanDiff = meanDir != null ? Math.abs(wxCircularDiff(meanDir, targetDir)) : null;
  const alignmentGain = startDiff != null && endDiff != null ? startDiff - endDiff : null;
  const signedShift = metrics.dirShiftSignedDeg ?? 0;
  const absShift = Math.abs(signedShift);
  const bendPotentialDeg = Math.round(4 + 16 * sourceScore);
  const towardTarget = firstDir != null ? wxCircularDiff(firstDir, targetDir) : 0;
  const towardWord = towardTarget >= 0 ? 'Right' : 'Left';
  const targetLabel = source === 'coast'
    ? `${wxCardinal(targetDir)} off the water`
    : `${Math.round(targetDir)} deg terrain axis`;
  const sourceLabel = source === 'coast' ? 'Coastline' : 'Terrain';

  if (alignmentGain != null && alignmentGain >= 5 && absShift >= 6) {
    const bendDeg = Math.round(wxClamp(Math.min(absShift, bendPotentialDeg, alignmentGain + 3), 4, 22));
    const bendWord = signedShift >= 0 ? 'Right' : 'Left';
    return {
      label: `${bendWord} bend`,
      note: `${sourceLabel} likely bends the flow ${bendWord.toLowerCase()} about ${bendDeg} deg toward ${targetLabel}.`,
      source,
      sourceScore: Math.round(sourceScore * 100),
      targetDir,
      active: true,
      holding: false,
      shiftWord: bendWord,
      absBendDeg: bendDeg,
    };
  }

  if (meanDiff != null && meanDiff <= (source === 'coast' ? 18 : 16)) {
    return {
      label: source === 'coast' ? 'Coastal bend holding' : 'Terrain bend holding',
      note: `${sourceLabel} likely keeps the mean wind biased toward ${targetLabel} even without a fresh one-sided move.`,
      source,
      sourceScore: Math.round(sourceScore * 100),
      targetDir,
      active: false,
      holding: true,
      shiftWord: null,
      absBendDeg: 0,
    };
  }

  if (absShift >= 8 && sourceScore >= 0.48) {
    const bendDeg = Math.round(wxClamp(Math.min(absShift * 0.7, bendPotentialDeg), 4, 18));
    return {
      label: `${towardWord} bend possible`,
      note: `Part of the broader ${signedShift >= 0 ? 'right' : 'left'} trend is likely geographic, with local forcing nudging the flow toward ${targetLabel}.`,
      source,
      sourceScore: Math.round(sourceScore * 100),
      targetDir,
      active: false,
      holding: false,
      shiftWord: towardWord,
      absBendDeg: bendDeg,
    };
  }

  return {
    label: source === 'coast' ? 'Coastal bias' : 'Terrain bias',
    note: `${sourceLabel} likely adds a mild bias toward ${targetLabel}, but not enough to call a clean active bend.`,
    source,
    sourceScore: Math.round(sourceScore * 100),
    targetDir,
    active: false,
    holding: false,
    shiftWord: null,
    absBendDeg: 0,
  };
}

function wxEstimateTrendProfile(metrics, dominantKey, timelineSummary, oscillation, consensus) {
  const signedShift = metrics.dirShiftSignedDeg ?? 0;
  const absShift = Math.abs(signedShift);
  const rangeDeg = Math.round(metrics.dirRangeDeg ?? 0);
  const shiftWord = signedShift >= 0 ? 'Right' : 'Left';
  const shiftWindow = wxEstimateShiftWindow(consensus, absShift);

  let label = 'Mostly steady';
  let note = 'No strong one-sided directional trend stands out.';
  let tradable = oscillation.tradable;

  if (dominantKey === 'frontal') {
    label = absShift >= 18 ? `${shiftWord} trend in unstable air` : 'Erratic / unstable';
    note = absShift >= 18
      ? `${shiftWord} trend roughly ${Math.round(absShift)} deg through the window, but expect the actual moves to come in irregular bursts.`
      : `Direction can flick through a ${Math.max(rangeDeg, oscillation.swingHighDeg)} deg envelope without a clean repeatable rhythm.`;
    tradable = false;
  } else if (absShift >= 20 && oscillation.tradable) {
    label = `${shiftWord} trend`;
    note = `${shiftWord} trend roughly ${Math.round(absShift)} deg through the window, with smaller tradable oscillations riding on top of the one-sided move.`;
  } else if (absShift >= 20) {
    label = `${shiftWord} trend`;
    note = `${shiftWord} trend roughly ${Math.round(absShift)} deg through the window. The one-sided move matters more than trying to pick tiny oscillations.`;
  } else if (absShift >= 10) {
    label = `${shiftWord} bias`;
    note = `${shiftWord} trend is present, but it is modest enough that the mean line still matters more than hunting a full structural shift.`;
  } else if (oscillation.tradable) {
    label = 'Mostly steady';
    note = `The main race feature is a repeatable oscillation band rather than a large structural shift.`;
  } else if (rangeDeg >= 20 || timelineSummary.transitions.length >= 2) {
    label = 'Phasey / intermittent shifts';
    note = `Expect a ${rangeDeg} deg directional envelope, but the timing looks inconsistent enough to treat it cautiously.`;
    tradable = false;
  }

  const timing = shiftWindow
    ? (shiftWindow.mature && shiftWindow.mature !== shiftWindow.onset
      ? `First meaningful trend move near ${wxFmtHour(shiftWindow.onset)}, maturing around ${wxFmtHour(shiftWindow.mature)}.`
      : `First meaningful trend move near ${wxFmtHour(shiftWindow.onset)}.`)
    : 'No clear onset time for a larger one-sided trend.';

  return {
    label,
    note,
    tradable,
    shiftWord,
    absShiftDeg: Math.round(absShift),
    rangeDeg: Math.max(rangeDeg, oscillation.swingLowDeg),
    timing,
  };
}

function wxBuildRaceCall(metrics, dominantKey, confidence, timelineSummary, consensus, fingerprint = null) {
  const oscillation = wxEstimateOscillation(metrics, dominantKey, confidence, timelineSummary, fingerprint);
  const trend = wxEstimateTrendProfile(metrics, dominantKey, timelineSummary, oscillation, consensus);
  const bend = wxEstimateBend(metrics, dominantKey, consensus, fingerprint);

  let tactic = 'Lean on the mean and keep the plan simple.';
  if (trend.tradable && oscillation.tradable && trend.absShiftDeg >= 18) {
    tactic = `Treat it as a one-sided ${trend.shiftWord.toLowerCase()} trend with tradable ${oscillation.swingLowDeg}-${oscillation.swingHighDeg} deg oscillations on top.`;
  } else if (oscillation.tradable) {
    tactic = `Best playable feature is the oscillation band around the bent mean: about ${oscillation.swingLowDeg}-${oscillation.swingHighDeg} deg peak-to-peak every ${oscillation.periodMin}-${oscillation.periodMax} min.`;
  } else if (bend.active) {
    tactic = `Respect the ${bend.shiftWord?.toLowerCase() ?? ''} bend first. Most of the usable move is probably local geography, not a clean basin-wide shift.`.replace(/\s+/g, ' ').trim();
  } else if (dominantKey === 'frontal' || !trend.tradable) {
    tactic = 'Do not overtrade small flicks. The bigger edge is avoiding bad timing in unstable or transition phases.';
  } else if (trend.absShiftDeg >= 18) {
    tactic = `Respect the one-sided ${trend.shiftWord.toLowerCase()} more than any tiny counter-moves.`;
  }

  return {
    trend,
    bend,
    oscillation,
    tactic,
    bullets: [
      `Trend: ${trend.label}. ${trend.note}`,
      `Bend: ${bend.label}. ${bend.note}`,
      `Oscillation: ${oscillation.label}. ${oscillation.note}`,
      `Race use: ${tactic}`,
    ],
  };
}

function wxBuildWeatherSummary(models, consensus) {
  if (!consensus.length) return null;

  const fingerprint = wxFingerprint();
  const windowMetrics = wxWindowMetrics(consensus);
  const coverage = wxCoverageSummary(consensus);
  const fullWindowScores = wxScoreRegimes(windowMetrics, fingerprint);
  const timeline = wxClassifyTimeline(consensus, fingerprint);
  const timelineSummary = wxTimelineSummary(timeline);
  const aggregateScores = wxAggregateScores(timeline);
  const scoreRows = Object.entries(aggregateScores)
    .map(([key, value]) => ({ key, label: WX_REGIMES[key].label, color: WX_REGIMES[key].color, value }))
    .sort((a, b) => b.value - a.value);

  let dominantKey = timelineSummary.dominantKey;
  if (timelineSummary.dominantShare < 0.45 || dominantKey === 'mixed_transition') {
    dominantKey = fullWindowScores.selectedKey;
  }

  const dominantScores = {
    topScore: scoreRows[0]?.value ?? 50,
    separation: (scoreRows[0]?.value ?? 50) - (scoreRows[1]?.value ?? 45),
    topKey: scoreRows[0]?.key ?? dominantKey,
    ordered: scoreRows.map(row => [row.key, row.value]),
  };
  const confidence = wxEstimateConfidence(windowMetrics, coverage, dominantScores, timelineSummary.dominantShare);
  const dominantMeta = WX_REGIMES[dominantKey] || WX_REGIMES.mixed_transition;
  const drivers = wxBuildDrivers(dominantKey, windowMetrics, coverage, dominantScores, fingerprint);
  const raceCall = wxBuildRaceCall(windowMetrics, dominantKey, confidence, timelineSummary, consensus, fingerprint);
  const segments = wxWindowSegments(consensus);
  const strongest = wxFindPeakRow(consensus, 'ws_kt');
  const wetPeak = wxFindPeakRow(consensus, 'precip_mm', 0.1);
  const windPeak = wxFindPeakRow(consensus, 'gust_kt');
  const heatingPeak = wxFindPeakRow(consensus, 'shortwave_wm2', 150);
  const turn = wxFindBiggestTurn(consensus);
  const pressureMove = wxFindBiggestPressureMove(consensus);
  const hazards = wxBuildHazards(windowMetrics, confidence, { strongest, wetPeak, windPeak, heatingPeak, turn, pressureMove }, timelineSummary);

  const prevailingDir = windowMetrics.dirMeanDeg;
  const windMin = wxMin(consensus.map(row => row.ws_kt));
  const windMax = wxMax(consensus.map(row => row.ws_kt));
  const gustMax = windowMetrics.gustMaxKt;
  const tempMin = windowMetrics.tempLowC;
  const tempMax = windowMetrics.tempHighC;
  const totalPrecip = windowMetrics.precipTotalMm ?? 0;
  const secondary = scoreRows[1];

  let transitionLine = 'No major regime handoff stands out inside the selected window.';
  if (timelineSummary.transitions.length) {
    const firstTransition = timelineSummary.transitions[0];
    transitionLine = `The first regime handoff is near ${wxFmtHour(firstTransition.time_utc)} from ${WX_REGIMES[firstTransition.from].label} toward ${WX_REGIMES[firstTransition.to].label}.`;
  } else if (turn && turn.delta_deg >= 20) {
    transitionLine = `The main directional change is ${turn.delta_deg.toFixed(0)} deg between ${wxFmtHour(turn.start)} and ${wxFmtHour(turn.end)}.`;
  }
  const weatherLabel = totalPrecip >= 2 ? 'showery/wet' : totalPrecip >= 0.2 ? 'some rain risk' : 'mainly dry';
  const localDriver = fingerprint?.coast?.coastal && dominantKey === 'thermal'
    ? `Open water lies ${fingerprint.coast.nearest_open_water_km?.toFixed(0) ?? '-'} km away toward ${wxCardinal(fingerprint.coast.dominant_sea_bearing_deg)}.`
    : ((fingerprint?.terrain?.topo_potential ?? 0) > 0.1 && dominantKey === 'local'
      ? `Terrain relief is about ${fingerprint.terrain.relief_m?.toFixed(0) ?? '-'} m with a preferred axis near ${Math.round(fingerprint.terrain.terrain_axis_deg ?? 0)} deg.`
      : null);

  const briefingBullets = [
    `${dominantMeta.label} day with ${confidence.label.toLowerCase()} confidence (${confidence.score.toFixed(0)}/100).`,
    `Base flow: ${wxCardinal(prevailingDir)} ${windMin != null ? windMin.toFixed(0) : '-'}-${windMax != null ? windMax.toFixed(0) : '-'} kt, gusts ${gustMax != null ? gustMax.toFixed(0) : '-'} kt${strongest ? ` strongest near ${wxFmtHour(strongest.time_utc)}` : ''}.`,
    `Trend: ${raceCall.trend.label}. ${raceCall.trend.note} ${raceCall.trend.timing}`,
    `Bend: ${raceCall.bend.label}. ${raceCall.bend.note}`,
    `Oscillation: ${raceCall.oscillation.label}. ${raceCall.oscillation.note}`,
    `Race use: ${raceCall.tactic}`,
    `Weather/risk: ${weatherLabel}, temperatures ${tempMin != null ? tempMin.toFixed(1) : '-'} to ${tempMax != null ? tempMax.toFixed(1) : '-'} degC, with ${hazards[0].charAt(0).toLowerCase()}${hazards[0].slice(1)}`,
    secondary ? `Alternative scenario: ${WX_REGIMES[secondary.key].label} still scores ${secondary.value.toFixed(0)}/100, so timing can still wobble.` : null,
    localDriver && !raceCall.bend.active ? `Local driver: ${localDriver}` : null,
    confidence.score < 60 || coverage.overall < 60 ? `Confidence watch: model spread averages ${confidence.meanWsSpread.toFixed(1)} kt and ${confidence.meanDirSpread.toFixed(0)} deg. ${coverage.sentence}` : null,
  ].filter(Boolean).slice(0, 7);

  const reportLines = briefingBullets.map(line => `- ${line}`);

  return {
    confidence,
    report: reportLines.join('\n'),
    briefingBullets,
    cards: [
      {
        label: 'Day Type',
        value: dominantMeta.label,
        note: secondary ? `Secondary signal: ${WX_REGIMES[secondary.key].label}.` : 'No serious competing regime signal.',
      },
      {
        label: 'Wind Envelope',
        value: `${windMin != null ? windMin.toFixed(0) : '-'}-${windMax != null ? windMax.toFixed(0) : '-'} kt`,
        note: `${wxCardinal(prevailingDir)} mean flow, gusts ${gustMax != null ? gustMax.toFixed(0) : '-'} kt.`,
      },
      {
        label: 'Trend',
        value: raceCall.trend.label,
        note: `${raceCall.trend.absShiftDeg ? `${raceCall.trend.shiftWord} ${raceCall.trend.absShiftDeg} deg` : 'No major one-sided move'}${raceCall.trend.tradable ? ', potentially playable.' : ', handle with care.'}`,
      },
      {
        label: 'Bend',
        value: raceCall.bend.label,
        note: raceCall.bend.active
          ? `${raceCall.bend.source === 'coast' ? 'Coastline' : 'Terrain'} likely adds about ${raceCall.bend.absBendDeg} deg.`
          : raceCall.bend.note,
      },
      {
        label: 'Oscillation',
        value: raceCall.oscillation.tradable
          ? `${raceCall.oscillation.swingLowDeg}-${raceCall.oscillation.swingHighDeg} deg`
          : raceCall.oscillation.label,
        note: raceCall.oscillation.tradable
          ? `Every ${raceCall.oscillation.periodMin}-${raceCall.oscillation.periodMax} min, around +/-${raceCall.oscillation.bandLowDeg}-${raceCall.oscillation.bandHighDeg} deg.`
          : raceCall.oscillation.note,
      },
      {
        label: 'Confidence',
        value: `${confidence.label} ${confidence.score.toFixed(0)}/100`,
        note: `Spread averages ${confidence.meanWsSpread.toFixed(1)} kt and ${confidence.meanDirSpread.toFixed(0)} deg.`,
      },
    ],
    dominantMeta,
    raceCall,
    scoreRows,
    drivers,
    qualityNote: coverage.sentence,
    transitionLine,
    localDriver,
  };
}

function wxRenderCards(summary) {
  const grid = document.getElementById('wxFactsGrid');
  if (!grid || !summary) return;
  grid.innerHTML = summary.cards.map(card => `
    <article class="panel wx-card">
      <div class="wx-card-label">${card.label}</div>
      <div class="wx-card-value">${card.value}</div>
      <div class="wx-card-note">${card.note}</div>
    </article>
  `).join('');
}

function wxRenderSignals(summary) {
  const scoreRows = document.getElementById('wxScoreRows');
  const driverList = document.getElementById('wxDriverList');
  const qualityNote = document.getElementById('wxQualityNote');
  if (qualityNote) qualityNote.textContent = summary?.qualityNote ?? 'Awaiting forecast data';
  if (!scoreRows || !driverList || !summary) return;

  scoreRows.innerHTML = summary.scoreRows.map(row => `
    <div class="wx-score-row">
      <div class="wx-score-label">${row.label}</div>
      <div class="wx-score-bar"><div class="wx-score-fill" style="width:${row.value.toFixed(0)}%;background:${row.color}"></div></div>
      <div class="wx-score-value">${row.value.toFixed(0)}</div>
    </div>
  `).join('');

  const lines = [
    ...(summary.raceCall?.bullets ?? []).slice(0, 4),
    ...(summary.localDriver && !summary.raceCall?.bend?.active ? [summary.localDriver] : []),
  ].slice(0, 4);
  driverList.innerHTML = lines.map(line => `<li>${line}</li>`).join('');
}

function wxRenderReport(summary, modelCount) {
  const reportEl = document.getElementById('wxReportText');
  const statusEl = document.getElementById('wxStatus');
  if (reportEl && summary) reportEl.textContent = summary.report;
  if (statusEl) {
    const winner = forecastData?.winner_model_id ? ` Winner model: ${forecastData.winner_model_id}.` : '';
    statusEl.textContent = `Built from ${modelCount} active model${modelCount === 1 ? '' : 's'} in the selected window.${winner}`;
  }
}

function wxRenderWindChart(models, consensus) {
  const panel = document.getElementById('wxWindPanel');
  const chartDiv = document.getElementById('wxWindChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const speeds = series.hours.map(hour => hour.ws_ms != null ? +(hour.ws_ms * MS_TO_KT).toFixed(1) : null);
    if (!speeds.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: speeds,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.85,
    });
  });

  const consensusWind = consensus.map(row => row.ws_kt != null ? +row.ws_kt.toFixed(1) : null);
  if (consensusWind.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusWind,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  const consensusGust = consensus.map(row => row.gust_kt != null ? +row.gust_kt.toFixed(1) : null);
  if (consensusGust.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusGust,
      name: 'Consensus gust',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#f59e0b', width: 2, dash: 'dot' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('Wind (kt)') },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderDirChart(models, consensus) {
  const panel = document.getElementById('wxDirPanel');
  const chartDiv = document.getElementById('wxDirChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const dirs = series.hours.map(hour => hour.wd_deg != null ? +hour.wd_deg.toFixed(0) : null);
    if (!dirs.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: dirs,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.8,
    });
  });

  const consensusDir = consensus.map(row => row.wd_deg != null ? +row.wd_deg.toFixed(0) : null);
  if (consensusDir.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusDir,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 72, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: {
      title: { text: 'Direction (deg)', standoff: 16 },
      automargin: true,
      range: [0, 360],
      dtick: 90,
      gridcolor: '#e2e8f0',
      tickfont: { color: '#64748b' },
      tickvals: [0, 90, 180, 270, 360],
      ticktext: ['N', 'E', 'S', 'W', 'N'],
    },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderTempChart(models, consensus) {
  const panel = document.getElementById('wxTempPanel');
  const chartDiv = document.getElementById('wxTempChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const temps = series.hours.map(hour => hour.temp_c);
    if (!temps.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: temps,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.85,
    });
  });

  const consensusTemps = consensus.map(row => row.temp_c != null ? +row.temp_c.toFixed(1) : null);
  if (consensusTemps.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusTemps,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Temp (degC)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' } },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderPrecipChart(models, consensus) {
  const panel = document.getElementById('wxPrecipPanel');
  const chartDiv = document.getElementById('wxPrecipChart');
  if (!panel || !chartDiv) return;

  const consensusPrecip = consensus.map(row => row.precip_mm != null ? +row.precip_mm.toFixed(2) : null);
  const hasConsensus = consensusPrecip.some(value => value != null && value > 0);
  const traces = [];

  if (hasConsensus) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusPrecip,
      name: 'Consensus precip',
      type: 'bar',
      marker: { color: '#7dd3fc' },
    });
  }

  models.forEach(series => {
    const precip = series.hours.map(hour => hour.precip_mm != null ? +hour.precip_mm.toFixed(2) : null);
    if (!precip.some(value => value != null && value > 0)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: precip,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2 : 1.2 },
      opacity: 0.85,
    });
  });

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Precip (mm/h)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, rangemode: 'tozero' },
    bargap: 0.15,
  }, { responsive: true, displayModeBar: false });
}

function wxHidePanels() {
  ['wxWindPanel', 'wxDirPanel', 'wxTempPanel', 'wxPrecipPanel'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

function wxResetSignalPanels(message) {
  const qualityNote = document.getElementById('wxQualityNote');
  const scoreRows = document.getElementById('wxScoreRows');
  const driverList = document.getElementById('wxDriverList');
  if (qualityNote) qualityNote.textContent = message;
  if (scoreRows) scoreRows.innerHTML = '';
  if (driverList) driverList.innerHTML = `<li>${message}</li>`;
}

function renderWeatherTab(forceResetRange = false) {
  const reportEl = document.getElementById('wxReportText');
  const statusEl = document.getElementById('wxStatus');
  const factsEl = document.getElementById('wxFactsGrid');

  if (!forecastData?.models?.length) {
    if (statusEl) statusEl.textContent = 'Load forecast data to build the report.';
    if (reportEl) reportEl.textContent = 'Run validation, then load forecast data to generate a weather report from the active models.';
    if (factsEl) {
      factsEl.innerHTML = `
        <article class="panel wx-card">
          <div class="wx-card-label">Dominant Regime</div>
          <div class="wx-card-value">-</div>
          <div class="wx-card-note">Awaiting forecast data</div>
        </article>
      `;
    }
    wxResetSignalPanels('Load forecast data to inspect the regime drivers.');
    wxHidePanels();
    wxSetWindowSummary();
    return;
  }

  wxInitRangeControls(forceResetRange);
  const models = wxSelectedModels();
  const consensus = wxBuildConsensus(models);
  const summary = wxBuildWeatherSummary(models, consensus);
  if (!summary) {
    wxResetSignalPanels('Not enough forecast data in the selected window.');
    wxHidePanels();
    return;
  }

  wxRenderCards(summary);
  wxRenderSignals(summary);
  wxRenderReport(summary, models.length);
  wxRenderWindChart(models, consensus);
  wxRenderDirChart(models, consensus);
  wxRenderTempChart(models, consensus);
  wxRenderPrecipChart(models, consensus);
}

async function wxEnsureForecastThenRender(forceReload = false) {
  if (!forecastData || forceReload) {
    const statusEl = document.getElementById('wxStatus');
    if (statusEl) statusEl.textContent = 'Loading forecast data...';
    await loadForecast();
    renderWeatherTab(true);
    return;
  }
  renderWeatherTab();
}

document.querySelector('.tab[data-tab="weather"]')?.addEventListener('click', async () => {
  await wxEnsureForecastThenRender();
});

document.getElementById('wxRefreshBtn')?.addEventListener('click', async () => {
  await wxEnsureForecastThenRender(true);
});

document.getElementById('wxCopyBtn')?.addEventListener('click', async () => {
  const report = document.getElementById('wxReportText')?.textContent?.trim();
  const statusEl = document.getElementById('wxStatus');
  if (!report) return;
  try {
    await navigator.clipboard.writeText(report);
    renderWeatherTab();
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Report copied.`;
  } catch {
    renderWeatherTab();
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Copy failed in this browser.`;
  }
});
