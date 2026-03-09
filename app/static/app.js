
const MODEL_COLORS = ["#2563eb","#dc2626","#d97706","#7c3aed","#0891b2","#be185d","#ea580c","#0f766e"];

// ── DOM refs ─────────────────────────────────────────────────────────────────
const latInput       = document.getElementById("lat");
const lonInput       = document.getElementById("lon");
const radiusInput    = document.getElementById("radius");
const hoursBackInput = document.getElementById("hoursBack");
const runBtn         = document.getElementById("run");
const rankingBody    = document.querySelector("#ranking tbody");
const stationsList   = document.getElementById("stations");
const metaBlock      = document.getElementById("meta");
const windowInfo     = document.getElementById("window-info");
const timePlot       = document.getElementById("timePlot");
const dirPlot        = document.getElementById("dirPlot");
const chartStatus    = document.getElementById("chart-status");
const modelToggles   = document.getElementById("modelToggles");

// ── map ──────────────────────────────────────────────────────────────────────
const map = L.map("map").setView([50.5, 7.0], 5);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 12,
  attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);

const stationLayer = L.layerGroup().addTo(map);
const obsLayer     = L.layerGroup().addTo(map);
const fcLayer      = L.layerGroup().addTo(map);

let clickMarker = null;

map.on("click", (e) => {
  const { lat, lng } = e.latlng;
  latInput.value = lat.toFixed(4);
  lonInput.value = lng.toFixed(4);
  if (clickMarker) map.removeLayer(clickMarker);
  clickMarker = L.marker([lat, lng]).addTo(map);
});

// ── state ────────────────────────────────────────────────────────────────────
let latestSeries   = [];
let selectedModels = new Set();
let analysisMode   = "pin";  // "pin" | "obs"
let errorMode      = false;
let hoverIndex     = null;
const modelColorMap = new Map();

// ── tooltip ──────────────────────────────────────────────────────────────────
const tooltip = document.createElement("div");
tooltip.style.cssText = [
  "position:fixed", "pointer-events:none", "display:none",
  "background:#1e293b", "color:#f8fafc", "font-size:11px",
  "padding:6px 10px", "border-radius:6px", "line-height:1.7",
  "z-index:9999", "white-space:nowrap", "box-shadow:0 2px 8px rgba(0,0,0,.35)",
  "font-family:'Segoe UI',system-ui,sans-serif",
].join(";");
document.body.appendChild(tooltip);

function getModelColor(modelId) {
  if (!modelColorMap.has(modelId)) {
    modelColorMap.set(modelId, MODEL_COLORS[modelColorMap.size % MODEL_COLORS.length]);
  }
  return modelColorMap.get(modelId);
}

// ── wind barb helpers ────────────────────────────────────────────────────────
function windBarbSVG(wdDeg, wsMs, color) {
  const SIZE = 48, cx = SIZE / 2, cy = SIZE / 2;
  const kt = wsMs * 1.944;

  if (kt < 2.5) {
    return `<svg width="${SIZE}" height="${SIZE}" style="overflow:visible" xmlns="http://www.w3.org/2000/svg">
      <circle cx="${cx}" cy="${cy}" r="5"  fill="none" stroke="${color}" stroke-width="1.5"/>
      <circle cx="${cx}" cy="${cy}" r="9"  fill="none" stroke="${color}" stroke-width="1.5"/>
    </svg>`;
  }

  const STAFF = 22, BARB = 11, HALF = 6, SEP = 5;
  const tipY  = cy - STAFF;
  let elems = [];
  elems.push(`<line x1="${cx}" y1="${cy}" x2="${cx}" y2="${tipY}" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/>`);
  elems.push(`<circle cx="${cx}" cy="${cy}" r="2.5" fill="${color}"/>`);

  let rem = kt, offset = 0;
  while (rem >= 47.5) {
    const y0 = tipY + offset, y1 = y0 + SEP + 1;
    elems.push(`<polygon points="${cx},${y0} ${cx+BARB},${y0+(SEP+1)*0.5} ${cx},${y1}" fill="${color}"/>`);
    offset += SEP + 3; rem -= 50;
  }
  while (rem >= 7.5) {
    const y0 = tipY + offset;
    elems.push(`<line x1="${cx}" y1="${y0}" x2="${cx+BARB}" y2="${y0+BARB*0.35}" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/>`);
    offset += SEP; rem -= 10;
  }
  if (rem >= 2.5) {
    if (offset === 0) offset = SEP;
    const y0 = tipY + offset;
    elems.push(`<line x1="${cx}" y1="${y0}" x2="${cx+HALF}" y2="${y0+HALF*0.35}" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/>`);
  }

  return `<svg width="${SIZE}" height="${SIZE}" style="overflow:visible" xmlns="http://www.w3.org/2000/svg">
    <g transform="rotate(${wdDeg}, ${cx}, ${cy})">${elems.join("")}</g>
  </svg>`;
}

function windBarbMarker(lat, lon, wdDeg, wsMs, color) {
  return L.marker([lat, lon], {
    icon: L.divIcon({
      html: windBarbSVG(wdDeg, wsMs, color),
      className: "",
      iconSize: [48, 48],
      iconAnchor: [24, 24],
    }),
    zIndexOffset: 100,
  });
}

// ── model toggles ────────────────────────────────────────────────────────────
function populateModelToggles(series, winner) {
  modelToggles.innerHTML = "";
  selectedModels.clear();

  series.forEach((s) => {
    const color   = getModelColor(s.model_id);
    const isWinner = s.model_id === winner;
    if (isWinner) selectedModels.add(s.model_id);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "model-toggle" + (isWinner ? " active" : "");
    btn.style.setProperty("--mt-color", color);
    btn.dataset.modelId = s.model_id;
    btn.innerHTML = `<span class="mt-dot"></span>${s.model_id}${isWinner ? " ★" : ""}`;
    btn.addEventListener("click", () => {
      if (selectedModels.has(s.model_id)) {
        selectedModels.delete(s.model_id);
        btn.classList.remove("active");
      } else {
        selectedModels.add(s.model_id);
        btn.classList.add("active");
      }
      drawCharts();
    });
    modelToggles.appendChild(btn);
  });

  if (selectedModels.size === 0 && series.length > 0) {
    selectedModels.add(series[0].model_id);
    modelToggles.firstElementChild?.classList.add("active");
  }
}

// ── chart drawing ────────────────────────────────────────────────────────────
function setupCanvas(canvas, cssH) {
  const dpr  = window.devicePixelRatio || 1;
  const cssW = Math.max(300, Math.floor(canvas.parentElement.getBoundingClientRect().width - 28));
  canvas.width        = cssW * dpr;
  canvas.height       = cssH * dpr;
  canvas.style.width  = cssW + "px";
  canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cssW, cssH);
  return { ctx, cssW, cssH };
}

function drawLineChart(canvas, cssH, obsVals, modelLines, times, { minY, maxY, yTicks, yLabel }) {
  const { ctx, cssW } = setupCanvas(canvas, cssH);
  const pad = { left: 42, right: 10, top: 10, bottom: 36 };
  const W = cssW - pad.left - pad.right;
  const H = cssH - pad.top  - pad.bottom;
  const n = times.length;
  if (n === 0) return;

  const xAt = (i) => pad.left + (i / Math.max(1, n - 1)) * W;
  const yAt = (v) => pad.top  + (1 - (v - minY) / (maxY - minY)) * H;

  ctx.font = `11px "Segoe UI", system-ui, sans-serif`;

  // grid + y-axis labels
  yTicks.forEach((v, idx) => {
    const y = yAt(v);
    if (v === 0 && minY < 0) {
      ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1.5;
    } else {
      ctx.strokeStyle = idx === 0 ? "#cbd5e1" : "#e2e8f0"; ctx.lineWidth = 1;
    }
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + W, y); ctx.stroke();
    ctx.fillStyle   = "#64748b";
    ctx.textAlign   = "right";
    ctx.fillText(yLabel(v), pad.left - 4, y + 4);
  });

  // axes
  ctx.strokeStyle = "#cbd5e1"; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, pad.top + H);
  ctx.lineTo(pad.left + W, pad.top + H);
  ctx.stroke();

  // x-axis time ticks + labels
  const labelEvery = Math.max(1, Math.floor(n / 8));
  times.forEach((iso, i) => {
    if (i % labelEvery !== 0 && i !== n - 1) return;
    const x = xAt(i);
    ctx.strokeStyle = "#e2e8f0"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, pad.top + H); ctx.lineTo(x, pad.top + H + 4); ctx.stroke();
    const t = new Date(iso);
    const label = `${String(t.getUTCDate()).padStart(2,"0")}/${String(t.getUTCMonth()+1).padStart(2,"0")} ${String(t.getUTCHours()).padStart(2,"0")}:00`;
    ctx.fillStyle = "#64748b"; ctx.textAlign = "center";
    ctx.fillText(label, x, pad.top + H + 14);
  });

  function drawLine(vals, color, lineWidth) {
    ctx.strokeStyle = color;
    ctx.lineWidth   = lineWidth;
    ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    vals.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = xAt(i);
      const y = yAt(Math.max(minY, Math.min(maxY, v)));
      if (!started) { ctx.moveTo(x, y); started = true; }
      else          { ctx.lineTo(x, y); }
    });
    ctx.stroke();
  }

  // model lines (thinner, drawn first so obs is on top)
  modelLines.forEach(({ vals, color }) => drawLine(vals, color, 1.8));
  // obs line (slightly thicker, on top)
  if (obsVals.some(v => v != null)) drawLine(obsVals, "#16a34a", 2.2);

  // crosshair
  if (hoverIndex != null && hoverIndex >= 0 && hoverIndex < n) {
    const x = xAt(hoverIndex);
    ctx.strokeStyle = "#94a3b8";
    ctx.lineWidth   = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + H); ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawScatterChart(canvas, cssH, obsVals, modelLines, times, { minY, maxY, yTicks, yLabel }) {
  // Like drawLineChart but uses dots — avoids 0/360 wrap artifacts for direction
  const { ctx, cssW } = setupCanvas(canvas, cssH);
  const pad = { left: 42, right: 10, top: 10, bottom: 36 };
  const W = cssW - pad.left - pad.right;
  const H = cssH - pad.top  - pad.bottom;
  const n = times.length;
  if (n === 0) return;

  const xAt = (i) => pad.left + (i / Math.max(1, n - 1)) * W;
  const yAt = (v) => pad.top  + (1 - (v - minY) / (maxY - minY)) * H;

  ctx.font = `11px "Segoe UI", system-ui, sans-serif`;

  // grid + y-axis labels
  yTicks.forEach((v, idx) => {
    const y = yAt(v);
    ctx.strokeStyle = idx === 0 ? "#cbd5e1" : "#e2e8f0";
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + W, y); ctx.stroke();
    ctx.fillStyle   = "#64748b";
    ctx.textAlign   = "right";
    ctx.fillText(yLabel(v), pad.left - 4, y + 4);
  });

  // axes
  ctx.strokeStyle = "#cbd5e1"; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, pad.top + H);
  ctx.lineTo(pad.left + W, pad.top + H);
  ctx.stroke();

  // x-axis time ticks + labels
  const labelEvery = Math.max(1, Math.floor(n / 8));
  times.forEach((iso, i) => {
    if (i % labelEvery !== 0 && i !== n - 1) return;
    const x = xAt(i);
    ctx.strokeStyle = "#e2e8f0"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, pad.top + H); ctx.lineTo(x, pad.top + H + 4); ctx.stroke();
    const t = new Date(iso);
    const label = `${String(t.getUTCDate()).padStart(2,"0")}/${String(t.getUTCMonth()+1).padStart(2,"0")} ${String(t.getUTCHours()).padStart(2,"0")}:00`;
    ctx.fillStyle = "#64748b"; ctx.textAlign = "center";
    ctx.fillText(label, x, pad.top + H + 14);
  });

  function drawDots(vals, color, r) {
    ctx.fillStyle = color;
    vals.forEach((v, i) => {
      if (v == null) return;
      const x = xAt(i);
      const y = yAt(Math.max(minY, Math.min(maxY, v)));
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // model dots first, obs dots on top
  modelLines.forEach(({ vals, color }) => drawDots(vals, color + "cc", 2.5));
  if (obsVals.some(v => v != null)) drawDots(obsVals, "#16a34a", 3.5);

  // crosshair
  if (hoverIndex != null && hoverIndex >= 0 && hoverIndex < n) {
    const x = xAt(hoverIndex);
    ctx.strokeStyle = "#94a3b8";
    ctx.lineWidth   = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + H); ctx.stroke();
    ctx.setLineDash([]);
  }
}

function updateChartStatus() {
  if (!latestSeries.length) return;
  const pts = latestSeries[0].points;
  const obsVals = pts.map(p => p.obs_ws_ms);
  const hasObs = obsVals.some(v => v != null);

  if (!hasObs) {
    chartStatus.textContent = "No observation data in window — check live sources.";
    chartStatus.className = "chart-status warn";
    return;
  }

  const lastObsIdx = obsVals.reduce((best, v, i) => (v != null ? i : best), -1);
  if (lastObsIdx >= 0) {
    const lastObsTime = new Date(pts[lastObsIdx].time_utc);
    const ageH = (Date.now() - lastObsTime.getTime()) / 3_600_000;
    if (ageH > 6) {
      chartStatus.textContent = `Observations end ${ageH.toFixed(1)}h ago — NCEI ISD data has 24–72h publication delay.`;
      chartStatus.className = "chart-status warn";
      return;
    }
  }
  chartStatus.textContent = "";
  chartStatus.className = "chart-status";
}

function drawCharts() {
  if (!latestSeries.length) return;
  updateChartStatus();

  const wsField  = analysisMode === "pin" ? "model_ws_ms"  : "model_ws_ms_obs";
  const wdField  = analysisMode === "pin" ? "model_wd_deg" : "model_wd_deg_obs";
  const times    = latestSeries[0].points.map(p => p.time_utc);
  const MS_TO_KT = 1.94384;

  if (errorMode) {
    document.getElementById("speedLabel").textContent = "Speed error (kt)";
    document.getElementById("dirLabel").textContent   = "Dir error (°)";

    const speedErrLines = [], dirErrLines = [];
    for (const s of latestSeries) {
      if (!selectedModels.has(s.model_id)) continue;
      const color = getModelColor(s.model_id);
      speedErrLines.push({
        vals: s.points.map(p => {
          const obs = p.obs_ws_ms, mod = p[wsField];
          return (obs != null && mod != null) ? (mod - obs) * MS_TO_KT : null;
        }), color,
      });
      dirErrLines.push({
        vals: s.points.map(p => {
          const obs = p.obs_wd_deg, mod = p[wdField];
          if (obs == null || mod == null) return null;
          const diff = Math.abs(obs - mod) % 360;
          return diff > 180 ? 360 - diff : diff;
        }), color,
      });
    }

    // symmetric speed error axis
    const allErr = speedErrLines.flatMap(l => l.vals).filter(v => v != null);
    const absMax = allErr.length ? Math.max(Math.abs(Math.min(...allErr)), Math.abs(Math.max(...allErr))) : 5;
    const bound  = Math.max(absMax * 1.2, 2);
    const errStep = bound <= 5 ? 1 : bound <= 15 ? 2 : bound <= 30 ? 5 : 10;
    const errTicks = [];
    for (let v = -Math.ceil(bound / errStep) * errStep; v <= bound + errStep * 0.4; v += errStep) errTicks.push(+v.toFixed(6));

    drawLineChart(timePlot, 170, [], speedErrLines, times, {
      minY: errTicks[0], maxY: errTicks[errTicks.length - 1],
      yTicks: errTicks,
      yLabel: v => (v > 0 ? "+" : "") + v.toFixed(0),
    });

    drawLineChart(dirPlot, 130, [], dirErrLines, times, {
      minY: 0, maxY: 180,
      yTicks: [0, 45, 90, 135, 180],
      yLabel: v => v + "°",
    });
  } else {
    document.getElementById("speedLabel").textContent = "Wind speed (kt)";
    document.getElementById("dirLabel").textContent   = "Wind direction (°)";

    const obsWs = latestSeries[0].points.map(p => p.obs_ws_ms  != null ? p.obs_ws_ms  * MS_TO_KT : null);
    const obsWd = latestSeries[0].points.map(p => p.obs_wd_deg);

    const speedLines = [], dirLines = [];
    for (const s of latestSeries) {
      if (!selectedModels.has(s.model_id)) continue;
      const color = getModelColor(s.model_id);
      speedLines.push({ vals: s.points.map(p => p[wsField] != null ? p[wsField] * MS_TO_KT : null), color });
      dirLines.push({   vals: s.points.map(p => p[wdField]), color });
    }

    const allWs  = [...obsWs, ...speedLines.flatMap(l => l.vals)].filter(v => v != null);
    const rawMax = allWs.length ? Math.max(...allWs) : 20;
    const wsMax  = Math.max(rawMax * 1.2, 5);
    const step   = wsMax <= 10 ? 2 : wsMax <= 25 ? 5 : 10;
    const wsTicks = [];
    for (let v = 0; v <= wsMax + step * 0.5; v += step) wsTicks.push(v);

    drawLineChart(timePlot, 170, obsWs, speedLines, times, {
      minY: 0, maxY: wsTicks[wsTicks.length - 1],
      yTicks: wsTicks,
      yLabel: v => v.toFixed(0),
    });

    const compassLabel = v => ({ 0: "N", 90: "E", 180: "S", 270: "W", 360: "N" }[v] ?? v + "°");
    drawScatterChart(dirPlot, 130, obsWd, dirLines, times, {
      minY: 0, maxY: 360,
      yTicks: [0, 90, 180, 270, 360],
      yLabel: compassLabel,
    });
  }
}

// ── utility ──────────────────────────────────────────────────────────────────
function fmtUTC(iso) {
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,"0")}-${String(d.getUTCDate()).padStart(2,"0")} ${String(d.getUTCHours()).padStart(2,"0")}:00 UTC`;
}
function fmt2(v) { return v != null ? Number(v).toFixed(2) : "—"; }

// ── main validation ──────────────────────────────────────────────────────────
async function runValidation() {
  runBtn.disabled = true;
  runBtn.textContent = "Loading…";
  metaBlock.innerHTML = "";
  rankingBody.innerHTML = "";
  stationsList.innerHTML = "";
  modelToggles.innerHTML = "";
  chartStatus.textContent = "Fetching…";
  chartStatus.className = "chart-status";

  const payload = {
    lat:        Number(latInput.value),
    lon:        Number(lonInput.value),
    radius_km:  Number(radiusInput.value),
    hours_back: Number(hoursBackInput.value),
  };

  let data;
  try {
    const resp = await fetch("/v1/validate-point", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const txt = await resp.text();
      metaBlock.innerHTML = `<span class="meta-error">Error ${resp.status}: ${txt.slice(0, 200)}</span>`;
      return;
    }
    data = await resp.json();
  } catch (err) {
    metaBlock.innerHTML = `<span class="meta-error">Request failed: ${err.message}</span>`;
    return;
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = "Validate";
  }

  // ── window info ──
  windowInfo.textContent =
    `${fmtUTC(data.window_start_utc)} → ${fmtUTC(data.window_end_utc)} · ${payload.hours_back}h`;

  // ── meta block ──
  const obsCount  = data.observation_points.length;
  const srcLabels = data.source_provenance.length ? data.source_provenance.join(" + ") : "none";

  let obsAgeStr = "—", obsAgeWarn = false;
  if (obsCount > 0) {
    const latestMs = Math.max(...data.observation_points.map(p => new Date(p.time_utc).getTime()));
    const ageH = (Date.now() - latestMs) / 3_600_000;
    obsAgeStr  = ageH < 1 ? `${Math.round(ageH * 60)} min ago` : `${ageH.toFixed(1)}h ago`;
    obsAgeWarn = ageH > 6;
  }

  metaBlock.innerHTML = `
    <div class="meta-row"><span class="meta-label">Winner:</span> ${data.winner_model_id ?? "—"}</div>
    <div class="meta-row">
      <span class="meta-label">Obs sources:</span> ${srcLabels} (${obsCount} points) &nbsp;
      <span class="meta-label">Latest obs:</span>
      <span${obsAgeWarn ? ' style="color:#b45309"' : ""}>${obsAgeStr}</span>
    </div>
    <div class="meta-row"><span class="meta-label">Country:</span> ${data.stations_used[0]?.country ?? "—"} &nbsp; <span class="meta-label">Stations:</span> ${data.stations_used.length}</div>
    <div class="meta-row" style="color:#94a3b8;font-size:11px">Computed ${fmtUTC(data.computed_at_utc)}</div>
  `;

  // ── ranking table ──
  data.models.forEach((row) => {
    const tr = document.createElement("tr");
    if (row.model_id === data.winner_model_id) tr.className = "winner-row";
    const isWinner = row.model_id === data.winner_model_id;
    const badgeCls = row.status === "ok" ? "badge-ok" : row.status === "excluded" ? "badge-excl" : "badge-insuf";
    const note = row.reasons.join(", ");
    tr.innerHTML = `
      <td class="model-cell ${isWinner ? "winner-cell" : ""}">${row.model_id}${isWinner ? " ★" : ""}</td>
      <td>${fmt2(row.vector_rmse_uv)}</td>
      <td>${fmt2(row.rmse_ws)}</td>
      <td>${fmt2(row.bias_ws)}</td>
      <td>${row.n_samples}</td>
      <td><span class="badge ${badgeCls}" title="${note}">${row.status}</span></td>
    `;
    rankingBody.appendChild(tr);
  });

  // ── stations list ──
  stationLayer.clearLayers();
  data.stations_used.forEach((s) => {
    const li = document.createElement("li");
    li.className = "station-item";
    li.innerHTML = `
      <span class="station-id">${s.station_id}</span>
      <div>
        <span class="source-tag source-${s.source}">${s.source}</span>
        <div class="station-detail">${s.lat.toFixed(3)}, ${s.lon.toFixed(3)} &nbsp; ${s.elevation_m !== null ? s.elevation_m + "m" : ""}</div>
      </div>
    `;
    stationsList.appendChild(li);
    L.circleMarker([s.lat, s.lon], {
      radius: 4, color: "#475569", fillColor: "#94a3b8", fillOpacity: 0.7,
    })
      .bindPopup(`<b>${s.station_id}</b><br/>Source: ${s.source}<br/>${s.lat.toFixed(3)}, ${s.lon.toFixed(3)}`)
      .addTo(stationLayer);
  });

  // ── obs barbs ──
  obsLayer.clearLayers();
  if (obsCount === 0) {
    chartStatus.textContent = "No observation data returned — check live sources and logs.";
    chartStatus.className = "chart-status warn";
  }
  data.observation_points.forEach((obs) => {
    windBarbMarker(obs.lat, obs.lon, obs.wd_deg, obs.ws_ms, "#16a34a")
      .bindPopup(
        `<b>${obs.station_id}</b> (${obs.source})<br/>` +
        `${obs.ws_ms.toFixed(1)} m/s &nbsp; ${obs.wd_deg.toFixed(0)}° &nbsp; ` +
        `<span style="color:#64748b">${fmtUTC(obs.time_utc)}</span>`
      )
      .addTo(obsLayer);
  });

  // ── model barb at query point (winner) ──
  fcLayer.clearLayers();
  const qfc = data.query_point_forecast;
  if (qfc) {
    windBarbMarker(qfc.lat, qfc.lon, qfc.wd_deg, qfc.ws_ms, "#2563eb")
      .bindPopup(
        `<b>${qfc.model_id}</b> at query point<br/>` +
        `${qfc.ws_ms.toFixed(1)} m/s &nbsp; ${qfc.wd_deg.toFixed(0)}°<br/>` +
        `<span style="color:#64748b">${fmtUTC(qfc.time_utc)}</span>`
      )
      .addTo(fcLayer);
  }

  // ── charts ──
  latestSeries = data.time_series || [];
  modelColorMap.clear();
  populateModelToggles(latestSeries, data.winner_model_id);
  drawCharts();
}

// ── chart hover (crosshair + tooltip) ────────────────────────────────────────
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
    html += `<span style="color:#94a3b8;font-size:10px"> (model − obs)</span>`;
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
      if (errWd != null) html += ` &nbsp;${errWd.toFixed(0)}°`;
      html += `</span>`;
    }
  } else {
    if (pt.obs_ws_ms != null) {
      html += `<br><span style="color:#4ade80">Obs: ${(pt.obs_ws_ms * MS_TO_KT).toFixed(1)} kt`;
      if (pt.obs_wd_deg != null) html += ` &nbsp;${pt.obs_wd_deg.toFixed(0)}°`;
      html += `</span>`;
    }
    for (const s of latestSeries) {
      if (!selectedModels.has(s.model_id)) continue;
      const p = s.points[idx];
      const ws = p[wsField], wd = p[wdField];
      if (ws == null) continue;
      html += `<br><span style="color:${getModelColor(s.model_id)}">${s.model_id}: ${(ws * MS_TO_KT).toFixed(1)} kt`;
      if (wd != null) html += ` &nbsp;${wd.toFixed(0)}°`;
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

// ── event listeners ──────────────────────────────────────────────────────────
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
