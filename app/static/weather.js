/* weather.js  -  Weather tab: consensus report + weather charts */

let wxRangeState = {
  total: 0,
  start: 0,
  end: 0,
};

function wxParseUtc(isoStr) {
  return new Date(isoStr.endsWith('Z') ? isoStr : `${isoStr}Z`);
}

function wxLocalISO(isoStr) {
  const d = wxParseUtc(isoStr);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function wxFmtHour(isoStr) {
  const d = wxParseUtc(isoStr);
  const pad = n => String(n).padStart(2, '0');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${pad(d.getDate())} ${months[d.getMonth()]} ${pad(d.getHours())}:00`;
}

function wxCardinal(deg) {
  if (deg == null) return '-';
  const dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return dirs[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
}

function wxCircularMean(values) {
  const valid = values.filter(v => v != null);
  if (!valid.length) return null;
  const s = valid.reduce((acc, value) => acc + Math.sin((value * Math.PI) / 180), 0);
  const c = valid.reduce((acc, value) => acc + Math.cos((value * Math.PI) / 180), 0);
  return ((Math.atan2(s, c) * 180) / Math.PI + 360) % 360;
}

function wxCircularDiff(fromDeg, toDeg) {
  return ((toDeg - fromDeg + 540) % 360) - 180;
}

function wxCircularSpread(values, meanDeg) {
  const valid = values.filter(v => v != null);
  if (!valid.length || meanDeg == null) return null;
  const diffs = valid.map(v => Math.abs(wxCircularDiff(meanDeg, v)));
  return diffs.reduce((acc, value) => acc + value, 0) / diffs.length;
}

function wxStd(values) {
  const valid = values.filter(v => v != null);
  if (valid.length < 2) return 0;
  const mean = valid.reduce((acc, value) => acc + value, 0) / valid.length;
  const variance = valid.reduce((acc, value) => acc + (value - mean) ** 2, 0) / (valid.length - 1);
  return Math.sqrt(variance);
}

function wxMean(values) {
  const valid = values.filter(v => v != null);
  if (!valid.length) return null;
  return valid.reduce((acc, value) => acc + value, 0) / valid.length;
}

function wxBaseHours() {
  return forecastData?.models?.[0]?.hours ?? [];
}

function wxSetWindowSummary() {
  const startLabel = document.getElementById('wxStartLabel');
  const endLabel = document.getElementById('wxEndLabel');
  const summary = document.getElementById('wxRangeSummary');
  const hours = wxBaseHours();
  if (!hours.length) {
    if (startLabel) startLabel.textContent = '-';
    if (endLabel) endLabel.textContent = '-';
    if (summary) summary.textContent = 'Full loaded forecast window';
    return;
  }

  const startHour = hours[wxRangeState.start];
  const endHour = hours[wxRangeState.end];
  if (startLabel) startLabel.textContent = wxFmtHour(startHour.time_utc);
  if (endLabel) endLabel.textContent = wxFmtHour(endHour.time_utc);
  if (summary) {
    summary.textContent = `${wxFmtHour(startHour.time_utc)} to ${wxFmtHour(endHour.time_utc)}  •  ${wxRangeState.end - wxRangeState.start + 1}h window`;
  }
}

function wxApplyRangeInputs(changed) {
  const startInput = document.getElementById('wxStartRange');
  const endInput = document.getElementById('wxEndRange');
  if (!startInput || !endInput) return;

  let start = parseInt(startInput.value, 10) || 0;
  let end = parseInt(endInput.value, 10) || 0;
  if (changed === 'start' && start > end) end = start;
  if (changed === 'end' && end < start) start = end;

  wxRangeState.start = start;
  wxRangeState.end = end;
  startInput.value = String(start);
  endInput.value = String(end);
  wxSetWindowSummary();
}

function wxInitRangeControls(forceReset = false) {
  const startInput = document.getElementById('wxStartRange');
  const endInput = document.getElementById('wxEndRange');
  const hours = wxBaseHours();
  if (!startInput || !endInput || !hours.length) {
    wxSetWindowSummary();
    return;
  }

  const max = hours.length - 1;
  startInput.min = '0';
  endInput.min = '0';
  startInput.max = String(max);
  endInput.max = String(max);

  const shouldReset = forceReset || wxRangeState.total !== hours.length || wxRangeState.end > max;
  if (shouldReset) {
    wxRangeState = { total: hours.length, start: 0, end: max };
  }

  startInput.value = String(wxRangeState.start);
  endInput.value = String(wxRangeState.end);

  if (!startInput.dataset.bound) {
    startInput.addEventListener('input', () => {
      wxApplyRangeInputs('start');
      renderWeatherTab();
    });
    startInput.dataset.bound = '1';
  }
  if (!endInput.dataset.bound) {
    endInput.addEventListener('input', () => {
      wxApplyRangeInputs('end');
      renderWeatherTab();
    });
    endInput.dataset.bound = '1';
  }

  wxApplyRangeInputs();
}

function wxWindowBounds() {
  const hours = wxBaseHours();
  if (!hours.length) return { startTime: null, endTime: null };
  return {
    startTime: hours[wxRangeState.start]?.time_utc ?? null,
    endTime: hours[wxRangeState.end]?.time_utc ?? null,
  };
}

function wxFilterHours(hours) {
  const { startTime, endTime } = wxWindowBounds();
  if (!startTime || !endTime) return hours;
  return hours.filter(hour => hour.time_utc >= startTime && hour.time_utc <= endTime);
}

function wxSelectedModels() {
  if (!forecastData?.models?.length) return [];
  const active = _selectedModels && _selectedModels.size
    ? forecastData.models.filter(model => _selectedModels.has(model.model_id))
    : forecastData.models;
  return active
    .map(model => ({ ...model, hours: wxFilterHours(model.hours || []) }))
    .filter(model => Array.isArray(model.hours) && model.hours.length > 0);
}

function wxBuildConsensus(models) {
  const times = [...new Set(models.flatMap(model => model.hours.map(hour => hour.time_utc)))].sort();
  const modelMaps = models.map(model => ({
    model_id: model.model_id,
    hours: new Map(model.hours.map(hour => [hour.time_utc, hour])),
  }));

  return times.map(time_utc => {
    const rows = modelMaps.map(model => model.hours.get(time_utc)).filter(Boolean);
    const wsMean = wxMean(rows.map(row => row.ws_ms));
    const gustMean = wxMean(rows.map(row => row.gust_ms));
    const tempMean = wxMean(rows.map(row => row.temp_c));
    const precipMean = wxMean(rows.map(row => row.precip_mm));
    const wdMean = wxCircularMean(rows.map(row => row.wd_deg));

    return {
      time_utc,
      ws_kt: wsMean != null ? wsMean * MS_TO_KT : null,
      gust_kt: gustMean != null ? gustMean * MS_TO_KT : null,
      wd_deg: wdMean,
      temp_c: tempMean,
      precip_mm: precipMean,
      ws_spread_kt: wxStd(rows.map(row => row.ws_ms)) * MS_TO_KT,
      dir_spread_deg: wxCircularSpread(rows.map(row => row.wd_deg), wdMean),
      model_count: rows.length,
    };
  });
}

function wxConfidenceSummary(consensus) {
  const meanWsSpread = wxMean(consensus.map(row => row.ws_spread_kt)) ?? 0;
  const meanDirSpread = wxMean(consensus.map(row => row.dir_spread_deg)) ?? 0;
  const modelCounts = consensus.map(row => row.model_count).filter(Boolean);
  const minModelCount = modelCounts.length ? Math.min(...modelCounts) : 0;

  let score = 100 - meanWsSpread * 14 - meanDirSpread * 1.1;
  if (minModelCount < 2) score -= 20;
  score = Math.max(12, Math.min(96, score));

  let label = 'High';
  let sentence = 'Model agreement is tight enough to lean on the consensus signal.';
  if (score < 75) {
    label = 'Medium';
    sentence = 'There is some spread in the timing and detail, so keep room for adjustment.';
  }
  if (score < 52) {
    label = 'Low';
    sentence = 'Model spread is meaningful, so treat the timing and exact values with caution.';
  }

  return { score, label, meanWsSpread, meanDirSpread, sentence };
}

function wxSegmentStats(rows) {
  if (!rows.length) return null;
  const windRows = rows.filter(row => row.ws_kt != null);
  const tempRows = rows.filter(row => row.temp_c != null);
  const precipRows = rows.filter(row => row.precip_mm != null);
  const gustRows = rows.filter(row => row.gust_kt != null);
  return {
    start: rows[0].time_utc,
    end: rows[rows.length - 1].time_utc,
    meanDir: wxCircularMean(rows.map(row => row.wd_deg)),
    minWind: windRows.length ? Math.min(...windRows.map(row => row.ws_kt)) : null,
    maxWind: windRows.length ? Math.max(...windRows.map(row => row.ws_kt)) : null,
    maxGust: gustRows.length ? Math.max(...gustRows.map(row => row.gust_kt)) : null,
    tempLow: tempRows.length ? Math.min(...tempRows.map(row => row.temp_c)) : null,
    tempHigh: tempRows.length ? Math.max(...tempRows.map(row => row.temp_c)) : null,
    precipTotal: precipRows.reduce((acc, row) => acc + (row.precip_mm || 0), 0),
  };
}

function wxWindowSegments(consensus) {
  if (!consensus.length) return [];
  const size = Math.max(1, Math.ceil(consensus.length / 3));
  const labels = ['Early', 'Mid', 'Late'];
  return labels.map((label, idx) => {
    const rows = consensus.slice(idx * size, (idx + 1) * size);
    if (!rows.length) return null;
    return { label, stats: wxSegmentStats(rows) };
  }).filter(Boolean);
}

function wxTimingLine(segment) {
  if (!segment?.stats) return '';
  const stats = segment.stats;
  const rainText = stats.precipTotal >= 0.2 ? `, rain signal ${stats.precipTotal.toFixed(1)} mm` : ', mainly dry';
  return `- ${segment.label} (${wxFmtHour(stats.start)} to ${wxFmtHour(stats.end)}): ${stats.minWind != null ? stats.minWind.toFixed(0) : '-'}-${stats.maxWind != null ? stats.maxWind.toFixed(0) : '-'} kt from ${wxCardinal(stats.meanDir)}${stats.maxGust != null ? `, gusts ${stats.maxGust.toFixed(0)} kt` : ''}${rainText}.`;
}

function wxBuildWeatherSummary(models, consensus) {
  if (!consensus.length) return null;

  const wsRows = consensus.filter(row => row.ws_kt != null);
  const gustRows = consensus.filter(row => row.gust_kt != null);
  const wdRows = consensus.filter(row => row.wd_deg != null);
  const tempRows = consensus.filter(row => row.temp_c != null);
  const precipRows = consensus.filter(row => row.precip_mm != null);
  const confidence = wxConfidenceSummary(consensus);
  const segments = wxWindowSegments(consensus);

  const prevailingDir = wxCircularMean(wdRows.map(row => row.wd_deg));
  const earlyDir = segments[0]?.stats?.meanDir ?? wxCircularMean(wdRows.slice(0, Math.max(2, Math.floor(wdRows.length / 3))).map(row => row.wd_deg));
  const lateDir = segments[segments.length - 1]?.stats?.meanDir ?? wxCircularMean(wdRows.slice(-Math.max(2, Math.floor(wdRows.length / 3))).map(row => row.wd_deg));
  const shiftDelta = earlyDir != null && lateDir != null ? wxCircularDiff(earlyDir, lateDir) : 0;
  const shiftAbs = Math.abs(shiftDelta);

  let shiftLabel = 'Little change';
  let shiftNote = 'Direction stays fairly steady through the selected window.';
  if (shiftAbs >= 15) {
    shiftLabel = shiftDelta > 0 ? `Veering ${shiftAbs.toFixed(0)} deg` : `Backing ${shiftAbs.toFixed(0)} deg`;
    shiftNote = shiftDelta > 0
      ? 'Direction trends clockwise through the window.'
      : 'Direction trends counter-clockwise through the window.';
  }

  const strongest = wsRows.reduce((best, row) => (best == null || row.ws_kt > best.ws_kt ? row : best), null);
  const wettest = precipRows.reduce((best, row) => (best == null || row.precip_mm > best.precip_mm ? row : best), null);
  const windMin = wsRows.length ? Math.min(...wsRows.map(row => row.ws_kt)) : null;
  const windMax = wsRows.length ? Math.max(...wsRows.map(row => row.ws_kt)) : null;
  const gustMax = gustRows.length ? Math.max(...gustRows.map(row => row.gust_kt)) : null;
  const tempMin = tempRows.length ? Math.min(...tempRows.map(row => row.temp_c)) : null;
  const tempMax = tempRows.length ? Math.max(...tempRows.map(row => row.temp_c)) : null;
  const totalPrecip = precipRows.reduce((acc, row) => acc + (row.precip_mm || 0), 0);

  let weatherLabel = 'Dry';
  let weatherNote = 'No meaningful precipitation signal in the selected window.';
  if (totalPrecip >= 0.2) {
    weatherLabel = totalPrecip >= 3 ? 'Showery' : 'Light rain risk';
    weatherNote = wettest
      ? `Wettest period looks near ${wxFmtHour(wettest.time_utc)} with around ${wettest.precip_mm.toFixed(1)} mm/h.`
      : 'Some precipitation is present in the selected window.';
  }

  const risks = [];
  if (confidence.score < 75) risks.push(`Model confidence is ${confidence.label.toLowerCase()} because average spread is ${confidence.meanWsSpread.toFixed(1)} kt and ${confidence.meanDirSpread.toFixed(0)} deg.`);
  if (shiftAbs >= 15) risks.push(`${shiftLabel}. ${shiftNote}`);
  if (gustMax != null && gustMax >= 22) risks.push(`Gust risk rises to about ${gustMax.toFixed(0)} kt${strongest ? ` near ${wxFmtHour(strongest.time_utc)}` : ''}.`);
  if (totalPrecip >= 0.2) risks.push(weatherNote);
  if (!risks.length) risks.push('No obvious hazard signal stands out in the selected window beyond normal short-term variability.');

  const timingLines = segments.map(wxTimingLine).filter(Boolean);
  const reportLines = [
    'HEADLINE',
    `${wxCardinal(prevailingDir)} flow, mostly ${windMin != null ? windMin.toFixed(0) : '-'}-${windMax != null ? windMax.toFixed(0) : '-'} kt, with ${confidence.label.toLowerCase()} confidence. Gusts top out near ${gustMax != null ? gustMax.toFixed(0) : '-'} kt${strongest ? ` around ${wxFmtHour(strongest.time_utc)}` : ''}.`,
    '',
    'TIMING',
    ...timingLines,
    '',
    'MODEL SIGNAL',
    `- Confidence score ${confidence.score.toFixed(0)}/100. ${confidence.sentence}`,
    `- Consensus mean direction is ${wxCardinal(prevailingDir)}${prevailingDir != null ? ` (${prevailingDir.toFixed(0)} deg)` : ''}. Winner model from validation: ${forecastData?.winner_model_id ?? 'none'}.`,
    '',
    'RISKS',
    ...risks.map(line => `- ${line}`),
    '',
    'TACTICAL TAKE',
    `- Use ${forecastData?.winner_model_id ?? 'the consensus'} as the anchor model, but watch the consensus spread more than the exact hour-to-hour value.`,
    `- Expect ${weatherLabel.toLowerCase()} conditions with temperatures around ${tempMin != null ? tempMin.toFixed(1) : '-'} to ${tempMax != null ? tempMax.toFixed(1) : '-'} degC and total precipitation near ${totalPrecip.toFixed(1)} mm.`,
  ];

  return {
    confidence,
    report: reportLines.join('\n'),
    cards: [
      {
        label: 'Consensus Wind',
        value: `${windMin != null ? windMin.toFixed(0) : '-'}-${windMax != null ? windMax.toFixed(0) : '-'} kt`,
        note: `${wxCardinal(prevailingDir)} ${prevailingDir != null ? prevailingDir.toFixed(0) : '-'} deg mean, gusts ${gustMax != null ? gustMax.toFixed(0) : '-'} kt`,
      },
      {
        label: 'Confidence',
        value: `${confidence.label} ${confidence.score.toFixed(0)}/100`,
        note: `Spread averages ${confidence.meanWsSpread.toFixed(1)} kt and ${confidence.meanDirSpread.toFixed(0)} deg.`,
      },
      {
        label: 'Shift Signal',
        value: shiftLabel,
        note: strongest ? `Strongest wind near ${wxFmtHour(strongest.time_utc)}.` : shiftNote,
      },
      {
        label: 'Weather',
        value: weatherLabel,
        note: `${tempMin != null ? tempMin.toFixed(1) : '-'} to ${tempMax != null ? tempMax.toFixed(1) : '-'} degC, total precip ${totalPrecip.toFixed(1)} mm.`,
      },
    ],
  };
}

function wxRenderCards(summary) {
  const grid = document.getElementById('wxFactsGrid');
  if (!grid || !summary) return;
  grid.innerHTML = summary.cards.map(card => `
    <article class="panel wx-card">
      <div class="wx-card-label">${card.label}</div>
      <div class="wx-card-value">${card.value}</div>
      <div class="wx-card-note">${card.note}</div>
    </article>
  `).join('');
}

function wxRenderReport(summary, modelCount) {
  const reportEl = document.getElementById('wxReportText');
  const statusEl = document.getElementById('wxStatus');
  if (reportEl && summary) reportEl.textContent = summary.report;
  if (statusEl) {
    const winner = forecastData?.winner_model_id ? ` Winner model: ${forecastData.winner_model_id}.` : '';
    statusEl.textContent = `Built from ${modelCount} active model${modelCount === 1 ? '' : 's'} in the selected window.${winner}`;
  }
}

function wxRenderWindChart(models, consensus) {
  const panel = document.getElementById('wxWindPanel');
  const chartDiv = document.getElementById('wxWindChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const speeds = series.hours.map(hour => hour.ws_ms != null ? +(hour.ws_ms * MS_TO_KT).toFixed(1) : null);
    if (!speeds.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: speeds,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.85,
    });
  });

  const consensusWind = consensus.map(row => row.ws_kt);
  if (consensusWind.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusWind,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  const consensusGust = consensus.map(row => row.gust_kt);
  if (consensusGust.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusGust,
      name: 'Consensus gust',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#f59e0b', width: 2, dash: 'dot' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { ...LIGHT_YAXIS('Wind (kt)') },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderDirChart(models, consensus) {
  const panel = document.getElementById('wxDirPanel');
  const chartDiv = document.getElementById('wxDirChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const dirs = series.hours.map(hour => hour.wd_deg != null ? +hour.wd_deg.toFixed(0) : null);
    if (!dirs.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: dirs,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.8,
    });
  });

  const consensusDir = consensus.map(row => row.wd_deg);
  if (consensusDir.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusDir,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 72, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: {
      title: { text: 'Direction (deg)', standoff: 16 },
      automargin: true,
      range: [0, 360],
      dtick: 90,
      gridcolor: '#e2e8f0',
      tickfont: { color: '#64748b' },
      tickvals: [0, 90, 180, 270, 360],
      ticktext: ['N', 'E', 'S', 'W', 'N'],
    },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderTempChart(models, consensus) {
  const panel = document.getElementById('wxTempPanel');
  const chartDiv = document.getElementById('wxTempChart');
  if (!panel || !chartDiv) return;

  const traces = [];
  models.forEach(series => {
    const temps = series.hours.map(hour => hour.temp_c);
    if (!temps.some(value => value != null)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: temps,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2.5 : 1.5 },
      opacity: 0.85,
    });
  });

  const consensusTemps = consensus.map(row => row.temp_c);
  if (consensusTemps.some(value => value != null)) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusTemps,
      name: 'Consensus',
      type: 'scatter',
      mode: 'lines',
      line: { color: '#0f172a', width: 3, dash: 'dash' },
    });
  }

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Temp (degC)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' } },
  }, { responsive: true, displayModeBar: false });
}

function wxRenderPrecipChart(models, consensus) {
  const panel = document.getElementById('wxPrecipPanel');
  const chartDiv = document.getElementById('wxPrecipChart');
  if (!panel || !chartDiv) return;

  const consensusPrecip = consensus.map(row => row.precip_mm);
  const hasConsensus = consensusPrecip.some(value => value != null && value > 0);
  const traces = [];

  if (hasConsensus) {
    traces.push({
      x: consensus.map(row => wxLocalISO(row.time_utc)),
      y: consensusPrecip,
      name: 'Consensus precip',
      type: 'bar',
      marker: { color: '#7dd3fc' },
    });
  }

  models.forEach(series => {
    const precip = series.hours.map(hour => hour.precip_mm);
    if (!precip.some(value => value != null && value > 0)) return;
    traces.push({
      x: series.hours.map(hour => wxLocalISO(hour.time_utc)),
      y: precip,
      name: series.model_id,
      type: 'scatter',
      mode: 'lines',
      line: { color: modelColor(series.model_id), width: series.model_id === forecastData?.winner_model_id ? 2 : 1.2 },
      opacity: 0.85,
    });
  });

  if (!traces.length) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = '';
  Plotly.newPlot(chartDiv, traces, {
    ...LIGHT_LAYOUT,
    height: 320,
    margin: { t: 20, b: 50, l: 55, r: 20 },
    legend: { orientation: 'h', x: 0, y: 1.12, font: { size: 10 } },
    xaxis: { ...LIGHT_XAXIS },
    yaxis: { title: 'Precip (mm/h)', gridcolor: '#e2e8f0', tickfont: { color: '#64748b' }, rangemode: 'tozero' },
    bargap: 0.15,
  }, { responsive: true, displayModeBar: false });
}

function wxHidePanels() {
  ['wxWindPanel', 'wxDirPanel', 'wxTempPanel', 'wxPrecipPanel'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

function renderWeatherTab(forceResetRange = false) {
  const reportEl = document.getElementById('wxReportText');
  const statusEl = document.getElementById('wxStatus');
  const factsEl = document.getElementById('wxFactsGrid');

  if (!forecastData?.models?.length) {
    if (statusEl) statusEl.textContent = 'Load forecast data to build the report.';
    if (reportEl) reportEl.textContent = 'Run validation, then load forecast data to generate a weather report from the active models.';
    if (factsEl) {
      factsEl.innerHTML = `
        <article class="panel wx-card">
          <div class="wx-card-label">Consensus Wind</div>
          <div class="wx-card-value">-</div>
          <div class="wx-card-note">Awaiting forecast data</div>
        </article>
      `;
    }
    wxHidePanels();
    wxSetWindowSummary();
    return;
  }

  wxInitRangeControls(forceResetRange);
  const models = wxSelectedModels();
  const consensus = wxBuildConsensus(models);
  const summary = wxBuildWeatherSummary(models, consensus);
  if (!summary) {
    wxHidePanels();
    return;
  }

  wxRenderCards(summary);
  wxRenderReport(summary, models.length);
  wxRenderWindChart(models, consensus);
  wxRenderDirChart(models, consensus);
  wxRenderTempChart(models, consensus);
  wxRenderPrecipChart(models, consensus);
}

async function wxEnsureForecastThenRender(forceReload = false) {
  if (!forecastData || forceReload) {
    const statusEl = document.getElementById('wxStatus');
    if (statusEl) statusEl.textContent = 'Loading forecast data...';
    await loadForecast();
    renderWeatherTab(true);
    return;
  }
  renderWeatherTab();
}

document.querySelector('.tab[data-tab="weather"]')?.addEventListener('click', async () => {
  await wxEnsureForecastThenRender();
});

document.getElementById('wxRefreshBtn')?.addEventListener('click', async () => {
  await wxEnsureForecastThenRender(true);
});

document.getElementById('wxCopyBtn')?.addEventListener('click', async () => {
  const report = document.getElementById('wxReportText')?.textContent?.trim();
  const statusEl = document.getElementById('wxStatus');
  if (!report) return;
  try {
    await navigator.clipboard.writeText(report);
    renderWeatherTab();
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Report copied.`;
  } catch {
    renderWeatherTab();
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Copy failed in this browser.`;
  }
});
