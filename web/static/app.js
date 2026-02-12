let selectedFile = null;
let currentJobId = null;
let lastDoneJobId = null;
let pollTimer = null;
let fx = null;
let fxCtx = null;
let sparks = [];
let activeStage = "queued";

const STAGES = ["download", "ingest", "stt", "summarize", "export"];
const THEME_KEY = "transcribelite_theme";

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

async function askRecording() {
  const question = $("askQuestion").value.trim();
  const activeJob = currentJobId || lastDoneJobId;

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

  $("askHint").textContent = `Ответ по записи #${activeJob}`;
  $("askAnswer").innerHTML = `<p>${escapeHtml(payload.answer || "В записи этого нет.")}</p>`;
  renderSources(payload.sources || []);
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
  }

  if (job.status === "error") {
    clearInterval(pollTimer);
    setTimeline("error");
    setMsg(job.error ? `ERROR: ${job.error}` : "Ошибка");
  }
}

function initDnD() {
  const drop = $("drop");
  drop.addEventListener("click", () => $("file").click());
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
  $("pick").addEventListener("click", () => $("file").click());
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
  setTimeline("queued");
}

init();
