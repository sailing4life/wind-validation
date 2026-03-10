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

// â”€â”€ Best model chart â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    winner_model_id + (bias_ws_ms ? `  -  bias ${(bias_ws_ms * MS_TO_KT).toFixed(1)} kt` : '');

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
    height: 340,
    margin: { t: 60, b: 30, l: 50, r: 55 },
    legend: { orientation: 'h', x: 0, y: 1.18, font: { size: 10 } },
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

  const { winner_model_id, models } = forecastData;
  // Briefing should always show ensemble context from all available models.
  const selected = models.filter(m => Array.isArray(m.hours) && m.hours.length > 0);
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
    traces.push({ x: statTimes, y: lower, name: '+/-1 sigma', type: 'scatter', mode: 'lines', fill: 'tonexty', fillcolor: 'rgba(20,184,166,0.18)', line: { width: 0 }, hoverinfo: 'skip' });
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

  // â”€â”€ TWD â”€â”€
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
      margin: { t: 20, b: 30, l: 70, r: 20 },
      showlegend: false,
      xaxis: { ...LIGHT_XAXIS },
      yaxis: {
        title: { text: 'TWD (deg)', standoff: 16 },
        automargin: true,
        range: [0, 360], dtick: 90,
        gridcolor: '#e2e8f0', tickfont: { color: '#64748b' },
        tickvals: [0, 90, 180, 270, 360],
        ticktext: ['N (0 deg)', 'E (90 deg)', 'S (180 deg)', 'W (270 deg)', 'N (360 deg)'],
      },
    }, { responsive: true, displayModeBar: false });
  }
}

// â”€â”€ Hourly wind table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Re-render charts + table (called by range selects) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function bfRerender() {
  renderBriefingBestChart();
  renderBriefingEnsembleCharts();
  renderBriefingWindTable();
}

// â”€â”€ Orchestrator â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function renderBriefingTab() {
  const meta = document.getElementById('bfMetaText');
  if (!forecastData) {
    if (meta) meta.textContent = 'Run Validation + load Forecast first.';
    return;
  }
  const pos = currentLatLon();
  const now = new Date().toUTCString().replace(' GMT', ' UTC');
  if (meta) meta.textContent = `${pos ? `${pos.lat.toFixed(4)} degN, ${pos.lon.toFixed(4)} degE` : ''}  -  ${now}`;

  bfInitRange();
  bfRerender();
}

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

document.getElementById('bfPrintBtn')?.addEventListener('click', async () => {
  const btn = document.getElementById('bfPrintBtn');
  btn.disabled = true;
  btn.textContent = 'Preparing...';

  const title = document.getElementById('bfTitle')?.value || 'Weather Briefing';
  const subtitle = document.getElementById('bfSubtitle')?.value || '';
  const notes = (document.getElementById('bfNotes')?.value || '').trim();
  const meta = document.getElementById('bfMetaText')?.textContent || '';

  const win = window.open('', '_blank', 'width=1200,height=900');
  if (!win) {
    btn.disabled = false;
    btn.textContent = 'Print / PDF';
    alert('Popup blocked. Allow popups to export PDF.');
    return;
  }
  const bestImg = await bfChartAsImg('bfBestChart', 340);
  const ensTwsImg = await bfChartAsImg('bfEnsembleChart', 300);
  const ensTwdImg = await bfChartAsImg('bfEnsembleDirChart', 300);
  const tableHtml = document.getElementById('bfTableWrap')?.innerHTML || '';

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
.title{font-size:24px;font-weight:700;color:#1e3a8a;margin:0;line-height:1.1}
.sub{font-size:13px;color:#334155;margin-top:3px}
.meta{font-size:11px;color:#475569;margin-top:4px;font-family:Consolas,"Courier New",monospace}
.notes{
  font-size:11px;line-height:1.65;background:#f8fafc;border:1px solid #dbe4ef;
  border-radius:10px;padding:10px 12px;white-space:pre-wrap
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
  <div class="section">
    <div class="label">Wind Forecast</div>
    ${bestImg ? `<img src="${bestImg}" alt="Best forecast chart" />` : '<div>No chart</div>'}
  </div>
  <div class="section">
    <div class="label">Hourly Forecast Table</div>
    <div class="table-wrap">${tableHtml}</div>
  </div>
  ${ensTwsImg ? `<div class="section"><div class="label">Ensemble TWS</div><img src="${ensTwsImg}" alt="Ensemble TWS" /></div>` : ''}
  ${ensTwdImg ? `<div class="section"><div class="label">Ensemble TWD</div><img src="${ensTwdImg}" alt="Ensemble TWD" /></div>` : ''}
  <div class="footer">Generated by Wind Validation briefing export</div>
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
  btn.textContent = 'Print / PDF';
});

// â”€â”€ Save briefing as JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Load briefing from JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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



