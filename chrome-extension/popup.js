const API_BASE = "http://127.0.0.1:8000";
const DRAFT_KEY = "timeTrackerDraft";
const pad = (value) => String(value).padStart(2, "0");
let state = { clients: [], projects: [], entries: [], tags: [], running: null };
let draft = {};
let saveTimer = null;

const $ = (id) => document.getElementById(id);

function toLocalInput(date) {
  const d = new Date(date);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function ensureEndAfterStart(startAt, endDate = new Date()) {
  const start = new Date(startAt);
  const end = new Date(endDate);
  const endInput = toLocalInput(end);
  if (new Date(endInput) <= start) end.setTime(start.getTime() + 60000);
  return toLocalInput(end);
}

function todayKey(offset = 0) {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function durationFromSeconds(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(data.error || "Ошибка запроса");
  }
  return response.json();
}

function setStatus(message = "") {
  $("status").textContent = message;
}

function syncActionIcon() {
  chrome.runtime.sendMessage({ type: "timer-state", running: Boolean(state.running) }).catch(() => {});
}

function optionList(items, selected, placeholder) {
  return `<option value="">${placeholder}</option>` + items.map((item) => (
    `<option value="${item.id}" ${String(item.id) === String(selected) ? "selected" : ""}>${escapeHtml(item.name)}</option>`
  )).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function filteredProjects() {
  const clientId = $("clientSelect").value || state.running?.client_id || draft.client_id;
  return clientId ? state.projects.filter((project) => String(project.client_id) === String(clientId)) : state.projects;
}

async function loadDraft() {
  const stored = await chrome.storage.local.get(DRAFT_KEY);
  draft = stored[DRAFT_KEY] || {};
}

async function saveDraft() {
  draft = {
    client_id: $("clientSelect").value,
    project_id: $("projectSelect").value,
    tags: $("tagsInput").value,
    description: $("descriptionInput").value,
  };
  await chrome.storage.local.set({ [DRAFT_KEY]: draft });
  if (state.running) {
    const result = await api("/api/timer", {
      method: "PUT",
      body: JSON.stringify({ ...state.running, ...draft }),
    });
    state.running = result.running;
  }
  renderTimer();
}

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDraft().catch((error) => setStatus(error.message)), 250);
}

async function loadState() {
  await loadDraft();
  state = await api("/api/state");
  render();
  syncActionIcon();
  setStatus("");
}

function render() {
  const values = state.running || draft;
  $("clientSelect").innerHTML = optionList(state.clients, values.client_id, "Client");
  $("projectSelect").innerHTML = optionList(filteredProjects(), values.project_id, "Project");
  $("tagsInput").value = values.tags || "";
  $("descriptionInput").value = values.description || "";
  $("tagSuggestions").innerHTML = state.tags.map((tag) => `<option value="${escapeHtml(tag)}"></option>`).join("");
  renderTimer();
  renderEntries();
}

function renderTimer() {
  const running = Boolean(state.running);
  $("timerButton").classList.toggle("running", running);
  $("timerIcon").className = running ? "fa-solid fa-stop" : "fa-solid fa-play";
  $("timerLabel").textContent = running ? "Стоп" : "Старт";
  if (!running) {
    $("timerClock").textContent = "00:00:00";
    return;
  }
  const seconds = (Date.now() - new Date(state.running.start_at).getTime()) / 1000;
  $("timerClock").textContent = durationFromSeconds(seconds);
}

function renderEntries() {
  const days = [todayKey(0), todayKey(-1)];
  $("latestEntries").innerHTML = days.map((date) => {
    const rows = state.entries.filter((entry) => entry.date === date).slice(0, 2);
    const seconds = rows.reduce((sum, entry) => sum + entry.duration_seconds, 0);
    return `
      <div class="entries-table">
        <div class="entry-header">
          <span>${date}</span>
          <span>${durationFromSeconds(seconds)}</span>
        </div>
        <div class="time-entries">
          ${rows.map(entryMarkup).join("") || `<div class="entry-empty">Записей нет</div>`}
        </div>
      </div>
    `;
  }).join("");
}

function entryMarkup(entry) {
  return `
    <div class="entry">
      <div class="entry-details">
        <strong>${escapeHtml(entry.description || entry.project_name)}</strong>
        <small>${escapeHtml(entry.client_name)}</small>
        <small>${escapeHtml(entry.project_name)}</small>
      </div>
      <div class="entry-time">${entry.duration}</div>
      <button class="restart" type="button" data-repeat="${entry.id}" title="Restart">
        <span class="fa-solid fa-play" aria-hidden="true"></span>
      </button>
    </div>
  `;
}

async function startTimer(defaults = {}) {
  const payload = {
    client_id: defaults.client_id || $("clientSelect").value || state.clients[0]?.id || "",
    project_id: defaults.project_id || $("projectSelect").value || filteredProjects()[0]?.id || state.projects[0]?.id || "",
    description: defaults.description ?? $("descriptionInput").value,
    tags: defaults.tags ?? $("tagsInput").value,
    start_at: toLocalInput(new Date()),
  };
  const result = await api("/api/timer/start", { method: "POST", body: JSON.stringify(payload) });
  state.running = result.running;
  await chrome.storage.local.set({ [DRAFT_KEY]: payload });
  render();
  syncActionIcon();
}

async function stopTimer() {
  await saveDraft();
  await api("/api/timer/stop", { method: "POST", body: JSON.stringify({ end_at: ensureEndAfterStart(state.running.start_at) }) });
  state.running = null;
  syncActionIcon();
  await loadState();
}

$("timerButton").addEventListener("click", async () => {
  try {
    if (state.running) await stopTimer();
    else await startTimer();
  } catch (error) {
    setStatus(error.message);
  }
});

$("clientSelect").addEventListener("change", () => {
  $("projectSelect").innerHTML = optionList(filteredProjects(), "", "Project");
  scheduleSave();
});

["projectSelect", "tagsInput", "descriptionInput"].forEach((id) => {
  $(id).addEventListener("change", scheduleSave);
  $(id).addEventListener("blur", scheduleSave);
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-repeat]");
  if (!button) return;
  const entry = state.entries.find((item) => String(item.id) === String(button.dataset.repeat));
  if (!entry) return;
  try {
    await startTimer(entry);
  } catch (error) {
    setStatus(error.message);
  }
});

setInterval(renderTimer, 1000);
loadState().catch((error) => setStatus(`Запусти трекер на ${API_BASE}. ${error.message}`));
