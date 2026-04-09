const monitorStatusEl = document.getElementById("monitor-status");
const latestEmptyEl = document.getElementById("monitor-latest-empty");
const latestCardEl = document.getElementById("monitor-latest-card");
const latestSpeedEl = document.getElementById("monitor-latest-speed");
const latestTimeEl = document.getElementById("monitor-latest-time");
const latestIdEl = document.getElementById("monitor-latest-id");
const latestModeEl = document.getElementById("monitor-latest-mode");
const latestPositionEl = document.getElementById("monitor-latest-position");
const officialAverageEl = document.getElementById("monitor-official-average");
const remainingDistanceEl = document.getElementById("monitor-remaining-distance");
const globalBiasEl = document.getElementById("monitor-global-bias");
const estimatedGoalEl = document.getElementById("monitor-estimated-goal");
const goalDeltaEl = document.getElementById("monitor-goal-delta");
const measurementPointEl = document.getElementById("monitor-measurement-point");

const state = {
  config: null,
  events: [],
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    throw new Error(data?.error || data?.details || "通信に失敗しました。");
  }
  return data;
}

function formatSeconds(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) {
    return "--";
  }
  const minutes = Math.floor(value / 60);
  const rest = value - (minutes * 60);
  return minutes > 0 ? `${minutes}:${rest.toFixed(3).padStart(6, "0")}` : value.toFixed(3);
}

function computeProjection(speedKmh) {
  const race = state.config?.measurement?.race_reference || {};
  const goalTimeSeconds = Number(race.goal_time_seconds || 0);
  const courseDistance = Number(race.course_distance_m || 0);
  const measurementPoint = Number(race.measurement_point_m || 0);
  const globalBias = Number(race.global_bias_kmh || 0);
  const biasEnabled = Boolean(race.bias_enabled);
  const speed = Number(speedKmh || 0);
  if (goalTimeSeconds <= 0 || courseDistance <= 0 || speed <= 0) {
    return null;
  }
  const point = Math.min(Math.max(0, measurementPoint), courseDistance);
  const avgSpeedMps = courseDistance / goalTimeSeconds;
  const adjustedSpeedMps = Math.max(0.1, speed + (biasEnabled ? globalBias : 0)) / 3.6;
  const estimatedGoalSeconds = (point / avgSpeedMps) + ((courseDistance - point) / adjustedSpeedMps);
  return {
    label: formatSeconds(estimatedGoalSeconds),
    deltaSeconds: estimatedGoalSeconds - goalTimeSeconds,
  };
}

function renderGoalReference() {
  const race = state.config?.measurement?.race_reference || {};
  const goalTimeSeconds = Number(race.goal_time_seconds || 0);
  const courseDistance = Number(race.course_distance_m || 0);
  const measurementPoint = Number(race.measurement_point_m || 0);
  const globalBias = Number(race.global_bias_kmh || 0);
  const biasEnabled = Boolean(race.bias_enabled);

  officialAverageEl.textContent = goalTimeSeconds > 0 && courseDistance > 0
    ? `${((courseDistance / goalTimeSeconds) * 3.6).toFixed(1)} km/h`
    : "--";
  remainingDistanceEl.textContent = courseDistance > 0
    ? `${Math.max(0, courseDistance - Math.max(0, measurementPoint)).toFixed(1)} m`
    : "--";
  globalBiasEl.textContent = biasEnabled
    ? `${globalBias >= 0 ? "+" : ""}${globalBias.toFixed(1)} km/h`
    : "OFF";
  measurementPointEl.textContent = courseDistance > 0
    ? `${measurementPoint.toFixed(1)} / ${courseDistance.toFixed(1)} m`
    : "--";

  const latest = state.events[0];
  const projection = latest ? computeProjection(latest.raw_speed_kmh ?? latest.speed_kmh) : null;
  estimatedGoalEl.textContent = projection ? projection.label : "--";
  goalDeltaEl.textContent = projection
    ? `${projection.deltaSeconds >= 0 ? "+" : ""}${projection.deltaSeconds.toFixed(3)} s`
    : "--";
}

function renderLatestLog() {
  const latest = state.events[0];
  if (!latest) {
    latestEmptyEl.hidden = false;
    latestCardEl.hidden = true;
    return;
  }

  latestEmptyEl.hidden = true;
  latestCardEl.hidden = false;
  latestSpeedEl.textContent = latest.speed_label || "--";
  latestTimeEl.textContent = latest.timestamp_label || "--";
  latestIdEl.textContent = String(latest.id ?? "--");
  latestModeEl.textContent = latest.mode === "line_crossing" ? "Line Crossing" : "Tracking";
  latestPositionEl.textContent = Number.isFinite(Number(latest.center_x)) && Number.isFinite(Number(latest.center_y))
    ? `${Number(latest.center_x).toFixed(0)}, ${Number(latest.center_y).toFixed(0)}`
    : "--";
}

async function loadConfig() {
  state.config = await fetchJson("/api/config");
  renderGoalReference();
}

async function loadRecentEvents() {
  const data = await fetchJson("/api/recent-events");
  state.events = Array.isArray(data.events) ? data.events : [];
  renderLatestLog();
  renderGoalReference();
}

async function refreshAll() {
  try {
    await Promise.all([loadConfig(), loadRecentEvents()]);
    monitorStatusEl.textContent = new Date().toLocaleTimeString("ja-JP", { hour12: false });
  } catch (error) {
    monitorStatusEl.textContent = error.message;
  }
}

refreshAll();
window.setInterval(() => {
  loadRecentEvents()
    .then(() => {
      monitorStatusEl.textContent = new Date().toLocaleTimeString("ja-JP", { hour12: false });
    })
    .catch((error) => {
      monitorStatusEl.textContent = error.message;
    });
}, 1500);
window.setInterval(() => {
  loadConfig().catch(() => {});
}, 5000);
