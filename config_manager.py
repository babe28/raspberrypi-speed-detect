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
        "usb_settings": {
            "auto_exposure": True,
            "exposure": None,
            "brightness": None,
            "contrast": None,
            "saturation": None,
            "sharpness": None,
            "gain": None,
            "autofocus": False,
            "focus": None,
        },
        "csi_settings": {
            "auto_exposure": True,
            "exposure_time_us": None,
            "analogue_gain": None,
            "brightness": 0.0,
            "contrast": 1.0,
            "saturation": 1.0,
            "sharpness": 1.0,
            "auto_white_balance": True,
            "colour_gain_red": None,
            "colour_gain_blue": None,
        },
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
        "points": [],
    },
    "measurement": {
        "mode": "tracking",
        "overlay_hold_seconds": 5.0,
        "repeat_behavior": "normal",
        "repeat_cooldown_seconds": 0.0,
        "tracking": {
            "direction": "any",
        },
        "line_crossing": {
            "line_a": [],
            "line_b": [],
            "distance_m": 2.0,
        },
    },
    "processing": {
        "detection_enabled": False,
        "downscale_factor": 0.5,
        "frame_skip": 0,
        "min_contour_area": 500,
        "max_contour_area": 50000,
        "min_speed_kmh": 0.0,
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
        "show_fps_overlay": False,
        "show_mask_preview": True,
        "undistort_enabled": True,
        "manual_distortion": 0.0,
        "perspective_enabled": True,
        "brightness_offset": 0,
        "contrast_gain": 1.0,
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
        self._validate(config)
        return config

    def save(self, config: dict[str, Any]) -> None:
        normalized = copy.deepcopy(config)
        self._normalize(normalized)
        self._validate(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(normalized, fh, indent=2, ensure_ascii=False)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        config = self.load()
        updated = self._deep_merge(config, patch)
        self._normalize(updated)
        self._validate(updated)
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
        try:
            camera["device"] = int(camera.get("device", 0))
        except (TypeError, ValueError):
            camera["device"] = 0
        camera["fps"] = int(camera.get("fps", 30))
        camera["type"] = str(camera.get("type", "usb")).lower()
        if camera["type"] not in {"usb", "csi", "rtsp"}:
            camera["type"] = "usb"
        camera["rtsp_enabled"] = camera["type"] == "rtsp" or bool(
            camera.get("rtsp_enabled", False)
        )
        if camera["rtsp_enabled"]:
            camera["type"] = "rtsp"
        camera["rtsp_url"] = str(camera.get("rtsp_url", ""))
        usb_settings = camera.get("usb_settings", {})
        camera["usb_settings"] = {
            "auto_exposure": bool(usb_settings.get("auto_exposure", True)),
            "exposure": self._normalize_optional_float(usb_settings.get("exposure")),
            "brightness": self._normalize_optional_float(usb_settings.get("brightness")),
            "contrast": self._normalize_optional_float(usb_settings.get("contrast")),
            "saturation": self._normalize_optional_float(usb_settings.get("saturation")),
            "sharpness": self._normalize_optional_float(usb_settings.get("sharpness")),
            "gain": self._normalize_optional_float(usb_settings.get("gain")),
            "autofocus": bool(usb_settings.get("autofocus", False)),
            "focus": self._normalize_optional_float(usb_settings.get("focus")),
        }
        csi_settings = camera.get("csi_settings", {})
        camera["csi_settings"] = {
            "auto_exposure": bool(csi_settings.get("auto_exposure", True)),
            "exposure_time_us": self._normalize_optional_int(csi_settings.get("exposure_time_us")),
            "analogue_gain": self._normalize_optional_float(csi_settings.get("analogue_gain")),
            "brightness": float(csi_settings.get("brightness", 0.0)),
            "contrast": float(csi_settings.get("contrast", 1.0)),
            "saturation": float(csi_settings.get("saturation", 1.0)),
            "sharpness": float(csi_settings.get("sharpness", 1.0)),
            "auto_white_balance": bool(csi_settings.get("auto_white_balance", True)),
            "colour_gain_red": self._normalize_optional_float(csi_settings.get("colour_gain_red")),
            "colour_gain_blue": self._normalize_optional_float(csi_settings.get("colour_gain_blue")),
        }

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
        scale["points"] = [self._normalize_point(point) for point in scale.get("points", [])]

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
        tracking = measurement.get("tracking", {})
        measurement["tracking"] = {
            "direction": str(tracking.get("direction", "any")).lower(),
        }
        line_crossing = measurement.get("line_crossing", {})
        measurement["line_crossing"] = {
            "line_a": [self._normalize_point(point) for point in line_crossing.get("line_a", [])],
            "line_b": [self._normalize_point(point) for point in line_crossing.get("line_b", [])],
            "distance_m": float(line_crossing.get("distance_m", scale["known_distance_m"] or 2.0)),
        }

        processing = config["processing"]
        processing["detection_enabled"] = bool(processing.get("detection_enabled", False))
        processing["downscale_factor"] = float(processing.get("downscale_factor", 0.5))
        processing["frame_skip"] = max(0, int(processing.get("frame_skip", 0)))
        processing["min_contour_area"] = int(processing.get("min_contour_area", 500))
        processing["max_contour_area"] = int(processing.get("max_contour_area", 50000))
        processing["min_speed_kmh"] = max(0.0, float(processing.get("min_speed_kmh", 0.0)))
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
        processing["show_fps_overlay"] = bool(processing.get("show_fps_overlay", False))
        processing["show_mask_preview"] = bool(processing.get("show_mask_preview", True))
        processing["undistort_enabled"] = bool(processing.get("undistort_enabled", True))
        processing["manual_distortion"] = max(
            -1.0, min(1.0, float(processing.get("manual_distortion", 0.0)))
        )
        processing["perspective_enabled"] = bool(processing.get("perspective_enabled", True))
        processing["brightness_offset"] = int(processing.get("brightness_offset", 0))
        processing["contrast_gain"] = max(0.1, float(processing.get("contrast_gain", 1.0)))
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

    def _validate(self, config: dict[str, Any]) -> None:
        camera = config["camera"]
        processing = config["processing"]
        measurement = config["measurement"]

        if camera["type"] not in {"usb", "csi", "rtsp"}:
            raise ValueError("camera.type must be usb, csi, or rtsp.")
        if camera["resolution"][0] <= 0 or camera["resolution"][1] <= 0:
            raise ValueError("Camera resolution values must be positive.")
        if camera["fps"] <= 0:
            raise ValueError("FPS must be at least 1.")
        if camera["type"] == "usb" and camera["device"] < 0:
            raise ValueError("USB camera device index must be 0 or greater.")
        if camera["type"] == "rtsp" and not camera["rtsp_url"].strip():
            raise ValueError("RTSP URL is required when camera.type is rtsp.")
        if camera["csi_settings"]["exposure_time_us"] is not None and camera["csi_settings"]["exposure_time_us"] <= 0:
            raise ValueError("CSI exposure_time_us must be positive.")
        if camera["csi_settings"]["analogue_gain"] is not None and camera["csi_settings"]["analogue_gain"] <= 0:
            raise ValueError("CSI analogue_gain must be positive.")
        if camera["csi_settings"]["colour_gain_red"] is not None and camera["csi_settings"]["colour_gain_red"] <= 0:
            raise ValueError("CSI colour_gain_red must be positive.")
        if camera["csi_settings"]["colour_gain_blue"] is not None and camera["csi_settings"]["colour_gain_blue"] <= 0:
            raise ValueError("CSI colour_gain_blue must be positive.")

        if not 0.1 <= processing["downscale_factor"] <= 1.0:
            raise ValueError("downscale_factor must be between 0.1 and 1.0.")
        if processing["frame_skip"] < 0 or processing["frame_skip"] > 10:
            raise ValueError("frame_skip must be between 0 and 10.")
        if processing["min_contour_area"] <= 0:
            raise ValueError("min_contour_area must be at least 1.")
        if processing["max_contour_area"] < processing["min_contour_area"]:
            raise ValueError("max_contour_area must be greater than or equal to min_contour_area.")
        if processing["max_speed_kmh"] <= 0:
            raise ValueError("max_speed_kmh must be positive.")
        if processing["max_speed_kmh"] < processing["min_speed_kmh"]:
            raise ValueError("max_speed_kmh must be greater than or equal to min_speed_kmh.")
        if processing["background_history"] <= 0:
            raise ValueError("background_history must be at least 1.")
        if processing["background_var_threshold"] <= 0:
            raise ValueError("background_var_threshold must be at least 1.")
        if processing["track_max_distance"] <= 0:
            raise ValueError("track_max_distance must be positive.")
        if processing["track_max_missing_frames"] <= 0:
            raise ValueError("track_max_missing_frames must be at least 1.")

        if measurement["mode"] not in {"tracking", "line_crossing"}:
            raise ValueError("measurement.mode must be tracking or line_crossing.")
        if measurement["tracking"]["direction"] not in {
            "any",
            "left_to_right",
            "right_to_left",
            "top_to_bottom",
            "bottom_to_top",
        }:
            raise ValueError(
                "measurement.tracking.direction must be any, left_to_right, right_to_left, top_to_bottom, or bottom_to_top."
            )
        if measurement["overlay_hold_seconds"] <= 0:
            raise ValueError("overlay_hold_seconds must be positive.")
        if measurement["line_crossing"]["distance_m"] <= 0:
            raise ValueError("line_crossing.distance_m must be positive.")

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

    def _normalize_optional_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_optional_int(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
