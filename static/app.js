const statusEl = document.getElementById("status");
const ppmViewEl = document.getElementById("ppm-view");
const snapshotImageEl = document.getElementById("snapshot-image");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function readJsonInput(elementId, fallback = []) {
  const value = document.getElementById(elementId).value.trim();
  if (!value) {
    return fallback;
  }
  return JSON.parse(value);
}

function fillForm(config) {
  document.getElementById("camera-type").value = config.camera.type;
  document.getElementById("camera-device").value = config.camera.device;
  document.getElementById("camera-width").value = config.camera.resolution[0];
  document.getElementById("camera-height").value = config.camera.resolution[1];
  document.getElementById("camera-fps").value = config.camera.fps;
  document.getElementById("downscale-factor").value = config.processing.downscale_factor;
  document.getElementById("min-contour-area").value = config.processing.min_contour_area;
  document.getElementById("max-speed-kmh").value = config.processing.max_speed_kmh;
  document.getElementById("roi-enabled").checked = config.roi.enabled;
  document.getElementById("roi-polygon").value = JSON.stringify(config.roi.polygon);
  document.getElementById("perspective-points").value = JSON.stringify(config.perspective.src_points);
  document.getElementById("known-distance").value = config.scale.known_distance_m;
  ppmViewEl.textContent = `ppm: ${Number(config.scale.ppm || 0).toFixed(2)}`;
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
  setStatus("設定を保存しました。");
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
  snapshotImageEl.src = `data:image/jpeg;base64,${data.image_base64}`;
  setStatus("スナップショットを取得しました。");
}

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

loadConfig().catch((error) => setStatus(error.message, true));
