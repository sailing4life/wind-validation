/* forecast.js — Forecast tab: Windy iframe + Plotly charts + hourly table */

const MS_TO_KT = 1.94384;

let forecastData = null;
let _winnerModelId = '';
let _biasWsMs = 0;

// ── Called by app.js after successful validation ───────────────────────────────
function setForecastParams(lat, lon, winnerModelId, biasWsMs) {
  _winnerModelId = winnerModelId || '';
  _biasWsMs = biasWsMs || 0;
  forecastData = null;  // invalidate cache on new pin
  // pre-fill lat/lon if forecast inputs ever added
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
    status.textContent = _winnerModelId
      ? `Winner: ${_winnerModelId} · bias ${(_biasWsMs * MS_TO_KT).toFixed(1)} kt`
      : 'Run Validation first for bias correction';
    updateWindyMap(pos.lat, pos.lon);
    renderForecast();
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  } finally {
    document.getElementById('fcRunBtn').disabled = false;
  }
}

// ── Main render ────────────────────────────────────────────────────────────────
function renderForecast() {
  if (!forecastData) return;
  renderForecastCharts();
  renderForecastTable();
}

// ── Color helpers ──────────────────────────────────────────────────────────────
function windSpeedColor(kt) {
  const t = Math.min(1, Math.max(0, (kt - 5) / 20));
  const hue = Math.round(220 - t * 220);
  return `hsl(${hue}, 75%, 82%)`;
}

const FC_COLORS = ['#2563eb','#16a34a','#dc2626','#d97706','#7c3aed','#0891b2','#be185d'];
function modelColor(idx) { return FC_COLORS[idx % FC_COLORS.length]; }

// ── Plotly charts ──────────────────────────────────────────────────────────────
function renderForecastCharts() {
  const container = document.getElementById('fcCharts');
  container.innerHTML = '';

  const { winner_model_id, bias_ws_ms, models } = forecastData;
  if (!models || models.length === 0) {
    container.textContent = 'No forecast data available.';
    return;
  }

  const traces = [];
  models.forEach((series, idx) => {
    const color = modelColor(idx);
    const isWinner = series.model_id === winner_model_id;
    const times = series.hours.map(h => h.time_utc);
    const ws_kt = series.hours.map(h => h.ws_ms != null ? +(h.ws_ms * MS_TO_KT).toFixed(1) : null);
    const gust_kt = series.hours.map(h => h.gust_ms != null ? +(h.gust_ms * MS_TO_KT).toFixed(1) : null);
    const wd = series.hours.map(h => h.wd_deg);

    traces.push({
      x: times, y: ws_kt, name: series.model_id,
      type: 'scatter', mode: 'lines',
      line: { color, width: isWinner ? 2.5 : 1.5 },
      legendgroup: series.model_id,
      yaxis: 'y1',
    });

    if (isWinner && gust_kt.some(v => v != null)) {
      traces.push({
        x: times, y: gust_kt, name: series.model_id + ' gust',
        type: 'scatter', mode: 'lines',
        line: { color, width: 1.5, dash: 'dot' },
        legendgroup: series.model_id,
        showlegend: true,
        yaxis: 'y1',
      });
    }

    if (isWinner && bias_ws_ms !== 0) {
      const bKt = bias_ws_ms * MS_TO_KT;
      const corr = ws_kt.map(v => v != null ? +(v - bKt).toFixed(1) : null);
      traces.push({
        x: times, y: corr, name: series.model_id + ' corrected',
        type: 'scatter', mode: 'lines',
        line: { color: '#f59e0b', width: 2.5 },
        legendgroup: series.model_id,
        yaxis: 'y1',
      });
    }

    traces.push({
      x: times, y: wd, name: series.model_id + ' dir',
      type: 'scatter', mode: 'markers',
      marker: { color, size: isWinner ? 5 : 3 },
      legendgroup: series.model_id,
      showlegend: false,
      yaxis: 'y2',
    });
  });

  const layout = {
    height: 340,
    margin: { t: 20, b: 40, l: 45, r: 55 },
    paper_bgcolor: '#1e293b',
    plot_bgcolor: '#0f172a',
    font: { color: '#e2e8f0', size: 11 },
    legend: { orientation: 'h', y: -0.22, font: { size: 10 } },
    xaxis: { gridcolor: '#334155', tickfont: { color: '#94a3b8' } },
    yaxis: { title: 'kt', gridcolor: '#334155', tickfont: { color: '#94a3b8' }, rangemode: 'tozero' },
    yaxis2: {
      title: '°', overlaying: 'y', side: 'right',
      range: [0, 360], dtick: 90,
      gridcolor: 'transparent',
      tickfont: { color: '#94a3b8' },
    },
  };

  const div = document.createElement('div');
  container.appendChild(div);
  Plotly.newPlot(div, traces, layout, { responsive: true, displayModeBar: false });
}

// ── Forecast table ─────────────────────────────────────────────────────────────
function renderForecastTable() {
  const wrap = document.getElementById('fcTableWrap');
  wrap.innerHTML = '';

  const { winner_model_id, models } = forecastData;
  if (!models || models.length === 0) return;

  const winnerSeries = models.find(m => m.model_id === winner_model_id) || models[0];

  const heading = document.createElement('h3');
  heading.className = 'fc-table-heading';
  heading.textContent = `Hourly forecast — ${winnerSeries.model_id}`;
  wrap.appendChild(heading);

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'fc-table-scroll';

  const table = document.createElement('table');
  table.className = 'fc-table';
  table.innerHTML = `<thead><tr>
    <th>Time UTC</th><th>TWS (kt)</th><th>Gust (kt)</th>
    <th>TWD (°)</th><th>Temp (°C)</th><th class="note-col">Notes</th>
  </tr></thead>`;

  const tbody = document.createElement('tbody');
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  for (const hour of winnerSeries.hours) {
    const ws_kt = hour.ws_ms != null ? (hour.ws_ms * MS_TO_KT).toFixed(1) : null;
    const gust_kt = hour.gust_ms != null ? (hour.gust_ms * MS_TO_KT).toFixed(1) : null;
    const wd = hour.wd_deg != null ? Math.round(hour.wd_deg) + '°' : '—';
    const temp = hour.temp_c != null ? hour.temp_c.toFixed(1) : '—';
    const t = new Date(hour.time_utc);
    const label = `${String(t.getUTCDate()).padStart(2,'0')} ${MONTHS[t.getUTCMonth()]} ${String(t.getUTCHours()).padStart(2,'0')}z`;
    const wsColor = ws_kt != null ? windSpeedColor(+ws_kt) : '';
    const gustColor = gust_kt != null ? windSpeedColor(+gust_kt) : '';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="fc-time">${label}</td>
      <td class="fc-num" style="background:${wsColor}">${ws_kt ?? '—'}</td>
      <td class="fc-num" style="background:${gustColor}">${gust_kt ?? '—'}</td>
      <td class="fc-num">${wd}</td>
      <td class="fc-num">${temp}</td>
      <td class="note-cell" contenteditable="true"></td>
    `;
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  scrollWrap.appendChild(table);
  wrap.appendChild(scrollWrap);
}
