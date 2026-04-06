from __future__ import annotations

import collections
import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DrowsinessResult:
    ear: float | None
    eyes_closed: bool


class DrowsinessDetector:
    """
    MediaPipe FaceMesh EAR-based drowsiness detection with dynamic EAR calibration.

    On startup, measures the driver's personal open-eye EAR baseline over the first
    CALIB_FRAMES valid readings. The threshold is then set to 75% of their personal
    baseline — far more accurate than a fixed threshold for all people.

    Uses a 4-frame rolling average to smooth JPEG compression noise.
    """

    _LEFT_EYE  = (33, 160, 158, 133, 153, 144)
    _RIGHT_EYE = (362, 385, 387, 263, 373, 380)

    CALIB_FRAMES = 40       # Collect 40 valid frames (~4s) to calibrate
    CALIB_RATIO  = 0.72     # Threshold = 72% of open-eye baseline
    DEFAULT_THRESHOLD = 0.21  # Fallback before calibration completes

    def __init__(self, ear_threshold: float = 0.21):
        self.ear_threshold = float(ear_threshold)
        self._configured_threshold = float(ear_threshold)  # keep original for reset

        import mediapipe as mp  # type: ignore
        self._mp_face_mesh = mp.solutions.face_mesh
        self._face_mesh = self._mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,        # Focus on one driver only
            refine_landmarks=True,  # Required for precise eye landmark positions
            min_detection_confidence=0.45,
            min_tracking_confidence=0.45,
        )

        # Rolling average buffer (4 frames smooths JPEG artifacts)
        self._ear_buffer: collections.deque[float] = collections.deque(maxlen=4)

        # Calibration state
        self._calib_samples: list[float] = []
        self._calibrated = False

    @staticmethod
    def _dist(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(a - b))

    def _eye_ear(self, pts: np.ndarray) -> float:
        p1, p2, p3, p4, p5, p6 = pts
        denom = 2.0 * self._dist(p1, p4)
        if denom <= 1e-6:
            return 0.30
        return (self._dist(p2, p6) + self._dist(p3, p5)) / denom

    def process(self, frame_bgr: np.ndarray) -> DrowsinessResult:
        if frame_bgr is None:
            return DrowsinessResult(ear=None, eyes_closed=False)

        h, w = frame_bgr.shape[:2]
        if w < 480:
            scale = 480.0 / w
            frame_bgr = cv2.resize(frame_bgr, (480, int(h * scale)), interpolation=cv2.INTER_LINEAR)
            h, w = frame_bgr.shape[:2]

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self._face_mesh.process(rgb)

        if not res.multi_face_landmarks:
            self._ear_buffer.clear()
            return DrowsinessResult(ear=None, eyes_closed=False)

        face = res.multi_face_landmarks[0]

        def lm_xy(i: int) -> np.ndarray:
            lm = face.landmark[i]
            return np.array([lm.x * w, lm.y * h], dtype=np.float32)

        left_pts  = np.stack([lm_xy(i) for i in self._LEFT_EYE],  axis=0)
        right_pts = np.stack([lm_xy(i) for i in self._RIGHT_EYE], axis=0)

        raw_ear = (self._eye_ear(left_pts) + self._eye_ear(right_pts)) / 2.0

        if math.isnan(raw_ear) or math.isinf(raw_ear):
            return DrowsinessResult(ear=None, eyes_closed=False)

        # --- DYNAMIC CALIBRATION ---
        # Collect high-EAR readings (open-eye state) during the calibration window.
        # Only sample if EAR is above the default threshold (driver's eyes are open).
        if not self._calibrated:
            if raw_ear > self.DEFAULT_THRESHOLD:
                self._calib_samples.append(raw_ear)
                if len(self._calib_samples) >= self.CALIB_FRAMES:
                    baseline = float(np.mean(self._calib_samples))
                    self.ear_threshold = round(baseline * self.CALIB_RATIO, 4)
                    self._calibrated = True
                    print(f"[DrowsinessDetector] Calibrated! Baseline EAR={baseline:.4f} "
                          f"→ Threshold={self.ear_threshold:.4f}")

        # 4-frame rolling average
        self._ear_buffer.append(raw_ear)
        ear = float(np.mean(self._ear_buffer))

        # Single-frame threshold check — pipeline hysteresis handles debouncing
        eyes_closed = ear < self.ear_threshold

        return DrowsinessResult(ear=round(ear, 4), eyes_closed=eyes_closed)
