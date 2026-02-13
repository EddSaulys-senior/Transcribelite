# Чеклист передачи Portable-сборки

1. Проверьте, что передаёте именно папку `portable_dist\TranscribeLite-Portable_FINAL`.
2. Убедитесь, что внутри есть файлы:
- `run_portable.bat`
- `run_web.bat`
- `run_hotkey.bat`
- `doctor_portable.bat`
- `README_PORTABLE.txt`
3. На целевом ПК распакуйте папку без изменения структуры.
4. Запустите `doctor_portable.bat` и проверьте:
- `python OK`
- `ffmpeg OK`
- `faster-whisper OK`
- `torch.cuda` может быть `FAIL` (это допустимо, будет CPU).
5. Тест CLI:
- `run_portable.bat <путь_к_аудио_или_видео>`
6. Тест Web:
- `run_web.bat`
- открыть `http://127.0.0.1:7860`
7. Если нужен summary/QA:
- установить Ollama
- выполнить `ollama pull llama3.1:8b` (или другую модель)
8. Если нужны gated модели HF (например pyannote):
- создать `HF_TOKEN` и подтвердить доступ к репозиториям на Hugging Face.
9. Для офлайн-работы Whisper:
- заранее перенести папку с моделями в `models\`.
10. Проверить, что результаты появляются в `output\`, логи в `logs\`.
