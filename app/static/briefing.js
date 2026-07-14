/* briefing.js  -  Weather Briefing tab
 * Reuses forecastData + helpers from forecast.js:
 *   MS_TO_KT, FC_COLORS, modelColor, computeEnsembleStats, windSpeedColor,
 *   LIGHT_LAYOUT, LIGHT_XAXIS, LIGHT_YAXIS, currentLatLon,
 *   _winnerModelId, _biasWsMs, _selectedModels, _correctedOnly
 */

// â”€â”€ Time formatting (local time) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Range selects â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Every-point labels â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function allPointText(vals, fmt = v => String(v)) {
  return vals.map(v => v != null ? fmt(v) : '');
}

// ── Model override helpers ───────────────────────────────────────────────────────
function bfGetActiveModel() {
  const sel = document.getElementById('bfModelOverride');
  return (sel && sel.value) ? sel.value : (_winnerModelId || null);
}

function bfGetActiveBias() {
  const overrideId = document.getElementById('bfModelOverride')?.value;
  if (overrideId && overrideId !== _winnerModelId) return 0;
  return _biasWsMs || 0;
}

function bfPopulateModelOverride() {
  const sel = document.getElementById('bfModelOverride');
  if (!sel || !forecastData) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">Auto (best)</option>';
  (forecastData.models || []).forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.model_id;
    opt.textContent = m.model_id + (m.model_id === _winnerModelId ? ' ★' : '');
    sel.appendChild(opt);
  });
  // Re-add any uploaded GRIB options (not part of forecastData.models)
  _bfUploadedGribs.forEach(({ model_id, filename }) => {
    const opt = document.createElement('option');
    opt.value = model_id;
    opt.textContent = `GRIB: ${filename}`;
    sel.appendChild(opt);
  });
  if (current) sel.value = current;

  // Populate model checkboxes (preserve existing checked state)
  const checksDiv = document.getElementById('bfModelChecks');
  if (!checksDiv) return;
  const existing = new Set(
    [...checksDiv.querySelectorAll('input[type=checkbox]')]
      .filter(cb => !cb.checked)
      .map(cb => cb.value)
  );
  checksDiv.innerHTML = '';
  (forecastData.models || []).forEach(m => {
    const label = document.createElement('label');
    label.className = 'bf-model-check-item';
    label.title = m.model_id;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = m.model_id;
    cb.checked = !existing.has(m.model_id);  // default: all checked
    cb.addEventListener('change', bfRerender);
    const dot = document.createElement('span');
    dot.className = 'bf-model-dot';
    dot.style.background = modelColor(m.model_id);
    const name = document.createElement('span');
    name.textContent = m.model_id;
    label.append(cb, dot, name);
    checksDiv.appendChild(label);
  });
}

function bfGetCheckedModels() {
  const checksDiv = document.getElementById('bfModelChecks');
  if (!checksDiv) return null;
  const boxes = [...checksDiv.querySelectorAll('input[type=checkbox]')];
  if (!boxes.length) return null;
  const checked = boxes.filter(cb => cb.checked).map(cb => cb.value);
  return checked.length ? new Set(checked) : null;
}


// â”€â”€ Best model chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderBriefingBestChart() {
  const panel   = document.getElementById('bfBestPanel');
  const chartDiv= document.getElementById('bfBestChart');
  if (!panel || !chartDiv || !forecastData) { if (panel) panel.style.display = 'none'; return; }

  const { models } = forecastData;
  const activeModelId = bfGetActiveModel();
  const activeBias    = bfGetActiveBias();
  const winner = models.find(m => m.model_id === activeModelId) || models[0];
  if (!winner) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const titleEl = document.getElementById('bfBestTitle');
  if (titleEl) titleEl.textContent =
    winner.model_id + (activeBias ? `  -  bias ${(activeBias * MS_TO_KT).toFixed(1)} kt` : '');

  const fh      = bfFilterHours(winner.hours);
  const biasKt  = activeBias * MS_TO_KT;
  const times   = fh.map(h => bfLocalISO(h.time_utc));
  const ws_kt   = fh.map(h => h.ws_ms   != null ? +(h.ws_ms   * MS_TO_KT).toFixed(1) : null);
  const corr_kt = ws_kt.map(v  => v != null ? +(v - biasKt).toFixed(1) : null);
  const gust_kt = fh.map(h => h.gust_ms != null ? +(h.gust_ms * MS_TO_KT).toFixed(1) : null);
  const wd      = fh.map(h => h.wd_deg);

  // Briefing always shows corrected TWS when a bias exists
  const mainWs = activeBias !== 0 ? corr_kt : ws_kt;

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
    x: times, y: wd, name: 'TWD ( deg)',
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
    height: 370,
    margin: { t: 50, b: 50, l: 55, r: 65 },
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

// â”€â”€ Ensemble charts (TWS + TWD, share a row wrapper) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderBriefingEnsembleCharts() {
  const row = document.getElementById('bfEnsembleRow');
  if (!row || !forecastData) { if (row) row.style.display = 'none'; return; }

  // Hide if the ensemble checkbox is not checked
  if (!document.getElementById('bfIncludeEnsemble')?.checked) {
    row.style.display = 'none';
    return;
  }

  const { winner_model_id, models } = forecastData;
  const checkedModels = bfGetCheckedModels();
  const selected = models.filter(m =>
    Array.isArray(m.hours) && m.hours.length > 0 &&
    (!checkedModels || checkedModels.has(m.model_id))
  );
  if (selected.length < 2) { row.style.display = 'none'; return; }
  row.style.display = '';

  const filteredSelected = selected.map(s => ({ ...s, hours: bfFilterHours(s.hours) }));

  // â”€â”€ TWS â”€â”€
  const twsDiv = document.getElementById('bfEnsembleChart');
  if (twsDiv) {
    const traces = [];
    filteredSelected.forEach(series => {
      const color = modelColor(series.model_id);
      const times = series.hours.map(h => bfLocalISO(h.time_utc));
      const ws_kt = series.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
      const isWinner = series.model_id === winner_model_id;
      traces.push({
        x: times, y: ws_kt, name: series.model_id,
        type: 'scatter', mode: 'lines+markers',
        line: { color, width: isWinner ? 2 : 1.5 },
        marker: { color, size: isWinner ? 5 : 4 },
        opacity: 0.85,
      });
    });
    const stats = computeEnsembleStats(filteredSelected);
    const statTimes = stats.times.map(bfLocalISO);
    const upper = stats.means.map((m, i) => +(m + stats.stds[i]).toFixed(2));
    const lower = stats.means.map((m, i) => +(m - stats.stds[i]).toFixed(2));
    traces.push({ x: statTimes, y: upper, type: 'scatter', mode: 'lines', line: { width: 0 }, showlegend: false, hoverinfo: 'skip' });
    traces.push({ x: statTimes, y: lower, name: '+/-1 sigma', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(20,184,166,0.18)', line: { width: 0 }, hoverinfo: 'skip' });
    traces.push({ x: statTimes, y: stats.means, name: 'Ensemble mean', type: 'scatter', mode: 'lines', line: { color: '#000', width: 2, dash: 'dash' } });
    Plotly.newPlot(twsDiv, traces, {
      ...LIGHT_LAYOUT,
      height: 370,
      margin: { t: 50, b: 50, l: 55, r: 20 },
      legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
      xaxis: { ...LIGHT_XAXIS },
      yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
    }, { responsive: true, displayModeBar: false });
  }

  // â”€â”€ TWD â”€â”€
  const twdDiv = document.getElementById('bfEnsembleDirChart');
  if (twdDiv) {
    const traces = [];
    const allWd = [];
    filteredSelected.forEach(series => {
      const color = modelColor(series.model_id);
      const times = series.hours.map(h => bfLocalISO(h.time_utc));
      const wd    = series.hours.map(h => h.wd_deg != null ? +h.wd_deg.toFixed(0) : null);
      wd.forEach(v => { if (v != null) allWd.push(v); });
      const isWinner = series.model_id === winner_model_id;
      traces.push({
        x: times, y: wd, name: series.model_id,
        type: 'scatter', mode: 'lines+markers',
        line: { color, width: isWinner ? 2 : 1.5 },
        marker: { color, size: isWinner ? 5 : 4 },
        opacity: 0.85, showlegend: false,
      });
    });

    // Dynamic y-axis range and tick interval based on data spread
    const wdMin = allWd.length ? Math.min(...allWd) : 0;
    const wdMax = allWd.length ? Math.max(...allWd) : 360;
    const spread = wdMax - wdMin;
    const pad = Math.max(spread * 0.1, 5);
    const yMin = Math.max(0, Math.floor((wdMin - pad) / 5) * 5);
    const yMax = Math.min(360, Math.ceil((wdMax + pad) / 5) * 5);
    const yRange = yMax - yMin;
    let dtick;
    if (yRange <= 30)       dtick = 5;
    else if (yRange <= 60)  dtick = 10;
    else if (yRange <= 120) dtick = 20;
    else if (yRange <= 180) dtick = 30;
    else if (yRange <= 270) dtick = 45;
    else                    dtick = 90;

    Plotly.newPlot(twdDiv, traces, {
      ...LIGHT_LAYOUT,
      height: 370,
      margin: { t: 20, b: 50, l: 70, r: 20 },
      showlegend: false,
      xaxis: { ...LIGHT_XAXIS },
      yaxis: {
        title: { text: 'TWD (deg)', standoff: 16 },
        automargin: true,
        range: [yMin, yMax], dtick,
        gridcolor: '#e2e8f0', tickfont: { color: '#64748b' },
      },
    }, { responsive: true, displayModeBar: false });
  }
}

// â”€â”€ Hourly wind table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderBriefingWindTable() {
  const wrap = document.getElementById('bfTableWrap');
  if (!wrap || !forecastData) return;

  const { models } = forecastData;
  const activeModelId = bfGetActiveModel();
  const activeBias    = bfGetActiveBias();
  const winner  = models.find(m => m.model_id === activeModelId) || models[0];
  if (!winner) { wrap.innerHTML = ''; return; }

  const fh       = bfFilterHours(winner.hours);
  const biasKt   = activeBias * MS_TO_KT;
  const hasPrecip= winner.hours.some(h => h.precip_mm != null);

  let headerCols = '<th class="bfc-time">Time</th>'
    + '<th class="bfc-num" title="True wind speed (kt)">TWS</th>'
    + '<th class="bfc-num" title="Wind gust (kt)">Gust</th>'
    + '<th class="bfc-num" title="True wind direction ( deg)">TWD</th>'
    + '<th class="bfc-num" title="Temperature ( degC)">Temp</th>';
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
    const wd      = hour.wd_deg  != null ? `${Math.round(hour.wd_deg)} deg` : ' - ';
    const temp    = hour.temp_c  != null ? hour.temp_c.toFixed(1) : ' - ';
    const precip  = hour.precip_mm != null ? hour.precip_mm.toFixed(2) : ' - ';

    const tr = document.createElement('tr');
    let cells = `<td class="bfc-time fc-time">${bfFmt(hour.time_utc)}</td>`;
    cells += `<td class="bfc-num fc-num" style="background:${tws_kt  != null ? windSpeedColor(+tws_kt)  : ''}">${tws_kt  ?? ' - '}</td>`;
    cells += `<td class="bfc-num fc-num" style="background:${gust_kt != null ? windSpeedColor(+gust_kt) : ''}">${gust_kt ?? ' - '}</td>`;
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
  heading.textContent = `Hourly Forecast  -  ${winner.model_id}`;
  wrap.appendChild(heading);
  wrap.appendChild(scrollWrap);
}

// â”€â”€ Ocean current chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let _bfCurrentData = null;   // cached fetch result { lat, lon, hours }

async function bfFetchAndRenderCurrent() {
  const panel    = document.getElementById('bfCurrentPanel');
  const chartDiv = document.getElementById('bfCurrentChart');
  const metaEl   = document.getElementById('bfCurrentMeta');
  if (!panel || !chartDiv) return;

  if (!document.getElementById('bfIncludeCurrent')?.checked) {
    panel.style.display = 'none';
    return;
  }

  const pos = currentLatLon();
  if (!pos) { panel.style.display = 'none'; return; }

  const hours = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);

  // Re-use cached data if location/hours unchanged
  if (
    _bfCurrentData &&
    _bfCurrentData.lat === pos.lat &&
    _bfCurrentData.lon === pos.lon &&
    _bfCurrentData.hours === hours
  ) {
    _bfRenderCurrentChart(_bfCurrentData.payload);
    return;
  }

  if (metaEl) metaEl.textContent = '— loading…';
  panel.style.display = '';

  try {
    const url = `/api/ocean-current?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const payload = await resp.json();
    _bfCurrentData = { lat: pos.lat, lon: pos.lon, hours, payload };
    _bfRenderCurrentChart(payload);
    if (metaEl) metaEl.textContent = `— ${pos.lat.toFixed(3)}N ${pos.lon.toFixed(3)}E`;
  } catch (err) {
    if (metaEl) metaEl.textContent = `— unavailable (${err.message})`;
    panel.style.display = 'none';
  }
}

function _bfRenderCurrentChart(payload) {
  const panel   = document.getElementById('bfCurrentPanel');
  const chartDiv= document.getElementById('bfCurrentChart');
  if (!panel || !chartDiv) return;

  const { startTime, endTime } = bfGetRangeTimes();
  const allHours = payload.hours ?? [];
  const fh = allHours.filter(h =>
    (!startTime || h.time_utc >= startTime) &&
    (!endTime   || h.time_utc <= endTime)
  );

  if (!fh.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const times  = fh.map(h => bfLocalISO(h.time_utc));
  const speeds = fh.map(h => h.speed_kt);
  const dirs   = fh.map(h => h.direction_deg);

  // Dynamic y-axis for direction
  const validDirs = dirs.filter(d => d != null);
  const dMin = validDirs.length ? Math.min(...validDirs) : 0;
  const dMax = validDirs.length ? Math.max(...validDirs) : 360;
  const dPad = Math.max((dMax - dMin) * 0.1, 5);
  const dirRange = [Math.max(0, Math.floor(dMin - dPad)), Math.min(360, Math.ceil(dMax + dPad))];
  const dirSpread = dirRange[1] - dirRange[0];
  const dirDtick = dirSpread <= 30 ? 5 : dirSpread <= 60 ? 10 : dirSpread <= 120 ? 20 : dirSpread <= 180 ? 30 : 45;

  const traces = [
    {
      x: times, y: speeds, name: 'Speed (kt)',
      type: 'scatter', mode: 'lines+markers+text',
      line: { color: '#0891b2', width: 2 },
      marker: { color: '#0891b2', size: 5 },
      text: allPointText(speeds, v => v.toFixed(1)),
      textposition: 'top center',
      textfont: { size: 8, color: '#0e7490' },
      yaxis: 'y1',
    },
    {
      x: times, y: dirs, name: 'Direction (°)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#7c3aed', width: 1.5 },
      marker: { color: '#7c3aed', size: 4 },
      yaxis: 'y2',
    },
  ];

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 300,
    margin: { t: 50, b: 50, l: 55, r: 60 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('kt'), zeroline: false, rangemode: 'tozero' },
    yaxis2: {
      title: '°', overlaying: 'y', side: 'right',
      range: dirRange, dtick: dirDtick,
      gridcolor: 'transparent',
      tickfont: { color: '#7c3aed' },
      titlefont: { color: '#7c3aed' },
    },
  }, { responsive: true, displayModeBar: false });
}

// ── Briefing extras: waves + cloud/CAPE (cached per location/hours) ─────────────
let _bfExtras = null;   // { lat, lon, hours, data }

async function bfFetchExtras() {
  const pos = currentLatLon();
  if (!pos) return null;
  const hours = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);
  if (_bfExtras && _bfExtras.lat === pos.lat && _bfExtras.lon === pos.lon && _bfExtras.hours === hours) {
    return _bfExtras.data;
  }
  try {
    const resp = await fetch(`/api/briefing-extras?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}`);
    if (!resp.ok) return null;
    const data = await resp.json();
    _bfExtras = { lat: pos.lat, lon: pos.lon, hours, data };
    return data;
  } catch {
    return null;
  }
}

// ── 1 · Met Office pressure charts ──────────────────────────────────────────────
let _bfPressure = null;   // [{ step_h, url, valid_utc }]

async function bfFetchPressureCharts() {
  if (_bfPressure) return _bfPressure;
  try {
    const resp = await fetch('/api/pressure-charts');
    if (!resp.ok) return null;
    _bfPressure = (await resp.json()).charts;
    return _bfPressure;
  } catch {
    return null;
  }
}

function bfVisiblePressureCharts() {
  if (!_bfPressure?.length) return [];
  // Analysis + T+12 give the synoptic picture; more steps just add noise
  return _bfPressure.filter(c => c.step_h <= 12);
}

function bfRenderPressureCharts() {
  const grid = document.getElementById('bfPressureGrid');
  const meta = document.getElementById('bfPressureMeta');
  if (!grid) return;
  const show = bfVisiblePressureCharts();
  if (!show.length) {
    grid.innerHTML = '';
    if (meta) meta.textContent = '— Met Office charts unavailable';
    return;
  }
  grid.innerHTML = '';
  show.forEach(c => {
    const fig = document.createElement('figure');
    const img = document.createElement('img');
    img.src = c.url;
    img.alt = `Surface pressure T+${c.step_h}h`;
    img.loading = 'lazy';
    const cap = document.createElement('figcaption');
    cap.textContent = `T+${c.step_h}h — ${bfFmt(c.valid_utc)}:00`;
    fig.append(img, cap);
    grid.appendChild(fig);
  });
  if (meta) meta.textContent = '— Met Office surface pressure';
}

// ── 2 · Gradient wind (925 hPa) ─────────────────────────────────────────────────
let _bfGradient = null;   // { lat, lon, hours, data }

async function bfFetchGradient() {
  const pos = currentLatLon();
  if (!pos) return null;
  const hours = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);
  if (_bfGradient && _bfGradient.lat === pos.lat && _bfGradient.lon === pos.lon && _bfGradient.hours === hours) {
    return _bfGradient.data;
  }
  try {
    const resp = await fetch(`/api/gradient-wind?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}`);
    if (!resp.ok) return null;
    const data = await resp.json();
    _bfGradient = { lat: pos.lat, lon: pos.lon, hours, data };
    return data;
  } catch {
    return null;
  }
}

function bfRenderGradientChart() {
  const chartDiv = document.getElementById('bfGradientChart');
  const meta = document.getElementById('bfGradientMeta');
  if (!chartDiv) return;
  const data = _bfGradient?.data;
  if (!data?.times?.length) {
    chartDiv.innerHTML = '';
    if (meta) meta.textContent = '— unavailable';
    return;
  }
  const idxs = bfInRangeIdx(data.times);
  const t = idxs.map(i => bfLocalISO(data.times[i]));

  const traces = [
    { x: t, y: idxs.map(i => data.ws925_kt[i]), name: '925 hPa TWS (kt)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#7c3aed', width: 2 },
      marker: { color: '#7c3aed', size: 4 },
      yaxis: 'y1' },
    { x: t, y: idxs.map(i => data.ws10_kt[i]), name: '10 m TWS (kt)',
      type: 'scatter', mode: 'lines',
      line: { color: '#94a3b8', width: 1.5, dash: 'dash' },
      yaxis: 'y1' },
    { x: t, y: idxs.map(i => data.wd925_deg[i]), name: '925 hPa TWD (deg)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#dc2626', width: 1.5 },
      marker: { color: '#dc2626', size: 4 },
      connectgaps: false,
      yaxis: 'y2' },
    { x: t, y: idxs.map(i => data.wd10_deg[i]), name: '10 m TWD (deg)',
      type: 'scatter', mode: 'lines',
      line: { color: '#f87171', width: 1.5, dash: 'dash' },
      connectgaps: false,
      yaxis: 'y2' },
  ];

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 300,
    margin: { t: 40, b: 40, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.14, font: { size: 10 } },
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
  if (meta) meta.textContent = `— ${data.model}`;
}

// ── 4 · Local effects: cloud cover + CAPE ───────────────────────────────────────
function bfRenderSkyChart() {
  const section = document.getElementById('bfSkySection');
  const chartDiv = document.getElementById('bfSkyChart');
  if (!section || !chartDiv) return;
  const sky = _bfExtras?.data?.sky;
  if (!sky?.times?.length) { section.style.display = 'none'; return; }
  const idxs = bfInRangeIdx(sky.times);
  if (!idxs.length) { section.style.display = 'none'; return; }
  section.style.display = '';

  const t = idxs.map(i => bfLocalISO(sky.times[i]));
  const traces = [
    { x: t, y: idxs.map(i => sky.cloud_cover_pct[i]), name: 'Cloud cover (%)',
      type: 'scatter', mode: 'lines',
      fill: 'tozeroy', fillcolor: 'rgba(100,116,139,0.18)',
      line: { color: '#64748b', width: 1.5 },
      yaxis: 'y1' },
    { x: t, y: idxs.map(i => sky.cape_jkg[i]), name: 'CAPE (J/kg)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#d97706', width: 2 },
      marker: { color: '#d97706', size: 4 },
      yaxis: 'y2' },
  ];

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 260,
    margin: { t: 40, b: 40, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.16, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Cloud (%)', range: [0, 100], gridcolor: '#e2e8f0', tickfont: { color: '#64748b' } },
    yaxis2: {
      title: 'CAPE', overlaying: 'y', side: 'right',
      rangemode: 'tozero',
      gridcolor: 'transparent',
      tickfont: { color: '#d97706' },
      titlefont: { color: '#d97706' },
    },
  }, { responsive: true, displayModeBar: false });
  const meta = document.getElementById('bfSkyMeta');
  if (meta) meta.textContent = '— icon_eu';
}

// ── 5 · Waves ───────────────────────────────────────────────────────────────────
function bfRenderWavesChart() {
  const panel = document.getElementById('bfWavesPanel');
  const chartDiv = document.getElementById('bfWavesChart');
  if (!panel || !chartDiv) return;
  const waves = _bfExtras?.data?.waves;
  if (!waves?.times?.length) { panel.style.display = 'none'; return; }
  const idxs = bfInRangeIdx(waves.times).filter(i => waves.height_m[i] != null);
  if (!idxs.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';

  const t = idxs.map(i => bfLocalISO(waves.times[i]));
  const traces = [
    { x: t, y: idxs.map(i => waves.height_m[i]), name: 'Height (m)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#0d9488', width: 2 },
      marker: { color: '#0d9488', size: 4 },
      yaxis: 'y1' },
    { x: t, y: idxs.map(i => waves.period_s[i]), name: 'Period (s)',
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#7c3aed', width: 1.5, dash: 'dash' },
      marker: { color: '#7c3aed', size: 4 },
      yaxis: 'y2' },
  ];

  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 260,
    margin: { t: 40, b: 40, l: 55, r: 65 },
    legend: { orientation: 'h', x: 0, y: 1.16, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('Height (m)') },
    yaxis2: {
      title: 'Period (s)', overlaying: 'y', side: 'right',
      rangemode: 'tozero',
      gridcolor: 'transparent',
      tickfont: { color: '#7c3aed' },
      titlefont: { color: '#7c3aed' },
    },
  }, { responsive: true, displayModeBar: false });
  const dirs = idxs.map(i => waves.direction_deg[i]).filter(v => v != null);
  const meta = document.getElementById('bfWavesMeta');
  if (meta && dirs.length) meta.textContent = `— mean direction ${Math.round(bfCircMeanDeg(dirs))}°`;
}

// ── 3 · ICON-EPS box plots (uses _ensembleData loaded by the forecast tab) ──────
function bfEpsOverlayModel() {
  if (!forecastData) return null;
  return forecastData.models.find(m => m.model_id === 'icon_eu')
    || forecastData.models.find(m => m.model_id === forecastData.winner_model_id)
    || forecastData.models[0]
    || null;
}

function bfRenderEpsCharts() {
  const row = document.getElementById('bfEpsRow');
  if (!row) return;
  if (!document.getElementById('bfIncludeEnsemble')?.checked || !_ensembleData) {
    row.style.display = 'none';
    return;
  }

  const { tws, twd } = _ensembleData;
  const twsIdxAll = bfInRangeIdx(tws.times)
    .filter(i => tws.p25[i] != null && tws.p50[i] != null && tws.p75[i] != null);
  if (!twsIdxAll.length) { row.style.display = 'none'; return; }
  row.style.display = '';

  // Hourly boxes up to 48h in range; 3-hourly beyond
  const stepH = twsIdxAll.length <= 49 ? 1 : 3;
  const boxW = stepH * 3600e3 * 0.55;
  const pick = arr => arr.filter((_, k) => k % stepH === 0);

  const overlay = bfEpsOverlayModel();
  const overlayHours = overlay ? bfFilterHours(overlay.hours) : [];
  const overlayX = overlayHours.map(h => bfLocalISO(h.time_utc));

  // ── TWS ──
  const ti = pick(twsIdxAll);
  const twsTraces = [{
    type: 'box',
    x: ti.map(i => bfLocalISO(tws.times[i])),
    lowerfence: ti.map(i => tws.p10[i]),
    q1:         ti.map(i => tws.p25[i]),
    median:     ti.map(i => tws.p50[i]),
    q3:         ti.map(i => tws.p75[i]),
    upperfence: ti.map(i => tws.p90[i]),
    name: 'ICON-EPS (p10–p90)',
    marker: { color: '#4f46e5' },
    line: { color: '#4f46e5', width: 1.2 },
    fillcolor: 'rgba(99,102,241,0.20)',
    width: boxW,
  }];
  if (overlay) {
    twsTraces.push({
      x: overlayX,
      y: overlayHours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null),
      name: `${overlay.model_id} (det.)`,
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#2563eb', width: 2 },
      marker: { color: '#2563eb', size: 4 },
    });
  }
  const twsDiv = document.getElementById('bfEpsTwsChart');
  if (twsDiv) {
    Plotly.newPlot(twsDiv, twsTraces, {
      ...LIGHT_LAYOUT,
      height: 300,
      margin: { t: 40, b: 40, l: 55, r: 20 },
      legend: { orientation: 'h', x: 0, y: 1.14, font: { size: 10 } },
      xaxis: { ...LIGHT_XAXIS },
      yaxis: { ...LIGHT_YAXIS('TWS (kt)') },
    }, { responsive: true, displayModeBar: false });
  }

  // ── TWD ──
  const twdIdxAll = bfInRangeIdx(twd.times)
    .filter(i => twd.p50[i] != null && twd.p25_dev[i] != null && twd.p75_dev[i] != null);
  const di = pick(twdIdxAll);
  const twdTraces = [{
    type: 'box',
    x: di.map(i => bfLocalISO(twd.times[i])),
    lowerfence: di.map(i => twd.p10_dev[i] != null ? +(twd.p50[i] + twd.p10_dev[i]).toFixed(1) : null),
    q1:         di.map(i => +(twd.p50[i] + twd.p25_dev[i]).toFixed(1)),
    median:     di.map(i => twd.p50[i]),
    q3:         di.map(i => +(twd.p50[i] + twd.p75_dev[i]).toFixed(1)),
    upperfence: di.map(i => twd.p90_dev[i] != null ? +(twd.p50[i] + twd.p90_dev[i]).toFixed(1) : null),
    name: 'ICON-EPS (p10–p90)',
    marker: { color: '#dc2626' },
    line: { color: '#dc2626', width: 1.2 },
    fillcolor: 'rgba(220,38,38,0.18)',
    width: boxW,
  }];
  if (overlay) {
    twdTraces.push({
      x: overlayX,
      y: overlayHours.map(h => h.wd_deg != null ? +h.wd_deg.toFixed(0) : null),
      name: `${overlay.model_id} (det.)`,
      type: 'scatter', mode: 'lines+markers',
      line: { color: '#2563eb', width: 2 },
      marker: { color: '#2563eb', size: 4 },
      connectgaps: false,
    });
  }
  const twdDiv = document.getElementById('bfEpsTwdChart');
  if (twdDiv) {
    Plotly.newPlot(twdDiv, twdTraces, {
      ...LIGHT_LAYOUT,
      height: 300,
      margin: { t: 40, b: 40, l: 70, r: 20 },
      legend: { orientation: 'h', x: 0, y: 1.14, font: { size: 10 } },
      xaxis: { ...LIGHT_XAXIS },
      yaxis: {
        title: { text: 'TWD (deg)', standoff: 16 },
        automargin: true,
        gridcolor: '#e2e8f0',
        tickfont: { color: '#64748b' },
      },
    }, { responsive: true, displayModeBar: false });
  }
}

// ── 7 · Confidence: auto-suggestion from EPS TWS/TWD spread + model sigma ───────
function bfConfidenceAuto() {
  const parts = [];
  let score = 0, n = 0;

  if (_ensembleData?.spread_kt != null) {
    const s = _ensembleData.spread_kt;
    score += s < 4 ? 2 : s > 10 ? 0 : 1;
    n++;
    parts.push(`EPS TWS spread ${s} kn`);
  }
  const twd = _ensembleData?.twd;
  if (twd?.times?.length) {
    const idxs = bfInRangeIdx(twd.times);
    const widths = idxs
      .map(i => (twd.p90_dev[i] != null && twd.p10_dev[i] != null) ? twd.p90_dev[i] - twd.p10_dev[i] : null)
      .filter(v => v != null);
    if (widths.length) {
      const meanW = widths.reduce((a, v) => a + v, 0) / widths.length;
      score += meanW < 20 ? 2 : meanW > 60 ? 0 : 1;
      n++;
      parts.push(`EPS TWD spread ±${Math.round(meanW / 2)}°`);
    }
  }
  const models = (forecastData?.models || []).filter(m => Array.isArray(m.hours) && m.hours.length);
  if (models.length >= 2) {
    const stats = computeEnsembleStats(models.map(m => ({ ...m, hours: bfFilterHours(m.hours) })));
    const sigmas = stats.stds.filter(v => v != null);
    if (sigmas.length) {
      const meanSigma = sigmas.reduce((a, v) => a + v, 0) / sigmas.length;
      score += meanSigma < 1.5 ? 2 : meanSigma > 3 ? 0 : 1;
      n++;
      parts.push(`model sigma ${meanSigma.toFixed(1)} kn (${models.length} models)`);
    }
  }
  if (!n) return null;
  const avg = score / n;
  const level = avg >= 1.5 ? 'High' : avg >= 0.75 ? 'Medium' : 'Low';
  return { level, detail: parts.join(' · ') };
}

function bfRenderConfidenceAuto() {
  const el = document.getElementById('bfConfidenceAuto');
  if (!el) return;
  const a = bfConfidenceAuto();
  el.textContent = a ? `Suggested: ${a.level} — ${a.detail}` : '';
}

// â”€â”€ Re-render charts + table (called by range selects) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function bfRerender() {
  renderBriefingBestChart();
  renderBriefingEnsembleCharts();
  bfRenderEpsCharts();
  renderBriefingWindTable();
  if (_bfCurrentData) _bfRenderCurrentChart(_bfCurrentData.payload);
  if (_bfWindmapFramesCache) {
    _bfRenderWindmapFrames(_bfWindmapFramesCache.frames);
  }
  bfRenderPressureCharts();
  bfRenderGradientChart();
  bfRenderSkyChart();
  bfRenderWavesChart();
  bfRenderConfidenceAuto();
}

// â”€â”€ Orchestrator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const BF_SCEN_TEMPLATE = 'Main: \nAlt 1: \nAlt 2: ';

function renderBriefingTab() {
  const meta = document.getElementById('bfMetaText');
  if (!forecastData) {
    if (meta) meta.textContent = 'Run Validation + load Forecast first.';
    return;
  }
  const pos = currentLatLon();
  const now = new Date().toUTCString().replace(' GMT', ' UTC');
  if (meta) meta.textContent = `${pos ? `${pos.lat.toFixed(4)}N, ${pos.lon.toFixed(4)}E` : ''}  -  ${now}`;

  // Pre-print the scenario lines once
  const scenEl = document.getElementById('bfScenarios');
  if (scenEl && !scenEl.value) scenEl.value = BF_SCEN_TEMPLATE;

  bfInitRange();
  bfPopulateModelOverride();
  bfRerender();
  bfFetchAndRenderCurrent();
  bfFetchPressureCharts().then(bfRenderPressureCharts);
  bfFetchGradient().then(bfRenderGradientChart);
  bfFetchExtras().then(() => { bfRenderSkyChart(); bfRenderWavesChart(); });
  bfRefreshArchiveList();
}

// ── Crew summary auto-fill ───────────────────────────────────────────────────────
// Bullet order (per navigator preference): wind + shifts, risks (rain/squalls
// with gusts), current, waves, temperature + sun, confidence.
const BF_CARDINALS = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];

function bfCardinal(deg) {
  return BF_CARDINALS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}

function bfAngDiff(a, b) {   // signed a-b in [-180, 180)
  return ((a - b + 540) % 360) - 180;
}

function bfCircMeanDeg(vals) {
  let s = 0, c = 0;
  vals.forEach(v => { s += Math.sin(v * Math.PI / 180); c += Math.cos(v * Math.PI / 180); });
  return ((Math.atan2(s, c) * 180 / Math.PI) + 360) % 360;
}

function bfHourLabel(isoStr) {   // local time "1300"
  const d = bfParseUtc(isoStr);
  return String(d.getHours()).padStart(2, '0') + '00';
}

// Indices of `times` (ISO strings) that fall inside the selected briefing range
function bfInRangeIdx(times) {
  const { startTime, endTime } = bfGetRangeTimes();
  const sMs = startTime ? bfParseUtc(startTime).getTime() : null;
  const eMs = endTime   ? bfParseUtc(endTime).getTime()   : null;
  const idxs = [];
  (times || []).forEach((t, i) => {
    const ms = bfParseUtc(t).getTime();
    if ((sMs == null || ms >= sMs) && (eMs == null || ms <= eMs)) idxs.push(i);
  });
  return idxs;
}

function bfWindBullets(fh, biasKt) {
  const pts = fh
    .filter(h => h.ws_ms != null && h.wd_deg != null)
    .map(h => ({
      t: h.time_utc,
      kt: h.ws_ms * MS_TO_KT - biasKt,
      deg: h.wd_deg,
    }));
  if (pts.length < 2) return [];

  // Segment on sustained direction changes (> 25 deg from the running segment mean)
  const segs = [];
  let seg = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const meanDir = bfCircMeanDeg(seg.map(p => p.deg));
    if (seg.length >= 2 && Math.abs(bfAngDiff(pts[i].deg, meanDir)) > 25) {
      segs.push(seg);
      seg = [pts[i]];
    } else {
      seg.push(pts[i]);
    }
  }
  segs.push(seg);

  const desc = s => {
    const kts = s.map(p => p.kt);
    return {
      dir: bfCircMeanDeg(s.map(p => p.deg)),
      lo: Math.round(Math.min(...kts)),
      hi: Math.round(Math.max(...kts)),
      start: s[0].t,
    };
  };
  const spdTxt = d => d.lo === d.hi ? `${d.hi} kn` : `${d.lo}-${d.hi} kn`;

  const bullets = [];
  let prev = desc(segs[0]);
  bullets.push(`Wind: ${bfCardinal(prev.dir)} (${Math.round(prev.dir)}°) ${spdTxt(prev)} from ${bfHourLabel(prev.start)}`);

  for (let i = 1; i < segs.length; i++) {
    const cur = desc(segs[i]);
    const turnWord = bfAngDiff(cur.dir, prev.dir) > 0 ? 'veering' : 'backing';
    const prevMid = (prev.lo + prev.hi) / 2;
    const curMid  = (cur.lo + cur.hi) / 2;
    const trend = curMid - prevMid > 2 ? `building to ${spdTxt(cur)}`
                : prevMid - curMid > 2 ? `easing to ${spdTxt(cur)}`
                : spdTxt(cur);
    bullets.push(`Around ${bfHourLabel(cur.start)} ${turnWord} to ${bfCardinal(cur.dir)} (${Math.round(cur.dir)}°), ${trend}`);
    prev = cur;
  }
  return bullets;
}

function bfRiskBullets(fh, sky) {
  const bullets = [];

  const rainy = fh.filter(h => (h.precip_mm ?? 0) >= 0.2);
  if (rainy.length) {
    const gusts = rainy.map(h => h.gust_ms != null ? h.gust_ms * MS_TO_KT : null).filter(v => v != null);
    let b = `Rain possible between ${bfHourLabel(rainy[0].time_utc)} and ${bfHourLabel(rainy[rainy.length - 1].time_utc)}`;
    if (gusts.length) b += `, gusts to ${Math.round(Math.max(...gusts))} kn`;
    bullets.push(b);
  }

  if (sky?.times?.length && sky.cape_jkg?.length) {
    const idxs = bfInRangeIdx(sky.times);
    const capes = idxs.map(i => sky.cape_jkg[i]).filter(v => v != null);
    const maxCape = capes.length ? Math.max(...capes) : 0;
    if (maxCape >= 800) {
      bullets.push(`Unstable air (CAPE up to ${Math.round(maxCape)} J/kg) — thunderstorms/squalls possible, expect big gusts near rain clouds`);
    }
  }
  return bullets;
}

function bfCurrentBullet(hours) {
  const idxs = bfInRangeIdx(hours.map(h => h.time_utc));
  const pts = idxs.map(i => hours[i]).filter(h => h.speed_kt != null);
  if (!pts.length) return [];
  const speeds = pts.map(h => h.speed_kt);
  const lo = Math.min(...speeds).toFixed(1);
  const hi = Math.max(...speeds).toFixed(1);
  const dirs = pts.map(h => h.direction_deg).filter(d => d != null);
  const dir = dirs.length ? Math.round(bfCircMeanDeg(dirs)) : null;
  if (+hi < 0.15) return ['Little to no current expected'];
  const range = lo === hi ? `${hi} kn` : `${lo}-${hi} kn`;
  return [`Current: ${range}${dir != null ? ` setting ${dir}°` : ''}`];
}

function bfWaveBullet(waves) {
  const idxs = bfInRangeIdx(waves.times);
  const hts = idxs.map(i => waves.height_m[i]).filter(v => v != null);
  if (!hts.length) return [];
  const hi = Math.max(...hts);
  if (hi < 0.2) return ['Calm sea, waves below 0.2 m'];
  const lo = Math.min(...hts);
  const dirs = idxs.map(i => waves.direction_deg[i]).filter(v => v != null);
  const pers = idxs.map(i => waves.period_s[i]).filter(v => v != null);
  let b = `Waves: ${lo.toFixed(1)}-${hi.toFixed(1)} m`;
  if (dirs.length) b += ` from ${Math.round(bfCircMeanDeg(dirs))}°`;
  if (pers.length) b += `, period ~${Math.round(pers.reduce((a, v) => a + v, 0) / pers.length)} s`;
  return [b];
}

function bfTempSunBullet(fh, sky) {
  const temps = fh.map(h => h.temp_c).filter(v => v != null);
  let skyLabel = null;
  if (sky?.times?.length && sky.cloud_cover_pct?.length) {
    const idxs = bfInRangeIdx(sky.times);
    const clouds = idxs.map(i => sky.cloud_cover_pct[i]).filter(v => v != null);
    if (clouds.length) {
      const mean = clouds.reduce((a, v) => a + v, 0) / clouds.length;
      skyLabel = mean < 25 ? 'Mostly sunny' : mean <= 70 ? 'Partly cloudy' : 'General overcast';
    }
  }
  if (!temps.length && !skyLabel) return [];
  const parts = [];
  if (skyLabel) parts.push(skyLabel);
  if (temps.length) parts.push(`max temp ~${Math.round(Math.max(...temps))}°C`);
  return [parts.join(', ')];
}

function bfConfidenceBullet() {
  const a = bfConfidenceAuto();
  return a ? [`Confidence: ${a.level} — ${a.detail}`] : [];
}

async function bfGetCurrentHours() {
  const pos = currentLatLon();
  if (!pos) return null;
  const hours = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);
  if (_bfCurrentData && _bfCurrentData.lat === pos.lat && _bfCurrentData.lon === pos.lon) {
    return _bfCurrentData.payload.hours;
  }
  try {
    const resp = await fetch(`/api/ocean-current?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}`);
    if (!resp.ok) return null;
    const payload = await resp.json();
    _bfCurrentData = { lat: pos.lat, lon: pos.lon, hours, payload };
    return payload.hours;
  } catch {
    return null;
  }
}

async function bfAutoFillSummary() {
  const notesEl = document.getElementById('bfNotes');
  const btn = document.getElementById('bfAutoFillBtn');
  if (!notesEl || !btn) return;
  if (!forecastData) { alert('Load a forecast first.'); return; }
  if (notesEl.value.trim() && !confirm('Replace the current notes with an auto-generated summary?')) return;

  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = 'Generating…';
  try {
    const { models } = forecastData;
    const winner = models.find(m => m.model_id === bfGetActiveModel()) || models[0];
    const biasKt = bfGetActiveBias() * MS_TO_KT;
    const fh = bfFilterHours(winner.hours);

    const [extras, currentHours] = await Promise.all([
      bfFetchExtras(),
      bfGetCurrentHours(),
    ]);

    const bullets = [
      ...bfWindBullets(fh, biasKt),
      ...bfRiskBullets(fh, extras?.sky),
      ...(currentHours ? bfCurrentBullet(currentHours) : []),
      ...(extras?.waves ? bfWaveBullet(extras.waves) : []),
      ...bfTempSunBullet(fh, extras?.sky),
      ...bfConfidenceBullet(),
    ];

    notesEl.value = bullets.map(b => `• ${b}`).join('\n');
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

document.getElementById('bfAutoFillBtn')?.addEventListener('click', bfAutoFillSummary);

// ── Copy briefing as plain text (WhatsApp-ready) ────────────────────────────────
document.getElementById('bfCopyTextBtn')?.addEventListener('click', async () => {
  const btn = document.getElementById('bfCopyTextBtn');
  const parts = [
    document.getElementById('bfTitle')?.value || 'Weather Briefing',
    document.getElementById('bfSubtitle')?.value || '',
    document.getElementById('bfMetaText')?.textContent || '',
    '',
    document.getElementById('bfNotes')?.value || '',
  ];
  const text = parts.filter((s, i) => s !== '' || i === 3).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch (err) {
    alert('Copy failed: ' + err.message);
  }
});

// â”€â”€ Tab click â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.querySelector('.tab[data-tab="briefing"]')
  ?.addEventListener('click', renderBriefingTab);

// â”€â”€ Print / PDF (convert Plotly charts to images before printing) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function bfChartAsImg(id, fallbackHeight = 300) {
  const el = document.getElementById(id);
  if (!el || !el._fullLayout) return '';
  return Plotly.toImage(el, {
    format: 'png',
    width: el.offsetWidth || 900,
    height: el.offsetHeight || fallbackHeight,
    scale: 2,
  }).catch(err => {
    console.warn('Plotly.toImage failed for', id, err);
    return '';
  });
}

function bfEscapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function bfInsertIntoNotes(mode) {
  const notesEl = document.getElementById('bfNotes');
  if (!notesEl) return;
  const start = notesEl.selectionStart ?? notesEl.value.length;
  const end = notesEl.selectionEnd ?? notesEl.value.length;
  const value = notesEl.value || '';
  const selected = value.slice(start, end);
  const linePrefix = mode === 'bullet' ? '• ' : mode === 'check' ? '☐ ' : '1. ';

  if (!selected) {
    const pad = start > 0 && value[start - 1] !== '\n' ? '\n' : '';
    const insert = `${pad}${linePrefix}`;
    notesEl.value = value.slice(0, start) + insert + value.slice(end);
    const caret = start + insert.length;
    notesEl.setSelectionRange(caret, caret);
    notesEl.focus();
    return;
  }

  const replaced = selected
    .split('\n')
    .map((line, idx) => {
      if (mode === 'numbered') return `${idx + 1}. ${line}`;
      return `${linePrefix}${line}`;
    })
    .join('\n');
  notesEl.value = value.slice(0, start) + replaced + value.slice(end);
  notesEl.setSelectionRange(start, start + replaced.length);
  notesEl.focus();
}

document.querySelectorAll('[data-note-insert]').forEach(btn => {
  btn.addEventListener('click', () => {
    const mode = btn.getAttribute('data-note-insert') || 'bullet';
    bfInsertIntoNotes(mode);
  });
});

// crew=true → one-pager: header, summary bullets, wind chart, hourly table, wind maps.
// crew=false → full briefing with every section in framework order.
async function bfExportPrint(crew) {
  const btn = document.getElementById(crew ? 'bfPrintCrewBtn' : 'bfPrintBtn');
  const origLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Preparing...';

  const title = document.getElementById('bfTitle')?.value || 'Weather Briefing';
  const subtitle = document.getElementById('bfSubtitle')?.value || '';
  const notes = (document.getElementById('bfNotes')?.value || '').trim();
  const meta = document.getElementById('bfMetaText')?.textContent || '';
  const exportedAt = new Date().toLocaleString();

  const win = window.open('', '_blank', 'width=1200,height=900');
  if (!win) {
    btn.disabled = false;
    btn.textContent = origLabel;
    alert('Popup blocked. Allow popups to export PDF.');
    return;
  }

  const bestImg   = await bfChartAsImg('bfBestChart', 370);
  const tableHtml = document.getElementById('bfTableWrap')?.innerHTML || '';

  // Full-only assets
  let ensTwsImg = '', ensTwdImg = '', epsTwsImg = '', epsTwdImg = '',
      currentImg = '', gradientImg = '', skyImg = '', wavesImg = '';
  if (!crew) {
    ensTwsImg   = await bfChartAsImg('bfEnsembleChart', 370);
    ensTwdImg   = await bfChartAsImg('bfEnsembleDirChart', 370);
    epsTwsImg   = await bfChartAsImg('bfEpsTwsChart', 300);
    epsTwdImg   = await bfChartAsImg('bfEpsTwdChart', 300);
    gradientImg = await bfChartAsImg('bfGradientChart', 300);
    skyImg      = await bfChartAsImg('bfSkyChart', 260);
    wavesImg    = await bfChartAsImg('bfWavesChart', 260);
    currentImg  = document.getElementById('bfIncludeCurrent')?.checked
      ? await bfChartAsImg('bfCurrentChart', 300) : '';
  }

  // Section texts (full only; empty fields are skipped)
  const synopticTxt   = crew ? '' : (document.getElementById('bfSynoptic')?.value || '').trim();
  const gradientTxt   = crew ? '' : (document.getElementById('bfGradientNotes')?.value || '').trim();
  const scenariosRaw  = crew ? '' : (document.getElementById('bfScenarios')?.value || '').trim();
  const scenariosTxt  = scenariosRaw && scenariosRaw !== BF_SCEN_TEMPLATE.trim() ? scenariosRaw : '';
  const confidenceTxt = crew ? '' : (document.getElementById('bfConfidenceNotes')?.value || '').trim();
  const confAuto      = crew ? null : bfConfidenceAuto();

  const pressureHtml = crew ? '' : bfVisiblePressureCharts().map(c => `
    <figure style="margin:0">
      <img src="${c.url}" alt="Surface pressure T+${c.step_h}h" style="border-radius:4px;border:1px solid #e2e8f0" />
      <figcaption style="font-size:9px;color:#64748b;text-align:center;margin-top:2px;font-weight:600">T+${c.step_h}h — ${bfEscapeHtml(bfFmt(c.valid_utc))}:00</figcaption>
    </figure>`).join('');

  // Wind maps: full follows the checkbox; crew includes them whenever frames are loaded
  const includeWindmaps = crew
    ? !!_bfWindmapFramesCache?.frames?.length
    : document.getElementById('bfIncludeWindmaps')?.checked;
  const { startTime: wStart, endTime: wEnd } = bfGetRangeTimes();
  const wStartMs = wStart ? bfParseUtc(wStart).getTime() : null;
  const wEndMs   = wEnd   ? bfParseUtc(wEnd).getTime()   : null;
  const windmapFrames = includeWindmaps && _bfWindmapFramesCache?.frames
    ? _bfWindmapFramesCache.frames.filter(f => {
        if (!f.time_utc) return true;
        const fMs = new Date(f.time_utc).getTime();
        if (wStartMs && fMs < wStartMs) return false;
        if (wEndMs   && fMs > wEndMs)   return false;
        return true;
      })
    : [];

  const html = `<!doctype html>
<html><head><meta charset="utf-8" />
<title>${bfEscapeHtml(title)}</title>
<style>
@page{size:A4 portrait;margin:12mm}
*{
  box-sizing:border-box;
  -webkit-print-color-adjust:exact !important;
  print-color-adjust:exact !important;
}
html,body{margin:0;padding:0;background:#e9eef5}
body{font-family:"Segoe UI",Arial,sans-serif;color:#0f172a}
.doc{
  width:100%;
  max-width:190mm;
  margin:0 auto;
  background:#fff;
  padding:9mm;
  display:flex;
  flex-direction:column;
  gap:9mm;
}
.head{border-bottom:3px solid #1e3a8a;padding-bottom:8px}
.title{font-size:28px;font-weight:700;color:#1e3a8a;margin:0;line-height:1.1}
.sub{font-size:15px;color:#334155;margin-top:3px}
.meta{font-size:11px;color:#475569;margin-top:4px;font-family:Consolas,"Courier New",monospace}
.notes{
  font-size:13px;line-height:1.65;background:transparent;border:none;
  border-radius:0;padding:0;white-space:pre-wrap
}
.section{
  border:1px solid #dbe4ef;
  border-radius:10px;
  padding:10px;
  break-inside:avoid-page;
  page-break-inside:avoid;
}
.label{
  font-size:10px;
  letter-spacing:.06em;
  color:#475569;
  text-transform:uppercase;
  font-weight:700;
  margin-bottom:8px;
  border-bottom:1px solid #e2e8f0;
  padding-bottom:4px;
}
img{width:100%;display:block;border-radius:6px}
.table-wrap{overflow:visible}
table{width:100%;border-collapse:collapse;font-size:10px}
th,td{border-bottom:1px solid #e2e8f0;padding:4px 5px;text-align:right;vertical-align:top}
th:first-child,td:first-child{text-align:left}
th{background:#f8fafc;font-size:9.5px;text-transform:uppercase;letter-spacing:.04em}
tr{page-break-inside:avoid}
td[style*="background"]{background-clip:padding-box}
.footer{font-size:10px;color:#64748b;border-top:1px solid #e2e8f0;padding-top:6px}
</style></head><body>
<div class="doc">
  <div class="head">
    <h1 class="title">${bfEscapeHtml(title)}</h1>
    <div class="sub">${bfEscapeHtml(subtitle)}</div>
    <div class="meta">${bfEscapeHtml(meta)}</div>
  </div>
  ${notes ? `<div class="notes">${bfEscapeHtml(notes)}</div>` : ''}
  ${(synopticTxt || pressureHtml) ? `
  <div class="section">
    <div class="label">1 · Synoptic Overview — Met Office surface pressure</div>
    ${pressureHtml ? `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:6px">${pressureHtml}</div>` : ''}
    ${synopticTxt ? `<div class="notes">${bfEscapeHtml(synopticTxt)}</div>` : ''}
  </div>` : ''}
  ${(gradientImg || gradientTxt) ? `
  <div class="section">
    <div class="label">2 · Gradient Wind — 925 hPa (~800 m)</div>
    ${gradientImg ? `<img src="${gradientImg}" alt="Gradient wind chart" />` : ''}
    ${gradientTxt ? `<div class="notes" style="margin-top:6px">${bfEscapeHtml(gradientTxt)}</div>` : ''}
  </div>` : ''}
  <div class="section">
    <div class="label">${crew ? 'Wind Forecast' : '3 · Wind Forecast'}</div>
    ${bestImg ? `<img src="${bestImg}" alt="Best forecast chart" />` : '<div>No chart</div>'}
  </div>
  ${tableHtml ? `
  <div class="section">
    <div class="label">Hourly Forecast Table</div>
    <div class="table-wrap">${tableHtml}</div>
  </div>` : ''}
  ${ensTwsImg ? `<div class="section"><div class="label">3 · Ensemble TWS</div><img src="${ensTwsImg}" alt="Ensemble TWS" /></div>` : ''}
  ${ensTwdImg ? `<div class="section"><div class="label">3 · Ensemble TWD</div><img src="${ensTwdImg}" alt="Ensemble TWD" /></div>` : ''}
  ${epsTwsImg ? `<div class="section"><div class="label">3 · ICON-EPS TWS Uncertainty</div><img src="${epsTwsImg}" alt="ICON-EPS TWS" /></div>` : ''}
  ${epsTwdImg ? `<div class="section"><div class="label">3 · ICON-EPS TWD Spread</div><img src="${epsTwdImg}" alt="ICON-EPS TWD" /></div>` : ''}
  ${skyImg ? `<div class="section"><div class="label">4 · Local Effects — Cloud / CAPE</div><img src="${skyImg}" alt="Cloud and CAPE" /></div>` : ''}
  ${currentImg ? `<div class="section"><div class="label">5 · Ocean Current</div><img src="${currentImg}" alt="Ocean current" /></div>` : ''}
  ${wavesImg ? `<div class="section"><div class="label">5 · Waves</div><img src="${wavesImg}" alt="Waves" /></div>` : ''}
  ${scenariosTxt ? `
  <div class="section">
    <div class="label">6 · Forecast</div>
    <div class="notes">${bfEscapeHtml(scenariosTxt)}</div>
  </div>` : ''}
  ${(confAuto || confidenceTxt) ? `
  <div class="section">
    <div class="label">7 · Confidence &amp; Review</div>
    ${confAuto ? `<div class="notes" style="color:#475569;font-size:11px">Suggested: ${bfEscapeHtml(confAuto.level)} — ${bfEscapeHtml(confAuto.detail)}</div>` : ''}
    ${confidenceTxt ? `<div class="notes" style="margin-top:4px">${bfEscapeHtml(confidenceTxt)}</div>` : ''}
  </div>` : ''}
  ${windmapFrames.length ? `
  <div class="section">
    <div class="label">${crew ? 'Wind Maps' : '8 · Wind Maps'}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px">
      ${windmapFrames.map(f => `
        <figure style="margin:0">
          <img src="data:image/png;base64,${f.png_b64}" alt="${f.label}" style="border-radius:4px;border:1px solid #e2e8f0" />
          <figcaption style="font-size:9px;color:#64748b;text-align:center;margin-top:2px;font-weight:600">${f.label}</figcaption>
        </figure>`).join('')}
    </div>
  </div>` : ''}
  <div class="footer">Jelle Lourens - jelle@jellelourens.nl - ${bfEscapeHtml(exportedAt)}</div>
</div></body></html>`;

  win.document.open();
  win.document.write(html);
  win.document.close();
  win.onload = () => {
    setTimeout(() => {
      win.focus();
      win.print();
    }, 350);
  };

  btn.disabled = false;
  btn.textContent = origLabel;
}

document.getElementById('bfPrintBtn')?.addEventListener('click', () => bfExportPrint(false));
document.getElementById('bfPrintCrewBtn')?.addEventListener('click', () => bfExportPrint(true));

// â”€â”€ Wind Map GIF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.getElementById('bfWindmapBtn')?.addEventListener('click', async () => {
  const btn = document.getElementById('bfWindmapBtn');
  const pos = currentLatLon();
  if (!pos) { alert('No location set — run a forecast first.'); return; }

  const { startTime, endTime } = bfGetRangeTimes();
  const hoursDefault = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);
  // Always request the full default window so the GRIB has enough frames;
  // let start_iso / end_iso do the filtering on the server side.
  const hours = hoursDefault;
  const now = new Date();
  // Only pass start_iso when it's in the future — GRIB model runs start near "now"
  // so a past startTime would filter out all frames.
  const gifStartIso = (startTime && bfParseUtc(startTime) > now) ? startTime : '';
  const gifEndIso   = endTime || '';
  const gifModel = bfGetActiveModel() || 'harmonie_nl';

  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = 'Generating…';

  try {
    const url = `/api/windmap-gif?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}&model=${encodeURIComponent(gifModel)}`
      + (gifStartIso ? `&start_iso=${encodeURIComponent(gifStartIso)}` : '')
      + (gifEndIso   ? `&end_iso=${encodeURIComponent(gifEndIso)}`     : '');
    const resp = await fetch(url);
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(msg || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const ts = new Date().toISOString().slice(0, 16).replace('T', '_').replace(':', '');
    a.download = `windmap_${ts}.gif`;
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    alert('Wind map failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
});

// ── Wind map snapshots in report ───────────────────────────────────────────────
let _bfWindmapFramesCache = null;  // { lat, lon, hours, step, model, frames }
const _bfUploadedGribs = [];       // { model_id, filename } — survives dropdown resets

async function bfFetchAndRenderWindmaps() {
  const panel    = document.getElementById('bfWindmapsPanel');
  const grid     = document.getElementById('bfWindmapsGrid');
  const metaEl   = document.getElementById('bfWindmapsMeta');
  const checkbox = document.getElementById('bfIncludeWindmaps');
  if (!panel || !grid || !checkbox) return;

  if (!checkbox.checked) { panel.style.display = 'none'; return; }

  const pos = currentLatLon();
  if (!pos) { panel.style.display = 'none'; return; }

  const { startTime, endTime } = bfGetRangeTimes();
  const hoursDefault = parseInt(document.getElementById('fcHoursAhead')?.value || '48', 10);
  const hours = hoursDefault;
  const now = new Date();
  // Only filter by start when it's in the future — GRIB starts near “now”
  const framesStartIso = (startTime && bfParseUtc(startTime) > now) ? startTime : '';
  const framesEndIso   = endTime || '';
  const step     = parseInt(document.getElementById('bfWindmapStep')?.value || '3', 10);
  const gifModel = bfGetActiveModel() || 'harmonie_nl';

  if (
    _bfWindmapFramesCache &&
    _bfWindmapFramesCache.lat          === pos.lat &&
    _bfWindmapFramesCache.lon          === pos.lon &&
    _bfWindmapFramesCache.hours        === hours &&
    _bfWindmapFramesCache.step         === step &&
    _bfWindmapFramesCache.model        === gifModel &&
    _bfWindmapFramesCache.framesStartIso === framesStartIso &&
    _bfWindmapFramesCache.framesEndIso   === framesEndIso
  ) {
    _bfRenderWindmapFrames(_bfWindmapFramesCache.frames);
    return;
  }

  panel.style.display = '';
  grid.innerHTML = '<p style=”color:#64748b;padding:8px”>Generating wind maps — this may take a minute…</p>';
  if (metaEl) metaEl.textContent = '— loading…';

  try {
    const url = `/api/windmap-frames?lat=${pos.lat}&lon=${pos.lon}&hours=${hours}&model=${encodeURIComponent(gifModel)}&step=${step}`
      + (framesStartIso ? `&start_iso=${encodeURIComponent(framesStartIso)}` : '')
      + (framesEndIso   ? `&end_iso=${encodeURIComponent(framesEndIso)}`     : '');
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    const data = await resp.json();
    _bfWindmapFramesCache = { lat: pos.lat, lon: pos.lon, hours, step, model: gifModel, framesStartIso, framesEndIso, frames: data.frames };
    _bfRenderWindmapFrames(data.frames);
    if (metaEl) metaEl.textContent = `— ${data.frames.length} frames`;
  } catch (err) {
    grid.innerHTML = `<p style=”color:#dc2626;padding:8px”>Failed: ${err.message}</p>`;
    if (metaEl) metaEl.textContent = '— unavailable';
  }
}

function _bfRenderWindmapFrames(frames) {
  const panel = document.getElementById('bfWindmapsPanel');
  const grid  = document.getElementById('bfWindmapsGrid');
  if (!panel || !grid || !frames?.length) { if (panel) panel.style.display = 'none'; return; }

  grid.innerHTML = '';
  frames.forEach(f => {
    const fig = document.createElement('figure');
    fig.className = 'bf-windmap-fig';
    const img = document.createElement('img');
    img.src = 'data:image/png;base64,' + f.png_b64;
    img.alt = f.label || '';
    // Show local time so it matches the range selectors, with UTC offset for clarity
    let caption = f.label || '';
    if (f.time_utc) {
      const dt = new Date(f.time_utc.endsWith('Z') ? f.time_utc : f.time_utc + 'Z');
      const pad = n => String(n).padStart(2, '0');
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const offsetMin = -dt.getTimezoneOffset();
      const sign = offsetMin >= 0 ? '+' : '-';
      const oh = pad(Math.floor(Math.abs(offsetMin) / 60));
      const om = pad(Math.abs(offsetMin) % 60);
      caption = `${pad(dt.getDate())} ${months[dt.getMonth()]}  ${pad(dt.getHours())}:${pad(dt.getMinutes())} (UTC${sign}${oh}:${om})`;
    }
    const cap = document.createElement('figcaption');
    cap.textContent = caption;
    fig.appendChild(img);
    fig.appendChild(cap);
    grid.appendChild(fig);
  });
  panel.style.display = '';
}

document.getElementById('bfModelOverride')?.addEventListener('change', () => {
  _bfWindmapFramesCache = null;
  bfRerender();
  if (document.getElementById('bfIncludeWindmaps')?.checked) bfFetchAndRenderWindmaps();
});
document.getElementById('bfIncludeWindmaps')?.addEventListener('change', () => {
  _bfWindmapFramesCache = null;
  bfFetchAndRenderWindmaps();
});
document.getElementById('bfWindmapStep')?.addEventListener('change', () => {
  _bfWindmapFramesCache = null;
  if (document.getElementById('bfIncludeWindmaps')?.checked) bfFetchAndRenderWindmaps();
});
document.getElementById('bfIncludeCurrent')?.addEventListener('change', () => {
  if (document.getElementById('bfIncludeCurrent').checked) {
    bfFetchAndRenderCurrent();
  } else {
    document.getElementById('bfCurrentPanel').style.display = 'none';
  }
});

document.getElementById('bfIncludeEnsemble')?.addEventListener('change', () => {
  renderBriefingEnsembleCharts();
  bfRenderEpsCharts();
});

// ── GRIB import ────────────────────────────────────────────────────────────────
document.getElementById('bfImportGribBtn')?.addEventListener('click', () => {
  document.getElementById('bfGribFileInput')?.click();
});

document.getElementById('bfGribFileInput')?.addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const btn = document.getElementById('bfImportGribBtn');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Uploading…';
  try {
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch('/api/upload-grib', { method: 'POST', body: form });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      throw new Error(data?.detail || `HTTP ${resp.status}`);
    }
    const { model_id, filename } = data;
    _bfUploadedGribs.push({ model_id, filename });
    const sel = document.getElementById('bfModelOverride');
    if (sel) {
      const opt = document.createElement('option');
      opt.value = model_id;
      opt.textContent = `GRIB: ${filename}`;
      sel.appendChild(opt);
      sel.value = model_id;
    }
    // Auto-enable Wind maps so the result is visible immediately
    const cb = document.getElementById('bfIncludeWindmaps');
    if (cb && !cb.checked) cb.checked = true;
    _bfWindmapFramesCache = null;
    bfRerender();
    bfFetchAndRenderWindmaps();
  } catch (err) {
    alert('GRIB import failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
    e.target.value = '';
  }
});

// ── Save briefing as JSON ──────────────────────────────────────────────────────
function bfBuildPayload() {
  const pos = currentLatLon();
  return {
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
    synoptic:         document.getElementById('bfSynoptic')?.value ?? '',
    gradient_notes:   document.getElementById('bfGradientNotes')?.value ?? '',
    scenarios:        document.getElementById('bfScenarios')?.value ?? '',
    confidence_notes: document.getElementById('bfConfidenceNotes')?.value ?? '',
    ensembleData: _ensembleData ?? null,
    forecastData,
  };
}

document.getElementById('bfSaveBtn')?.addEventListener('click', () => {
  if (!forecastData) { alert('No forecast loaded.'); return; }

  const payload = bfBuildPayload();

  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  const ts   = new Date().toISOString().slice(0, 16).replace('T', '_').replace(':', '');
  a.href     = url;
  a.download = `briefing_${ts}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// â”€â”€ Load briefing from JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.getElementById('bfLoadBtn')?.addEventListener('click', () => {
  document.getElementById('bfFileInput')?.click();
});

function bfApplyPayload(payload) {
  if (!payload.forecastData) throw new Error('Invalid briefing file');

  // Restore global forecast state (variables defined in forecast.js)
  forecastData      = payload.forecastData;
  _winnerModelId    = payload.winner_model_id ?? forecastData.winner_model_id;
  _biasWsMs         = payload.bias_ws_ms      ?? forecastData.bias_ws_ms;
  _selectedModels   = new Set(forecastData.models.map(m => m.model_id));
  _ensembleData     = payload.ensembleData ?? null;   // older files: EPS charts stay hidden
  bfPopulateModelOverride();

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
  if (payload.synoptic         != null) document.getElementById('bfSynoptic').value         = payload.synoptic;
  if (payload.gradient_notes   != null) document.getElementById('bfGradientNotes').value    = payload.gradient_notes;
  if (payload.scenarios        != null) document.getElementById('bfScenarios').value        = payload.scenarios;
  if (payload.confidence_notes != null) document.getElementById('bfConfidenceNotes').value  = payload.confidence_notes;

  // Switch to briefing tab and render
  document.querySelector('.tab[data-tab="briefing"]')?.click();

  // Restore range selects after init (bfInitRange runs inside renderBriefingTab)
  setTimeout(() => {
    if (payload.range_start != null) document.getElementById('bfRangeStart').value = payload.range_start;
    if (payload.range_end   != null) document.getElementById('bfRangeEnd').value   = payload.range_end;
    bfRerender();
  }, 50);
}

document.getElementById('bfFileInput')?.addEventListener('change', e => {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    try {
      bfApplyPayload(JSON.parse(ev.target.result));
    } catch (err) {
      alert('Failed to load briefing file: ' + err.message);
    }
  };
  reader.readAsText(file);
  // Reset so same file can be re-loaded
  e.target.value = '';
});

// ── Briefing archive: save on the server, look back later ──────────────────────
async function bfRefreshArchiveList(selectedId) {
  const sel = document.getElementById('bfArchiveSelect');
  if (!sel) return;
  try {
    const resp = await fetch('/api/briefings');
    if (!resp.ok) return;
    const { briefings } = await resp.json();
    sel.innerHTML = '<option value="">Saved briefings…</option>';
    briefings.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.id;
      const when = b.saved_at ? b.saved_at.slice(0, 16).replace('T', ' ') : b.id;
      const label = `${when} — ${b.title}${b.subtitle ? ' · ' + b.subtitle : ''}`;
      opt.textContent = label.length > 70 ? label.slice(0, 69) + '…' : label;
      sel.appendChild(opt);
    });
    if (selectedId) sel.value = selectedId;
  } catch { /* archive unavailable — leave the select as is */ }
}

document.getElementById('bfArchiveBtn')?.addEventListener('click', async () => {
  const btn = document.getElementById('bfArchiveBtn');
  if (!forecastData) { alert('No forecast loaded.'); return; }
  btn.disabled = true;
  const orig = btn.textContent;
  try {
    const resp = await fetch('/api/briefings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(bfBuildPayload()),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${(await resp.text()).slice(0, 120)}`);
    const { id } = await resp.json();
    btn.textContent = 'Saved ✓';
    setTimeout(() => { btn.textContent = orig; }, 1500);
    bfRefreshArchiveList(id);
  } catch (err) {
    alert('Save online failed: ' + err.message);
    btn.textContent = orig;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('bfArchiveSelect')?.addEventListener('change', async e => {
  const id = e.target.value;
  if (!id) return;
  try {
    const resp = await fetch(`/api/briefings/${encodeURIComponent(id)}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    bfApplyPayload(await resp.json());
  } catch (err) {
    alert('Failed to load saved briefing: ' + err.message);
  }
});

document.getElementById('bfArchiveDeleteBtn')?.addEventListener('click', async () => {
  const sel = document.getElementById('bfArchiveSelect');
  const id = sel?.value;
  if (!id) { alert('Select a saved briefing first.'); return; }
  const label = sel.options[sel.selectedIndex]?.textContent || id;
  if (!confirm(`Delete saved briefing?\n\n${label}`)) return;
  try {
    const resp = await fetch(`/api/briefings/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    bfRefreshArchiveList();
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
});



