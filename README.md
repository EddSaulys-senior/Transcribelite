# TranscribeLite

Локальное приложение для Windows 10/11:

`аудио/видео -> транскрипт -> (опционально) локальное summary через Ollama -> экспорт`

## Скриншот

![TranscribeLite Web UI](docs/images/web-ui.png)

## Возможности

- CLI и Web интерфейс
- GPU-распознавание через `faster-whisper` (CUDA при наличии)
- Локальное summary только через Ollama (без облачных API)
- Профили качества: `auto`, `fast`, `balanced`, `quality`
- Запуск в online/offline/portable режимах
- В Web: загрузка файла или запуск по ссылке из интернета (например YouTube) через `yt-dlp`
- Q&A по записи: блок `🧠 Спроси у записи` (локально через Ollama + SQLite FTS5)
- История Q&A в веб-интерфейсе с сохранением между перезапусками
- Глобальный поиск по истории записей (`/api/search`)

## Быстрый старт (online)

1. Установите Python 3.11+.
2. Выполните `scripts\install_online.bat`.
3. Выполните `scripts\doctor.bat`.
4. Запустите CLI: `scripts\run.bat <файл_или_папка>`.
5. Запустите Web: `scripts\run_web.bat`, затем откройте `http://127.0.0.1:7860`.

## Быстрый старт (offline)

На ПК с интернетом:

1. Выполните `scripts\build_wheels.bat`.
2. Скопируйте весь проект вместе с папкой `wheels\`.

На офлайн ПК:

1. Установите Python 3.11+.
2. Выполните `scripts\install_offline.bat`.
3. Выполните `scripts\doctor.bat`.

## Web интерфейс

Запуск:

- `scripts\run_web.bat`

Поддерживает:

- загрузку медиафайла
- запуск по URL (`http/https`) через `yt-dlp`
- живой статус по этапам (`download -> ingest -> stt -> summarize -> export`)
- красивый предпросмотр: карточки `Summary` и `Action items`, плюс фрагмент транскрипта
- Q&A по текущей записи (`POST /api/ask`): ответ + источники (sources)
- История последних вопросов/ответов в UI
- Глобальный поиск по всем проиндексированным записям в UI
- Блоки Q&A и глобального поиска сворачиваемые (по умолчанию закрыты)
- скачивание `note.md`, `transcript.txt`, `transcript.json`
- переключение светлой/тёмной темы (сохраняется в браузере)

### Ask the recording (Q&A)

- После `job done` транскрипт автоматически индексируется в `data/index.db`.
- Индексация: SQLite FTS5, токенизатор `unicode61`, word-chunks с overlap.
- В UI задайте вопрос в блоке `🧠 Спроси у записи`, получите:
  - короткий ответ
  - `Sources` с фрагментами, на которых основан ответ
- Если релевантных источников нет, сервис вернёт: `В записи этого нет.`
- Если Ollama недоступна, UI покажет понятную ошибку.
- Для глобального поиска используйте endpoint: `GET /api/search?q=<запрос>&limit=12`.
- Для восстановления истории Q&A используйте endpoint: `GET /api/qa/history?limit=50`.

Ограничения URL-режима:

- максимальная длительность: 3 часа
- максимальный размер скачивания: 1 GB
- только одиночный ролик (playlist отключён)

## Профили качества

В `config.ini`:

```ini
[profile]
active = auto

[profile_auto]
short_max_minutes = 0.5
medium_max_minutes = 6
short_profile = quality
medium_profile = balanced
long_profile = fast
```

Переопределение на один запуск:

- `--profile auto|fast|balanced|quality`

## CLI

- `python -m transcribelite.app transcribe <файл_или_папка>`
- `python -m transcribelite.app doctor`
- `python -m transcribelite.app config --init`
- `python -m transcribelite.app --version`

## Выходные файлы

Для каждого источника создаётся:

`output\<timestamp>_<name>\`

Файлы:

- `transcript.txt`
- `transcript.json`
- `note.md`

## Portable режим

1. Выполните `scripts\build_portable.bat`.
2. Перенесите `portable_dist\TranscribeLite-Portable\` на другой ПК.
3. Установите Ollama и модель (например `ollama pull llama3.1:8b`).
4. Запускайте `run_portable.bat <файл_или_папка>`.
5. Для проверки окружения используйте `doctor_portable.bat`.

## Важно

- Summary работает только локально через Ollama.
- Если Ollama недоступна, транскрипция не падает: summary пропускается.
- Для URL-источников используйте только контент, который вам разрешено обрабатывать.

## Версии

- Версия приложения: `transcribelite.__version__`
- История изменений: `CHANGELOG.md`
