from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass
class Track:
    track_id: int
    centroid: tuple[float, float]
    timestamp: float
    missed_frames: int = 0
    speed_kmh: float = 0.0
    history: list[tuple[float, float, float]] = field(default_factory=list)


class SpeedEstimator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        processing = config["processing"]
        self.scale_ppm = float(config["scale"]["ppm"])
        self.max_speed_kmh = float(processing["max_speed_kmh"])
        self.min_contour_area = int(processing["min_contour_area"])
        self.track_max_distance = float(processing["track_max_distance"])
        self.track_max_missing_frames = int(processing["track_max_missing_frames"])
        self.warmup_frames = int(processing["warmup_frames"])
        self.frame_index = 0
        self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=int(processing["background_history"]),
            varThreshold=float(processing["background_var_threshold"]),
            detectShadows=False,
        )
        self.roi_mask: np.ndarray | None = None
        self.homography_matrix = self._as_matrix(config["perspective"]["homography_matrix"])
        self.camera_matrix = self._as_matrix(config["calibration"]["camera_matrix"])
        self.dist_coeffs = self._as_vector(config["calibration"]["dist_coeffs"])
        self.tracks: dict[int, Track] = {}
        self.next_track_id = 1
        self.csv_writer: csv.writer | None = None
        self.csv_handle = None
        self._setup_logging()

    def process(self, frame: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
        corrected = self._apply_corrections(frame)
        mask = self._motion_mask(corrected)
        self.frame_index += 1
        if self.frame_index <= self.warmup_frames:
            annotated = self._annotate(corrected, mask, [])
            return annotated, []
        detections = self._find_detections(mask)
        events = self._update_tracks(detections)
        annotated = self._annotate(corrected, mask, events)
        return annotated, events

    def close(self) -> None:
        if self.csv_handle is not None:
            self.csv_handle.close()
            self.csv_handle = None

    def _apply_corrections(self, frame: np.ndarray) -> np.ndarray:
        corrected = frame.copy()
        if self.camera_matrix is not None and self.dist_coeffs is not None:
            corrected = cv2.undistort(corrected, self.camera_matrix, self.dist_coeffs)

        if self.homography_matrix is not None:
            corrected = cv2.warpPerspective(
                corrected,
                self.homography_matrix,
                (corrected.shape[1], corrected.shape[0]),
            )
        return corrected

    def _motion_mask(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        fg_mask = self.background_subtractor.apply(gray)

        if self.roi_mask is None or self.roi_mask.shape != fg_mask.shape:
            self.roi_mask = self._build_roi_mask(fg_mask.shape)

        fg_mask = cv2.bitwise_and(fg_mask, fg_mask, mask=self.roi_mask)
        _, fg_mask = cv2.threshold(fg_mask, 180, 255, cv2.THRESH_BINARY)
        fg_mask = cv2.medianBlur(fg_mask, 5)
        kernel = np.ones((3, 3), np.uint8)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)
        return fg_mask

    def _find_detections(self, mask: np.ndarray) -> list[dict[str, Any]]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[dict[str, Any]] = []

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.min_contour_area:
                continue

            x, y, w, h = cv2.boundingRect(contour)
            centroid = (x + w / 2.0, y + h / 2.0)
            detections.append(
                {
                    "bbox": (x, y, w, h),
                    "centroid": centroid,
                    "area": area,
                }
            )

        return detections

    def _update_tracks(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        matched_track_ids: set[int] = set()
        events: list[dict[str, Any]] = []

        for detection in detections:
            track = self._find_nearest_track(detection["centroid"])
            if track is None:
                track = Track(
                    track_id=self.next_track_id,
                    centroid=detection["centroid"],
                    timestamp=now,
                    history=[(detection["centroid"][0], detection["centroid"][1], now)],
                )
                self.tracks[track.track_id] = track
                self.next_track_id += 1
            else:
                speed = self._estimate_speed(track, detection["centroid"], now)
                track.centroid = detection["centroid"]
                track.timestamp = now
                track.missed_frames = 0
                track.speed_kmh = speed
                track.history.append((detection["centroid"][0], detection["centroid"][1], now))
                track.history = track.history[-6:]

            matched_track_ids.add(track.track_id)
            event = {
                "id": track.track_id,
                "bbox": detection["bbox"],
                "centroid": track.centroid,
                "speed_kmh": track.speed_kmh,
            }
            events.append(event)
            self._log_event(event, now)

        for track_id, track in list(self.tracks.items()):
            if track_id not in matched_track_ids:
                track.missed_frames += 1
                if track.missed_frames > self.track_max_missing_frames:
                    self.tracks.pop(track_id, None)

        return events

    def _estimate_speed(
        self, track: Track, new_centroid: tuple[float, float], now: float
    ) -> float:
        if self.scale_ppm <= 0:
            return 0.0

        dt = now - track.timestamp
        if dt <= 0:
            return track.speed_kmh

        dx = new_centroid[0] - track.centroid[0]
        dy = new_centroid[1] - track.centroid[1]
        distance_pixels = math.hypot(dx, dy)
        meters = distance_pixels / self.scale_ppm
        kmh = meters / dt * 3.6
        if kmh > self.max_speed_kmh:
            return track.speed_kmh
        return kmh

    def _find_nearest_track(self, centroid: tuple[float, float]) -> Track | None:
        nearest: Track | None = None
        nearest_distance = self.track_max_distance

        for track in self.tracks.values():
            distance = math.hypot(track.centroid[0] - centroid[0], track.centroid[1] - centroid[1])
            if distance < nearest_distance:
                nearest = track
                nearest_distance = distance

        return nearest

    def _annotate(
        self, frame: np.ndarray, mask: np.ndarray, events: list[dict[str, Any]]
    ) -> np.ndarray:
        annotated = frame.copy()

        roi_points = self.config["roi"]["polygon"]
        if self.config["roi"]["enabled"] and len(roi_points) >= 3:
            pts = np.array(roi_points, dtype=np.int32)
            cv2.polylines(annotated, [pts], isClosed=True, color=(255, 255, 0), thickness=2)

        perspective_points = self.config["perspective"]["src_points"]
        if len(perspective_points) == 4:
            pts = np.array(perspective_points, dtype=np.int32)
            cv2.polylines(annotated, [pts], isClosed=True, color=(0, 255, 255), thickness=2)

        for event in events:
            x, y, w, h = event["bbox"]
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 220, 0), 2)
            label = f"ID {event['id']} {event['speed_kmh']:.1f} km/h"
            cv2.putText(
                annotated,
                label,
                (x, max(20, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 220, 0),
                2,
                cv2.LINE_AA,
            )

        mask_preview = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        preview_h = max(1, annotated.shape[0] // 4)
        preview_w = max(1, annotated.shape[1] // 4)
        mask_preview = cv2.resize(mask_preview, (preview_w, preview_h))
        annotated[:preview_h, :preview_w] = mask_preview
        return annotated

    def _build_roi_mask(self, shape: tuple[int, int]) -> np.ndarray:
        height, width = shape
        mask = np.ones((height, width), dtype=np.uint8) * 255
        roi_points = self.config["roi"]["polygon"]
        if self.config["roi"]["enabled"] and len(roi_points) >= 3:
            mask = np.zeros((height, width), dtype=np.uint8)
            pts = np.array(roi_points, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
        return mask

    def _setup_logging(self) -> None:
        logging_config = self.config["logging"]
        if not logging_config["enable_csv"]:
            return

        csv_path = Path(logging_config["csv_path"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        file_exists = csv_path.exists()
        self.csv_handle = csv_path.open("a", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_handle)
        if not file_exists:
            self.csv_writer.writerow(["timestamp", "id", "speed_kmh", "center_x", "center_y"])

    def _log_event(self, event: dict[str, Any], timestamp: float) -> None:
        if self.csv_writer is None:
            return
        center_x, center_y = event["centroid"]
        self.csv_writer.writerow(
            [f"{timestamp:.3f}", event["id"], f"{event['speed_kmh']:.3f}", center_x, center_y]
        )
        self.csv_handle.flush()

    def _as_matrix(self, values: Any) -> np.ndarray | None:
        if values is None:
            return None
        matrix = np.array(values, dtype=np.float32)
        return matrix if matrix.size else None

    def _as_vector(self, values: Any) -> np.ndarray | None:
        if values is None:
            return None
        vector = np.array(values, dtype=np.float32)
        return vector if vector.size else None
