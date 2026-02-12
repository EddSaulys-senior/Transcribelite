# Changelog

## 0.3.0 - 2026-02-12

- Added profile auto-selection by media duration.
- Kept manual profile selection (`fast`, `balanced`, `quality`) and CLI override `--profile`.
- Added app version flag: `python -m transcribelite.app --version`.
- Added run metadata to `transcript.json`: `app_version`, `profile`, `requested_stt`.
- Updated README with current startup parameters and profile behavior.

## 0.2.0 - 2026-02-12

- Added quality presets and profile support.
- Added robust CUDA fallback flow for faster-whisper.
- Updated GPU install flow to CUDA-compatible PyTorch wheels.

## 0.1.0 - 2026-02-12

- Initial TranscribeLite CLI pipeline (ingest, STT, summary, export).
- Added online/offline install scripts and doctor checks.

