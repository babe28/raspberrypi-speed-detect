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
const goalTimeInputEl = document.getElementById("monitor-goal-time");
const courseDistanceInputEl = document.getElementById("monitor-course-distance");
const measurementPointInputEl = document.getElementById("monitor-measurement-point-input");
const globalBiasEnabledEl = document.getElementById("monitor-global-bias-enabled");
const globalBiasInputEl = document.getElementById("monitor-global-bias-input");
const saveGoalButtonEl = document.getElementById("monitor-save-goal");

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

function parseGoalTimeInput(raw) {
  const value = String(raw || "").trim();
  if (!value) {
    return 0;
  }
  if (value.includes(":")) {
    const parts = value.split(":");
    if (parts.length !== 2) {
      throw new Error("ゴールタイムは 12.345 または 1:12.345 形式で入力してください。");
    }
    const minutes = Number(parts[0]);
    const seconds = Number(parts[1]);
    if (!Number.isFinite(minutes) || !Number.isFinite(seconds) || minutes < 0 || seconds < 0) {
      throw new Error("ゴールタイムの形式が正しくありません。");
    }
    return (minutes * 60) + seconds;
  }
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) {
    throw new Error("ゴールタイムは 0 以上で入力してください。");
  }
  return seconds;
}

function computeProjection(speedKmh) {
  const race = state.config?.measurement?.race_reference || {};
  const goalTimeSeconds = (() => {
    try {
      return parseGoalTimeInput(goalTimeInputEl.value);
    } catch {
      return Number(race.goal_time_seconds || 0);
    }
  })();
  const courseDistance = Number(courseDistanceInputEl.value || race.course_distance_m || 0);
  const measurementPoint = Number(measurementPointInputEl.value || race.measurement_point_m || 0);
  const globalBias = Number(globalBiasInputEl.value || race.global_bias_kmh || 0);
  const biasEnabled = Boolean(globalBiasEnabledEl.checked);
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
  const race = state.config?.measurement?.race_reference || {};
  goalTimeInputEl.value = formatSeconds(race.goal_time_seconds || 0) === "--"
    ? ""
    : formatSeconds(race.goal_time_seconds || 0);
  courseDistanceInputEl.value = String(race.course_distance_m ?? 0);
  measurementPointInputEl.value = String(race.measurement_point_m ?? 0);
  globalBiasEnabledEl.checked = Boolean(race.bias_enabled);
  globalBiasInputEl.value = String(race.global_bias_kmh ?? 0);
  renderGoalReference();
}

async function saveGoalReference() {
  const goalTimeSeconds = parseGoalTimeInput(goalTimeInputEl.value);
  const courseDistance = Number(courseDistanceInputEl.value || 0);
  const measurementPoint = Number(measurementPointInputEl.value || 0);
  const globalBias = Number(globalBiasInputEl.value || 0);
  if (courseDistance < 0 || measurementPoint < 0) {
    throw new Error("距離は 0 以上で入力してください。");
  }
  if (courseDistance > 0 && measurementPoint > courseDistance) {
    throw new Error("計測点はコース距離以下にしてください。");
  }
  if (!Number.isFinite(globalBias) || globalBias < -2 || globalBias > 3) {
    throw new Error("グローバル補正は -2.0 から 3.0 で入力してください。");
  }

  state.config = await fetchJson("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      measurement: {
        race_reference: {
          goal_time_seconds: goalTimeSeconds,
          course_distance_m: courseDistance,
          measurement_point_m: measurementPoint,
          bias_enabled: globalBiasEnabledEl.checked,
          global_bias_kmh: globalBias,
        },
      },
    }),
  });
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
saveGoalButtonEl.addEventListener("click", () => {
  saveGoalReference()
    .then(() => {
      monitorStatusEl.textContent = "Saved";
    })
    .catch((error) => {
      monitorStatusEl.textContent = error.message;
    });
});
[
  goalTimeInputEl,
  courseDistanceInputEl,
  measurementPointInputEl,
  globalBiasEnabledEl,
  globalBiasInputEl,
].forEach((element) => {
  element.addEventListener("input", renderGoalReference);
  element.addEventListener("change", renderGoalReference);
});
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
