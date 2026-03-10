/* briefing.js — Weather Briefing tab
 * Reuses forecastData + helpers from forecast.js:
 *   MS_TO_KT, FC_COLORS, modelColor, computeEnsembleStats, windSpeedColor,
 *   LIGHT_LAYOUT, LIGHT_XAXIS, LIGHT_YAXIS, currentLatLon,
 *   _winnerModelId, _biasWsMs, _selectedModels, _correctedOnly
 */

// ── Time formatting (local time) ──────────────────────────────────────────────
const BF_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

function bfParseUtc(isoStr) {
  // Always treat server strings as UTC (append Z if missing)
  return new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
}

function bfFmt(isoStr) {
  const d = bfParseUtc(isoStr);
  return `${String(d.getDate()).padStart(2,'0')} ${BF_MONTHS[d.getMonth()]} ${String(d.getHours()).padStart(2,'0')}`;
}

// Shift a UTC ISO string to a local-time ISO string so Plotly shows local time
function bfLocalISO(isoStr) {
  const d = bfParseUtc(isoStr);
  // Build YYYY-MM-DDTHH:MM using local date parts
  const pad = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Range selects ─────────────────────────────────────────────────────────────
function bfInitRange() {
  const hours = forecastData?.models?.[0]?.hours ?? [];
  const sel = ['bfRangeStart', 'bfRangeEnd'].map(id => document.getElementById(id));
  sel.forEach(s => { s.innerHTML = ''; });
  hours.forEach((h, i) => {
    const label = bfFmt(h.time_utc);   // local time label
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
  const times   = fh.map(h => bfLocalISO(h.time_utc));
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

// ── Ensemble charts (TWS + TWD, share a row wrapper) ─────────────────────────
function renderBriefingEnsembleCharts() {
  const row = document.getElementById('bfEnsembleRow');
  if (!row || !forecastData || _correctedOnly) { if (row) row.style.display = 'none'; return; }

  const { winner_model_id, models } = forecastData;
  const selected = models.filter(m => _selectedModels.has(m.model_id));
  if (selected.length < 2) { row.style.display = 'none'; return; }
  row.style.display = '';

  const filteredSelected = selected.map(s => ({ ...s, hours: bfFilterHours(s.hours) }));

  // ── TWS ──
  const twsDiv = document.getElementById('bfEnsembleChart');
  if (twsDiv) {
    const traces = [];
    filteredSelected.forEach(series => {
      const color = modelColor(series.model_id);
      const times = series.hours.map(h => bfLocalISO(h.time_utc));
      const ws_kt = series.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
      traces.push({
        x: times, y: ws_kt, name: series.model_id,
        type: 'scatter', mode: 'lines',
        line: { color, width: series.model_id === winner_model_id ? 2 : 1.5 },
        opacity: 0.85,
      });
    });
    const stats = computeEnsembleStats(filteredSelected);
    const statTimes = stats.times.map(bfLocalISO);
    const upper = stats.means.map((m, i) => +(m + stats.stds[i]).toFixed(2));
    const lower = stats.means.map((m, i) => +(m - stats.stds[i]).toFixed(2));
    traces.push({ x: statTimes, y: upper, type: 'scatter', mode: 'lines', line: { width: 0 }, showlegend: false, hoverinfo: 'skip' });
    traces.push({ x: statTimes, y: lower, name: '±1σ', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(20,184,166,0.18)', line: { width: 0 }, hoverinfo: 'skip' });
    traces.push({ x: statTimes, y: stats.means, name: 'Ensemble mean', type: 'scatter', mode: 'lines', line: { color: '#000', width: 2, dash: 'dash' } });
    Plotly.newPlot(twsDiv, traces, {
      ...LIGHT_LAYOUT,
      height: 300,
      margin: { t: 40, b: 30, l: 50, r: 20 },
      legend: { orientation: 'h', x: 0, y: 1.15, font: { size: 10 } },
      xaxis: { ...LIGHT_XAXIS },
      yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
    }, { responsive: true, displayModeBar: false });
  }

  // ── TWD ──
  const twdDiv = document.getElementById('bfEnsembleDirChart');
  if (twdDiv) {
    const traces = [];
    filteredSelected.forEach(series => {
      const color = modelColor(series.model_id);
      const times = series.hours.map(h => bfLocalISO(h.time_utc));
      const wd    = series.hours.map(h => h.wd_deg != null ? +h.wd_deg.toFixed(0) : null);
      traces.push({
        x: times, y: wd, name: series.model_id,
        type: 'scatter', mode: 'lines',
        line: { color, width: series.model_id === winner_model_id ? 2 : 1.5 },
        opacity: 0.85, showlegend: false,
      });
    });
    Plotly.newPlot(twdDiv, traces, {
      ...LIGHT_LAYOUT,
      height: 300,
      margin: { t: 20, b: 30, l: 50, r: 20 },
      showlegend: false,
      xaxis: { ...LIGHT_XAXIS },
      yaxis: {
        title: 'TWD (°)', range: [0, 360], dtick: 90,
        gridcolor: '#e2e8f0', tickfont: { color: '#64748b' },
        tickvals: [0, 90, 180, 270, 360],
        ticktext: ['N (0°)', 'E (90°)', 'S (180°)', 'W (270°)', 'N (360°)'],
      },
    }, { responsive: true, displayModeBar: false });
  }
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

  let headerCols = '<th class="bfc-time">Time</th>'
    + '<th class="bfc-num" title="True wind speed (kt)">TWS</th>'
    + '<th class="bfc-num" title="Wind gust (kt)">Gust</th>'
    + '<th class="bfc-num" title="True wind direction (°)">TWD</th>'
    + '<th class="bfc-num" title="Temperature (°C)">Temp</th>';
  if (hasPrecip) headerCols += '<th class="bfc-rain" title="Precipitation (mm/h)">Rain</th>';
  headerCols += '<th class="bf-note-col">Notes</th>';

  const table = document.createElement('table');
  table.className = 'fc-table bf-wind-table';
  table.innerHTML = `<thead><tr>${headerCols}</tr></thead>`;

  const tbody = document.createElement('tbody');
  for (const hour of fh) {
    const raw_kt  = hour.ws_ms   != null ? (hour.ws_ms   * MS_TO_KT) : null;
    const tws_kt  = raw_kt != null ? (raw_kt - biasKt).toFixed(1) : null;
    const gust_kt = hour.gust_ms != null ? (hour.gust_ms * MS_TO_KT).toFixed(1) : null;
    const wd      = hour.wd_deg  != null ? `${Math.round(hour.wd_deg)}°` : '—';
    const temp    = hour.temp_c  != null ? hour.temp_c.toFixed(1) : '—';
    const precip  = hour.precip_mm != null ? hour.precip_mm.toFixed(2) : '—';

    const tr = document.createElement('tr');
    let cells = `<td class="bfc-time fc-time">${bfFmt(hour.time_utc)}</td>`;
    cells += `<td class="bfc-num fc-num" style="background:${tws_kt  != null ? windSpeedColor(+tws_kt)  : ''}">${tws_kt  ?? '—'}</td>`;
    cells += `<td class="bfc-num fc-num" style="background:${gust_kt != null ? windSpeedColor(+gust_kt) : ''}">${gust_kt ?? '—'}</td>`;
    cells += `<td class="bfc-num fc-num">${wd}</td><td class="bfc-num fc-num">${temp}</td>`;
    if (hasPrecip) cells += `<td class="bfc-rain fc-num">${precip}</td>`;
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
  renderBriefingEnsembleCharts();
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

// ── Print / PDF (convert Plotly charts to images before printing) ──────────────
document.getElementById('bfPrintBtn')?.addEventListener('click', async () => {
  const btn = document.getElementById('bfPrintBtn');
  btn.disabled = true;
  btn.textContent = 'Preparing…';

  const chartIds = ['bfBestChart', 'bfEnsembleChart', 'bfEnsembleDirChart'];
  const replacements = [];

  for (const id of chartIds) {
    const el = document.getElementById(id);
    if (!el || !el._fullLayout) continue;
    try {
      const imgUrl = await Plotly.toImage(el, {
        format: 'png',
        width:  el.offsetWidth  || 700,
        height: el.offsetHeight || 300,
      });
      const img = document.createElement('img');
      img.src = imgUrl;
      img.style.cssText = 'width:100%;display:block';
      el.parentNode.insertBefore(img, el);
      el.style.display = 'none';
      replacements.push({ el, img });
    } catch (e) {
      console.warn('Plotly.toImage failed for', id, e);
    }
  }

  window.print();

  for (const { el, img } of replacements) {
    el.style.display = '';
    img.remove();
  }
  btn.disabled = false;
  btn.textContent = 'Print / PDF';
});

// ── Save briefing as JSON ──────────────────────────────────────────────────────
document.getElementById('bfSaveBtn')?.addEventListener('click', () => {
  if (!forecastData) { alert('No forecast loaded.'); return; }

  const pos = currentLatLon();
  const payload = {
    _version: 1,
    lat: pos?.lat ?? null,
    lon: pos?.lon ?? null,
    winner_model_id: _winnerModelId,
    bias_ws_ms: _biasWsMs,
    hours_ahead: parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10),
    range_start: document.getElementById('bfRangeStart')?.value ?? '0',
    range_end:   document.getElementById('bfRangeEnd')?.value ?? '',
    title:    document.getElementById('bfTitle')?.value ?? '',
    subtitle: document.getElementById('bfSubtitle')?.value ?? '',
    notes:    document.getElementById('bfNotes')?.value ?? '',
    forecastData,
  };

  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  const ts   = new Date().toISOString().slice(0, 16).replace('T', '_').replace(':', '');
  a.href     = url;
  a.download = `briefing_${ts}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// ── Load briefing from JSON ────────────────────────────────────────────────────
document.getElementById('bfLoadBtn')?.addEventListener('click', () => {
  document.getElementById('bfFileInput')?.click();
});

document.getElementById('bfFileInput')?.addEventListener('change', e => {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      const payload = JSON.parse(ev.target.result);
      if (!payload.forecastData) throw new Error('Invalid briefing file');

      // Restore global forecast state (variables defined in forecast.js)
      forecastData      = payload.forecastData;
      _winnerModelId    = payload.winner_model_id ?? forecastData.winner_model_id;
      _biasWsMs         = payload.bias_ws_ms      ?? forecastData.bias_ws_ms;
      _selectedModels   = new Set(forecastData.models.map(m => m.model_id));

      // Restore coordinates
      if (payload.lat != null) document.getElementById('lat').value = payload.lat;
      if (payload.lon != null) document.getElementById('lon').value = payload.lon;
      if (payload.hours_ahead) {
        const el = document.getElementById('fcHoursAhead');
        if (el) el.value = payload.hours_ahead;
      }

      // Restore text fields
      if (payload.title    != null) document.getElementById('bfTitle').value    = payload.title;
      if (payload.subtitle != null) document.getElementById('bfSubtitle').value = payload.subtitle;
      if (payload.notes    != null) document.getElementById('bfNotes').value    = payload.notes;

      // Switch to briefing tab and render
      document.querySelector('.tab[data-tab="briefing"]')?.click();

      // Restore range selects after init (bfInitRange runs inside renderBriefingTab)
      setTimeout(() => {
        if (payload.range_start != null) document.getElementById('bfRangeStart').value = payload.range_start;
        if (payload.range_end   != null) document.getElementById('bfRangeEnd').value   = payload.range_end;
        bfRerender();
      }, 50);

    } catch (err) {
      alert('Failed to load briefing file: ' + err.message);
    }
  };
  reader.readAsText(file);
  // Reset so same file can be re-loaded
  e.target.value = '';
});
