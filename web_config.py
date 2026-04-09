from __future__ import annotations

import base64
import copy
from collections import deque
from datetime import datetime
import json
import logging
from pathlib import Path
from threading import Event, Lock, Thread
import time
from typing import Any

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request

from camera_manager import CameraManager
from config_manager import ConfigManager
from speed_estimator_core import SpeedEstimator

app = Flask(__name__)
config_manager = ConfigManager()
recent_events: deque[dict[str, Any]] = deque(maxlen=20)
recent_events_lock = Lock()
last_event_by_track: dict[int, dict[str, float]] = {}
latest_snapshot_frame: np.ndarray | None = None
latest_snapshot_lock = Lock()
latest_stream_frame_jpeg: bytes | None = None
latest_stream_frame_lock = Lock()
processor_thread: Thread | None = None
processor_lock = Lock()
processor_error: str | None = None
processor_started = False
processor_stop_event = Event()
processor_metrics: dict[str, Any] = {}
processor_metrics_lock = Lock()
diagnostic_frames_jpeg: dict[str, bytes] = {}
diagnostic_frames_lock = Lock()
event_history: deque[dict[str, Any]] = deque(maxlen=300)
event_history_lock = Lock()
PRESETS_PATH = Path("config_presets.json")
CSI_TUNING_DIR = Path("lens-json")


class SuppressRecentEventsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        suppressed_paths = (
            "/api/recent-events",
            "/api/processor-stats",
            "/api/diagnostics-frames",
        )
        return not any(path in message for path in suppressed_paths)


logging.getLogger("werkzeug").addFilter(SuppressRecentEventsFilter())


def _camera_and_config() -> tuple[CameraManager, dict[str, Any]]:
    config = config_manager.load()
    camera = CameraManager(config)
    camera.start()
    return camera, config


def _scale_points(points: list[Any], ratio: float) -> list[list[float]]:
    scaled: list[list[float]] = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            scaled.append([float(point[0]) * ratio, float(point[1]) * ratio])
    return scaled


def _perspective_output_size(
    config: dict[str, Any], snapshot_scale_ratio: float | None = None
) -> tuple[int, int]:
    frame = _latest_snapshot_frame()
    if frame is not None:
        width = frame.shape[1]
        height = frame.shape[0]
        if snapshot_scale_ratio is not None:
            width = max(1, int(round(width * snapshot_scale_ratio)))
            height = max(1, int(round(height * snapshot_scale_ratio)))
        return width, height

    base_width, base_height = config["camera"]["resolution"]
    downscale_factor = float(config["processing"]["downscale_factor"])
    return (
        max(1, int(round(base_width * downscale_factor))),
        max(1, int(round(base_height * downscale_factor))),
    )


def _recompute_perspective_matrix(
    config: dict[str, Any], output_size: tuple[int, int] | None = None
) -> None:
    points = config["perspective"].get("src_points", [])
    if len(points) != 4:
        config["perspective"]["homography_matrix"] = None
        return

    if output_size is None:
        output_width, output_height = _perspective_output_size(config)
    else:
        output_width, output_height = output_size
    src = np.array(points, dtype=np.float32)
    dst = np.array(
        [
            [0, 0],
            [output_width - 1, 0],
            [output_width - 1, output_height - 1],
            [0, output_height - 1],
        ],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    config["perspective"]["homography_matrix"] = matrix.tolist()


def _rescale_config_for_downscale(config: dict[str, Any], ratio: float) -> dict[str, Any]:
    if abs(ratio - 1.0) < 1e-6:
        return config

    config["roi"]["polygon"] = _scale_points(config["roi"].get("polygon", []), ratio)
    config["perspective"]["src_points"] = _scale_points(
        config["perspective"].get("src_points", []), ratio
    )
    config["scale"]["points"] = _scale_points(config["scale"].get("points", []), ratio)
    config["measurement"]["line_crossing"]["line_a"] = _scale_points(
        config["measurement"]["line_crossing"].get("line_a", []), ratio
    )
    config["measurement"]["line_crossing"]["line_b"] = _scale_points(
        config["measurement"]["line_crossing"].get("line_b", []), ratio
    )

    config["scale"]["pixel_distance"] = float(config["scale"].get("pixel_distance", 0.0)) * ratio
    known_distance_m = float(config["scale"].get("known_distance_m", 0.0))
    if known_distance_m > 0:
        config["scale"]["ppm"] = float(config["scale"]["pixel_distance"]) / known_distance_m

    _recompute_perspective_matrix(config, _perspective_output_size(config, ratio))
    return config


def _remember_events(events: list[dict[str, Any]]) -> None:
    now = time.time()
    with recent_events_lock:
        for event in events:
            if event.get("subdued", False):
                continue
            speed_kmh = float(event.get("speed_kmh", 0.0))
            if speed_kmh <= 0.1:
                continue

            track_id = int(event["id"])
            previous = last_event_by_track.get(track_id)
            if previous is not None:
                seconds_since_last = now - previous["timestamp"]
                speed_delta = abs(speed_kmh - previous["speed_kmh"])
                if seconds_since_last < 0.8 and speed_delta < 2.0:
                    continue

            center_x, center_y = event["centroid"]
            recent_events.appendleft(
                {
                    "timestamp": now,
                    "timestamp_label": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
                    "id": track_id,
                    "mode": str(event.get("mode", "tracking")),
                    "speed_kmh": round(speed_kmh, 1),
                    "raw_speed_kmh": round(float(event.get("raw_speed_kmh", speed_kmh)), 1),
                    "speed_px_s": round(float(event.get("speed_px_s", 0.0)), 1),
                    "speed_label": event.get("speed_label", f"{speed_kmh:.1f} km/h"),
                    "area": round(float(event.get("area", 0.0)), 1),
                    "estimated_goal_time_seconds": round(
                        float(event.get("estimated_goal_time_seconds", 0.0)), 3
                    )
                    if event.get("estimated_goal_time_seconds") is not None
                    else None,
                    "estimated_goal_time_label": event.get("estimated_goal_time_label", "--"),
                    "goal_time_delta_seconds": round(
                        float(event.get("goal_time_delta_seconds", 0.0)), 3
                    )
                    if event.get("goal_time_delta_seconds") is not None
                    else None,
                    "center_x": round(float(center_x), 1),
                    "center_y": round(float(center_y), 1),
                }
            )
            last_event_by_track[track_id] = {"timestamp": now, "speed_kmh": speed_kmh}
            with event_history_lock:
                event_history.appendleft(
                    {
                        "timestamp": now,
                        "speed_kmh": round(speed_kmh, 1),
                        "mode": str(event.get("mode", "tracking")),
                    }
                )


def _encode_jpeg(frame: np.ndarray | None, quality: int = 80) -> bytes | None:
    if frame is None:
        return None
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return None
    return buffer.tobytes()


def _store_processor_metrics(metrics: dict[str, Any], camera_info: dict[str, Any] | None = None) -> None:
    with processor_metrics_lock:
        processor_metrics.clear()
        processor_metrics.update(metrics)
        if camera_info:
            processor_metrics.update(camera_info)


def _store_diagnostic_frames(estimator: SpeedEstimator, annotated: np.ndarray) -> None:
    frames: dict[str, bytes] = {}
    encoded_raw = _encode_jpeg(estimator.latest_display_frame, 78)
    encoded_perspective = _encode_jpeg(estimator.latest_detection_frame, 78)
    encoded_mask = _encode_jpeg(estimator.latest_mask_frame, 82)
    encoded_annotated = _encode_jpeg(annotated, 80)
    if encoded_raw is not None:
        frames["raw"] = encoded_raw
    if encoded_perspective is not None:
        frames["perspective"] = encoded_perspective
    if encoded_mask is not None:
        frames["mask"] = encoded_mask
    if encoded_annotated is not None:
        frames["annotated"] = encoded_annotated
    with diagnostic_frames_lock:
        diagnostic_frames_jpeg.clear()
        diagnostic_frames_jpeg.update(frames)


def _recent_event_stats() -> dict[str, Any]:
    cutoff = time.time() - 60.0
    with event_history_lock:
        recent = [event for event in event_history if event["timestamp"] >= cutoff]
    count = len(recent)
    avg_speed = round(sum(event["speed_kmh"] for event in recent) / count, 1) if count else 0.0
    max_speed = round(max((event["speed_kmh"] for event in recent), default=0.0), 1)
    tracking_count = sum(1 for event in recent if event["mode"] == "tracking")
    line_crossing_count = sum(1 for event in recent if event["mode"] == "line_crossing")
    return {
        "last_minute_count": count,
        "last_minute_avg_speed": avg_speed,
        "last_minute_max_speed": max_speed,
        "tracking_count": tracking_count,
        "line_crossing_count": line_crossing_count,
    }


def _preset_store() -> dict[str, Any]:
    if not PRESETS_PATH.exists():
        return {"slots": {}}
    with PRESETS_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return {"slots": {}}
    slots = data.get("slots", {})
    if not isinstance(slots, dict):
        slots = {}
    return {"slots": slots}


def _save_preset_store(data: dict[str, Any]) -> None:
    with PRESETS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def _preset_summary() -> list[dict[str, Any]]:
    slots = _preset_store().get("slots", {})
    summary: list[dict[str, Any]] = []
    for slot in (1, 2, 3):
        entry = slots.get(str(slot))
        summary.append(
            {
                "slot": slot,
                "saved": isinstance(entry, dict) and isinstance(entry.get("config"), dict),
                "updated_at": entry.get("updated_at") if isinstance(entry, dict) else None,
            }
        )
    return summary


def _list_csi_tuning_files() -> list[str]:
    if not CSI_TUNING_DIR.exists() or not CSI_TUNING_DIR.is_dir():
        return []
    files = sorted(
        path.relative_to(Path.cwd()).as_posix()
        for path in CSI_TUNING_DIR.glob("*.json")
        if path.is_file()
    )
    return files


def _store_latest_snapshot(frame: np.ndarray) -> None:
    global latest_snapshot_frame
    with latest_snapshot_lock:
        latest_snapshot_frame = frame.copy()


def _store_latest_stream_frame(frame: np.ndarray, quality: int = 80) -> None:
    global latest_stream_frame_jpeg
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return
    with latest_stream_frame_lock:
        latest_stream_frame_jpeg = buffer.tobytes()


def _latest_snapshot_frame() -> np.ndarray | None:
    with latest_snapshot_lock:
        cached = latest_snapshot_frame
    if cached is None:
        return None
    return cached.copy()


def _build_perspective_preview() -> bytes | None:
    frame = _latest_snapshot_frame()
    if frame is None:
        return None

    config = config_manager.load()
    matrix_raw = config["perspective"].get("homography_matrix")
    if not matrix_raw:
        return None

    corrected = frame.copy()
    processing = config["processing"]
    camera_matrix_raw = config["calibration"].get("camera_matrix")
    dist_coeffs_raw = config["calibration"].get("dist_coeffs")
    if (
        processing.get("undistort_enabled", True)
        and camera_matrix_raw is not None
        and dist_coeffs_raw is not None
    ):
        camera_matrix = np.array(camera_matrix_raw, dtype=np.float32)
        dist_coeffs = np.array(dist_coeffs_raw, dtype=np.float32)
        corrected = cv2.undistort(corrected, camera_matrix, dist_coeffs)
    manual_distortion = float(processing.get("manual_distortion", 0.0))
    if processing.get("undistort_enabled", True) and abs(manual_distortion) > 1e-6:
        height, width = corrected.shape[:2]
        map_x, map_y = np.meshgrid(
            np.arange(width, dtype=np.float32),
            np.arange(height, dtype=np.float32),
        )
        cx = (width - 1) * 0.5
        cy = (height - 1) * 0.5
        nx = (map_x - cx) / max(cx, 1.0)
        ny = (map_y - cy) / max(cy, 1.0)
        r2 = (nx * nx) + (ny * ny)
        k = -0.35 * manual_distortion
        scale = 1.0 + (k * r2)
        src_x = (nx * scale * max(cx, 1.0)) + cx
        src_y = (ny * scale * max(cy, 1.0)) + cy
        corrected = cv2.remap(
            corrected,
            src_x.astype(np.float32),
            src_y.astype(np.float32),
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )

    matrix = np.array(matrix_raw, dtype=np.float32)
    if matrix.shape != (3, 3):
        return None

    preview = cv2.warpPerspective(corrected, matrix, (corrected.shape[1], corrected.shape[0]))
    success, buffer = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if not success:
        return None
    return buffer.tobytes()


def _processing_loop() -> None:
    global processor_error
    try:
        camera, config = _camera_and_config()
        estimator = SpeedEstimator(config)
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        processor_error = str(exc)
        return

    processor_error = None
    try:
        stream_frame_skip = max(0, int(config["processing"].get("stream_frame_skip", 0)))
        stream_quality = int(config["processing"].get("stream_jpeg_quality", 80))
        stream_counter = 0
        while not processor_stop_event.is_set():
            ok, frame = camera.read()
            if not ok or frame is None:
                processor_error = "Camera frame could not be read."
                time.sleep(0.5)
                continue

            _store_latest_snapshot(frame)
            annotated, events = estimator.process(frame)
            if stream_counter % (stream_frame_skip + 1) == 0:
                _store_latest_stream_frame(annotated, stream_quality)
            stream_counter += 1
            _store_diagnostic_frames(estimator, annotated)
            _store_processor_metrics(estimator.runtime_metrics(), camera.runtime_info())
            _remember_events(events)
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        processor_error = str(exc)
    finally:
        estimator.close()
        camera.stop()


def ensure_processor_started() -> None:
    global processor_thread, processor_started
    with processor_lock:
        if processor_started and processor_thread is not None and processor_thread.is_alive():
            return

        processor_stop_event.clear()
        processor_started = True
        processor_thread = Thread(target=_processing_loop, daemon=True, name="speed-processor")
        processor_thread.start()


def restart_processor() -> None:
    global processor_started
    with processor_lock:
        processor_stop_event.set()
        thread = processor_thread

    if thread is not None and thread.is_alive():
        thread.join(timeout=1.5)

    with processor_lock:
        processor_started = False

    ensure_processor_started()


def _json_error(message: str, status: int = 400, details: str | None = None) -> Response:
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return jsonify(payload), status


@app.get("/")
def index() -> str:
    ensure_processor_started()
    return render_template("index.html")


@app.get("/monitor")
def monitor() -> str:
    ensure_processor_started()
    return render_template("monitor.html")


@app.get("/api/config")
def get_config() -> Response:
    ensure_processor_started()
    return jsonify(config_manager.load())


@app.get("/api/recent-events")
def get_recent_events() -> Response:
    ensure_processor_started()
    with recent_events_lock:
        return jsonify({"events": list(recent_events)})


@app.post("/api/recent-events/clear")
def clear_recent_events() -> Response:
    with recent_events_lock:
        recent_events.clear()
        last_event_by_track.clear()
    with event_history_lock:
        event_history.clear()
    return jsonify({"status": "ok"})


@app.get("/api/processor-stats")
def get_processor_stats() -> Response:
    ensure_processor_started()
    with processor_metrics_lock:
        metrics = dict(processor_metrics)
    metrics.update(_recent_event_stats())
    metrics["processor_error"] = processor_error
    return jsonify(metrics)


@app.get("/api/diagnostics-frames")
def get_diagnostics_frames() -> Response:
    ensure_processor_started()
    with diagnostic_frames_lock:
        if not diagnostic_frames_jpeg:
            return _json_error("比較用フレームがまだ準備できていません。", 503)
        frames = {
            name: base64.b64encode(data).decode("ascii")
            for name, data in diagnostic_frames_jpeg.items()
        }
    return jsonify({"frames": frames})


@app.get("/api/presets")
def get_presets() -> Response:
    return jsonify({"presets": _preset_summary()})


@app.get("/api/csi-tuning-files")
def get_csi_tuning_files() -> Response:
    return jsonify({"files": _list_csi_tuning_files()})


@app.post("/api/presets/<int:slot>/save")
def save_preset(slot: int) -> Response:
    if slot not in {1, 2, 3}:
        return _json_error("プリセット番号は 1 から 3 を指定してください。", 400)
    payload = request.get_json(silent=True) or {}
    config_to_store = payload.get("config")
    if isinstance(config_to_store, dict):
        normalized = copy.deepcopy(config_manager.load())
        normalized.update(copy.deepcopy(config_to_store))
        config_manager._normalize(normalized)
        config_manager._validate(normalized)
        config_to_store = normalized
    else:
        config_to_store = config_manager.load()
    store = _preset_store()
    slots = store.setdefault("slots", {})
    slots[str(slot)] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": config_to_store,
    }
    _save_preset_store(store)
    return jsonify({"status": "ok", "presets": _preset_summary()})


@app.post("/api/presets/<int:slot>/load")
def load_preset(slot: int) -> Response:
    if slot not in {1, 2, 3}:
        return _json_error("プリセット番号は 1 から 3 を指定してください。", 400)
    store = _preset_store()
    entry = store.get("slots", {}).get(str(slot))
    if not isinstance(entry, dict) or not isinstance(entry.get("config"), dict):
        return _json_error("そのプリセットはまだ保存されていません。", 404)
    config_manager.save(entry["config"])
    restart_processor()
    return jsonify({"status": "ok", "config": config_manager.load(), "presets": _preset_summary()})


@app.post("/api/camera/reinitialize")
def reinitialize_camera() -> Response:
    restart_processor()
    return jsonify({"status": "ok", "message": "カメラを再初期化しました。"})


@app.post("/api/config")
def save_config() -> Response:
    try:
        payload = request.get_json(force=True) or {}
        before = config_manager.load()
        updated = config_manager.update(payload)
        before_downscale = float(before["processing"]["downscale_factor"])
        after_downscale = float(updated["processing"]["downscale_factor"])
        if before_downscale > 0 and abs(before_downscale - after_downscale) > 1e-6:
            ratio = after_downscale / before_downscale
            updated = _rescale_config_for_downscale(updated, ratio)
            config_manager.save(updated)
        restart_processor()
        return jsonify(updated)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:  # pragma: no cover - defensive API guard
        return _json_error("設定保存中にエラーが発生しました。", 500, str(exc))


@app.post("/api/calibrate/scale")
def calibrate_scale() -> Response:
    payload = request.get_json(force=True) or {}
    points = payload.get("points", [])
    try:
        known_distance_m = float(payload.get("known_distance_m", 0))
    except (TypeError, ValueError):
        return _json_error("既知距離は数値で入力してください。", 400)

    if len(points) != 2 or known_distance_m <= 0:
        return _json_error("2点と正の既知距離が必要です。", 400)

    x1, y1 = points[0]
    x2, y2 = points[1]
    pixel_distance = float(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    if pixel_distance <= 0:
        return _json_error("ピクセル距離は正の値である必要があります。", 400)

    ppm = pixel_distance / known_distance_m
    updated = config_manager.update(
        {
            "scale": {
                "known_distance_m": known_distance_m,
                "pixel_distance": pixel_distance,
                "ppm": ppm,
                "points": points,
            }
        }
    )
    restart_processor()
    return jsonify(updated)


@app.post("/api/perspective")
def save_perspective() -> Response:
    payload = request.get_json(force=True) or {}
    points = payload.get("src_points", [])
    if len(points) != 4:
        return _json_error("Perspective にはちょうど4点が必要です。", 400)

    config = config_manager.load()
    output_size = _perspective_output_size(config)
    config["perspective"]["src_points"] = points
    _recompute_perspective_matrix(config, output_size)

    updated = config_manager.update(
        {
            "perspective": {
                "src_points": points,
                "homography_matrix": config["perspective"]["homography_matrix"],
            }
        }
    )
    restart_processor()
    return jsonify(updated)


@app.get("/api/snapshot")
def snapshot() -> Response:
    ensure_processor_started()
    frame = _latest_snapshot_frame()
    if frame is not None:
        encoded_jpeg = _encode_jpeg(frame, 85)
        if encoded_jpeg is None:
            return (
                jsonify({"error": "Snapshot could not be encoded yet."}),
                503,
            )
        encoded = base64.b64encode(encoded_jpeg).decode("ascii")
        return jsonify({"image_base64": encoded, "source": "stream-cache"})

    return (
        jsonify(
            {
                "error": "Snapshot is not ready yet.",
                "details": processor_error or "Background processor is starting.",
            }
        ),
        503,
    )


@app.get("/api/perspective-preview")
def perspective_preview() -> Response:
    ensure_processor_started()
    preview = _build_perspective_preview()
    if preview is None:
        return (
            jsonify({"error": "Perspective preview is not ready yet."}),
            503,
        )
    encoded = base64.b64encode(preview).decode("ascii")
    return jsonify({"image_base64": encoded})


@app.get("/stream")
def stream() -> Response:
    ensure_processor_started()

    def generate() -> bytes:
        while True:
            with latest_stream_frame_lock:
                frame = latest_stream_frame_jpeg

            if frame is None:
                time.sleep(0.1)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
            time.sleep(0.03)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
