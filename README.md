# TranscribeLite

Локальное Windows-приложение: `audio/video -> transcript -> optional summary (Ollama) -> export`.

## Что умеет

- Windows 10/11
- STT через `faster-whisper`
- GPU (CUDA) при наличии, fallback при проблемах
- Локальный summary только через Ollama (`/api/generate`)
- Online/offline установка (через `wheels`)
- Профили качества: `fast`, `balanced`, `quality`, `auto`

## Быстрый старт (online)

1. Установить Python 3.11+.
2. Установить зависимости:
   - `scripts\install_online.bat`
3. Проверить окружение:
   - `scripts\doctor.bat`
4. Запуск:
   - `scripts\run.bat <file_or_folder>`

## Быстрый старт (offline)

На машине с интернетом:

1. `scripts\build_wheels.bat`
2. Скопировать проект вместе с папкой `wheels`.

На offline машине:

1. `scripts\install_offline.bat`
2. `scripts\run.bat <file_or_folder>`

## CLI

- `python -m transcribelite.app transcribe <file_or_folder>`
- `python -m transcribelite.app doctor`
- `python -m transcribelite.app config --init`
- `python -m transcribelite.app --version`

Переопределения на запуск:

- `--profile auto|fast|balanced|quality`
- `--device cuda|cpu`
- `--compute-type <value>`
- `--model-name <name>`
- `--no-summary`

## Профили качества

Источник правды: `config.ini`.

### Ручной выбор

В `config.ini`:

```ini
[profile]
active = quality
```

Допустимые значения: `auto`, `fast`, `balanced`, `quality`.

### Auto-режим

В `config.ini`:

```ini
[profile_auto]
short_max_minutes = 0.5
medium_max_minutes = 6
short_profile = quality
medium_profile = balanced
long_profile = fast
```

Логика:

- короткие файлы -> `short_profile`
- средние -> `medium_profile`
- длинные -> `long_profile`

## Выходные файлы

Для каждого входного файла создается:

`output\<timestamp>_<name>\`

Содержимое:

- `transcript.txt`
- `transcript.json`
- `note.md`

`transcript.json` содержит метаданные запуска, включая:

- `app_version`
- `profile`
- `requested_stt`
- фактически использованные параметры STT

## Версионирование и изменения

- Версия приложения: `transcribelite.__version__`
- История изменений: `CHANGELOG.md`

Правило проекта:

- При изменении параметров запуска, профилей, CLI-флагов или поведения pipeline обязательно обновлять:
  - `README.md`
  - `CHANGELOG.md`

## Частые проблемы

- `ffmpeg not found`:
  - проверить `[paths] ffmpeg_path` в `config.ini`
- `torch.cuda FAIL`:
  - проверить драйвер NVIDIA и CUDA-совместимость
- `ollama unavailable`:
  - запустить Ollama и проверить `http://127.0.0.1:11434/api/tags`

