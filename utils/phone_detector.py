from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from ultralytics import YOLO  # type: ignore


@dataclass(frozen=True)
class PhoneDetection:
    present: bool
    confidence: float
    boxes_xyxy: list[list[float]]


class PhoneDetector:
    """
    YOLOv8n cell-phone detector tuned for in-car dashcam usage.

    Key design decisions:
    - Detects class 67 (cell phone) only — class 0 (person) detection removed.
      Reason: close-up dashcam frames often miss the 'person' class entirely,
      which was silently blocking all valid phone detections.
    - Low confidence threshold (0.18) to catch partially occluded phones.
    - Minimal area filter (0.001) to catch phones at arm's length.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        conf: float = 0.18,
        imgsz: int = 416,
    ):
        self.model = YOLO(model_path)
        self.conf = float(conf)
        self.imgsz = int(imgsz)

    def detect(self, frame_bgr: np.ndarray) -> PhoneDetection:
        if frame_bgr is None:
            return PhoneDetection(False, 0.0, [])

        h, w = frame_bgr.shape[:2]

        results = self.model.predict(
            source=frame_bgr,
            verbose=False,
            imgsz=self.imgsz,
            conf=self.conf,
            classes=[67],  # ONLY class 67 = cell phone (removed person class)
        )

        if not results:
            return PhoneDetection(False, 0.0, [])

        r0 = results[0]
        if not hasattr(r0, "boxes") or r0.boxes is None or len(r0.boxes) == 0:
            return PhoneDetection(False, 0.0, [])

        boxes     = r0.boxes
        cpu_boxes = boxes.xyxy.cpu().numpy().tolist()
        cpu_confs = boxes.conf.cpu().numpy().tolist()

        filtered_xyxy: list[list[float]] = []
        confs_out:     list[float]       = []

        for box, conf in zip(cpu_boxes, cpu_confs):
            x1, y1, x2, y2 = box
            area       = (x2 - x1) * (y2 - y1)
            area_ratio = area / float(w * h + 1e-6)
            cy         = (y1 + y2) / 2.0

            # Filter 1: Skip extremely tiny detections (pure noise)
            if area_ratio < 0.001:
                continue

            # Filter 2: Skip phones at the very bottom of the frame (passenger's lap)
            if cy > h * 0.92:
                continue

            filtered_xyxy.append(box)
            confs_out.append(float(conf))

        if not filtered_xyxy:
            return PhoneDetection(False, 0.0, [])

        best = float(max(confs_out))
        return PhoneDetection(True, best, filtered_xyxy)
