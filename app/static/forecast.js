/* forecast.js — Forecast tab: Windy iframe + Plotly charts + hourly table */

const MS_TO_KT = 1.94384;

// ── State ──────────────────────────────────────────────────────────────────────
let forecastData = null;
let _winnerModelId = '';
let _biasWsMs = 0;
let _selectedModels = new Set();
let _correctedOnly = false;
let _relayoutHandler = null;   // for range-slider sync

// ── Model color palette ────────────────────────────────────────────────────────
const FC_COLORS = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed', '#0891b2', '#be185d'];

function modelColor(modelId) {
  if (!forecastData) return '#94a3b8';
  const idx = forecastData.models.findIndex(m => m.model_id === modelId);
  return idx >= 0 ? FC_COLORS[idx % FC_COLORS.length] : '#94a3b8';
}

// ── Called by app.js after successful validation ───────────────────────────────
function setForecastParams(lat, lon, winnerModelId, biasWsMs) {
  _winnerModelId = winnerModelId || '';
  _biasWsMs = biasWsMs || 0;
  forecastData = null;
}

// ── Read current lat/lon from validation inputs ────────────────────────────────
function currentLatLon() {
  const lat = parseFloat(document.getElementById('lat').value);
  const lon = parseFloat(document.getElementById('lon').value);
  return (isNaN(lat) || isNaN(lon)) ? null : { lat, lon };
}

// ── Tab switching ──────────────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    if (btn.dataset.tab === 'forecast') {
      const pos = currentLatLon();
      if (pos) updateWindyMap(pos.lat, pos.lon);
      if (!forecastData) loadForecast();
    }
  });
});

document.getElementById('fcRunBtn').addEventListener('click', loadForecast);
document.getElementById('fcCorrectedOnly').addEventListener('change', e => {
  _correctedOnly = e.target.checked;
  if (forecastData) renderAllCharts();
});

// ── Windy map ──────────────────────────────────────────────────────────────────
function updateWindyMap(lat, lon) {
  const zoom = 7;
  document.getElementById('fcMap').src =
    `https://embed.windy.com/embed2.html?lat=${lat}&lon=${lon}&zoom=${zoom}` +
    `&level=surface&overlay=wind&menu=&message=&marker=true&calendar=&pressure=` +
    `&type=map&location=coordinates&detail=&detailLat=${lat}&detailLon=${lon}` +
    `&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1`;
}

// ── API call ───────────────────────────────────────────────────────────────────
async function loadForecast() {
  const pos = currentLatLon();
  const status = document.getElementById('fcStatus');

  if (!pos) {
    status.textContent = 'Set coordinates in the Validation tab first.';
    return;
  }

  const hoursAhead = parseInt(document.getElementById('fcHoursAhead').value, 10) || 48;
  status.textContent = 'Loading…';
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
        hours_ahead: hoursAhead,
      }),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${txt.slice(0, 200)}`);
    }
    forecastData = await resp.json();
    _selectedModels = new Set(forecastData.models.map(m => m.model_id));

    const biasKt = (_biasWsMs * MS_TO_KT).toFixed(1);
    status.textContent = _winnerModelId
      ? `Winner: ${_winnerModelId}  ·  bias ${biasKt} kt`
      : 'Run Validation first for bias correction';

    updateWindyMap(pos.lat, pos.lon);
    renderModelToggles();
    renderAllCharts();
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  } finally {
    document.getElementById('fcRunBtn').disabled = false;
  }
}

// ── Model toggle pills ─────────────────────────────────────────────────────────
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
    const winnerTag = isWinner ? ' ★' : '';
    btn.innerHTML = `<span class="mt-dot"></span>${series.model_id}${winnerTag}`;
    btn.title = isWinner ? 'Winner model from validation' : '';

    btn.addEventListener('click', () => {
      if (_selectedModels.has(series.model_id)) {
        if (_selectedModels.size > 1) _selectedModels.delete(series.model_id);
      } else {
        _selectedModels.add(series.model_id);
      }
      renderModelToggles();
      renderAllCharts();
    });
    container.appendChild(btn);
  });
}

// ── Shared chart config ────────────────────────────────────────────────────────
const LIGHT_LAYOUT = {
  paper_bgcolor: '#ffffff',
  plot_bgcolor: '#f8fafc',
  font: { color: '#1e293b', size: 11 },
};

const LIGHT_XAXIS = { gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, type: 'date' };
const LIGHT_YAXIS = (title) => ({ title, gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, rangemode: 'tozero' });

// ── Ensemble stats ─────────────────────────────────────────────────────────────
function computeEnsembleStats(selectedSeries) {
  const timeMap = new Map(); // ISO string → number[]
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

// ── Value labels every 3h ─────────────────────────────────────────────────────
function every3hText(times, vals, fmt = v => String(v)) {
  return vals.map((v, i) => {
    const t = new Date(times[i]);
    return (t.getUTCHours() % 3 === 0 && v != null) ? fmt(v) : '';
  });
}

// ── Wind speed cell color ──────────────────────────────────────────────────────
function windSpeedColor(kt) {
  const t = Math.min(1, Math.max(0, (kt - 5) / 20));
  const hue = Math.round(220 - t * 220);
  return `hsl(${hue}, 75%, 82%)`;
}

// ── Range sync: best-forecast slider → all other charts ───────────────────────
function syncChartRanges(range) {
  for (const id of ['fcEnsembleChart', 'fcTempChart', 'fcPrecipChart']) {
    const el = document.getElementById(id);
    if (el && el._fullLayout) Plotly.relayout(el, { 'xaxis.range': range });
  }
}

// ── Chart 1: Best Forecast (winner model, expedition style) ───────────────────
function renderBestForecastChart() {
  const panel = document.getElementById('fcBestPanel');
  const chartDiv = document.getElementById('fcBestChart');
  if (!panel || !chartDiv) return;
  const { winner_model_id, bias_ws_ms, models } = forecastData;
  const winner = models.find(m => m.model_id === winner_model_id) || models[0];
  if (!winner) { panel.style.display = 'none'; return; }

  panel.style.display = '';
  document.getElementById('fcBestTitle').textContent =
    winner_model_id + (bias_ws_ms ? ` · bias ${(bias_ws_ms * MS_TO_KT).toFixed(1)} kt` : '');

  const times = winner.hours.map(h => h.time_utc);
  const ws_kt = winner.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
  const biasKt = bias_ws_ms * MS_TO_KT;
  const corr_kt = ws_kt.map(v => v != null ? +(v - biasKt).toFixed(1) : null);
  const gust_kt = winner.hours.map(h => h.gust_ms != null ? +(h.gust_ms * MS_TO_KT).toFixed(1) : null);
  const wd = winner.hours.map(h => h.wd_deg);

  const mainWs = _correctedOnly ? corr_kt : ws_kt;
  const mainLabel = _correctedOnly ? 'Corrected TWS (kt)' : 'TWS (kt)';

  const traces = [];

  // TWS (or corrected) — blue solid line+markers+labels
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

  // Corrected overlay when not in corrected-only mode
  if (!_correctedOnly && bias_ws_ms !== 0) {
    traces.push({
      x: times, y: corr_kt,
      name: 'Corrected TWS (kt)',
      type: 'scatter', mode: 'lines+markers+text',
      line: { color: '#f59e0b', width: 2.5 },
      marker: { color: '#f59e0b', size: 6 },
      text: every3hText(times, corr_kt),
      textposition: 'top center',
      textfont: { size: 10, color: '#92400e' },
      yaxis: 'y1',
    });
  }

  // Gust — light blue dashed+X+labels
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

  // TWD — red line+markers+labels, right axis
  traces.push({
    x: times, y: wd,
    name: 'TWD (°)',
    type: 'scatter', mode: 'lines+markers+text',
    line: { color: '#dc2626', width: 1.5 },
    marker: { color: '#dc2626', size: 5 },
    text: every3hText(times, wd, v => String(Math.round(v))),
    textposition: 'top center',
    textfont: { size: 9, color: '#dc2626' },
    connectgaps: false,
    yaxis: 'y2',
  });

  const layout = {
    ...LIGHT_LAYOUT,
    height: 480,
    margin: { t: 70, b: 30, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.18, font: { size: 11 } },
    xaxis: {
      ...LIGHT_XAXIS,
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
      title: '°', overlaying: 'y', side: 'right',
      range: [0, 360], dtick: 90,
      gridcolor: 'transparent',
      tickfont: { color: '#dc2626' },
      titlefont: { color: '#dc2626' },
    },
  };

  Plotly.newPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });

  // Re-attach range-sync listener (replaces previous one on re-render)
  if (_relayoutHandler) chartDiv.removeListener('plotly_relayout', _relayoutHandler);
  _relayoutHandler = (ev) => {
    if (ev['xaxis.range[0]'] != null) {
      syncChartRanges([ev['xaxis.range[0]'], ev['xaxis.range[1]']]);
    } else if (ev['xaxis.autorange']) {
      for (const id of ['fcEnsembleChart', 'fcTempChart', 'fcPrecipChart']) {
        const el = document.getElementById(id);
        if (el && el._fullLayout) Plotly.relayout(el, { 'xaxis.autorange': true });
      }
    }
  };
  chartDiv.on('plotly_relayout', _relayoutHandler);
}

// ── Chart 2: Ensemble (all selected models + mean ± 1σ) ──────────────────────
function renderEnsembleChart() {
  const panel = document.getElementById('fcEnsemblePanel');
  const chartDiv = document.getElementById('fcEnsembleChart');
  if (!panel || !chartDiv) return;
  if (_correctedOnly) { panel.style.display = 'none'; return; }

  const { winner_model_id, models } = forecastData;
  const selected = models.filter(m => _selectedModels.has(m.model_id));
  if (selected.length === 0) { panel.style.display = 'none'; return; }
  panel.style.display = '';

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

  // Ensemble mean + ±1σ band
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
      name: '±1σ',
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

  Plotly.newPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });
}

// ── Chart 3: Temperature ───────────────────────────────────────────────────────
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
    yaxis: { title: 'Temp (°C)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' } },
  };

  Plotly.newPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });
}

// ── Chart 4: Precipitation ────────────────────────────────────────────────────
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

  Plotly.newPlot(chartDiv, [{
    x: times, y: precip,
    name: 'Precipitation',
    type: 'bar',
    marker: { color: '#60a5fa' },
  }], layout, { responsive: true, displayModeBar: false });
}

// ── Render all charts ─────────────────────────────────────────────────────────
function renderAllCharts() {
  if (!forecastData) return;
  renderBestForecastChart();
  renderEnsembleChart();
  renderTempChart();
  renderPrecipChart();
  renderForecastTable();
}

// ── Hourly forecast table ──────────────────────────────────────────────────────
function renderForecastTable() {
  const wrap = document.getElementById('fcTableWrap');
  if (!wrap) return;

  const { winner_model_id, bias_ws_ms, models } = forecastData;
  if (!models || models.length === 0) { wrap.style.display = 'none'; return; }

  const winner = models.find(m => m.model_id === winner_model_id) || models[0];
  const biasKt = bias_ws_ms * MS_TO_KT;
  wrap.innerHTML = '';
  wrap.style.display = '';

  const heading = document.createElement('div');
  heading.className = 'fc-chart-title';
  heading.textContent = `Hourly forecast — ${winner.model_id}`;
  wrap.appendChild(heading);

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'fc-table-scroll';

  const hasPrecip = winner.hours.some(h => h.precip_mm != null);
  const hasCorr = bias_ws_ms !== 0;

  let colHtml = '<th>Time UTC</th><th>TWS (kt)</th>';
  if (hasCorr) colHtml += '<th>Corr (kt)</th>';
  colHtml += '<th>Gust (kt)</th><th>TWD (°)</th><th>Temp (°C)</th>';
  if (hasPrecip) colHtml += '<th>Rain</th>';
  colHtml += '<th class="note-col">Notes</th>';

  const table = document.createElement('table');
  table.className = 'fc-table';
  table.innerHTML = `<thead><tr>${colHtml}</tr></thead>`;

  const tbody = document.createElement('tbody');
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  for (const hour of winner.hours) {
    const ws_kt = hour.ws_ms != null ? (hour.ws_ms * MS_TO_KT).toFixed(1) : null;
    const corr_kt = ws_kt != null ? (parseFloat(ws_kt) - biasKt).toFixed(1) : null;
    const gust_kt = hour.gust_ms != null ? (hour.gust_ms * MS_TO_KT).toFixed(1) : null;
    const wd = hour.wd_deg != null ? Math.round(hour.wd_deg) + '°' : '—';
    const temp = hour.temp_c != null ? hour.temp_c.toFixed(1) : '—';
    const precip = hour.precip_mm != null ? hour.precip_mm.toFixed(2) : '—';

    const t = new Date(hour.time_utc);
    const label = `${String(t.getUTCDate()).padStart(2,'0')} ${MONTHS[t.getUTCMonth()]} ${String(t.getUTCHours()).padStart(2,'0')}z`;
    const wsColor = ws_kt != null ? windSpeedColor(+ws_kt) : '';
    const gustColor = gust_kt != null ? windSpeedColor(+gust_kt) : '';
    const corrColor = corr_kt != null ? windSpeedColor(+corr_kt) : '';

    const tr = document.createElement('tr');
    let cells = `<td class="fc-time">${label}</td>`;
    cells += `<td class="fc-num" style="background:${wsColor}">${ws_kt ?? '—'}</td>`;
    if (hasCorr) cells += `<td class="fc-num" style="background:${corrColor}">${corr_kt ?? '—'}</td>`;
    cells += `<td class="fc-num" style="background:${gustColor}">${gust_kt ?? '—'}</td>`;
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
