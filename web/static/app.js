let selectedFile = null;
let currentJobId = null;
let lastDoneJobId = null;
let pollTimer = null;
let fx = null;
let fxCtx = null;
let sparks = [];
let activeStage = "queued";
let askHistory = [];
let dictWs = null;
let dictRecorder = null;
let dictStream = null;
let dictRunning = false;
let dictLastJobId = null;
let polishLastText = "";
let polishLastResult = "";

const STAGES = ["download", "ingest", "stt", "summarize", "export"];
const THEME_KEY = "transcribelite_theme";
const MD_TAB_KEY = "transcribelite_md_tab";

const $ = (id) => document.getElementById(id);

function setHint(text) {
  $("hint").textContent = text || "";
}

function setStatus(text) {
  $("status").textContent = text || "—";
}

function setStage(text) {
  $("stage").textContent = text || "—";
}

function setMsg(text) {
  $("msg").textContent = text || "";
}

function setBar(progress) {
  $("bar").style.width = `${Math.round((progress || 0) * 100)}%`;
}

function setTimeline(stage) {
  activeStage = stage || "queued";
  const nodes = document.querySelectorAll(".timeline li");
  nodes.forEach((node) => {
    const s = node.dataset.stage;
    node.classList.remove("active", "done");
    if (STAGES.includes(activeStage)) {
      const currentIdx = STAGES.indexOf(activeStage);
      const idx = STAGES.indexOf(s);
      if (idx < currentIdx) node.classList.add("done");
      if (idx === currentIdx) node.classList.add("active");
    }
    if (activeStage === "done") node.classList.add("done");
  });
}

function showDownloads(jobId) {
  $("dl_note").href = `/api/jobs/${jobId}/download/note`;
  $("dl_txt").href = `/api/jobs/${jobId}/download/txt`;
  $("dl_json").href = `/api/jobs/${jobId}/download/json`;
  $("dl_note").classList.remove("hidden");
  $("dl_txt").classList.remove("hidden");
  $("dl_json").classList.remove("hidden");
}

function hideDownloads() {
  $("dl_note").classList.add("hidden");
  $("dl_txt").classList.add("hidden");
  $("dl_json").classList.add("hidden");
}

function showDictDownloads(jobId) {
  $("dictDlNote").href = `/api/jobs/${jobId}/download/note`;
  $("dictDlTxt").href = `/api/jobs/${jobId}/download/txt`;
  $("dictDlJson").href = `/api/jobs/${jobId}/download/json`;
  $("dictDlNote").classList.remove("hidden");
  $("dictDlTxt").classList.remove("hidden");
  $("dictDlJson").classList.remove("hidden");
}

function hideDictDownloads() {
  $("dictDlNote").classList.add("hidden");
  $("dictDlTxt").classList.add("hidden");
  $("dictDlJson").classList.add("hidden");
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const btn = $("themeToggle");
  if (!btn) return;
  btn.textContent = theme === "dark" ? "☀️ Светлая тема" : "🌙 Тёмная тема";
}

function detectSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  applyTheme(saved || detectSystemTheme());

  $("themeToggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  });
}

function resetBeforeStart(stage = "ingest") {
  setHint("");
  setStatus("Подготовка...");
  setStage(stage);
  setMsg("");
  setBar(0.02);
  setTimeline(stage);
  hideDownloads();

  $("summaryCard").innerHTML = '<p class="muted">Нет данных</p>';
  $("actionsCard").innerHTML = '<p class="muted">Нет данных</p>';
  $("transcriptExcerpt").textContent = "";
  $("metaDate").textContent = "Дата: —";
  $("metaModel").textContent = "Модель: —";
  $("metaDevice").textContent = "Устройство: —";

  $("askAnswer").innerHTML = '<p class="muted">Пока нет ответа</p>';
  $("askSources").innerHTML = '<p class="muted">Источники появятся после ответа</p>';
  $("askHint").textContent = "";
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatReadableText(text) {
  const src = String(text || "").trim();
  if (!src) return "";

  const normalized = src.replace(/\s+/g, " ").trim();
  const sentenceBroken = normalized.replace(/([.!?])\s+/g, "$1\n");
  const lines = sentenceBroken.split("\n");
  const wrapped = [];

  for (const line of lines) {
    const words = line.trim().split(/\s+/).filter(Boolean);
    if (!words.length) continue;
    let row = "";
    for (const word of words) {
      if (!row) {
        row = word;
        continue;
      }
      if ((row + " " + word).length > 110) {
        wrapped.push(row);
        row = word;
      } else {
        row += " " + word;
      }
    }
    if (row) wrapped.push(row);
  }

  return wrapped.join("\n");
}

function safeMarkedParse(raw) {
  const text = String(raw || "");
  if (!window.marked) return escapeHtml(text).replaceAll("\n", "<br>");
  window.marked.setOptions({
    gfm: true,
    breaks: true,
    mangle: false,
    headerIds: true,
  });
  const html = window.marked.parse(text);
  if (window.DOMPurify) return window.DOMPurify.sanitize(html);
  return html;
}

function renderMarkdownPreview() {
  const source = $("live-md-source").textContent || "";
  $("live-md-render").innerHTML = safeMarkedParse(source);
}

function setMdTab(mode) {
  const isRender = mode === "render";
  $("tab-source").classList.toggle("active", !isRender);
  $("tab-render").classList.toggle("active", isRender);
  $("live-md-source").classList.toggle("hidden", isRender);
  $("live-md-render").classList.toggle("hidden", !isRender);
  localStorage.setItem(MD_TAB_KEY, isRender ? "render" : "source");
  if (isRender) renderMarkdownPreview();
}

function updateMarkdownPreviewFromLive() {
  $("live-md-source").textContent = $("dictLiveText").value || "";
  const mode = localStorage.getItem(MD_TAB_KEY) || "source";
  if (mode === "render" && !$("live-md-render").classList.contains("hidden")) {
    renderMarkdownPreview();
  }
}

function renderBulletCard(elementId, items, fallbackText) {
  const node = $(elementId);
  if (!items || !items.length) {
    node.innerHTML = `<p class="muted">${escapeHtml(fallbackText)}</p>`;
    return;
  }
  const html = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  node.innerHTML = `<ul>${html}</ul>`;
}

function renderPreview(preview) {
  const meta = preview.meta || {};
  const metaDate = meta.created_at ? meta.created_at.replace("T", " ") : "—";
  const model = meta.stt_model || "—";
  const device = [meta.device || "—", meta.compute_type || ""].filter(Boolean).join(" / ");

  $("metaDate").textContent = `Дата: ${metaDate}`;
  $("metaModel").textContent = `Модель: ${model}`;
  $("metaDevice").textContent = `Устройство: ${device}`;

  if (preview.summary_status === "skipped") {
    const reason = preview.summary_error || "summary skipped";
    $("summaryCard").innerHTML = `<p class="muted">Summary пропущен: ${escapeHtml(reason)}</p>`;
  } else if (preview.summary_points?.length) {
    renderBulletCard("summaryCard", preview.summary_points, "Нет summary");
  } else if (preview.summary_text) {
    $("summaryCard").innerHTML = `<p>${escapeHtml(preview.summary_text)}</p>`;
  } else {
    $("summaryCard").innerHTML = '<p class="muted">Нет summary</p>';
  }

  renderBulletCard("actionsCard", preview.action_items || [], "Action items не найдены");
  $("transcriptExcerpt").textContent = preview.transcript_excerpt || "";
}

function openPolishModal() {
  polishLastText = ($("dictLiveText").value || "").trim();
  $("polishModal").classList.remove("hidden");
  $("polishHint").textContent = "";
  $("polishModalResult").innerHTML = '<p class="muted">Запустите обработку</p>';
  loadPolishModels();
}

function closePolishModal() {
  $("polishModal").classList.add("hidden");
}

function getCurrentPolishJobId() {
  return dictLastJobId || null;
}

async function loadPolishModels() {
  const select = $("polishModel");
  select.innerHTML = "";
  const response = await fetch("/api/ollama/models");
  const payload = await response.json();
  if (!response.ok || !payload.ok) {
    $("polishHint").textContent = payload.error || "Не удалось получить модели Ollama";
    return;
  }
  const models = Array.isArray(payload.models) ? payload.models : [];
  const fallback = payload.default_model || "";
  if (!models.length && fallback) {
    const opt = document.createElement("option");
    opt.value = fallback;
    opt.textContent = `${fallback} (not pulled)`;
    select.appendChild(opt);
    return;
  }
  models.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === fallback) opt.selected = true;
    select.appendChild(opt);
  });
  if (fallback && !models.includes(fallback)) {
    const opt = document.createElement("option");
    opt.value = fallback;
    opt.textContent = `${fallback} (not pulled)`;
    opt.selected = true;
    select.appendChild(opt);
  }
}

async function ensurePolishModel(model) {
  const listResponse = await fetch("/api/ollama/models");
  const listPayload = await listResponse.json();
  if (listResponse.ok && listPayload.ok && Array.isArray(listPayload.models) && listPayload.models.includes(model)) {
    return true;
  }

  $("polishHint").textContent = `Загружаю модель ${model}...`;
  const startResponse = await fetch("/api/ollama/pull/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  const startPayload = await startResponse.json();
  if (!startResponse.ok || !startPayload.pull_id) {
    throw new Error(startPayload.detail || startPayload.error || "Не удалось начать загрузку модели");
  }
  const pullId = startPayload.pull_id;
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 850));
    const statusResponse = await fetch(`/api/ollama/pull/${pullId}`);
    const statusPayload = await statusResponse.json();
    if (!statusResponse.ok) throw new Error(statusPayload.detail || statusPayload.error || "Ошибка проверки загрузки модели");
    $("polishHint").textContent = `Загрузка модели: ${statusPayload.message || "..."}`;
    if (statusPayload.done) {
      if (statusPayload.status === "error") {
        throw new Error(statusPayload.error || "Загрузка модели завершилась ошибкой");
      }
      break;
    }
  }
  await loadPolishModels();
  return true;
}

async function runPolish() {
  const preset = $("polishPreset").value;
  const strict = $("polishStrict").checked;
  const instruction = ($("polishInstruction").value || "").trim();
  const model = ($("polishModel").value || "").trim();
  const text = polishLastText;
  const jobId = getCurrentPolishJobId();

  if (!text) {
    $("polishHint").textContent = "Нет текста для обработки";
    return;
  }
  if (preset === "custom" && !instruction) {
    $("polishHint").textContent = "Для custom заполните инструкцию";
    return;
  }

  try {
    await ensurePolishModel(model);
  } catch (err) {
    $("polishHint").textContent = String(err);
    return;
  }

  $("polishHint").textContent = "Выполняю обработку...";
  $("polishModalResult").innerHTML = '<p class="muted">Обработка...</p>';

  const payload = {
    job_id: jobId || "",
    text,
    preset,
    instruction,
    strict,
    ollama_model: model,
    save_as_file: false,
  };
  const response = await fetch("/api/polish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    $("polishHint").textContent = data.detail || data.error || "Ошибка polish";
    $("polishModalResult").innerHTML = '<p class="muted">Ошибка обработки</p>';
    return;
  }

  polishLastResult = data.polished_text || "";
  $("polishModalResult").innerHTML = `<pre>${escapeHtml(formatReadableText(polishLastResult))}</pre>`;
  $("polishHint").textContent = "Готово. Нажмите 'Вставить в Live text'.";
}

function syncDictTextToSession() {
  const text = ($("dictLiveText").value || "").trim();
  if (!text) return;
  wsSendJson({ type: "set_text", text });
}

function applyPolishToLiveText() {
  if (!polishLastResult.trim()) {
    $("polishHint").textContent = "Сначала выполните Run";
    return;
  }
  $("dictLiveText").value = polishLastResult;
  updateMarkdownPreviewFromLive();
  syncDictTextToSession();
  $("polishHint").textContent = "Текст вставлен в Live text";
  setDictHint("Live text обновлён из polish результата");
}

function renderSources(sources) {
  const node = $("askSources");
  if (!sources || !sources.length) {
    node.innerHTML = '<p class="muted">Источники не найдены</p>';
    return;
  }

  const html = sources
    .map((src) => {
      const num = escapeHtml(src.number);
      const chunkId = escapeHtml(src.chunk_id);
      const text = escapeHtml(src.text || "");
      return `<details class="source-item"><summary>[${num}] chunk #${chunkId}</summary><div class="source-text">${text}</div></details>`;
    })
    .join("");

  node.innerHTML = html;
}

function renderAskHistory() {
  const node = $("askHistory");
  if (!askHistory.length) {
    node.innerHTML = '<p class="muted">История пока пуста</p>';
    return;
  }

  const html = askHistory
    .map((item) => {
      const q = escapeHtml(item.question);
      const a = escapeHtml(item.answer);
      const meta = `#${escapeHtml(item.job_id)} · ${escapeHtml((item.created_at || "").replace("T", " "))}`;
      return `<div class="history-item"><p class="history-q">Q: ${q}</p><p class="history-a">A: ${a}</p><p class="search-meta">${meta}</p></div>`;
    })
    .join("");
  node.innerHTML = html;
}

async function loadQaHistory() {
  const response = await fetch("/api/qa/history?limit=50");
  if (!response.ok) {
    renderAskHistory();
    return;
  }
  const payload = await response.json();
  askHistory = Array.isArray(payload.items) ? payload.items : [];
  renderAskHistory();
}

function renderGlobalResults(items) {
  const node = $("globalSearchResults");
  if (!items || !items.length) {
    node.innerHTML = '<p class="muted">Ничего не найдено</p>';
    return;
  }

  const html = items
    .map((item) => {
      const title = escapeHtml(item.title || "без названия");
      const meta = `#${escapeHtml(item.job_id)} · ${escapeHtml(item.created_at || "")}`;
      const snippet = escapeHtml(item.snippet || "");
      return `<div class="search-item"><p class="search-meta">${title} · ${meta}</p><p class="search-snippet">${snippet}</p></div>`;
    })
    .join("");
  node.innerHTML = html;
}

function renderTranscriptionHistory(items) {
  const node = $("transcriptionHistory");
  if (!items || !items.length) {
    node.innerHTML = '<p class="muted">История пока пуста</p>';
    return;
  }
  const html = items
    .map((item) => {
      const created = escapeHtml((item.created_at || "").replace("T", " "));
      const title = escapeHtml(item.title || item.source_name || "Без названия");
      const jobId = escapeHtml(item.job_id || "");
      const itemId = Number(item.id || 0);
      return `<div class="history-item"><p class="search-meta">#${jobId} · ${created}</p><p class="history-q">${title}</p><div class="history-actions"><a class="btn btn-ghost" href="/api/transcription/history/${jobId}/zip">Export ZIP</a><button class="btn btn-ghost trans-history-del-btn" type="button" data-id="${itemId}" title="Удалить запись и файлы">🗑</button></div></div>`;
    })
    .join("");
  node.innerHTML = html;
}

async function loadTranscriptionHistory() {
  const response = await fetch("/api/transcription/history?limit=60");
  if (!response.ok) {
    renderTranscriptionHistory([]);
    return;
  }
  const payload = await response.json();
  renderTranscriptionHistory(Array.isArray(payload.items) ? payload.items : []);
}

async function deleteTranscriptionHistoryItem(itemId) {
  if (!itemId || Number.isNaN(Number(itemId))) return;
  const ok = window.confirm("Удалить запись из архива транскрибации и все связанные файлы?");
  if (!ok) return;

  const response = await fetch(`/api/transcription/history/${itemId}`, { method: "DELETE" });
  if (!response.ok) {
    setHint("Не удалось удалить запись транскрибации");
    return;
  }
  setHint("Запись транскрибации и файлы удалены");
  await loadTranscriptionHistory();
}

function renderDictHistory(items) {
  const node = $("dictHistory");
  if (!items || !items.length) {
    node.innerHTML = '<p class="muted">История пока пуста</p>';
    return;
  }
  const html = items
    .map((item) => {
      const created = escapeHtml((item.created_at || "").replace("T", " "));
      const preview = escapeHtml(item.text_preview || "");
      const jobId = escapeHtml(item.job_id || "");
      const itemId = Number(item.id || 0);
      return `<div class="history-item"><p class="search-meta">#${jobId} · ${created}</p><p class="history-a">${preview}</p><div class="history-actions"><button class="btn btn-ghost history-del-btn" type="button" data-id="${itemId}">Удалить</button></div></div>`;
    })
    .join("");
  node.innerHTML = html;
}

async function loadDictHistory() {
  const response = await fetch("/api/dictation/history?limit=30");
  if (!response.ok) {
    renderDictHistory([]);
    return;
  }
  const payload = await response.json();
  renderDictHistory(Array.isArray(payload.items) ? payload.items : []);
}

async function deleteDictHistoryItem(itemId) {
  if (!itemId || Number.isNaN(Number(itemId))) return;
  const ok = window.confirm("Удалить запись из истории диктовки?");
  if (!ok) return;

  const response = await fetch(`/api/dictation/history/${itemId}`, { method: "DELETE" });
  if (!response.ok) {
    setDictHint("Не удалось удалить запись");
    return;
  }
  setDictHint("Запись истории удалена");
  await loadDictHistory();
}

function switchTab(tabName) {
  const isTranscribe = tabName === "transcribe";
  $("tabTranscribe").classList.toggle("active", isTranscribe);
  $("tabDictation").classList.toggle("active", !isTranscribe);
  $("tabBtnTranscribe").classList.toggle("active", isTranscribe);
  $("tabBtnDictation").classList.toggle("active", !isTranscribe);
}

function detectWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ws/dictation`;
}

function pickDictationMimeType() {
  const options = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus"];
  for (const opt of options) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(opt)) return opt;
  }
  return "";
}

function setDictState(text) {
  $("dictState").textContent = `State: ${text}`;
}

function setDictHint(text) {
  $("dictHint").textContent = text || "";
}

function stopDictationCapture() {
  if (dictRecorder && dictRecorder.state !== "inactive") {
    dictRecorder.stop();
  }
  if (dictStream) {
    dictStream.getTracks().forEach((t) => t.stop());
  }
  dictRecorder = null;
  dictStream = null;
}

function wsSendJson(payload) {
  if (dictWs && dictWs.readyState === WebSocket.OPEN) {
    dictWs.send(JSON.stringify(payload));
  }
}

function setupDictationWsHandlers() {
  dictWs.onopen = () => {
    setDictHint("WS connected");
  };

  dictWs.onmessage = (event) => {
    let payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    const t = payload.type;
    if (t === "status") {
      setDictHint(payload.message || "");
    } else if (t === "state") {
      setDictState(payload.state || "idle");
    } else if (t === "started") {
      setDictState("listening");
      $("dictModel").textContent = `Model: ${payload.model || "-"}`;
      $("dictDevice").textContent = `Device: ${payload.device || "-"}`;
      setDictHint(`Listening... profile=${payload.profile}`);
    } else if (t === "partial") {
      setDictHint("Transcribing...");
    } else if (t === "final") {
      $("dictLiveText").value = formatReadableText(payload.text || "");
      updateMarkdownPreviewFromLive();
    } else if (t === "stats") {
      $("dictRtf").textContent = `RTF: ${payload.rtf ?? "-"}`;
    } else if (t === "saved") {
      dictLastJobId = payload.job_id || null;
      if (dictLastJobId) showDictDownloads(dictLastJobId);
      setDictHint(`Saved: ${payload.output_dir || ""}`);
      setDictState("saved");
      loadDictHistory();
    } else if (t === "stopped") {
      setDictState("stopped");
      setDictHint("Stopped");
    } else if (t === "error") {
      setDictHint(payload.message || "dictation error");
      setDictState("error");
    }
  };

  dictWs.onclose = () => {
    dictRunning = false;
    setDictState("disconnected");
  };
}

async function startDictation() {
  if (dictRunning) return;
  const mimeType = pickDictationMimeType();
  if (!mimeType) {
    setDictHint("Browser does not support Opus MediaRecorder mimeType.");
    return;
  }
  hideDictDownloads();
  $("dictRtf").textContent = "RTF: —";

  try {
    dictStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    setDictHint(`Microphone error: ${err}`);
    return;
  }

  dictWs = new WebSocket(detectWsUrl());
  setupDictationWsHandlers();

  dictRecorder = new MediaRecorder(dictStream, { mimeType });
  dictRecorder.ondataavailable = async (e) => {
    if (!dictRunning || !e.data || e.data.size === 0) return;
    const buffer = await e.data.arrayBuffer();
    if (dictWs && dictWs.readyState === WebSocket.OPEN) {
      dictWs.send(buffer);
    }
  };
  dictRecorder.onerror = () => {
    setDictHint("Recorder error");
  };

  const waitOpen = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("WS timeout")), 5000);
    dictWs.onopen = () => {
      clearTimeout(timer);
      resolve(true);
    };
    dictWs.onerror = () => {
      clearTimeout(timer);
      reject(new Error("WS open error"));
    };
  });

  try {
    await waitOpen;
  } catch (err) {
    setDictHint(`WS error: ${err}`);
    stopDictationCapture();
    return;
  }

  setupDictationWsHandlers();
  wsSendJson({
    type: "start",
    profile: $("dictProfile").value,
    language: $("dictLanguage").value,
    summarize: $("dictSummarize").checked,
    mime_type: mimeType,
  });
  dictRecorder.start(320);
  dictRunning = true;
  setDictState("listening");
}

function stopDictation() {
  if (!dictRunning) return;
  dictRunning = false;
  stopDictationCapture();
  wsSendJson({ type: "stop" });
  setDictState("stopping");
}

function clearDictation() {
  $("dictLiveText").value = "";
  updateMarkdownPreviewFromLive();
  setDictHint("");
  setDictState("idle");
  hideDictDownloads();
  wsSendJson({ type: "clear" });
}

function saveDictation() {
  if (!dictWs || dictWs.readyState !== WebSocket.OPEN) {
    setDictHint("Dictation WS not connected");
    return;
  }
  const text = ($("dictLiveText").value || "").trim();
  wsSendJson({ type: "save", text_override: text });
  setDictHint("Saving...");
}

async function copyDictationText() {
  const text = $("dictLiveText").value || "";
  if (!text.trim()) return;
  try {
    await navigator.clipboard.writeText(text);
    setDictHint("Copied");
  } catch {
    setDictHint("Copy failed");
  }
}

async function searchGlobalHistory() {
  const query = $("globalSearchQuery").value.trim();
  if (!query) {
    $("globalSearchHint").textContent = "Введите текст для поиска.";
    return;
  }

  $("globalSearchHint").textContent = "Ищем по всем записям...";
  $("globalSearchResults").innerHTML = '<p class="muted">Поиск...</p>';

  const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=12`);
  const payload = await response.json();

  if (!response.ok) {
    $("globalSearchHint").textContent = payload.detail || payload.error || "Ошибка поиска.";
    $("globalSearchResults").innerHTML = '<p class="muted">Результаты недоступны</p>';
    return;
  }

  $("globalSearchHint").textContent = `Найдено: ${(payload.items || []).length}`;
  renderGlobalResults(payload.items || []);
}

async function askRecording() {
  const question = $("askQuestion").value.trim();
  const activeJob = lastDoneJobId || currentJobId;

  if (!activeJob) {
    $("askHint").textContent = "Сначала завершите хотя бы одну транскрибацию.";
    return;
  }
  if (!question) {
    $("askHint").textContent = "Введите вопрос.";
    return;
  }

  $("askHint").textContent = "Ищем по записи...";
  $("askAnswer").innerHTML = '<p class="muted">Формируем ответ...</p>';
  $("askSources").innerHTML = '<p class="muted">Подбираем источники...</p>';

  const fd = new FormData();
  fd.append("job_id", activeJob);
  fd.append("question", question);
  fd.append("limit", "6");

  const response = await fetch("/api/ask", { method: "POST", body: fd });
  const payload = await response.json();

  if (!response.ok) {
    $("askHint").textContent = payload.detail || payload.error || "Ошибка запроса.";
    $("askAnswer").innerHTML = '<p class="muted">Ответ не получен</p>';
    $("askSources").innerHTML = '<p class="muted">Источники не получены</p>';
    return;
  }

  const answer = payload.answer || "В записи этого нет.";
  const jobForAnswer = payload.job_id || activeJob;
  $("askHint").textContent = `Ответ по записи #${jobForAnswer}`;
  $("askAnswer").innerHTML = `<p>${escapeHtml(answer)}</p>`;
  renderSources(payload.sources || []);
  await loadQaHistory();
}

async function launchJob(response) {
  const payload = await response.json();
  if (!response.ok) {
    setStatus("error");
    setStage("error");
    setMsg(payload.detail || payload.error || "Ошибка запуска");
    setBar(1);
    return;
  }

  currentJobId = payload.id;
  $("jobid").textContent = `#${currentJobId}`;

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 700);
  await pollJob();
}

async function startFileJob() {
  if (!selectedFile) {
    setHint("Сначала выберите файл.");
    return;
  }

  resetBeforeStart("ingest");
  setStatus("Загрузка файла...");

  const fd = new FormData();
  fd.append("file", selectedFile);
  fd.append("profile", $("profile").value);

  const response = await fetch("/api/jobs", { method: "POST", body: fd });
  await launchJob(response);
}

async function startUrlJob() {
  const url = $("urlInput").value.trim();
  if (!url) {
    setHint("Вставьте ссылку, например YouTube.");
    return;
  }

  resetBeforeStart("download");
  setStatus("Проверка ссылки...");

  const fd = new FormData();
  fd.append("url", url);
  fd.append("profile", $("profile").value);

  const response = await fetch("/api/jobs/from-url", { method: "POST", body: fd });
  await launchJob(response);
}

async function pollJob() {
  if (!currentJobId) return;

  const response = await fetch(`/api/jobs/${currentJobId}`);
  if (!response.ok) {
    clearInterval(pollTimer);
    setStatus("error");
    setStage("error");
    setMsg("Не удалось получить статус задачи.");
    return;
  }

  const job = await response.json();
  setStatus(job.status || "—");
  setStage(job.stage || "—");
  setBar(job.progress || 0);
  setMsg(job.message || "");
  setTimeline(job.stage);

  if (job.status === "done") {
    clearInterval(pollTimer);
    showDownloads(currentJobId);
    celebrate();
    lastDoneJobId = currentJobId;

    const previewResponse = await fetch(`/api/jobs/${currentJobId}/preview`);
    const preview = await previewResponse.json();
    renderPreview(preview);
    loadTranscriptionHistory();
  }

  if (job.status === "error") {
    clearInterval(pollTimer);
    setTimeline("error");
    setMsg(job.error ? `ERROR: ${job.error}` : "Ошибка");
  }
}

function initDnD() {
  const drop = $("drop");
  drop.addEventListener("click", (e) => {
    const target = e.target;
    if (target instanceof HTMLElement && target.closest("#pick")) {
      return;
    }
    $("file").click();
  });
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") $("file").click();
  });
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("drag");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("drag");
    const file = e.dataTransfer.files?.[0];
    if (file) {
      selectedFile = file;
      setHint(`Файл: ${file.name}`);
    }
  });
}

function initControls() {
  $("pick").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    $("file").click();
  });
  $("file").addEventListener("change", (e) => {
    selectedFile = e.target.files?.[0] || null;
    setHint(selectedFile ? `Файл: ${selectedFile.name}` : "");
  });
  $("run").addEventListener("click", startFileJob);
  $("runUrl").addEventListener("click", startUrlJob);
  $("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      startUrlJob();
    }
  });
  $("askRun").addEventListener("click", askRecording);
  $("askQuestion").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      askRecording();
    }
  });

  $("globalSearchRun").addEventListener("click", searchGlobalHistory);
  $("globalSearchQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      searchGlobalHistory();
    }
  });

  $("tabBtnTranscribe").addEventListener("click", () => switchTab("transcribe"));
  $("tabBtnDictation").addEventListener("click", () => switchTab("dictation"));

  $("dictStart").addEventListener("click", startDictation);
  $("dictStop").addEventListener("click", stopDictation);
  $("dictClear").addEventListener("click", clearDictation);
  $("dictSave").addEventListener("click", saveDictation);
  $("dictCopy").addEventListener("click", copyDictationText);
  $("polishFromDictation").addEventListener("click", openPolishModal);
  $("polishClose").addEventListener("click", closePolishModal);
  $("polishRun").addEventListener("click", runPolish);
  $("polishApply").addEventListener("click", applyPolishToLiveText);
  $("polishPreset").addEventListener("change", () => {
    const isCustom = $("polishPreset").value === "custom";
    $("polishInstruction").placeholder = isCustom
      ? "Опишите свою команду"
      : "Доп. уточнение (необязательно)";
  });
  $("polishModal").addEventListener("click", (e) => {
    if (e.target === $("polishModal")) closePolishModal();
  });
  $("dictHistory").addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.classList.contains("history-del-btn")) return;
    const id = Number(target.dataset.id || "0");
    deleteDictHistoryItem(id);
  });
  $("transcriptionHistory").addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.classList.contains("trans-history-del-btn")) return;
    const id = Number(target.dataset.id || "0");
    deleteTranscriptionHistoryItem(id);
  });
  $("dictLiveText").addEventListener("input", () => {
    updateMarkdownPreviewFromLive();
  });
  $("tab-source").addEventListener("click", () => setMdTab("source"));
  $("tab-render").addEventListener("click", () => setMdTab("render"));
}

function resizeFx() {
  fx.width = window.innerWidth;
  fx.height = window.innerHeight;
}

function initFx() {
  fx = $("fx");
  fxCtx = fx.getContext("2d");
  resizeFx();
  window.addEventListener("resize", resizeFx);
  requestAnimationFrame(tickFx);
}

function tickFx() {
  if (!fxCtx) return;
  fxCtx.clearRect(0, 0, fx.width, fx.height);
  sparks = sparks.filter((s) => s.life > 0);
  for (const s of sparks) {
    s.x += s.vx;
    s.y += s.vy;
    s.vy += 0.06;
    s.life -= 1;
    fxCtx.beginPath();
    fxCtx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
    fxCtx.fillStyle = `rgba(${s.c}, ${Math.max(0, s.life / s.maxLife)})`;
    fxCtx.fill();
  }
  requestAnimationFrame(tickFx);
}

function celebrate() {
  const centerX = window.innerWidth * 0.5;
  const centerY = 120;
  for (let i = 0; i < 120; i++) {
    const angle = Math.random() * Math.PI * 2;
    const speed = 2 + Math.random() * 4;
    sparks.push({
      x: centerX,
      y: centerY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 1.8,
      r: 1.8 + Math.random() * 2.2,
      life: 36 + Math.random() * 36,
      maxLife: 72,
      c: Math.random() > 0.5 ? "56, 189, 248" : "34, 211, 238",
    });
  }
}

function init() {
  initTheme();
  initDnD();
  initControls();
  initFx();
  loadQaHistory();
  loadDictHistory();
  loadTranscriptionHistory();
  switchTab("transcribe");
  hideDictDownloads();
  setTimeline("queued");
  setMdTab(localStorage.getItem(MD_TAB_KEY) || "source");
  updateMarkdownPreviewFromLive();
}

init();
