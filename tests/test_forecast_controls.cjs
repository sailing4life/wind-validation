// Run with: node --test tests/test_forecast_controls.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the actual request handlers with a small DOM and mocked API.
function handler(file, name) {
  const source = readFileSync(path.join(__dirname, '../app/static', file), 'utf8');
  const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

function setup() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', disabled: false,
      classList: {contains: () => false, remove() {}, add() {}, toggle() {}},
    });
    return elements.get(id);
  };
  element('fcHoursAhead').value = '96';
  element('radius').value = '35';
  const calls = [], rendered = [];
  const context = vm.createContext({
    document: {getElementById: element},
    selectedLocationRecord: {id: 7, monitoring_enabled: true}, querySource: 'point',
    _forecastRequest: 0, snapshotRequest: 0, snapshotComputedAt: null,
    preserveManualForecast: false, preserveManualAnalysis: false, latestSeries: [],
    _winnerModelId: '', _biasWsMs: 0, _validationQueryId: '',
    currentLatLon: () => ({lat: 52.33, lon: 5.07}),
    fcLocalTime: iso => iso, fcAge: () => '2h ago',
    renderPreparedForecast: data => rendered.push(data),
    renderValidationResult() {}, renderNowStations() {}, renderBiasSummary() {},
    fetch: async (url, options) => {
      calls.push({url, options});
      return {ok: true, json: async () => ({models: [], hours_ahead: 96})};
    },
  });
  for (const name of ['beginManualForecast', 'showSavedForecast', 'loadLocationSnapshot']) {
    vm.runInContext(handler('app.js', name), context);
  }
  vm.runInContext(handler('forecast.js', 'loadForecast'), context);
  vm.runInContext(handler('forecast.js', 'fcArchiveNote'), context);
  return {context, calls, rendered, element};
}

test('followed and free locations both request the chosen forecast horizon', async () => {
  for (const followed of [true, false]) {
    const {context, calls, rendered, element} = setup();
    context.selectedLocationRecord.monitoring_enabled = followed;
    await context.loadForecast();
    assert.equal(calls[0].url, '/api/forecast');
    assert.equal(calls[0].options.method, 'POST');
    const body = JSON.parse(calls[0].options.body);
    assert.equal(body.hours_ahead, 96);
    assert.equal(body.radius_km, 35);
    assert.equal(rendered.length, 1);
    assert.equal(element('fcRunBtn').disabled, false);
    assert.match(element('fcFreshness').textContent, /On demand/);
    await context.loadLocationSnapshot();
    assert.equal(calls.length, 1, 'polling must not replace a manual forecast');
  }
});

test('an in-flight saved result cannot overwrite a manual forecast', async () => {
  const {context, rendered, element} = setup();
  let finish;
  context.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = context.loadLocationSnapshot();
  context.beginManualForecast();
  element('fcFreshness').textContent = 'On demand';
  finish({ok: true, json: async () => ({forecast: {}, validation: {}, computed_at_utc: 'old'})});
  await pending;
  assert.equal(rendered.length, 0);
  assert.equal(element('fcFreshness').textContent, 'On demand');
});

test('returning to saved forecasts cancels an in-flight manual result', async () => {
  const {context, rendered} = setup();
  let finish;
  context.fetch = () => new Promise(resolve => { finish = resolve; });
  const pending = context.loadForecast();
  const saved = {models: [], hours_ahead: 48};
  context.fetch = async () => ({ok: true, json: async () => ({
    forecast: saved, validation: {}, computed_at_utc: new Date().toISOString(), status: 'ready',
  })});
  await context.showSavedForecast();
  finish({ok: true, json: async () => ({hours_ahead: 96})});
  await pending;
  assert.deepEqual(rendered, [saved]);
  assert.equal(context.preserveManualForecast, false);
});

test('saved forecast age respects the configured collection interval', async () => {
  const {context, element} = setup();
  context.fetch = async () => ({ok: true, json: async () => ({
    forecast: {}, validation: {}, status: 'ready', refresh_interval_seconds: 10800,
    computed_at_utc: new Date(Date.now() - 2 * 3600000).toISOString(),
  })});
  await context.loadLocationSnapshot();
  assert.match(element('fcFreshness').textContent, /Following automatically · every 3h/);
});

test('a failed manual request leaves the button usable', async () => {
  const {context, element} = setup();
  context.fetch = async () => { throw new Error('Source unavailable'); };
  await context.loadForecast();
  assert.equal(element('fcRunBtn').disabled, false);
  assert.match(element('fcStatus').textContent, /Source unavailable/);
});

test('quota errors show the server explanation without replacing existing charts', async () => {
  const {context, element, rendered} = setup();
  context.fetch = async () => ({ok: false, status: 503,
    text: async () => JSON.stringify({detail: 'Open-Meteo is tijdelijk beperkt.'}),
  });
  await context.loadForecast();
  assert.equal(element('fcRunBtn').disabled, false);
  assert.equal(rendered.length, 0);
  assert.equal(element('fcStatus').textContent, 'Error: Open-Meteo is tijdelijk beperkt.');
});

test('an archived fallback displays its original fetch time and available horizon', async () => {
  const {context, element, rendered} = setup();
  context.fetch = async () => ({ok: true, json: async () => ({
    models: [{model_id: 'icon_eu', hours: []}],
    archive_fallbacks: [{model_id: 'icon_eu', fetched_at_utc: '2026-09-21T06:00:00Z',
      last_valid_time_utc: '2026-09-22T06:00:00Z'}],
  })});
  await context.loadForecast();
  assert.equal(rendered.length, 1);
  assert.match(element('fcFreshness').textContent, /Archived data: icon_eu/);
  assert.match(element('fcFreshness').textContent, /fetched 2026-09-21T06:00:00Z/);
  assert.match(element('fcFreshness').textContent, /available through 2026-09-22T06:00:00Z/);
});

test('Analyse + Forecast runs validation for a followed location', async () => {
  const {context, calls} = setup();
  Object.assign(context, {
    locationSelectionVersion: 1, runBtn: {}, metaBlock: {}, rankingBody: {},
    stationsList: {}, modelToggles: {}, chartStatus: {},
    latInput: {value: '52'}, lonInput: {value: '5'},
    radiusInput: {value: '35'}, hoursBackInput: {value: '48'},
    setInterval: () => 1, clearInterval() {}, updateSidebarAction() {},
  });
  let options;
  context.renderValidationResult = (data, opts) => { options = opts; };
  vm.runInContext(handler('app.js', 'runValidation'), context);
  await context.runValidation();
  assert.equal(calls[0].url, '/v1/validate-point');
  assert.equal(options.loadForecast, true);
  assert.equal(context.preserveManualForecast, true);
});

test('returning to saved during analysis prevents a late manual forecast', async () => {
  const {context} = setup();
  Object.assign(context, {
    locationSelectionVersion: 1, runBtn: {}, metaBlock: {}, rankingBody: {},
    stationsList: {}, modelToggles: {}, chartStatus: {},
    latInput: {value: '52'}, lonInput: {value: '5'},
    radiusInput: {value: '35'}, hoursBackInput: {value: '48'},
    setInterval: () => 1, clearInterval() {}, updateSidebarAction() {},
  });
  let finish;
  context.fetch = () => new Promise(resolve => { finish = resolve; });
  vm.runInContext(handler('app.js', 'runValidation'), context);
  const pending = context.runValidation();
  context.fetch = async () => ({ok: true, json: async () => ({status: 'pending'})});
  await context.showSavedForecast();
  let options;
  context.renderValidationResult = (data, opts) => { options = opts; };
  finish({ok: true, json: async () => ({})});
  await pending;
  assert.equal(options.loadForecast, false);
  assert.equal(options.updateNow, false);
});
