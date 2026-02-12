# TranscribeLite

Локальное приложение для Windows: `аудио/видео -> транскрипт -> (опционально) локальное summary через Ollama -> экспорт`.

## Скриншот

![TranscribeLite Web UI](docs/images/web-ui.png)

## Быстрый старт (онлайн)

1. Установите Python 3.11+.
2. Выполните `scripts\install_online.bat`.
3. Выполните `scripts\doctor.bat`.
4. Запустите `scripts\run.bat <файл_или_папка>`.

## Быстрый старт (офлайн)

На ПК с интернетом:

1. Выполните `scripts\build_wheels.bat`.
2. Скопируйте проект вместе с папкой `wheels\`.

На офлайн ПК:

1. Установите Python 3.11+.
2. Выполните `scripts\install_offline.bat`.
3. Запустите `scripts\run.bat <файл_или_папка>`.

## Web UI

Запуск локального веб-интерфейса:

- `scripts\run_web.bat`

Открыть в браузере:

- `http://127.0.0.1:7860`

Поведение Web API:

- проверка расширений загружаемых медиафайлов
- проверка профиля: `auto|fast|balanced|quality`
- лимит размера загрузки: 1 GB
- удаление временных upload-файлов после завершения задачи

## Portable-режим (максимально просто)

Если нужна папка, которую можно перенести и запускать отдельно:

1. На исходном ПК выполните `scripts\build_portable.bat`.
2. Скопируйте `portable_dist\TranscribeLite-Portable\` на целевой ПК.
3. Установите и запустите Ollama на целевом ПК.
4. При необходимости скачайте модель: `ollama pull llama3.1:8b`.
5. Внутри portable-папки запускайте `run_portable.bat <файл_или_папка>`.
6. Проверка окружения (опционально): `doctor_portable.bat`.

Примечания:

- `build_portable.bat` по умолчанию берет runtime из `C:\Python311`.
- Можно переопределить источник Python:
  - `set PORTABLE_PYTHON_SRC=D:\MyPython311`
  - затем выполнить `scripts\build_portable.bat`
- Если в `config.ini` указан корректный `ffmpeg_path`, ffmpeg автоматически попадет в portable-сборку.
- Выходные файлы portable: `portable_dist\TranscribeLite-Portable\output\`.
- Из корня проекта также доступны:
  - `scripts\run_portable.bat <файл_или_папка>`
  - `scripts\doctor_portable.bat`

## Профили качества

Источник настроек: `config.ini`.

Ручной выбор:

```ini
[profile]
active = quality
```

Допустимые значения: `auto`, `fast`, `balanced`, `quality`.

Настройки auto-профиля:

```ini
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

Для каждого входного файла создается папка:

`output\<timestamp>_<name>\`

Файлы:

- `transcript.txt`
- `transcript.json`
- `note.md`

В `transcript.json` добавляются метаданные запуска:

- `app_version`
- `profile`
- `requested_stt`

## Версии и контроль изменений

- Версия приложения: `transcribelite.__version__`
- История изменений: `CHANGELOG.md`

Правило проекта:

- При изменении поведения рантайма, параметров запуска, профилей или CLI-флагов обновлять:
  - `README.md`
  - `CHANGELOG.md`

