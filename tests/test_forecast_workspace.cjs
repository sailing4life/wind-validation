// node --test tests/test_forecast_workspace.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function sourceFunction(file, name) {
  const source = readFileSync(path.join(__dirname, '../app/static', file), 'utf8');
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

test('briefing accepts UTC, explicit offsets and legacy timestamps without shifting instants', () => {
  const ctx = vm.createContext({});
  vm.runInContext(sourceFunction('briefing.js', 'bfParseUtc'), ctx);
  const expected = Date.parse('2026-09-22T10:00:00Z');
  for (const value of ['2026-09-22T10:00:00Z', '2026-09-22T10:00:00+00:00',
    '2026-09-22T12:00:00+02:00', '2026-09-22T05:00:00-0500', '2026-09-22T10:00:00']) {
    assert.equal(ctx.bfParseUtc(value).getTime(), expected, value);
  }
});

test('briefing filters by instants across offset formats and allows the first hour as the end', () => {
  const fields = {bfRangeStart: {value: '0'}, bfRangeEnd: {value: '0'}};
  const ctx = vm.createContext({document: {getElementById: id => fields[id]}, forecastData: {
    models: [{hours: [{time_utc: '2026-09-22T10:00:00Z'}, {time_utc: '2026-09-22T11:00:00Z'}]}],
  }});
  for (const name of ['bfParseUtc', 'bfGetRangeTimes', 'bfFilterHours']) vm.runInContext(sourceFunction('briefing.js', name), ctx);
  const rows = [{time_utc: '2026-09-22T12:00:00+02:00'}, {time_utc: '2026-09-22T13:00:00+02:00'}];
  assert.deepEqual(ctx.bfFilterHours(rows), [rows[0]]);
});

test('direction curves keep both sides of north without drawing a 358 degree turn', () => {
  const ctx = vm.createContext({MS_TO_KT: 1.94384});
  vm.runInContext(sourceFunction('forecast_workspace.js', 'forecastDetailTrace'), ctx);
  const points = [{time_utc: 'a', direction: 359, speed: 5}, {time_utc: 'b', direction: 1, speed: 6}];
  const direction = ctx.forecastDetailTrace(points, 'direction', 'Observed', 'green', true);
  assert.deepEqual(Array.from(direction.y), [359, null, 1]);
  assert.deepEqual(Array.from(direction.x), ['a', 'b', 'b']);
  assert.equal(ctx.forecastDetailTrace(points, 'speed', 'Observed', 'green').y[0], 9.7192);
});

test('station drilldown compares only the selected station and selected model', () => {
  const fields = {fcDetailModel: {value: 'icon'}, fcDetailStatus: {textContent: ''}};
  let plotted;
  const ctx = vm.createContext({MS_TO_KT: 1.94384, forecastDetailStation: 'A',
    document: {getElementById: id => fields[id]},
    _forecastValidation: {station_series: [
      {station_id: 'B', points: [{time_utc: 'a', obs_ws_ms: 99, icon: 99}]},
      {station_id: 'A', points: [{time_utc: 'a', obs_ws_ms: 5, obs_wd_deg: 270, icon: 6, model_wd_deg: {icon: 280}}]},
    ]}, plotForecastDetail: (speed, direction) => { plotted = {speed, direction}; },
  });
  for (const name of ['forecastDetailTrace', 'renderForecastStation']) vm.runInContext(sourceFunction('forecast_workspace.js', name), ctx);
  ctx.renderForecastStation();
  assert.equal(plotted.speed[0].y[0], 5 * 1.94384);
  assert.equal(plotted.speed[1].y[0], 6 * 1.94384);
  assert.equal(plotted.direction[0].y[0], 270);
  assert.equal(plotted.direction[1].y[0], 280);
  ctx._forecastValidation.station_series = [];
  ctx._forecastValidation.observation_points = [{station_id: 'A', time_utc: 'a', ws_ms: 5, wd_deg: 270}];
  ctx.renderForecastStation();
  assert.equal(plotted.speed[0].y[0], 5 * 1.94384);
  assert.equal(plotted.speed[1].y.length, 0, 'missing station forecasts must not be replaced by a pin forecast');
  assert.match(fields.fcDetailStatus.textContent, /No archived forecast history/);
});
