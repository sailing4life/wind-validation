/* briefing.js — Weather Briefing tab
 * Reuses forecastData + helpers from forecast.js:
 *   MS_TO_KT, FC_COLORS, modelColor, computeEnsembleStats, windSpeedColor,
 *   LIGHT_LAYOUT, LIGHT_XAXIS, LIGHT_YAXIS, currentLatLon,
 *   _winnerModelId, _biasWsMs, _selectedModels, _correctedOnly
 */

// ── Time formatting ───────────────────────────────────────────────────────────
const BF_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function bfFmt(isoStr) {
  const t = new Date(isoStr);
  return `${String(t.getUTCDate()).padStart(2,'0')} ${BF_MONTHS[t.getUTCMonth()]} ${String(t.getUTCHours()).padStart(2,'0')}z`;
}

// ── Range selects ─────────────────────────────────────────────────────────────
function bfInitRange() {
  const hours = forecastData?.models?.[0]?.hours ?? [];
  const sel = ['bfRangeStart', 'bfRangeEnd'].map(id => document.getElementById(id));
  sel.forEach(s => { s.innerHTML = ''; });
  hours.forEach((h, i) => {
    const label = bfFmt(h.time_utc);
    sel.forEach(s => s.add(new Option(label, i)));
  });
  sel[0].value = '0';
  sel[1].value = String(hours.length - 1);
  sel.forEach(s => s.addEventListener('change', bfRerender));
}

function bfGetRangeTimes() {
  const hours = forecastData?.models?.[0]?.hours ?? [];
  const si = parseInt(document.getElementById('bfRangeStart').value, 10) || 0;
  const ei = parseInt(document.getElementById('bfRangeEnd').value, 10) || hours.length - 1;
  return {
    startTime: hours[si]?.time_utc ?? null,
    endTime:   hours[ei]?.time_utc ?? null,
  };
}

function bfFilterHours(hours) {
  const { startTime, endTime } = bfGetRangeTimes();
  if (!startTime || !endTime) return hours;
  return hours.filter(h => h.time_utc >= startTime && h.time_utc <= endTime);
}

// ── Every-point labels ────────────────────────────────────────────────────────
function allPointText(vals, fmt = v => String(v)) {
  return vals.map(v => v != null ? fmt(v) : '');
}

// ── Best model chart ──────────────────────────────────────────────────────────
function renderBriefingBestChart() {
  const panel   = document.getElementById('bfBestPanel');
  const chartDiv= document.getElementById('bfBestChart');
  if (!panel || !chartDiv || !forecastData) { if (panel) panel.style.display = 'none'; return; }

  const { winner_model_id, bias_ws_ms, models } = forecastData;
  const winner = models.find(m => m.model_id === winner_model_id) || models[0];
  if (!winner) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const titleEl = document.getElementById('bfBestTitle');
  if (titleEl) titleEl.textContent =
    winner_model_id + (bias_ws_ms ? ` · bias ${(bias_ws_ms * MS_TO_KT).toFixed(1)} kt` : '');

  const fh      = bfFilterHours(winner.hours);
  const biasKt  = bias_ws_ms * MS_TO_KT;
  const times   = fh.map(h => h.time_utc);
  const ws_kt   = fh.map(h => h.ws_ms   != null ? +(h.ws_ms   * MS_TO_KT).toFixed(1) : null);
  const corr_kt = ws_kt.map(v  => v != null ? +(v - biasKt).toFixed(1) : null);
  const gust_kt = fh.map(h => h.gust_ms != null ? +(h.gust_ms * MS_TO_KT).toFixed(1) : null);
  const wd      = fh.map(h => h.wd_deg);

  // Briefing always shows corrected TWS when a bias exists
  const mainWs = bias_ws_ms !== 0 ? corr_kt : ws_kt;

  const traces = [{
    x: times, y: mainWs, name: 'TWS (kt)',
    type: 'scatter', mode: 'lines+markers+text',
    line: { color: '#2563eb', width: 2 },
    marker: { color: '#2563eb', size: 5 },
    text: allPointText(mainWs),
    textposition: 'top center',
    textfont: { size: 9, color: '#1e3a8a' },
    yaxis: 'y1',
  }];

  if (gust_kt.some(v => v != null)) {
    traces.push({
      x: times, y: gust_kt, name: 'Gust (kt)',
      type: 'scatter', mode: 'lines+markers+text',
      line: { color: '#93c5fd', width: 1.5, dash: 'dash' },
      marker: { color: '#93c5fd', size: 4, symbol: 'x' },
      text: allPointText(gust_kt),
      textposition: 'top center',
      textfont: { size: 9, color: '#1e40af' },
      yaxis: 'y1',
    });
  }

  traces.push({
    x: times, y: wd, name: 'TWD (°)',
    type: 'scatter', mode: 'lines+markers+text',
    line: { color: '#dc2626', width: 1.5 },
    marker: { color: '#dc2626', size: 4 },
    text: allPointText(wd, v => String(Math.round(v))),
    textposition: 'top center',
    textfont: { size: 9, color: '#dc2626' },
    connectgaps: false,
    yaxis: 'y2',
  });

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 340,
    margin: { t: 60, b: 30, l: 50, r: 55 },
    legend: { orientation: 'h', x: 0, y: 1.18, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('kt'), zeroline: false },
    yaxis2: {
      title: '°', overlaying: 'y', side: 'right',
      range: [0, 360], dtick: 90,
      gridcolor: 'transparent',
      tickfont: { color: '#dc2626' },
      titlefont: { color: '#dc2626' },
    },
  }, { responsive: true, displayModeBar: false });
}

// ── Ensemble chart ────────────────────────────────────────────────────────────
function renderBriefingEnsembleChart() {
  const panel   = document.getElementById('bfEnsemblePanel');
  const chartDiv= document.getElementById('bfEnsembleChart');
  if (!panel || !chartDiv || !forecastData || _correctedOnly) {
    if (panel) panel.style.display = 'none'; return;
  }

  const { winner_model_id, models } = forecastData;
  const selected = models.filter(m => _selectedModels.has(m.model_id));
  if (selected.length < 2) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const traces = [];
  selected.forEach(series => {
    const color = modelColor(series.model_id);
    const fh    = bfFilterHours(series.hours);
    const times = fh.map(h => h.time_utc);
    const ws_kt = fh.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
    traces.push({
      x: times, y: ws_kt, name: series.model_id,
      type: 'scatter', mode: 'lines',
      line: { color, width: series.model_id === winner_model_id ? 2 : 1.5 },
      opacity: 0.85,
    });
  });

  // Ensemble mean ± 1σ — computed over the filtered time window
  const filteredSelected = selected.map(s => ({ ...s, hours: bfFilterHours(s.hours) }));
  const stats = computeEnsembleStats(filteredSelected);
  const upper = stats.means.map((m, i) => +(m + stats.stds[i]).toFixed(2));
  const lower = stats.means.map((m, i) => +(m - stats.stds[i]).toFixed(2));

  traces.push({ x: stats.times, y: upper, type: 'scatter', mode: 'lines', line: { width: 0 }, showlegend: false, hoverinfo: 'skip' });
  traces.push({ x: stats.times, y: lower, name: '±1σ',    type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(20,184,166,0.18)', line: { width: 0 }, hoverinfo: 'skip' });
  traces.push({ x: stats.times, y: stats.means, name: 'Ensemble mean', type: 'scatter', mode: 'lines', line: { color: '#000', width: 2, dash: 'dash' } });

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 340,
    margin: { t: 40, b: 30, l: 50, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.15, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
  }, { responsive: true, displayModeBar: false });
}

// ── Hourly wind table ─────────────────────────────────────────────────────────
function renderBriefingWindTable() {
  const wrap = document.getElementById('bfTableWrap');
  if (!wrap || !forecastData) return;

  const { winner_model_id, bias_ws_ms, models } = forecastData;
  const winner  = models.find(m => m.model_id === winner_model_id) || models[0];
  if (!winner) { wrap.innerHTML = ''; return; }

  const fh       = bfFilterHours(winner.hours);
  const biasKt   = bias_ws_ms * MS_TO_KT;
  const hasPrecip= winner.hours.some(h => h.precip_mm != null);

  let headerCols = '<th>Time UTC</th><th>TWS (kt)</th><th>Gust (kt)</th><th>TWD (°)</th><th>Temp (°C)</th>';
  if (hasPrecip) headerCols += '<th>Rain (mm)</th>';
  headerCols += '<th class="bf-note-col">Notes</th>';

  const table = document.createElement('table');
  table.className = 'fc-table bf-wind-table';
  table.innerHTML = `<thead><tr>${headerCols}</tr></thead>`;

  const tbody = document.createElement('tbody');
  for (const hour of fh) {
    const raw_kt  = hour.ws_ms   != null ? (hour.ws_ms   * MS_TO_KT) : null;
    const tws_kt  = raw_kt != null ? (raw_kt - biasKt).toFixed(1) : null;  // always corrected
    const gust_kt = hour.gust_ms != null ? (hour.gust_ms * MS_TO_KT).toFixed(1) : null;
    const wd      = hour.wd_deg  != null ? `${Math.round(hour.wd_deg)}°` : '—';
    const temp    = hour.temp_c  != null ? hour.temp_c.toFixed(1) : '—';
    const precip  = hour.precip_mm != null ? hour.precip_mm.toFixed(2) : '—';

    const tr = document.createElement('tr');
    let cells = `<td class="fc-time">${bfFmt(hour.time_utc)}</td>`;
    cells += `<td class="fc-num" style="background:${tws_kt  != null ? windSpeedColor(+tws_kt)  : ''}">${tws_kt  ?? '—'}</td>`;
    cells += `<td class="fc-num" style="background:${gust_kt != null ? windSpeedColor(+gust_kt) : ''}">${gust_kt ?? '—'}</td>`;
    cells += `<td class="fc-num">${wd}</td><td class="fc-num">${temp}</td>`;
    if (hasPrecip) cells += `<td class="fc-num">${precip}</td>`;
    cells += `<td class="bf-note-cell" contenteditable="true"></td>`;
    tr.innerHTML = cells;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'fc-table-scroll';
  scrollWrap.appendChild(table);

  wrap.innerHTML = '';
  const heading = document.createElement('div');
  heading.className = 'fc-chart-title';
  heading.textContent = `Hourly Forecast — ${winner.model_id}`;
  wrap.appendChild(heading);
  wrap.appendChild(scrollWrap);
}

// ── Re-render charts + table (called by range selects) ────────────────────────
function bfRerender() {
  renderBriefingBestChart();
  renderBriefingEnsembleChart();
  renderBriefingWindTable();
}

// ── Orchestrator ──────────────────────────────────────────────────────────────
function renderBriefingTab() {
  const meta = document.getElementById('bfMetaText');
  if (!forecastData) {
    if (meta) meta.textContent = 'Run Validation + load Forecast first.';
    return;
  }
  const pos = currentLatLon();
  const now = new Date().toUTCString().replace(' GMT', ' UTC');
  if (meta) meta.textContent = `${pos ? `${pos.lat.toFixed(4)}°N, ${pos.lon.toFixed(4)}°E` : ''} · ${now}`;

  bfInitRange();
  bfRerender();
}

// ── Tab click ─────────────────────────────────────────────────────────────────
document.querySelector('.tab[data-tab="briefing"]')
  ?.addEventListener('click', renderBriefingTab);

// ── Print ─────────────────────────────────────────────────────────────────────
document.getElementById('bfPrintBtn')
  ?.addEventListener('click', () => window.print());

// ── Share link ────────────────────────────────────────────────────────────────
document.getElementById('bfShareBtn')?.addEventListener('click', () => {
  const pos = currentLatLon();
  if (!pos) { alert('Set coordinates in Validation tab first.'); return; }

  const params = new URLSearchParams({
    lat:   pos.lat,
    lon:   pos.lon,
    model: _winnerModelId || '',
    bias:  (_biasWsMs || 0).toString(),
    hours: document.getElementById('fcHoursAhead')?.value || '48',
    tab:   'briefing',
  });

  const url = `${location.origin}${location.pathname}#${params.toString()}`;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(() => {
      const btn = document.getElementById('bfShareBtn');
      if (!btn) return;
      const orig = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = orig; }, 2000);
    });
  } else {
    prompt('Share this link:', url);
  }
});

// ── Share URL on load ─────────────────────────────────────────────────────────
(function handleShareUrl() {
  const hash = location.hash.slice(1);
  if (!hash) return;
  try {
    const p = new URLSearchParams(hash);
    if (p.get('lat'))   document.getElementById('lat').value = p.get('lat');
    if (p.get('lon'))   document.getElementById('lon').value = p.get('lon');
    if (p.get('hours')) { const el = document.getElementById('fcHoursAhead'); if (el) el.value = p.get('hours'); }
    if (p.get('model')) _winnerModelId = p.get('model');
    if (p.get('bias'))  _biasWsMs = parseFloat(p.get('bias'));
    if (p.get('tab') === 'briefing') {
      setTimeout(() => document.querySelector('.tab[data-tab="briefing"]')?.click(), 400);
    }
  } catch (e) {}
})();
