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
  document.getElementById("camera-type").value = config.camera.type;
  document.getElementById("camera-device").value = config.camera.device;
  document.getElementById("camera-width").value = config.camera.resolution[0];
  document.getElementById("camera-height").value = config.camera.resolution[1];
  document.getElementById("camera-fps").value = config.camera.fps;
  document.getElementById("downscale-factor").value = config.processing.downscale_factor;
  document.getElementById("min-contour-area").value = config.processing.min_contour_area;
  document.getElementById("max-speed-kmh").value = config.processing.max_speed_kmh;
  document.getElementById("warmup-frames").value = config.processing.warmup_frames ?? 15;
  document.getElementById("roi-enabled").checked = config.roi.enabled;
  document.getElementById("known-distance").value = config.scale.known_distance_m;

  state.roiPoints = (config.roi.polygon || []).map((point) => [...point]);
  state.perspectivePoints = (config.perspective.src_points || []).map((point) => [...point]);
  state.scalePoints = [];

  if (config.scale.pixel_distance > 0 && config.scale.known_distance_m > 0) {
    const y = 120;
    state.scalePoints = [
      [100, y],
      [100 + config.scale.pixel_distance, y],
    ];
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
  if (points.length === 0) {
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
      <tr>
        <td colspan="4" class="empty-row">まだ検知ログはありません。</td>
      </tr>
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
  const x = (event.clientX - rect.left) * scaleX;
  const y = (event.clientY - rect.top) * scaleY;
  return [x, y];
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
  } catch (error) {
    setStatus(`${elementId} のJSONを解釈できませんでした。`, true);
  }
}

async function loadConfig() {
  const response = await fetch("/api/config");
  if (!response.ok) {
    throw new Error("設定の取得に失敗しました。");
  }
  const config = await response.json();
  fillForm(config);
  setStatus("設定を読み込みました。");
}

async function loadRecentEvents() {
  const response = await fetch("/api/recent-events");
  if (!response.ok) {
    throw new Error("検知ログの取得に失敗しました。");
  }
  const payload = await response.json();
  renderRecentEvents(payload.events || []);
}

async function saveConfig() {
  const payload = {
    camera: {
      type: document.getElementById("camera-type").value,
      device: Number(document.getElementById("camera-device").value),
      resolution: [
        Number(document.getElementById("camera-width").value),
        Number(document.getElementById("camera-height").value),
      ],
      fps: Number(document.getElementById("camera-fps").value),
    },
    roi: {
      enabled: document.getElementById("roi-enabled").checked,
      polygon: readJsonInput("roi-polygon", []),
    },
    processing: {
      downscale_factor: Number(document.getElementById("downscale-factor").value),
      min_contour_area: Number(document.getElementById("min-contour-area").value),
      max_speed_kmh: Number(document.getElementById("max-speed-kmh").value),
      warmup_frames: Number(document.getElementById("warmup-frames").value),
    },
  };

  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error("設定の保存に失敗しました。");
  }
  const config = await response.json();
  fillForm(config);
  setStatus("基本設定を保存しました。");
}

async function savePerspective() {
  const payload = {
    src_points: readJsonInput("perspective-points", []),
  };

  const response = await fetch("/api/perspective", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Perspective保存に失敗しました。");
  }
  fillForm(data);
  setStatus("Perspective設定を保存しました。");
}

async function saveScale() {
  const payload = {
    points: readJsonInput("scale-points", []),
    known_distance_m: Number(document.getElementById("known-distance").value),
  };

  const response = await fetch("/api/calibrate/scale", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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

document.getElementById("clear-current-points").addEventListener("click", () => {
  clearCurrentModePoints();
});

document.getElementById("clear-all-overlays").addEventListener("click", () => {
  clearAllOverlays();
});

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
}, 1000);
