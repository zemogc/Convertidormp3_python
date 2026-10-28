"use strict";

const $ = (sel) => document.querySelector(sel);
const FINAL = new Set(["done", "failed", "canceled"]);
const TOKEN_KEY = "convertidor.token";

const state = {
  token: load(TOKEN_KEY),
  formats: [],
  defaultBitrate: 192,
  files: [],
  watching: new Set(),
  blobs: new Map(),
};

// ---------- utilidades ----------

function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function save(key, value) {
  try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch { /* modo privado */ }
}

async function api(path, { method = "GET", body, headers = {}, raw = false } = {}) {
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const resp = await fetch(path, { method, body, headers });
  if (resp.status === 401 && state.token) logout();
  if (raw) return resp;
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `Error ${resp.status}`);
  return data;
}

function formatBytes(n) {
  if (n == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function formatDuration(s) {
  if (s == null) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function timeAgo(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "hace un momento";
  if (diff < 3600) return `hace ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)} h`;
  return new Date(iso).toLocaleDateString();
}

// ---------- sesion ----------

let authMode = "login";

function showAuth() {
  $("#auth-view").hidden = false;
  $("#app-view").hidden = true;
  $("#session").hidden = true;
}

async function showApp() {
  try {
    const me = await api("/api/me");
    $("#who").textContent = me.email;
  } catch {
    return showAuth();
  }
  $("#auth-view").hidden = true;
  $("#app-view").hidden = false;
  $("#session").hidden = false;
  await loadJobs();
}

function logout() {
  state.token = null;
  save(TOKEN_KEY, null);
  state.watching.clear();
  $("#jobs").replaceChildren();
  showAuth();
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    authMode = tab.dataset.mode;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    $("#auth-submit").textContent = authMode === "login" ? "Entrar" : "Crear cuenta";
    $("#auth-form").password.autocomplete = authMode === "login" ? "current-password" : "new-password";
    $("#auth-error").textContent = "";
  }),
);

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const button = $("#auth-submit");
  $("#auth-error").textContent = "";
  button.disabled = true;
  try {
    if (authMode === "register") {
      await api("/api/register", { method: "POST", body: { email, password } });
    }
    const resp = await fetch("/api/login", {
      method: "POST",
      headers: { Authorization: "Basic " + btoa(unescape(encodeURIComponent(`${email}:${password}`))) },
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || "No se pudo iniciar sesion");
    state.token = data.token;
    save(TOKEN_KEY, data.token);
    form.reset();
    await showApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
});

$("#logout").addEventListener("click", logout);

// ---------- formulario de conversion ----------

async function loadFormats() {
  const data = await api("/api/formats");
  state.formats = data.formats;
  state.defaultBitrate = data.default_bitrate;
  $("#format").replaceChildren(
    ...data.formats.map((f) => new Option(f.id.toUpperCase(), f.id)),
  );
  updateBitrates();
}

function updateBitrates() {
  const fmt = state.formats.find((f) => f.id === $("#format").value);
  const select = $("#bitrate");
  if (!fmt || !fmt.bitrates.length) {
    select.replaceChildren(new Option("Sin perdida", ""));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const best = fmt.bitrates.includes(state.defaultBitrate)
    ? state.defaultBitrate
    : fmt.bitrates[Math.floor(fmt.bitrates.length / 2)];
  select.replaceChildren(
    ...fmt.bitrates.map((b) => new Option(`${b} kbps`, b, false, b === best)),
  );
}
$("#format").addEventListener("change", updateBitrates);

const MAX_PARALLEL_UPLOADS = 2;

function setFiles(files) {
  state.files = [...(files || [])];
  const n = state.files.length;
  const size = state.files.reduce((sum, f) => sum + f.size, 0);
  $("#dropzone").classList.toggle("has-file", n > 0);
  $("#drop-title").textContent =
    n === 0 ? "Arrastra uno o varios videos aqui" : n === 1 ? state.files[0].name : `${n} archivos`;
  $("#drop-hint").textContent = n ? `${formatBytes(size)} · clic para cambiar` : "o haz clic para elegirlos";
  $("#convert").textContent = n > 1 ? `Convertir ${n} archivos` : "Convertir";
  $("#convert").disabled = n === 0;
  $("#upload-error").textContent = "";
}

const zone = $("#dropzone");
$("#file").addEventListener("change", (e) => setFiles(e.target.files));
["dragenter", "dragover"].forEach((ev) =>
  zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }),
);
["dragleave", "drop"].forEach((ev) =>
  zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }),
);
zone.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));

$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.files.length) return;
  const options = new FormData(event.target);
  options.delete("file");
  if (!options.get("bitrate")) options.delete("bitrate");

  // Todas las tarjetas aparecen al instante; las subidas van de a MAX_PARALLEL_UPLOADS.
  const pending = state.files.map((file) => ({
    file,
    card: renderJob({
      id: `upload-${crypto.randomUUID()}`,
      status: "uploading",
      progress: 0,
      filename: file.name,
      output_name: file.name,
      options: { format: options.get("format") },
      created_at: new Date().toISOString(),
    }),
  }));
  $("#jobs").prepend(...pending.map((p) => p.card));
  updateEmpty();
  setFiles([]);
  event.target.reset();
  updateBitrates();

  const errors = [];
  const worker = async () => {
    for (let item = pending.shift(); item; item = pending.shift()) {
      const err = await uploadOne(item.file, item.card, options);
      if (err) errors.push(`${item.file.name}: ${err}`);
    }
  };
  await Promise.all(Array.from({ length: MAX_PARALLEL_UPLOADS }, worker));
  $("#upload-error").textContent = errors.join(" · ");
});

function uploadOne(file, card, options) {
  const form = new FormData();
  for (const [key, value] of options) form.append(key, value);
  form.append("file", file);

  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) setProgress(card, (e.loaded / e.total) * 100);
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* respuesta vacia */ }
      if (xhr.status === 401) {
        logout();
        return resolve("sesion expirada");
      }
      if (xhr.status >= 400) {
        card.remove();
        updateEmpty();
        return resolve(data.error || `Error ${xhr.status}`);
      }
      card.replaceWith(renderJob(data));
      if (!FINAL.has(data.status)) watch(data.id);
      resolve(null);
    };
    xhr.onerror = () => {
      card.remove();
      updateEmpty();
      resolve("no se pudo subir, revisa tu conexion");
    };
    xhr.send(form);
  });
}

// ---------- lista de trabajos ----------

async function loadJobs() {
  const jobs = await api("/api/jobs");
  $("#jobs").replaceChildren(...jobs.map(renderJob));
  updateEmpty();
  jobs.filter((j) => !FINAL.has(j.status)).forEach((j) => watch(j.id));
  highlightFromHash();
}

function updateEmpty() {
  const n = $("#jobs").children.length;
  $("#empty").hidden = n > 0;
  $("#count").textContent = n ? `${n} ${n === 1 ? "archivo" : "archivos"}` : "";
}

function statusInfo(job) {
  if (job.status === "uploading") return ["Subiendo", ""];
  if (job.status === "done") return [job.cached ? "Listo · instantaneo" : "Listo", "done"];
  if (job.status === "failed") return ["Error", "failed"];
  if (job.status === "canceled") return ["Cancelado", "canceled"];
  if (job.canceling) return ["Cancelando…", "retry"];
  if (job.status === "queued" && job.error) return ["Reintentando", "retry"];
  // Tras ffmpeg queda calcular la forma de onda y guardar el resultado.
  if (job.status === "processing" && job.progress >= 100) return ["Finalizando…", ""];
  if (job.status === "processing") return [`Convirtiendo ${Math.round(job.progress)}%`, ""];
  return ["En cola", ""];
}

function setProgress(el, pct) {
  el.querySelector(".fill").style.width = `${Math.max(2, Math.min(100, pct))}%`;
  el.classList.remove("indeterminate");
}

function renderJob(job) {
  const el = $("#job-template").content.firstElementChild.cloneNode(true);
  el.id = `job-${job.id}`;
  updateJob(el, job);
  el.querySelector(".download").addEventListener("click", () => download(job.id, el));
  el.querySelector(".cancel").addEventListener("click", () => cancelJob(job.id, el));
  el.querySelector(".delete").addEventListener("click", () => deleteJob(job.id, el));
  return el;
}

function updateJob(el, job) {
  el.dataset.name = job.output_name;
  el.className = `job ${job.status}`;
  el.querySelector(".name").textContent = job.output_name;
  el.querySelector(".name").title = `Original: ${job.filename}`;

  const [label, cls] = statusInfo(job);
  const badge = el.querySelector(".badge");
  badge.textContent = label;
  badge.className = `badge ${cls}`;

  const o = job.options || {};
  const parts = [
    (o.format || "").toUpperCase() + (o.bitrate ? ` ${o.bitrate} kbps` : ""),
    o.normalize ? "normalizado" : "",
    o.start || o.end ? `recorte ${formatDuration(o.start || 0)}–${o.end ? formatDuration(o.end) : "fin"}` : "",
    formatDuration(job.duration),
    formatBytes(job.output_size),
    timeAgo(job.created_at),
  ];
  el.querySelector(".meta").textContent = parts.filter(Boolean).join(" · ");

  if (job.status === "queued" || (job.status === "processing" && !job.progress)) {
    el.classList.add("indeterminate");
  } else {
    setProgress(el, job.progress);
  }
  el.querySelector(".job-error").textContent =
    job.status === "failed" || job.status === "queued" ? job.error || "" : "";

  const done = job.status === "done";
  el.querySelector(".download").hidden = !done;
  if (done) setupPlayer(el, job);
  const uploading = job.status === "uploading";
  el.querySelector(".cancel").hidden = uploading || FINAL.has(job.status) || !!job.canceling;
  el.querySelector(".delete").hidden = !FINAL.has(job.status);
}

async function cancelJob(id, el) {
  const button = el.querySelector(".cancel");
  button.disabled = true;
  try {
    updateJob(el, await api(`/api/jobs/${id}/cancel`, { method: "POST" }));
  } catch (err) {
    el.querySelector(".job-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
}

async function deleteJob(id, el) {
  if (!confirm(`¿Borrar "${el.dataset.name}"? No se puede deshacer.`)) return;
  try {
    const resp = await api(`/api/jobs/${id}`, { method: "DELETE", raw: true });
    if (!resp.ok && resp.status !== 404) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error || `Error ${resp.status}`);
    }
    el.querySelector("audio")?.pause();
    if (state.blobs.has(id)) URL.revokeObjectURL(state.blobs.get(id));
    state.blobs.delete(id);
    el.remove();
    updateEmpty();
  } catch (err) {
    el.querySelector(".job-error").textContent = err.message;
  }
}

// Progreso en vivo: el endpoint es SSE, pero se lee con fetch para poder
// mandar el token en el header (EventSource no permite headers).
async function watch(id, retries = 0) {
  if (state.watching.has(id)) return;
  state.watching.add(id);
  let finished = false;
  try {
    const resp = await api(`/api/jobs/${id}/events`, { raw: true });
    if (!resp.ok) return;
    const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let sep;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("");
        if (!data) continue;
        const job = JSON.parse(data);
        const el = document.getElementById(`job-${id}`);
        if (el) updateJob(el, job);
        if (FINAL.has(job.status)) finished = true;
      }
    }
  } catch {
    /* corte de red: se reintenta abajo */
  } finally {
    state.watching.delete(id);
  }
  if (!finished && state.token && retries < 20) {
    setTimeout(() => watch(id, retries + 1), Math.min(1000 * 2 ** retries, 10000));
  }
}

async function fetchAudio(id) {
  if (state.blobs.has(id)) return state.blobs.get(id);
  const resp = await api(`/api/jobs/${id}/download`, { raw: true });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || `Error ${resp.status}`);
  }
  const url = URL.createObjectURL(await resp.blob());
  state.blobs.set(id, url);
  return url;
}

async function download(id, el) {
  const button = el.querySelector(".download");
  button.disabled = true;
  try {
    const a = document.createElement("a");
    a.href = await fetchAudio(id);
    a.download = el.dataset.name;
    a.click();
  } catch (err) {
    el.querySelector(".job-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
}

// ---------- reproductor con forma de onda ----------

const SVG_NS = "http://www.w3.org/2000/svg";
const WAVE_HEIGHT = 40;
let nowPlaying = null;

function drawBars(group, peaks) {
  peaks.forEach((peak, i) => {
    const h = Math.max(1.5, peak * WAVE_HEIGHT);
    const bar = document.createElementNS(SVG_NS, "rect");
    bar.setAttribute("x", i + 0.15);
    bar.setAttribute("width", 0.7);
    bar.setAttribute("y", (WAVE_HEIGHT - h) / 2);
    bar.setAttribute("height", h);
    group.append(bar);
  });
}

function setupPlayer(el, job) {
  const player = el.querySelector(".player");
  if (player.dataset.ready) return;
  player.dataset.ready = "1";
  player.hidden = false;

  // Trabajos anteriores a la forma de onda: barra plana, el reproductor funciona igual.
  const peaks = job.waveform?.length ? job.waveform : Array(80).fill(0.1);
  const wave = player.querySelector(".wave");
  const clipId = `clip-${job.id}`;
  wave.setAttribute("viewBox", `0 0 ${peaks.length} ${WAVE_HEIGHT}`);
  wave.querySelector("clipPath").id = clipId;
  wave.querySelector(".played").setAttribute("clip-path", `url(#${clipId})`);
  wave.querySelectorAll(".bars").forEach((g) => drawBars(g, peaks));

  const audio = player.querySelector("audio");
  const button = player.querySelector(".play-toggle");
  const area = wave.querySelector(".played-area");
  const time = player.querySelector(".time");
  const total = () => (Number.isFinite(audio.duration) && audio.duration) || job.duration || 0;

  const paint = () => {
    const t = total();
    area.setAttribute("width", t ? (audio.currentTime / t) * peaks.length : 0);
    time.textContent = `${formatDuration(audio.currentTime)} / ${formatDuration(t)}`;
    wave.setAttribute("aria-valuenow", Math.round(audio.currentTime));
    wave.setAttribute("aria-valuemax", Math.round(t));
  };
  const loop = () => {
    paint();
    if (!audio.paused) requestAnimationFrame(loop);
  };
  paint();

  const ensureLoaded = async () => {
    if (audio.src) return;
    button.classList.add("loading");
    try {
      const url = await fetchAudio(job.id);
      await new Promise((resolve, reject) => {
        audio.addEventListener("loadedmetadata", resolve, { once: true });
        audio.addEventListener("error", () => reject(new Error("No se pudo cargar el audio")), { once: true });
        audio.src = url;
      });
    } finally {
      button.classList.remove("loading");
    }
  };

  const play = async () => {
    try {
      await ensureLoaded();
      if (nowPlaying && nowPlaying !== audio) nowPlaying.pause();
      nowPlaying = audio;
      await audio.play();
    } catch (err) {
      el.querySelector(".job-error").textContent = err.message;
    }
  };

  const seekTo = async (seconds) => {
    await ensureLoaded();
    audio.currentTime = Math.min(Math.max(seconds, 0), total());
    paint();
  };

  button.addEventListener("click", () => (audio.paused ? play() : audio.pause()));
  audio.addEventListener("play", () => {
    player.classList.add("playing");
    button.setAttribute("aria-label", "Pausar");
    requestAnimationFrame(loop);
  });
  ["pause", "ended"].forEach((ev) =>
    audio.addEventListener(ev, () => {
      player.classList.remove("playing");
      button.setAttribute("aria-label", "Reproducir");
      paint();
    }),
  );

  wave.addEventListener("click", async (e) => {
    const rect = wave.getBoundingClientRect();
    await seekTo(((e.clientX - rect.left) / rect.width) * total());
    if (audio.paused) play();
  });
  wave.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 5, ArrowLeft: -5 }[e.key];
    if (step) {
      e.preventDefault();
      seekTo(audio.currentTime + step);
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      button.click();
    }
  });
}

// Enlace del email: /#job-<id> resalta ese trabajo.
function highlightFromHash() {
  const el = location.hash && document.getElementById(location.hash.slice(1));
  if (!el) return;
  el.classList.add("highlight");
  el.scrollIntoView({ behavior: "smooth", block: "center" });
}
window.addEventListener("hashchange", highlightFromHash);

// ---------- arranque ----------

(async () => {
  try {
    await loadFormats();
  } catch {
    $("#upload-error").textContent = "No se pudieron cargar los formatos.";
  }
  if (state.token) await showApp();
  else showAuth();
})();
