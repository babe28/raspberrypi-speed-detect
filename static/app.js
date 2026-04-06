const statusEl = document.getElementById("status");
const ppmViewEl = document.getElementById("ppm-view");
const canvas = document.getElementById("snapshot-canvas");
const ctx = canvas.getContext("2d");
const modeBadgeEl = document.getElementById("mode-badge");
const roiCountEl = document.getElementById("roi-count");
const perspectiveCountEl = document.getElementById("perspective-count");
const scaleCountEl = document.getElementById("scale-count");
const eventLogBodyEl = document.getElementById("event-log-body");

const state = {
  config: null,
  image: new Image(),
  imageLoaded: false,
  mode: "pan",
  roiPoints: [],
  perspectivePoints: [],
  scalePoints: [],
};

state.image.addEventListener("load", () => {
  state.imageLoaded = true;
  canvas.width = state.image.naturalWidth || state.image.width;
  canvas.height = state.image.naturalHeight || state.image.height;
  drawCanvas();
});

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setMode(mode) {
  state.mode = mode;
  const labels = {
    pan: "閲覧モード",
    roi: "ROI入力中",
    perspective: "Perspective入力中",
    scale: "スケール入力中",
  };
  modeBadgeEl.textContent = labels[mode] || "モード未選択";
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
}

function readJsonInput(elementId, fallback = []) {
  const value = document.getElementById(elementId).value.trim();
  if (!value) {
    return fallback;
  }
  return JSON.parse(value);
}

function writeJson(elementId, value) {
  document.getElementById(elementId).value = JSON.stringify(value);
}

function roundPoint([x, y]) {
  return [Math.round(x), Math.round(y)];
}

function getValue(id) {
  return document.getElementById(id).value;
}

function setValue(id, value) {
  document.getElementById(id).value = value;
}

function getChecked(id) {
  return document.getElementById(id).checked;
}

function syncCounts() {
  roiCountEl.textContent = `${state.roiPoints.length}点`;
  perspectiveCountEl.textContent = `${state.perspectivePoints.length} / 4点`;
  scaleCountEl.textContent = `${state.scalePoints.length} / 2点`;
}

function syncTextareas() {
  writeJson("roi-polygon", state.roiPoints.map(roundPoint));
  writeJson("perspective-points", state.perspectivePoints.map(roundPoint));
  writeJson("scale-points", state.scalePoints.map(roundPoint));
  syncCounts();
}

function fillForm(config) {
  state.config = config;
  const processing = config.processing;

  setValue("camera-type", config.camera.type);
  setValue("camera-device", config.camera.device);
  setValue("camera-width", config.camera.resolution[0]);
  setValue("camera-height", config.camera.resolution[1]);
  setValue("camera-fps", config.camera.fps);
  setValue("downscale-factor", processing.downscale_factor);
  setValue("min-contour-area", processing.min_contour_area);
  setValue("max-contour-area", processing.max_contour_area);
  setValue("threshold-value", processing.threshold_value);
  setValue("max-speed-kmh", processing.max_speed_kmh);
  setValue("warmup-frames", processing.warmup_frames ?? 15);
  setValue("background-history", processing.background_history);
  setValue("background-var-threshold", processing.background_var_threshold);
  setValue("blur-kernel-size", processing.blur_kernel_size);
  setValue("morph-kernel-size", processing.morph_kernel_size);
  setValue("open-iterations", processing.open_iterations);
  setValue("dilate-iterations", processing.dilate_iterations);
  setValue("track-max-distance", processing.track_max_distance);
  setValue("track-max-missing-frames", processing.track_max_missing_frames);
  setValue("known-distance", config.scale.known_distance_m);
  document.getElementById("roi-enabled").checked = config.roi.enabled;
  document.getElementById("debug-mode").checked = processing.debug_mode;
  document.getElementById("exclude-blue-floor").checked = processing.exclude_blue_floor;

  const low = processing.blue_hsv_low || [90, 50, 40];
  const high = processing.blue_hsv_high || [135, 255, 255];
  setValue("blue-h-low", low[0]);
  setValue("blue-s-low", low[1]);
  setValue("blue-v-low", low[2]);
  setValue("blue-h-high", high[0]);
  setValue("blue-s-high", high[1]);
  setValue("blue-v-high", high[2]);

  state.roiPoints = (config.roi.polygon || []).map((point) => [...point]);
  state.perspectivePoints = (config.perspective.src_points || []).map((point) => [...point]);
  state.scalePoints = [];

  if (config.scale.pixel_distance > 0 && config.scale.known_distance_m > 0) {
    const y = 120;
    state.scalePoints = [[100, y], [100 + config.scale.pixel_distance, y]];
  }

  syncTextareas();
  ppmViewEl.textContent = `ppm: ${Number(config.scale.ppm || 0).toFixed(2)}`;
  drawCanvas();
}

function drawPoint(point, color, label) {
  ctx.beginPath();
  ctx.arc(point[0], point[1], 7, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 15px Segoe UI";
  ctx.fillText(label, point[0] + 10, point[1] - 10);
}

function drawPolygon(points, strokeStyle, fillStyle) {
  if (!points.length) {
    return;
  }
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  points.slice(1).forEach((point) => ctx.lineTo(point[0], point[1]));
  if (points.length >= 3) {
    ctx.closePath();
    ctx.fillStyle = fillStyle;
    ctx.fill();
  }
  ctx.strokeStyle = strokeStyle;
  ctx.lineWidth = 3;
  ctx.stroke();
}

function drawLine(points, color) {
  if (points.length < 2) {
    return;
  }
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  ctx.lineTo(points[1][0], points[1][1]);
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.stroke();
}

function drawCanvas() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (state.imageLoaded) {
    ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
  } else {
    ctx.fillStyle = "#d9e8e4";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#33504b";
    ctx.font = "600 24px Segoe UI";
    ctx.fillText("スナップショットを取得してください", 32, 48);
  }

  drawPolygon(state.roiPoints, "#00bcd4", "rgba(0, 188, 212, 0.16)");
  state.roiPoints.forEach((point, index) => drawPoint(point, "#00bcd4", `R${index + 1}`));

  drawPolygon(state.perspectivePoints, "#ffd166", "rgba(255, 209, 102, 0.14)");
  state.perspectivePoints.forEach((point, index) => drawPoint(point, "#ffd166", `P${index + 1}`));

  drawLine(state.scalePoints, "#ff6b6b");
  state.scalePoints.forEach((point, index) => drawPoint(point, "#ff6b6b", `S${index + 1}`));
}

function renderRecentEvents(events) {
  if (!events.length) {
    eventLogBodyEl.innerHTML = `
      <tr><td colspan="4" class="empty-row">まだ検知ログはありません。</td></tr>
    `;
    return;
  }
  eventLogBodyEl.innerHTML = events
    .map(
      (event) => `
        <tr>
          <td>${event.timestamp_label}</td>
          <td>${event.id}</td>
          <td>${Number(event.speed_kmh).toFixed(1)} km/h</td>
          <td>${event.center_x}, ${event.center_y}</td>
        </tr>
      `,
    )
    .join("");
}

function getCanvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return [(event.clientX - rect.left) * scaleX, (event.clientY - rect.top) * scaleY];
}

function addPoint(point) {
  if (state.mode === "roi") {
    state.roiPoints.push(point);
  } else if (state.mode === "perspective") {
    if (state.perspectivePoints.length >= 4) {
      state.perspectivePoints = [];
    }
    state.perspectivePoints.push(point);
  } else if (state.mode === "scale") {
    if (state.scalePoints.length >= 2) {
      state.scalePoints = [];
    }
    state.scalePoints.push(point);
  }
  syncTextareas();
  drawCanvas();
}

function clearCurrentModePoints() {
  if (state.mode === "roi") {
    state.roiPoints = [];
  } else if (state.mode === "perspective") {
    state.perspectivePoints = [];
  } else if (state.mode === "scale") {
    state.scalePoints = [];
  }
  syncTextareas();
  drawCanvas();
}

function clearAllOverlays() {
  state.roiPoints = [];
  state.perspectivePoints = [];
  state.scalePoints = [];
  syncTextareas();
  drawCanvas();
}

function syncFromTextarea(elementId, targetKey) {
  try {
    const parsed = readJsonInput(elementId, []);
    state[targetKey] = parsed.map((point) => [...point]);
    syncCounts();
    drawCanvas();
  } catch {
    setStatus(`${elementId} のJSONを解釈できませんでした。`, true);
  }
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error("設定の取得に失敗しました。");
  }
  fillForm(await response.json());
  setStatus("設定を読み込みました。");
}

async function loadRecentEvents() {
  if (document.hidden) {
    return;
  }
  const response = await fetch("/api/recent-events");
  if (!response.ok) {
    throw new Error("検知ログの取得に失敗しました。");
  }
  renderRecentEvents((await response.json()).events || []);
}

function buildProcessingPayload() {
  return {
    downscale_factor: Number(getValue("downscale-factor")),
    min_contour_area: Number(getValue("min-contour-area")),
    max_contour_area: Number(getValue("max-contour-area")),
    threshold_value: Number(getValue("threshold-value")),
    max_speed_kmh: Number(getValue("max-speed-kmh")),
    warmup_frames: Number(getValue("warmup-frames")),
    background_history: Number(getValue("background-history")),
    background_var_threshold: Number(getValue("background-var-threshold")),
    blur_kernel_size: Number(getValue("blur-kernel-size")),
    morph_kernel_size: Number(getValue("morph-kernel-size")),
    open_iterations: Number(getValue("open-iterations")),
    dilate_iterations: Number(getValue("dilate-iterations")),
    track_max_distance: Number(getValue("track-max-distance")),
    track_max_missing_frames: Number(getValue("track-max-missing-frames")),
    debug_mode: getChecked("debug-mode"),
    exclude_blue_floor: getChecked("exclude-blue-floor"),
    blue_hsv_low: [
      Number(getValue("blue-h-low")),
      Number(getValue("blue-s-low")),
      Number(getValue("blue-v-low")),
    ],
    blue_hsv_high: [
      Number(getValue("blue-h-high")),
      Number(getValue("blue-s-high")),
      Number(getValue("blue-v-high")),
    ],
  };
}

async function saveConfig() {
  const payload = {
    camera: {
      type: getValue("camera-type"),
      device: Number(getValue("camera-device")),
      resolution: [Number(getValue("camera-width")), Number(getValue("camera-height"))],
      fps: Number(getValue("camera-fps")),
    },
    roi: {
      enabled: getChecked("roi-enabled"),
      polygon: readJsonInput("roi-polygon", []),
    },
    processing: buildProcessingPayload(),
  };

  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error("設定の保存に失敗しました。");
  }
  fillForm(await response.json());
  setStatus("基本設定を保存しました。");
}

async function savePerspective() {
  const response = await fetch("/api/perspective", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ src_points: readJsonInput("perspective-points", []) }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Perspective保存に失敗しました。");
  }
  fillForm(data);
  setStatus("Perspective設定を保存しました。");
}

async function saveScale() {
  const response = await fetch("/api/calibrate/scale", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      points: readJsonInput("scale-points", []),
      known_distance_m: Number(getValue("known-distance")),
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "スケール計算に失敗しました。");
  }
  fillForm(data);
  setStatus("スケールを更新しました。");
}

async function takeSnapshot() {
  const response = await fetch("/api/snapshot");
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "スナップショット取得に失敗しました。");
  }
  await new Promise((resolve) => {
    state.image.onload = () => {
      state.imageLoaded = true;
      canvas.width = state.image.naturalWidth || state.image.width;
      canvas.height = state.image.naturalHeight || state.image.height;
      drawCanvas();
      resolve();
    };
    state.image.src = `data:image/jpeg;base64,${data.image_base64}`;
  });
  setStatus("スナップショットを取得しました。");
}

canvas.addEventListener("click", (event) => {
  if (state.mode === "pan") {
    return;
  }
  if (!state.imageLoaded) {
    setStatus("先にスナップショットを取得してください。", true);
    return;
  }
  addPoint(getCanvasPoint(event));
});

document.getElementById("reload-config").addEventListener("click", () => {
  loadConfig().catch((error) => setStatus(error.message, true));
});
document.getElementById("save-config").addEventListener("click", () => {
  saveConfig().catch((error) => setStatus(error.message, true));
});
document.getElementById("save-perspective").addEventListener("click", () => {
  savePerspective().catch((error) => setStatus(error.message, true));
});
document.getElementById("save-scale").addEventListener("click", () => {
  saveScale().catch((error) => setStatus(error.message, true));
});
document.getElementById("take-snapshot").addEventListener("click", () => {
  takeSnapshot().catch((error) => setStatus(error.message, true));
});
document.getElementById("clear-current-points").addEventListener("click", clearCurrentModePoints);
document.getElementById("clear-all-overlays").addEventListener("click", clearAllOverlays);

document.getElementById("mode-roi").dataset.mode = "roi";
document.getElementById("mode-perspective").dataset.mode = "perspective";
document.getElementById("mode-scale").dataset.mode = "scale";
document.getElementById("mode-pan").dataset.mode = "pan";
document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

document.getElementById("roi-polygon").addEventListener("change", () => {
  syncFromTextarea("roi-polygon", "roiPoints");
});
document.getElementById("perspective-points").addEventListener("change", () => {
  syncFromTextarea("perspective-points", "perspectivePoints");
});
document.getElementById("scale-points").addEventListener("change", () => {
  syncFromTextarea("scale-points", "scalePoints");
});

setMode("pan");
loadConfig().catch((error) => setStatus(error.message, true));
loadRecentEvents().catch((error) => setStatus(error.message, true));
window.setInterval(() => {
  loadRecentEvents().catch(() => {});
}, 3000);
