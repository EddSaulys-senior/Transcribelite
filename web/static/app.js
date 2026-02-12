let selectedFile = null;
let currentJobId = null;
let pollTimer = null;
let fx = null;
let fxCtx = null;
let sparks = [];
let activeStage = "queued";

const STAGES = ["ingest", "stt", "summarize", "export"];

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

async function startJob() {
  if (!selectedFile) {
    setHint("Сначала выберите файл.");
    return;
  }

  setHint("");
  setStatus("Загрузка...");
  setStage("upload");
  setMsg("");
  setBar(0.02);
  setTimeline("ingest");
  hideDownloads();
  $("note").textContent = "";
  $("transcript").textContent = "";

  const fd = new FormData();
  fd.append("file", selectedFile);
  fd.append("profile", $("profile").value);

  const response = await fetch("/api/jobs", { method: "POST", body: fd });
  const payload = await response.json();
  if (!response.ok) {
    setStatus("error");
    setStage("error");
    setMsg(payload.detail || payload.error || "Upload failed");
    setBar(1);
    return;
  }

  currentJobId = payload.id;
  $("jobid").textContent = `#${currentJobId}`;

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollJob, 700);
  await pollJob();
}

function hideDownloads() {
  $("dl_note").classList.add("hidden");
  $("dl_txt").classList.add("hidden");
  $("dl_json").classList.add("hidden");
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

    const previewResponse = await fetch(`/api/jobs/${currentJobId}/preview`);
    const preview = await previewResponse.json();
    $("note").textContent = preview.note_md || "";
    $("transcript").textContent = preview.transcript || "";
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
  $("run").addEventListener("click", startJob);
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
      c: Math.random() > 0.5 ? "14, 165, 233" : "34, 197, 94",
    });
  }
}

function init() {
  initDnD();
  initControls();
  initFx();
  setTimeline("queued");
}

init();
