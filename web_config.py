from __future__ import annotations

import base64
from collections import deque
from datetime import datetime
import logging
from threading import Lock
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


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/config")
def get_config() -> Response:
    return jsonify(config_manager.load())


@app.get("/api/recent-events")
def get_recent_events() -> Response:
    with recent_events_lock:
        return jsonify({"events": list(recent_events)})


@app.post("/api/config")
def save_config() -> Response:
    payload = request.get_json(force=True) or {}
    updated = config_manager.update(payload)
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
    with latest_snapshot_lock:
        cached = latest_snapshot_jpeg

    if cached is not None:
        encoded = base64.b64encode(cached).decode("ascii")
        return jsonify({"image_base64": encoded, "source": "stream-cache"})

    try:
        camera, _ = _camera_and_config()
    except RuntimeError as exc:
        return (
            jsonify(
                {
                    "error": "Camera open failed. Check camera.type and device.",
                    "details": str(exc),
                }
            ),
            503,
        )

    try:
        ok, frame = camera.read()
        if not ok or frame is None:
            return jsonify({"error": "Camera frame could not be captured."}), 503
        success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not success:
            return jsonify({"error": "Snapshot encode failed."}), 500
        encoded = base64.b64encode(buffer.tobytes()).decode("ascii")
        return jsonify({"image_base64": encoded, "source": "direct-camera"})
    finally:
        camera.stop()


@app.get("/stream")
def stream() -> Response:
    def generate() -> bytes:
        camera, config = _camera_and_config()
        estimator = SpeedEstimator(config)
        try:
            while True:
                ok, frame = camera.read()
                if not ok or frame is None:
                    break
                _store_latest_snapshot(frame)
                annotated, events = estimator.process(frame)
                _remember_events(events)
                success, buffer = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not success:
                    continue
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
                )
        finally:
            estimator.close()
            camera.stop()

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
