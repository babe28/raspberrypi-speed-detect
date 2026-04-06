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
    speed_px_s: float = 0.0
    history: list[tuple[float, float, float]] = field(default_factory=list)
    crossed_lines: dict[str, float] = field(default_factory=dict)


@dataclass
class OverlayMeasurement:
    label: str
    centroid: tuple[float, float]
    expires_at: float
    track_id: int
    color: tuple[int, int, int]


class SpeedEstimator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        processing = config["processing"]
        measurement = config["measurement"]
        self.scale_ppm = float(config["scale"]["ppm"])
        self.max_speed_kmh = float(processing["max_speed_kmh"])
        self.measurement_mode = str(measurement["mode"])
        self.overlay_hold_seconds = float(measurement["overlay_hold_seconds"])
        self.line_crossing = measurement["line_crossing"]
        self.line_a = self._as_points(self.line_crossing["line_a"])
        self.line_b = self._as_points(self.line_crossing["line_b"])
        self.line_distance_m = float(self.line_crossing["distance_m"])
        self.min_contour_area = int(processing["min_contour_area"])
        self.max_contour_area = int(processing["max_contour_area"])
        self.track_max_distance = float(processing["track_max_distance"])
        self.track_max_missing_frames = int(processing["track_max_missing_frames"])
        self.warmup_frames = int(processing["warmup_frames"])
        self.threshold_value = int(processing["threshold_value"])
        self.blur_kernel_size = int(processing["blur_kernel_size"])
        self.morph_kernel_size = int(processing["morph_kernel_size"])
        self.open_iterations = int(processing["open_iterations"])
        self.dilate_iterations = int(processing["dilate_iterations"])
        self.debug_mode = bool(processing["debug_mode"])
        self.undistort_enabled = bool(processing["undistort_enabled"])
        self.perspective_enabled = bool(processing["perspective_enabled"])
        self.blur_enabled = bool(processing["blur_enabled"])
        self.morphology_enabled = bool(processing["morphology_enabled"])
        self.exclude_blue_floor = bool(processing["exclude_blue_floor"])
        self.blue_hsv_low = np.array(processing["blue_hsv_low"], dtype=np.uint8)
        self.blue_hsv_high = np.array(processing["blue_hsv_high"], dtype=np.uint8)
        self.effective_min_contour_area = (
            max(25, int(self.min_contour_area * 0.35)) if self.debug_mode else self.min_contour_area
        )
        self.effective_track_max_distance = (
            self.track_max_distance * 1.35 if self.debug_mode else self.track_max_distance
        )
        self.effective_threshold_value = (
            max(80, int(self.threshold_value * 0.85)) if self.debug_mode else self.threshold_value
        )
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
        self.active_measurements: list[OverlayMeasurement] = []
        self.next_track_id = 1
        self.csv_writer: csv.writer | None = None
        self.csv_handle = None
        self._setup_logging()

    def process(self, frame: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
        corrected = self._apply_corrections(frame)
        mask = self._motion_mask(corrected)
        self.frame_index += 1
        self._prune_measurements()
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
        if self.undistort_enabled and self.camera_matrix is not None and self.dist_coeffs is not None:
            corrected = cv2.undistort(corrected, self.camera_matrix, self.dist_coeffs)

        if self.perspective_enabled and self.homography_matrix is not None:
            corrected = cv2.warpPerspective(
                corrected,
                self.homography_matrix,
                (corrected.shape[1], corrected.shape[0]),
            )
        return corrected

    def _motion_mask(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.blur_enabled and self.blur_kernel_size > 1:
            gray = cv2.GaussianBlur(gray, (self.blur_kernel_size, self.blur_kernel_size), 0)
        fg_mask = self.background_subtractor.apply(gray)

        if self.roi_mask is None or self.roi_mask.shape != fg_mask.shape:
            self.roi_mask = self._build_roi_mask(fg_mask.shape)

        fg_mask = cv2.bitwise_and(fg_mask, fg_mask, mask=self.roi_mask)
        if self.exclude_blue_floor:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            blue_mask = cv2.inRange(hsv, self.blue_hsv_low, self.blue_hsv_high)
            fg_mask = cv2.bitwise_and(fg_mask, cv2.bitwise_not(blue_mask))

        _, fg_mask = cv2.threshold(
            fg_mask, self.effective_threshold_value, 255, cv2.THRESH_BINARY
        )
        if self.blur_enabled and self.blur_kernel_size > 1:
            fg_mask = cv2.medianBlur(fg_mask, self.blur_kernel_size)
        if self.morphology_enabled:
            kernel = np.ones((self.morph_kernel_size, self.morph_kernel_size), np.uint8)
            fg_mask = cv2.morphologyEx(
                fg_mask,
                cv2.MORPH_OPEN,
                kernel,
                iterations=self.open_iterations,
            )
            fg_mask = cv2.dilate(fg_mask, kernel, iterations=self.dilate_iterations)
        return fg_mask

    def _find_detections(self, mask: np.ndarray) -> list[dict[str, Any]]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[dict[str, Any]] = []

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < self.effective_min_contour_area:
                continue
            if area > self.max_contour_area:
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
                previous_centroid = track.centroid
                speed_kmh, speed_px_s = self._estimate_speed(track, detection["centroid"], now)
                track.centroid = detection["centroid"]
                track.timestamp = now
                track.missed_frames = 0
                track.speed_kmh = speed_kmh
                track.speed_px_s = speed_px_s
                track.history.append((detection["centroid"][0], detection["centroid"][1], now))
                track.history = track.history[-6:]
                if self.measurement_mode == "line_crossing":
                    measurement_event = self._maybe_measure_line_crossing(
                        track,
                        previous_centroid,
                        detection["centroid"],
                        now,
                    )
                    if measurement_event is not None:
                        events.append(measurement_event)
                        self._log_event(measurement_event, now)

            matched_track_ids.add(track.track_id)
            if self.measurement_mode == "tracking":
                event = {
                    "id": track.track_id,
                    "bbox": detection["bbox"],
                    "centroid": track.centroid,
                    "speed_kmh": track.speed_kmh,
                    "speed_px_s": track.speed_px_s,
                    "speed_label": self._format_speed_label(track.speed_kmh, track.speed_px_s),
                    "color": self._track_color(track.track_id),
                }
                events.append(event)
                self._log_event(event, now)

        for track_id, track in list(self.tracks.items()):
            if track_id not in matched_track_ids:
                track.missed_frames += 1
                if track.missed_frames > self.track_max_missing_frames:
                    self.tracks.pop(track_id, None)

        return events

    def _maybe_measure_line_crossing(
        self,
        track: Track,
        previous_centroid: tuple[float, float],
        current_centroid: tuple[float, float],
        now: float,
    ) -> dict[str, Any] | None:
        if self.line_a is None or self.line_b is None or self.line_distance_m <= 0:
            return None

        crossed_a = self._segments_intersect(previous_centroid, current_centroid, *self.line_a)
        crossed_b = self._segments_intersect(previous_centroid, current_centroid, *self.line_b)

        if crossed_a:
            track.crossed_lines["line_a"] = now

        if crossed_b:
            track.crossed_lines["line_b"] = now

        line_a_time = track.crossed_lines.get("line_a")
        line_b_time = track.crossed_lines.get("line_b")
        if line_a_time is None or line_b_time is None:
            return None

        dt = abs(line_b_time - line_a_time)
        if dt <= 0:
            return None

        speed_kmh = self.line_distance_m / dt * 3.6
        if speed_kmh > self.max_speed_kmh:
            return None

        event = {
            "id": track.track_id,
            "bbox": self._bbox_from_centroid(current_centroid),
            "centroid": current_centroid,
            "speed_kmh": speed_kmh,
            "speed_px_s": 0.0,
            "speed_label": f"{speed_kmh:.1f} km/h",
            "color": self._track_color(track.track_id),
        }
        self.active_measurements.append(
            OverlayMeasurement(
                label=event["speed_label"],
                centroid=current_centroid,
                expires_at=now + self.overlay_hold_seconds,
                track_id=track.track_id,
                color=event["color"],
            )
        )
        track.crossed_lines.clear()
        track.speed_kmh = speed_kmh
        track.speed_px_s = 0.0
        return event

    def _estimate_speed(
        self, track: Track, new_centroid: tuple[float, float], now: float
    ) -> tuple[float, float]:
        dt = now - track.timestamp
        if dt <= 0:
            return track.speed_kmh, track.speed_px_s

        dx = new_centroid[0] - track.centroid[0]
        dy = new_centroid[1] - track.centroid[1]
        distance_pixels = math.hypot(dx, dy)
        speed_px_s = distance_pixels / dt
        if self.scale_ppm <= 0:
            return 0.0, speed_px_s

        meters = distance_pixels / self.scale_ppm
        kmh = meters / dt * 3.6
        if kmh > self.max_speed_kmh:
            return track.speed_kmh, track.speed_px_s
        return kmh, speed_px_s

    def _find_nearest_track(self, centroid: tuple[float, float]) -> Track | None:
        nearest: Track | None = None
        nearest_distance = self.effective_track_max_distance

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
            if self.measurement_mode == "tracking" or self.debug_mode:
                x, y, w, h = event["bbox"]
                color = event.get("color", (0, 220, 0))
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                self._draw_label_badges(
                    annotated,
                    x,
                    y,
                    f"ID {event['id']}",
                    event["speed_label"],
                    color,
                )

        self._draw_measurement_lines(annotated)
        self._draw_active_measurements(annotated)

        mask_preview = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        preview_scale = 3 if self.debug_mode else 4
        preview_h = max(1, annotated.shape[0] // preview_scale)
        preview_w = max(1, annotated.shape[1] // preview_scale)
        mask_preview = cv2.resize(mask_preview, (preview_w, preview_h))
        annotated[:preview_h, :preview_w] = mask_preview
        if self.debug_mode:
            debug_text = (
                f"debug min={self.min_contour_area} max={self.max_contour_area} "
                f"thr={self.effective_threshold_value} blue={'on' if self.exclude_blue_floor else 'off'} "
                f"ppm={'set' if self.scale_ppm > 0 else 'unset'}"
            )
            cv2.putText(
                annotated,
                debug_text,
                (12, annotated.shape[0] - 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
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

    def _draw_measurement_lines(self, frame: np.ndarray) -> None:
        if self.line_a is not None:
            cv2.line(
                frame,
                self._as_int_point(self.line_a[0]),
                self._as_int_point(self.line_a[1]),
                (255, 166, 0),
                3,
            )
            cv2.putText(
                frame,
                "Line A",
                self._as_int_point(self.line_a[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 166, 0),
                2,
                cv2.LINE_AA,
            )
        if self.line_b is not None:
            cv2.line(
                frame,
                self._as_int_point(self.line_b[0]),
                self._as_int_point(self.line_b[1]),
                (255, 0, 170),
                3,
            )
            cv2.putText(
                frame,
                "Line B",
                self._as_int_point(self.line_b[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 0, 170),
                2,
                cv2.LINE_AA,
            )

    def _draw_active_measurements(self, frame: np.ndarray) -> None:
        for measurement in self.active_measurements:
            x, y = self._as_int_point(measurement.centroid)
            anchor_x = max(10, x - 56)
            anchor_y = max(36, y - 24)
            self._draw_label_badges(
                frame,
                anchor_x,
                anchor_y,
                f"ID {measurement.track_id}",
                measurement.label,
                measurement.color,
            )

    def _prune_measurements(self) -> None:
        now = time.time()
        self.active_measurements = [
            measurement
            for measurement in self.active_measurements
            if measurement.expires_at > now
        ]

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

    def _format_speed_label(self, speed_kmh: float, speed_px_s: float) -> str:
        if self.scale_ppm > 0:
            return f"{speed_kmh:.1f} km/h"
        return f"{speed_px_s:.1f} px/s"

    def _track_color(self, track_id: int) -> tuple[int, int, int]:
        palette = [
            (0, 200, 255),
            (0, 220, 120),
            (255, 170, 0),
            (220, 100, 255),
            (255, 90, 90),
            (80, 180, 255),
        ]
        return palette[(track_id - 1) % len(palette)]

    def _draw_label_badges(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        id_text: str,
        speed_text: str,
        color: tuple[int, int, int],
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.55
        thickness = 1
        padding_x = 8
        padding_y = 6
        gap = 6
        (id_w, id_h), _ = cv2.getTextSize(id_text, font, font_scale, thickness)
        (speed_w, speed_h), _ = cv2.getTextSize(speed_text, font, font_scale, thickness)
        box_h = max(id_h, speed_h) + padding_y * 2
        id_box_w = id_w + padding_x * 2
        speed_box_w = speed_w + padding_x * 2
        top = max(2, y - box_h)
        speed_x = x + id_box_w + gap

        cv2.rectangle(frame, (x, top), (x + id_box_w, top + box_h), color, -1)
        cv2.rectangle(frame, (speed_x, top), (speed_x + speed_box_w, top + box_h), (18, 18, 18), -1)
        cv2.putText(
            frame,
            id_text,
            (x + padding_x, top + box_h - padding_y),
            font,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            speed_text,
            (speed_x + padding_x, top + box_h - padding_y),
            font,
            font_scale,
            color,
            thickness + 1,
            cv2.LINE_AA,
        )

    def _as_points(self, values: Any) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if not isinstance(values, list) or len(values) != 2:
            return None
        return (tuple(values[0]), tuple(values[1]))

    def _as_int_point(self, point: tuple[float, float]) -> tuple[int, int]:
        return (int(point[0]), int(point[1]))

    def _bbox_from_centroid(self, centroid: tuple[float, float]) -> tuple[int, int, int, int]:
        x = int(centroid[0] - 12)
        y = int(centroid[1] - 12)
        return (x, y, 24, 24)

    def _segments_intersect(
        self,
        p1: tuple[float, float],
        p2: tuple[float, float],
        q1: tuple[float, float],
        q2: tuple[float, float],
    ) -> bool:
        def ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
            return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

        return ccw(p1, q1, q2) != ccw(p2, q1, q2) and ccw(p1, p2, q1) != ccw(p1, p2, q2)

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
