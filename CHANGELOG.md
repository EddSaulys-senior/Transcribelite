# Changelog

## 0.11.1 - 2026-02-12

- Dictation UX update:
  - removed separate `Polished text` block from dictation tab
  - `Live text` is now editable (`textarea`) for manual corrections
  - polish modal now uses flow: `Run` -> `Вставить в Live text`
  - removed polish modal actions `Save as file` and `Copy`
- Dictation save now respects edited text:
  - `Save` sends `text_override`
  - backend `set_text` / `manual_text_override` keeps final edited text for export
- Improved readability:
  - live dictation text and polish result are auto-formatted into readable multi-line text
  - dictation history preview is wrapped and displayed with preserved line breaks

## 0.11.0 - 2026-02-12

- Added new Polish Text flow (`✨ Привести в порядок`) in dictation tab (`Live text`).
- Added backend endpoint: `POST /api/polish` (Ollama-only).
  - presets: `punct`, `clean`, `short`, `task`, `obsidian`, `custom`
  - supports `strict`, custom instruction, model override
  - optional save to output folder as second file
- Added polished output saving without overwriting original files:
  - `transcript_polished.txt` or `note_polished.md`
  - metadata file `polish_YYYYMMDD_HHMMSS.json`
- Added Ollama model management API for UI:
  - `GET /api/ollama/models`
  - `POST /api/ollama/pull/start`
  - `GET /api/ollama/pull/{pull_id}`
- Added polished download alias endpoint support:
  - `GET /api/jobs/{job_id}/download/polished`
- Updated web UI:
  - modal with preset/model/strict/instruction
  - actions `Run`, `Save as file`, `Copy`
  - per-tab `Polished text` preview block
  - progress display while missing Ollama model is being pulled

## 0.10.2 - 2026-02-12

- Added deletion of dictation history items with confirmation in UI.
- Added backend endpoint: `DELETE /api/dictation/history/{item_id}`.

## 0.10.1 - 2026-02-12

- Fixed dictation duplicate text growth with stronger anti-repeat checks in live chunk processing.
- Improved dictation ffmpeg robustness:
  - `-sseof` decode now has fallback to full decode for growing/incomplete `.webm`.
- Added persistent dictation history storage in SQLite (`dictation_history`).
- Added API endpoint: `GET /api/dictation/history`.
- Added "История диктовки" panel in dictation tab UI.

## 0.10.0 - 2026-02-12

- Added dictation auto-save on `Stop` (configurable via `dictation.auto_save`).
- Added dictation config section in `config.ini`:
  - `hotkey`, `profile`, `language`, `summarize`, `max_seconds`, `silence_stop`, `auto_save`
- Added global hotkey helper (Mode B):
  - new module `transcribelite/hotkey.py`
  - new launcher `scripts/run_hotkey.bat`
  - default hotkey: `Ctrl+Alt+Space`
  - first press starts local mic capture, second press stops + transcribes + exports to `output/...`
- Added dependencies for hotkey mode:
  - `pynput`, `sounddevice`, `soundfile`
- Improved dictation WebSocket protocol by emitting explicit `state` updates (`recording` / `stopped`).

## 0.9.0 - 2026-02-12

- Added real-time dictation tab (`🎙 Диктовка`) in Web UI.
- Added local WebSocket endpoint for streaming mic audio:
  - `WS /ws/dictation`
  - commands: `start`, `stop`, `flush`, `clear`, `save`
  - events: `partial`, `final`, `stats`, `saved`, `error`
- Implemented browser audio chunk streaming via `MediaRecorder` (`webm/ogg opus`) and binary WS frames.
- Added server-side dictation session manager with temp files in `cache/dictation/`.
- Added periodic near-live transcription loop using local `ffmpeg` + `faster-whisper`.
- Added overlap-based text deduplication for incremental dictation text updates.
- Added dictation save flow to regular project outputs (`transcript.txt/json`, `note.md`) via existing export pipeline.
- Dictation save supports optional local summary; if Ollama is unavailable summary is skipped without failing save.
- Added dictation quick actions in UI: `Start / Stop / Clear / Save / Copy`.

## 0.8.2 - 2026-02-12

- Added persistent Q&A history storage in local SQLite (`data/index.db`, table `qa_history`).
- `/api/ask` now saves each successful Q&A record (job_id, question, answer, created_at).
- Added endpoint `GET /api/qa/history` for restoring Q&A history after app restart.
- Updated Web UI to load Q&A history from backend (not only in-memory session).
- Made sections `🧠 Спроси у записи` and `🔎 Поиск по истории записей` collapsible.
- Both sections are collapsed by default on page load.

## 0.8.1 - 2026-02-12

- Added Q&A history in Web UI (`🧠 Спроси у записи`) for current browser session.
- Added global transcript search endpoint: `GET /api/search?q=...&limit=...`.
- Added global search block in Web UI (`🔎 Поиск по истории записей`).
- Global search now returns indexed snippets with job metadata (`job_id`, `title`, `created_at`, `chunk_id`).
- Improved `/api/ask` response to include `job_id`.
- Fixed Russian text encoding in Q&A backend error/fallback messages.

## 0.8.0 - 2026-02-12

- Added feature "Ask the recording" (local Q&A per completed job) in Web UI.
- Added local transcript indexing with SQLite FTS5 (`unicode61`) in `data/index.db`.
- Added word-based chunking utility with overlap:
  - `transcribelite/utils/chunking.py`
- Added search index module:
  - `transcribelite/search_index.py`
  - `open_db`, `index_job`, `search_chunks`
- Added automatic indexing after successful transcription job in web backend.
- Added endpoint `POST /api/ask`:
  - input: `job_id`, `question`, optional `limit`
  - output: `answer`, `sources[]`
  - fallback when nothing found: `В записи этого нет.`
- Reused local Ollama integration for Q&A generation with conservative options (`num_predict`, `temperature`).
- Added new UI block "🧠 Спроси у записи" with question box, answer panel, and expandable sources.
- Updated `prompts/qa_ru.txt` with strict no-hallucination prompt template.

## 0.7.1 - 2026-02-12

- Redesigned web preview block:
  - now shows structured `Summary` and `Action items` cards instead of raw `note.md`
  - added transcript excerpt panel for quick reading
  - added compact meta badges (date/model/device)
- Enhanced `/api/jobs/{job_id}/preview` response with structured fields:
  - `summary_status`, `summary_error`
  - `summary_points`, `summary_text`
  - `action_items`
  - `transcript_excerpt`
  - normalized `meta` object
- Raw files remain available via download buttons (`note.md`, `transcript.txt`, `transcript.json`).

## 0.7.0 - 2026-02-12

- Added web transcription from internet URL (including YouTube-like sources) using `yt-dlp`.
- Added backend endpoint `POST /api/jobs/from-url`.
- Added remote download guards:
  - URL validation (`http/https`)
  - max duration: 3 hours
  - max file size: 1 GB
  - playlist disabled
- Fixed web job-id mapping bug in `/api/jobs` creation flow.
- Added `download` stage to web timeline and progress flow.
- Refreshed web UI text and controls:
  - separate start buttons for file and URL
  - URL input field with Enter-to-start
  - corrected Russian strings/encoding
- Added dependency: `yt-dlp==2026.2.4`.
- Updated `requirements-lock.txt` and `README.md` (Russian docs with URL mode).

## 0.6.0 - 2026-02-12

- Added light/dark theme switcher in Web UI.
- Theme selection is persisted in browser local storage.
- Refreshed Web UI text encoding (UTF-8) and cleaned Russian strings in frontend.

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
