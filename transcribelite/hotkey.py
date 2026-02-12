from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from transcribelite.config import AppConfig, load_config
from transcribelite.pipeline.export import export_outputs
from transcribelite.pipeline.stt_faster_whisper import transcribe_file
from transcribelite.pipeline.summarize_ollama import summarize_text
from transcribelite.utils.logging_setup import setup_logging


def _apply_profile(cfg: AppConfig, profile: str) -> None:
    p = profile.strip().lower()
    if p == "auto":
        p = "balanced"
    preset = cfg.profile_presets.get(p)
    if not preset:
        return
    cfg.profile_name = p
    cfg.stt.model_name = preset.model_name
    cfg.stt.compute_type = preset.compute_type
    cfg.stt.beam_size = preset.beam_size


def _normalize_hotkey(hotkey: str) -> str:
    mapping = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "space": "<space>"}
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    normalized = []
    for p in parts:
        normalized.append(mapping.get(p, f"<{p}>"))
    return "+".join(normalized) if normalized else "<ctrl>+<alt>+<space>"


@dataclass
class RecorderState:
    stream: Optional[object]
    chunks: List["np.ndarray"]
    sample_rate: int
    channels: int
    started_at: float
    recording: bool
    busy: bool


class HotkeyDictation:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.logger = setup_logging(cfg.paths.logs_dir / "transcribelite-hotkey.log")
        self.state = RecorderState(
            stream=None,
            chunks=[],
            sample_rate=16000,
            channels=1,
            started_at=0.0,
            recording=False,
            busy=False,
        )
        self.lock = threading.Lock()

    def _start_recording(self) -> None:
        import sounddevice as sd  # type: ignore

        with self.lock:
            if self.state.recording or self.state.busy:
                return
            self.state.chunks = []
            self.state.started_at = time.time()
            self.state.recording = True

        max_seconds = max(5, int(self.cfg.dictation.max_seconds))

        def callback(indata, frames, callback_time, status):  # noqa: ANN001
            if status:
                self.logger.warning("Mic status: %s", status)
            with self.lock:
                if not self.state.recording:
                    return
                self.state.chunks.append(indata.copy())
                if (time.time() - self.state.started_at) >= max_seconds:
                    self.state.recording = False

        stream = sd.InputStream(
            samplerate=self.state.sample_rate,
            channels=self.state.channels,
            dtype="float32",
            callback=callback,
            blocksize=1024,
        )
        stream.start()
        with self.lock:
            self.state.stream = stream
        print("HOTKEY: recording started")

    def _stop_recording(self) -> Optional[Path]:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore

        with self.lock:
            if self.state.busy:
                return None
            self.state.busy = True
            self.state.recording = False
            stream = self.state.stream
            self.state.stream = None
            chunks = list(self.state.chunks)
            self.state.chunks = []

        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception:
            pass

        if not chunks:
            with self.lock:
                self.state.busy = False
            print("HOTKEY: no audio captured")
            return None

        audio = np.concatenate(chunks, axis=0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_path = self.cfg.paths.cache_dir / "dictation" / f"hotkey_{timestamp}.wav"
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(wav_path), audio, self.state.sample_rate, subtype="PCM_16")
        with self.lock:
            self.state.busy = False
        return wav_path

    def _process_file(self, wav_path: Path) -> None:
        _apply_profile(self.cfg, self.cfg.dictation.profile)
        self.cfg.stt.language = self.cfg.dictation.language or "auto"
        self.cfg.summarize.enabled = self.cfg.dictation.summarize

        try:
            stt = transcribe_file(wav_path, self.cfg)
            summary = None
            summary_error = None
            if self.cfg.summarize.enabled:
                summary, summary_error = summarize_text(stt["text"], self.cfg)
            else:
                summary_error = "summary disabled"
            out_dir = export_outputs(
                cfg=self.cfg,
                source_path=wav_path,
                transcript_text=stt["text"],
                segments=stt["segments"],
                stt_meta=stt["meta"],
                summary=summary,
                summary_error=summary_error,
            )
            print(f"HOTKEY: saved -> {out_dir}")
        except Exception as exc:
            print(f"HOTKEY ERROR: {exc}")
            self.logger.exception("Hotkey processing failed")
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    def toggle(self) -> None:
        with self.lock:
            recording = self.state.recording
        if not recording:
            self._start_recording()
            return

        wav_path = self._stop_recording()
        if not wav_path:
            return
        print("HOTKEY: recording stopped, transcribing...")
        threading.Thread(target=self._process_file, args=(wav_path,), daemon=True).start()

    def run(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            print("Install dependency: pynput")
            return

        hotkey = _normalize_hotkey(self.cfg.dictation.hotkey)
        print(f"HOTKEY: listening on {hotkey}")

        listener = keyboard.GlobalHotKeys({hotkey: self.toggle})
        listener.start()
        try:
            while True:
                time.sleep(0.25)
        except KeyboardInterrupt:
            print("HOTKEY: stopped")
        finally:
            listener.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transcribelite-hotkey")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config, init_if_missing=True)
    app = HotkeyDictation(cfg)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

