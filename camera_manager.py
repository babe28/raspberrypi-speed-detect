from __future__ import annotations

import os
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
        self.camera_type = config["camera"]["type"]
        self.device = config["camera"]["device"]
        self.rtsp_enabled = bool(config["camera"].get("rtsp_enabled", False))
        self.rtsp_url = str(config["camera"].get("rtsp_url", ""))
        self.width, self.height = config["camera"]["resolution"]
        self.fps = config["camera"]["fps"]
        self.downscale_factor = float(config["processing"]["downscale_factor"])
        self.cap: cv2.VideoCapture | None = None
        self.picam2: Any = None

    def start(self) -> None:
        if self.rtsp_enabled:
            self._start_rtsp_stream()
            return

        if self.camera_type == "csi":
            self._start_csi_camera()
            return

        self.cap = cv2.VideoCapture(self.device)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        if not self.cap.isOpened():
            raise RuntimeError(f"USB camera could not be opened: device={self.device}")

    def _start_rtsp_stream(self) -> None:
        if not self.rtsp_url:
            raise RuntimeError("RTSP is enabled but rtsp_url is empty")

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
            f"{last_error}. URL={self.rtsp_url}. Try tcp transport and verify the stream with ffplay/vlc."
        )

    def read(self) -> tuple[bool, np.ndarray | None]:
        frame: np.ndarray | None
        if self.camera_type == "csi":
            if self.picam2 is None:
                return False, None
            frame = self.picam2.capture_array()
            if frame is None:
                return False, None
            if frame.ndim == 3 and frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            elif frame.ndim == 3 and frame.shape[2] == 3:
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return True, self._downscale(frame)

        if self.cap is None:
            return False, None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return False, None
        return True, self._downscale(frame)

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

    def _start_csi_camera(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("picamera2 is not installed but camera.type='csi' was selected")

        self.picam2 = Picamera2()
        preview_config = self.picam2.create_preview_configuration(
            main={"size": (self.width, self.height), "format": "RGB888"},
            controls={"FrameRate": self.fps},
        )
        self.picam2.configure(preview_config)
        self.picam2.start()
