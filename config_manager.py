import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "camera": {
        "type": "usb",
        "device": 0,
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
    "processing": {
        "downscale_factor": 0.5,
        "min_contour_area": 500,
        "max_speed_kmh": 50.0,
        "warmup_frames": 15,
        "background_history": 300,
        "background_var_threshold": 32,
        "track_max_distance": 80,
        "track_max_missing_frames": 8,
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

        processing = config["processing"]
        processing["downscale_factor"] = float(processing.get("downscale_factor", 0.5))
        processing["min_contour_area"] = int(processing.get("min_contour_area", 500))
        processing["max_speed_kmh"] = float(processing.get("max_speed_kmh", 50.0))
        processing["warmup_frames"] = int(processing.get("warmup_frames", 15))
        processing["background_history"] = int(processing.get("background_history", 300))
        processing["background_var_threshold"] = int(
            processing.get("background_var_threshold", 32)
        )
        processing["track_max_distance"] = float(processing.get("track_max_distance", 80))
        processing["track_max_missing_frames"] = int(
            processing.get("track_max_missing_frames", 8)
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
