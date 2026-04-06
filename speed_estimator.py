from __future__ import annotations

import argparse
import time

import cv2

from camera_manager import CameraManager
from config_manager import ConfigManager
from speed_estimator_core import SpeedEstimator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Soapbox Speed Camera CLI")
    parser.add_argument("--config", default="config.json", help="Path to config file")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Display annotated preview window while estimating speed",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Stop after N frames (0 means unlimited)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = ConfigManager(args.config).load()
    camera = CameraManager(config)
    estimator = SpeedEstimator(config)
    camera.start()

    frame_count = 0
    start_time = time.time()

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError("Camera frame could not be read.")

            annotated, events = estimator.process(frame)
            frame_count += 1

            if events:
                speeds = ", ".join(
                    f"ID {event['id']}: {event['speed_kmh']:.1f} km/h" for event in events
                )
                print(speeds)

            if args.stream:
                cv2.imshow("Soapbox Speed Camera", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if args.max_frames and frame_count >= args.max_frames:
                break
    finally:
        elapsed = max(time.time() - start_time, 0.001)
        print(f"Processed {frame_count} frames in {elapsed:.1f}s ({frame_count / elapsed:.1f} FPS)")
        estimator.close()
        camera.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
