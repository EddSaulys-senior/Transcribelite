# Changelog

## 0.5.0 - 2026-02-12

- Added major web UI refresh with custom local CSS/JS, larger layout, animated progress, stage timeline, and completion effects.
- Added `auto` profile option in web UI.
- Added web backend hardening:
  - upload size limit and extension validation
  - profile validation
  - cleanup of temporary uploaded files after job completion
  - safer output folder detection by matching `transcript.json.meta.source_file`
  - runtime Python executable fallback (`.venv` first, then `sys.executable`)
- Fixed text encoding issues in web status messages and UI strings.
- Pinned web dependencies in `requirements.txt`.
- Updated `scripts/run_web.bat` to use explicit python execution.

## 0.4.0 - 2026-02-12

- Added portable build scripts:
  - `scripts/build_portable.bat`
  - `scripts/run_portable.bat`
- Added portable doctor scripts:
  - `scripts/doctor_portable.bat`
  - `portable_dist\TranscribeLite-Portable\doctor_portable.bat` (generated on build)
- Portable build now includes:
  - local Python runtime copy
  - installed `.venv` packages
  - project code and prompts
  - generated `config.portable.ini`
  - optional bundled `ffmpeg` if configured path exists
- Updated README with a simple step-by-step portable guide.

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
