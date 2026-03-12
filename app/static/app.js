
const MODEL_COLORS = ["#2563eb","#dc2626","#d97706","#7c3aed","#0891b2","#be185d","#ea580c","#0f766e"];

  // â”€â”€ charts â”€â”€
  latestSeries = data.time_series || [];
  modelColorMap.clear();
  const runTimes = {};
  (data.models || []).forEach(m => { if (m.run_time_utc) runTimes[m.model_id] = m.run_time_utc; });
  populateModelToggles(latestSeries, data.winner_model_id, runTimes);
  drawCharts();

  // Pass params to forecast tab
  const winnerRow = (data.models || []).find(m => m.model_id === data.winner_model_id);
  if (typeof setForecastParams === 'function') {
    setForecastParams(
      data.lat,
      data.lon,
      data.winner_model_id ?? '',
      winnerRow?.bias_ws ?? 0,
    );
  }
}

// â”€â”€ chart hover (crosshair + tooltip) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const PAD_LEFT = 42, PAD_RIGHT = 10;

function onChartMouseMove(e) {
  if (!latestSeries.length) return;
  const n = latestSeries[0].points.length;
  if (n === 0) return;

  const rect = e.currentTarget.getBoundingClientRect();
  const W    = rect.width - PAD_LEFT - PAD_RIGHT;
  if (W <= 0) return;

  const x    = e.clientX - rect.left;
  const frac = Math.max(0, Math.min(1, (x - PAD_LEFT) / W));
  const idx  = Math.round(frac * (n - 1));

  const MS_TO_KT = 1.94384;
  const wsField  = analysisMode === "pin" ? "model_ws_ms"  : "model_ws_ms_obs";
  const wdField  = analysisMode === "pin" ? "model_wd_deg" : "model_wd_deg_obs";
  const pt = latestSeries[0].points[idx];
  const t  = new Date(pt.time_utc);
  const ts = `${String(t.getUTCDate()).padStart(2,"0")}/${String(t.getUTCMonth()+1).padStart(2,"0")} ${String(t.getUTCHours()).padStart(2,"0")}:00 UTC`;

  let html = `<b style="color:#94a3b8">${ts}</b>`;
  if (errorMode) {
    html += `<span style="color:#94a3b8;font-size:10px"> (model âˆ’ obs)</span>`;
    for (const s of latestSeries) {
      if (!selectedModels.has(s.model_id)) continue;
      const p = s.points[idx];
      const obsWs = p.obs_ws_ms, modWs = p[wsField];
      if (obsWs == null || modWs == null) continue;
      const errWs = (modWs - obsWs) * MS_TO_KT;
      let errWd = null;
      if (p.obs_wd_deg != null && p[wdField] != null) {
        const diff = Math.abs(p.obs_wd_deg - p[wdField]) % 360;
        errWd = diff > 180 ? 360 - diff : diff;
      }
      html += `<br><span style="color:${getModelColor(s.model_id)}">${s.model_id}: ${errWs > 0 ? "+" : ""}${errWs.toFixed(1)} kt`;
      if (errWd != null) html += ` &nbsp;${errWd.toFixed(0)}Â°`;
      html += `</span>`;
    }
  } else {
    if (pt.obs_ws_ms != null) {
      html += `<br><span style="color:#4ade80">Obs: ${(pt.obs_ws_ms * MS_TO_KT).toFixed(1)} kt`;
      if (pt.obs_wd_deg != null) html += ` &nbsp;${pt.obs_wd_deg.toFixed(0)}Â°`;
      html += `</span>`;
    }
    for (const s of latestSeries) {
      if (!selectedModels.has(s.model_id)) continue;
      const p = s.points[idx];
      const ws = p[wsField], wd = p[wdField];
      if (ws == null) continue;
      html += `<br><span style="color:${getModelColor(s.model_id)}">${s.model_id}: ${(ws * MS_TO_KT).toFixed(1)} kt`;
      if (wd != null) html += ` &nbsp;${wd.toFixed(0)}Â°`;
      html += `</span>`;
    }
  }

  tooltip.innerHTML = html;
  tooltip.style.display = "block";
  // keep tooltip within viewport
  const tipW = tooltip.offsetWidth || 190;
  const left = (e.clientX + 14 + tipW > window.innerWidth) ? e.clientX - tipW - 8 : e.clientX + 14;
  tooltip.style.left = left + "px";
  tooltip.style.top  = (e.clientY - 10) + "px";

  if (hoverIndex !== idx) {
    hoverIndex = idx;
    drawCharts();
  }
}

function onChartMouseLeave() {
  hoverIndex = null;
  tooltip.style.display = "none";
  drawCharts();
}

[timePlot, dirPlot].forEach(canvas => {
  canvas.addEventListener("mousemove",  onChartMouseMove);
  canvas.addEventListener("mouseleave", onChartMouseLeave);
  canvas.style.cursor = "crosshair";
});

// â”€â”€ event listeners â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
document.querySelectorAll('input[name="analysisMode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    analysisMode = radio.value;
    drawCharts();
  });
});

document.getElementById("errorModeToggle").addEventListener("change", (e) => {
  errorMode = e.target.checked;
  drawCharts();
});

window.addEventListener("resize", () => drawCharts());
runBtn.addEventListener("click", runValidation);

