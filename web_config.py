from __future__ import annotations

import base64
from collections import deque
from datetime import datetime
import logging
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
latest_snapshot_jpeg: bytes | None = None
latest_snapshot_lock = Lock()
latest_stream_frame_jpeg: bytes | None = None
latest_stream_frame_lock = Lock()
processor_thread: Thread | None = None
processor_lock = Lock()
processor_error: str | None = None
processor_started = False
processor_stop_event = Event()


class SuppressRecentEventsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "/api/recent-events" not in message


logging.getLogger("werkzeug").addFilter(SuppressRecentEventsFilter())


def _camera_and_config() -> tuple[CameraManager, dict[str, Any]]:
    config = config_manager.load()
    camera = CameraManager(config)
    camera.start()
    return camera, config


def _remember_events(events: list[dict[str, Any]]) -> None:
    now = time.time()
    with recent_events_lock:
        for event in events:
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
                    "speed_kmh": round(speed_kmh, 1),
                    "center_x": round(float(center_x), 1),
                    "center_y": round(float(center_y), 1),
                }
            )
            last_event_by_track[track_id] = {"timestamp": now, "speed_kmh": speed_kmh}


def _store_latest_snapshot(frame: np.ndarray) -> None:
    global latest_snapshot_jpeg
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not success:
        return
    with latest_snapshot_lock:
        latest_snapshot_jpeg = buffer.tobytes()


def _store_latest_stream_frame(frame: np.ndarray) -> None:
    global latest_stream_frame_jpeg
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not success:
        return
    with latest_stream_frame_lock:
        latest_stream_frame_jpeg = buffer.tobytes()


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
        while not processor_stop_event.is_set():
            ok, frame = camera.read()
            if not ok or frame is None:
                processor_error = "Camera frame could not be read."
                time.sleep(0.5)
                continue

            _store_latest_snapshot(frame)
            annotated, events = estimator.process(frame)
            _store_latest_stream_frame(annotated)
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


@app.get("/")
def index() -> str:
    ensure_processor_started()
    return render_template("index.html")


@app.get("/api/config")
def get_config() -> Response:
    ensure_processor_started()
    return jsonify(config_manager.load())


@app.get("/api/recent-events")
def get_recent_events() -> Response:
    ensure_processor_started()
    with recent_events_lock:
        return jsonify({"events": list(recent_events)})


@app.post("/api/config")
def save_config() -> Response:
    payload = request.get_json(force=True) or {}
    updated = config_manager.update(payload)
    restart_processor()
    return jsonify(updated)


@app.post("/api/calibrate/scale")
def calibrate_scale() -> Response:
    payload = request.get_json(force=True) or {}
    points = payload.get("points", [])
    known_distance_m = float(payload.get("known_distance_m", 0))

    if len(points) != 2 or known_distance_m <= 0:
        return jsonify({"error": "Two points and a positive distance are required."}), 400

    x1, y1 = points[0]
    x2, y2 = points[1]
    pixel_distance = float(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)
    if pixel_distance <= 0:
        return jsonify({"error": "Pixel distance must be positive."}), 400

    ppm = pixel_distance / known_distance_m
    updated = config_manager.update(
        {
            "scale": {
                "known_distance_m": known_distance_m,
                "pixel_distance": pixel_distance,
                "ppm": ppm,
            }
        }
    )
    return jsonify(updated)


@app.post("/api/perspective")
def save_perspective() -> Response:
    payload = request.get_json(force=True) or {}
    points = payload.get("src_points", [])
    if len(points) != 4:
        return jsonify({"error": "Exactly four points are required."}), 400

    src = np.array(points, dtype=np.float32)
    width = float(max(np.linalg.norm(src[1] - src[0]), 1.0))
    height = float(max(np.linalg.norm(src[3] - src[0]), 1.0))
    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)

    updated = config_manager.update(
        {
            "perspective": {
                "src_points": points,
                "homography_matrix": matrix.tolist(),
            }
        }
    )
    return jsonify(updated)


@app.get("/api/snapshot")
def snapshot() -> Response:
    ensure_processor_started()
    with latest_snapshot_lock:
        cached = latest_snapshot_jpeg

    if cached is not None:
        encoded = base64.b64encode(cached).decode("ascii")
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
