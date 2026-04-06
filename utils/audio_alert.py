from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechRequest:
    key: str
    text: str
    min_interval_s: float


class AudioAlerter:
    """
    Non-blocking server-side TTS (optional).

    In a web app, it's usually better UX to do voice on the client using
    browser SpeechSynthesis. This module exists for local deployments
    where server-side audio is desired.
    """

    def __init__(self):
        self._q: queue.Queue[SpeechRequest] = queue.Queue()
        self._stop = threading.Event()
        self._last_spoken: dict[str, float] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        self._engine = None
        self._engine_lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(SpeechRequest(key="__stop__", text="", min_interval_s=0))
        except Exception:
            pass

    def speak(self, *, key: str, text: str, min_interval_s: float = 3.0) -> None:
        self._q.put(SpeechRequest(key=key, text=text, min_interval_s=float(min_interval_s)))

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3  # type: ignore

            eng = pyttsx3.init()
            eng.setProperty("rate", 165)
            self._engine = eng
        return self._engine

    def _run(self) -> None:
        while not self._stop.is_set():
            req = self._q.get()
            if self._stop.is_set():
                return
            if req.key == "__stop__":
                return

            now = time.monotonic()
            last = self._last_spoken.get(req.key, 0.0)
            if now - last < req.min_interval_s:
                continue

            try:
                with self._engine_lock:
                    eng = self._get_engine()
                    eng.say(req.text)
                    eng.runAndWait()
                self._last_spoken[req.key] = time.monotonic()
            except Exception:
                # Never allow audio issues to affect video processing.
                pass

