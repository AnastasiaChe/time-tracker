const state = {
  clients: [],
  projects: [],
  entries: [],
  tags: [],
  totals: { duration: "00:00:00", amounts: {} },
  currencies: { RUB: "₽", USD: "$", CAD: "C$" },
  view: "tracker",
  running: null,
  filters: {},
  sorts: {
    entries: { key: "date", direction: "desc" },
    clients: { key: "created_at", direction: "desc" },
    projects: { key: "created_at", direction: "desc" },
    dashboardActivities: { key: "last_activity", direction: "desc" },
  },
  completingTimer: false,
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
const UNTAGGED_FILTER = "__untagged__";
let calendarBaseDate = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let calendarHoverDate = "";
let calendarSuppressClick = false;
let calendarStartNewRangeOnNextDate = false;
let dateRangePresetLabel = "";

function ensureEndAfterStart(startAt, endDate = new Date()) {
  const start = new Date(startAt);
  const end = new Date(endDate);
  const endInput = toLocalInput(end);
  if (new Date(endInput) <= start) end.setTime(start.getTime() + 60000);
  return toLocalInput(end);
}

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
  dateRangePresetLabel = "";
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

function tagLabel(tag) {
  return tag === UNTAGGED_FILTER ? "Без тегов" : tag;
}

function parseDateInput(value) {
  if (!value) return null;
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatDotDate(value) {
  const date = typeof value === "string" ? parseDateInput(value) : value;
  if (!date) return "";
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

function formatShortDate(value) {
  const date = typeof value === "string" ? parseDateInput(value) : value;
  if (!date) return "";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
}

function sameDate(a, b) {
  return a && b && toDateInput(a) === toDateInput(b);
}

function addDays(date, amount) {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  return next;
}

function addMonths(date, amount) {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

function weekBounds(date = new Date()) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  return { start, end: addDays(start, 6) };
}

function monthName(date) {
  return date.toLocaleDateString("ru-RU", { month: "long", year: "numeric" }).replace(/^./, (char) => char.toUpperCase());
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

function sortHeader(table, key, label) {
  const current = state.sorts[table];
  const active = current?.key === key;
  const direction = active ? current.direction : "";
  const icon = direction === "asc" ? "fa-chevron-up" : "fa-chevron-down";
  return `
    <button class="sort-button${active ? " active" : ""}" type="button" data-sort-table="${table}" data-sort-key="${key}" aria-sort="${active ? direction : "none"}">
      <span>${escapeHtml(label)}</span>
      ${active ? `<i class="fa-solid ${icon}"></i>` : ""}
    </button>
  `;
}

function normalizeSortValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value;
  const numeric = Number(value);
  if (value !== "" && Number.isFinite(numeric)) return numeric;
  return String(value).toLocaleLowerCase("ru-RU");
}

function compareSortValues(a, b) {
  const left = normalizeSortValue(a);
  const right = normalizeSortValue(b);
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), "ru-RU", { numeric: true, sensitivity: "base" });
}

function entrySortValue(entry, key) {
  return {
    created_at: entry.created_at || entry.start_at || entry.id,
    date: entry.start_at || `${entry.date || ""}T00:00`,
    client: entry.client_name,
    project: entry.project_name,
    description: `${entry.description || ""} ${entry.tags || ""}`,
    time: entry.start_at,
    duration: entry.duration_seconds,
    amount: Number(entry.amount_value || 0),
  }[key];
}

function clientSortValue(client, key) {
  return {
    created_at: client.created_at || client.id,
    name: client.name,
    contact: client.contact_name,
    email: client.contact_email,
    currency: client.currency,
  }[key];
}

function projectSortValue(project, key) {
  return {
    created_at: project.created_at || project.id,
    name: project.name,
    client: project.client_name,
    rate: Number(project.hourly_rate || 0),
  }[key];
}

function dashboardActivitySortValue(activity, key) {
  return {
    client: activity.key,
    last_activity: activity.latest?.start_at || activity.latest?.date || "",
    total: activity.seconds,
  }[key];
}

function sortRows(rows, table, valueFn) {
  const sort = state.sorts[table];
  const direction = sort?.direction === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const compared = compareSortValues(valueFn(a, sort.key), valueFn(b, sort.key));
    if (compared) return compared * direction;
    return compareSortValues(a.id ?? a.key ?? "", b.id ?? b.key ?? "") * -1;
  });
}

function setTableSort(table, key) {
  const current = state.sorts[table] || {};
  state.sorts[table] = {
    key,
    direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
  };
  render();
}

function renderSortHeaders() {
  document.querySelectorAll(".sort-button").forEach((button) => {
    const sort = state.sorts[button.dataset.sortTable];
    const active = sort?.key === button.dataset.sortKey;
    button.classList.toggle("active", active);
    button.setAttribute("aria-sort", active ? sort.direction : "none");
    button.querySelector("i")?.remove();
    if (active) {
      button.insertAdjacentHTML("beforeend", `<i class="fa-solid ${sort.direction === "asc" ? "fa-chevron-up" : "fa-chevron-down"}"></i>`);
    }
  });
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
  renderSortHeaders();
}

function renderSelects() {
  $("filterFrom").value = state.filters.from || "";
  $("filterTo").value = state.filters.to || "";
  if (state.filters.from && !$("dateRangeSelect")?.open) {
    const filterStart = parseDateInput(state.filters.from);
    calendarBaseDate = new Date(filterStart.getFullYear(), filterStart.getMonth(), 1);
  }
  $("filterClient").innerHTML = optionList(state.clients, state.filters.client_id || "");
  $("filterProject").innerHTML = optionList(filteredProjects(state.filters.client_id || ""), state.filters.project_id || "");
  $("entryClient").innerHTML = optionList(state.clients, "", "Выбери клиента");
  $("entryProject").innerHTML = optionList(state.projects, "", "Выбери проект");
  $("projectClient").innerHTML = optionList(state.clients, "", "Выбери клиента");
  $("clientCurrency").innerHTML = currencyOptions();
  $("projectCurrency").innerHTML = currencyOptions();
  updateTagSuggestions($("entryTags")?.value || "");
  const selectedTags = new Set(state.filters.tags || []);
  const filterTags = [UNTAGGED_FILTER, ...state.tags];
  $("tagFilters").innerHTML = filterTags.length
    ? filterTags.map((tag) => `
      <label><input type="checkbox" value="${escapeHtml(tag)}" ${selectedTags.has(tag) ? "checked" : ""}> ${escapeHtml(tagLabel(tag))}</label>
    `).join("")
    : `<span class="muted">Тегов пока нет.</span>`;
  $("tagSummary").textContent = selectedTags.size ? [...selectedTags].map(tagLabel).join(", ") : "Все";
  updateDateRangeSummary();
  renderCalendarMonths();
}

function updateDateRangeSummary() {
  const from = $("filterFrom").value;
  const to = $("filterTo").value;
  let text = "Все";
  if (dateRangePresetLabel && from && to) text = dateRangePresetLabel;
  else if (from && to) text = `${formatDotDate(from)} — ${formatDotDate(to)}`;
  else if (from) text = formatDotDate(from);
  else if (to) text = formatDotDate(to);
  ["dateRangeText", "dashboardDateRangeText"].forEach((id) => {
    const node = $(id);
    if (node) node.textContent = text;
  });
}

function renderCalendarMonths() {
  ["calendarMonths", "dashboardCalendarMonths"].forEach((id) => {
    const container = $(id);
    if (container) container.innerHTML = [0, 1].map((offset) => calendarMonthMarkup(addMonths(calendarBaseDate, offset), offset)).join("");
  });
}

function calendarSelectionRange() {
  const fromValue = $("filterFrom").value;
  const toValue = $("filterTo").value;
  let from = parseDateInput(fromValue);
  let to = parseDateInput(toValue);
  if (from && !to && calendarHoverDate) {
    const hover = parseDateInput(calendarHoverDate);
    if (hover < from) {
      to = from;
      from = hover;
    } else {
      to = hover;
    }
  }
  return { from, to };
}

function calendarMonthMarkup(monthDate, offset) {
  const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  const first = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const days = [];
  for (let index = 0; index < 35; index += 1) {
    const day = new Date(start);
    day.setDate(start.getDate() + index);
    days.push(calendarDayMarkup(day, monthDate));
  }
  return `
    <div class="calendar-month">
      <div class="calendar-head">
        ${offset === 0 ? `<button type="button" data-calendar-shift="-1" title="Предыдущий месяц"><i class="fa-solid fa-chevron-left"></i></button>` : `<span></span>`}
        <span class="calendar-title">${monthName(monthDate)}</span>
        ${offset === 1 ? `<button type="button" data-calendar-shift="1" title="Следующий месяц"><i class="fa-solid fa-chevron-right"></i></button>` : `<span></span>`}
      </div>
      <div class="calendar-grid">
        ${weekdays.map((day) => `<span class="calendar-weekday">${day}</span>`).join("")}
        ${days.join("")}
      </div>
    </div>
  `;
}

function calendarDayMarkup(day, monthDate) {
  const { from, to } = calendarSelectionRange();
  const today = new Date();
  const inRange = from && to && day >= from && day <= to;
  const isStart = sameDate(day, from);
  const isEnd = sameDate(day, to);
  const isSingle = isStart && (!to || isEnd);
  const classes = [
    "calendar-day",
    day.getMonth() !== monthDate.getMonth() ? "outside" : "",
    inRange || isStart || isEnd ? "in-range" : "",
    isSingle ? "range-single" : "",
    isStart && !isSingle ? "range-start" : "",
    isEnd && !isSingle ? "range-end" : "",
    sameDate(day, today) ? "today" : "",
  ].filter(Boolean).join(" ");
  return `<button type="button" class="${classes}" data-filter-date="${toDateInput(day)}">${day.getDate()}</button>`;
}

function selectCalendarDate(value, detailsId = "dateRangeSelect") {
  const details = $(detailsId);
  dateRangePresetLabel = "";
  calendarHoverDate = "";
  const from = $("filterFrom").value;
  const to = $("filterTo").value;
  const hasCompleteRange = Boolean(from && to);
  const shouldStartNewRange = calendarStartNewRangeOnNextDate || hasCompleteRange;
  calendarStartNewRangeOnNextDate = false;
  if (!from || shouldStartNewRange) {
    $("filterFrom").value = value;
    $("filterTo").value = "";
    if (details) details.open = true;
  } else if (value < from) {
    $("filterFrom").value = value;
    $("filterTo").value = from;
  } else {
    $("filterTo").value = value;
    if (details) details.open = false;
  }
  state.filters.from = $("filterFrom").value;
  state.filters.to = $("filterTo").value;
  calendarBaseDate = new Date(parseDateInput($("filterFrom").value || value).getFullYear(), parseDateInput($("filterFrom").value || value).getMonth(), 1);
  updateDateRangeSummary();
  renderCalendarMonths();
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
  const tableEntries = sortRows(state.entries, "entries", entrySortValue);
  $("entriesTable").innerHTML = tableEntries.map(tableRow).join("") || `<tr><td colspan="8" class="muted">Ничего не найдено.</td></tr>`;
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
  const clients = sortRows(state.clients, "clients", clientSortValue);
  $("clientsList").innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>${sortHeader("clients", "name", "Клиент")}</th>
            <th>${sortHeader("clients", "contact", "Контакт")}</th>
            <th>${sortHeader("clients", "email", "Email")}</th>
            <th>${sortHeader("clients", "currency", "Валюта")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${clients.map((client) => `
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
  const projects = sortRows(state.projects, "projects", projectSortValue);
  $("projectsList").innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>${sortHeader("projects", "name", "Проект")}</th>
            <th>${sortHeader("projects", "client", "Клиент")}</th>
            <th>${sortHeader("projects", "rate", "Ставка")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${projects.map((project) => `
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

function groupEntrySeconds(entries, keyFn) {
  const groups = new Map();
  entries.forEach((entry) => {
    const key = keyFn(entry);
    const item = groups.get(key) || { key, seconds: 0, entries: [] };
    item.seconds += entry.duration_seconds;
    item.entries.push(entry);
    groups.set(key, item);
  });
  return [...groups.values()].sort((a, b) => b.seconds - a.seconds);
}

function dashboardPalette(index) {
  return ["#ff5722", "#5c6bc0", "#26a69a", "#ffb300", "#8e44ad", "#78909c"][index % 6];
}

function activeDashboardRange() {
  const from = parseDateInput(state.filters.from || "");
  const to = parseDateInput(state.filters.to || "");
  if (from && to) return { from, to };
  if (from) return { from, to: from };
  if (to) return { from: to, to };
  return null;
}

function dashboardDayKeys(entries) {
  const range = activeDashboardRange();
  if (range) {
    const keys = [];
    for (let day = new Date(range.from); day <= range.to; day = addDays(day, 1)) {
      keys.push(toDateInput(day));
      if (keys.length >= 31) break;
    }
    return keys;
  }
  return [...new Set(entries.map((entry) => entry.date))].sort().slice(-14);
}

function renderDashboardDayChart(entries, projectColors) {
  const dayKeys = dashboardDayKeys(entries);
  if (!dayKeys.length) {
    $("dashboardDayChart").innerHTML = `<p class="muted dashboard-empty">Нет данных за выбранный период.</p>`;
    return;
  }
  const totalsByDay = new Map(dayKeys.map((day) => [day, 0]));
  const projectsByDay = new Map(dayKeys.map((day) => [day, new Map()]));
  entries.forEach((entry) => {
    if (!projectsByDay.has(entry.date)) return;
    totalsByDay.set(entry.date, (totalsByDay.get(entry.date) || 0) + entry.duration_seconds);
    const projects = projectsByDay.get(entry.date);
    projects.set(entry.project_name, (projects.get(entry.project_name) || 0) + entry.duration_seconds);
  });
  const maxSeconds = Math.max(1, ...totalsByDay.values());
  $("dashboardDayChart").innerHTML = dayKeys.map((day) => {
    const total = totalsByDay.get(day) || 0;
    const height = Math.max(total ? 8 : 1, Math.round((total / maxSeconds) * 150));
    const stacks = [...(projectsByDay.get(day) || new Map()).entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([project, seconds]) => {
        const percent = total ? (seconds / total) * 100 : 0;
        return `<span style="height:${percent}%;background:${projectColors.get(project) || dashboardPalette(0)}" title="${escapeHtml(project)} · ${durationFromSeconds(seconds)}"></span>`;
      }).join("");
    return `
      <div class="day-bar">
        <strong>${durationFromSeconds(total)}</strong>
        <div class="day-bar-track" style="height:${height}px">${stacks || `<span class="empty-stack"></span>`}</div>
        <small>${escapeHtml(formatShortDate(day))}</small>
      </div>
    `;
  }).join("");
}

function renderDashboardBreakdown(projectGroups, totalSeconds, projectColors) {
  if (!totalSeconds) {
    $("dashboardDonut").style.background = "#edf3f7";
    $("dashboardDonut").innerHTML = `<span>00:00:00</span>`;
    $("dashboardBreakdown").innerHTML = `<p class="muted">Проектов за период нет.</p>`;
    return;
  }
  let cursor = 0;
  const segments = projectGroups.map((group) => {
    const start = cursor;
    cursor += (group.seconds / totalSeconds) * 100;
    return `${projectColors.get(group.key)} ${start}% ${cursor}%`;
  });
  $("dashboardDonut").style.background = `conic-gradient(${segments.join(", ")})`;
  $("dashboardDonut").innerHTML = `<span>${durationFromSeconds(totalSeconds)}</span>`;
  $("dashboardBreakdown").innerHTML = projectGroups.slice(0, 6).map((group) => {
    const sample = group.entries[0];
    const percent = totalSeconds ? (group.seconds / totalSeconds) * 100 : 0;
    return `
      <div class="breakdown-row">
        <div>
          <strong>${escapeHtml(group.key)}</strong>
          <span>${escapeHtml(sample.client_name)}</span>
        </div>
        <code>${durationFromSeconds(group.seconds)}</code>
        <div class="breakdown-track"><span style="width:${percent}%;background:${projectColors.get(group.key)}"></span></div>
        <em>${percent.toFixed(1).replace(".", ",")}%</em>
      </div>
    `;
  }).join("");
}

function renderDashboardActivities(entries, totalSeconds) {
  const clients = sortRows(
    groupEntrySeconds(entries, (entry) => entry.client_name).map((client) => ({
      ...client,
      latest: client.entries.slice().sort((a, b) => String(b.start_at).localeCompare(String(a.start_at)))[0],
    })),
    "dashboardActivities",
    dashboardActivitySortValue,
  );
  const maxSeconds = Math.max(1, ...clients.map((client) => client.seconds));
  $("dashboardActivities").innerHTML = clients.map((client) => {
    const latest = client.latest;
    const width = Math.round((client.seconds / maxSeconds) * 100);
    const percent = totalSeconds ? (client.seconds / totalSeconds) * 100 : 0;
    return `
      <tr>
        <td>
          <div class="activity-client">
            <span>${escapeHtml(client.key.slice(0, 2).toUpperCase())}</span>
            <strong>${escapeHtml(client.key)}</strong>
          </div>
        </td>
        <td>
          <strong>${escapeHtml(latest.description || latest.project_name)}</strong><br>
          <small class="muted">${escapeHtml(latest.project_name)} · ${escapeHtml(latest.date)} · ${latest.duration}</small>
        </td>
        <td>
          <div class="activity-total">
            <code>${durationFromSeconds(client.seconds)}</code>
            <div class="breakdown-track"><span style="width:${width}%"></span></div>
            <em>${percent.toFixed(1).replace(".", ",")}%</em>
          </div>
        </td>
      </tr>
    `;
  }).join("") || `<tr><td colspan="3" class="muted">Активностей пока нет.</td></tr>`;
}

function renderDashboard() {
  const entries = state.entries;
  const totalSeconds = entries.reduce((sum, entry) => sum + entry.duration_seconds, 0);
  const projectGroups = groupEntrySeconds(entries, (entry) => entry.project_name);
  const clientGroups = groupEntrySeconds(entries, (entry) => entry.client_name);
  const projectColors = new Map(projectGroups.map((group, index) => [group.key, dashboardPalette(index)]));
  $("dashClients").textContent = state.clients.length;
  $("dashProjects").textContent = state.projects.length;
  $("dashEntries").textContent = entries.length;
  $("dashTotalTime").textContent = durationFromSeconds(totalSeconds);
  $("dashTopProject").textContent = projectGroups[0]?.key || "—";
  $("dashTopClient").textContent = clientGroups[0]?.key || "—";
  $("dashboardActivityCount").textContent = `${entries.length} ${entries.length === 1 ? "запись" : "записей"}`;
  renderDashboardDayChart(entries, projectColors);
  renderDashboardBreakdown(projectGroups, totalSeconds, projectColors);
  renderDashboardActivities(entries, totalSeconds);
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
  if (state.completingTimer && state.running && payload.start_at === state.running.start_at) {
    payload.end_at = ensureEndAfterStart(payload.start_at, payload.end_at);
    $("entryEnd").value = payload.end_at;
  }
  if (state.running && !id && payload.start_at === state.running.start_at && !state.completingTimer) {
    const result = await api("/api/timer", { method: "PUT", body: JSON.stringify(payload) });
    state.running = result.running;
    $("entryDialog").close();
    renderTimer();
    return;
  }
  try {
    await api(id ? `/api/entries/${id}` : "/api/entries", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
  } catch (error) {
    alert(error.message);
    return;
  }
  if (state.completingTimer && state.running && payload.start_at === state.running.start_at) {
    state.running = null;
    state.completingTimer = false;
    await api("/api/timer", { method: "DELETE" }).catch(() => {});
  }
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

async function startTimer(defaults = {}) {
  if (!state.clients.length || !state.projects.length) {
    alert("Сначала добавь клиента и проект. Без них трекер будет считать воздух, а воздух плохо оплачивается.");
    return;
  }
  const payload = {
    client_id: defaults.client_id || state.clients[0]?.id || "",
    project_id: defaults.project_id || state.projects.find((p) => String(p.client_id) === String(defaults.client_id))?.id || state.projects[0]?.id || "",
    description: defaults.description || "",
    tags: defaults.tags || "",
    start_at: toLocalInput(new Date()),
  };
  const result = await api("/api/timer/start", { method: "POST", body: JSON.stringify(payload) });
  state.running = result.running;
  renderTimer();
  openEntryDialog(null, { ...state.running, end_at: toLocalInput(new Date(Date.now() + 60000)) });
}

async function stopTimer() {
  const running = state.running;
  if (!running) return;
  const end = ensureEndAfterStart(running.start_at);
  const payload = { ...running, end_at: end };
  try {
    await api("/api/timer", { method: "PUT", body: JSON.stringify(running) });
    await api("/api/timer/stop", { method: "POST", body: JSON.stringify({ end_at: end }) });
    state.running = null;
    await loadState();
  } catch (error) {
    state.running = running;
    state.completingTimer = true;
    renderTimer();
    openEntryDialog(null, payload);
    alert(`${error.message}. Я вернул таймер, заполни детали и сохрани запись.`);
  }
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

async function applyReportFiltersWhenReady() {
  if (!$("filterFrom").value || !$("filterTo").value) return;
  await applyFilters();
}

async function resetFilters() {
  state.filters = {};
  calendarStartNewRangeOnNextDate = false;
  dateRangePresetLabel = "";
  $("filterFrom").value = "";
  $("filterTo").value = "";
  $("filterClient").value = "";
  $("filterProject").value = "";
  document.querySelectorAll("#tagFilters input").forEach((input) => input.checked = false);
  $("tagSummary").textContent = "Все";
  updateDateRangeSummary();
  renderCalendarMonths();
  writeFiltersToUrl(false);
  await loadState();
}

function setPeriod(kind) {
  const now = new Date();
  let start;
  let end;
  if (kind === "today") {
    start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    end = start;
    dateRangePresetLabel = "Сегодня";
  } else if (kind === "month") {
    start = new Date(now.getFullYear(), now.getMonth(), 1);
    end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    dateRangePresetLabel = "Этот месяц";
  } else if (kind === "prev") {
    start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    end = new Date(now.getFullYear(), now.getMonth(), 0);
    dateRangePresetLabel = "Прошлый месяц";
  } else if (kind === "two-weeks") {
    start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 13);
    end = now;
    dateRangePresetLabel = "Последние две недели";
  } else {
    start = new Date(now.getFullYear(), 0, 1);
    end = now;
    dateRangePresetLabel = "С начала года";
  }
  $("filterFrom").value = toDateInput(start);
  $("filterTo").value = toDateInput(end);
  calendarStartNewRangeOnNextDate = false;
  state.filters.from = $("filterFrom").value;
  state.filters.to = $("filterTo").value;
  calendarBaseDate = new Date(start.getFullYear(), start.getMonth(), 1);
  updateDateRangeSummary();
  renderCalendarMonths();
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, label, a");
  document.querySelectorAll(".date-range-select[open], .tag-select[open]").forEach((details) => {
    if (!details.contains(event.target)) details.open = false;
  });
  if (!target) return;
  if (target.dataset.sortTable && target.dataset.sortKey) {
    setTableSort(target.dataset.sortTable, target.dataset.sortKey);
    return;
  }
  if (target.classList.contains("menu-toggle")) {
    const sidebar = document.querySelector(".sidebar");
    const isOpen = sidebar.classList.toggle("menu-open");
    target.setAttribute("aria-expanded", String(isOpen));
    target.setAttribute("aria-label", isOpen ? "Закрыть меню" : "Открыть меню");
    target.innerHTML = `<i class="fa-solid ${isOpen ? "fa-xmark" : "fa-bars"}"></i>`;
    return;
  }
  if (target.dataset.view) {
    setView(target.dataset.view);
    const sidebar = document.querySelector(".sidebar");
    const menuToggle = document.querySelector(".menu-toggle");
    if (sidebar?.classList.contains("menu-open")) {
      sidebar.classList.remove("menu-open");
      menuToggle?.setAttribute("aria-expanded", "false");
      menuToggle?.setAttribute("aria-label", "Открыть меню");
      if (menuToggle) menuToggle.innerHTML = `<i class="fa-solid fa-bars"></i>`;
    }
  }
  if (target.dataset.viewJump) setView(target.dataset.viewJump);
  if (target.id === "openEntry") openEntryDialog();
  if (target.id === "addClient") openClientDialog();
  if (target.id === "addProject") openProjectDialog();
  if (target.id === "periodToday") {
    setPeriod("today");
    await applyFilters();
  }
  if (target.id === "periodMonth") {
    setPeriod("month");
    await applyFilters();
  }
  if (target.id === "periodPrevMonth") {
    setPeriod("prev");
    await applyFilters();
  }
  if (target.id === "periodTwoWeeks") {
    setPeriod("two-weeks");
    await applyFilters();
  }
  if (target.id === "periodYear") {
    setPeriod("year");
    await applyFilters();
  }
  if (target.id === "dashboardPeriodToday") {
    setPeriod("today");
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  }
  if (target.id === "dashboardPeriodMonth") {
    setPeriod("month");
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  }
  if (target.id === "dashboardPeriodPrevMonth") {
    setPeriod("prev");
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  }
  if (target.id === "dashboardPeriodTwoWeeks") {
    setPeriod("two-weeks");
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  }
  if (target.id === "dashboardPeriodYear") {
    setPeriod("year");
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  }
  if (target.dataset.calendarShift) {
    calendarBaseDate = addMonths(calendarBaseDate, Number(target.dataset.calendarShift));
    renderCalendarMonths();
  }
  if (target.id === "resetFilters") {
    await resetFilters();
  }
  if (target.dataset.editEntry) openEntryDialog(state.entries.find((entry) => String(entry.id) === target.dataset.editEntry));
  if (target.dataset.editClient) openClientDialog(state.clients.find((client) => String(client.id) === target.dataset.editClient));
  if (target.dataset.editProject) openProjectDialog(state.projects.find((project) => String(project.id) === target.dataset.editProject));
  if (target.dataset.repeat) {
    const entry = state.entries.find((item) => String(item.id) === target.dataset.repeat);
    await startTimer(entry);
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
  else await startTimer();
});

$("entryClient").addEventListener("change", () => updateEntryProjects());
$("entryTags").addEventListener("input", (event) => updateTagSuggestions(event.target.value));
$("filterClient").addEventListener("change", async () => {
  $("filterProject").innerHTML = optionList(filteredProjects($("filterClient").value));
  $("filterProject").value = "";
  await applyFilters();
});
$("filterProject").addEventListener("change", async () => {
  await applyFilters();
});
$("tagFilters").addEventListener("change", async () => {
  const selected = [...document.querySelectorAll("#tagFilters input:checked")].map((input) => input.value);
  $("tagSummary").textContent = selected.length ? selected.map(tagLabel).join(", ") : "Все";
  await applyFilters();
});
$("dateRangeSelect").addEventListener("toggle", () => {
  if (!$("dateRangeSelect").open) return;
  calendarStartNewRangeOnNextDate = Boolean($("filterFrom").value && $("filterTo").value);
});
if ($("dashboardDateRangeSelect")) {
  $("dashboardDateRangeSelect").addEventListener("toggle", () => {
    if (!$("dashboardDateRangeSelect").open) return;
    calendarStartNewRangeOnNextDate = Boolean($("filterFrom").value && $("filterTo").value);
  });
}

function setupDateRangeCalendar(containerId, detailsId, autoApply = false) {
  const container = $(containerId);
  if (!container) return;
  const applyDashboardDate = async () => {
    if (!autoApply || !$("filterFrom").value || !$("filterTo").value) return;
    state.view = "dashboard";
    writeFiltersToUrl(false);
    await loadState();
  };
  container.addEventListener("mouseover", (event) => {
    const day = event.target.closest("[data-filter-date]");
    if (!day || !$("filterFrom").value || $("filterTo").value) return;
    if (calendarHoverDate === day.dataset.filterDate) return;
    calendarHoverDate = day.dataset.filterDate;
    renderCalendarMonths();
  });
  container.addEventListener("pointerdown", async (event) => {
    const day = event.target.closest("[data-filter-date]");
    if (!day) return;
    event.preventDefault();
    event.stopPropagation();
    calendarSuppressClick = true;
    selectCalendarDate(day.dataset.filterDate, detailsId);
    await applyDashboardDate();
    if (!autoApply) await applyReportFiltersWhenReady();
  });
  container.addEventListener("click", async (event) => {
    const day = event.target.closest("[data-filter-date]");
    if (!day) return;
    event.preventDefault();
    event.stopPropagation();
    if (calendarSuppressClick) {
      calendarSuppressClick = false;
      return;
    }
    selectCalendarDate(day.dataset.filterDate, detailsId);
    await applyDashboardDate();
    if (!autoApply) await applyReportFiltersWhenReady();
  });
  container.addEventListener("mouseleave", () => {
    if (!calendarHoverDate) return;
    calendarHoverDate = "";
    renderCalendarMonths();
  });
}

setupDateRangeCalendar("calendarMonths", "dateRangeSelect");
setupDateRangeCalendar("dashboardCalendarMonths", "dashboardDateRangeSelect", true);

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
setInterval(() => {
  if (!document.querySelector("dialog[open]")) loadState().catch(() => {});
}, 5000);
localStorage.removeItem("runningTimer");
readFiltersFromUrl();
loadState().catch((error) => alert(error.message));
