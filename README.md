# TranscribeLite

Local Windows app: `audio/video -> transcript -> optional local summary (Ollama) -> export`.

## Screenshot

![TranscribeLite Web UI](docs/images/web-ui.png)

## Quick Start (Online)

1. Install Python 3.11+.
2. Run `scripts\install_online.bat`.
3. Run `scripts\doctor.bat`.
4. Run `scripts\run.bat <file_or_folder>`.

## Quick Start (Offline)

On internet PC:

1. Run `scripts\build_wheels.bat`.
2. Copy project folder with `wheels\`.

On offline PC:

1. Install Python 3.11+.
2. Run `scripts\install_offline.bat`.
3. Run `scripts\run.bat <file_or_folder>`.

## Web UI

Run local web server:

- `scripts\run_web.bat`

Then open:

- `http://127.0.0.1:7860`

Web API behavior:

- validates file extension (audio/video only)
- validates profile: `auto|fast|balanced|quality`
- enforces upload limit (1 GB)
- removes temporary uploaded files after job completion

## Portable Mode (Very Simple)

If you want a copy that runs from a folder with no project setup:

1. On source PC run `scripts\build_portable.bat`.
2. Copy `portable_dist\TranscribeLite-Portable\` to target PC.
3. Install and start Ollama on target PC.
4. (If needed) pull model: `ollama pull llama3.1:8b`.
5. Run `run_portable.bat <file_or_folder>` inside portable folder.
6. Health check (optional): run `doctor_portable.bat` inside portable folder.

Notes:

- `build_portable.bat` uses `C:\Python311` by default.
- You can override runtime source:
  - `set PORTABLE_PYTHON_SRC=D:\MyPython311`
  - then run `scripts\build_portable.bat`
- If `config.ini` has a valid `ffmpeg_path`, ffmpeg is bundled into portable build automatically.
- Portable output is in `portable_dist\TranscribeLite-Portable\output\`.
- From project root you can run:
  - `scripts\run_portable.bat <file_or_folder>`
  - `scripts\doctor_portable.bat`

## Profiles

Config source: `config.ini`.

Manual:

```ini
[profile]
active = quality
```

Values: `auto`, `fast`, `balanced`, `quality`.

Auto profile settings:

```ini
[profile_auto]
short_max_minutes = 0.5
medium_max_minutes = 6
short_profile = quality
medium_profile = balanced
long_profile = fast
```

CLI one-run override:

- `--profile auto|fast|balanced|quality`

## CLI

- `python -m transcribelite.app transcribe <file_or_folder>`
- `python -m transcribelite.app doctor`
- `python -m transcribelite.app config --init`
- `python -m transcribelite.app --version`

## Outputs

For each input file:

`output\<timestamp>_<name>\`

Files:

- `transcript.txt`
- `transcript.json`
- `note.md`

`transcript.json` includes run metadata:

- `app_version`
- `profile`
- `requested_stt`

## Versioning and Change Tracking

- App version: `transcribelite.__version__`
- Change log: `CHANGELOG.md`

Project rule:

- If runtime behavior, launch params, profiles, or CLI flags change, update both:
  - `README.md`
  - `CHANGELOG.md`
