/* forecast.js — Forecast tab: Windy iframe + Plotly charts + hourly table */

let forecastData = null;
let lastForecastParams = null;   // set by app.js after validation

const MS_TO_KT = 1.94384;

// ── Tab switching ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');

      if (btn.dataset.tab === 'forecast') {
        if (lastForecastParams) {
          updateWindyMap(lastForecastParams.lat, lastForecastParams.lon);
          if (!forecastData) {
            loadForecast();
          }
        }
      }
    });
  });

  document.getElementById('fcRunBtn').addEventListener('click', loadForecast);
});

// Called by app.js after a successful validation
function setForecastParams(lat, lon, winnerModelId, biasWsMs) {
  lastForecastParams = { lat, lon, winnerModelId, biasWsMs };
  forecastData = null;  // invalidate cache when pin changes
}

// ── Windy map ──────────────────────────────────────────────────────────────────
function updateWindyMap(lat, lon) {
  const zoom = 7;
  const iframe = document.getElementById('fcMap');
  iframe.src = `https://embed.windy.com/embed2.html?lat=${lat}&lon=${lon}&zoom=${zoom}` +
    `&level=surface&overlay=wind&menu=&message=&marker=true&calendar=&pressure=` +
    `&type=map&location=coordinates&detail=&detailLat=${lat}&detailLon=${lon}` +
    `&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1`;
}

// ── API call ───────────────────────────────────────────────────────────────────
async function loadForecast() {
  if (!lastForecastParams) return;
  const { lat, lon, winnerModelId, biasWsMs } = lastForecastParams;
  const hoursAhead = parseInt(document.getElementById('fcHoursAhead').value, 10) || 48;
  const status = document.getElementById('fcStatus');
  status.textContent = 'Loading…';

  try {
    const resp = await fetch('/api/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        lat, lon,
        winner_model_id: winnerModelId || '',
        bias_ws_ms: biasWsMs || 0,
        hours_ahead: hoursAhead,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    forecastData = await resp.json();
    status.textContent = '';
    renderForecast();
    updateWindyMap(lat, lon);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
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
  if (kt == null) return '';
  const t = Math.min(1, Math.max(0, (kt - 5) / 20));  // 0 at 5 kt, 1 at 25 kt
  const hue = Math.round(220 - t * 220);               // 220 (blue) → 0 (red)
  return `hsl(${hue}, 75%, 82%)`;
}

const MODEL_COLORS = [
  '#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed', '#0891b2', '#be185d',
];
function modelColor(idx) { return MODEL_COLORS[idx % MODEL_COLORS.length]; }

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

    // TWS line
    traces.push({
      x: times, y: ws_kt, name: series.model_id,
      type: 'scatter', mode: 'lines',
      line: { color, width: isWinner ? 2.5 : 1.5 },
      legendgroup: series.model_id,
      yaxis: 'y1',
    });

    // Gust dashed (winner only to keep chart readable)
    if (isWinner && gust_kt.some(v => v != null)) {
      traces.push({
        x: times, y: gust_kt, name: `${series.model_id} gust`,
        type: 'scatter', mode: 'lines',
        line: { color, width: 1.5, dash: 'dot' },
        legendgroup: series.model_id,
        showlegend: false,
        yaxis: 'y1',
      });
    }

    // Corrected TWS (winner only)
    if (isWinner && bias_ws_ms !== 0) {
      const corr_kt = ws_kt.map(v => v != null ? +(v - bias_ws_ms * MS_TO_KT).toFixed(1) : null);
      traces.push({
        x: times, y: corr_kt, name: `${series.model_id} corrected`,
        type: 'scatter', mode: 'lines',
        line: { color: '#f59e0b', width: 2.5 },
        legendgroup: series.model_id,
        yaxis: 'y1',
      });
    }

    // TWD — right axis, winner only to keep readable; others as separate trace group
    traces.push({
      x: times, y: wd, name: `${series.model_id} dir`,
      type: 'scatter', mode: 'markers',
      marker: { color, size: isWinner ? 5 : 3, symbol: 'circle' },
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
    legend: { orientation: 'h', y: -0.18, font: { size: 10 } },
    xaxis: { gridcolor: '#334155', tickfont: { color: '#94a3b8' } },
    yaxis: { title: 'kt', gridcolor: '#334155', tickfont: { color: '#94a3b8' } },
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

  // Use winner model for the table; fall back to first model
  const winnerSeries = models.find(m => m.model_id === winner_model_id) || models[0];

  const heading = document.createElement('h3');
  heading.className = 'fc-table-heading';
  heading.textContent = `Hourly forecast — ${winnerSeries.model_id}`;
  wrap.appendChild(heading);

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'fc-table-scroll';

  const table = document.createElement('table');
  table.className = 'fc-table';
  table.innerHTML = `
    <thead>
      <tr>
        <th>Time UTC</th>
        <th>TWS (kt)</th>
        <th>Gust (kt)</th>
        <th>TWD (°)</th>
        <th>Temp (°C)</th>
        <th class="note-col">Notes</th>
      </tr>
    </thead>
  `;
  const tbody = document.createElement('tbody');

  for (const hour of winnerSeries.hours) {
    const ws_kt = hour.ws_ms != null ? (hour.ws_ms * MS_TO_KT).toFixed(1) : '—';
    const gust_kt = hour.gust_ms != null ? (hour.gust_ms * MS_TO_KT).toFixed(1) : '—';
    const wd = hour.wd_deg != null ? Math.round(hour.wd_deg) + '°' : '—';
    const temp = hour.temp_c != null ? hour.temp_c.toFixed(1) : '—';
    const t = new Date(hour.time_utc);
    const label = t.toUTCString().replace(':00 GMT', 'z').slice(5, -4);  // "DD Mon HH:MMz"
    const timeLabel = `${t.getUTCDate().toString().padStart(2,'0')} ${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][t.getUTCMonth()]} ${t.getUTCHours().toString().padStart(2,'0')}z`;

    const wsColor = hour.ws_ms != null ? windSpeedColor(+ws_kt) : '';
    const gustColor = hour.gust_ms != null ? windSpeedColor(+gust_kt) : '';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="fc-time">${timeLabel}</td>
      <td class="fc-num" style="background:${wsColor}">${ws_kt}</td>
      <td class="fc-num" style="background:${gustColor}">${gust_kt}</td>
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
