/* weather.js  -  Weather tab: consensus report + weather charts */

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

function wxSelectedModels() {
  if (!forecastData?.models?.length) return [];
  const active = _selectedModels && _selectedModels.size
    ? forecastData.models.filter(model => _selectedModels.has(model.model_id))
    : forecastData.models;
  return active.filter(model => Array.isArray(model.hours) && model.hours.length > 0);
}

function wxBuildConsensus(models) {
  const times = [...new Set(models.flatMap(model => model.hours.map(hour => hour.time_utc)))].sort();
  const modelMaps = models.map(model => ({
    model_id: model.model_id,
    hours: new Map(model.hours.map(hour => [hour.time_utc, hour])),
  }));

  return times.map(time_utc => {
    const rows = modelMaps.map(model => model.hours.get(time_utc)).filter(Boolean);
    const wsVals = rows.map(row => row.ws_ms);
    const gustVals = rows.map(row => row.gust_ms);
    const wdVals = rows.map(row => row.wd_deg);
    const tempVals = rows.map(row => row.temp_c);
    const precipVals = rows.map(row => row.precip_mm);
    const wdMean = wxCircularMean(wdVals);

    return {
      time_utc,
      ws_kt: wxMean(wsVals) != null ? wxMean(wsVals) * MS_TO_KT : null,
      gust_kt: wxMean(gustVals) != null ? wxMean(gustVals) * MS_TO_KT : null,
      wd_deg: wdMean,
      temp_c: wxMean(tempVals),
      precip_mm: wxMean(precipVals),
      ws_spread_kt: wxStd(wsVals) * MS_TO_KT,
      dir_spread_deg: wxCircularSpread(wdVals, wdMean),
      model_count: rows.length,
    };
  });
}

function wxConfidenceSummary(consensus) {
  const meanWsSpread = wxMean(consensus.map(row => row.ws_spread_kt)) ?? 0;
  const meanDirSpread = wxMean(consensus.map(row => row.dir_spread_deg)) ?? 0;
  const minModelCount = Math.min(...consensus.map(row => row.model_count).filter(Boolean));
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

  return {
    score,
    label,
    meanWsSpread,
    meanDirSpread,
    sentence,
  };
}

function wxBuildWeatherSummary(models, consensus) {
  if (!consensus.length) return null;

  const wsRows = consensus.filter(row => row.ws_kt != null);
  const gustRows = consensus.filter(row => row.gust_kt != null);
  const wdRows = consensus.filter(row => row.wd_deg != null);
  const tempRows = consensus.filter(row => row.temp_c != null);
  const precipRows = consensus.filter(row => row.precip_mm != null);
  const confidence = wxConfidenceSummary(consensus);

  const prevailingDir = wxCircularMean(wdRows.map(row => row.wd_deg));
  const earlyDir = wxCircularMean(wdRows.slice(0, Math.max(2, Math.floor(wdRows.length / 3))).map(row => row.wd_deg));
  const lateDir = wxCircularMean(wdRows.slice(-Math.max(2, Math.floor(wdRows.length / 3))).map(row => row.wd_deg));
  const shiftDelta = earlyDir != null && lateDir != null ? wxCircularDiff(earlyDir, lateDir) : 0;
  const shiftAbs = Math.abs(shiftDelta);
  let shiftLabel = 'Little change';
  let shiftNote = 'Direction stays fairly steady through the forecast window.';
  if (shiftAbs >= 15) {
    shiftLabel = shiftDelta > 0 ? `Veering ${shiftAbs.toFixed(0)} deg` : `Backing ${shiftAbs.toFixed(0)} deg`;
    shiftNote = shiftDelta > 0
      ? 'Direction trends clockwise through the period.'
      : 'Direction trends counter-clockwise through the period.';
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
  let weatherNote = 'No meaningful precipitation signal in the model consensus.';
  if (totalPrecip >= 0.2) {
    weatherLabel = totalPrecip >= 3 ? 'Showery' : 'Light rain risk';
    weatherNote = wettest
      ? `Wettest period looks near ${wxFmtHour(wettest.time_utc)} with around ${wettest.precip_mm.toFixed(1)} mm/h.`
      : 'Some precipitation is present in the forecast window.';
  }

  const report = [
    `Consensus points to ${confidence.label.toLowerCase()} confidence for a ${wxCardinal(prevailingDir)} flow, mostly ${windMin != null ? windMin.toFixed(0) : '-'}-${windMax != null ? windMax.toFixed(0) : '-'} kt. Gusts peak near ${gustMax != null ? gustMax.toFixed(0) : '-'} kt${strongest ? ` around ${wxFmtHour(strongest.time_utc)}` : ''}.`,
    `${shiftLabel}. ${shiftNote} Average model spread is ${confidence.meanWsSpread.toFixed(1)} kt in speed and ${confidence.meanDirSpread.toFixed(0)} deg in direction. ${confidence.sentence}`,
    `Temperatures run about ${tempMin != null ? tempMin.toFixed(1) : '-'} to ${tempMax != null ? tempMax.toFixed(1) : '-'} degC. ${weatherNote} Total modeled precipitation is about ${totalPrecip.toFixed(1)} mm for the loaded window.`,
    forecastData?.winner_model_id
      ? `Best validated model over the recent lookback is ${forecastData.winner_model_id}. Use that as the anchor, but keep the consensus in view for timing confidence.`
      : 'No validated winner model is currently available, so the report leans entirely on model consensus.'
  ].join('\n\n');

  return {
    confidence,
    report,
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
    statusEl.textContent = `Built from ${modelCount} active model${modelCount === 1 ? '' : 's'}.${winner}`;
  }
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

function renderWeatherTab() {
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
    document.getElementById('wxTempPanel').style.display = 'none';
    document.getElementById('wxPrecipPanel').style.display = 'none';
    return;
  }

  const models = wxSelectedModels();
  const consensus = wxBuildConsensus(models);
  const summary = wxBuildWeatherSummary(models, consensus);
  if (!summary) return;

  wxRenderCards(summary);
  wxRenderReport(summary, models.length);
  wxRenderTempChart(models, consensus);
  wxRenderPrecipChart(models, consensus);
}

async function wxEnsureForecastThenRender(forceReload = false) {
  if (!forecastData || forceReload) {
    const statusEl = document.getElementById('wxStatus');
    if (statusEl) statusEl.textContent = 'Loading forecast data...';
    await loadForecast();
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
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Report copied.`;
  } catch {
    if (statusEl) statusEl.textContent = `${statusEl.textContent} Copy failed in this browser.`;
  }
});
