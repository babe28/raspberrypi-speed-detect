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
    last_measurement_at: float = 0.0


@dataclass
class OverlayMeasurement:
    label: str
    centroid: tuple[float, float]
    expires_at: float
    track_id: int
    color: tuple[int, int, int]
    subdued: bool = False
    kind: str = "speed"


class SpeedEstimator:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        processing = config["processing"]
        measurement = config["measurement"]
        self.detection_enabled = bool(processing.get("detection_enabled", False))
        self.scale_ppm = float(config["scale"]["ppm"])
        self.max_speed_kmh = float(processing["max_speed_kmh"])
        self.measurement_mode = str(measurement["mode"])
        self.overlay_hold_seconds = float(measurement["overlay_hold_seconds"])
        self.repeat_behavior = str(measurement.get("repeat_behavior", "normal"))
        self.repeat_cooldown_seconds = float(measurement.get("repeat_cooldown_seconds", 0.0))
        self.tracking_settings = measurement.get("tracking", {})
        self.tracking_direction = str(self.tracking_settings.get("direction", "any")).lower()
        self.race_reference = measurement.get("race_reference", {})
        self.goal_time_seconds = float(self.race_reference.get("goal_time_seconds", 0.0))
        self.course_distance_m = float(self.race_reference.get("course_distance_m", 0.0))
        self.measurement_point_m = float(self.race_reference.get("measurement_point_m", 0.0))
        self.bias_enabled = bool(self.race_reference.get("bias_enabled", False))
        self.global_bias_kmh = float(self.race_reference.get("global_bias_kmh", 0.0))
        self.line_crossing = measurement["line_crossing"]
        self.display_line_a = self._as_points(self.line_crossing["line_a"])
        self.display_line_b = self._as_points(self.line_crossing["line_b"])
        self.line_a = self.display_line_a
        self.line_b = self.display_line_b
        self.line_distance_m = float(self.line_crossing["distance_m"])
        self.min_contour_area = int(processing["min_contour_area"])
        self.max_contour_area = int(processing["max_contour_area"])
        self.frame_skip = int(processing.get("frame_skip", 0))
        self.min_speed_kmh = float(processing.get("min_speed_kmh", 0.0))
        self.track_max_distance = float(processing["track_max_distance"])
        self.track_max_missing_frames = int(processing["track_max_missing_frames"])
        self.warmup_frames = int(processing["warmup_frames"])
        self.threshold_value = int(processing["threshold_value"])
        self.blur_kernel_size = int(processing["blur_kernel_size"])
        self.morph_kernel_size = int(processing["morph_kernel_size"])
        self.open_iterations = int(processing["open_iterations"])
        self.dilate_iterations = int(processing["dilate_iterations"])
        self.debug_mode = bool(processing["debug_mode"])
        self.show_fps_overlay = bool(processing.get("show_fps_overlay", False))
        self.show_mask_preview = bool(processing.get("show_mask_preview", True))
        self.undistort_enabled = bool(processing["undistort_enabled"])
        self.manual_distortion = float(processing.get("manual_distortion", 0.0))
        self.perspective_enabled = bool(processing["perspective_enabled"])
        self.brightness_offset = int(processing.get("brightness_offset", 0))
        self.contrast_gain = float(processing.get("contrast_gain", 1.0))
        self.blur_enabled = bool(processing["blur_enabled"])
        self.morphology_enabled = bool(processing["morphology_enabled"])
        self.line_crossing_fast_mode = bool(processing.get("line_crossing_fast_mode", False))
        self.exclude_blue_floor = bool(processing["exclude_blue_floor"])
        self.blue_hsv_low = np.array(processing["blue_hsv_low"], dtype=np.uint8)
        self.blue_hsv_high = np.array(processing["blue_hsv_high"], dtype=np.uint8)
        self.line_crossing_fast_active = (
            self.measurement_mode == "line_crossing" and self.line_crossing_fast_mode
        )
        self.effective_min_contour_area = self.min_contour_area
        self.effective_track_max_distance = self.track_max_distance
        self.matchable_missing_frames = self.track_max_missing_frames
        if self.measurement_mode == "line_crossing":
            self.effective_track_max_distance *= 1.2
            self.matchable_missing_frames = min(self.track_max_missing_frames, 1)
        self.effective_threshold_value = self.threshold_value
        self.frame_index = 0
        self.background_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=int(processing["background_history"]),
            varThreshold=float(processing["background_var_threshold"]),
            detectShadows=False,
        )
        self.morph_kernel = (
            np.ones((self.morph_kernel_size, self.morph_kernel_size), np.uint8)
            if self.morphology_enabled and self.morph_kernel_size > 0
            else None
        )
        self.roi_mask: np.ndarray | None = None
        self.homography_matrix = self._as_matrix(config["perspective"]["homography_matrix"])
        self.inverse_homography_matrix = self._invert_matrix(self.homography_matrix)
        self.camera_matrix = self._as_matrix(config["calibration"]["camera_matrix"])
        self.dist_coeffs = self._as_vector(config["calibration"]["dist_coeffs"])
        self.display_roi_points = self._as_polygon(config["roi"]["polygon"])
        self.detect_roi_points = self.display_roi_points
        self.display_perspective_points = self._as_polygon(config["perspective"]["src_points"])
        self.display_roi_polygon = (
            np.array(self.display_roi_points, dtype=np.int32)
            if self.config["roi"]["enabled"] and len(self.display_roi_points) >= 3
            else None
        )
        self.display_perspective_polygon = (
            np.array(self.display_perspective_points, dtype=np.int32)
            if len(self.display_perspective_points) == 4
            else None
        )
        self._configure_detection_geometry()
        self.tracks: dict[int, Track] = {}
        self.active_measurements: list[OverlayMeasurement] = []
        self.next_track_id = 1
        self.csv_writer: csv.writer | None = None
        self.csv_handle = None
        self.csv_pending_flush_count = 0
        self._setup_logging()
        self.latest_display_frame: np.ndarray | None = None
        self.latest_detection_frame: np.ndarray | None = None
        self.latest_mask_frame: np.ndarray | None = None
        self.input_fps_ema = 0.0
        self.process_fps_ema = 0.0
        self.last_frame_ms = 0.0
        self.last_input_timestamp = 0.0
        self.last_processed_timestamp = 0.0

    def process(self, frame: np.ndarray) -> tuple[np.ndarray, list[dict[str, Any]]]:
        process_started = time.perf_counter()
        now = time.time()
        self._update_input_fps(now)
        display_frame = self._apply_undistort(frame)
        display_frame = self._apply_image_correction(display_frame)
        detection_frame = self._apply_perspective(display_frame)
        self.frame_index += 1
        self._prune_measurements()
        mask = np.zeros(detection_frame.shape[:2], dtype=np.uint8)
        processed_this_frame = False
        if not self.detection_enabled:
            self._store_debug_frames(display_frame, detection_frame, mask)
            annotated = self._annotate(display_frame, mask, [])
            self._finalize_metrics(process_started, processed_this_frame, now)
            return annotated, []
        if self.frame_skip > 0 and self.frame_index > self.warmup_frames:
            process_interval = self.frame_skip + 1
            active_index = self.frame_index - self.warmup_frames - 1
            if active_index % process_interval != 0:
                self._store_debug_frames(display_frame, detection_frame, mask)
                annotated = self._annotate(display_frame, mask, [])
                self._finalize_metrics(process_started, processed_this_frame, now)
                return annotated, []
        mask = self._motion_mask(detection_frame)
        if self.frame_index <= self.warmup_frames:
            self._store_debug_frames(display_frame, detection_frame, mask)
            annotated = self._annotate(display_frame, mask, [])
            self._finalize_metrics(process_started, processed_this_frame, now)
            return annotated, []
        processed_this_frame = True
        detections = self._find_detections(mask)
        events = self._update_tracks(detections)
        self._store_debug_frames(display_frame, detection_frame, mask)
        annotated = self._annotate(display_frame, mask, events)
        self._finalize_metrics(process_started, processed_this_frame, now)
        return annotated, events

    def close(self) -> None:
        if self.csv_handle is not None:
            self.csv_handle.flush()
            self.csv_handle.close()
            self.csv_handle = None

    def _apply_undistort(self, frame: np.ndarray) -> np.ndarray:
        corrected = frame
        if self.undistort_enabled and self.camera_matrix is not None and self.dist_coeffs is not None:
            corrected = cv2.undistort(corrected, self.camera_matrix, self.dist_coeffs)
        if self.undistort_enabled and abs(self.manual_distortion) > 1e-6:
            corrected = self._apply_manual_distortion(corrected, self.manual_distortion)
        return corrected

    def _apply_manual_distortion(self, frame: np.ndarray, amount: float) -> np.ndarray:
        height, width = frame.shape[:2]
        if width <= 1 or height <= 1:
            return frame

        map_x, map_y = np.meshgrid(
            np.arange(width, dtype=np.float32),
            np.arange(height, dtype=np.float32),
        )
        cx = (width - 1) * 0.5
        cy = (height - 1) * 0.5
        nx = (map_x - cx) / max(cx, 1.0)
        ny = (map_y - cy) / max(cy, 1.0)
        r2 = (nx * nx) + (ny * ny)

        # Positive values pull edges inward a bit; negative values push them outward.
        k = -0.35 * float(amount)
        scale = 1.0 + (k * r2)
        src_x = (nx * scale * max(cx, 1.0)) + cx
        src_y = (ny * scale * max(cy, 1.0)) + cy
        return cv2.remap(
            frame,
            src_x.astype(np.float32),
            src_y.astype(np.float32),
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )

    def _apply_perspective(self, frame: np.ndarray) -> np.ndarray:
        if self.perspective_enabled and self.homography_matrix is not None:
            return cv2.warpPerspective(
                frame,
                self.homography_matrix,
                (frame.shape[1], frame.shape[0]),
            )
        return frame

    def _apply_image_correction(self, frame: np.ndarray) -> np.ndarray:
        if abs(self.contrast_gain - 1.0) < 1e-6 and self.brightness_offset == 0:
            return frame
        return cv2.convertScaleAbs(frame, alpha=self.contrast_gain, beta=self.brightness_offset)

    def _motion_mask(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        use_blur = self.blur_enabled and not self.line_crossing_fast_active
        if use_blur and self.blur_kernel_size > 1:
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
        if use_blur and self.blur_kernel_size > 1:
            fg_mask = cv2.medianBlur(fg_mask, self.blur_kernel_size)
        if self.morphology_enabled:
            fg_mask = cv2.morphologyEx(
                fg_mask,
                cv2.MORPH_OPEN,
                self.morph_kernel,
                iterations=self.open_iterations,
            )
            fg_mask = cv2.dilate(fg_mask, self.morph_kernel, iterations=self.dilate_iterations)
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
            previous_centroid: tuple[float, float] | None = None
            
            if track is None:
                track = Track(
                    track_id=self.next_track_id,
                    centroid=detection["centroid"],
                    timestamp=now,
                    history=(
                        [(detection["centroid"][0], detection["centroid"][1], now)]
                        if self.measurement_mode != "line_crossing"
                        else []
                    ),
                )
                self.tracks[track.track_id] = track
                self.next_track_id += 1
            else:
                previous_centroid = track.centroid
                previous_timestamp = track.timestamp
                if self.measurement_mode == "line_crossing":
                    track.centroid = detection["centroid"]
                    track.timestamp = now
                    track.missed_frames = 0
                    measurement_event = self._maybe_measure_line_crossing(
                        track,
                        previous_centroid,
                        previous_timestamp,
                        detection["centroid"],
                        now,
                    )
                    if measurement_event is not None:
                        measurement_event["area"] = detection["area"]
                        events.append(measurement_event)
                        if not measurement_event.get("subdued", False):
                            self._log_event(measurement_event, now)
                else:
                    speed_kmh, speed_px_s = self._estimate_speed(
                        track, detection["centroid"], now
                    )
                    track.centroid = detection["centroid"]
                    track.timestamp = now
                    track.missed_frames = 0
                    track.speed_kmh = speed_kmh
                    track.speed_px_s = speed_px_s
                    track.history.append((detection["centroid"][0], detection["centroid"][1], now))
                    track.history = track.history[-6:]

            matched_track_ids.add(track.track_id)
            if self.measurement_mode == "tracking" and previous_centroid is not None:
                if self.scale_ppm > 0 and track.speed_kmh < self.min_speed_kmh:
                    continue
                if not self._tracking_direction_matches(previous_centroid, track.centroid):
                    continue
                display_speed_kmh = self._display_speed_kmh(track.speed_kmh)
                event = {
                    "id": track.track_id,
                    "bbox": detection["bbox"],
                    "centroid": track.centroid,
                    "area": detection["area"],
                    "mode": "tracking",
                    "speed_kmh": display_speed_kmh,
                    "raw_speed_kmh": track.speed_kmh,
                    "speed_px_s": track.speed_px_s,
                    "speed_label": self._format_speed_label(display_speed_kmh, track.speed_px_s),
                    "color": self._track_color(track.track_id),
                }
                event.update(self._goal_time_projection(track.speed_kmh))
                event = self._apply_repeat_behavior(track, event, now)
                if event is None:
                    continue
                events.append(event)
                self._upsert_active_measurement(
                    track_id=track.track_id,
                    centroid=track.centroid,
                    label=event["speed_label"],
                    color=event["color"],
                    subdued=bool(event.get("subdued", False)),
                    kind="tracking",
                )
                if not event.get("subdued", False):
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
        previous_timestamp: float,
        current_centroid: tuple[float, float],
        now: float,
    ) -> dict[str, Any] | None:
        if self.line_a is None or self.line_b is None or self.line_distance_m <= 0:
            return None

        crossed_a = self._segment_intersection_ratio(previous_centroid, current_centroid, *self.line_a)
        crossed_b = self._segment_intersection_ratio(previous_centroid, current_centroid, *self.line_b)
        segment_dt = max(0.0, now - previous_timestamp)

        if crossed_a is not None:
            track.crossed_lines["line_a"] = previous_timestamp + (segment_dt * crossed_a)

        if crossed_b is not None:
            track.crossed_lines["line_b"] = previous_timestamp + (segment_dt * crossed_b)

        line_a_time = track.crossed_lines.get("line_a")
        line_b_time = track.crossed_lines.get("line_b")
        if line_a_time is None or line_b_time is None:
            return None

        dt = abs(line_b_time - line_a_time)
        if dt <= 0:
            return None

        speed_kmh = self.line_distance_m / dt * 3.6
        if speed_kmh < self.min_speed_kmh:
            return None
        if speed_kmh > self.max_speed_kmh:
            return None

        display_speed_kmh = self._display_speed_kmh(speed_kmh)
        event = {
            "id": track.track_id,
            "bbox": self._bbox_from_centroid(current_centroid),
            "centroid": current_centroid,
            "mode": "line_crossing",
            "speed_kmh": display_speed_kmh,
            "raw_speed_kmh": speed_kmh,
            "speed_px_s": 0.0,
            "speed_label": f"{display_speed_kmh:.1f} km/h",
            "color": self._track_color(track.track_id),
        }
        event.update(self._goal_time_projection(speed_kmh))
        event = self._apply_repeat_behavior(track, event, now)
        track.crossed_lines.clear()
        if event is None:
            return None
        self._upsert_active_measurement(
            track_id=track.track_id,
            centroid=current_centroid,
            label=event["speed_label"],
            color=event["color"],
            subdued=bool(event.get("subdued", False)),
            kind="line_crossing",
        )
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
        if kmh < self.min_speed_kmh:
            return 0.0, speed_px_s
        if kmh > self.max_speed_kmh:
            return track.speed_kmh, track.speed_px_s
        return kmh, speed_px_s

    def _tracking_direction_matches(
        self,
        previous_centroid: tuple[float, float],
        current_centroid: tuple[float, float],
    ) -> bool:
        if self.tracking_direction == "any":
            return True

        dx = current_centroid[0] - previous_centroid[0]
        dy = current_centroid[1] - previous_centroid[1]

        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return False

        if self.tracking_direction == "left_to_right":
            return dx > 0 and abs(dx) >= abs(dy)
        if self.tracking_direction == "right_to_left":
            return dx < 0 and abs(dx) >= abs(dy)
        if self.tracking_direction == "top_to_bottom":
            return dy > 0 and abs(dy) >= abs(dx)
        if self.tracking_direction == "bottom_to_top":
            return dy < 0 and abs(dy) >= abs(dx)
        return True

    def _apply_repeat_behavior(
        self, track: Track, event: dict[str, Any], now: float
    ) -> dict[str, Any] | None:
        if self.repeat_behavior == "normal" or self.repeat_cooldown_seconds <= 0:
            event["subdued"] = False
            track.last_measurement_at = now
            return event

        if track.last_measurement_at <= 0:
            event["subdued"] = False
            track.last_measurement_at = now
            return event

        elapsed = now - track.last_measurement_at
        if elapsed >= self.repeat_cooldown_seconds:
            event["subdued"] = False
            track.last_measurement_at = now
            return event

        if self.repeat_behavior == "ignore":
            return None

        event["subdued"] = True
        return event

    def _update_input_fps(self, now: float) -> None:
        if self.last_input_timestamp > 0:
            dt = now - self.last_input_timestamp
            if dt > 0:
                self.input_fps_ema = self._smooth_metric(self.input_fps_ema, 1.0 / dt)
        self.last_input_timestamp = now

    def _finalize_metrics(
        self, process_started: float, processed_this_frame: bool, now: float
    ) -> None:
        self.last_frame_ms = (time.perf_counter() - process_started) * 1000.0
        if processed_this_frame:
            if self.last_processed_timestamp > 0:
                dt = now - self.last_processed_timestamp
                if dt > 0:
                    self.process_fps_ema = self._smooth_metric(self.process_fps_ema, 1.0 / dt)
            self.last_processed_timestamp = now

    def _store_debug_frames(
        self, display_frame: np.ndarray, detection_frame: np.ndarray, mask: np.ndarray
    ) -> None:
        if not self.debug_mode:
            self.latest_display_frame = None
            self.latest_detection_frame = None
            self.latest_mask_frame = None
            return
        self.latest_display_frame = display_frame.copy()
        self.latest_detection_frame = detection_frame.copy()
        self.latest_mask_frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    def _smooth_metric(self, current: float, new_value: float, alpha: float = 0.2) -> float:
        if current <= 0:
            return new_value
        return (current * (1.0 - alpha)) + (new_value * alpha)

    def runtime_metrics(self) -> dict[str, float | int | bool]:
        process_interval = self.frame_skip + 1
        return {
            "input_fps": round(self.input_fps_ema, 2),
            "process_fps": round(self.process_fps_ema, 2),
            "last_frame_ms": round(self.last_frame_ms, 2),
            "frame_index": self.frame_index,
            "frame_skip": self.frame_skip,
            "process_interval": process_interval,
            "detection_enabled": self.detection_enabled,
            "debug_mode": self.debug_mode,
        }

    def _display_speed_kmh(self, speed_kmh: float) -> float:
        if not self.bias_enabled:
            return speed_kmh
        return max(0.1, speed_kmh + self.global_bias_kmh)

    def _goal_time_projection(self, speed_kmh: float) -> dict[str, float | str]:
        if (
            self.goal_time_seconds <= 0
            or self.course_distance_m <= 0
            or speed_kmh <= 0
        ):
            return {}

        adjusted_speed_kmh = self._display_speed_kmh(speed_kmh)
        adjusted_speed_mps = adjusted_speed_kmh / 3.6
        avg_speed_mps = self.course_distance_m / self.goal_time_seconds
        point_m = min(max(self.measurement_point_m, 0.0), self.course_distance_m)
        elapsed_to_point = point_m / avg_speed_mps if avg_speed_mps > 0 else 0.0
        remaining_distance = max(0.0, self.course_distance_m - point_m)
        estimated_goal_time = elapsed_to_point + (remaining_distance / adjusted_speed_mps)
        delta_seconds = estimated_goal_time - self.goal_time_seconds
        minutes = int(estimated_goal_time // 60)
        seconds = estimated_goal_time - (minutes * 60)
        if minutes > 0:
            label = f"{minutes}:{seconds:06.3f}"
        else:
            label = f"{estimated_goal_time:.3f}s"
        return {
            "estimated_goal_time_seconds": estimated_goal_time,
            "estimated_goal_time_label": label,
            "goal_time_delta_seconds": delta_seconds,
            "adjusted_speed_kmh": adjusted_speed_kmh,
            "bias_enabled": self.bias_enabled,
        }

    def _find_nearest_track(self, centroid: tuple[float, float]) -> Track | None:
        nearest: Track | None = None
        nearest_distance = self.effective_track_max_distance

        for track in self.tracks.values():
            if track.missed_frames > self.matchable_missing_frames:
                continue
            distance = math.hypot(track.centroid[0] - centroid[0], track.centroid[1] - centroid[1])
            if distance < nearest_distance:
                nearest = track
                nearest_distance = distance

        return nearest

    def _annotate(
        self, frame: np.ndarray, mask: np.ndarray, events: list[dict[str, Any]]
    ) -> np.ndarray:
        has_roi_overlay = self.display_roi_polygon is not None
        has_perspective_overlay = self.display_perspective_polygon is not None
        has_event_boxes = bool(events) and (self.measurement_mode == "tracking" or self.debug_mode)
        has_lines = self.measurement_mode == "line_crossing"
        has_active_measurements = bool(self.active_measurements)
        has_text_overlays = self.debug_mode or self.show_fps_overlay or self.show_mask_preview
        if not (
            has_roi_overlay
            or has_perspective_overlay
            or has_event_boxes
            or has_lines
            or has_active_measurements
            or has_text_overlays
        ):
            return frame

        annotated = frame.copy()

        if has_roi_overlay:
            cv2.polylines(
                annotated,
                [self.display_roi_polygon],
                isClosed=True,
                color=(255, 255, 0),
                thickness=2,
            )

        if has_perspective_overlay:
            cv2.polylines(
                annotated,
                [self.display_perspective_polygon],
                isClosed=True,
                color=(0, 255, 255),
                thickness=2,
            )

        for event in events:
            if self.measurement_mode == "tracking" or self.debug_mode:
                x, y, w, h = self._display_bbox(event["bbox"])
                color = event.get("color", (0, 220, 0))
                subdued = bool(event.get("subdued", False))
                box_color = self._muted_color(color) if subdued else color
                box_thickness = 1 if subdued else 2
                cv2.rectangle(annotated, (x, y), (x + w, y + h), box_color, box_thickness)
                self._draw_label_badges(
                    annotated,
                    x,
                    y,
                    f"ID {event['id']}",
                    event["speed_label"],
                    color,
                    detail_text=(
                        f"area {int(round(float(event.get('area', 0.0))))}"
                        if self.debug_mode and event.get("area") is not None
                        else None
                    ),
                    subdued=subdued,
                )

        if has_lines:
            self._draw_measurement_lines(annotated)
        if has_active_measurements:
            self._draw_active_measurements(annotated)

        if self.show_mask_preview:
            mask_preview = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            preview_scale = 3 if self.debug_mode else 4
            preview_h = max(1, annotated.shape[0] // preview_scale)
            preview_w = max(1, annotated.shape[1] // preview_scale)
            mask_preview = cv2.resize(mask_preview, (preview_w, preview_h))
            annotated[:preview_h, :preview_w] = mask_preview
        if self.debug_mode:
            debug_text = (
                f"[DBG] min={self.min_contour_area} max={self.max_contour_area} "
                f"minspd={self.min_speed_kmh:.1f} "
                f"thr={self.effective_threshold_value} BLUE={'on' if self.exclude_blue_floor else 'off'} "
                f"ppm={'set' if self.scale_ppm > 0 else 'unset'}"
            )
            cv2.putText(
                annotated,
                debug_text,
                (12, annotated.shape[0] - 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        if self.show_fps_overlay:
            fps_text = (
                f"FPS {self.input_fps_ema:.1f} / PROC {self.process_fps_ema:.1f}"
                if self.process_fps_ema > 0
                else f"FPS {self.input_fps_ema:.1f}"
            )
            if self.debug_mode:
                fps_text += f" / {self.last_frame_ms:.1f} ms"
            cv2.putText(
                annotated,
                fps_text,
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        return annotated

    def _build_roi_mask(self, shape: tuple[int, int]) -> np.ndarray:
        height, width = shape
        mask = np.ones((height, width), dtype=np.uint8) * 255
        if self.config["roi"]["enabled"] and len(self.detect_roi_points) >= 3:
            mask = np.zeros((height, width), dtype=np.uint8)
            pts = np.array(self.detect_roi_points, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
        return mask

    def _draw_measurement_lines(self, frame: np.ndarray) -> None:
        if self.display_line_a is not None:
            cv2.line(
                frame,
                self._as_int_point(self.display_line_a[0]),
                self._as_int_point(self.display_line_a[1]),
                (255, 166, 0),
                3,
            )
            cv2.putText(
                frame,
                "Line A",
                self._as_int_point(self.display_line_a[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 166, 0),
                2,
                cv2.LINE_AA,
            )
        if self.display_line_b is not None:
            cv2.line(
                frame,
                self._as_int_point(self.display_line_b[0]),
                self._as_int_point(self.display_line_b[1]),
                (255, 0, 170),
                3,
            )
            cv2.putText(
                frame,
                "Line B",
                self._as_int_point(self.display_line_b[0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 0, 170),
                2,
                cv2.LINE_AA,
            )

    def _draw_active_measurements(self, frame: np.ndarray) -> None:
        for measurement in self.active_measurements:
            x, y = self._as_int_point(self._to_display_point(measurement.centroid))
            anchor_x = max(10, x - 56)
            anchor_y = max(36, y - 24)
            self._draw_label_badges(
                frame,
                anchor_x,
                anchor_y,
                f"ID {measurement.track_id}",
                measurement.label,
                measurement.color,
                subdued=measurement.subdued,
            )

    def _upsert_active_measurement(
        self,
        track_id: int,
        centroid: tuple[float, float],
        label: str,
        color: tuple[int, int, int],
        subdued: bool,
        kind: str,
    ) -> None:
        expires_at = time.time() + self.overlay_hold_seconds
        for measurement in self.active_measurements:
            if measurement.track_id == track_id and measurement.kind == kind:
                measurement.centroid = centroid
                measurement.label = label
                measurement.color = color
                measurement.subdued = subdued
                measurement.expires_at = expires_at
                return

        self.active_measurements.append(
            OverlayMeasurement(
                label=label,
                centroid=centroid,
                expires_at=expires_at,
                track_id=track_id,
                color=color,
                subdued=subdued,
                kind=kind,
            )
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
        self.csv_pending_flush_count += 1
        if self.csv_pending_flush_count >= 5:
            self.csv_handle.flush()
            self.csv_pending_flush_count = 0

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

    def _muted_color(self, color: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(int((component * 0.45) + 60) for component in color)

    def _configure_detection_geometry(self) -> None:
        if not self.perspective_enabled or self.homography_matrix is None:
            self.detect_roi_points = self.display_roi_points
            self.line_a = self.display_line_a
            self.line_b = self.display_line_b
            return

        self.detect_roi_points = self._transform_polygon(
            self.display_roi_points, self.homography_matrix
        )
        self.line_a = self._transform_segment(self.display_line_a, self.homography_matrix)
        self.line_b = self._transform_segment(self.display_line_b, self.homography_matrix)

    def _display_bbox(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if not self.perspective_enabled or self.inverse_homography_matrix is None:
            return bbox

        x, y, w, h = bbox
        corners = [
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
        ]
        transformed = [self._to_display_point(point) for point in corners]
        xs = [point[0] for point in transformed]
        ys = [point[1] for point in transformed]
        min_x = int(min(xs))
        min_y = int(min(ys))
        max_x = int(max(xs))
        max_y = int(max(ys))
        return (min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))

    def _to_display_point(self, point: tuple[float, float]) -> tuple[float, float]:
        if not self.perspective_enabled or self.inverse_homography_matrix is None:
            return point
        return self._transform_point(point, self.inverse_homography_matrix)

    def _draw_label_badges(
        self,
        frame: np.ndarray,
        x: int,
        y: int,
        id_text: str,
        speed_text: str,
        color: tuple[int, int, int],
        detail_text: str | None = None,
        subdued: bool = False,
    ) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.42 if subdued else 0.55
        thickness = 1
        padding_x = 6 if subdued else 8
        padding_y = 4 if subdued else 6
        gap = 4 if subdued else 6
        (id_w, id_h), _ = cv2.getTextSize(id_text, font, font_scale, thickness)
        (speed_w, speed_h), _ = cv2.getTextSize(speed_text, font, font_scale, thickness)
        box_h = max(id_h, speed_h) + padding_y * 2
        id_box_w = id_w + padding_x * 2
        speed_box_w = speed_w + padding_x * 2
        top = max(2, y - box_h)
        speed_x = x + id_box_w + gap
        detail_box_w = 0
        detail_x = speed_x + speed_box_w + gap

        if detail_text:
            (detail_w, detail_h), _ = cv2.getTextSize(detail_text, font, font_scale, thickness)
            box_h = max(box_h, detail_h + padding_y * 2)
            detail_box_w = detail_w + padding_x * 2

        id_bg = self._muted_color(color) if subdued else color
        speed_bg = (48, 48, 48) if subdued else (18, 18, 18)
        cv2.rectangle(frame, (x, top), (x + id_box_w, top + box_h), id_bg, -1)
        cv2.rectangle(frame, (speed_x, top), (speed_x + speed_box_w, top + box_h), speed_bg, -1)
        if detail_text:
            detail_bg = (70, 92, 102) if subdued else (28, 76, 94)
            cv2.rectangle(
                frame,
                (detail_x, top),
                (detail_x + detail_box_w, top + box_h),
                detail_bg,
                -1,
            )
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
        if detail_text:
            cv2.putText(
                frame,
                detail_text,
                (detail_x + padding_x, top + box_h - padding_y),
                font,
                font_scale,
                (255, 255, 255),
                thickness,
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

    def _as_polygon(self, values: Any) -> list[tuple[float, float]]:
        if not isinstance(values, list):
            return []
        points: list[tuple[float, float]] = []
        for value in values:
            if isinstance(value, (list, tuple)) and len(value) == 2:
                points.append((float(value[0]), float(value[1])))
        return points

    def _invert_matrix(self, matrix: np.ndarray | None) -> np.ndarray | None:
        if matrix is None or matrix.shape != (3, 3):
            return None
        try:
            return np.linalg.inv(matrix)
        except np.linalg.LinAlgError:
            return None

    def _transform_point(
        self, point: tuple[float, float], matrix: np.ndarray
    ) -> tuple[float, float]:
        src = np.array([[[point[0], point[1]]]], dtype=np.float32)
        dst = cv2.perspectiveTransform(src, matrix)
        return (float(dst[0][0][0]), float(dst[0][0][1]))

    def _transform_polygon(
        self, points: list[tuple[float, float]], matrix: np.ndarray
    ) -> list[tuple[float, float]]:
        if not points:
            return []
        src = np.array([points], dtype=np.float32)
        dst = cv2.perspectiveTransform(src, matrix)[0]
        return [(float(point[0]), float(point[1])) for point in dst]

    def _transform_segment(
        self,
        segment: tuple[tuple[float, float], tuple[float, float]] | None,
        matrix: np.ndarray,
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if segment is None:
            return None
        transformed = self._transform_polygon([segment[0], segment[1]], matrix)
        if len(transformed) != 2:
            return None
        return (transformed[0], transformed[1])

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

    def _segment_intersection_ratio(
        self,
        p1: tuple[float, float],
        p2: tuple[float, float],
        q1: tuple[float, float],
        q2: tuple[float, float],
    ) -> float | None:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = q1
        x4, y4 = q2
        denom = ((x1 - x2) * (y3 - y4)) - ((y1 - y2) * (x3 - x4))
        if abs(denom) < 1e-6:
            return None

        t = (((x1 - x3) * (y3 - y4)) - ((y1 - y3) * (x3 - x4))) / denom
        u = (((x1 - x3) * (y1 - y2)) - ((y1 - y3) * (x1 - x2))) / denom
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            return float(t)
        return None

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
