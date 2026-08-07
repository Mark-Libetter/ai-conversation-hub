// 全局错误捕获：任何未捕获的错误显示在页面顶部
window.addEventListener("error", (e) => {
  const d = document.getElementById("globalErr") || (() => {
    const el = document.createElement("div");
    el.id = "globalErr";
    el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#a45045;color:#fff;padding:8px 16px;font-size:12px;font-family:sans-serif;white-space:pre-wrap";
    document.body ? document.body.prepend(el) : document.documentElement.prepend(el);
    return el;
  })();
  d.textContent += (d.textContent ? "\n" : "") + (e.message || "未知错误") + (e.filename ? " @ " + e.filename.split("/").pop() + ":" + e.lineno : "");
});
window.addEventListener("unhandledrejection", (e) => {
  const d = document.getElementById("globalErr") || (() => {
    const el = document.createElement("div");
    el.id = "globalErr";
    el.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:99999;background:#a45045;color:#fff;padding:8px 16px;font-size:12px;font-family:sans-serif;white-space:pre-wrap";
    document.body ? document.body.prepend(el) : document.documentElement.prepend(el);
    return el;
  })();
  d.textContent += (d.textContent ? "\n" : "") + "Promise: " + (e.reason?.message || e.reason || "未知");
});

const SAVED_VIEWS_KEY = "conversation-hub-v6-saved-views";
const DETAIL_WIDTH_KEY = "conversation-hub-detail-width";
const SOURCE_DETAILS_KEY = "conversation-hub-source-details-open";
const SOURCE_ORDER_KEY = "conversation-hub-source-order";
const SIDEBAR_COLLAPSED_KEY = "conversation-hub-sidebar-collapsed";
const THEME_KEY = "ai-hub-theme";
const THEMES = {
  "dream-glass": { name: "梦境流光", mode: "dark" },
  "violet-night": { name: "紫夜星云", mode: "dark" },
  "warm-paper": { name: "温暖纸张", mode: "light" },
  terminal: { name: "终端黑客", mode: "dark" },
  archive: { name: "档案纸张", mode: "light" },
};
const SOURCE_LABELS = {
  hermes: "Hermes",
  codex: "Codex",
  workbuddy: "WorkBuddy",
  claude: "Claude Code",
  qoderwork: "QoderWork",
  zcode: "ZCode",
};
const EXTRA_SOURCES = ["claude", "qoderwork", "zcode"];
const VALID_SOURCES = new Set(["all", ...Object.keys(SOURCE_LABELS)]);
const VALID_RANGES = new Set(["all", "today", "3d", "7d", "30d"]);
const VALID_STATUSES = new Set(["all", "todo", "done", "reference", "archive_candidate"]);
const VALID_VIEWS = new Set(["find", "daily", "projects", "assets", "settings"]);
const customSourceIds = new Set();

function registerCustomSources(sources = {}) {
  const currentIds = new Set(
    Object.entries(sources).filter(([, item]) => item.custom).map(([source]) => source)
  );
  customSourceIds.forEach((source) => {
    if (currentIds.has(source)) return;
    customSourceIds.delete(source);
    VALID_SOURCES.delete(source);
    delete SOURCE_LABELS[source];
    delete state.filters[source];
  });
  currentIds.forEach((source) => {
    const item = sources[source];
    customSourceIds.add(source);
    VALID_SOURCES.add(source);
    SOURCE_LABELS[source] = item.label || "自定义 Agent";
    state.filters[source] ||= defaultFilters();
  });
  $("#customSourceRows").innerHTML = [...currentIds].map((source) => {
    const item = sources[source];
    return `<button class="source-row" data-source="${escapeHtml(source)}" type="button">
      <span class="source-dot ${escapeHtml(source)}"></span>
      <span>${escapeHtml(item.label || source)}</span>
      <b id="${escapeHtml(source)}Count">${item.conversations || 0}</b>
    </button>`;
  }).join("");
  const search = $("#searchAgentFilter");
  search.querySelectorAll("[data-custom-source-option]").forEach((node) => node.remove());
  [...currentIds].forEach((source) => {
    const option = document.createElement("option");
    option.value = source;
    option.dataset.customSourceOption = "1";
    option.textContent = SOURCE_LABELS[source];
    search.append(option);
  });
  if (!VALID_SOURCES.has(state.source)) {
    state.source = "all";
    Object.assign(state, state.filters.all);
  }
}

function syncSourceControls(sources = {}) {
  state.enabledSources = new Set(
    Object.entries(sources)
      .filter(([, item]) => item.enabled !== false)
      .map(([source]) => source)
  );
  document.querySelectorAll("#agentSwitcher .source-row[data-source]").forEach((row) => {
    const source = row.dataset.source;
    if (source === "all") return;
    // 后端没有的源：隐藏按钮（动态跟随后端实际启用的源）
    if (!(source in sources)) {
      row.style.display = "none";
      return;
    }
    row.style.display = "";
    let checkbox = row.querySelector("[data-source-enabled]");
    if (!checkbox) {
      checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.sourceEnabled = source;
      checkbox.setAttribute("aria-label", `启用 ${SOURCE_LABELS[source] || source}`);
      row.prepend(checkbox);
    }
    const enabled = sources[source]?.enabled !== false;
    checkbox.checked = enabled;
    checkbox.disabled = false;
    row.classList.toggle("source-disabled", !enabled);
  });
  document.querySelectorAll("#searchAgentFilter option").forEach((option) => {
    if (option.value === "all") return;
    // 后端没有的源：隐藏筛选项
    if (!(option.value in sources)) { option.style.display = "none"; option.disabled = true; }
    else { option.style.display = ""; option.disabled = !state.enabledSources.has(option.value); }
  });
  if (state.source !== "all" && !state.enabledSources.has(state.source)) {
    state.source = "all";
    Object.assign(state, state.filters.all);
  }
}

function conversationSourceLabel(item) {
  const base = SOURCE_LABELS[item.source] || item.source;
  if (item.source === "workbuddy" && item.source_kind === "assistant") {
    return `${base} · 助理`;
  }
  if (item.source === "claude" && item.source_kind?.includes("metadata-only")) {
    return `${base} · 历史索引`;
  }
  return base;
}

function conversationKindLabel(item) {
  if (item.source === "workbuddy" && item.source_kind === "assistant") return "助理 / Claw";
  if (item.source === "claude" && item.source_kind?.includes("metadata-only")) return "正文不完整";
  if (item.source === "claude" && item.source_kind?.includes("partial")) return "部分正文";
  return "";
}

function localDateIso(value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const get = (type) => parts.find((part) => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function shiftDate(day, amount) {
  const [year, month, value] = day.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, value + amount));
  return shifted.toISOString().slice(0, 10);
}

const defaultFilters = () => ({
  range: "all",
  status: "all",
  workspace: "all",
  nativeProject: "all",
  favorites: false,
  query: "",
});

const state = {
  view: "find",
  source: "all",
  ...defaultFilters(),
  offset: 0,
  limit: 120,
  total: 0,
  selected: null,
  token: "",
  items: [],
  queryTerms: [],
  summary: null,
  enabledSources: new Set(),
  checked: new Map(),
  exportResult: null,
  backupImport: null,
  updateCandidate: null,
  dailyDate: localDateIso(),
  daily: null,
  dailyReportOpen: false,
  projects: [],
  openProjectId: null,
  projectForm: { mode: "create", id: null, addAfter: false },
  filters: {
    all: defaultFilters(),
    hermes: defaultFilters(),
    codex: defaultFilters(),
    workbuddy: defaultFilters(),
    claude: defaultFilters(),
    qoderwork: defaultFilters(),
    zcode: defaultFilters(),
  },
};

const $ = (selector) => document.querySelector(selector);
const list = $("#conversationList");
const detailPane = $("#detailPane");
let searchTimer = null;
let toastTimer = null;

function currentTheme() {
  const value = document.documentElement.dataset.theme || "archive";
  return THEMES[value] ? value : "archive";
}

function applyTheme(themeId, { persist = true } = {}) {
  const selected = THEMES[themeId] ? themeId : "archive";
  document.documentElement.dataset.theme = selected;
  document.documentElement.style.colorScheme = THEMES[selected].mode;
  if (persist) {
    try { localStorage.setItem(THEME_KEY, selected); } catch {}
  }
  const trigger = $("#themeButton");
  if (trigger) {
    trigger.title = `当前皮肤：${THEMES[selected].name}`;
    trigger.setAttribute("aria-label", `切换皮肤，当前为${THEMES[selected].name}`);
  }
  document.querySelectorAll("[data-theme-id]").forEach((button) => {
    const active = button.dataset.themeId === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const stateRoot = $("#themeSelectionState");
  if (stateRoot) stateRoot.textContent = `当前：${THEMES[selected].name} · 已保存在本机`;
}

function setSidebarCollapsed(collapsed, { persist = true } = {}) {
  const value = Boolean(collapsed);
  document.body.classList.toggle("sidebar-collapsed", value);
  const button = $("#sidebarCollapseButton");
  if (button) {
    button.setAttribute("aria-expanded", String(!value));
    button.setAttribute("aria-label", value ? "展开侧边栏" : "收起侧边栏");
    button.title = value ? "展开侧边栏" : "收起侧边栏";
    button.textContent = value ? "›" : "‹";
  }
  if (persist) {
    try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, value ? "1" : "0"); } catch {}
  }
}

function initSidebarCollapse() {
  let saved = false;
  try { saved = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1"; } catch {}
  if (window.matchMedia("(max-width: 840px)").matches) saved = false;
  setSidebarCollapsed(saved, { persist: false });
  $("#sidebarCollapseButton")?.addEventListener("click", () => {
    setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
  });
  window.addEventListener("resize", () => {
    if (window.matchMedia("(max-width: 840px)").matches) {
      setSidebarCollapsed(false, { persist: false });
    }
  });
}

function openThemeDialog() {
  applyTheme(currentTheme(), { persist: false });
  $("#themeDialog").showModal();
}

function detailWidthBounds() {
  const layout = $(".find-layout");
  const max = Math.max(300, Math.min(720, (layout?.clientWidth || window.innerWidth) - 420));
  return { min: 300, max };
}

function setDetailWidth(value, { persist = false } = {}) {
  const bounds = detailWidthBounds();
  const width = Math.round(Math.max(bounds.min, Math.min(bounds.max, Number(value) || 400)));
  document.documentElement.style.setProperty("--detail", `${width}px`);
  $("#detailResizer").setAttribute("aria-valuenow", String(width));
  $("#detailResizer").setAttribute("aria-valuemax", String(bounds.max));
  if (persist) {
    try {
      localStorage.setItem(DETAIL_WIDTH_KEY, String(width));
    } catch {
      // Browser storage is optional; resizing still works for this session.
    }
  }
  return width;
}

function setDetailOpen(open, { focusToggle = false } = {}) {
  const layout = $(".find-layout");
  const toggle = $("#detailToggleButton");
  const expanded = Boolean(open);
  layout.classList.toggle("detail-open", expanded);
  detailPane.hidden = !expanded;
  detailPane.setAttribute("aria-hidden", String(!expanded));
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.textContent = expanded ? "收起对话内容" : "打开对话内容";
  if (focusToggle) toggle.focus({ preventScroll: true });
}

async function toggleDetailDrawer() {
  const isOpen = $(".find-layout").classList.contains("detail-open");
  if (isOpen) {
    setDetailOpen(false);
    return;
  }
  if (state.selected && (
    detailPane.dataset.source !== state.selected.source
    || detailPane.dataset.conversationId !== state.selected.id
  )) {
    await openDetail(state.selected.source, state.selected.id);
    return;
  }
  setDetailOpen(true);
}

function initDetailResizer() {
  const handle = $("#detailResizer");
  let saved = 400;
  let startX = 0;
  let startWidth = 400;
  let previewWidth = 400;
  let dragBounds = { min: 300, max: 720 };
  try {
    saved = Number(localStorage.getItem(DETAIL_WIDTH_KEY)) || 400;
  } catch {
    saved = 400;
  }
  setDetailWidth(saved);
  handle.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(max-width: 840px)").matches) return;
    startX = event.clientX;
    startWidth = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--detail")) || 400;
    previewWidth = startWidth;
    dragBounds = detailWidthBounds();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-detail");
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    const requested = startWidth - (event.clientX - startX);
    previewWidth = Math.round(
      Math.max(dragBounds.min, Math.min(dragBounds.max, requested)) / 4
    ) * 4;
    handle.style.transform = `translate3d(${startWidth - previewWidth}px,0,0)`;
    handle.setAttribute("aria-valuenow", String(previewWidth));
  });
  const finish = (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    handle.releasePointerCapture(event.pointerId);
    handle.style.transform = "";
    document.body.classList.remove("resizing-detail");
    setDetailWidth(previewWidth, { persist: true });
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("dblclick", () => setDetailWidth(400, { persist: true }));
  handle.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--detail")) || 400;
    const next = event.key === "Home" ? 400 : current + (event.key === "ArrowLeft" ? 24 : -24);
    setDetailWidth(next, { persist: true });
  });
  window.addEventListener("resize", () => {
    if (!window.matchMedia("(max-width: 840px)").matches) {
      const current = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--detail")) || 400;
      setDetailWidth(current);
    }
  });
}

function initSourceDetails() {
  const details = $("#sourceDetails");
  try {
    const saved = localStorage.getItem(SOURCE_DETAILS_KEY);
    if (saved !== null) details.open = saved === "1";
  } catch {
    // Browser storage is optional; the section remains open by default.
  }
  details.addEventListener("toggle", () => {
    try {
      localStorage.setItem(SOURCE_DETAILS_KEY, details.open ? "1" : "0");
    } catch {
      // Keep the interaction available even when storage is disabled.
    }
  });
}

function builtinSourceRows() {
  return [...document.querySelectorAll("#agentSwitcher .source-row[data-source]")]
    .filter((row) => row.dataset.source !== "all");
}

function applySourceOrder(order) {
  if (!Array.isArray(order) || !order.length) return;
  const switcher = $("#agentSwitcher");
  const custom = $("#customSourceRows");
  const rows = builtinSourceRows();
  const rowById = Object.fromEntries(rows.map((row) => [row.dataset.source, row]));
  for (const id of order) if (rowById[id]) switcher.insertBefore(rowById[id], custom);
  for (const row of rows) if (!order.includes(row.dataset.source)) switcher.insertBefore(row, custom);

  const select = $("#searchAgentFilter");
  if (!select) return;
  const options = [...select.options].filter((opt) => opt.value !== "all");
  const optById = Object.fromEntries(options.map((opt) => [opt.value, opt]));
  select.innerHTML = "";
  const allOpt = document.createElement("option");
  allOpt.value = "all";
  allOpt.textContent = "全部 Agent";
  select.appendChild(allOpt);
  for (const id of order) if (optById[id]) select.appendChild(optById[id]);
  for (const opt of options) if (!order.includes(opt.value)) select.appendChild(opt);
}

function persistSourceOrder() {
  try {
    localStorage.setItem(SOURCE_ORDER_KEY, JSON.stringify(builtinSourceRows().map((r) => r.dataset.source)));
  } catch {
    // Ordering is a convenience; ignore storage failures.
  }
}

function initSourceDrag() {
  const switcher = $("#agentSwitcher");
  let dragged = null;
  for (const row of builtinSourceRows()) {
    row.draggable = true;
    row.title = "拖动可调整来源排序";
  }
  switcher.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".source-row[data-source]");
    if (!row || row.dataset.source === "all") return;
    dragged = row;
    row.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", row.dataset.source); } catch {}
  });
  switcher.addEventListener("dragover", (event) => {
    if (!dragged) return;
    const row = event.target.closest(".source-row[data-source]:not([data-source='all'])");
    if (!row || row === dragged) return;
    event.preventDefault();
    const rect = row.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    switcher.insertBefore(dragged, after ? row.nextSibling : row);
  });
  const settle = () => {
    if (dragged) { dragged.classList.remove("dragging"); persistSourceOrder(); }
    dragged = null;
  };
  switcher.addEventListener("drop", (event) => { if (dragged) { event.preventDefault(); settle(); } });
  switcher.addEventListener("dragend", settle);
  applySourceOrder(JSON.parse(localStorage.getItem(SOURCE_ORDER_KEY) || "null") || []);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function highlightHtml(value, query) {
  const raw = String(value ?? "");
  const needles = (Array.isArray(query) ? query : [query])
    .map((item) => String(item ?? "").trim())
    .filter(Boolean)
    .sort((left, right) => right.length - left.length);
  if (!needles.length) return escapeHtml(raw);
  const pattern = needles.map(escapeRegExp).join("|");
  const marked = raw.replace(new RegExp(pattern, "gi"), (match) => `\u0000${match}\u0001`);
  return escapeHtml(marked).replaceAll("\u0000", "<mark>").replaceAll("\u0001", "</mark>");
}

function dateTime(value) {
  if (!value) return "未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function dayLabel(day) {
  const today = localDateIso();
  if (day === today) return "今天";
  if (day === shiftDate(today, -1)) return "昨天";
  const [year, month, value] = day.split("-").map(Number);
  return `${month}月${value}日${year !== new Date().getFullYear() ? ` · ${year}` : ""}`;
}

function relativeTime(value) {
  const seconds = Math.max(0, Date.now() / 1000 - value);
  if (seconds < 3600) return `${Math.max(1, Math.floor(seconds / 60))} 分钟`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`;
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)} 天`;
  return dateTime(value);
}

function statusLabel(value) {
  return {
    active: "活跃",
    week: "近七天",
    recent: "近期",
    archive: "可归档",
    history: "历史",
    todo: "待继续",
    done: "已完成",
    reference: "重要参考",
    archive_candidate: "归档候选",
  }[value] || value || "";
}

function rangeLabel(value) {
  return {
    today: "今天",
    "3d": "近 3 天",
    "7d": "近 7 天",
    "30d": "近 30 天",
    all: "全部",
  }[value] || value;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json", "X-Hub-Token": state.token } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 ${response.status}`);
  return data;
}

function downloadText(filename, content, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sourceValues(group, fallback) {
  if (state.source === "all") return group;
  return fallback[state.source];
}

async function loadSummary() {
  const data = await api("/api/summary");
  state.summary = data;
  $("#allCount").textContent = data.total;
  Object.keys(SOURCE_LABELS).forEach((source) => {
    const node = $(`#${source}Count`);
    if (node) node.textContent = data.by_source[source] || 0;
  });
  renderWorkspaceSummary();
}

function renderWorkspaceSummary() {
  const data = state.summary;
  if (!data) return;
  const sourceTotal = state.source === "all" ? data.total : data.by_source[state.source];
  const ranges = sourceValues(data.by_range, data.by_source_range);
  const favoriteTotal = state.source === "all" ? data.favorites : data.favorites_by_source[state.source];
  const stats = [
    {
      label: state.source === "all"
        ? "全部对话"
        : `${SOURCE_LABELS[state.source] || state.source} 对话`,
      value: sourceTotal,
      range: "all",
    },
    { label: "今天", value: ranges.today, range: "today" },
    { label: "近 3 天", value: ranges["3d"], range: "3d" },
    { label: "近 7 天", value: ranges["7d"], range: "7d" },
    { label: "近 30 天", value: ranges["30d"], range: "30d" },
    { label: "收藏", value: favoriteTotal, favorite: true },
  ];
  $("#summary").innerHTML = stats.map((stat) => {
    const active = stat.favorite ? state.favorites : (!state.favorites && state.range === stat.range);
    const action = stat.favorite ? `data-favorite="1"` : `data-range="${stat.range}"`;
    return `<button class="stat${active ? " active" : ""}" ${action} type="button">
      <strong>${stat.value}</strong><span>${stat.label}</span>
    </button>`;
  }).join("");

  $("#refreshedAt").textContent = `更新于 ${dateTime(data.refreshed_at)}`;
  const select = $("#workspaceFilter");
  const workspaceRows = state.source === "all" ? data.workspaces : data.workspaces_by_source[state.source];
  select.innerHTML = `<option value="all">全部工作区</option>` +
    workspaceRows.map(([name, count]) =>
      `<option value="${escapeHtml(name)}">${escapeHtml(name)} · ${count}</option>`
    ).join("");
  select.value = [...select.options].some((option) => option.value === state.workspace)
    ? state.workspace
    : "all";
  if (select.value !== state.workspace) {
    state.workspace = "all";
    state.filters[state.source].workspace = "all";
  }
  const nativeSelect = $("#nativeProjectFilter");
  const nativeRows = state.source === "all"
    ? (data.native_projects || [])
    : (data.native_projects_by_source?.[state.source] || []);
  nativeSelect.innerHTML = `<option value="all">全部原生项目</option>` +
    nativeRows.map(([name, count]) =>
      `<option value="${escapeHtml(name)}">${escapeHtml(name)} · ${count}</option>`
    ).join("");
  nativeSelect.value = [...nativeSelect.options].some(
    (option) => option.value === state.nativeProject
  ) ? state.nativeProject : "all";
  if (nativeSelect.value !== state.nativeProject) {
    state.nativeProject = "all";
    state.filters[state.source].nativeProject = "all";
  }

  document.querySelectorAll("#quickRanges [data-range]").forEach((button) => {
    const value = button.dataset.range;
    const count = value === "all" ? sourceTotal : ranges[value];
    const active = state.range === value;
    button.innerHTML = `<span>${escapeHtml(rangeLabel(value))}</span><b>${count}</b>`;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function queryString() {
  const params = new URLSearchParams({
    source: state.source,
    range: state.range,
    status: state.status,
    workspace: state.workspace,
    native_project: state.nativeProject,
    favorites: state.favorites ? "1" : "0",
    q: state.query,
    offset: String(state.offset),
    limit: String(state.limit),
  });
  return params.toString();
}

function syncUrl() {
  const params = new URLSearchParams();
  if (state.view !== "find") params.set("view", state.view);
  if (state.source !== "all") params.set("source", state.source);
  if (state.range !== "all") params.set("range", state.range);
  if (state.status !== "all") params.set("status", state.status);
  if (state.workspace !== "all") params.set("workspace", state.workspace);
  if (state.nativeProject !== "all") params.set("nativeProject", state.nativeProject);
  if (state.favorites) params.set("favorites", "1");
  if (state.query) params.set("q", state.query);
  if (state.dailyDate !== localDateIso()) params.set("reviewDate", state.dailyDate);
  if (state.selectedProjectId) params.set("project", state.selectedProjectId);
  if (state.selected) {
    params.set("conversationSource", state.selected.source);
    params.set("conversation", state.selected.id);
  }
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
}

function readUrlState() {
  const params = new URLSearchParams(location.search);
  const view = params.get("view");
  const source = params.get("source");
  const range = params.get("range");
  const status = params.get("status");
  if (VALID_VIEWS.has(view)) state.view = view;
  if (VALID_SOURCES.has(source)) state.source = source;
  if (VALID_RANGES.has(range)) state.range = range;
  if (VALID_STATUSES.has(status)) state.status = status;
  state.workspace = params.get("workspace") || "all";
  state.nativeProject = params.get("nativeProject") || "all";
  state.favorites = params.get("favorites") === "1";
  state.query = (params.get("q") || "").trim();
  const reviewDate = params.get("reviewDate");
  if (/^\d{4}-\d{2}-\d{2}$/.test(reviewDate || "")) state.dailyDate = reviewDate;
  state.selectedProjectId = params.get("project") || "";
  const conversation = params.get("conversation");
  const conversationSource = params.get("conversationSource");
  if (conversation && VALID_SOURCES.has(conversationSource) && conversationSource !== "all") {
    state.selected = { source: conversationSource, id: conversation };
  }
  state.filters[state.source] = currentFilters();
}

function renderDailyDateStrip() {
  const today = localDateIso();
  const days = Array.from({ length: 7 }, (_, index) => shiftDate(today, index - 6));
  if (!days.includes(state.dailyDate)) days.unshift(state.dailyDate);
  $("#dailyDateStrip").innerHTML = days.map((day) => `
    <button class="daily-date${day === state.dailyDate ? " active" : ""}" data-day="${day}" type="button">
      <strong>${escapeHtml(dayLabel(day))}</strong>
      <span>${escapeHtml(day.slice(5))}</span>
    </button>
  `).join("");
}

function dailyItemHtml(item, tone = "") {
  const linked = item.source && item.conversation_id;
  const tag = linked ? "button" : "div";
  const attrs = linked
    ? `type="button" data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.conversation_id)}"`
    : "";
  return `<${tag} class="daily-item ${tone}${linked ? " linked" : ""}" ${attrs}>
    ${linked ? `<span class="source-dot ${escapeHtml(item.source)}"></span>` : `<span class="daily-bullet">•</span>`}
    <span>
      <strong>${escapeHtml(item.text)}</strong>
      ${item.reason ? `<small class="daily-item-detail"><b>原因：</b>${escapeHtml(item.reason)}</small>` : ""}
      ${item.next_action ? `<small class="daily-item-detail"><b>后续：</b>${escapeHtml(item.next_action)}</small>` : ""}
    </span>
    ${linked ? `<small>查看原对话 ↗</small>` : ""}
  </${tag}>`;
}

function dailySectionHtml(title, items, tone = "", empty = "当天没有识别到相关事项") {
  return `<section class="daily-section ${tone}">
    <div class="daily-section-head"><h3>${title}</h3><span>${items.length}</span></div>
    <div class="daily-items">
      ${items.length ? items.map((item) => dailyItemHtml(item, tone)).join("") : `<p class="daily-empty">${empty}</p>`}
    </div>
  </section>`;
}

function readableParagraphsHtml(value) {
  return String(value || "")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
    .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
    .join("");
}

function joinedSummary(items, fallback) {
  const sentences = (items || []).map((item) => String(item.text || "").trim()).filter(Boolean);
  return sentences.length ? sentences.join(" ") : fallback;
}

function summaryEvidenceButton(item) {
  if (!item?.source || !item?.conversation_id) return "";
  return `<button class="summary-evidence-link" type="button"
    data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.conversation_id)}">
    <span class="source-dot ${escapeHtml(item.source)}"></span>证据
  </button>`;
}

function summaryItemParts(item, tone) {
  const text = String(item?.text || "").trim();
  // 优先用对话真实标题（agent 自己起的），没有才从摘要句子解析
  const itemTitle = String(item?.title || "").trim();
  let title = itemTitle;
  let detail = "";
  let match;
  if (tone === "achievement") {
    match = text.match(/^围绕“(.+?)”.*?[：:](.+)$/);
    if (match) { if (!title) title = match[1]; detail = match[2]; }
    else detail = text;
  } else if (tone === "unfinished") {
    match = text.match(/^“(.+?)”目前还没有完成/);
    if (match && !title) title = match[1];
    detail = item.reason || "";
  } else if (tone === "decision") {
    match = text.match(/^关于“(.+?)”.*?[：:](.+)$/);
    if (match) { if (!title) title = match[1]; detail = match[2]; }
  } else if (tone === "next") {
    if (!title) title = "优先动作";
    detail = text;
  }
  if (!title) title = text;
  return { title, detail };
}

function summaryTreeItem(item, tone, project = false) {
  const { title, detail } = summaryItemParts(item, tone);
  const showNext = tone === "unfinished" && item.next_action;
  return `<li class="summary-tree-item ${tone}">
    <span class="summary-tree-branch" aria-hidden="true"></span>
    <div class="summary-tree-content">
      <div class="summary-tree-title">
        <strong>${escapeHtml(title)}</strong>
        ${summaryEvidenceButton(item, project)}
      </div>
      ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
      ${showNext ? `<div class="summary-child-node"><b>下一步</b><span>${escapeHtml(item.next_action)}</span></div>` : ""}
    </div>
  </li>`;
}

function summaryTreeGroup(title, items, tone, empty, project = false) {
  const values = items || [];
  return `<section class="summary-tree-group ${tone}">
    <header>
      <span class="summary-parent-node"></span>
      <h3>${escapeHtml(title)}</h3>
      <b>${values.length}</b>
    </header>
    ${values.length
      ? `<ul>${values.map((item) => summaryTreeItem(item, tone, project)).join("")}</ul>`
      : `<p class="summary-tree-empty">${escapeHtml(empty)}</p>`}
  </section>`;
}

function summaryOtherItems(summary) {
  const used = new Set(
    [
      ...(summary.main_focus || []),
      ...(summary.achievements || []),
      ...(summary.unfinished || []),
      ...(summary.decisions || []),
      ...(summary.first_step || []),
    ].map((item) => `${item.source}:${item.conversation_id}`)
  );
  return (summary.activities || []).filter(
    (item) => !used.has(`${item.source}:${item.conversation_id}`)
  ).slice(0, 8);
}

function summaryHierarchyHtml(summary, { project = false } = {}) {
  const focus = summary.main_focus?.[0];
  const firstStep = summary.first_step?.[0] || summary.next_actions?.[0];
  const lead = String(summary.narrative || summary.overview_sentence || summary.overview || "")
    .split(/\n\s*\n/)[0]
    .trim();
  const others = summaryOtherItems(summary);
  return `
    <article class="summary-priority">
      <div class="summary-priority-main">
        <span class="summary-priority-label">最重要</span>
        <h2>${escapeHtml(focus?.text || "今天没有识别到唯一主线")}</h2>
        ${lead && lead !== focus?.text ? `<p>${escapeHtml(lead)}</p>` : ""}
        ${summaryEvidenceButton(focus, project)}
      </div>
      <div class="summary-priority-next">
        <span>接下来先做</span>
        <strong>${escapeHtml(firstStep?.text || summary.next_step_summary || "核对今天的结果并确定下一步")}</strong>
        ${summaryEvidenceButton(firstStep, project)}
      </div>
    </article>
    <div class="summary-tree">
      ${summaryTreeGroup("已经完成", summary.achievements, "achievement", "今天没有识别到可以核验的完成成果。", project)}
      ${summaryTreeGroup("尚未完成", summary.unfinished || summary.ongoing, "unfinished", "目前没有明确遗留事项。", project)}
      ${summaryTreeGroup("关键决定", summary.decisions || [], "decision", "今天没有需要单独记录的关键决定。", project)}
    </div>
    <details class="summary-other">
      <summary><span>其他记录</span><b>${others.length}</b><small>展开查看次要事项</small></summary>
      ${others.length
        ? `<ul>${others.map((item) => summaryTreeItem(item, "other", project)).join("")}</ul>`
        : `<p>没有额外的次要记录。</p>`}
    </details>`;
}

// ---- 每日回顾：摘要卡（结构化摘要模板）+ 完整日报（日报模板） ----

function dailySummaryCardHtml(data) {
  const summary = data.summary;
  const focus = summary.main_focus?.[0]?.text || "今天没有识别到唯一主线";
  const achievements = summary.achievements || [];
  const unfinished = summary.unfinished || summary.ongoing || [];
  const decisions = summary.decisions || [];
  const firstStep = (summary.first_step || summary.next_actions || [])[0];
  return `
    <section class="daily-card">
      <div class="daily-card-head">
        <span class="daily-card-label">今日主线</span>
        <h2>${escapeHtml(focus)}</h2>
        ${summary.overview_sentence && summary.overview_sentence !== focus
          ? `<p class="daily-card-overview">${escapeHtml(summary.overview_sentence)}</p>` : ""}
      </div>
      <div class="daily-card-metrics">
        <span><b>${data.stats.conversations}</b> 对话</span>
        <span><b>${data.stats.messages}</b> 有效消息</span>
        <span class="m-done"><b>${achievements.length}</b> 完成</span>
        <span class="m-open"><b>${unfinished.length}</b> 待继续</span>
        <span><b>${decisions.length}</b> 决定</span>
      </div>
      <div class="daily-card-cols">
        ${summaryTreeGroup("已完成", achievements.slice(0, 2), "achievement", "今天暂无可核验成果。")}
        ${summaryTreeGroup("待继续", unfinished.slice(0, 2), "unfinished", "目前没有明确遗留事项。")}
        ${summaryTreeGroup("关键决定", decisions.slice(0, 2), "decision", "今天没有关键决定。")}
      </div>
      ${firstStep ? `
        <div class="daily-card-next">
          <span>接下来先做</span>
          <strong>${escapeHtml(firstStep.text)}</strong>
          ${summaryEvidenceButton(firstStep)}
        </div>` : ""}
      <div class="daily-card-actions">
        <button id="toggleDailyReportButton" class="button primary" type="button">
          ${state.dailyReportOpen ? "收起完整日报" : "查看完整日报"}
        </button>
        <span class="muted">日报含逐节明细、数据概览与当天对话清单</span>
      </div>
    </section>
  `;
}

function reportItem(item, tone) {
  const { title, detail } = summaryItemParts(item, tone);
  const nextLine = (tone === "unfinished" || tone === "blocked") && item.next_action
    ? `<p class="report-next"><b>${tone === "blocked" ? "建议动作" : "下一步"}</b>${escapeHtml(item.next_action)}</p>`
    : "";
  return `<li class="report-item ${tone}">
    <div class="report-item-title">
      <strong>${escapeHtml(title || item.text || "")}</strong>
      ${summaryEvidenceButton(item)}
    </div>
    ${detail ? `<p>${escapeHtml(detail)}</p>` : ""}
    ${nextLine}
  </li>`;
}

function reportList(items, tone, empty) {
  const values = items || [];
  return values.length
    ? `<ul class="report-list">${values.map((item) => reportItem(item, tone)).join("")}</ul>`
    : `<p class="report-empty">${escapeHtml(empty)}</p>`;
}

function dailyReportHtml(data) {
  const summary = data.summary;
  const s = data.stats;
  const blocked = summary.blocked || [];
  const nextItems = [...(summary.first_step || []), ...(summary.next_actions || [])]
    .filter((item, index, arr) => arr.findIndex((o) => o.text === item.text) === index)
    .slice(0, 5);
  const kv = (label, value) => `<div class="report-kv"><span>${escapeHtml(label)}</span><b>${value}</b></div>`;
  const overviewParagraphs = [summary.overview_sentence || summary.overview || ""]
    .concat(
      String(summary.narrative || "")
        .split(/\n\s*\n/)
        .map((p) => p.trim())
        .filter((p) => p && p !== (summary.overview_sentence || summary.overview))
    )
    .slice(0, 4);
  return `
    <header class="report-head">
      <p class="eyebrow">DAILY REPORT</p>
      <h2>${escapeHtml(dayLabel(data.day))} · 工作日报</h2>
      <p class="muted">${data.conversations.length} 个对话 · ${s.messages} 条有效消息 · 生成于 ${dateTime(data.generated_at)}</p>
    </header>

    <section class="report-section">
      <h3>一、今日概览</h3>
      ${overviewParagraphs.map((p) => `<p>${escapeHtml(p)}</p>`).join("")}
    </section>

    <section class="report-section">
      <h3>二、已完成成果</h3>
      ${reportList(summary.achievements, "achievement", "今天暂无可核验的完成成果。")}
    </section>

    <section class="report-section">
      <h3>三、关键决定</h3>
      ${reportList(summary.decisions, "decision", "今天没有需要单独记录的关键决定。")}
    </section>

    <section class="report-section">
      <h3>四、未完成与原因</h3>
      ${reportList(summary.unfinished || summary.ongoing, "unfinished", "目前没有识别到明确的未完成事项。")}
    </section>

    <section class="report-section">
      <h3>五、受阻项</h3>
      ${reportList(blocked, "blocked", "今天没有明显的受阻事项。")}
    </section>

    <section class="report-section">
      <h3>六、下一步计划</h3>
      ${reportList(nextItems, "next", "暂无明确的下一步计划。")}
    </section>

    <section class="report-section">
      <h3>七、数据概览</h3>
      <div class="report-stats">
        ${kv("对话数", s.conversations)}
        ${kv("有效消息", s.messages)}
        ${kv("工作区", s.workspaces)}
        ${Object.entries(s.by_source || {})
          .filter(([, count]) => count > 0)
          .map(([source, count]) => kv(conversationSourceLabel({ source }), count))
          .join("")}
      </div>
    </section>

    <details class="report-conversations" ${data.conversations.length ? "" : ""}>
      <summary>当天对话清单（${data.conversations.length}）</summary>
      <div class="daily-conversation-list">
        ${data.conversations.length ? data.conversations.map((item) => `
          <button class="daily-conversation" type="button" data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.id)}">
            <span class="source-dot ${escapeHtml(item.source)}"></span>
            <span><strong>${escapeHtml(item.title)}</strong><small>${
              escapeHtml(conversationSourceLabel(item))
            } · ${escapeHtml(item.workspace)} · ${item.message_count} 条消息</small></span>
            <time>${dateTime(item.updated_at)}</time>
          </button>
        `).join("") : `<p class="daily-empty">当天没有可显示的对话。</p>`}
      </div>
    </details>

    <div class="daily-manual-note">
      <div>
        <strong>人工补充与修订</strong>
        <p class="muted">记录模型或规则没有捕捉到的成果、状态和下一步。</p>
      </div>
      <textarea id="dailyManualNote" rows="3" placeholder="例如：项目已人工验收；下周继续处理数据迁移…">${escapeHtml(data.manual_note || "")}</textarea>
      <button id="saveDailyNoteButton" class="button secondary" type="button">保存补充</button>
    </div>
  `;
}

function renderDaily(data) {
  state.daily = data;
  const summary = data.summary;
  const generatorLabel = data.generator === "model"
    ? `模型摘要 · ${data.model}`
    : data.generator === "model_fallback"
      ? `备用模型摘要 · ${data.model}`
    : data.generator === "rules_after_model_error"
      ? "模型失败，已回退模板摘要"
      : "模板摘要 · 本地规则";
  const templateName = data.template?.name || "daily_review_v3";
  const statusBadges = [
    data.is_today ? "今日草稿" : "历史回顾",
    generatorLabel,
    `模板 ${templateName}`,
    data.is_stale ? "对话有更新，建议重新生成" : "",
  ].filter(Boolean);
  const html = `<div class="daily-head">
      <span class="muted">生成于 ${dateTime(data.generated_at)}</span>
    </div>
    ${dailySummaryCardHtml(data)}
    <div class="daily-report" id="dailyReport" ${state.dailyReportOpen ? "" : "hidden"}>
      ${dailyReportHtml(data)}
    </div>
  `;
  const brief = $("#findDailyBrief");
  if (brief) {
    const focusEntry = summary.main_focus?.[0] || {};
    const achievements = summary.achievements || [];
    const unfinishedList = summary.unfinished || summary.ongoing || [];
    const focusKey = (focusEntry.source || "") + "/" + (focusEntry.conversation_id || focusEntry.id || "");
    // 所有事项平等并列：焦点 + 完成 + 待继续，去重同源（统一圆点，不区分符号/状态）
    const seenKeys = new Set();
    const items = [];
    const add = (entry, fallbackTitle) => {
      const key = (entry.source || "") + "/" + (entry.conversation_id || entry.id || "");
      if (seenKeys.has(key)) return;
      const parts = summaryItemParts(entry, "unfinished");
      const title = (parts.title && parts.title !== entry.text ? parts.title : fallbackTitle) || "（无标题）";
      items.push({
        title,
        source: entry.source,
        conversation_id: entry.conversation_id || entry.id,
        last_user: entry.last_user || "",
        last_reply: entry.last_reply || "",
      });
      seenKeys.add(key);
    };
    if (focusEntry.text) add(focusEntry, focusEntry.text);
    unfinishedList.slice(0, 4).forEach((it) => add(it, summaryItemParts(it, "unfinished").title || "待继续"));
    achievements.slice(0, 3).forEach((it) => add(it, summaryItemParts(it, "achievement").title || "已完成"));
    const totalItems = achievements.length + unfinishedList.length;
    const itemLi = (it) => {
      const hasMsg = !!(it.last_user || it.last_reply);
      const sourceLabel = SOURCE_LABELS[it.source] || it.source;
      return `<li class="brief-item"${hasMsg ? ' tabindex="0"' : ""}>
        <span class="brief-row">
          <span class="brief-dot" aria-hidden="true"></span>
          <span class="brief-title">${escapeHtml(it.title)}</span>
          <span class="brief-source src-${escapeHtml(it.source)}">${escapeHtml(sourceLabel)}</span>
          ${hasMsg ? '<span class="brief-toggle" aria-hidden="true">▾</span>' : ""}
          <button class="brief-jump" type="button" data-source="${escapeHtml(it.source)}" data-id="${escapeHtml(it.conversation_id)}" title="打开该对话" aria-label="打开该对话">↗</button>
        </span>
        ${hasMsg ? `<div class="brief-detail" hidden>${[
          it.last_user ? `<div class="brief-msg user"><b>你最近说</b><span>${escapeHtml(it.last_user)}</span></div>` : "",
          it.last_reply ? `<div class="brief-msg assistant"><b>最近回复</b><span>${escapeHtml(it.last_reply)}</span></div>` : "",
        ].join("")}</div>` : ""}
      </li>`;
    };
    const today = localDateIso();
    const canPrev = data.day > "2026-01-01";
    const canNext = data.day < today;
    // 本地日期加减（避免 toISOString 的时区坑）
    const shiftDay = (dayStr, delta) => {
      const [y, m, d] = dayStr.split("-").map(Number);
      const dt = new Date(y, m - 1, d + delta);
      return localDateIso(dt);
    };
    brief.innerHTML = `
      <div class="brief-label">
        <div class="brief-date-nav">
          <button class="brief-date-btn" type="button" data-brief-day-prev ${canPrev ? "" : "disabled"} aria-label="前一天">‹</button>
          <button class="brief-date-pick" type="button" data-brief-day-pick title="选择日期">${escapeHtml(dayLabel(data.day))}</button>
          <input class="brief-date-input" type="date" max="${today}" value="${data.day}" hidden>
          <button class="brief-date-btn" type="button" data-brief-day-next ${canNext ? "" : "disabled"} aria-label="后一天">›</button>
        </div>
        <strong>${data.day === today ? "今日要点" : "当日要点"}</strong>
      </div>
      <div class="brief-copy">
        <ul class="brief-points-list">${items.map(itemLi).join("")}</ul>
      </div>
      <div class="brief-actions">
        <span><b>${totalItems}</b> 件事</span>
        <button class="button secondary" type="button" data-open-daily>完整回顾</button>
      </div>
    `;
    // 日期切换：‹ 前一天 / › 后一天 / 点中间开日历
    brief.querySelector("[data-brief-day-prev]")?.addEventListener("click", () => setDailyDate(shiftDay(data.day, -1)));
    brief.querySelector("[data-brief-day-next]")?.addEventListener("click", () => setDailyDate(shiftDay(data.day, 1)));
    const pickBtn = brief.querySelector("[data-brief-day-pick]");
    const pickInput = brief.querySelector(".brief-date-input");
    pickBtn?.addEventListener("click", () => pickInput?.showPicker?.() || pickInput?.click());
    pickInput?.addEventListener("change", () => { if (pickInput.value && pickInput.value <= today) setDailyDate(pickInput.value); });
    // 展开/收起：直接用注入的最近消息，无需请求
    brief.querySelectorAll(".brief-item").forEach((li) => {
      const box = li.querySelector(".brief-detail");
      if (!box) return;
      const toggle = () => {
        const open = li.classList.toggle("expanded");
        box.hidden = !open;
      };
      li.addEventListener("click", toggle);
      li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    });
    // 跳转按钮：切到找对话视图并打开该对话（阻止冒泡，不触发展开/收起）
    brief.querySelectorAll(".brief-jump").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        setView("find");
        await openDetail(btn.dataset.source, btn.dataset.id);
      });
    });
  }
  if (data.warning) showToast(data.warning);
  return html;
}

async function loadDaily() {
  const _t = (m) => { const el = document.getElementById("bootDebug"); if (el) el.textContent = `[daily] ${m}`; };
  _t("start");
  $("#reviewDate").value = state.dailyDate;
  $("#reviewDate").max = localDateIso();
  $("#nextDayButton").disabled = state.dailyDate >= localDateIso();
  renderDailyDateStrip();
  _t("before api");
  const data = await api(`/api/daily?date=${encodeURIComponent(state.dailyDate)}`);
  _t("api done");
  $("#dailyBody").innerHTML = renderDaily(data);
  _t("render done");
  syncUrl();
}

async function setDailyDate(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day > localDateIso()) return;
  state.dailyDate = day;
  state.dailyReportOpen = false;
  await loadDaily();
}

function currentFilters() {
  return {
    range: state.range,
    status: state.status,
    workspace: state.workspace,
    nativeProject: state.nativeProject,
    favorites: state.favorites,
    query: state.query,
  };
}

function rememberCurrentFilters() {
  state.filters[state.source] = currentFilters();
}

function syncControls() {
  document.querySelectorAll("#agentSwitcher [data-source]").forEach((node) =>
    node.classList.toggle("active", node.dataset.source === state.source));
  $("#searchAgentFilter").value = state.source;
  document.body.dataset.agent = state.source;
  $("#statusFilter").value = state.status;
  $("#favoriteFilter").setAttribute("aria-pressed", String(state.favorites));
  $("#favoriteFilter").textContent = state.favorites ? "★ 只看收藏" : "☆ 只看收藏";
  $("#searchInput").value = state.query;
  $("#workspaceFilter").value = state.workspace;
  $("#nativeProjectFilter").value = state.nativeProject;
  renderWorkspaceHeading();
  renderWorkspaceSummary();
}

function setView(view, { sync = true } = {}) {
  state.view = VALID_VIEWS.has(view) ? view : "find";
  document.querySelectorAll(".app-view").forEach((node) => {
    node.classList.toggle("active", node.id === `${state.view}View`);
  });
  document.querySelectorAll("#primaryNav [data-view], #sidebarUtility [data-view]").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === state.view);
  });
  document.body.dataset.view = state.view;
  if (state.view === "daily" && !state.daily) {
    loadDaily().catch((error) => showToast(error.message));
  }
  if (state.view === "settings") {
    loadSourceHealth().catch((error) => showToast(error.message));
    loadVersionInfo().catch((error) => showToast(error.message));
  }
  if (state.view === "assets") {
    loadAssets().catch((error) => showToast(error.message));
  }
  if (state.view === "projects") {
    state.openProjectId = null;
    loadProjects().catch((error) => showToast(error.message));
  }
  if (sync) syncUrl();
}

async function loadSourceHealth() {
  const data = await api("/api/sources");
  const values = Object.values(data.sources);
  const healthy = values.filter((item) => item.status === "healthy").length;
  const warnings = values.filter((item) =>
    ["partial", "metadata_only"].includes(item.completeness) || ["error", "schema_changed"].includes(item.status)
  ).length;
  $("#sourceQualitySummary").textContent =
    `${healthy} 个适配器健康 · ${warnings} 个需关注 · 核心来源优先验收`;
  $("#sourceHealth").innerHTML = Object.entries(data.sources).map(([source, item]) => `
    <div class="health-row">
      <strong>${escapeHtml(item.label || SOURCE_LABELS[source] || source)}</strong>
      <span class="health-state ${item.status === "healthy" ? "ok" : "missing"}">${
        !item.enabled ? "未启用" : ({
          healthy: "兼容",
          schema_changed: "结构有变化",
          error: "读取异常",
          missing: "路径缺失",
        }[item.status] || (item.exists ? "待检查" : "路径缺失"))
      }</span>
      <code title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</code>
      <b>${item.conversations} 个对话${
        item.subsources?.assistant ? `（桌面 ${item.subsources.desktop || 0} · 助理 ${item.subsources.assistant}）` : ""
      } · ${item.message_count || 0} 条正文 · ${
        { full: "正文完整", partial: "部分正文", metadata_only: "仅元数据", waiting: "等待数据", disabled: "未启用" }[item.completeness] || "待评估"
      }${item.schema_fingerprint_short ? ` · 结构 ${escapeHtml(item.schema_fingerprint_short)}` : ""}${
        item.excluded ? ` · 已排除 ${item.excluded} 个子 Agent/后台线程` : ""}${
        item.error ? ` · ${escapeHtml(item.error)}` : ""
      }</b>
    </div>
  `).join("");
}

async function loadVersionInfo() {
  try {
    const health = await api("/api/health");
    const el = $("#updateState");
    if (el) el.textContent = `当前版本 ${health.app_version}`;
  } catch {}
}

async function previewBackupFile(file) {
  const text = await file.text();
  if (text.length > 10_000_000) throw new Error("备份文件超过 10 MB");
  const backup = JSON.parse(text);
  const preview = await api("/api/backup/preview", {
    method: "POST",
    body: JSON.stringify({ backup }),
  });
  state.backupImport = backup;
  $("#backupState").innerHTML = `
    <strong>${preview.rows} 行</strong> · 新增 ${preview.new} · 冲突 ${preview.conflicts}
    <div class="settings-card-actions">
      <button id="restoreBackupKeepButton" class="button ghost" type="button">保留本机并补充缺失</button>
      <button id="restoreBackupNewerButton" class="button secondary" type="button">以较新记录合并</button>
    </div>`;
  const restore = async (mode) => {
    const result = await api("/api/backup/restore", {
      method: "POST",
      body: JSON.stringify({ backup: state.backupImport, mode }),
    });
    $("#backupState").textContent = `恢复完成：新增 ${result.inserted}，更新 ${result.updated}`;
    await Promise.all([loadDaily()]);
  };
  $("#restoreBackupKeepButton").addEventListener("click", () =>
    restore("keep_existing").catch((error) => showToast(error.message))
  );
  $("#restoreBackupNewerButton").addEventListener("click", () =>
    restore("merge_newer").catch((error) => showToast(error.message))
  );
}

function renderSetupStatus(data, { fill = true } = {}) {
  registerCustomSources(data.sources || {});
  syncSourceControls(data.sources || {});
  const mapping = {
    hermes: ["#setupHermesPath", "Hermes"],
    codex: ["#setupCodexPath", "Codex"],
    workbuddy: ["#setupWorkbuddyPath", "WorkBuddy"],
  };
  if (fill) {
    Object.entries(mapping).forEach(([key, [selector]]) => {
      const node = $(selector);
      const value = data.sources?.[key]?.path || "";
      if (node && value) node.value = value;
    });
  }
  $("#setupExtraSources").innerHTML = EXTRA_SOURCES.map((source) => {
    const item = data.sources?.[source] || {};
    return `<label class="setup-extra-source${item.enabled ? " enabled" : ""}" data-extra-source="${source}">
      <input type="checkbox" data-extra-enabled="${source}" ${item.enabled ? "checked" : ""}>
      <span><strong>${escapeHtml(item.label || SOURCE_LABELS[source])}</strong>
        <small>${item.valid ? `${item.conversations || 0} 个候选 · ${escapeHtml(item.detail || "结构验证通过")}` : "未发现或结构不匹配"}</small>
      </span>
      <input type="text" data-extra-path="${source}" value="${escapeHtml(item.path || "")}"
        placeholder="自动发现或粘贴数据路径">
    </label>`;
  }).join("");
  const customEntries = Object.entries(data.sources || {}).filter(([, item]) => item.custom);
  $("#setupCustomSources").innerHTML = customEntries.map(([source, item]) =>
    customSourceRow({
      id: source,
      label: item.label,
      format: item.format,
      path: item.path,
      enabled: item.enabled,
      valid: item.valid,
      detail: item.detail,
      conversations: item.conversations,
    })
  ).join("");
  $("#setupSourceState").innerHTML = Object.entries(mapping).map(([key, [, label]]) => {
    const item = data.sources?.[key] || {};
    return `<div class="setup-source-row ${item.valid ? "ok" : "missing"}">
      <strong>${label}</strong>
      <span>${item.valid ? `有效 · ${item.conversations || 0} 个对话` : "未找到或结构不匹配"}</span>
    </div>`;
  }).join("");
  $("#setupDataDir").textContent = data.data_dir ? `管理信息将保存在：${data.data_dir}` : "";
  $("#setupDialog").dataset.required = String(Boolean(data.required));
  $("#closeSetupButton").hidden = Boolean(data.required);
}

function customSourceRow(item = {}) {
  const id = item.id || `custom_${Date.now().toString(36)}`;
  const format = item.format || "jsonl";
  const stateText = item.valid
    ? `结构验证通过 · ${item.conversations || 0} 个候选 · ${item.detail || ""}`
    : (item.detail || "填写名称、格式和路径后保存验证");
  return `<div class="setup-custom-source${item.enabled ? " enabled" : ""}"
      data-custom-source="${escapeHtml(id)}">
    <input type="checkbox" data-custom-enabled ${item.enabled ? "checked" : ""} aria-label="启用">
    <input type="text" data-custom-label value="${escapeHtml(item.label || "")}" placeholder="Agent 名称">
    <select data-custom-format aria-label="数据格式">
      <option value="jsonl" ${format === "jsonl" ? "selected" : ""}>JSONL 自动识别</option>
      <option value="markdown" ${format === "markdown" ? "selected" : ""}>Markdown 目录</option>
      <option value="sqlite" ${format === "sqlite" ? "selected" : ""}>SQLite 自动识别</option>
    </select>
    <input class="custom-path" type="text" data-custom-path value="${escapeHtml(item.path || "")}"
      placeholder="会话文件、数据库或目录路径">
    <button class="button ghost custom-remove" data-remove-custom type="button" title="移除">×</button>
    <small>${escapeHtml(stateText)}</small>
  </div>`;
}

async function loadSetupStatus({ openIfRequired = false, open = false } = {}) {
  const data = await api("/api/setup/status");
  renderSetupStatus(data);
  if ((open || (openIfRequired && data.required)) && !$("#setupDialog").open) {
    $("#setupDialog").showModal();
  }
  return data;
}

async function loadAssets() {
  const today = localDateIso();
  const exportDate = $("#exportDate");
  if (exportDate && !exportDate.value) exportDate.value = today;
  if (exportDate) exportDate.max = today;
}

function exportPayload() {
  const scope = $("#exportScope")?.value || "day";
  const payload = {
    format: $("#exportFormat").value,
    include_messages: $("#exportMessages").checked,
    include_notes: $("#exportNotes").checked,
    anonymize_paths: $("#exportAnonPaths")?.checked !== false,
  };
  if (scope === "selected") {
    const selected = [...state.checked.values()];
    if (!selected.length) throw new Error("请先在「找对话」里勾选要导出的对话（点击对话左侧方框）");
    payload.scope = "selected";
    payload.conversations = selected.map((it) => ({ source: it.source, id: it.id }));
  } else {
    payload.scope = "day";
    payload.day = $("#exportDate").value;
  }
  return payload;
}

// 导出范围切换：选"已选对话"时隐藏日期，选"按日期"时显示
$("#exportScope")?.addEventListener("change", (e) => {
  const dateLabel = $("#exportDateLabel");
  if (dateLabel) dateLabel.style.display = e.target.value === "selected" ? "none" : "";
  const count = state.checked.size;
  const hint = $("#exportState");
  if (e.target.value === "selected") {
    if (count) {
      hint.textContent = `已勾选 ${count} 个对话`;
    } else {
      hint.textContent = "尚未勾选，正在跳转到「找对话」…";
      setView("find");
      showToast("请勾选要导出的对话，然后点选择栏的「导出所选」");
    }
  } else {
    hint.textContent = "";
  }
});

async function previewExport() {
  const button = $("#previewExportButton");
  button.disabled = true;
  $("#exportState").textContent = "正在整理安全导出…";
  try {
    const result = await api("/api/export", {
      method: "POST",
      body: JSON.stringify(exportPayload()),
    });
    state.exportResult = result;
    $("#exportPreview").value = result.preview;
    $("#downloadExportButton").disabled = false;
    $("#exportState").textContent = `${result.conversation_count} 个对话 · ${(result.bytes / 1024).toFixed(1)} KB`;
  } finally {
    button.disabled = false;
  }
}

function fileCategoryLabel(value) {
  return {
    code: "代码", document: "文档", image: "图片", data: "数据",
    archive: "压缩包", other: "其他",
  }[value] || value;
}

function classificationQueryMatches(item) {
  const query = ($("#classificationSearch").value || "").trim().toLocaleLowerCase();
  return !query || `${item.title} ${item.workspace} ${item.preview} ${item.reason}`
    .toLocaleLowerCase().includes(query);
}

function renderClassificationList() {
  const visible = state.classificationItems.filter(classificationQueryMatches);
  $("#classificationList").innerHTML = visible.length ? visible.map((item) => `
    <label class="classification-row">
      <input type="checkbox" data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.id)}">
      <span>
        <strong>${escapeHtml(item.title)}</strong>
        <small>${escapeHtml(item.workspace || "未命名工作区")} · ${escapeHtml(item.source)} · ${dateTime(item.updated_at)}</small>
        <em>${escapeHtml(item.reason)}${item.project_name ? ` · 当前：${escapeHtml(item.project_name)}` : ""}</em>
      </span>
      <b>${Math.round((item.confidence || 0) * 100)}%</b>
    </label>
  `).join("") : `<div class="asset-empty"><strong>没有匹配的对话</strong><p>可以切换“未归属”或“待确认”范围。</p></div>`;
  $("#classificationState").textContent = `共 ${state.classificationItems.length} 条，当前显示 ${visible.length} 条`;
}

function linesValue(values) {
  return (values || []).join("\n");
}

function renderWorkspaceHeading() {
  const labels = {
    all: ["UNIFIED OVERVIEW", "统一总览", "同时查看所有已启用 Agent，需要专注时切换到独立来源。"],
    hermes: ["HERMES WORKSPACE", "Hermes 工作区", "专门管理 Hermes 会话、续接链、上下文和历史记录。"],
    codex: ["CODEX WORKSPACE", "Codex 工作区", "专门管理 Codex 任务、项目执行记录和长期工作线程。"],
    workbuddy: ["WORKBUDDY WORKSPACE", "WorkBuddy 工作区", "集中查找 WorkBuddy 的本地任务、问答与整理记录。"],
  }[state.source] || [
    `${state.source.toUpperCase()} WORKSPACE`,
    `${SOURCE_LABELS[state.source] || state.source} 工作区`,
    `集中查找 ${SOURCE_LABELS[state.source] || state.source} 的本地主对话。`,
  ];
  $("#workspaceEyebrow").textContent = labels[0];
  $("#workspaceTitle").textContent = labels[1];
  $("#workspaceDescription").textContent = labels[2];
}

async function loadConversations({ append = false } = {}) {
  list.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/conversations?${queryString()}`);
    state.total = data.total;
    state.queryTerms = data.query_terms || [];
    state.items = append ? [...state.items, ...data.items] : data.items;
    $("#searchInput").removeAttribute("aria-invalid");
    renderList();
    syncUrl();
  } finally {
    list.removeAttribute("aria-busy");
  }
}

function renderList() {
  $("#resultCount").textContent = `${state.total} 个结果`;
  if (!state.items.length) {
    list.innerHTML = `<div class="empty-detail"><div><h2>没有匹配结果</h2><p>换一个关键词或放宽时间范围。</p></div></div>`;
  } else {
    const groups = new Map();
    state.items.forEach((item) => {
      const day = localDateIso(new Date(item.updated_at * 1000));
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(item);
    });
    list.innerHTML = [...groups.entries()].map(([day, items]) => {
      const rows = items.map((item) => {
      const selected = state.selected?.source === item.source && state.selected?.id === item.id;
      const checked = state.checked.has(`${item.source}:${item.id}`);
      const tags = [
        item.favorite ? "★ 收藏" : "",
        item.user_status ? statusLabel(item.user_status) : statusLabel(item.status),
        conversationKindLabel(item),
        ...(item.tags || []).slice(0, 3),
      ].filter(Boolean);
      const match = item.match_snippet
        ? `<span class="conversation-match">${highlightHtml(item.match_snippet, state.queryTerms)}</span>`
        : "";
      return `
        <button class="conversation${selected ? " selected" : ""}${checked ? " checked" : ""}" type="button"
          data-source="${item.source}" data-id="${escapeHtml(item.id)}">
          <span class="check-mark" role="checkbox" aria-checked="${checked}" data-check="1">${checked ? "✓" : ""}</span>
          <span class="source-dot ${item.source}"></span>
          <span class="conversation-main">
            <span class="conversation-title">${highlightHtml(item.title, state.queryTerms)}</span>
            <span class="conversation-preview">${highlightHtml(item.preview || "暂无预览", state.queryTerms)}</span>
            ${match}
            <span class="chips">
              ${item.native_project
                ? `<span class="chip native-project">原生 · ${escapeHtml(item.native_project)}</span>`
                : `<span class="chip neutral">${escapeHtml(item.workspace)}</span>`}
              ${tags.map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`).join("")}
            </span>
          </span>
          <span class="conversation-source">${escapeHtml(conversationSourceLabel(item))}</span>
          <span class="conversation-workspace" title="${escapeHtml(item.workspace)}">${escapeHtml(item.workspace)}</span>
          <span class="conversation-status">${escapeHtml(tags[0] || statusLabel(item.status))}</span>
          <span class="conversation-time">${relativeTime(item.updated_at)}</span>
        </button>`;
      }).join("");
      return `<section class="timeline-group">
        <header class="timeline-head"><h2>${escapeHtml(dayLabel(day))}</h2><span>${items.length} 个对话</span></header>
        ${rows}
      </section>`;
    }).join("");
  }
  $("#loadMoreButton").hidden = state.items.length >= state.total;
}

async function openDetail(source, id) {
  state.selected = { source, id };
  setDetailOpen(true);
  renderList();
  syncUrl();
  detailPane.scrollTop = 0;
  detailPane.innerHTML = `<div class="empty-detail"><div><h2>读取上下文…</h2></div></div>`;
  try {
    const data = await api(`/api/conversation/${encodeURIComponent(source)}/${encodeURIComponent(id)}`);
    renderDetail(data);
  } catch (error) {
    detailPane.innerHTML = `<div class="empty-detail"><div><h2>读取失败</h2><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

function renderDetail(data) {
  const item = data.conversation;
  detailPane.dataset.source = item.source;
  detailPane.dataset.conversationId = item.id;
  const fragment = $("#detailTemplate").content.cloneNode(true);
  // replaceChildren 会清空 fragment，先拿到元素节点引用供后续保存使用
  const detailRoot = fragment.querySelector(".detail-inner");
  fragment.querySelector(".source-line").textContent = [
    conversationSourceLabel(item),
    item.native_project ? `原生项目：${item.native_project}` : item.workspace,
  ].filter(Boolean).join(" · ");
  fragment.querySelector(".detail-title").textContent = item.title;
  fragment.querySelector(".detail-meta").textContent =
    `${dateTime(item.updated_at)} · ${item.id}${item.model ? ` · ${item.model}` : ""}`;
  const favoriteButton = fragment.querySelector(".favorite-button");
  favoriteButton.textContent = item.favorite ? "★" : "☆";
  favoriteButton.classList.toggle("active", item.favorite);
  fragment.querySelector(".detail-close-button").addEventListener("click", () => {
    setDetailOpen(false, { focusToggle: true });
  });

  const link = fragment.querySelector(".open-link");
  if (item.source === "codex") {
    link.href = `codex://threads/${encodeURIComponent(item.id)}`;
    link.textContent = "在 Codex 中打开";
  } else if (item.source === "hermes") {
    link.href = `hermes://session/${encodeURIComponent(item.id)}`;
    link.textContent = "在 Hermes 中打开";
  } else if (item.source === "claude" && !item.source_kind.includes("metadata-only")) {
    link.href = "#";
    link.textContent = "复制 Claude 续接命令";
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      await navigator.clipboard.writeText(`claude --resume ${item.id}`);
      showToast("已复制 Claude 续接命令");
    });
  } else if (item.source === "workbuddy") {
    link.href = "workbuddy://";
    link.textContent = "在 WorkBuddy 中打开";
  } else {
    link.hidden = true;
  }

  const overviewRows = [
    ["最初目标", data.overview.goal || "未提取到"],
    ["最新请求", data.overview.latest_request || "未提取到"],
    ["最新回应", data.overview.latest_response || "未提取到"],
  ];
  fragment.querySelector(".overview").innerHTML = overviewRows.map(([term, text]) =>
    `<div><dt>${term}</dt><dd>${escapeHtml(text)}</dd></div>`
  ).join("");

  const status = fragment.querySelector(".user-status");
  status.value = item.user_status || "";
  const projectSelect = fragment.querySelector(".project-assignment");
  if (projectSelect) projectSelect.parentElement.style.display = "none";
  fragment.querySelector(".tags-input").value = (item.tags || []).join(", ");
  fragment.querySelector(".note-input").value = item.note || "";
  const relatedBlock = fragment.querySelector(".related-block");
  const relatedItems = data.related_conversations || [];
  if (relatedItems.length) {
    relatedBlock.hidden = false;
    fragment.querySelector(".related-count").textContent = `${relatedItems.length} 条`;
    fragment.querySelector(".related-conversations").innerHTML = relatedItems.map((related) => `
      <button type="button" class="related-conversation" data-source="${escapeHtml(related.source)}"
        data-id="${escapeHtml(related.id)}">
        <span class="source-badge ${escapeHtml(related.source)}">${escapeHtml(SOURCE_LABELS[related.source] || related.source)}</span>
        <strong>${escapeHtml(related.title)}</strong>
        <small>${Math.round((related.confidence || 0) * 100)}% · ${dateTime(related.updated_at)}</small>
      </button>
    `).join("");
    fragment.querySelector(".related-conversations").addEventListener("click", (event) => {
      const button = event.target.closest(".related-conversation");
      if (button) openDetail(button.dataset.source, button.dataset.id);
    });
  }

  let messageRole = "all";
  let messageQuery = "";
  let activeMessageMatch = 0;
  let conversationMessages = data.messages;
  let fullMessagesLoaded = false;
  let fullMessagesLoading = false;
  const messagesRoot = fragment.querySelector(".messages");
  const messageCount = fragment.querySelector(".message-count");
  const roleButtons = [...fragment.querySelectorAll(".message-role-filter [data-role]")];
  const messageSearch = fragment.querySelector(".conversation-search-input");
  const messageSearchState = fragment.querySelector(".conversation-search-state");
  const previousMatchButton = fragment.querySelector(".conversation-search-previous");
  const nextMatchButton = fragment.querySelector(".conversation-search-next");
  const clearMessageSearchButton = fragment.querySelector(".conversation-search-clear");

  const renderMessages = () => {
    const needle = messageQuery.trim().toLocaleLowerCase();
    const filtered = conversationMessages.filter((message) => {
      const roleMatch = messageRole === "all" || message.role === messageRole;
      const queryMatch = !needle || message.text.toLocaleLowerCase().includes(needle);
      return roleMatch && queryMatch;
    });
    if (activeMessageMatch >= filtered.length) activeMessageMatch = Math.max(0, filtered.length - 1);
    messageCount.textContent = needle
      ? `命中 ${filtered.length} 条 · 已读取 ${conversationMessages.length} 条`
      : `${filtered.length} / ${conversationMessages.length} 条`;
    messageSearchState.textContent = fullMessagesLoading
      ? "正在读取完整对话…"
      : (!needle ? "输入关键词" : (filtered.length ? `${activeMessageMatch + 1} / ${filtered.length}` : "没有匹配"));
    previousMatchButton.disabled = filtered.length < 2;
    nextMatchButton.disabled = filtered.length < 2;
    clearMessageSearchButton.disabled = !needle;
    messagesRoot.innerHTML = filtered.length ? filtered.map((message, index) => `
      <article class="message ${message.role}${needle && index === activeMessageMatch ? " active-match" : ""}"
        data-message-match="${needle ? index : ""}">
        <div class="message-head"><strong>${message.role === "user" ? "用户" : "助手"}</strong><span>${dateTime(message.timestamp)}</span></div>
        <div class="message-text">${highlightHtml(message.text, messageQuery)}</div>
      </article>`).join("") : `<p class="muted">当前条件下没有消息。</p>`;
  };

  const focusMessageMatch = (direction = 0) => {
    const matches = [...messagesRoot.querySelectorAll("[data-message-match]")];
    if (!matches.length) return;
    activeMessageMatch = (activeMessageMatch + direction + matches.length) % matches.length;
    matches.forEach((node, index) => node.classList.toggle("active-match", index === activeMessageMatch));
    messageSearchState.textContent = `${activeMessageMatch + 1} / ${matches.length}`;
    matches[activeMessageMatch].scrollIntoView({ block: "center", behavior: "smooth" });
  };

  const loadFullConversationMessages = async () => {
    if (fullMessagesLoaded || fullMessagesLoading) return;
    fullMessagesLoading = true;
    renderMessages();
    try {
      const result = await api(
        `/api/conversation-messages/${encodeURIComponent(item.source)}/${encodeURIComponent(item.id)}?limit=300`
      );
      conversationMessages = result.messages || conversationMessages;
      fullMessagesLoaded = true;
    } catch (error) {
      showToast(`读取完整对话失败：${error.message}`);
    } finally {
      fullMessagesLoading = false;
      activeMessageMatch = 0;
      renderMessages();
      if (messageQuery.trim()) focusMessageMatch(0);
    }
  };

  renderMessages();

  roleButtons.forEach((button) => button.addEventListener("click", () => {
    messageRole = button.dataset.role;
    roleButtons.forEach((candidate) => candidate.classList.toggle("active", candidate === button));
    activeMessageMatch = 0;
    renderMessages();
  }));
  let messageSearchTimer = 0;
  messageSearch.addEventListener("input", (event) => {
    messageQuery = event.target.value;
    activeMessageMatch = 0;
    renderMessages();
    window.clearTimeout(messageSearchTimer);
    if (messageQuery.trim()) {
      messageSearchTimer = window.setTimeout(() => {
        loadFullConversationMessages().catch((error) => showToast(error.message));
      }, 180);
    }
  });
  messageSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      focusMessageMatch(event.shiftKey ? -1 : 1);
    } else if (event.key === "Escape") {
      messageSearch.value = "";
      messageQuery = "";
      activeMessageMatch = 0;
      renderMessages();
    }
  });
  previousMatchButton.addEventListener("click", () => focusMessageMatch(-1));
  nextMatchButton.addEventListener("click", () => focusMessageMatch(1));
  clearMessageSearchButton.addEventListener("click", () => {
    messageSearch.value = "";
    messageQuery = "";
    activeMessageMatch = 0;
    renderMessages();
    messageSearch.focus();
  });

  favoriteButton.addEventListener("click", async () => {
    item.favorite = !item.favorite;
    favoriteButton.textContent = item.favorite ? "★" : "☆";
    favoriteButton.classList.toggle("active", item.favorite);
    await saveDetail(detailRoot, item, true);
  });
  fragment.querySelector(".copy-id").addEventListener("click", async () => {
    await navigator.clipboard.writeText(item.id);
    showToast("已复制对话 ID");
  });
  fragment.querySelector(".export-conversation").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const result = await api("/api/export", {
        method: "POST",
        body: JSON.stringify({
          scope: "conversation",
          source: item.source,
          conversation_id: item.id,
          format: "markdown",
          include_messages: true,
          include_notes: true,
          include_knowledge: false,
        }),
      });
      downloadText(result.filename, result.content, result.mime);
      showToast("对话 Markdown 已导出");
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  });
  fragment.querySelector(".save-note").addEventListener("click", () => saveDetail(detailRoot, item, false));
  detailPane.replaceChildren(fragment);
}

async function saveDetail(root, item, quiet) {
  const payload = {
    source: item.source,
    id: item.id,
    note: root.querySelector(".note-input").value,
    tags: root.querySelector(".tags-input").value
      .split(/[,，]/)
      .map((tag) => tag.trim())
      .filter(Boolean),
    user_status: root.querySelector(".user-status").value,
    favorite: item.favorite,
  };
  const saveState = root.querySelector(".save-state");
  saveState.textContent = "保存中…";
  try {
    await api("/api/note", { method: "POST", body: JSON.stringify(payload) });
    saveState.textContent = "已保存";
    Object.assign(item, payload);
    const cached = state.items.find((candidate) => candidate.source === item.source && candidate.id === item.id);
    if (cached) Object.assign(cached, payload);
    renderList();
    loadSummary();
    if (!quiet) showToast("备注和状态已保存");
  } catch (error) {
    saveState.textContent = "保存失败";
    showToast(error.message);
  }
}

function resetAndLoad() {
  state.offset = 0;
  state.items = [];
  rememberCurrentFilters();
  syncControls();
  syncUrl();
  loadConversations().catch((error) => {
    if (error.message.startsWith("搜索语法：")) {
      $("#searchInput").setAttribute("aria-invalid", "true");
      $("#resultCount").textContent = "搜索条件需要调整";
      list.innerHTML = `<div class="empty-detail"><div><h2>搜索语法未完成</h2><p>${escapeHtml(error.message)}。点击搜索框右侧“语法”查看示例。</p></div></div>`;
    } else {
      showToast(error.message);
    }
  });
}

function setRange(value) {
  if (!VALID_RANGES.has(value)) return;
  state.range = value;
  resetAndLoad();
}

function readSavedViews() {
  try {
    const value = JSON.parse(localStorage.getItem(SAVED_VIEWS_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function writeSavedViews(views) {
  localStorage.setItem(SAVED_VIEWS_KEY, JSON.stringify(views));
}

function renderSavedViews(selectedId = "") {
  const select = $("#savedViewSelect");
  const views = readSavedViews();
  select.innerHTML = `<option value="">选择视图…</option>` + views.map((view) =>
    `<option value="${escapeHtml(view.id)}">${escapeHtml(view.name)}</option>`
  ).join("");
  select.value = views.some((view) => view.id === selectedId) ? selectedId : "";
  $("#deleteViewButton").disabled = !select.value;
}

function saveCurrentView() {
  const nameInput = $("#savedViewName");
  const name = nameInput.value.trim();
  if (!name) {
    nameInput.focus();
    showToast("先给当前视图起个名字");
    return;
  }
  const views = readSavedViews();
  const existing = views.find((view) => view.name === name);
  const saved = {
    id: existing?.id || `${Date.now()}`,
    name,
    filters: { source: state.source, ...currentFilters() },
  };
  const next = existing
    ? views.map((view) => view.id === existing.id ? saved : view)
    : [...views, saved];
  writeSavedViews(next);
  renderSavedViews(saved.id);
  nameInput.value = "";
  showToast(existing ? "已更新保存的视图" : "当前视图已保存");
}

function applySavedView(id) {
  const view = readSavedViews().find((candidate) => candidate.id === id);
  if (!view) return;
  const filters = view.filters || {};
  state.source = VALID_SOURCES.has(filters.source) ? filters.source : "all";
  state.range = VALID_RANGES.has(filters.range) ? filters.range : "all";
  state.status = VALID_STATUSES.has(filters.status) ? filters.status : "all";
  state.workspace = filters.workspace || "all";
  state.nativeProject = filters.nativeProject || "all";
  state.favorites = Boolean(filters.favorites);
  state.query = filters.query || "";
  state.filters[state.source] = currentFilters();
  state.selected = null;
  syncControls();
  resetAndLoad();
}

$("#primaryNav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  setView(button.dataset.view);
});

$("#sidebarUtility").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  setView(button.dataset.view);
});

$("#closeSetupButton").addEventListener("click", () => $("#setupDialog").close());
$("#setupDialog").addEventListener("cancel", (event) => {
  if (event.currentTarget.dataset.required === "true") event.preventDefault();
});
$("#setupExtraSources").addEventListener("change", (event) => {
  const source = event.target.dataset.extraEnabled;
  if (!source) return;
  event.target.closest(".setup-extra-source")?.classList.toggle("enabled", event.target.checked);
});
$("#addCustomSourceButton").addEventListener("click", () => {
  const root = $("#setupCustomSources");
  root.insertAdjacentHTML("beforeend", customSourceRow());
  root.lastElementChild?.querySelector("[data-custom-label]")?.focus();
});
$("#setupCustomSources").addEventListener("change", (event) => {
  if (event.target.matches("[data-custom-enabled]")) {
    event.target.closest(".setup-custom-source")?.classList.toggle("enabled", event.target.checked);
  }
});
$("#setupCustomSources").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-custom]");
  if (button) button.closest(".setup-custom-source")?.remove();
});
$("#discoverSourcesButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "发现中…";
  try {
    const extraRoot = $("#setupExtraRoot").value.trim();
    const data = await api("/api/setup/discover", {
      method: "POST",
      body: JSON.stringify({ roots: extraRoot ? [extraRoot] : [] }),
    });
    renderSetupStatus(data);
    showToast("自动发现完成，请确认后保存");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "自动发现";
  }
});
$("#saveSourcesButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "验证中…";
  try {
    const data = await api("/api/setup/save", {
      method: "POST",
      body: JSON.stringify({
        hermes_db: $("#setupHermesPath").value.trim(),
        codex_db: $("#setupCodexPath").value.trim(),
        workbuddy_home: $("#setupWorkbuddyPath").value.trim(),
        extra_sources: Object.fromEntries(EXTRA_SOURCES.map((source) => [
          source,
          {
            enabled: Boolean($(`[data-extra-enabled="${source}"]`)?.checked),
            path: $(`[data-extra-path="${source}"]`)?.value.trim() || "",
          },
        ])),
        custom_sources: [...document.querySelectorAll("[data-custom-source]")].map((row) => ({
          id: row.dataset.customSource,
          label: row.querySelector("[data-custom-label]").value.trim(),
          format: row.querySelector("[data-custom-format]").value,
          path: row.querySelector("[data-custom-path]").value.trim(),
          enabled: row.querySelector("[data-custom-enabled]").checked,
        })),
      }),
    });
    renderSetupStatus(data);
    $("#setupDialog").close();
    await Promise.all([loadDaily(), loadConversations(), loadSourceHealth()]);
    showToast("数据源已验证并建立索引");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "验证并开始使用";
  }
});

$("#previewExportButton").addEventListener("click", () => {
  previewExport().catch((error) => {
    $("#exportState").textContent = error.message;
    showToast(error.message);
  });
});
$("#downloadExportButton").addEventListener("click", () => {
  if (!state.exportResult) return;
  let content = state.exportResult.content;
  // Markdown 加 UTF-8 BOM，避免 Windows 记事本/部分知识库工具按 ANSI 误判乱码
  if (state.exportResult.filename.endsWith(".md") && !content.startsWith("\ufeff")) {
    content = "\ufeff" + content;
  }
  downloadText(state.exportResult.filename, content, state.exportResult.mime);
  showToast("导出文件已下载");
});

$("#findDailyBrief").addEventListener("click", (event) => {
  if (event.target.closest("[data-open-daily]")) setView("daily");
});

async function setSourceEnabled(checkbox) {
  const source = checkbox.dataset.sourceEnabled;
  const enabled = checkbox.checked;
  checkbox.disabled = true;
  const row = checkbox.closest(".source-row");
  row?.classList.add("source-updating");
  try {
    const data = await api("/api/sources/enabled", {
      method: "POST",
      body: JSON.stringify({ source, enabled }),
    });
    syncSourceControls(data.sources || {});
    await Promise.all([
      loadConversations(),
      loadDaily(),
    ]);
    syncControls();
    showToast(`${SOURCE_LABELS[source] || source} 已${enabled ? "启用" : "停用"}`);
  } catch (error) {
    checkbox.checked = !enabled;
    showToast(error.message);
  } finally {
    checkbox.disabled = false;
    row?.classList.remove("source-updating");
  }
}

function switchSource(source, { preserveQuery = false } = {}) {
  if (
    !VALID_SOURCES.has(source)
    || source === state.source
    || (source !== "all" && !state.enabledSources.has(source))
  ) return;
  setView("find");
  const currentQuery = state.query;
  rememberCurrentFilters();
  state.source = source;
  Object.assign(state, state.filters[state.source]);
  if (preserveQuery) state.query = currentQuery;
  if (state.selected && state.selected.source !== state.source && state.source !== "all") {
    state.selected = null;
  }
  syncControls();
  resetAndLoad();
}

$("#agentSwitcher").addEventListener("click", (event) => {
  const checkbox = event.target.closest("[data-source-enabled]");
  if (checkbox) {
    setSourceEnabled(checkbox).catch((error) => showToast(error.message));
    return;
  }
  const button = event.target.closest("[data-source]");
  if (!button) return;
  switchSource(button.dataset.source);
});

$("#searchAgentFilter").addEventListener("change", (event) => {
  switchSource(event.target.value, { preserveQuery: true });
});

$("#dailyDateStrip").addEventListener("click", (event) => {
  const button = event.target.closest("[data-day]");
  if (button) setDailyDate(button.dataset.day).catch((error) => showToast(error.message));
});

$("#previousDayButton").addEventListener("click", () => {
  setDailyDate(shiftDate(state.dailyDate, -1)).catch((error) => showToast(error.message));
});

$("#nextDayButton").addEventListener("click", () => {
  setDailyDate(shiftDate(state.dailyDate, 1)).catch((error) => showToast(error.message));
});

$("#reviewDate").addEventListener("change", (event) => {
  setDailyDate(event.target.value).catch((error) => showToast(error.message));
});

$("#refreshDataButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  showToast("正在重新读取数据源…");
  try {
    const result = await api("/api/refresh", { method: "POST", body: "{}" });
    await Promise.all([loadSummary(), loadConversations(), loadDaily()]);
    showToast(`已刷新 · 共 ${result.total ?? state.total} 个对话`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

// ------------------------------------------------------------------ 我的项目
async function loadProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  if (state.openProjectId) {
    await openProject(state.openProjectId);
  } else {
    renderProjectList();
  }
}

function renderProjectList() {
  const list = $("#projectList");
  const detail = $("#projectDetail");
  detail.hidden = true;
  list.hidden = false;
  $("#backToProjectsButton").hidden = true;
  if (!state.projects.length) {
    list.innerHTML = `<div class="empty-detail"><div><h2>还没有项目</h2><p>在「找对话」里勾选几个对话，点「归入项目」；或点「新建项目」。</p></div></div>`;
    return;
  }
  const stLabels = { active: "进行中", done: "已完成", paused: "暂停" };
  const stClass = { active: "st-active", done: "st-done", paused: "st-paused" };
  list.innerHTML = state.projects.map((p) => {
    const st = p.status || "active";
    return `
    <button class="project-card" type="button" data-project="${escapeHtml(p.id)}">
      <span class="project-card-head">
        <strong>${escapeHtml(p.name)}</strong>
        <span class="proj-status-pill ${stClass[st]}">${stLabels[st]}</span>
      </span>
      <span class="project-desc">${escapeHtml(p.description || "暂无说明")}</span>
      <span class="muted">${p.count} 个对话 · 更新于 ${relativeTime(p.updated_at)}</span>
    </button>`;
  }).join("");
}

async function openProject(id) {
  const data = await api(`/api/projects/${encodeURIComponent(id)}`);
  state.openProjectId = id;
  renderProjectDetail(data);
}

function renderProjectDetail(p) {
  const list = $("#projectList");
  const detail = $("#projectDetail");
  list.hidden = true;
  detail.hidden = false;
  $("#backToProjectsButton").hidden = false;
  state.openProjectId = p.id;
  const items = p.items || [];
  const tasks = p.tasks || [];
  const statusLabels = { active: "进行中", done: "已完成", paused: "暂停" };
  const statusClass = { active: "st-active", done: "st-done", paused: "st-paused" };
  const st = p.status || "active";
  const nextSt = st === "active" ? "done" : st === "done" ? "paused" : "active";

  detail.innerHTML = `
    <div class="project-detail-head">
      <div>
        <h3>${escapeHtml(p.name)}</h3>
        <p class="muted">${escapeHtml(p.description || "暂无说明")}</p>
      </div>
      <span class="project-detail-actions">
        <button class="proj-status-pill ${statusClass[st]}" type="button" data-cycle-status="${escapeHtml(p.id)}" title="点击切换状态">${statusLabels[st]}</button>
        <button class="button ghost" type="button" data-edit-project="${escapeHtml(p.id)}">编辑</button>
        <button class="button ghost" type="button" data-delete-project="${escapeHtml(p.id)}">删除</button>
      </span>
    </div>

    <section class="proj-section">
      <h4 class="proj-section-title">📝 项目笔记</h4>
      <textarea id="projectNoteArea" class="proj-note-area" rows="5" placeholder="记录关键结论、决策、反思…">${escapeHtml(p.note || "")}</textarea>
      <button class="button secondary proj-note-save" type="button" data-save-note="${escapeHtml(p.id)}">保存笔记</button>
    </section>

    <section class="proj-section">
      <h4 class="proj-section-title">✓ 任务清单 <small class="muted">(${tasks.filter(t => !t.done).length} 待办)</small></h4>
      <div class="proj-tasks">
        ${tasks.length ? tasks.map((t) => `
          <div class="proj-task${t.done ? " done" : ""}">
            <label><input type="checkbox" data-toggle-task="${escapeHtml(t.id)}" ${t.done ? "checked" : ""}><span>${escapeHtml(t.title)}</span></label>
            <button class="proj-task-del" type="button" data-del-task="${escapeHtml(t.id)}" title="删除">×</button>
          </div>`).join("") : `<p class="muted proj-empty-hint">暂无任务</p>`}
      </div>
      <div class="proj-task-add">
        <input id="projTaskInput" type="text" placeholder="添加任务后回车…" maxlength="200">
        <button class="button ghost" type="button" data-add-task="${escapeHtml(p.id)}">＋</button>
      </div>
    </section>

    <section class="proj-section">
      <h4 class="proj-section-title">💬 对话 (${items.length})</h4>
      ${items.length ? items.map((it) => `
        <div class="project-item${it.present ? "" : " missing"}">
          <span class="source-dot ${escapeHtml(it.source)}"></span>
          <div class="project-item-body">
            <div class="project-item-row">
              <button class="project-item-main" type="button" ${it.present ? `data-open-conv="${escapeHtml(it.source)}|${escapeHtml(it.id)}"` : "disabled"}>
                <strong>${escapeHtml(it.title)}</strong>
                <small class="muted">${escapeHtml(conversationSourceLabel({ source: it.source }))} · ${it.message_count} 条 · ${it.updated_at ? relativeTime(it.updated_at) : ""}</small>
              </button>
              <button class="button ghost" type="button" data-remove-conv="${escapeHtml(it.source)}|${escapeHtml(it.id)}">移除</button>
            </div>
            <input class="proj-item-note" type="text" placeholder="${escapeHtml(it.note ? "" : "加标注：为什么重要…")}"
              value="${escapeHtml(it.note || "")}"
              data-annotate="${escapeHtml(it.source)}|${escapeHtml(it.id)}">
          </div>
        </div>`).join("") : `<div class="empty-detail"><div><h2>这个项目还是空的</h2><p>回「找对话」勾选对话后点「归入项目」。</p></div></div>`}
    </section>`;

  // 笔记保存按钮
  detail.querySelector("[data-save-note]")?.addEventListener("click", async () => {
    const body = $("#projectNoteArea").value;
    await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "save_note", id: p.id, body }) });
    showToast("笔记已保存");
  });
  // 状态切换
  detail.querySelector("[data-cycle-status]")?.addEventListener("click", async () => {
    await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "set_status", id: p.id, status: nextSt }) });
    openProject(p.id);
  });
  // 添加任务
  const addTask = async () => {
    const title = $("#projTaskInput").value.trim();
    if (!title) return;
    await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "add_task", id: p.id, title }) });
    openProject(p.id);
  };
  detail.querySelector("[data-add-task]")?.addEventListener("click", addTask);
  $("#projTaskInput")?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addTask(); } });
  // 任务勾选/删除
  detail.querySelectorAll("[data-toggle-task]").forEach((cb) => {
    cb.addEventListener("change", async () => {
      await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "toggle_task", id: p.id, task_id: cb.dataset.toggleTask }) });
      openProject(p.id);
    });
  });
  detail.querySelectorAll("[data-del-task]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "delete_task", id: p.id, task_id: btn.dataset.delTask }) });
      openProject(p.id);
    });
  });
  // 对话标注（失焦时保存）
  detail.querySelectorAll("[data-annotate]").forEach((inp) => {
    inp.addEventListener("change", async () => {
      const [source, cid] = inp.dataset.annotate.split("|");
      await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "annotate_item", id: p.id, source, conversation_id: cid, note: inp.value }) });
      showToast("标注已保存");
    });
  });
}

function checkedConversations() {
  return [...state.checked.values()].map((it) => ({ source: it.source, id: it.id }));
}

async function assignToProject(projectId) {
  const conversations = checkedConversations();
  if (!conversations.length) { showToast("请先勾选对话"); return; }
  await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "add", id: projectId, conversations }) });
  state.checked.clear();
  renderList();
  updateSelectionBar();
  $("#projectAssignDialog").close();
  showToast(`已归入 ${conversations.length} 个对话`);
  if (state.view === "projects") loadProjects().catch(() => {});
}

function renderAssignList() {
  const box = $("#assignProjectList");
  box.innerHTML = state.projects.length
    ? state.projects.map((p) => `
        <button class="assign-project-row" type="button" data-assign="${escapeHtml(p.id)}">
          <strong>${escapeHtml(p.name)}</strong><span class="muted">${p.count} 个对话</span>
        </button>`).join("")
    : `<p class="muted">还没有项目，先在下方新建一个。</p>`;
}

$("#addToProjectButton").addEventListener("click", async () => {
  if (!state.checked.size) { showToast("请先勾选要归入的对话"); return; }
  try {
    const data = await api("/api/projects");
    state.projects = data.projects || [];
    renderAssignList();
    $("#projectAssignDialog").showModal();
  } catch (error) { showToast(error.message); }
});

$("#assignProjectList").addEventListener("click", (event) => {
  const row = event.target.closest("[data-assign]");
  if (row) assignToProject(row.dataset.assign).catch((error) => showToast(error.message));
});

$("#assignCreateButton").addEventListener("click", async () => {
  const name = $("#assignNewName").value.trim();
  if (!name) { showToast("先给新项目起个名字"); return; }
  try {
    const created = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ action: "create", name, description: $("#assignNewDesc").value.trim() }),
    });
    $("#assignNewName").value = "";
    $("#assignNewDesc").value = "";
    await assignToProject(created.id);
  } catch (error) { showToast(error.message); }
});

$("#closeAssignButton").addEventListener("click", () => $("#projectAssignDialog").close());

function openProjectForm(mode, id = null, addAfter = false) {
  state.projectForm = { mode, id, addAfter };
  const existing = mode === "edit" ? state.projects.find((p) => p.id === id) : null;
  $("#projectFormTitle").textContent = mode === "edit" ? "编辑项目" : "新建项目";
  $("#projectNameInput").value = existing?.name || "";
  $("#projectDescInput").value = existing?.description || "";
  $("#projectFormDialog").showModal();
}

$("#newProjectButton").addEventListener("click", () => openProjectForm("create"));
$("#projectFormCancel").addEventListener("click", () => $("#projectFormDialog").close());

$("#projectFormSave").addEventListener("click", async () => {
  const name = $("#projectNameInput").value.trim();
  if (!name) { showToast("项目需要名字"); return; }
  const { mode, id, addAfter } = state.projectForm;
  try {
    const result = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        action: mode === "edit" ? "update" : "create",
        id: mode === "edit" ? id : undefined,
        name,
        description: $("#projectDescInput").value.trim(),
      }),
    });
    $("#projectFormDialog").close();
    if (mode === "create" && addAfter) {
      await assignToProject(result.id);
    } else {
      showToast("已保存");
      loadProjects().catch(() => {});
    }
  } catch (error) { showToast(error.message); }
});

$("#projectList").addEventListener("click", (event) => {
  const card = event.target.closest("[data-project]");
  if (card) openProject(card.dataset.project).catch((error) => showToast(error.message));
});

$("#backToProjectsButton").addEventListener("click", () => {
  state.openProjectId = null;
  renderProjectList();
});

$("#projectDetail").addEventListener("click", async (event) => {
  const open = event.target.closest("[data-open-conv]");
  if (open) {
    const [source, id] = open.dataset.openConv.split("|");
    setView("find");
    await openDetail(source, id);
    detailPane.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const remove = event.target.closest("[data-remove-conv]");
  if (remove) {
    const [source, id] = remove.dataset.removeConv.split("|");
    await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ action: "remove", id: state.openProjectId, conversations: [{ source, id }] }),
    });
    openProject(state.openProjectId).catch(() => {});
    return;
  }
  const edit = event.target.closest("[data-edit-project]");
  if (edit) { openProjectForm("edit", edit.dataset.editProject); return; }
  const del = event.target.closest("[data-delete-project]");
  if (del) {
    const ok = await new Promise((resolve) => {
      wxConfirm(resolve);
    });
    if (!ok) return;
    await api("/api/projects", { method: "POST", body: JSON.stringify({ action: "delete", id: del.dataset.deleteProject }) });
    state.openProjectId = null;
    loadProjects().catch(() => {});
  }
});

function wxConfirm(resolve) {
  // 桌面环境用原生 confirm
  resolve(window.confirm("删除该项目？（不会删除对话本身）"));
}

$("#dailyBody").addEventListener("click", async (event) => {
  const toggle = event.target.closest("#toggleDailyReportButton");
  if (toggle) {
    state.dailyReportOpen = !state.dailyReportOpen;
    const report = $("#dailyReport");
    if (report) report.hidden = !state.dailyReportOpen;
    toggle.textContent = state.dailyReportOpen ? "收起完整日报" : "查看完整日报";
    if (state.dailyReportOpen) report?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const conversation = event.target.closest("[data-source][data-id]");
  if (conversation) {
    setView("find");
    await openDetail(conversation.dataset.source, conversation.dataset.id);
    detailPane.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  const saveButton = event.target.closest("#saveDailyNoteButton");
  if (!saveButton) return;
  saveButton.disabled = true;
  saveButton.textContent = "保存中…";
  try {
    const result = await api("/api/daily/note", {
      method: "POST",
      body: JSON.stringify({
        day: state.dailyDate,
        manual_note: $("#dailyManualNote").value,
      }),
    });
    if (state.daily) state.daily.manual_note = result.manual_note;
    showToast("当日补充已保存");
  } catch (error) {
    showToast(error.message);
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "保存补充";
  }
});

$("#summary").addEventListener("click", (event) => {
  const favorite = event.target.closest("[data-favorite]");
  if (favorite) {
    state.favorites = !state.favorites;
    resetAndLoad();
    return;
  }
  const range = event.target.closest("[data-range]");
  if (range) setRange(range.dataset.range);
});

$("#quickRanges").addEventListener("click", (event) => {
  const button = event.target.closest("[data-range]");
  if (button) setRange(button.dataset.range);
});

$("#clearFiltersButton").addEventListener("click", () => {
  Object.assign(state, defaultFilters());
  state.selected = null;
  resetAndLoad();
});

$("#statusFilter").addEventListener("change", (event) => {
  state.status = event.target.value;
  resetAndLoad();
});

$("#workspaceFilter").addEventListener("change", (event) => {
  state.workspace = event.target.value;
  resetAndLoad();
});

$("#nativeProjectFilter").addEventListener("change", (event) => {
  state.nativeProject = event.target.value;
  resetAndLoad();
});

$("#favoriteFilter").addEventListener("click", () => {
  state.favorites = !state.favorites;
  resetAndLoad();
});

// ---- 全局搜索 ----
function applySearch(rawValue) {
  state.query = rawValue.trim();
  resetAndLoad();
}

$("#searchInput").addEventListener("input", (event) => {
  event.target.removeAttribute("aria-invalid");
  clearTimeout(searchTimer);
  const value = event.target.value;
  searchTimer = setTimeout(() => applySearch(value), 380);
});
$("#searchInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    clearTimeout(searchTimer);
    applySearch(event.currentTarget.value);
  }
  if (event.key === "Escape" && event.currentTarget.value) {
    clearTimeout(searchTimer);
    event.currentTarget.value = "";
    state.query = "";
    resetAndLoad();
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    setView("find");
    $("#searchInput").focus();
    return;
  }
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (event.target.closest("input, textarea, select, [contenteditable]")) return;
    event.preventDefault();
    setView("find");
    $("#searchInput").focus();
  }
});

$("#savedViewSelect").addEventListener("change", (event) => {
  $("#deleteViewButton").disabled = !event.target.value;
  if (event.target.value) applySavedView(event.target.value);
});

$("#saveViewButton").addEventListener("click", saveCurrentView);
$("#savedViewName").addEventListener("keydown", (event) => {
  if (event.key === "Enter") saveCurrentView();
});

$("#deleteViewButton").addEventListener("click", () => {
  const id = $("#savedViewSelect").value;
  if (!id) return;
  writeSavedViews(readSavedViews().filter((view) => view.id !== id));
  renderSavedViews();
  showToast("保存的视图已删除");
});

list.addEventListener("click", (event) => {
  const button = event.target.closest(".conversation");
  if (!button) return;
  if (event.target.closest(".check-mark")) {
    toggleConversationCheck(button.dataset.source, button.dataset.id);
    return;
  }
  openDetail(button.dataset.source, button.dataset.id);
});

function toggleConversationCheck(source, id) {
  const key = `${source}:${id}`;
  if (state.checked.has(key)) {
    state.checked.delete(key);
  } else {
    const item = state.items.find((it) => it.source === source && it.id === id);
    state.checked.set(key, { source, id, title: item?.title || "" });
  }
  renderList();
  updateSelectionBar();
}

function updateSelectionBar() {
  const bar = $("#selectionBar");
  if (!bar) return;
  const count = state.checked.size;
  bar.hidden = count === 0;
  $("#selectionCount").textContent = `已选 ${count} 个对话`;
}

// ---- 对话总结 / 内容分析 ----

$("#selectAllVisibleButton")?.addEventListener("click", () => {
  state.items.forEach((item) => {
    const key = `${item.source}:${item.id}`;
    if (!state.checked.has(key)) {
      state.checked.set(key, { source: item.source, id: item.id, title: item.title });
    }
  });
  renderList();
  updateSelectionBar();
});

$("#clearSelectionButton")?.addEventListener("click", () => {
  state.checked.clear();
  renderList();
  updateSelectionBar();
});

// 导出所选：切到工具页，自动选"已勾选的对话"并预览
$("#exportSelectedButton")?.addEventListener("click", () => {
  if (!state.checked.size) { showToast("请先勾选要导出的对话"); return; }
  setView("assets");
  const scopeSelect = $("#exportScope");
  if (scopeSelect) scopeSelect.value = "selected";
  const dateLabel = $("#exportDateLabel");
  if (dateLabel) dateLabel.style.display = "none";
  $("#exportState").textContent = `已勾选 ${state.checked.size} 个对话`;
  previewExport().catch((error) => showToast(error.message));
});

$("#loadMoreButton").addEventListener("click", async () => {
  state.offset = state.items.length;
  await loadConversations({ append: true });
});

$("#refreshButton")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "刷新中…";
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
    await Promise.all([loadConversations(), loadDaily()]);
    showToast("已从所有启用的数据来源重新读取");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "刷新数据";
  }
});

$("#openSetupButton")?.addEventListener("click", () => loadSetupStatus({ open: true }));

$("#diagnoseSourcesButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "检查中…";
  try {
    await api("/api/sources/diagnose", { method: "POST", body: "{}" });
    await loadSourceHealth();
    showToast("适配器、结构和正文索引检查完成");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "运行兼容性检查";
  }
});

$("#exportBackupButton").addEventListener("click", async () => {
  try {
    const backup = await api("/api/backup/export", { method: "POST", body: "{}" });
    downloadText(
      `AIConversationHub-backup-${localDateIso()}.json`,
      JSON.stringify(backup, null, 2),
      "application/json;charset=utf-8"
    );
    $("#backupState").textContent = "备份已导出；不含密钥与原始对话。";
  } catch (error) {
    showToast(error.message);
  }
});

$("#backupFileInput").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) previewBackupFile(file).catch((error) => showToast(error.message));
  event.target.value = "";
});




$("#themeButton").addEventListener("click", openThemeDialog);
$("#detailToggleButton").addEventListener("click", () => {
  toggleDetailDrawer().catch((error) => showToast(error.message));
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !$(".find-layout").classList.contains("detail-open")) return;
  if (event.target.closest("dialog")) return;
  setDetailOpen(false, { focusToggle: true });
});
$("#openThemeSettingsButton").addEventListener("click", openThemeDialog);
$("#closeThemeButton").addEventListener("click", () => $("#themeDialog").close());
$("#themeGallery").addEventListener("click", (event) => {
  const button = event.target.closest("[data-theme-id]");
  if (!button) return;
  applyTheme(button.dataset.themeId);
  showToast(`已切换为${THEMES[button.dataset.themeId].name}`);
});
$("#resetThemeButton").addEventListener("click", () => {
  applyTheme("archive");
  showToast("已恢复经典主题");
});

async function boot() {
  const DEBUG = /[?&]debug=1/.test(location.search);
  const _log = (msg) => {
    if (!DEBUG) return;
    const el = document.getElementById("bootDebug") || (() => {
      const d = document.createElement("div");
      d.id = "bootDebug";
      d.style.cssText = "position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:9999;background:#173f3b;color:#fff;padding:6px 16px;border-radius:0 0 8px 8px;font-size:12px;font-family:sans-serif";
      document.body.prepend(d);
      return d;
    })();
    el.textContent = msg;
  };
  try {
    _log("启动中…");
    let savedTheme = "";
    try { savedTheme = localStorage.getItem(THEME_KEY) || ""; } catch {}
    applyTheme(THEMES[savedTheme] ? savedTheme : currentTheme(), { persist: false });
    initSidebarCollapse();
    setDetailOpen(false);
    initDetailResizer();
    initSourceDetails();
    initSourceDrag();
    _log("初始化完成…");
    $("#todayDate").textContent = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "long",
      day: "numeric",
      weekday: "short",
    }).format(new Date());
    renderSavedViews();
    state.token = (await api("/api/token")).token;
    _log("获取令牌…");
    await loadSetupStatus({ openIfRequired: true });
    _log("检查数据源…");
    readUrlState();
    setView(state.view, { sync: false });
    _log("加载对话…");
    loadSummary();
    syncControls();
    await loadConversations();
    _log("完成");
    if (state.view === "daily") {
      await loadDaily();
    } else {
      loadDaily().catch((error) => showToast(error.message));
    }
  } catch (error) {
    showToast(error.message);
  }
}

boot();
