import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "camera": {
        "type": "usb",
        "device": 0,
        "rtsp_enabled": False,
        "rtsp_url": "",
        "resolution": [1280, 720],
        "fps": 30,
    },
    "calibration": {
        "camera_matrix": None,
        "dist_coeffs": None,
    },
    "perspective": {
        "src_points": [],
        "homography_matrix": None,
    },
    "roi": {
        "polygon": [],
        "enabled": False,
    },
    "scale": {
        "known_distance_m": 2.0,
        "pixel_distance": 0.0,
        "ppm": 0.0,
    },
    "measurement": {
        "mode": "tracking",
        "overlay_hold_seconds": 5.0,
        "repeat_behavior": "normal",
        "repeat_cooldown_seconds": 0.0,
        "line_crossing": {
            "line_a": [],
            "line_b": [],
            "distance_m": 2.0,
        },
    },
    "processing": {
        "downscale_factor": 0.5,
        "min_contour_area": 500,
        "max_contour_area": 50000,
        "max_speed_kmh": 50.0,
        "warmup_frames": 15,
        "background_history": 300,
        "background_var_threshold": 32,
        "threshold_value": 180,
        "blur_kernel_size": 5,
        "morph_kernel_size": 3,
        "open_iterations": 1,
        "dilate_iterations": 2,
        "track_max_distance": 80,
        "track_max_missing_frames": 8,
        "debug_mode": False,
        "undistort_enabled": True,
        "perspective_enabled": True,
        "blur_enabled": True,
        "morphology_enabled": True,
        "exclude_blue_floor": False,
        "blue_hsv_low": [90, 50, 40],
        "blue_hsv_high": [135, 255, 255],
    },
    "logging": {
        "enable_csv": True,
        "csv_path": "logs/speed_log.csv",
    },
}


class ConfigManager:
    def __init__(self, path: str | Path = "config.json") -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            config = copy.deepcopy(DEFAULT_CONFIG)
            self.save(config)
            return config

        with self.path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)

        config = self._deep_merge(copy.deepcopy(DEFAULT_CONFIG), raw)
        self._normalize(config)
        return config

    def save(self, config: dict[str, Any]) -> None:
        normalized = copy.deepcopy(config)
        self._normalize(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(normalized, fh, indent=2, ensure_ascii=False)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        updated = self._deep_merge(config, patch)
        self._normalize(updated)
        self.save(updated)
        return updated

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    def _normalize(self, config: dict[str, Any]) -> None:
        camera = config["camera"]
        resolution = camera.get("resolution", [1280, 720])
        if not isinstance(resolution, list) or len(resolution) != 2:
            resolution = [1280, 720]
        camera["resolution"] = [int(resolution[0]), int(resolution[1])]
        camera["device"] = int(camera.get("device", 0))
        camera["fps"] = int(camera.get("fps", 30))
        camera["type"] = str(camera.get("type", "usb")).lower()
        camera["rtsp_enabled"] = bool(camera.get("rtsp_enabled", False))
        camera["rtsp_url"] = str(camera.get("rtsp_url", ""))

        roi = config["roi"]
        roi["enabled"] = bool(roi.get("enabled", False))
        roi["polygon"] = [self._normalize_point(point) for point in roi.get("polygon", [])]

        perspective = config["perspective"]
        perspective["src_points"] = [
            self._normalize_point(point) for point in perspective.get("src_points", [])
        ]
        perspective["homography_matrix"] = self._normalize_matrix(
            perspective.get("homography_matrix")
        )

        calibration = config["calibration"]
        calibration["camera_matrix"] = self._normalize_matrix(calibration.get("camera_matrix"))
        calibration["dist_coeffs"] = self._normalize_vector(calibration.get("dist_coeffs"))

        scale = config["scale"]
        scale["known_distance_m"] = float(scale.get("known_distance_m", 2.0))
        scale["pixel_distance"] = float(scale.get("pixel_distance", 0.0))
        scale["ppm"] = float(scale.get("ppm", 0.0))

        measurement = config["measurement"]
        measurement["mode"] = str(measurement.get("mode", "tracking")).lower()
        measurement["overlay_hold_seconds"] = float(
            measurement.get("overlay_hold_seconds", 5.0)
        )
        measurement["repeat_behavior"] = str(
            measurement.get("repeat_behavior", "normal")
        ).lower()
        if measurement["repeat_behavior"] not in {"normal", "ignore", "subdued"}:
            measurement["repeat_behavior"] = "normal"
        measurement["repeat_cooldown_seconds"] = max(
            0.0, float(measurement.get("repeat_cooldown_seconds", 0.0))
        )
        line_crossing = measurement.get("line_crossing", {})
        measurement["line_crossing"] = {
            "line_a": [self._normalize_point(point) for point in line_crossing.get("line_a", [])],
            "line_b": [self._normalize_point(point) for point in line_crossing.get("line_b", [])],
            "distance_m": float(line_crossing.get("distance_m", scale["known_distance_m"] or 2.0)),
        }

        processing = config["processing"]
        processing["downscale_factor"] = float(processing.get("downscale_factor", 0.5))
        processing["min_contour_area"] = int(processing.get("min_contour_area", 500))
        processing["max_contour_area"] = int(processing.get("max_contour_area", 50000))
        processing["max_speed_kmh"] = float(processing.get("max_speed_kmh", 50.0))
        processing["warmup_frames"] = int(processing.get("warmup_frames", 15))
        processing["background_history"] = int(processing.get("background_history", 300))
        processing["background_var_threshold"] = int(
            processing.get("background_var_threshold", 32)
        )
        processing["threshold_value"] = int(processing.get("threshold_value", 180))
        processing["blur_kernel_size"] = self._normalize_odd_int(
            processing.get("blur_kernel_size", 5),
            default=5,
            minimum=1,
        )
        processing["morph_kernel_size"] = self._normalize_odd_int(
            processing.get("morph_kernel_size", 3),
            default=3,
            minimum=1,
        )
        processing["open_iterations"] = int(processing.get("open_iterations", 1))
        processing["dilate_iterations"] = int(processing.get("dilate_iterations", 2))
        processing["track_max_distance"] = float(processing.get("track_max_distance", 80))
        processing["track_max_missing_frames"] = int(
            processing.get("track_max_missing_frames", 8)
        )
        processing["debug_mode"] = bool(processing.get("debug_mode", False))
        processing["undistort_enabled"] = bool(processing.get("undistort_enabled", True))
        processing["perspective_enabled"] = bool(processing.get("perspective_enabled", True))
        processing["blur_enabled"] = bool(processing.get("blur_enabled", True))
        processing["morphology_enabled"] = bool(processing.get("morphology_enabled", True))
        processing["exclude_blue_floor"] = bool(processing.get("exclude_blue_floor", False))
        processing["blue_hsv_low"] = self._normalize_hsv_triplet(
            processing.get("blue_hsv_low", [90, 50, 40]),
            [90, 50, 40],
        )
        processing["blue_hsv_high"] = self._normalize_hsv_triplet(
            processing.get("blue_hsv_high", [135, 255, 255]),
            [135, 255, 255],
        )

        logging_cfg = config["logging"]
        logging_cfg["enable_csv"] = bool(logging_cfg.get("enable_csv", True))
        logging_cfg["csv_path"] = str(logging_cfg.get("csv_path", "logs/speed_log.csv"))

    def _normalize_point(self, point: Any) -> list[float]:
        if isinstance(point, (list, tuple)) and len(point) == 2:
            return [float(point[0]), float(point[1])]
        return [0.0, 0.0]

    def _normalize_matrix(self, matrix: Any) -> list[list[float]] | None:
        if matrix is None:
            return None
        if isinstance(matrix, list):
            return [
                [float(value) for value in row]
                for row in matrix
                if isinstance(row, (list, tuple))
            ]
        return None

    def _normalize_vector(self, vector: Any) -> list[float] | None:
        if vector is None:
            return None
        if isinstance(vector, list):
            return [float(value) for value in vector]
        return None

    def _normalize_odd_int(self, value: Any, default: int, minimum: int = 1) -> int:
        try:
            normalized = max(int(value), minimum)
        except (TypeError, ValueError):
            normalized = default
        if normalized % 2 == 0:
            normalized += 1
        return normalized

    def _normalize_hsv_triplet(self, value: Any, default: list[int]) -> list[int]:
        if not isinstance(value, list) or len(value) != 3:
            return default
        return [int(max(0, min(component, 255))) for component in value]
