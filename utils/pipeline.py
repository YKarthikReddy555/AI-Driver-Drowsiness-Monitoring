
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
import collections
import uuid
import os

import cv2
import numpy as np

from utils.drowsiness_detector import DrowsinessDetector
from utils.phone_detector import PhoneDetector, PhoneDetection
from utils.database import EventLogger
from concurrent.futures import ThreadPoolExecutor


@dataclass
class SafetyStatus:
    # Severity levels: 0 safe, 1 warning, 2 danger, 3 critical (phone only)
    drowsiness_level: int = 0
    phone_level: int = 0
    overall_level: int = 0

    drowsiness_duration_s: float = 0.0
    phone_duration_s: float = 0.0

    drowsiness_message: str = "Safe"
    phone_message: str = "Safe"
    overall_message: str = "Safe"

    # Client-side voice: token increments each time we want the browser to speak
    voice_token: int = 0
    voice_text: str = ""

    # Debug/telemetry
    ear: float | None = None
    phone_conf: float = 0.0
    phone_boxes: list[list[float]] = field(default_factory=list)

    camera_running: bool = False
    fps_capture: float = 0.0
    fps_process: float = 0.0


class CameraCapture:
    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480):
        self.camera_index = int(camera_index)
        self.width = int(width)
        self.height = int(height)

        self._cap: Optional[cv2.VideoCapture] = None
        self._t: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_id: int = 0

        self._fps = 0.0

    def start(self) -> None:
        if self._t and self._t.is_alive():
            return
        self._stop.clear()
        self._cap = cv2.VideoCapture(self.camera_index)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap.set(cv2.CAP_PROP_FPS, 60)  # Request 60 FPS
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=1.5)
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def read_latest(self) -> tuple[Optional[np.ndarray], int]:
        with self._lock:
            if self._frame is None:
                return None, 0
            return self._frame.copy(), self._frame_id

    def fps(self) -> float:
        return float(self._fps)

    def _run(self) -> None:
        last_t = time.monotonic()
        frames = 0
        while not self._stop.is_set():
            if not self._cap:
                time.sleep(0.01)
                continue
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._lock:
                self._frame = frame
                self._frame_id += 1

            frames += 1
            now = time.monotonic()
            if now - last_t >= 1.0:
                self._fps = frames / (now - last_t)
                frames = 0
                last_t = now


class SafetyPipeline:
    def __init__(
        self,
        *,
        camera_index: int = 0,
        width: int = 640,
        height: int = 480,
        drowsy_warning_s: float = 2.0,
        drowsy_danger_s: float = 5.0,
        phone_warning_s: float = 3.0,
        phone_danger_s: float = 7.0,
        phone_critical_s: float = 12.0,
        phone_detect_every_n: int = 3,
        voice_repeat_s: float = 3.0,
        db_path: str = "database.db",
        enable_server_tts: bool = False,
        ear_threshold: float = 0.20,
        frame_buffer_size: int = 100,
    ):
        self.capture = CameraCapture(camera_index=camera_index, width=width, height=height)
        self.status = SafetyStatus()

        self._status_lock = threading.Lock()
        self._jpeg_lock = threading.Lock()
        self._processing_lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None

        self._stop = threading.Event()
        self._t: Optional[threading.Thread] = None

        self._drowsiness_detector = DrowsinessDetector(ear_threshold=ear_threshold)
        self._phone_detector = PhoneDetector()
        self._logger = EventLogger(db_path=db_path)
        self._frame_buffer = collections.deque(maxlen=int(frame_buffer_size))

        self._drowsy_warning_s = float(drowsy_warning_s)
        self._drowsy_danger_s = float(drowsy_danger_s)
        self._phone_warning_s = float(phone_warning_s)
        self._phone_danger_s = float(phone_danger_s)
        self._phone_critical_s = float(phone_critical_s)
        self._phone_detect_every_n = 1  # Check phone every frame (runs in parallel, zero extra cost)
        self._voice_repeat_s = float(voice_repeat_s)

        self._eye_closed_start: Optional[float] = None
        self._phone_start: Optional[float] = None

        self._prev_drowsy_level = 0
        self._prev_phone_level = 0
        self._last_phone_voice = 0.0
        self._last_drowsy_voice = 0.0
        self._last_face_timestamp = 0.0
        self._last_phone_timestamp = 0.0
        self._last_eye_closed_timestamp = 0.0
        self._last_process_time = 0.0

        self._process_fps = 0.0

        self._frame_buffer = collections.deque(maxlen=100) # 10 seconds at 10fps
        self._record_post_frames = 0
        self._incident_frames_to_save: list[np.ndarray] = []
        self._incident_type_in_progress = ""
        self._external_frame_idx = 0
        self._cached_phone = PhoneDetection(present=False, confidence=0.0, boxes_xyxy=[])

        self._server_tts = None
        if enable_server_tts:
            from utils.audio_alert import AudioAlerter
            self._server_tts = AudioAlerter()
            
        self.driver_id: Optional[int] = None
        self._executor = ThreadPoolExecutor(max_workers=2) # V8 - Parallel AI Engine

    def set_driver_id(self, driver_id: Optional[int]) -> None:
        self.driver_id = driver_id

    def start(self) -> None:
        if self._t and self._t.is_alive():
            return
        self._stop.clear()
        self.capture.start()
        with self._status_lock:
            self.status.camera_running = True
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=2.0)
        self.capture.stop()
        with self._status_lock:
            self.status.camera_running = False
            self.status.overall_level = 0
            self.status.drowsiness_level = 0
            self.status.phone_level = 0
            self.status.overall_message = "Stopped"
        if self._server_tts:
            self._server_tts.stop()

    def get_status(self) -> SafetyStatus:
        with self._status_lock:
            return SafetyStatus(**self.status.__dict__)

    def _clone_frame(self, frame: np.ndarray) -> np.ndarray:
        """Creates a strictly detached, C-aligned copy of the frame memory."""
        if frame is None: return None
        # Copy + Contiguous Alignment + C-Order forces a fresh, independent memory block
        return np.ascontiguousarray(frame.copy(), dtype=np.uint8)

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._jpeg_lock:
            return self._latest_jpeg

    def process_external_frame(self, frame: np.ndarray) -> SafetyStatus:
        """Process a single frame provided externally (e.g. from a web upload)."""
        return self._do_process_frame(frame)

    def _do_process_frame(self, frame: np.ndarray) -> SafetyStatus:
        if not self._processing_lock.acquire(blocking=False):
            return self.get_status()

        try:
            now_start = time.monotonic()
            frame_idx = self._external_frame_idx
            self._external_frame_idx += 1

            # Memory detachment for background indexing
            orig_frame = self._clone_frame(frame)
            self._frame_buffer.append(orig_frame)

            # --- INCIDENT VIDEO RECORDING ---
            # Collect post-incident frames to build the 20-second video
            if self._record_post_frames > 0:
                self._incident_frames_to_save.append(orig_frame)
                self._record_post_frames -= 1
                if self._record_post_frames == 0:
                    # Video complete: fire background save worker
                    frames_snapshot = list(self._incident_frames_to_save)
                    inc_type_snapshot = self._incident_type_in_progress
                    d_id_snapshot = self.driver_id
                    self._incident_frames_to_save = []
                    self._incident_type_in_progress = ""
                    threading.Thread(
                        target=self._save_incident_video,
                        args=(frames_snapshot, inc_type_snapshot, d_id_snapshot),
                        daemon=True
                    ).start()

            # --- 1. PARALLEL AI ENGINE (V8) ---
            # Run Drowsiness and Phone detection simultaneously on separate threads
            f_drowsy = self._executor.submit(self._drowsiness_detector.process, frame)
            
            # Predict phone only every N frames to save CPU, otherwise use cached
            if frame_idx % self._phone_detect_every_n == 0:
                f_phone = self._executor.submit(self._phone_detector.detect, frame)
            else:
                f_phone = None 

            # Wait for parallel results
            d = f_drowsy.result()
            pd = f_phone.result() if f_phone else self._cached_phone
            self._cached_phone = pd

            now = time.monotonic() # Update time after AI processing
            
            # --- 2. DROWSINESS LOGIC (V8 RESILIENT) ---
            if d.ear is not None:
                self._last_face_timestamp = now
                if d.eyes_closed:
                    if self._eye_closed_start is None: self._eye_closed_start = now
                    self._last_eye_closed_timestamp = now
                else:
                    # 0.3s eye-open hysteresis â€” brief eye flicker won't cancel the timer
                    if self._eye_closed_start is not None and (now - self._last_eye_closed_timestamp) > 0.3:
                        self._eye_closed_start = None
            else:
                # 1.5s Face-Lost Persistence Buffer
                if self._eye_closed_start is not None and (now - self._last_face_timestamp) > 1.5:
                    self._eye_closed_start = None
        
            drowsy_dur = (now - self._eye_closed_start) if self._eye_closed_start is not None else 0.0
            drowsy_level = 2 if drowsy_dur > self._drowsy_danger_s else (1 if drowsy_dur > self._drowsy_warning_s else 0)
            drowsy_msg = "Stop the vehicle." if drowsy_level == 2 else ("Stay alert." if drowsy_level == 1 else "Safe")

            # --- 3. PHONE LOGIC (V8 STICKY) ---
            if pd.present:
                self._last_phone_timestamp = now
                if self._phone_start is None: self._phone_start = now
            else:
                # 2.0s Sticky Phone Reset
                if self._phone_start is not None and (now - self._last_phone_timestamp) > 2.0:
                    self._phone_start = None
        
            phone_dur = (now - self._phone_start) if self._phone_start is not None else 0.0
            if phone_dur > self._phone_critical_s: phone_level, phone_msg = 3, "STOP PHONE NOW!"
            elif phone_dur > self._phone_danger_s: phone_level, phone_msg = 2, "FOCUS ON DRIVING!"
            elif phone_dur > self._phone_warning_s: phone_level, phone_msg = 1, "Phone Warning"
            else: phone_level, phone_msg = 0, "Safe"

            # --- 4. REAL-TIME ALERTING (ASYNCHRONOUS) ---
            if drowsy_level > 0 and now - self._last_drowsy_voice > self._voice_repeat_s:
                self._set_voice("Drowsy alert! Stay awake" if drowsy_level == 2 else "Please wake up")
                self._last_drowsy_voice = now
            if phone_level > 0 and now - self._last_phone_voice > self._voice_repeat_s:
                msg = "Put the phone away" if phone_level >= 2 else "No phones while driving"
                self._set_voice(msg)
                self._last_phone_voice = now

            overall_level = max(drowsy_level, phone_level)
            overall_msg = "Safe" if overall_level == 0 else ("Warning" if overall_level == 1 else ("Danger" if overall_level == 2 else "Critical"))

            # --- 5. LOGGING & INCIDENTS ---
            if (drowsy_level > self._prev_drowsy_level) or (phone_level > self._prev_phone_level):
                _snap_prev_d = self._prev_drowsy_level
                _snap_prev_p = self._prev_phone_level

                # --- SNAPSHOT: Write to disk HERE in main thread while orig_frame is valid ---
                _snap_path = None
                if self.driver_id and orig_frame is not None:
                    try:
                        import datetime as _dt2
                        _ev   = "DROWSY" if drowsy_level > _snap_prev_d else "PHONE"
                        _sev  = self._level_to_severity(max(drowsy_level, phone_level))
                        _dur  = drowsy_dur if _ev == "DROWSY" else phone_dur
                        _ts   = _dt2.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        _lbl  = f"[!] {_ev} | {_sev} | {_dur:.1f}s"
                        _snap = np.ascontiguousarray(orig_frame, dtype=np.uint8)
                        _ann  = self._annotate_frame(_snap, _lbl, _ts)
                        _sname = f"snap_{uuid.uuid4().hex[:8]}.jpg"
                        _snap_path = os.path.join("static", "uploads", "chat", _sname)
                        os.makedirs(os.path.dirname(_snap_path), exist_ok=True)
                        ok = cv2.imwrite(_snap_path, _ann, [cv2.IMWRITE_JPEG_QUALITY, 92])
                        if not ok:
                            print(f"[Pipeline] imwrite failed: {_snap_path}")
                            _snap_path = None
                        else:
                            print(f"[Pipeline] Snapshot saved: {_snap_path} "
                                  f"min={_snap.min()} max={_snap.max()}")
                    except Exception as e:
                        print(f"[Pipeline] Snapshot error: {e}")
                        _snap_path = None

                    # Start post-incident video recording (60 frames ≈ 12s at ~5fps)
                    if self._record_post_frames == 0:
                        self._record_post_frames = 60
                        self._incident_frames_to_save = list(self._frame_buffer)
                        self._incident_type_in_progress = (
                            "DROWSY" if drowsy_level > _snap_prev_d else "PHONE"
                        )

                # Spawn background thread ONLY for DB logging + upload (no numpy array)
                threading.Thread(
                    target=self._handle_transition_background,
                    args=(drowsy_level, phone_level, _snap_prev_d, _snap_prev_p,
                          drowsy_dur, phone_dur, drowsy_msg, phone_msg, _snap_path),
                    daemon=True
                ).start()


            # --- 6. STATUS UPDATE ---
            with self._status_lock:
                self.status.ear = d.ear
                self.status.phone_conf = pd.confidence
                self.status.phone_boxes = pd.boxes_xyxy if pd.present else []
                self.status.drowsiness_level = int(drowsy_level)
                self.status.phone_level = int(phone_level)
                self.status.overall_level = int(overall_level)
                self.status.drowsiness_duration_s = float(drowsy_dur)
                self.status.phone_duration_s = float(phone_dur)
                self.status.drowsiness_message = drowsy_msg
                self.status.phone_message = phone_msg
                self.status.overall_message = overall_msg
                self.status.camera_running = True

            self._prev_drowsy_level = drowsy_level
            self._prev_phone_level = phone_level
            self._last_process_time = now
            return self.get_status()
        finally:
            self._processing_lock.release()

    def _handle_transition_background(self, d_level, p_level, prev_d_level, prev_p_level,
                                       d_dur, p_dur, d_msg, p_msg, snap_path):
        """Background worker: DB event logging + snapshot upload.
        snap_path is the already-written JPEG path (or None). No numpy arrays."""
        import datetime as _dt
        try:
            if d_level > prev_d_level:
                self._log_and_notify("DROWSY", d_level, d_dur, d_msg, snap_path)
            if p_level > prev_p_level:
                self._log_and_notify("PHONE",  p_level, p_dur, p_msg, snap_path)
        except Exception as e:
            print(f"[ERROR] _handle_transition_background: {e}")

    def _log_and_notify(self, ev_type, level, dur, msg, snap_path):
        """Log event to DB and send snapshot notification to org chat."""
        import datetime as _dt
        sev = self._level_to_severity(level)
        ts  = _dt.datetime.now().isoformat(timespec="seconds")
        ts_str = _dt.datetime.fromtimestamp(
            _dt.datetime.now().timestamp()
        ).strftime("%Y-%m-%d %H:%M:%S")

        self._logger.log_event(
            ts=ts, event_type=ev_type, severity=sev,
            duration_s=dur, message=msg, driver_id=self.driver_id
        )

        if snap_path and self.driver_id:
            self._upload_and_notify_snapshot(snap_path, self.driver_id, ev_type, sev, dur, ts_str)

    # Legacy wrapper — kept so any remaining callers don't crash
    def _handle_transition(self, ev_type, level, dur, msg, snap_path=None):
        self._log_and_notify(ev_type, level, dur, msg, snap_path)


    def _annotate_frame(self, frame: np.ndarray, label: str, ts: str) -> np.ndarray:
        """Stamp a frame with an incident label and timestamp. ASCII only."""
        out = frame.copy()
        h, w = out.shape[:2]
        # Top banner
        cv2.rectangle(out, (0, 0), (w, 42), (15, 15, 15), -1)
        cv2.putText(out, label, (8, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (30, 80, 230), 2, cv2.LINE_AA)
        # Bottom timestamp
        cv2.rectangle(out, (0, h - 28), (w, h), (15, 15, 15), -1)
        cv2.putText(out, ts, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (210, 210, 210), 1, cv2.LINE_AA)
        return out

    def _upload_and_notify_snapshot(self, snap_path, d_id, ev_type, severity, dur, ts_str):
        """Background worker: upload the already-written snapshot and notify org chat."""
        import datetime as _dt
        try:
            from utils.cloud_storage import upload_file
            cloud_url = upload_file(snap_path, resource_type="image")
            final_url = cloud_url if cloud_url else f"/{snap_path.replace(os.sep, '/')}"

            driver = None
            try:
                with self._logger._connect() as conn:
                    driver = conn.execute(
                        "SELECT organisation_id, name FROM drivers WHERE id=%s", (d_id,)
                    ).fetchone()
            except Exception:
                pass
            if not driver:
                try:
                    with self._logger._connect() as conn:
                        driver = conn.execute(
                            "SELECT organisation_id, name FROM drivers WHERE id=?", (d_id,)
                        ).fetchone()
                except Exception:
                    pass

            if driver and driver["organisation_id"]:
                org_id   = driver["organisation_id"]
                drv_name = driver["name"]
                ts       = _dt.datetime.now().isoformat(timespec="seconds")
                alert_text = (
                    f"[ALERT] INCIDENT DETECTED\n"
                    f"Driver : {drv_name}\n"
                    f"Type   : {ev_type} | Severity: {severity}\n"
                    f"Duration: {dur:.1f}s | Time: {ts_str}\n"
                    f"[snapshot below]"
                )
                self._logger.save_message('driver', d_id, 'org', org_id, alert_text, ts, message_type='text')
                self._logger.save_message('driver', d_id, 'org', org_id, final_url, ts, message_type='image')
        except Exception as e:
            print(f"[ERROR] Snapshot upload/notify failed: {e}")

    def _save_incident_video(self, frames, inc_type, d_id):
        import datetime as _dt
        if not frames:
            return

        frames_clean = [np.ascontiguousarray(f, dtype=np.uint8) for f in frames if f is not None]
        if not frames_clean:
            return

        ts_str   = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        filename = f"incident_{uuid.uuid4().hex[:8]}.mp4"
        path     = os.path.join("static", "uploads", "chat", filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        try:
            vw, vh  = 640, 480
            # MJPG is the most reliable codec on Windows — mp4v produces black frames
            filename = filename.replace('.mp4', '.avi')
            path     = path.replace('.mp4', '.avi')
            fourcc  = cv2.VideoWriter_fourcc(*'MJPG')
            fps_out = 8.0
            out     = cv2.VideoWriter(path, fourcc, fps_out, (vw, vh))
            if not out.isOpened():
                print(f"[ERROR] VideoWriter failed to open: {path}")
                return

            # ASCII label only
            label = f"[!] {inc_type}"
            for f in frames_clean:
                sized     = cv2.resize(f, (vw, vh), interpolation=cv2.INTER_AREA)
                annotated = self._annotate_frame(sized, label, ts_str)
                out.write(annotated)
            out.release()

            from utils.cloud_storage import upload_file
            cloud_url = upload_file(path, resource_type="video")
            final_url = cloud_url if cloud_url else f"/{path.replace(os.sep, '/')}"

            if d_id:
                driver = None
                try:
                    with self._logger._connect() as conn:
                        driver = conn.execute(
                            "SELECT organisation_id, name FROM drivers WHERE id=%s", (d_id,)
                        ).fetchone()
                except Exception:
                    pass
                if not driver:
                    try:
                        with self._logger._connect() as conn:
                            driver = conn.execute(
                                "SELECT organisation_id, name FROM drivers WHERE id=?", (d_id,)
                            ).fetchone()
                    except Exception:
                        pass

                if driver and driver["organisation_id"]:
                    org_id   = driver["organisation_id"]
                    drv_name = driver["name"]
                    ts       = _dt.datetime.now().isoformat(timespec="seconds")
                    dur_s    = len(frames_clean) / fps_out
                    vid_msg  = (
                        f"[VIDEO] INCIDENT RECORDED\n"
                        f"Driver : {drv_name} | {inc_type}\n"
                        f"Duration: {dur_s:.0f}s | Captured: {ts_str}"
                    )
                    self._logger.save_message('driver', d_id, 'org', org_id, vid_msg, ts, message_type='text')
                    self._logger.save_message('driver', d_id, 'org', org_id, final_url, ts, message_type='video')
        except Exception as e:
            print(f"[ERROR] Video worker failed: {e}")

    def _save_incident_snapshot(self, *args, **kwargs):
        """Deprecated â€” snapshot saving now happens in _handle_transition directly."""
        pass

    def _set_voice(self, text: str) -> None:
        if self._server_tts:
            self._server_tts.speak(key=text, text=text, min_interval_s=self._voice_repeat_s)
        self.status.voice_token += 1
        self.status.voice_text = text

    def _level_to_severity(self, level: int) -> str:
        return {0: "SAFE", 1: "WARNING", 2: "DANGER", 3: "CRITICAL"}.get(int(level), "UNKNOWN")

    def _run(self) -> None:
        last_t = time.monotonic()
        frames = 0
        last_processed_id = -1
        while not self._stop.is_set():
            frame, frame_id = self.capture.read_latest()
            if frame is None or frame_id == last_processed_id:
                time.sleep(0.005)
                continue
            last_processed_id = frame_id
            self._do_process_frame(frame)
            frames += 1
            t = time.monotonic()
            if t - last_t >= 1.0:
                self._process_fps = frames / (t - last_t)
                frames = 0
                last_t = t

