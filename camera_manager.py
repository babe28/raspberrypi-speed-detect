from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    from picamera2 import Picamera2
except ImportError:  # pragma: no cover - optional dependency on non-Pi machines
    Picamera2 = None


class CameraManager:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        camera_config = config["camera"]
        self.camera_type = camera_config["type"]
        self.device = camera_config["device"]
        self.rtsp_enabled = self.camera_type == "rtsp" or bool(
            camera_config.get("rtsp_enabled", False)
        )
        self.rtsp_url = str(camera_config.get("rtsp_url", ""))
        self.width, self.height = camera_config["resolution"]
        self.fps = camera_config["fps"]
        self.rotation = int(camera_config.get("rotation", 0))
        self.flip_horizontal = bool(camera_config.get("flip_horizontal", False))
        self.flip_vertical = bool(camera_config.get("flip_vertical", False))
        self.usb_settings = camera_config.get("usb_settings", {})
        self.csi_settings = camera_config.get("csi_settings", {})
        self.csi_tuning_file = str(self.csi_settings.get("tuning_file", "")).strip()
        self.downscale_factor = float(config["processing"]["downscale_factor"])
        self.cap: cv2.VideoCapture | None = None
        self.picam2: Any = None
        self.last_csi_metadata: dict[str, Any] = {}

    def start(self) -> None:
        if self.camera_type == "rtsp" or self.rtsp_enabled:
            self._start_rtsp_stream()
            return

        if self.camera_type == "csi":
            self._start_csi_camera()
            return

        if self.camera_type != "usb":
            raise RuntimeError(
                f"Unsupported camera type: {self.camera_type}. Expected usb, csi, or rtsp."
            )

        self.cap = cv2.VideoCapture(self.device)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)
        
        # USB buffer settings to prevent frame drops and freezes
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            raise RuntimeError(f"USB camera could not be opened: device={self.device}")

        self._apply_usb_settings()

    def _start_rtsp_stream(self) -> None:
        if not self.rtsp_url.strip():
            raise RuntimeError("RTSP was selected but rtsp_url is empty.")

        candidates = [self.rtsp_url]
        if "rtsp_transport=" not in self.rtsp_url:
            separator = "&" if "?" in self.rtsp_url else "?"
            candidates.append(f"{self.rtsp_url}{separator}rtsp_transport=tcp")

        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|buffer_size;1024000"
        backends = [cv2.CAP_FFMPEG, cv2.CAP_ANY]
        last_error = "RTSP stream could not be opened"

        for url in candidates:
            for backend in backends:
                cap = cv2.VideoCapture(url, backend)
                if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)

                if not cap.isOpened():
                    cap.release()
                    continue

                ok, frame = cap.read()
                if ok and frame is not None:
                    self.cap = cap
                    return

                last_error = f"RTSP opened but no frames were received: backend={backend}"
                cap.release()

        raise RuntimeError(
            f"{last_error}. URL={self.rtsp_url}. Try TCP transport and verify the stream with ffplay or VLC."
        )

    def read(self) -> tuple[bool, np.ndarray | None]:
        frame: np.ndarray | None
        if self.camera_type == "csi":
            if self.picam2 is None:
                return False, None
            try:
                request = self.picam2.capture_request()
            except Exception:
                return False, None
            try:
                frame = request.make_array("main")
                metadata = request.get_metadata()
                if isinstance(metadata, dict):
                    self.last_csi_metadata = metadata
                else:
                    self.last_csi_metadata = {}
            finally:
                request.release()
            if frame is None:
                return False, None
            if frame.ndim == 3 and frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            elif frame.ndim == 3 and frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            frame = self._downscale(frame)
            frame = self._apply_orientation(frame)
            return True, frame

        if self.cap is None:
            return False, None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return False, None
        frame = self._downscale(frame)
        frame = self._apply_orientation(frame)
        return True, frame

    def stop(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        if self.picam2 is not None:
            self.picam2.stop()
            self.picam2.close()
            self.picam2 = None

    def _downscale(self, frame: np.ndarray) -> np.ndarray:
        if 0 < self.downscale_factor < 1:
            return cv2.resize(
                frame,
                None,
                fx=self.downscale_factor,
                fy=self.downscale_factor,
                interpolation=cv2.INTER_AREA,
            )
        return frame

    def _apply_orientation(self, frame: np.ndarray) -> np.ndarray:
        if self.rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif self.rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        if self.flip_horizontal and self.flip_vertical:
            frame = cv2.flip(frame, -1)
        elif self.flip_horizontal:
            frame = cv2.flip(frame, 1)
        elif self.flip_vertical:
            frame = cv2.flip(frame, 0)

        return frame

    def _start_csi_camera(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("picamera2 is not installed but camera.type='csi' was selected")

        if self.csi_tuning_file:
            tuning_path = Path(self.csi_tuning_file).expanduser()
            if not tuning_path.is_absolute():
                tuning_path = Path.cwd() / tuning_path
            if not tuning_path.exists():
                raise RuntimeError(f"CSI tuning file could not be found: {tuning_path}")
            try:
                tuning = Picamera2.load_tuning_file(str(tuning_path))
                self.picam2 = Picamera2(tuning=tuning)
            except Exception as exc:
                raise RuntimeError(
                    f"CSI tuning file could not be loaded: {tuning_path} ({exc})"
                ) from exc
        else:
            self.picam2 = Picamera2()
        controls = self._build_csi_controls()
        preview_config = self.picam2.create_preview_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"},
            controls=controls,
        )
        self.picam2.configure(preview_config)
        self.picam2.start()
        self._apply_csi_runtime_controls()

    def _apply_usb_settings(self) -> None:
        if self.cap is None:
            return
        
        # Apply buffer size setting
        buffer_size = self.usb_settings.get("buffer_size", 1)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, buffer_size)

        self._set_cap_prop("CAP_PROP_AUTO_EXPOSURE", 0.75 if self.usb_settings.get("auto_exposure", True) else 0.25)
        for prop_name, key in (
            ("CAP_PROP_BRIGHTNESS", "brightness"),
            ("CAP_PROP_CONTRAST", "contrast"),
            ("CAP_PROP_SATURATION", "saturation"),
            ("CAP_PROP_SHARPNESS", "sharpness"),
            ("CAP_PROP_GAIN", "gain"),
            ("CAP_PROP_EXPOSURE", "exposure"),
            ("CAP_PROP_FOCUS", "focus"),
        ):
            self._set_cap_prop(prop_name, self.usb_settings.get(key))

        autofocus = self.usb_settings.get("autofocus")
        if autofocus is not None:
            self._set_cap_prop("CAP_PROP_AUTOFOCUS", 1 if autofocus else 0)

    def _build_csi_controls(self) -> dict[str, Any]:
        controls: dict[str, Any] = {"FrameRate": self.fps}
        auto_exposure = bool(self.csi_settings.get("auto_exposure", True))
        controls["AeEnable"] = auto_exposure

        exposure_time_us = self.csi_settings.get("exposure_time_us")
        if not auto_exposure and exposure_time_us is not None:
            controls["ExposureTime"] = int(exposure_time_us)

        analogue_gain = self.csi_settings.get("analogue_gain")
        if not auto_exposure and analogue_gain is not None:
            controls["AnalogueGain"] = float(analogue_gain)

        controls["Brightness"] = float(self.csi_settings.get("brightness", 0.0))
        controls["Contrast"] = float(self.csi_settings.get("contrast", 1.0))
        controls["Saturation"] = float(self.csi_settings.get("saturation", 1.0))
        controls["Sharpness"] = float(self.csi_settings.get("sharpness", 1.0))

        auto_wb = bool(self.csi_settings.get("auto_white_balance", True))
        controls["AwbEnable"] = auto_wb
        red_gain = self.csi_settings.get("colour_gain_red")
        blue_gain = self.csi_settings.get("colour_gain_blue")
        if not auto_wb and red_gain is not None and blue_gain is not None:
            controls["ColourGains"] = (float(red_gain), float(blue_gain))

        return controls

    def _apply_csi_runtime_controls(self) -> None:
        if self.picam2 is None:
            return

        controls: dict[str, Any] = {}
        auto_exposure = bool(self.csi_settings.get("auto_exposure", True))
        exposure_time_us = self.csi_settings.get("exposure_time_us")
        analogue_gain = self.csi_settings.get("analogue_gain")
        auto_wb = bool(self.csi_settings.get("auto_white_balance", True))
        red_gain = self.csi_settings.get("colour_gain_red")
        blue_gain = self.csi_settings.get("colour_gain_blue")

        if not auto_exposure and exposure_time_us is not None:
            controls["ExposureTime"] = int(exposure_time_us)
        if not auto_exposure and analogue_gain is not None:
            controls["AnalogueGain"] = float(analogue_gain)
        if not auto_wb and red_gain is not None and blue_gain is not None:
            controls["ColourGains"] = (float(red_gain), float(blue_gain))

        if controls:
            self.picam2.set_controls(controls)

    def _set_cap_prop(self, prop_name: str, value: float | int | None) -> None:
        if self.cap is None or value is None:
            return
        prop = getattr(cv2, prop_name, None)
        if prop is None:
            return
        try:
            self.cap.set(prop, float(value))
        except Exception:
            return

    def runtime_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"camera_type": self.camera_type}
        if self.camera_type == "csi":
            metadata = self.last_csi_metadata or {}
            info["csi"] = {
                "tuning_file": self.csi_tuning_file or None,
                "exposure_time_us": metadata.get("ExposureTime"),
                "analogue_gain": metadata.get("AnalogueGain"),
                "awb_enabled": metadata.get("AwbEnable"),
                "colour_gains": metadata.get("ColourGains"),
            }
        return info
