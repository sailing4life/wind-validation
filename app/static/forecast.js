/* forecast.js  -  Forecast tab: Windy iframe + Plotly charts + hourly table */

const MS_TO_KT = 1.94384;
const LOCAL_TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
const LOCAL_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
});

function fcLocalTime(iso) {
  const date = new Date(iso);
  if (!iso || !Number.isFinite(date.getTime())) return 'unknown time';
  return LOCAL_TIME_FORMATTER.format(date);
}

// â”€â”€ Masthead height scaling â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Models give 10 m wind; scale up to masthead height with the log wind profile
// over open water (roughness z0 ≈ 0.0002 m). Speed only — direction is unchanged.
const Z0_SEA = 0.0002;
function mastheadFactor() {
  const z = parseFloat(document.getElementById('fcMastHeight')?.value) || 10;
  if (!(z > 10)) return 1.0;
  return Math.log(z / Z0_SEA) / Math.log(10 / Z0_SEA);
}

// â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let forecastData = null;
let _winnerModelId = '';
let _biasWsMs = 0;
let _validationQueryId = '';
let _selectedModels = new Set();
let _correctedOnly = false;
let _relayoutHandler = null;   // for range-slider sync
let _ensembleData = null;
let _gradientData = null;
let _forecastRequest = 0;
let _forecastValidation = null;
let _forecastComparison = null;
let _forecastIsMobile = window.innerWidth < 700;

// Chart configuration must be initialized before any async forecast response can render.
const LIGHT_LAYOUT = {
  paper_bgcolor: '#ffffff',
  plot_bgcolor: '#f8fafc',
  font: { color: '#1e293b', size: 11 },
};

const LIGHT_XAXIS = {
  gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, type: 'date',
  title: { text: `Local time (${LOCAL_TIME_ZONE})`, font: { size: 10 } },
};
const LIGHT_YAXIS = (title) => ({ title, gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, rangemode: 'tozero' });

// â”€â”€ Model color palette â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const FC_COLORS = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed', '#0891b2', '#be185d'];

// ── Best-series selection: blended consensus when available, else winner ─────
function bestSeries() {
  if (!forecastData) return null;
  if (forecastData.blend?.hours?.length) return forecastData.blend;
  const models = forecastData.models || [];
  return models.find(m => m.model_id === _winnerModelId) || models[0] || null;
}

function bestSeriesLabel() {
  const weights = forecastData?.calibration?.weights || {};
  if (forecastData?.blend?.hours?.length && Object.keys(weights).length > 1) {
    const parts = Object.entries(weights).sort((a, b) => b[1] - a[1])
      .map(([id, w]) => `${id} ${(w * 100).toFixed(0)}%`);
    return `Blend: ${parts.join(' + ')}`;
  }
  return _winnerModelId || 'Best model';
}

function modelColor(modelId) {
  if (!forecastData) return '#94a3b8';
  const idx = forecastData.models.findIndex(m => m.model_id === modelId);
  return idx >= 0 ? FC_COLORS[idx % FC_COLORS.length] : '#94a3b8';
}

// â”€â”€ Called by app.js after successful validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function setForecastParams(lat, lon, winnerModelId, biasWsMs, queryId = '') {
  _winnerModelId = winnerModelId || '';
  _biasWsMs = biasWsMs || 0;
  _validationQueryId = queryId || '';
  forecastData = null;
  _ensembleData = null;
  _gradientData = null;
}

// â”€â”€ Read current lat/lon from validation inputs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function currentLatLon() {
  const lat = parseFloat(document.getElementById('lat').value);
  const lon = parseFloat(document.getElementById('lon').value);
  return (isNaN(lat) || isNaN(lon)) ? null : { lat, lon };
}

// â”€â”€ Tab switching â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.querySelectorAll('button.tab[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    updateSidebarAction();
    resizeForecastCharts();
    if (btn.dataset.tab === 'validation' && typeof drawCharts === 'function') drawCharts();
  });
});

document.getElementById('fcRunBtn').addEventListener('click', loadForecast);
document.getElementById('fcCorrectedOnly')?.addEventListener('change', e => {
  _correctedOnly = e.target.checked;
  if (forecastData) renderAllCharts();
});
// Masthead height rescales displayed wind instantly — no re-fetch needed.
document.getElementById('fcMastHeight')?.addEventListener('input', () => {
  if (forecastData) { renderBestForecastChart(); renderForecastTable(); }
});

// â”€â”€ API call â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function loadForecast() {
  if (selectedLocationRecord?.monitoring_enabled && querySource === 'point') return loadLocationSnapshot();
  const requestId = ++_forecastRequest;
  const pos = currentLatLon();
  const status = document.getElementById('fcStatus');

  if (!pos) {
    status.textContent = 'Set coordinates in the sidebar first.';
    return;
  }

  const hoursAhead = parseInt(document.getElementById('fcHoursAhead').value, 10) || 48;
  status.textContent = 'Loading...';
  document.getElementById('fcRunBtn').disabled = true;

  try {
    const resp = await fetch('/api/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat: pos.lat,
        lon: pos.lon,
        winner_model_id: _winnerModelId,
        bias_ws_ms: _biasWsMs,
        query_id: _validationQueryId,
        hours_ahead: hoursAhead,
        radius_km: Number(document.getElementById('radius').value) || 50,
      }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt.slice(0, 200)}`);
    }
    const data = await resp.json();
    if (requestId !== _forecastRequest) return;
    renderPreparedForecast(data, null, null);
    document.getElementById('fcFreshness').textContent = `On demand · loaded ${fcLocalTime(new Date().toISOString())}`;
  } catch (err) {
    if (requestId === _forecastRequest) status.textContent = `Error: ${err.message}`;
  } finally {
    if (requestId === _forecastRequest) document.getElementById('fcRunBtn').disabled = false;
  }
}

// â”€â”€ Model toggle pills â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderModelToggles() {
  const container = document.getElementById('fcModelToggles');
  container.innerHTML = '';
  if (!forecastData) return;

  forecastData.models.forEach((series, idx) => {
    const color = FC_COLORS[idx % FC_COLORS.length];
    const isActive = _selectedModels.has(series.model_id);
    const isWinner = series.model_id === (forecastData.winner_model_id || '');

    const btn = document.createElement('button');
    btn.className = 'model-toggle' + (isActive ? ' active' : '');
    btn.style.setProperty('--mt-color', color);
    const winnerTag = isWinner ? ' *' : '';
    btn.innerHTML = '<span class="mt-dot"></span>';
    btn.append(document.createTextNode(series.model_id + winnerTag));
    btn.title = isWinner ? 'Winner model from validation' : '';

    btn.addEventListener('click', () => {
      if (_selectedModels.has(series.model_id)) {
        if (_selectedModels.size > 1) _selectedModels.delete(series.model_id);
      } else {
        _selectedModels.add(series.model_id);
      }
      renderModelToggles();
      renderAllCharts();
      if (typeof renderWeatherTab === 'function') renderWeatherTab();
    });
    container.appendChild(btn);
  });
}

// â”€â”€ Shared chart config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// â”€â”€ Ensemble stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function computeEnsembleStats(selectedSeries) {
  const timeMap = new Map(); // ISO string â†’ number[]
  for (const series of selectedSeries) {
    for (const h of series.hours) {
      if (h.ws_ms == null) continue;
      const kt = h.ws_ms * MS_TO_KT;
      if (!timeMap.has(h.time_utc)) timeMap.set(h.time_utc, []);
      timeMap.get(h.time_utc).push(kt);
    }
  }
  const sorted = [...timeMap.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  const times = sorted.map(e => e[0]);
  const means = sorted.map(e => {
    const v = e[1];
    return +(v.reduce((a, b) => a + b, 0) / v.length).toFixed(2);
  });
  const stds = sorted.map(e => {
    const v = e[1];
    if (v.length < 2) return 0;
    const m = v.reduce((a, b) => a + b, 0) / v.length;
    return +Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / (v.length - 1)).toFixed(2);
  });
  return { times, means, stds };
}

// â”€â”€ Value labels every 3h â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function every3hText(times, vals, fmt = v => String(v)) {
  return vals.map((v, i) => {
    const t = new Date(times[i]);
    return (t.getHours() % 3 === 0 && v != null) ? fmt(v) : '';
  });
}

// â”€â”€ Wind speed cell color â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function windSpeedColor(kt) {
  const t = Math.min(1, Math.max(0, (kt - 5) / 20));
  const hue = Math.round(220 - t * 220);
  return `hsl(${hue}, 75%, 82%)`;
}

// â”€â”€ Range sync: best-forecast slider â†’ all other charts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const _SYNC_CHART_IDS = [
  'fcEnsembleChart', 'fcEnsembleDirChart',
  'fcIconEpsTwsChart', 'fcIconEpsTwdChart',
  'fcGradientChart',
  'fcTempChart', 'fcPrecipChart',
];

function syncChartRanges(range) {
  for (const id of _SYNC_CHART_IDS) {
    const el = document.getElementById(id);
    if (el && el._fullLayout) Plotly.relayout(el, { 'xaxis.range': range });
  }
}

// â”€â”€ Chart 1: Best Forecast (winner model, expedition style) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderBestForecastChart() {
  const panel = document.getElementById('fcBestPanel');
  const chartDiv = document.getElementById('fcBestChart');
  if (!panel || !chartDiv) return;
  const winner = bestSeries();
  if (!winner) { panel.style.display = 'none'; return; }

  panel.style.display = '';
  const mf = mastheadFactor();
  document.getElementById('fcBestTitle').textContent = bestSeriesLabel() + (winner.hours.some(h => h.ws_p10_ms != null && h.ws_p90_ms != null) ? ' · p10–p90 band' : ' · uncertainty unavailable')
    + (mf > 1 ? ` · ${document.getElementById('fcMastHeight').value} m masthead` : '');

  const times = winner.hours.map(h => h.time_utc);
  const ws_kt = winner.hours.map(h => h.corrected_ws_ms != null ? +(h.corrected_ws_ms * MS_TO_KT * mf).toFixed(1) : (h.ws_ms != null ? +(h.ws_ms * MS_TO_KT * mf).toFixed(1) : null));
  const p10_kt = winner.hours.map(h => h.ws_p10_ms != null ? +(h.ws_p10_ms * MS_TO_KT * mf).toFixed(1) : null);
  const p90_kt = winner.hours.map(h => h.ws_p90_ms != null ? +(h.ws_p90_ms * MS_TO_KT * mf).toFixed(1) : null);
  const gust_kt = winner.hours.map(h => (h.corrected_gust_ms ?? h.gust_ms) != null ? +((h.corrected_gust_ms ?? h.gust_ms) * MS_TO_KT * mf).toFixed(1) : null);
  const wd = winner.hours.map(h => h.corrected_wd_deg ?? h.wd_deg);

  const mainWs = ws_kt;
  const mainLabel = forecastData.calibration?.status === 'insufficient_history' ? 'Raw TWS (kt)' : 'Corrected TWS (kt)';

  const traces = [];
  if (p10_kt.some(v => v != null) && p90_kt.some(v => v != null)) {
    traces.push({ x: times, y: p10_kt, type: 'scatter', mode: 'lines', line: { width: 0 }, hoverinfo: 'skip', showlegend: false });
    traces.push({ x: times, y: p90_kt, name: 'TWS p10–p90', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(37,99,235,.16)', line: { width: 0 } });
  }

  // TWS (or corrected)  -  blue solid line+markers+labels
  traces.push({
    x: times, y: mainWs,
    name: mainLabel,
    type: 'scatter', mode: 'lines+markers+text',
    line: { color: '#2563eb', width: 2 },
    marker: { color: '#2563eb', size: 6 },
    text: every3hText(times, mainWs),
    textposition: 'top center',
    textfont: { size: 10, color: '#1e3a8a', weight: 600 },
    yaxis: 'y1',
  });


  // Gust  -  light blue dashed+X+labels
  if (gust_kt.some(v => v != null)) {
    traces.push({
      x: times, y: gust_kt,
      name: 'Gust (kt)',
      type: 'scatter', mode: 'lines+markers+text',
      line: { color: '#93c5fd', width: 1.5, dash: 'dash' },
      marker: { color: '#93c5fd', size: 6, symbol: 'x' },
      text: every3hText(times, gust_kt),
      textposition: 'top center',
      textfont: { size: 9, color: '#1e40af' },
      yaxis: 'y1',
    });
  }

  // TWD  -  red line+markers+labels, right axis
  traces.push({
    x: times, y: wd,
    name: 'TWD ( deg)',
    type: 'scatter', mode: 'lines+markers+text',
    line: { color: '#dc2626', width: 1.5 },
    marker: { color: '#dc2626', size: 5 },
    text: every3hText(times, wd, v => String(Math.round(v))),
    textposition: 'top center',
    textfont: { size: 9, color: '#dc2626' },
    connectgaps: false,
    yaxis: 'y2',
  });

  const mobile = window.innerWidth < 700;
  _forecastIsMobile = mobile;
  if (mobile) traces.forEach(trace => {
    if (trace.mode?.includes('text')) { trace.mode = trace.mode.replace('+text', ''); delete trace.text; }
  });
  const mobileRange = mobile && times.length ? [fcPlotTime(times[0]), fcPlotTime(new Date(new Date(times[0]).getTime() + 12 * 3600000).toISOString())] : null;

  const layout = {
    ...LIGHT_LAYOUT,
    height: 480,
    margin: { t: 70, b: 30, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.18, font: { size: 11 } },
    xaxis: {
      ...LIGHT_XAXIS,
      ...(mobileRange ? {range: mobileRange} : {}),
      rangeselector: {
        buttons: [
          { count: 12, label: '12h', step: 'hour', stepmode: 'backward' },
          { count: 24, label: '24h', step: 'hour', stepmode: 'backward' },
          { count: 48, label: '48h', step: 'hour', stepmode: 'backward' },
          { step: 'all', label: 'All' },
        ],
        bgcolor: '#f1f5f9',
        activecolor: '#0369a1',
        bordercolor: '#e2e8f0',
        font: { size: 10 },
      },
      rangeslider: { visible: true, thickness: 0.06 },
    },
    yaxis: { ...LIGHT_YAXIS('kt'), zeroline: false },
    yaxis2: {
      title: ' deg', overlaying: 'y', side: 'right',
      range: [0, 360], dtick: 90,
      gridcolor: 'transparent',
      tickfont: { color: '#dc2626' },
      titlefont: { color: '#dc2626' },
    },
  };

  fcLocalPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });

  // Re-attach range-sync listener (replaces previous one on re-render)
  if (_relayoutHandler) chartDiv.removeListener('plotly_relayout', _relayoutHandler);
  _relayoutHandler = (ev) => {
    if (ev['xaxis.range[0]'] != null) {
      syncChartRanges([ev['xaxis.range[0]'], ev['xaxis.range[1]']]);
    } else if (ev['xaxis.autorange']) {
      for (const id of _SYNC_CHART_IDS) {
        const el = document.getElementById(id);
        if (el && el._fullLayout) Plotly.relayout(el, { 'xaxis.autorange': true });
      }
    }
  };
  chartDiv.on('plotly_relayout', _relayoutHandler);
}

// â”€â”€ Chart 2: Ensemble (all selected models + mean Â± 1Ïƒ) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderEnsembleChart() {
  const row = document.getElementById('fcEnsembleRow');
  const chartDiv = document.getElementById('fcEnsembleChart');
  if (!row || !chartDiv) return;

  const { winner_model_id, models } = forecastData;
  const selected = models.filter(m => _selectedModels.has(m.model_id));
  if (selected.length === 0) { row.style.display = 'none'; return; }
  row.style.display = '';

  const traces = [];

  // Individual model lines
  selected.forEach(series => {
    const color = modelColor(series.model_id);
    const isWinner = series.model_id === winner_model_id;
    const times = series.hours.map(h => h.time_utc);
    const ws_kt = series.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
    traces.push({
      x: times, y: ws_kt,
      name: series.model_id,
      type: 'scatter', mode: 'lines+markers',
      line: { color, width: isWinner ? 2 : 1.5 },
      marker: { color, size: isWinner ? 5 : 4 },
      opacity: 0.85,
    });
  });

  // Ensemble mean + +/-1 sigma band
  if (selected.length > 1) {
    const stats = computeEnsembleStats(selected);
    const upper = stats.means.map((m, i) => +(m + stats.stds[i]).toFixed(2));
    const lower = stats.means.map((m, i) => +(m - stats.stds[i]).toFixed(2));

    // Upper bound (invisible anchor for fill)
    traces.push({
      x: stats.times, y: upper,
      type: 'scatter', mode: 'lines',
      line: { width: 0, color: 'rgba(20,184,166,0)' },
      showlegend: false, hoverinfo: 'skip',
    });
    // Lower bound fills to previous trace
    traces.push({
      x: stats.times, y: lower,
      name: '+/-1 sigma',
      type: 'scatter', mode: 'lines',
      fill: 'tonexty',
      fillcolor: 'rgba(20,184,166,0.18)',
      line: { width: 0, color: 'rgba(20,184,166,0)' },
      hoverinfo: 'skip',
    });
    // Mean line
    traces.push({
      x: stats.times, y: stats.means,
      name: 'Ensemble mean',
      type: 'scatter', mode: 'lines',
      line: { color: '#000000', width: 2, dash: 'dash' },
    });
  }

  const layout = {
    ...LIGHT_LAYOUT,
    height: 370,
    margin: { t: 50, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
  };

  fcLocalPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });

  renderEnsembleDirChart(selected, winner_model_id);
}

// â”€â”€ Chart 2b: Ensemble TWD â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderEnsembleDirChart(selected, winner_model_id) {
  const chartDiv = document.getElementById('fcEnsembleDirChart');
  if (!chartDiv) return;

  const traces = [];
  selected.forEach(series => {
    const color = modelColor(series.model_id);
    const isWinner = series.model_id === winner_model_id;
    const times = series.hours.map(h => h.time_utc);
    const wd = series.hours.map(h => h.wd_deg != null ? +h.wd_deg.toFixed(0) : null);
    traces.push({
      x: times, y: wd,
      name: series.model_id,
      type: 'scatter', mode: 'lines+markers',
      line: { color, width: isWinner ? 2 : 1.5 },
      marker: { color, size: isWinner ? 5 : 4 },
      opacity: 0.85,
      showlegend: false,
    });
  });

  fcLocalPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 370,
    margin: { t: 20, b: 50, l: 70, r: 20 },
    showlegend: false,
    xaxis: { ...LIGHT_XAXIS },
    yaxis: {
      title: { text: 'TWD (deg)', standoff: 16 },
      automargin: true,
      range: [0, 360], dtick: 90,
      gridcolor: '#e2e8f0',
      tickfont: { color: '#64748b' },
      tickvals: [0, 90, 180, 270, 360],
      ticktext: ['N (0 deg)', 'E (90 deg)', 'S (180 deg)', 'W (270 deg)', 'N (360 deg)'],
    },
  }, { responsive: true, displayModeBar: false });
}

// â”€â”€ Chart 3: Temperature â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderTempChart() {
  const panel = document.getElementById('fcTempPanel');
  const chartDiv = document.getElementById('fcTempChart');
  if (!panel || !chartDiv) return;
  if (_correctedOnly) { panel.style.display = 'none'; return; }

  const { models } = forecastData;
  const selected = models.filter(m => _selectedModels.has(m.model_id));
  const traces = [];

  selected.forEach(series => {
    const color = modelColor(series.model_id);
    const times = series.hours.map(h => h.time_utc);
    const temp = series.hours.map(h => h.temp_c != null ? +h.temp_c.toFixed(1) : null);
    if (!temp.some(v => v != null)) return;
    traces.push({
      x: times, y: temp, name: series.model_id,
      type: 'scatter', mode: 'lines+markers',
      line: { color, width: 1.5 },
      marker: { color, size: 4 },
    });
  });

  if (traces.length === 0) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const layout = {
    ...LIGHT_LAYOUT,
    height: 240,
    margin: { t: 15, b: 50, l: 55, r: 20 },
    showlegend: false,
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Temp ( degC)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' } },
  };

  fcLocalPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });
}

// â”€â”€ Chart 4: Precipitation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderPrecipChart() {
  const panel = document.getElementById('fcPrecipPanel');
  const chartDiv = document.getElementById('fcPrecipChart');
  if (!panel || !chartDiv) return;
  if (_correctedOnly) { panel.style.display = 'none'; return; }

  const { winner_model_id, models } = forecastData;
  const winner = models.find(m => m.model_id === winner_model_id) || models[0];
  if (!winner) { panel.style.display = 'none'; return; }

  const times = winner.hours.map(h => h.time_utc);
  const precip = winner.hours.map(h => h.precip_mm != null ? +h.precip_mm.toFixed(2) : null);

  if (!precip.some(v => v != null && v > 0)) { panel.style.display = 'none'; return; }
  panel.style.display = '';


  const layout = {
    ...LIGHT_LAYOUT,
    height: 220,
    margin: { t: 15, b: 50, l: 55, r: 20 },
    showlegend: false,
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Precip (mm/h)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, rangemode: 'tozero' },
    bargap: 0.15,
  };

  fcLocalPlot(chartDiv, [{
    x: times, y: precip,
    name: 'Precipitation',
    type: 'bar',
    marker: { color: '#60a5fa' },
  }], layout, { responsive: true, displayModeBar: false });
}

// â”€â”€ Render all charts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderAllCharts() {
  if (!forecastData) return;
  renderBestForecastChart();
  if (!document.getElementById('fcEvidence').open) return;
  renderEnsembleChart();
  renderIconEpsCharts();
  if (_gradientData) renderGradientChart();
  renderTempChart();
  renderPrecipChart();
  // Show temp+precip row if at least one panel is visible
  const row = document.getElementById('fcTempPrecipRow');
  if (row) {
    const tempVis   = document.getElementById('fcTempPanel')?.style.display   !== 'none';
    const precipVis = document.getElementById('fcPrecipPanel')?.style.display !== 'none';
    row.style.display = (tempVis || precipVis) ? '' : 'none';
  }
  renderForecastTable();
  renderVerification();
}

function renderVerification() {
  const panel = document.getElementById('fcVerificationPanel');
  const el = document.getElementById('fcVerification');
  if (!panel || !el) return;
  const c = forecastData?.calibration || {};
  panel.style.display = '';
  if (c.status === 'insufficient_history') {
    el.innerHTML = `<p class="fc-status">Insufficient local history (n=${c.n_effective ?? 0}). Best Forecast remains raw; ICON-EPS p10–p90 is shown separately.</p>`;
    return;
  }
  const weights = c.weights || {};
  const weightsLabel = Object.entries(weights).sort((a, b) => b[1] - a[1])
    .map(([id, w]) => `${id} ${(w * 100).toFixed(0)}%`).join(' + ') || '—';
  const uncertaintyText = {
    eps_calibrated: `ICON-EPS ensemble spread, calibrated to local skill (×${(c.eps_factor ?? 0.65).toFixed(2)})`,
  }[c.uncertainty_source] || 'historical local-hour, regime and lead-matched residuals; recent bias correction fades with lead time';
  el.innerHTML = `<div class="meta-row"><span class="meta-label">Status:</span> ${fcEscape(c.status || "unknown")} &nbsp; <span class="meta-label">Band evidence (n eff.):</span> ${c.n_effective}</div>
    <div class="meta-row"><span class="meta-label">Method:</span> inverse-MSE blend of calibrated models &nbsp; <span class="meta-label">Weights:</span> ${fcEscape(weightsLabel)}</div>
    <div class="meta-row"><span class="meta-label">Uncertainty:</span> ${uncertaintyText}.</div>
    <div id="fcErrorProfile" class="fc-error-profile" aria-label="Historical error profile by forecast hour"></div>
    <section class="fc-adjustments" aria-labelledby="fcAdjustmentsTitle">
      <div id="fcAdjustmentsTitle" class="fc-adjustments-title">Model adjustments · Δ TWS (kt)</div>
      <p class="fc-adjustments-note">Positive adds wind; negative removes it. Hover a value for raw → corrected wind and direction shift.</p>
      <div id="fcAdjustmentsTable" class="fc-adjustments-table"></div>
    </section>`;
  renderHistoricalErrorProfile();
  renderModelAdjustments();
}

function fcSigned(value, digits = 1) {
  if (value == null || !Number.isFinite(value)) return '—';
  const rounded = Number(value.toFixed(digits));
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(digits)}`;
}

function fcDirectionShift(rawDeg, correctedDeg) {
  if (rawDeg == null || correctedDeg == null) return null;
  return ((correctedDeg - rawDeg + 540) % 360) - 180;
}

function fcRawBlendSpeed(timeIso, weights) {
  let sumU = 0, sumV = 0, total = 0;
  for (const [modelId, weight] of Object.entries(weights)) {
    const hour = forecastData?.models?.find(m => m.model_id === modelId)?.hours?.find(h => h.time_utc === timeIso);
    if (!hour || hour.ws_ms == null || hour.wd_deg == null) continue;
    const theta = hour.wd_deg * Math.PI / 180;
    sumU += weight * (-hour.ws_ms * Math.sin(theta));
    sumV += weight * (-hour.ws_ms * Math.cos(theta));
    total += weight;
  }
  return total ? Math.hypot(sumU / total, sumV / total) : null;
}

function renderModelAdjustments() {
  const target = document.getElementById('fcAdjustmentsTable');
  const series = bestSeries();
  const weights = forecastData?.calibration?.weights || {};
  const modelIds = Object.keys(weights).filter(id => forecastData?.models?.some(m => m.model_id === id));
  const ids = modelIds.length ? modelIds : [_winnerModelId].filter(Boolean);
  if (!target || !series?.hours?.length || !ids.length) return;

  const scroll = document.createElement('div');
  scroll.className = 'fc-adjustments-scroll';
  const table = document.createElement('table');
  table.className = 'fc-table fc-adjustments-data';
  table.innerHTML = `<thead><tr><th>Local time</th><th>Blend Δ</th>${ids.map(id => `<th>${fcEscape(id)} Δ</th>`).join('')}</tr></thead>`;
  const body = document.createElement('tbody');

  series.hours.forEach(blendHour => {
    const label = fcLocalTime(blendHour.time_utc);
    const rawBlend = fcRawBlendSpeed(blendHour.time_utc, weights);
    const blendCorrected = blendHour.corrected_ws_ms ?? blendHour.ws_ms;
    const blendDelta = rawBlend != null && blendCorrected != null ? (blendCorrected - rawBlend) * MS_TO_KT : null;
    const cells = [`<td class="fc-time">${label}</td>`, fcAdjustmentCell(blendDelta, rawBlend, blendCorrected, null, 'Blend')];
    ids.forEach(id => {
      const hour = forecastData.models.find(m => m.model_id === id)?.hours?.find(h => h.time_utc === blendHour.time_utc);
      const corrected = hour?.corrected_ws_ms ?? hour?.ws_ms;
      const delta = hour?.ws_ms != null && corrected != null ? (corrected - hour.ws_ms) * MS_TO_KT : null;
      const dirShift = fcDirectionShift(hour?.wd_deg, hour?.corrected_wd_deg ?? hour?.wd_deg);
      cells.push(fcAdjustmentCell(delta, hour?.ws_ms, corrected, dirShift, id));
    });
    const row = document.createElement('tr');
    row.innerHTML = cells.join('');
    body.appendChild(row);
  });
  table.appendChild(body);
  scroll.appendChild(table);
  target.replaceChildren(scroll);
}

function fcAdjustmentCell(deltaKt, rawMs, correctedMs, directionShift, label) {
  const cls = deltaKt > 0.05 ? 'fc-adjustment-up' : deltaKt < -0.05 ? 'fc-adjustment-down' : 'fc-adjustment-flat';
  const detail = rawMs == null || correctedMs == null
    ? `${label}: forecast unavailable`
    : `${label}: ${(rawMs * MS_TO_KT).toFixed(1)} → ${(correctedMs * MS_TO_KT).toFixed(1)} kt${directionShift != null ? ` · TWD ${fcSigned(directionShift, 0)}°` : ''}`;
  return `<td class="fc-adjustment ${cls}" title="${fcEscape(detail)}">${fcSigned(deltaKt)}<span class="fc-adjustment-unit"> kt</span></td>`;
}

function renderHistoricalErrorProfile() {
  const target = document.getElementById('fcErrorProfile');
  const winner = bestSeries();
  const rows = winner?.hours?.filter(h => h.calibration_sigma_along_ms != null && h.calibration_sigma_cross_ms != null) || [];
  if (!target || !rows.length || typeof Plotly === 'undefined') return;

  const times = rows.map(h => h.time_utc);
  const along = rows.map(h => +(h.calibration_sigma_along_ms * MS_TO_KT).toFixed(2));
  const cross = rows.map(h => +(h.calibration_sigma_cross_ms * MS_TO_KT).toFixed(2));
  const nEff = rows.map(h => h.calibration_n_effective);
  const sourceLabels = {
    eps_calibrated: 'ICON-EPS spread · calibrated to local skill',
    blend: 'Blended historical residuals',
    historical_hour_regime: 'Historical local-hour + regime residuals',
  };
  const source = sourceLabels[rows[0].calibration_uncertainty_source] || 'Recent regime residuals';
  const layout = {
    height: 245,
    margin: { l: 42, r: 40, t: 38, b: 35 },
    title: { text: `Error profile · ${source}`, x: 0, xanchor: 'left', font: { size: 12 } },
    paper_bgcolor: 'white', plot_bgcolor: 'white',
    xaxis: { title: { text: `Forecast time (${LOCAL_TIME_ZONE})`, font: { size: 10 } }, tickformat: '%d %b<br>%H:%M', showgrid: false },
    yaxis: { title: { text: 'σ residual (kt)', font: { size: 10 } }, rangemode: 'tozero', gridcolor: '#e2e8f0' },
    yaxis2: { title: { text: 'n eff.', font: { size: 10 } }, overlaying: 'y', side: 'right', rangemode: 'tozero', showgrid: false },
    legend: { orientation: 'h', y: 1.18, x: 0, font: { size: 10 } },
    hovermode: 'x unified',
  };
  fcLocalPlot(target, [
    { x: times, y: nEff, type: 'bar', name: 'Comparable cases (n eff.)', yaxis: 'y2', marker: { color: '#cbd5e1' }, hovertemplate: '%{y:.1f}<extra>n eff.</extra>' },
    { x: times, y: along, type: 'scatter', mode: 'lines+markers', name: 'σ along-wind', line: { color: '#0369a1', width: 2 }, marker: { size: 4 }, hovertemplate: '%{y:.2f} kt<extra>σ along</extra>' },
    { x: times, y: cross, type: 'scatter', mode: 'lines+markers', name: 'σ cross-wind', line: { color: '#b45309', width: 2 }, marker: { size: 4 }, hovertemplate: '%{y:.2f} kt<extra>σ cross</extra>' },
  ], layout, { responsive: true, displayModeBar: false });
}

// â”€â”€ ICON-EPS ensemble load + render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function loadEnsemble() {
  const requestData = forecastData;
  const pos = currentLatLon();
  if (!pos) return;

  const statusEl = document.getElementById('fcEpsStatus');
  const row = document.getElementById('fcIconEpsRow');
  const badge = document.getElementById('fcEpsSpreadBadge');

  if (statusEl) statusEl.textContent = 'Loading ICON-EPS…';
  if (badge) badge.style.display = 'none';
  if (row) row.style.display = '';

  const hoursAhead = parseInt(document.getElementById('fcHoursAhead').value, 10) || 120;

  try {
    const resp = await fetch(
      `/api/forecast-ensemble?lat=${pos.lat}&lon=${pos.lon}&hours=${Math.min(hoursAhead, 120)}`
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 120)}`);
    const result = await resp.json();
    if (requestData !== forecastData) return;
    _ensembleData = result;

    if (statusEl) statusEl.textContent = `${_ensembleData.n_members} members`;
    if (badge) {
      const lbl = _ensembleData.spread_label;
      badge.textContent = `${lbl}  ·  ${_ensembleData.spread_kt} kt spread`;
      badge.className = `badge badge-eps-${lbl}`;
      badge.style.display = '';
    }
    _renderEpsTwsChart();
    _renderEpsTwdChart();
  } catch (err) {
    if (requestData !== forecastData) return;
    if (statusEl) statusEl.textContent = `ICON-EPS unavailable: ${err.message}`;
    // keep row visible so the error message is readable
  }
}

function renderIconEpsCharts() {
  if (!_ensembleData) return;
  _renderEpsTwsChart();
  _renderEpsTwdChart();
}

function _renderEpsTwsChart() {
  const el = document.getElementById('fcIconEpsTwsChart');
  if (!el || !_ensembleData) return;
  const { tws } = _ensembleData;
  const { times } = tws;

  // Hourly boxes up to 48h; 3-hourly beyond that to keep the chart readable
  const stepH = times.length <= 49 ? 1 : 3;
  const idxs = [];
  for (let i = 0; i < times.length; i += stepH) {
    if (tws.p25[i] != null && tws.p50[i] != null && tws.p75[i] != null) idxs.push(i);
  }

  const traces = [{
    type: 'box',
    x: idxs.map(i => times[i]),
    lowerfence: idxs.map(i => tws.p10[i]),
    q1:         idxs.map(i => tws.p25[i]),
    median:     idxs.map(i => tws.p50[i]),
    q3:         idxs.map(i => tws.p75[i]),
    upperfence: idxs.map(i => tws.p90[i]),
    name: 'ICON-EPS (p10–p90)',
    marker: { color: '#4f46e5' },
    line: { color: '#4f46e5', width: 1.2 },
    fillcolor: 'rgba(99,102,241,0.20)',
    width: stepH * 3600e3 * 0.55,   // box width in ms on the date axis
  }];

  // ICON-EU deterministic line through the boxes (fallback: winner model)
  if (forecastData) {
    const overlay = forecastData.models.find(m => m.model_id === 'icon_eu')
      || forecastData.models.find(m => m.model_id === forecastData.winner_model_id)
      || forecastData.models[0];
    if (overlay) {
      traces.push({
        x: overlay.hours.map(h => h.time_utc),
        y: overlay.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null),
        name: `${overlay.model_id} (det.)`,
        type: 'scatter', mode: 'lines+markers',
        line: { color: '#2563eb', width: 2 },
        marker: { color: '#2563eb', size: 4 },
      });
    }
  }

  fcLocalPlot(el, traces, {
    ...LIGHT_LAYOUT,
    height: 310,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
  }, { responsive: true, displayModeBar: false });
}

function _renderEpsTwdChart() {
  const el = document.getElementById('fcIconEpsTwdChart');
  if (!el || !_ensembleData) return;
  const { twd } = _ensembleData;
  const { times } = twd;

  // Hourly boxes up to 48h; 3-hourly beyond that (same cadence as the TWS chart).
  // Signed deviations are added to the circular mean so the axis stays linear
  // (no 0/360 wraparound inside a single box).
  const stepH = times.length <= 49 ? 1 : 3;
  const idxs = [];
  for (let i = 0; i < times.length; i += stepH) {
    if (twd.p50[i] != null && twd.p25_dev[i] != null && twd.p75_dev[i] != null) idxs.push(i);
  }

  const traces = [{
    type: 'box',
    x: idxs.map(i => times[i]),
    lowerfence: idxs.map(i => twd.p10_dev[i] != null ? +(twd.p50[i] + twd.p10_dev[i]).toFixed(1) : null),
    q1:         idxs.map(i => +(twd.p50[i] + twd.p25_dev[i]).toFixed(1)),
    median:     idxs.map(i => twd.p50[i]),
    q3:         idxs.map(i => +(twd.p50[i] + twd.p75_dev[i]).toFixed(1)),
    upperfence: idxs.map(i => twd.p90_dev[i] != null ? +(twd.p50[i] + twd.p90_dev[i]).toFixed(1) : null),
    name: 'ICON-EPS (p10–p90)',
    marker: { color: '#dc2626' },
    line: { color: '#dc2626', width: 1.2 },
    fillcolor: 'rgba(220,38,38,0.18)',
    width: stepH * 3600e3 * 0.55,   // box width in ms on the date axis
  }];

  // ICON-EU deterministic TWD through the boxes (fallback: winner model)
  if (forecastData) {
    const overlay = forecastData.models.find(m => m.model_id === 'icon_eu')
      || forecastData.models.find(m => m.model_id === forecastData.winner_model_id)
      || forecastData.models[0];
    if (overlay) {
      traces.push({
        x: overlay.hours.map(h => h.time_utc),
        y: overlay.hours.map(h => h.wd_deg != null ? +h.wd_deg.toFixed(0) : null),
        name: `${overlay.model_id} (det.)`,
        type: 'scatter', mode: 'lines+markers',
        line: { color: '#2563eb', width: 2 },
        marker: { color: '#2563eb', size: 4 },
        connectgaps: false,
      });
    }
  }

  fcLocalPlot(el, traces, {
    ...LIGHT_LAYOUT,
    height: 310,
    margin: { t: 20, b: 50, l: 70, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: {
      title: { text: 'TWD (deg)', standoff: 16 },
      automargin: true,
      gridcolor: '#e2e8f0',
      tickfont: { color: '#64748b' },
    },
  }, { responsive: true, displayModeBar: false });
}

// ── Gradient wind (925 hPa) ──────────────────────────────────────────────────────
async function loadGradientWind() {
  const requestData = forecastData;
  const pos = currentLatLon();
  const panel = document.getElementById('fcGradientPanel');
  const statusEl = document.getElementById('fcGradientStatus');
  if (!pos || !panel) return;

  panel.style.display = '';
  if (statusEl) statusEl.textContent = 'Loading…';

  const hoursAhead = parseInt(document.getElementById('fcHoursAhead').value, 10) || 48;

  try {
    const resp = await fetch(
      `/api/gradient-wind?lat=${pos.lat}&lon=${pos.lon}&hours=${Math.min(hoursAhead, 168)}`
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 120)}`);
    const result = await resp.json();
    if (requestData !== forecastData) return;
    _gradientData = result;
    if (statusEl) statusEl.textContent = _gradientData.model;
    renderGradientChart();
  } catch (err) {
    if (requestData !== forecastData) return;
    if (statusEl) statusEl.textContent = `unavailable: ${err.message}`;
  }
}

function renderGradientChart() {
  const el = document.getElementById('fcGradientChart');
  if (!el || !_gradientData) return;
  const { times, ws925_kt, wd925_deg, ws10_kt, wd10_deg } = _gradientData;

  const traces = [
    { x: times, y: ws925_kt, name: '925 hPa TWS (kt)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#7c3aed', width: 2 },
      marker: { color: '#7c3aed', size: 4 },
      yaxis: 'y1' },
    { x: times, y: ws10_kt, name: '10 m TWS (kt)',
      type: 'scatter', mode: 'lines',
      line: { color: '#94a3b8', width: 1.5, dash: 'dash' },
      yaxis: 'y1' },
    { x: times, y: wd925_deg, name: '925 hPa TWD (deg)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#dc2626', width: 1.5 },
      marker: { color: '#dc2626', size: 4 },
      connectgaps: false,
      yaxis: 'y2' },
    { x: times, y: wd10_deg, name: '10 m TWD (deg)',
      type: 'scatter', mode: 'lines',
      line: { color: '#f87171', width: 1.5, dash: 'dash' },
      connectgaps: false,
      yaxis: 'y2' },
  ];

  fcLocalPlot(el, traces, {
    ...LIGHT_LAYOUT,
    height: 310,
    margin: { t: 20, b: 50, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('kt'), zeroline: false },
    yaxis2: {
      title: ' deg', overlaying: 'y', side: 'right',
      range: [0, 360], dtick: 90,
      gridcolor: 'transparent',
      tickfont: { color: '#dc2626' },
      titlefont: { color: '#dc2626' },
    },
  }, { responsive: true, displayModeBar: false });
}

// â”€â”€ Hourly forecast table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderForecastTable() {
  const wrap = document.getElementById('fcTableWrap');
  if (!wrap) return;

  const mf = mastheadFactor();
  const winner = bestSeries();
  if (!winner) { wrap.style.display = 'none'; return; }
  wrap.innerHTML = '';
  wrap.style.display = '';

  const heading = document.createElement('div');
  heading.className = 'fc-chart-title';
  heading.textContent = `Hourly forecast  -  ${bestSeriesLabel()}`;
  wrap.appendChild(heading);

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'fc-table-scroll';

  const hasPrecip = winner.hours.some(h => h.precip_mm != null);
  let colHtml = '<th>Local time</th><th>TWS p10–p90 (kt)</th>';
  colHtml += '<th>Gust max (kt)</th><th>TWD p10–p90 ( deg)</th><th>Temp ( degC)</th>';
  if (hasPrecip) colHtml += '<th>Rain</th>';
  colHtml += '<th class="note-col">Notes</th>';

  const table = document.createElement('table');
  table.className = 'fc-table';
  table.innerHTML = `<thead><tr>${colHtml}</tr></thead>`;

  const tbody = document.createElement('tbody');
  for (const hour of winner.hours) {
    const ws_kt = hour.corrected_ws_ms != null ? (hour.corrected_ws_ms * MS_TO_KT * mf).toFixed(1) : (hour.ws_ms != null ? (hour.ws_ms * MS_TO_KT * mf).toFixed(1) : null);
    const wsRange = hour.ws_p10_ms != null && hour.ws_p90_ms != null ? `${(hour.ws_p10_ms * MS_TO_KT * mf).toFixed(1)}–${(hour.ws_p90_ms * MS_TO_KT * mf).toFixed(1)}` : (ws_kt ?? ' - ');
    const gust_kt = (hour.corrected_gust_ms ?? hour.gust_ms) != null ? ((hour.corrected_gust_ms ?? hour.gust_ms) * MS_TO_KT * mf).toFixed(1) : null;
    const wd = hour.wd_p10_deg != null && hour.wd_p90_deg != null ? `${Math.round(hour.wd_p10_deg)}–${Math.round(hour.wd_p90_deg)}°` : (hour.corrected_wd_deg ?? hour.wd_deg) != null ? Math.round(hour.corrected_wd_deg ?? hour.wd_deg) + '°' : ' - ';
    const temp = hour.temp_c != null ? hour.temp_c.toFixed(1) : ' - ';
    const precip = hour.precip_mm != null ? hour.precip_mm.toFixed(2) : ' - ';

    const label = fcLocalTime(hour.time_utc);
    const wsColor = ws_kt != null ? windSpeedColor(+ws_kt) : '';
    const gustColor = gust_kt != null ? windSpeedColor(+gust_kt) : '';

    const tr = document.createElement('tr');
    let cells = `<td class="fc-time">${label}</td>`;
    cells += `<td class="fc-num" style="background:${wsColor}">${wsRange}</td>`;
    cells += `<td class="fc-num" style="background:${gustColor}">${gust_kt ?? ' - '}</td>`;
    cells += `<td class="fc-num">${wd}</td>`;
    cells += `<td class="fc-num">${temp}</td>`;
    if (hasPrecip) cells += `<td class="fc-num">${precip}</td>`;
    cells += `<td class="note-cell" contenteditable="true"></td>`;
    tr.innerHTML = cells;
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  scrollWrap.appendChild(table);
  wrap.appendChild(scrollWrap);
}


// Forecast workspace: render archived results without issuing provider requests.
function fcEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
}
function fcAge(iso) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (!Number.isFinite(minutes)) return 'at an unknown time';
  return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : minutes < 1440 ? `${(minutes / 60).toFixed(1)}h ago` : `${Math.floor(minutes / 1440)}d ago`;
}
function resetForecastLocation() {
  _forecastRequest += 1;
  forecastData = null; _forecastValidation = null; _forecastComparison = null;
  _ensembleData = null; _gradientData = null;
  _winnerModelId = ''; _biasWsMs = 0; _validationQueryId = '';
  document.getElementById('fcRunBtn').disabled = false;
  document.getElementById('fcStatus').textContent = '';
  document.getElementById('fcFreshness').classList.remove('is-stale');
  document.getElementById('fcBestPanel').style.display = 'none';
  document.getElementById('fcEvidence').open = false;
  document.getElementById('wxDetails').open = false;
  document.querySelectorAll('#fcVerificationPanel, #fcEnsembleRow, #fcIconEpsRow, #fcGradientPanel, #fcTempPrecipRow, #fcTableWrap').forEach(el => { el.style.display = 'none'; });
  document.getElementById('fcModelToggles').replaceChildren();
  document.getElementById('fcExtrasStatus').textContent = '';
  document.getElementById('fcGradientPanel').style.display = 'none';
  document.getElementById('fcIconEpsRow').style.display = 'none';
  renderNowStations(null); renderForecastChanges();
}
function renderPreparedForecast(data, validation = null, comparison = null) {
  validation = validation || (data.observation_points ? {observation_points: data.observation_points, stations_used: data.stations_used || []} : _forecastValidation);
  forecastData = data;
  if (selectedLocationRecord?.monitoring_enabled && data.hours_ahead) document.getElementById('fcHoursAhead').value = data.hours_ahead;
  _forecastValidation = validation; _forecastComparison = comparison;
  _winnerModelId = data.winner_model_id || validation?.winner_model_id || '';
  _validationQueryId = validation?.query_id || '';
  _biasWsMs = data.bias_ws_ms || 0;
  _ensembleData = null; _gradientData = null;
  _selectedModels = new Set((data.models || []).map(m => m.model_id));
  const cal = data.calibration || {};
  document.getElementById('fcStatus').textContent = cal.status === 'insufficient_history'
    ? 'Limited local history · raw forecast where correction is unavailable.' : `Correction: ${fcBiasSource(cal)} · model weights: ${cal.model_weight_window_hours || 48}h`;
  document.getElementById('fcExtrasStatus').textContent = '';
  document.getElementById('fcGradientPanel').style.display = 'none';
  document.getElementById('fcIconEpsRow').style.display = 'none';
  renderNowStations(validation); renderForecastChanges(); renderModelToggles(); renderAllCharts();
  if (document.getElementById('wxDetails').open && typeof renderWeatherTab === 'function') renderWeatherTab(true);
  resizeForecastCharts();
}
function renderNowStations(validation) {
  _forecastValidation = validation;
  const container = document.getElementById('fcNowStations');
  container.replaceChildren();
  const latest = new Map();
  for (const point of validation?.observation_points || []) {
    const old = latest.get(point.station_id);
    if (!old || new Date(point.time_utc) > new Date(old.time_utc)) latest.set(point.station_id, point);
  }
  const points = [...latest.values()].sort((a, b) => new Date(b.time_utc) - new Date(a.time_utc)).slice(0, 6);
  if (!points.length) { const p = document.createElement('p'); p.className = 'fc-empty'; p.textContent = 'No recent station observations available for this location.'; container.append(p); return; }
  points.forEach(point => {
    const station = (validation.stations_used || []).find(s => s.station_id === point.station_id);
    const item = document.createElement('article'); item.className = 'fc-now-station';
    const name = document.createElement('h3'); name.textContent = station?.name || point.station_id;
    const wind = document.createElement('p'); wind.className = 'fc-now-wind';
    wind.textContent = Number.isFinite(point.ws_ms) ? `${(point.ws_ms * MS_TO_KT).toFixed(1)} kt` : '—';
    const dir = document.createElement('span'); dir.textContent = Number.isFinite(point.wd_deg) ? `${Math.round(point.wd_deg)}°` : '—'; wind.append(dir);
    if (Number.isFinite(point.gust_ms)) {
      const gust = document.createElement('span'); gust.textContent = `gust ${(point.gust_ms * MS_TO_KT).toFixed(1)} kt`; wind.append(gust);
    }
    const age = document.createElement('p'); age.className = 'fc-now-age'; age.textContent = `${point.source || 'Station'} · ${fcAge(point.time_utc)}`;
    age.title = fcLocalTime(point.time_utc);
    if (Date.now() - new Date(point.time_utc).getTime() > 3 * 3600000) age.classList.add('is-stale');
    item.append(name, wind, age); container.append(item);
  });
  appendObservationCredits(container, [...latest.values()]);
}
function fcBiasSource(cal) {
  const sources = {recent_3h:'last 3h',fallback_6h:'6h fallback',fallback_24h:'24h fallback',fallback_48h:'48h fallback',historical:'historical fallback',raw:'no recent correction',mixed:'different windows per model'};
  return sources[cal.bias_source] || (cal.bias_window_hours ? `last ${cal.bias_window_hours}h` : 'awaiting evidence');
}
function renderBiasSummary() {
  const cal = forecastData?.calibration || {};
  const drift = cal.drift || {};
  let summary = forecastData ? `Bias: ${fcBiasSource(cal)}.` : 'Current correction is assessed separately from the analysis history.';
  if (cal.latest_observation_utc) summary += ` Latest observation ${fcAge(cal.latest_observation_utc)}.`;
  if (Number.isFinite(drift.delta_speed_ms)) {
    summary += ` Recent bias change: ${fcSigned(drift.delta_speed_ms * MS_TO_KT)} kt`;
    if (Number.isFinite(drift.delta_direction_deg)) summary += ` / ${fcSigned(drift.delta_direction_deg, 0)}°`;
    summary += ` across ${drift.station_count || 0} matched stations.`;
  } else if (forecastData) summary += ' Not enough matched observations to assess a bias change.';
  const bias = document.getElementById('fcBiasSummary'); bias.textContent = summary;
  bias.classList.toggle('is-stale', ['changing','changed','shifting','drift_detected'].includes(drift.status));
}
function renderForecastChanges() {
  renderBiasSummary();
  const container = document.getElementById('fcRunComparison'); container.replaceChildren();
  const comparison = _forecastComparison;
  const models = comparison?.models || [];
  if (!models.length) {
    const p = document.createElement('p'); p.className = 'fc-empty';
    p.textContent = selectedLocationRecord?.monitoring_enabled ? 'A comparison appears after two different forecasts have been collected.' : 'Follow a saved location to compare successive forecasts.';
    container.append(p); return;
  }
  const note = document.createElement('p'); note.className = 'fc-comparison-note';
  note.textContent = `Compared with the forecast saved ${fcLocalTime(comparison.previous_computed_at_utc)}. Raw wind at matching future hours.`; container.append(note);
  const table = document.createElement('table'); table.className = 'fc-comparison-table';
  table.innerHTML = '<thead><tr><th>Model</th><th>Mean wind change</th><th>Largest change</th><th>Direction</th><th>Hours</th></tr></thead>';
  const body = document.createElement('tbody');
  models.forEach(model => {
    const row = document.createElement('tr');
    const values = [model.model_id,fcSigned(Number.isFinite(model.mean_ws_change_ms) ? model.mean_ws_change_ms * MS_TO_KT : null) + ' kt',Number.isFinite(model.max_abs_ws_change_ms) ? (model.max_abs_ws_change_ms * MS_TO_KT).toFixed(1) + ' kt' : '—',fcSigned(model.mean_wd_change_deg, 0) + '°',model.overlap_hours];
    values.forEach(value => { const td = document.createElement('td'); td.textContent = value ?? '—'; row.append(td); }); body.append(row);
  });
  table.append(body); const wrap = document.createElement('div'); wrap.className = 'fc-comparison-scroll'; wrap.append(table); container.append(wrap);
  const detail = document.createElement('details'); detail.className = 'fc-comparison-detail';
  const title = document.createElement('summary'); title.textContent = 'Compare forecast curves'; detail.append(title);
  const chart = document.createElement('div'); chart.id = 'fcComparisonChart'; detail.append(chart); container.append(detail);
  detail.addEventListener('toggle', () => {
    if (!detail.open) return;
    const traces = [];
    models.forEach((model, idx) => {
      const hours = model.hours || []; const color = FC_COLORS[idx % FC_COLORS.length];
      traces.push({x:hours.map(h => h.time_utc),y:hours.map(h => h.previous_ws_ms == null ? null : h.previous_ws_ms * MS_TO_KT),name:`${model.model_id} previous`,mode:'lines',line:{color,width:1.5,dash:'dot'}});
      traces.push({x:hours.map(h => h.time_utc),y:hours.map(h => h.current_ws_ms == null ? null : h.current_ws_ms * MS_TO_KT),name:`${model.model_id} latest`,mode:'lines',line:{color,width:2}});
    });
    fcLocalPlot(chart, traces, {...LIGHT_LAYOUT,height:340,margin:{t:30,b:60,l:45,r:15},xaxis:LIGHT_XAXIS,yaxis:LIGHT_YAXIS('kt'),legend:{orientation:'h',y:-0.3}}, {responsive:true,displayModeBar:false});
  });
}
function resizeForecastCharts() {
  requestAnimationFrame(() => {
    document.querySelectorAll('.tab-panel.active .js-plotly-plot').forEach(el => { if (el.offsetWidth && el.offsetHeight) Plotly.Plots.resize(el); });
  });
}
document.getElementById('fcEvidence').addEventListener('toggle', e => { if (e.target.open && forecastData) { renderAllCharts(); resizeForecastCharts(); } });
document.getElementById('wxDetails').addEventListener('toggle', e => { if (e.target.open && forecastData && typeof renderWeatherTab === 'function') { renderWeatherTab(); resizeForecastCharts(); } });
document.getElementById('fcLoadExtras').addEventListener('click', async () => {
  if (!forecastData) return;
  const button = document.getElementById('fcLoadExtras'); button.disabled = true;
  const state = forecastData;
  document.getElementById('fcExtrasStatus').textContent = 'Loading extra detail…';
  try { await Promise.all([loadEnsemble(), loadGradientWind()]); }
  finally {
    button.disabled = false;
    if (state === forecastData) { document.getElementById('fcExtrasStatus').textContent = 'Detail checked.'; resizeForecastCharts(); }
  }
});
window.addEventListener('resize', () => {
  if (forecastData && _forecastIsMobile !== (window.innerWidth < 700)) renderBestForecastChart();
  resizeForecastCharts();
});
new ResizeObserver(resizeForecastCharts).observe(document.querySelector('.app-main'));


// Plotly treats ISO date coordinates as wall-clock values. Shift once at the
// rendering boundary; API timestamps, comparisons and range filtering stay UTC.
function fcPlotTime(iso) {
  if (iso == null) return null;
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return iso;
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
function fcPlotTraces(traces, layout) {
  return traces.map(original => {
    const trace = {...original, x: (original.x || []).map(fcPlotTime)};
    const axis = layout['yaxis' + ((original.yaxis || 'y').slice(1))] || {};
    const direction = axis.range?.[0] === 0 && axis.range?.[1] === 360;
    if (!direction || !original.mode?.includes('lines') || !original.y?.length) return trace;
    // Insert a gap without discarding either endpoint of a north crossing.
    const fields = ['x', 'y', 'text', 'customdata'].filter(key => Array.isArray(trace[key]));
    const arrays = Object.fromEntries(fields.map(key => [key, []]));
    original.y.forEach((value, i) => {
      if (i > 0 && value != null && original.y[i - 1] != null && Math.abs(value - original.y[i - 1]) > 180) {
        fields.forEach(key => arrays[key].push(key === 'x' ? trace.x[i] : null));
      }
      fields.forEach(key => arrays[key].push(trace[key][i]));
    });
    return {...trace, ...arrays, connectgaps: false};
  });
}
function fcLocalPlot(target, traces, layout, options) {
  return Plotly.newPlot(target, fcPlotTraces(traces, layout), layout, options);
}
