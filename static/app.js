const state = {
  clients: [],
  projects: [],
  entries: [],
  tags: [],
  totals: { duration: "00:00:00", amounts: {} },
  currencies: { RUB: "₽", USD: "$", CAD: "C$" },
  view: "tracker",
  running: JSON.parse(localStorage.getItem("runningTimer") || "null"),
  filters: {},
};

const $ = (id) => document.getElementById(id);
const pad = (n) => String(n).padStart(2, "0");
const BASE_TITLE = document.title;
const toLocalInput = (date) => {
  const d = new Date(date);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};
const toDateInput = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const today = () => toDateInput(new Date());
const FILTER_KEYS = ["from", "to", "client_id", "project_id", "tags"];

function durationFromSeconds(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(data.error || "Ошибка запроса");
  }
  return res.json();
}

function queryString() {
  const params = new URLSearchParams();
  if (state.view === "reports" || hasActiveFilters()) params.set("view", state.view);
  Object.entries(state.filters).forEach(([key, value]) => {
    if (Array.isArray(value)) value.forEach((item) => item && params.append(key, item));
    else if (value) params.set(key, value);
  });
  return params.toString();
}

function readFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  state.filters = {
    from: params.get("from") || "",
    to: params.get("to") || "",
    client_id: params.get("client_id") || "",
    project_id: params.get("project_id") || "",
    tags: params.getAll("tags").flatMap((tag) => tag.split(",")).map((tag) => tag.trim()).filter(Boolean),
  };
  const requestedView = params.get("view");
  if (requestedView && document.getElementById(requestedView)) {
    state.view = requestedView;
  } else if (hasActiveFilters()) {
    state.view = "reports";
  }
}

function hasActiveFilters() {
  return FILTER_KEYS.some((key) => {
    const value = state.filters[key];
    return Array.isArray(value) ? value.length > 0 : Boolean(value);
  });
}

function writeFiltersToUrl(replace = false) {
  const qs = queryString();
  const nextUrl = `${window.location.pathname}${qs ? `?${qs}` : ""}`;
  const method = replace ? "replaceState" : "pushState";
  window.history[method]({}, "", nextUrl);
}

async function loadState() {
  const qs = queryString();
  const data = await api(`/api/state${qs ? `?${qs}` : ""}`);
  Object.assign(state, data);
  render();
}

function optionList(items, selected = "", empty = "Все") {
  const head = empty === null ? "" : `<option value="">${empty}</option>`;
  return head + items.map((item) => `<option value="${item.id}" ${String(item.id) === String(selected) ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
}

function currencyOptions(selected = "RUB") {
  return Object.keys(state.currencies).map((cur) => `<option value="${cur}" ${cur === selected ? "selected" : ""}>${cur}</option>`).join("");
}

function formatAmount(value) {
  return String(value ?? "0,00").replace(".", ",");
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

function setView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active-view", el.id === view));
  document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("active", el.dataset.view === view));
  $("viewTitle").textContent = { tracker: "Трекер", reports: "Отчеты", clients: "Клиенты", projects: "Проекты", dashboard: "Дашборд" }[view];
  if (view === "reports" || hasActiveFilters()) writeFiltersToUrl(true);
}

function render() {
  setView(state.view);
  renderSelects();
  renderTimer();
  renderEntries();
  renderClients();
  renderProjects();
  renderDashboard();
}

function renderSelects() {
  $("filterFrom").value = state.filters.from || "";
  $("filterTo").value = state.filters.to || "";
  $("filterClient").innerHTML = optionList(state.clients, state.filters.client_id || "");
  $("filterProject").innerHTML = optionList(filteredProjects(state.filters.client_id || ""), state.filters.project_id || "");
  $("entryClient").innerHTML = optionList(state.clients, "", "Выбери клиента");
  $("entryProject").innerHTML = optionList(state.projects, "", "Выбери проект");
  $("projectClient").innerHTML = optionList(state.clients, "", "Выбери клиента");
  $("clientCurrency").innerHTML = currencyOptions();
  $("projectCurrency").innerHTML = currencyOptions();
  updateTagSuggestions($("entryTags")?.value || "");
  const selectedTags = new Set(state.filters.tags || []);
  $("tagFilters").innerHTML = state.tags.length
    ? state.tags.map((tag) => `
      <label><input type="checkbox" value="${escapeHtml(tag)}" ${selectedTags.has(tag) ? "checked" : ""}> ${escapeHtml(tag)}</label>
    `).join("")
    : `<span class="muted">Тегов пока нет.</span>`;
  $("tagSummary").textContent = selectedTags.size ? `Теги: ${[...selectedTags].join(", ")}` : "Все теги";
}

function updateTagSuggestions(value = "") {
  const lastComma = value.lastIndexOf(",");
  const prefix = lastComma >= 0 ? `${value.slice(0, lastComma + 1).trimEnd()} ` : "";
  const existing = new Set(value.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean));
  $("tagSuggestions").innerHTML = state.tags
    .filter((tag) => !existing.has(tag.toLowerCase()))
    .map((tag) => `<option value="${escapeHtml(prefix + tag)}"></option>`)
    .join("");
}

function filteredProjects(clientId) {
  return clientId ? state.projects.filter((p) => String(p.client_id) === String(clientId)) : state.projects;
}

function renderTimer() {
  const running = Boolean(state.running);
  $("timerButton").classList.toggle("running", running);
  $("timerLabel").textContent = running ? "Стоп" : "Старт";
  $("timerIcon").className = running ? "fa-solid fa-stop" : "fa-solid fa-play";
  if (!running) {
    $("timerClock").textContent = "00:00:00";
    $("runningStatus").textContent = "Счетчик остановлен";
    $("runningMeta").textContent = "Нажми «Старт», детали можно заполнить уже после запуска.";
    document.title = BASE_TITLE;
    return;
  }
  const project = state.projects.find((p) => String(p.id) === String(state.running.project_id));
  const client = state.clients.find((c) => String(c.id) === String(state.running.client_id));
  $("runningStatus").textContent = state.running.description || "Идет работа";
  $("runningMeta").textContent = [client?.name, project?.name].filter(Boolean).join(" / ") || "Детали еще не заполнены";
  tickTimer();
}

function tickTimer() {
  if (!state.running) {
    document.title = BASE_TITLE;
    return;
  }
  const seconds = (Date.now() - new Date(state.running.start_at).getTime()) / 1000;
  const duration = durationFromSeconds(seconds);
  $("timerClock").textContent = duration;
  document.title = `${duration} · ${BASE_TITLE}`;
}

function renderEntries() {
  const todayEntries = state.entries.filter((entry) => entry.date === today());
  $("todayCount").textContent = todayEntries.length;
  $("todayDuration").textContent = durationFromSeconds(todayEntries.reduce((sum, entry) => sum + entry.duration_seconds, 0));
  $("totalDuration").textContent = state.totals.duration;
  $("totalAmount").textContent = Object.entries(state.totals.amounts || {})
    .map(([cur, value]) => `${state.currencies[cur] || cur} ${formatAmount(value)}`)
    .join(", ") || "0,00";

  $("recentEntries").innerHTML = state.entries.slice(0, 6).map(entryRow).join("") || `<p class="muted">Записей пока нет.</p>`;
  $("entriesTable").innerHTML = state.entries.map(tableRow).join("") || `<tr><td colspan="8" class="muted">Ничего не найдено.</td></tr>`;
}

function entryRow(entry) {
  return `
    <div class="entry-row">
      <div class="entry-main">
        <strong>${escapeHtml(entry.description || entry.project_name)}</strong>
        <small>${escapeHtml(entry.date)} · ${escapeHtml(entry.client_name)} / ${escapeHtml(entry.project_name)} · ${entry.duration}</small>
      </div>
      <div class="row-actions">
        <button class="ghost" data-repeat="${entry.id}"><i class="fa-solid fa-play"></i> Старт</button>
        <button class="ghost" data-edit-entry="${entry.id}"><i class="fa-solid fa-pen"></i> Править</button>
      </div>
    </div>
  `;
}

function tableRow(entry) {
  const cross = entry.cross_day ? `<sup>+${entry.cross_day}</sup>` : "";
  return `
    <tr>
      <td>${entry.date}${cross}</td>
      <td>${escapeHtml(entry.client_name)}</td>
      <td>${escapeHtml(entry.project_name)}</td>
      <td>${escapeHtml(entry.description)}<br><small class="muted">${escapeHtml(entry.tags)}</small></td>
      <td>${entry.timerange}</td>
      <td>${entry.duration}</td>
      <td>${entry.currency_symbol} ${formatAmount(entry.amount)}</td>
      <td>
        <div class="row-actions">
          <button class="ghost" data-repeat="${entry.id}" title="Запустить такую же запись"><i class="fa-solid fa-play"></i></button>
          <button class="ghost" data-edit-entry="${entry.id}" title="Редактировать"><i class="fa-solid fa-pen"></i></button>
          <button class="ghost danger" data-delete-entry="${entry.id}" title="Удалить"><i class="fa-solid fa-trash"></i></button>
        </div>
      </td>
    </tr>
  `;
}

function renderClients() {
  $("clientsList").innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Клиент</th>
            <th>Контакт</th>
            <th>Email</th>
            <th>Валюта</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${state.clients.map((client) => `
            <tr>
              <td>${escapeHtml(client.name)}</td>
              <td>${escapeHtml(client.contact_name || "Контакт не указан")}</td>
              <td>${escapeHtml(client.contact_email || "Email не указан")}</td>
              <td>${client.currency}</td>
              <td>
                <div class="row-actions">
                  <button class="ghost" data-edit-client="${client.id}" title="Редактировать"><i class="fa-solid fa-pen"></i></button>
                  <button class="ghost danger" data-delete-client="${client.id}" title="Удалить"><i class="fa-solid fa-trash"></i></button>
                </div>
              </td>
            </tr>
          `).join("") || `<tr><td colspan="5" class="muted">Клиентов пока нет.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function renderProjects() {
  $("projectsList").innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Проект</th>
            <th>Клиент</th>
            <th>Ставка</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${state.projects.map((project) => `
            <tr>
              <td>${escapeHtml(project.name)}</td>
              <td>${escapeHtml(project.client_name)}</td>
              <td>${state.currencies[project.currency] || project.currency} ${formatAmount(project.hourly_rate)}/час</td>
              <td>
                <div class="row-actions">
                  <button class="ghost" data-edit-project="${project.id}" title="Редактировать"><i class="fa-solid fa-pen"></i></button>
                  <button class="ghost danger" data-delete-project="${project.id}" title="Удалить"><i class="fa-solid fa-trash"></i></button>
                </div>
              </td>
            </tr>
          `).join("") || `<tr><td colspan="4" class="muted">Проектов пока нет.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function renderDashboard() {
  $("dashClients").textContent = state.clients.length;
  $("dashProjects").textContent = state.projects.length;
  $("dashEntries").textContent = state.entries.length;
}

function openEntryDialog(entry = null, defaults = {}) {
  $("entryDialogTitle").textContent = entry ? "Редактировать запись" : "Time entry";
  $("entryId").value = entry?.id || "";
  $("entryClient").value = entry?.client_id || defaults.client_id || state.clients[0]?.id || "";
  updateEntryProjects(entry?.project_id || defaults.project_id || "");
  $("entryDescription").value = entry?.description || defaults.description || "";
  $("entryTags").value = entry?.tags || defaults.tags || "";
  $("entryStart").value = (entry?.start_at || defaults.start_at || toLocalInput(new Date())).slice(0, 16);
  $("entryEnd").value = (entry?.end_at || defaults.end_at || toLocalInput(new Date(Date.now() + 3600000))).slice(0, 16);
  $("entryDialog").showModal();
}

function updateEntryProjects(selected = "") {
  const clientId = $("entryClient").value;
  const projects = filteredProjects(clientId);
  $("entryProject").innerHTML = optionList(projects, selected || projects[0]?.id || "", "Выбери проект");
}

function openClientDialog(client = null) {
  $("clientId").value = client?.id || "";
  $("clientName").value = client?.name || "";
  $("clientContact").value = client?.contact_name || "";
  $("clientEmail").value = client?.contact_email || "";
  $("clientCurrency").innerHTML = currencyOptions(client?.currency || "RUB");
  $("clientDialog").showModal();
}

function openProjectDialog(project = null) {
  $("projectId").value = project?.id || "";
  $("projectClient").innerHTML = optionList(state.clients, project?.client_id || state.clients[0]?.id || "", "Выбери клиента");
  $("projectName").value = project?.name || "";
  $("projectRate").value = project?.hourly_rate || "0";
  $("projectCurrency").innerHTML = currencyOptions(project?.currency || "RUB");
  $("projectDialog").showModal();
}

async function saveEntry(event) {
  event.preventDefault();
  const id = $("entryId").value;
  const payload = {
    client_id: $("entryClient").value,
    project_id: $("entryProject").value,
    description: $("entryDescription").value,
    tags: $("entryTags").value,
    start_at: $("entryStart").value,
    end_at: $("entryEnd").value,
  };
  if (state.running && !id && payload.start_at === state.running.start_at) {
    state.running = { ...state.running, ...payload };
    localStorage.setItem("runningTimer", JSON.stringify(state.running));
    $("entryDialog").close();
    renderTimer();
    return;
  }
  await api(id ? `/api/entries/${id}` : "/api/entries", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  $("entryDialog").close();
  await loadState();
}

async function saveClient(event) {
  event.preventDefault();
  const id = $("clientId").value;
  const payload = {
    name: $("clientName").value,
    contact_name: $("clientContact").value,
    contact_email: $("clientEmail").value,
    currency: $("clientCurrency").value,
  };
  await api(id ? `/api/clients/${id}` : "/api/clients", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  $("clientDialog").close();
  await loadState();
}

async function saveProject(event) {
  event.preventDefault();
  const id = $("projectId").value;
  const payload = {
    client_id: $("projectClient").value,
    name: $("projectName").value,
    hourly_rate: $("projectRate").value,
    currency: $("projectCurrency").value,
  };
  await api(id ? `/api/projects/${id}` : "/api/projects", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  $("projectDialog").close();
  await loadState();
}

function startTimer(defaults = {}) {
  if (!state.clients.length || !state.projects.length) {
    alert("Сначала добавь клиента и проект. Без них трекер будет считать воздух, а воздух плохо оплачивается.");
    return;
  }
  state.running = {
    client_id: defaults.client_id || state.clients[0]?.id || "",
    project_id: defaults.project_id || state.projects.find((p) => String(p.client_id) === String(defaults.client_id))?.id || state.projects[0]?.id || "",
    description: defaults.description || "",
    tags: defaults.tags || "",
    start_at: toLocalInput(new Date()),
  };
  localStorage.setItem("runningTimer", JSON.stringify(state.running));
  renderTimer();
  openEntryDialog(null, { ...state.running, end_at: toLocalInput(new Date(Date.now() + 60000)) });
}

async function stopTimer() {
  const running = state.running;
  if (!running) return;
  state.running = null;
  localStorage.removeItem("runningTimer");
  const end = toLocalInput(new Date());
  await api("/api/entries", {
    method: "POST",
    body: JSON.stringify({ ...running, end_at: end }),
  });
  await loadState();
}

function collectFilters() {
  state.filters = {
    from: $("filterFrom").value,
    to: $("filterTo").value,
    client_id: $("filterClient").value,
    project_id: $("filterProject").value,
    tags: [...document.querySelectorAll("#tagFilters input:checked")].map((input) => input.value),
  };
}

function syncPrintForm() {
  collectFilters();
  const fields = [];
  Object.entries(state.filters).forEach(([key, value]) => {
    const values = Array.isArray(value) ? value : [value];
    values.filter(Boolean).forEach((item) => {
      fields.push(`<input type="hidden" name="${escapeHtml(key)}" value="${escapeHtml(item)}">`);
    });
  });
  $("printFields").innerHTML = fields.join("");
}

async function applyFilters({ replace = false } = {}) {
  collectFilters();
  state.view = "reports";
  writeFiltersToUrl(replace);
  await loadState();
}

async function resetFilters() {
  state.filters = {};
  $("filterFrom").value = "";
  $("filterTo").value = "";
  $("filterClient").value = "";
  $("filterProject").value = "";
  document.querySelectorAll("#tagFilters input").forEach((input) => input.checked = false);
  $("tagSummary").textContent = "Все теги";
  writeFiltersToUrl(false);
  await loadState();
}

function setPeriod(kind) {
  const now = new Date();
  let start;
  let end;
  if (kind === "month") {
    start = new Date(now.getFullYear(), now.getMonth(), 1);
    end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  } else if (kind === "prev") {
    start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    end = new Date(now.getFullYear(), now.getMonth(), 0);
  } else {
    start = new Date(now.getFullYear(), 0, 1);
    end = now;
  }
  $("filterFrom").value = toDateInput(start);
  $("filterTo").value = toDateInput(end);
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, label, a");
  if (!target) return;
  if (target.dataset.view) setView(target.dataset.view);
  if (target.dataset.viewJump) setView(target.dataset.viewJump);
  if (target.id === "openEntry") openEntryDialog();
  if (target.id === "addClient") openClientDialog();
  if (target.id === "addProject") openProjectDialog();
  if (target.id === "periodMonth") setPeriod("month");
  if (target.id === "periodPrevMonth") setPeriod("prev");
  if (target.id === "periodYear") setPeriod("year");
  if (target.id === "applyFilters") {
    await applyFilters();
  }
  if (target.id === "resetFilters") {
    await resetFilters();
  }
  if (target.dataset.editEntry) openEntryDialog(state.entries.find((entry) => String(entry.id) === target.dataset.editEntry));
  if (target.dataset.editClient) openClientDialog(state.clients.find((client) => String(client.id) === target.dataset.editClient));
  if (target.dataset.editProject) openProjectDialog(state.projects.find((project) => String(project.id) === target.dataset.editProject));
  if (target.dataset.repeat) {
    const entry = state.entries.find((item) => String(item.id) === target.dataset.repeat);
    startTimer(entry);
  }
  if (target.dataset.deleteEntry && confirm("Удалить time entry?")) {
    await api(`/api/entries/${target.dataset.deleteEntry}`, { method: "DELETE" });
    await loadState();
  }
  if (target.dataset.deleteClient && confirm("Удалить клиента вместе с проектами и записями?")) {
    await api(`/api/clients/${target.dataset.deleteClient}`, { method: "DELETE" });
    await loadState();
  }
  if (target.dataset.deleteProject && confirm("Удалить проект вместе с записями?")) {
    await api(`/api/projects/${target.dataset.deleteProject}`, { method: "DELETE" });
    await loadState();
  }
});

$("timerButton").addEventListener("click", async () => {
  if (state.running) await stopTimer();
  else startTimer();
});

$("entryClient").addEventListener("change", () => updateEntryProjects());
$("entryTags").addEventListener("input", (event) => updateTagSuggestions(event.target.value));
$("filterClient").addEventListener("change", () => {
  $("filterProject").innerHTML = optionList(filteredProjects($("filterClient").value));
  $("filterProject").value = "";
});
$("tagFilters").addEventListener("change", () => {
  const selected = [...document.querySelectorAll("#tagFilters input:checked")].map((input) => input.value);
  $("tagSummary").textContent = selected.length ? `Теги: ${selected.join(", ")}` : "Все теги";
});

window.addEventListener("popstate", async () => {
  readFiltersFromUrl();
  await loadState();
});

document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => button.closest("dialog").close());
});

$("entryForm").addEventListener("submit", saveEntry);
$("clientForm").addEventListener("submit", saveClient);
$("projectForm").addEventListener("submit", saveProject);
$("printForm").addEventListener("submit", syncPrintForm);

$("importPdf").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const result = await api("/api/import", { method: "POST", body: form });
  alert(`Импортировано записей: ${result.imported_entries}. ${result.note}`);
  event.target.value = "";
  await loadState();
});

setInterval(tickTimer, 1000);
readFiltersFromUrl();
loadState().catch((error) => alert(error.message));
