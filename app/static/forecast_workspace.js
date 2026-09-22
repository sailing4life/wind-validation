/* Overview drilldowns and a single selected forecast chart surface. */
let forecastWorkspaceView = 'overview';
let forecastDetailStation = null;

function selectForecastView(name) {
  forecastWorkspaceView = name;
  document.querySelectorAll('[data-forecast-view]').forEach(button => {
    const active = button.dataset.forecastView === name;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll('[data-forecast-panel]').forEach(panel => {
    panel.hidden = panel.dataset.forecastPanel !== name;
  });
  if (name === 'weather') {
    document.getElementById('wxDetails').open = true;
    if (forecastData && typeof renderWeatherTab === 'function') renderWeatherTab();
  }
  renderForecastWorkspace();
  resizeForecastCharts();
}

function renderForecastWorkspace() {
  const ready = !!forecastData?.models?.some(model => model.hours?.length);
  document.getElementById('fcChartsEmpty').hidden = ready;
  const view = document.getElementById('fcChartView').value;
  document.querySelectorAll('[data-chart-view]').forEach(panel => {
    panel.hidden = !ready || (panel.dataset.chartView !== view &&
      !(panel.dataset.chartView === 'extras' && ['uncertainty', 'upperair'].includes(view)));
  });
  const descriptions = {
    best: 'Expected wind, gusts and direction. Open a station on Overview to compare with observations.',
    comparison: 'Compare the selected models at your location. Click a model to include or exclude it.',
    uncertainty: 'ICON-EPS members show how much the forecast may vary. Load ensemble detail when needed.',
    weather: 'Temperature and hourly rainfall from the loaded models.',
    upperair: 'Compare surface wind with the wind at 925 hPa (about 800 m). Load upper-air detail when needed.',
    table: 'Hourly values for the selected forecast, including wind and gusts.',
    calibration: 'How local observations adjust the forecast, and the history supporting the correction.',
  };
  document.getElementById('fcChartDescription').textContent = descriptions[view];
  document.getElementById('fcLoadExtras').textContent = view === 'upperair' ? 'Load upper-air detail' : 'Load ensemble detail';
  if (!ready || forecastWorkspaceView !== 'charts') return;
  if (view === 'best') renderBestForecastChart();
  if (view === 'comparison') { renderModelToggles(); renderEnsembleChart(); }
  if (view === 'uncertainty') renderIconEpsCharts();
  if (view === 'weather') {
    renderTempChart(); renderPrecipChart();
    document.getElementById('fcTempPrecipRow').style.display = '';
  }
  if (view === 'upperair' && _gradientData) renderGradientChart();
  if (view === 'table') renderForecastTable();
  if (view === 'calibration') renderVerification();
}

// Avoid drawing a false 359° → 1° sweep through the whole compass.
function forecastDetailTrace(points, field, name, color, direction = false, dash = 'solid') {
  const x = [], y = [];
  let previous = null;
  points.forEach(point => {
    const raw = typeof field === 'function' ? field(point) : point[field];
    const value = Number.isFinite(raw) ? (direction ? (raw % 360 + 360) % 360 : raw * MS_TO_KT) : null;
    if (direction && previous !== null && value !== null && Math.abs(value - previous) > 180) {
      x.push(point.time_utc); y.push(null);
    }
    x.push(point.time_utc); y.push(value); previous = value;
  });
  return {x, y, name, type: 'scatter', mode: 'lines+markers', connectgaps: false,
    line: {color, width: 2, dash}, marker: {size: 4}};
}

function plotForecastDetail(speed, direction) {
  const layout = {...LIGHT_LAYOUT, height: 270, margin: {t: 15, b: 65, l: 48, r: 15},
    xaxis: {...LIGHT_XAXIS}, legend: {orientation: 'h', y: -0.35}, hovermode: 'x unified'};
  fcLocalPlot('fcDetailTws', speed, {...layout, yaxis: LIGHT_YAXIS('kt')}, {responsive: true, displayModeBar: false});
  fcLocalPlot('fcDetailTwd', direction, {...layout,
    yaxis: {title: '°', range: [0, 360], tickvals: [0, 90, 180, 270, 360], gridcolor: '#e2e8f0'}},
    {responsive: true, displayModeBar: false});
}

function openForecastDetail(title, eyebrow, description) {
  document.getElementById('fcDetailTitle').textContent = title;
  document.getElementById('fcDetailEyebrow').textContent = eyebrow;
  document.getElementById('fcDetailDescription').textContent = description;
  document.getElementById('fcDetailStatus').textContent = '';
  const dialog = document.getElementById('fcDetailDialog');
  if (!dialog.open) dialog.showModal();
}

function openForecastStation(stationId) {
  forecastDetailStation = stationId;
  const evidence = _forecastValidation || {};
  const station = (evidence.stations_used || []).find(item => item.station_id === stationId);
  openForecastDetail(station?.name || stationId, 'Station history',
    'Observed and raw forecast wind at this station · 10 m · local time. Gaps mean data is unavailable.');
  const series = (evidence.station_series || []).find(item => item.station_id === stationId)?.points || [];
  const reserved = new Set(['time_utc', 'obs_ws_ms', 'obs_wd_deg', 'model_wd_deg']);
  const modelIds = [...new Set(series.flatMap(point => Object.keys(point).filter(key => !reserved.has(key))))];
  const select = document.getElementById('fcDetailModel');
  select.replaceChildren();
  modelIds.forEach(id => select.add(new Option(id, id)));
  if (modelIds.includes(_winnerModelId)) select.value = _winnerModelId;
  document.getElementById('fcDetailModelLabel').hidden = !modelIds.length;
  renderForecastStation();
}

function renderForecastStation() {
  const evidence = _forecastValidation || {};
  const series = (evidence.station_series || []).find(item => item.station_id === forecastDetailStation)?.points || [];
  const model = document.getElementById('fcDetailModel').value;
  // Older saved snapshots contain speed only. Show available observations,
  // never substitute the location's forecast for a station's missing history.
  const observations = series.length ? series : (evidence.observation_points || [])
    .filter(point => point.station_id === forecastDetailStation)
    .map(point => ({time_utc: point.time_utc, obs_ws_ms: point.ws_ms, obs_wd_deg: point.wd_deg}));
  const speed = [forecastDetailTrace(observations, 'obs_ws_ms', 'Observed', '#15803d')];
  const direction = [forecastDetailTrace(observations, 'obs_wd_deg', 'Observed', '#15803d', true)];
  if (model) {
    speed.push(forecastDetailTrace(series, model, model, '#2563eb', false, 'dash'));
    direction.push(forecastDetailTrace(series, point => point.model_wd_deg?.[model], model, '#2563eb', true, 'dash'));
  }
  const hasModel = series.some(point => Number.isFinite(point[model]));
  const hasDirection = series.some(point => Number.isFinite(point.obs_wd_deg) || Number.isFinite(point.model_wd_deg?.[model]));
  document.getElementById('fcDetailStatus').textContent = !hasModel
    ? 'No archived forecast history for this station yet. Available observations are shown.'
    : !hasDirection ? 'This saved snapshot has no direction history yet; it becomes available after the next refresh.' : '';
  plotForecastDetail(speed, direction);
}

function openForecastChange(modelId) {
  const comparison = _forecastComparison;
  const model = comparison?.models?.find(item => item.model_id === modelId);
  if (!model) return;
  forecastDetailStation = null;
  openForecastDetail(modelId, 'Forecast change',
    `Previous: ${fcLocalTime(comparison.previous_computed_at_utc)} · Latest: ${fcLocalTime(comparison.current_computed_at_utc)}. Same model, matching forecast hours.`);
  document.getElementById('fcDetailModelLabel').hidden = true;
  const traces = direction => [
    forecastDetailTrace(model.hours, direction ? 'previous_wd_deg' : 'previous_ws_ms', 'Previous', '#64748b', direction, 'dash'),
    forecastDetailTrace(model.hours, direction ? 'current_wd_deg' : 'current_ws_ms', 'Latest', '#2563eb', direction),
  ];
  plotForecastDetail(traces(false), traces(true));
}

document.querySelectorAll('[data-forecast-view]').forEach((button, index, buttons) => {
  button.addEventListener('click', () => selectForecastView(button.dataset.forecastView));
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
    buttons[next].focus(); buttons[next].click();
  });
});
document.getElementById('fcExploreCharts').addEventListener('click', event => {
  event.preventDefault(); selectForecastView('charts'); document.getElementById('fcViewCharts').focus();
});
document.getElementById('fcChartView').addEventListener('change', () => { renderForecastWorkspace(); resizeForecastCharts(); });
document.getElementById('fcDetailModel').addEventListener('change', renderForecastStation);
document.getElementById('fcDetailClose').addEventListener('click', () => document.getElementById('fcDetailDialog').close());
document.getElementById('fcDetailDialog').addEventListener('click', event => {
  if (event.target !== event.currentTarget) return;
  const bounds = event.currentTarget.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.currentTarget.close();
});
selectForecastView('overview');
