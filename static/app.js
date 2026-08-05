const SAVED_VIEWS_KEY = "conversation-hub-v6-saved-views";
const DETAIL_WIDTH_KEY = "conversation-hub-detail-width";
const PROJECT_RAIL_WIDTH_KEY = "conversation-hub-project-rail-width";
const PROJECT_DETAIL_WIDTH_KEY = "conversation-hub-project-detail-width";
const SOURCE_DETAILS_KEY = "conversation-hub-source-details-open";
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
  codepilot: "CodePilot",
  cursor: "Cursor",
  marvis: "Marvis",
  qclaw: "QClaw",
  qoderwork: "QoderWork",
};
const EXTRA_SOURCES = ["claude", "codepilot", "cursor", "marvis", "qclaw", "qoderwork"];
const VALID_SOURCES = new Set(["all", ...Object.keys(SOURCE_LABELS)]);
const VALID_RANGES = new Set(["all", "today", "3d", "7d", "30d"]);
const VALID_STATUSES = new Set(["all", "todo", "done", "reference", "archive_candidate"]);
const VALID_VIEWS = new Set(["find", "daily", "project", "summaries", "skills", "assets", "settings"]);
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
    option.disabled = option.value !== "all" && !state.enabledSources.has(option.value);
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
  summaryConfig: null,
  summaryModels: [],
  enabledSources: new Set(),
  checked: new Map(),
  summaries: [],
  currentSummaryId: "",
  projects: [],
  selectedProjectId: "",
  projectData: null,
  projectCache: {},
  projectCatalogRefreshedAt: 0,
  projectLoadRequestId: 0,
  projectPrefetchRun: 0,
  selectedMilestoneId: "",
  knowledge: [],
  exportResult: null,
  contextPack: null,
  classificationItems: [],
  activity: null,
  projectFiles: null,
  projectRulePreviewValid: false,
  skills: [],
  skillCounts: null,
  skillCapabilities: [],
  selectedSkillId: "",
  skillDetail: null,
  skillFilters: {
    query: "",
    agent: "all",
    capability: "all",
    status: "all",
    favorites: false,
    driftOnly: false,
  },
  backupImport: null,
  updateCandidate: null,
  dailyDate: localDateIso(),
  daily: null,
  dailyReportOpen: false,
  smartMode: true,
  smartRaw: "",
  smartInterp: null,
  filters: {
    all: defaultFilters(),
    hermes: defaultFilters(),
    codex: defaultFilters(),
    workbuddy: defaultFilters(),
    claude: defaultFilters(),
    codepilot: defaultFilters(),
    cursor: defaultFilters(),
    marvis: defaultFilters(),
    qclaw: defaultFilters(),
    qoderwork: defaultFilters(),
  },
};

const $ = (selector) => document.querySelector(selector);
const list = $("#conversationList");
const detailPane = $("#detailPane");
let searchTimer = null;
let skillSearchTimer = null;
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

function projectLayoutWidth() {
  const layout = $(".project-layout");
  return layout?.clientWidth || Math.max(650, window.innerWidth - 180);
}

function projectColumnWidth(variable, fallback) {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue(variable)) || fallback;
}

function setProjectColumnWidth(kind, value, { persist = false } = {}) {
  const isRail = kind === "rail";
  const variable = isRail ? "--project-rail-width" : "--project-detail-width";
  const key = isRail ? PROJECT_RAIL_WIDTH_KEY : PROJECT_DETAIL_WIDTH_KEY;
  const handle = isRail ? $("#projectRailResizer") : $("#projectDetailResizer");
  const minimum = isRail ? 180 : 250;
  const hardMaximum = isRail ? 420 : 560;
  const other = isRail
    ? projectColumnWidth("--project-detail-width", 330)
    : projectColumnWidth("--project-rail-width", 230);
  const availableForSides = Math.max(430, projectLayoutWidth() - 234);
  const maximum = Math.max(minimum, Math.min(hardMaximum, availableForSides - other));
  const width = Math.round(Math.max(minimum, Math.min(maximum, Number(value) || (isRail ? 230 : 330))));
  document.documentElement.style.setProperty(variable, `${width}px`);
  handle.setAttribute("aria-valuenow", String(width));
  handle.setAttribute("aria-valuemax", String(maximum));
  if (persist) {
    try {
      localStorage.setItem(key, String(width));
    } catch {
      // Browser storage is optional; resizing still works for this session.
    }
  }
  return width;
}

function initProjectColumnResizers() {
  let savedRail = 230;
  let savedDetail = 330;
  try {
    savedRail = Number(localStorage.getItem(PROJECT_RAIL_WIDTH_KEY)) || 230;
    savedDetail = Number(localStorage.getItem(PROJECT_DETAIL_WIDTH_KEY)) || 330;
  } catch {
    // Keep defaults when local storage is unavailable.
  }
  setProjectColumnWidth("detail", savedDetail);
  setProjectColumnWidth("rail", savedRail);

  [
    { kind: "rail", handle: $("#projectRailResizer"), fallback: 230 },
    { kind: "detail", handle: $("#projectDetailResizer"), fallback: 330 },
  ].forEach(({ kind, handle, fallback }) => {
    let startX = 0;
    let startWidth = fallback;
    handle.addEventListener("pointerdown", (event) => {
      if (window.matchMedia("(max-width: 840px)").matches) return;
      startX = event.clientX;
      startWidth = projectColumnWidth(
        kind === "rail" ? "--project-rail-width" : "--project-detail-width",
        fallback,
      );
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("active");
      document.body.classList.add("resizing-project-columns");
    });
    handle.addEventListener("pointermove", (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const delta = event.clientX - startX;
      setProjectColumnWidth(kind, startWidth + (kind === "rail" ? delta : -delta));
    });
    const finish = (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      handle.releasePointerCapture(event.pointerId);
      handle.classList.remove("active");
      document.body.classList.remove("resizing-project-columns");
      setProjectColumnWidth(
        kind,
        projectColumnWidth(
          kind === "rail" ? "--project-rail-width" : "--project-detail-width",
          fallback,
        ),
        { persist: true },
      );
    };
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    handle.addEventListener("dblclick", () => {
      setProjectColumnWidth(kind, fallback, { persist: true });
    });
    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
      event.preventDefault();
      const current = projectColumnWidth(
        kind === "rail" ? "--project-rail-width" : "--project-detail-width",
        fallback,
      );
      const movement = event.key === "Home" ? 0 : (event.key === "ArrowRight" ? 24 : -24);
      const next = event.key === "Home"
        ? fallback
        : current + (kind === "rail" ? movement : -movement);
      setProjectColumnWidth(kind, next, { persist: true });
    });
  });

  window.addEventListener("resize", () => {
    if (window.matchMedia("(max-width: 840px)").matches) return;
    setProjectColumnWidth(
      "detail",
      projectColumnWidth("--project-detail-width", 330),
    );
    setProjectColumnWidth(
      "rail",
      projectColumnWidth("--project-rail-width", 230),
    );
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

function summaryEvidenceButton(item, project = false) {
  if (!item?.source || !item?.conversation_id) return "";
  if (project) return projectEvidenceButton(item, "证据");
  return `<button class="summary-evidence-link" type="button"
    data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.conversation_id)}">
    <span class="source-dot ${escapeHtml(item.source)}"></span>证据
  </button>`;
}

function summaryItemParts(item, tone) {
  const text = String(item?.text || "").trim();
  let title = text;
  let detail = "";
  let match;
  if (tone === "achievement") {
    match = text.match(/^围绕“(.+?)”.*?[：:](.+)$/);
    if (match) [title, detail] = [match[1], match[2]];
  } else if (tone === "unfinished") {
    match = text.match(/^“(.+?)”目前还没有完成/);
    if (match) title = match[1];
    detail = item.reason || "";
  } else if (tone === "decision") {
    match = text.match(/^关于“(.+?)”.*?[：:](.+)$/);
    if (match) [title, detail] = [match[1], match[2]];
  } else if (tone === "next") {
    title = "优先动作";
    detail = text;
  }
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
  $("#generateDailyButton").textContent = data.model_available ? "使用模型重新生成" : "按模板重新生成";
  $("#dailyBody").innerHTML = `
    <div class="daily-meta">
      <div class="daily-badges">${statusBadges.map((value, index) =>
        `<span class="daily-badge${index === 2 ? " stale" : ""}">${escapeHtml(value)}</span>`
      ).join("")}</div>
      <span class="muted">生成于 ${dateTime(data.generated_at)}</span>
    </div>
    ${dailySummaryCardHtml(data)}
    <div class="daily-report" id="dailyReport" ${state.dailyReportOpen ? "" : "hidden"}>
      ${dailyReportHtml(data)}
    </div>
  `;
  const brief = $("#findDailyBrief");
  if (brief) {
    const unfinished = (summary.unfinished || summary.ongoing || []).length;
    const focus = summary.main_focus?.[0]?.text || "今天没有识别到唯一主线";
    const achievement = summary.achievements?.[0];
    const unfinishedItem = (summary.unfinished || summary.ongoing || [])[0];
    const achievementParts = achievement ? summaryItemParts(achievement, "achievement") : null;
    const unfinishedParts = unfinishedItem ? summaryItemParts(unfinishedItem, "unfinished") : null;
    brief.innerHTML = `
      <div class="brief-label">
        <span>${escapeHtml(dayLabel(data.day))}</span>
        <strong>工作焦点</strong>
      </div>
      <div class="brief-copy">
        <h2>${escapeHtml(focus)}</h2>
        <div class="brief-points">
          <p class="done"><b>完成</b><span>${escapeHtml(
            achievementParts?.title || "暂无可核验成果"
          )}</span></p>
          <p class="open"><b>待继续</b><span>${escapeHtml(
            unfinishedParts?.title || "暂无明确遗留事项"
          )}</span></p>
        </div>
      </div>
      <div class="brief-actions">
        <span><b>${summary.achievements.length}</b> 完成 · <b>${unfinished}</b> 待继续</span>
        <button class="button secondary" type="button" data-open-daily>完整回顾</button>
      </div>
    `;
  }
  if (data.warning) showToast(data.warning);
}

async function loadDaily() {
  $("#reviewDate").value = state.dailyDate;
  $("#reviewDate").max = localDateIso();
  $("#nextDayButton").disabled = state.dailyDate >= localDateIso();
  renderDailyDateStrip();
  $("#dailyBody").innerHTML = `<div class="daily-loading">正在整理 ${escapeHtml(dayLabel(state.dailyDate))} 的对话…</div>`;
  const data = await api(`/api/daily?date=${encodeURIComponent(state.dailyDate)}`);
  renderDaily(data);
  syncUrl();
}

async function setDailyDate(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day > localDateIso()) return;
  state.dailyDate = day;
  state.dailyReportOpen = false;
  await loadDaily();
}

function renderSummaryConfig(config) {
  state.summaryConfig = config;
  state.summaryModels = Array.isArray(config.models) ? config.models : [];
  $("#summaryModelEnabled").checked = Boolean(config.enabled);
  $("#summaryProviderPreset").value = config.provider || "custom";
  updateSummaryPresetHint();
  $("#summaryApiUrl").value = config.api_url || "";
  $("#summaryModelName").value = config.model || "";
  $("#summaryFallbackModel").value = config.fallback_model || "";
  $("#summaryTemperature").value = config.temperature ?? 0.2;
  $("#summaryMaxTokens").value = config.max_tokens ?? 2400;
  $("#summaryTimeout").value = config.timeout ?? 120;
  $("#summaryApiKey").value = "";
  $("#clearSummaryApiKey").checked = false;
  const keyLabels = {
    dpapi: `已保存加密密钥 · ${config.secret_storage}`,
    keychain: `已保存钥匙串密钥 · ${config.secret_storage}`,
    environment: "密钥由进程环境变量提供",
    none: "未保存 API 密钥；本地无密钥接口可留空",
  };
  $("#summarySecretState").textContent = keyLabels[config.key_source] || keyLabels.none;
  $("#modelSecretStorage").textContent =
    `密钥使用 ${config.secret_storage} 保护，不会返回浏览器，也不会写入 sources.json。`;
  $("#summaryApiKey").placeholder = config.has_api_key
    ? "留空则保留现有密钥"
    : "本地接口可留空";
  $("#summaryConnectionBadge").textContent = config.has_api_key || config.api_url.includes("127.0.0.1")
    ? "配置已保存"
    : "需要密钥";
  $("#summaryConnectionBadge").classList.toggle("ok", Boolean(config.has_api_key));
  const isLocalEndpoint = /127\.0\.0\.1|localhost/.test(config.api_url || "");
  $("#summaryFreeQuotaTip").hidden = config.has_api_key || isLocalEndpoint;
  renderSummaryModelLibrary();
  const updated = config.models_updated_at
    ? `上次读取 ${dateTime(config.models_updated_at)}`
    : "尚未读取模型列表";
  $("#summaryModelCatalogState").textContent = state.summaryModels.length
    ? `${state.summaryModels.length} 个模型 · ${updated}`
    : updated;
  $("#summaryModelTestState").textContent = "";
}

async function loadSummaryConfig() {
  const config = await api("/api/summary-config");
  renderSummaryConfig(config);
  return config;
}

function summaryConfigPayload() {
  return {
    enabled: $("#summaryModelEnabled").checked,
    provider: $("#summaryProviderPreset").value,
    api_url: $("#summaryApiUrl").value.trim(),
    model: $("#summaryModelName").value.trim(),
    fallback_model: $("#summaryFallbackModel").value.trim(),
    temperature: Number($("#summaryTemperature").value),
    max_tokens: Number($("#summaryMaxTokens").value),
    timeout: Number($("#summaryTimeout").value),
    api_key: $("#summaryApiKey").value.trim(),
    clear_api_key: $("#clearSummaryApiKey").checked,
  };
}

function capabilityLabel(value) {
  return {
    text: "文本",
    reasoning: "推理",
    coding: "代码",
    vision: "视觉",
    embedding: "Embedding",
    rerank: "重排",
    image: "图片",
    video: "视频",
    audio: "音频",
  }[value] || value || "其他";
}

function renderSummaryModelLibrary() {
  const root = $("#summaryModelList");
  const query = ($("#summaryModelSearch").value || "").trim().toLocaleLowerCase();
  const capability = $("#summaryCapabilityFilter").value;
  const filtered = state.summaryModels.filter((item) => {
    const matchesQuery = !query || `${item.id} ${item.family}`.toLocaleLowerCase().includes(query);
    const matchesCapability = capability === "all"
      || (capability === "summary" && item.summary_compatible)
      || item.capability === capability;
    return matchesQuery && matchesCapability;
  });
  $("#summaryModelOptions").innerHTML = state.summaryModels
    .filter((item) => item.summary_compatible)
    .map((item) => `<option value="${escapeHtml(item.id)}"></option>`)
    .join("");
  if (!state.summaryModels.length) {
    root.innerHTML = `<p class="muted">连接服务后读取这把密钥实际可用的模型。</p>`;
    return;
  }
  if (!filtered.length) {
    root.innerHTML = `<p class="muted">当前筛选下没有模型。</p>`;
    return;
  }
  const current = $("#summaryModelName").value.trim();
  root.innerHTML = filtered.slice(0, 300).map((item) => `
    <button class="model-library-row${item.id === current ? " active" : ""}${item.summary_compatible ? "" : " incompatible"}"
      type="button" data-model="${escapeHtml(item.id)}" ${item.summary_compatible ? "" : "disabled"}>
      <span><strong>${escapeHtml(item.id)}</strong><small>${escapeHtml(item.family || "其他")}</small></span>
      <span class="model-capability">${escapeHtml(capabilityLabel(item.capability))}</span>
      <span>${item.summary_compatible ? "可用于摘要" : "不适用"}</span>
    </button>
  `).join("");
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
  $("#searchInput").value = (state.smartMode && state.smartInterp)
    ? (state.smartRaw || "")
    : state.query;
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
    loadUpdateConfig().catch((error) => showToast(error.message));
  }
  if (state.view === "project") {
    loadProjects().catch((error) => showToast(error.message));
  }
  if (state.view === "skills") {
    loadSkills().catch((error) => showToast(error.message));
  }
  if (state.view === "assets") {
    loadAssets().catch((error) => showToast(error.message));
  }
  if (state.view === "summaries") {
    loadSummaries().catch((error) => showToast(error.message));
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

function skillStatusLabel(value) {
  return {
    active: "正在使用",
    watching: "重点关注",
    needs_sync: "待同步",
    deprecated: "准备停用",
    "": "未设置",
  }[value || ""] || value;
}

function skillSourceKindLabel(value) {
  return {
    local: "本地安装",
    system: "内置",
    plugin: "插件提供",
    workspace: "工作区",
  }[value] || value;
}

function skillQueryString() {
  const filters = state.skillFilters;
  const params = new URLSearchParams({
    q: filters.query,
    agent: filters.agent,
    capability: filters.capability,
    status: filters.status,
    favorites: filters.favorites ? "1" : "0",
  });
  return params.toString();
}

async function loadSkills({ keepSelection = true } = {}) {
  $("#skillCatalogSummary").textContent = "正在发现本机 Skill…";
  const data = await api(`/api/skills?${skillQueryString()}`);
  state.skills = data.items || [];
  state.skillCounts = data.counts || {};
  state.skillCapabilities = data.capabilities || [];
  $("#skillTotalCount").textContent = data.counts?.all ?? data.total;
  $("#skillDriftCount").textContent = data.counts?.drift_groups ?? 0;
  $("#skillFavoriteCount").textContent = data.counts?.favorites ?? 0;
  $("#skillCatalogSummary").textContent =
    `${data.counts?.all || 0} 个 Skill · ${data.counts?.drift_groups || 0} 组跨 Agent 差异 · 原目录只读`;
  const capabilitySelect = $("#skillCapabilityFilter");
  const selectedCapability = state.skillFilters.capability;
  capabilitySelect.innerHTML = `<option value="all">全部功能</option>` + state.skillCapabilities.map((value) =>
    `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`
  ).join("");
  capabilitySelect.value = state.skillCapabilities.includes(selectedCapability)
    ? selectedCapability
    : "all";
  if (capabilitySelect.value !== selectedCapability) state.skillFilters.capability = "all";
  renderSkillList();
  const visible = state.skillFilters.driftOnly
    ? state.skills.filter((item) => item.drift)
    : state.skills;
  if (!keepSelection || !visible.some((item) => item.instance_id === state.selectedSkillId)) {
    state.selectedSkillId = visible[0]?.instance_id || "";
  }
  if (state.selectedSkillId) {
    await openSkillDetail(state.selectedSkillId);
  } else {
    $("#skillDetailPane").innerHTML = `
      <div class="empty-detail"><span class="empty-index">S</span>
      <h2>没有匹配的 Skill</h2><p>调整搜索、Agent、功能或状态筛选。</p></div>`;
  }
  return data;
}

function renderSkillList() {
  const root = $("#skillList");
  const values = state.skillFilters.driftOnly
    ? state.skills.filter((item) => item.drift)
    : state.skills;
  if (!values.length) {
    root.innerHTML = `<p class="muted skill-list-empty">当前条件下没有 Skill。</p>`;
    return;
  }
  root.innerHTML = values.map((item) => `
    <button class="skill-row${item.instance_id === state.selectedSkillId ? " active" : ""}"
      type="button" data-skill-id="${escapeHtml(item.instance_id)}">
      <span class="source-dot ${escapeHtml(item.agent)}"></span>
      <span class="skill-row-main">
        <strong>${escapeHtml(item.name)}</strong>
        <small>${escapeHtml(item.description || item.origin)}</small>
        <span>${(item.capabilities || []).map((value) =>
          `<i>${escapeHtml(value)}</i>`
        ).join("")}</span>
      </span>
      <span class="skill-row-meta">
        ${item.favorite ? `<b>★</b>` : ""}
        ${item.drift ? `<em>有差异</em>` : ""}
        <small>${escapeHtml(SOURCE_LABELS[item.agent] || item.agent)}</small>
      </span>
    </button>
  `).join("");
}

async function openSkillDetail(instanceId) {
  state.selectedSkillId = instanceId;
  renderSkillList();
  $("#skillDetailPane").innerHTML = `<div class="empty-detail"><h2>读取 Skill…</h2></div>`;
  try {
    state.skillDetail = await api(`/api/skill/${encodeURIComponent(instanceId)}`);
    renderSkillDetail(state.skillDetail);
  } catch (error) {
    $("#skillDetailPane").innerHTML = `
      <div class="empty-detail"><h2>读取失败</h2><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderSkillDetail(data) {
  const item = data.skill;
  const management = data.management || {};
  const copyStatus = data.copies.length > 1
    ? (data.copies_drift ? `${data.copies.length} 个副本存在差异` : `${data.copies.length} 个副本一致`)
    : "当前只有一个副本";
  const manualIds = new Set(data.manual_project_ids || []);
  $("#skillDetailPane").innerHTML = `
    <div class="skill-detail-inner">
      <header class="skill-detail-head">
        <div>
          <div class="skill-detail-kicker">
            <span class="source-badge ${escapeHtml(item.agent)}">${escapeHtml(SOURCE_LABELS[item.agent] || item.agent)}</span>
            <span>${escapeHtml(item.origin)}</span>
            <span>${escapeHtml(skillSourceKindLabel(item.source_kind))}</span>
          </div>
          <h2>${escapeHtml(item.name)}</h2>
          <p>${escapeHtml(item.description || "该 Skill 没有填写 description。")}</p>
        </div>
        <button id="skillFavoriteButton" class="favorite-button${management.favorite ? " active" : ""}" type="button"
          aria-label="收藏 Skill">${management.favorite ? "★" : "☆"}</button>
      </header>
      <div class="skill-capability-list">
        ${(item.capabilities || []).map((value) => `<span>${escapeHtml(value)}</span>`).join("")}
      </div>
      <div class="skill-detail-actions">
        <button id="revealSkillButton" class="button primary" type="button">打开 Skill 文件夹</button>
        <button id="copySkillPathButton" class="button secondary" type="button">复制路径</button>
      </div>

      <section class="skill-source-card">
        <h3>来源与版本</h3>
        <dl>
          <div><dt>安装来源</dt><dd>${escapeHtml(item.origin)}</dd></div>
          <div><dt>相对路径</dt><dd>${escapeHtml(item.relative_path)}</dd></div>
          <div><dt>最后修改</dt><dd>${dateTime(item.modified_at)}</dd></div>
          <div><dt>内容指纹</dt><dd><code>${escapeHtml(item.fingerprint.slice(0, 16))}</code></dd></div>
          <div><dt>纳入文件</dt><dd>${item.file_count} 个</dd></div>
        </dl>
      </section>

      <section class="skill-copy-section">
        <div class="section-heading"><h3>跨 Agent 副本</h3>
          <span class="${data.copies_drift ? "skill-warning" : "muted"}">${escapeHtml(copyStatus)}</span></div>
        <div class="skill-copy-list">
          ${data.copies.map((copy) => `
            <button type="button" data-copy-skill-id="${escapeHtml(copy.instance_id)}"
              class="${copy.instance_id === item.instance_id ? "current" : ""}">
              <span class="source-badge ${escapeHtml(copy.agent)}">${escapeHtml(SOURCE_LABELS[copy.agent] || copy.agent)}</span>
              <span><strong>${escapeHtml(copy.origin)}</strong><small>${dateTime(copy.modified_at)} · ${copy.file_count} 文件</small></span>
              <code>${escapeHtml(copy.fingerprint.slice(0, 10))}</code>
            </button>
          `).join("")}
        </div>
      </section>

      <section class="skill-project-section">
        <div class="section-heading"><h3>关联项目</h3><span class="muted">自动识别 + 人工锁定</span></div>
        <div class="skill-project-detected">
          ${(data.projects || []).map((project) => `
            <button type="button" data-skill-project="${escapeHtml(project.project_id)}">
              <strong>${escapeHtml(project.name)}</strong>
              <small>${project.origin === "manual" ? "人工关联" : `自动识别 ${Math.round(project.confidence * 100)}%`}</small>
            </button>
          `).join("") || `<p class="muted">暂未识别到关联项目。</p>`}
        </div>
        <details class="skill-project-editor">
          <summary>编辑人工项目关联</summary>
          <div id="skillProjectChoices">
            ${(data.all_projects || []).map((project) => `
              <label><input type="checkbox" value="${escapeHtml(project.id)}" ${manualIds.has(project.id) ? "checked" : ""}>
                ${escapeHtml(project.name)}</label>
            `).join("")}
          </div>
          <button id="saveSkillProjectsButton" class="button secondary" type="button">保存项目关联</button>
        </details>
      </section>

      <section class="skill-management-section">
        <div class="section-heading"><h3>我的管理信息</h3><span id="skillSaveState" class="muted"></span></div>
        <div class="skill-management-grid">
          <label><span>统一名称</span><input id="skillCanonicalName" type="text"
            value="${escapeHtml(management.canonical_name || item.name)}"
            placeholder="用于把不同 Agent 的同类 Skill 归为一组"></label>
          <label><span>状态</span><select id="skillManagementStatus">
            <option value="">未设置</option>
            <option value="active" ${management.status === "active" ? "selected" : ""}>正在使用</option>
            <option value="watching" ${management.status === "watching" ? "selected" : ""}>重点关注</option>
            <option value="needs_sync" ${management.status === "needs_sync" ? "selected" : ""}>待同步</option>
            <option value="deprecated" ${management.status === "deprecated" ? "selected" : ""}>准备停用</option>
          </select></label>
          <label class="wide"><span>标签</span><input id="skillManagementTags" type="text"
            value="${escapeHtml((management.tags || []).join(", "))}" placeholder="日报, 核心流程, 待核对"></label>
          <label class="wide"><span>备注</span><textarea id="skillManagementNote" rows="4"
            placeholder="记录用途、维护责任或同步注意事项…">${escapeHtml(management.note || "")}</textarea></label>
        </div>
        <button id="saveSkillManagementButton" class="button secondary" type="button">保存管理信息</button>
      </section>

      <section class="skill-structure-section">
        <details>
          <summary><strong>功能结构</strong><span>${data.sections.length} 个章节</span></summary>
          <ol>${data.sections.map((section) =>
            `<li class="level-${section.level}">${escapeHtml(section.title)}</li>`
          ).join("") || `<li>未识别到 Markdown 章节</li>`}</ol>
        </details>
        <details>
          <summary><strong>文件清单</strong><span>${data.files.length} 个安全元数据项</span></summary>
          <div class="skill-file-list">${data.files.map((file) => `
            <div><code>${escapeHtml(file.path)}</code><span>${file.size} B · ${dateTime(file.modified_at)}</span></div>
          `).join("")}</div>
        </details>
      </section>
    </div>`;

  const saveManagement = async () => {
    $("#skillSaveState").textContent = "保存中…";
    const result = await api("/api/skill/manage", {
      method: "POST",
      body: JSON.stringify({
        instance_id: item.instance_id,
        canonical_name: $("#skillCanonicalName").value.trim(),
        status: $("#skillManagementStatus").value,
        favorite: $("#skillFavoriteButton").classList.contains("active"),
        tags: $("#skillManagementTags").value,
        note: $("#skillManagementNote").value,
      }),
    });
    state.skillDetail = result;
    $("#skillSaveState").textContent = "已保存";
    await loadSkills();
  };
  $("#skillFavoriteButton").addEventListener("click", (event) => {
    event.currentTarget.classList.toggle("active");
    event.currentTarget.textContent = event.currentTarget.classList.contains("active") ? "★" : "☆";
    saveManagement().catch((error) => showToast(error.message));
  });
  $("#saveSkillManagementButton").addEventListener("click", () =>
    saveManagement().catch((error) => showToast(error.message))
  );
  $("#saveSkillProjectsButton").addEventListener("click", async () => {
    const projectIds = [...$("#skillProjectChoices").querySelectorAll('input:checked')].map((node) => node.value);
    const result = await api("/api/skill/projects", {
      method: "POST",
      body: JSON.stringify({ instance_id: item.instance_id, project_ids: projectIds }),
    });
    state.skillDetail = result;
    renderSkillDetail(result);
    showToast("Skill 项目关联已保存");
  });
  $("#revealSkillButton").addEventListener("click", () =>
    api("/api/skill/reveal", {
      method: "POST",
      body: JSON.stringify({ instance_id: item.instance_id }),
    }).catch((error) => showToast(error.message))
  );
  $("#copySkillPathButton").addEventListener("click", async () => {
    await navigator.clipboard.writeText(item.path);
    showToast("Skill 路径已复制");
  });
  $("#skillDetailPane").querySelectorAll("[data-copy-skill-id]").forEach((button) =>
    button.addEventListener("click", () => openSkillDetail(button.dataset.copySkillId))
  );
  $("#skillDetailPane").querySelectorAll("[data-skill-project]").forEach((button) =>
    button.addEventListener("click", async () => {
      setView("project");
      await loadProject(button.dataset.skillProject);
    })
  );
}

async function loadUpdateConfig() {
  const config = await api("/api/update");
  $("#updateManifestUrl").value = config.manifest_url || "";
  $("#updateAutoCheck").checked = Boolean(config.auto_check);
  $("#updateState").textContent = `当前版本 ${config.current_version}`;
}

async function saveUpdateConfig() {
  const result = await api("/api/update", {
    method: "POST",
    body: JSON.stringify({
      manifest_url: $("#updateManifestUrl").value.trim(),
      auto_check: $("#updateAutoCheck").checked,
    }),
  });
  $("#updateState").textContent = `已保存 · 当前版本 ${result.current_version}`;
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
    await Promise.all([loadSummary(), loadProjects(), loadDaily()]);
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

function projectStatusLabel(value) {
  return {
    active: "进行中",
    maintenance: "维护中",
    paused: "已暂停",
    done: "已完成",
    in_progress: "进行中",
  }[value] || value || "待确认";
}

function renderProjectList() {
  const query = ($("#projectSearchInput").value || "").trim().toLocaleLowerCase();
  const visible = state.projects.filter((project) =>
    !query || `${project.name} ${project.description || ""}`.toLocaleLowerCase().includes(query));
  const groups = [
    ["active", "进行中"],
    ["maintenance", "维护中"],
    ["pending", "待确认"],
    ["done", "已完成"],
  ];
  $("#projectList").innerHTML = groups.map(([key, label]) => {
    const rows = visible.filter((project) => {
      if (key === "pending") return project.pending_count > 0 && project.pending_count === project.conversation_count;
      if (project.pending_count > 0 && project.pending_count === project.conversation_count) return false;
      return project.status === key;
    });
    if (!rows.length) return "";
    return `<section class="project-list-group">
      <h3>${label} <span>${rows.length}</span></h3>
      ${rows.map((project) => `
        <button class="project-row${project.id === state.selectedProjectId ? " active" : ""}"
          type="button" data-project-id="${escapeHtml(project.id)}">
          <span class="project-state-dot ${escapeHtml(key)}"></span>
          <span><strong>${escapeHtml(project.name)}</strong><small>${dateTime(project.last_activity)}</small></span>
          <b>${project.conversation_count}</b>
        </button>
      `).join("")}
    </section>`;
  }).join("") || `<p class="muted project-list-empty">没有匹配项目。</p>`;
}

async function loadProjects() {
  const data = await api("/api/projects");
  state.projectCatalogRefreshedAt = data.refreshed_at || 0;
  state.projects = data.projects || [];
  populateProjectControls();
  $("#unassignedProjectCount").textContent = data.unassigned_count;
  $("#projectRefreshState").textContent = `共 ${state.projects.length} 个项目`;
  if (!state.selectedProjectId || !state.projects.some((project) => project.id === state.selectedProjectId)) {
    state.selectedProjectId = state.projects.find((project) => project.id === "ai-conversation-hub")?.id
      || state.projects[0]?.id
      || "";
  }
  renderProjectList();
  if (state.selectedProjectId) {
    await loadProject(state.selectedProjectId);
    prefetchProjectDetails();
  } else {
    $("#projectContent").hidden = true;
    $("#projectEmptyState").hidden = false;
  }
  syncUrl();
  return data;
}

function invalidateProjectCache() {
  state.projects = [];
  state.projectCache = {};
  state.projectPrefetchRun += 1;
  state.projectCatalogRefreshedAt = 0;
}

function renderProjectLoading(projectId) {
  const project = state.projects.find((item) => item.id === projectId);
  state.selectedProjectId = projectId;
  renderProjectList();
  $("#projectContent").hidden = true;
  $("#projectEmptyState").hidden = false;
  $("#projectEmptyState").innerHTML = `
    <div class="project-loading" role="status">
      <span></span>
      <h2>正在打开${project ? `「${escapeHtml(project.name)}」` : "项目"}</h2>
      <p>首次读取会整理摘要、版本与工作流；完成后再次切换会直接打开。</p>
    </div>`;
}

function applyProjectData(data, projectId) {
  state.projectData = data;
  state.selectedProjectId = data.project?.id || projectId;
  state.selectedMilestoneId = data.milestones.at(-1)?.id || "";
  renderProjectList();
  renderProjectData();
  const fileDetails = $("#projectFilesDetails");
  if (fileDetails?.open) {
    loadProjectFiles(state.selectedProjectId).catch((error) => {
      $("#projectFileScanState").textContent = error.message;
    });
  }
  syncUrl();
}

async function loadProject(projectId) {
  const requestId = ++state.projectLoadRequestId;
  const cached = state.projectCache[projectId];
  if (cached) {
    applyProjectData(cached, projectId);
    return cached;
  }
  renderProjectLoading(projectId);
  const data = await api(`/api/project/${encodeURIComponent(projectId)}`);
  state.projectCache[projectId] = data;
  if (requestId !== state.projectLoadRequestId) return data;
  applyProjectData(data, projectId);
  return data;
}

async function prefetchProjectDetails() {
  const run = ++state.projectPrefetchRun;
  const catalogVersion = state.projectCatalogRefreshedAt;
  const total = state.projects.length;
  let ready = Object.keys(state.projectCache).filter((id) =>
    state.projects.some((project) => project.id === id)).length;
  $("#projectRefreshState").textContent = `正在准备快速切换 ${ready}/${total}`;
  for (const project of state.projects) {
    if (
      run !== state.projectPrefetchRun
      || catalogVersion !== state.projectCatalogRefreshedAt
    ) return;
    if (project.id === state.selectedProjectId || state.projectCache[project.id]) continue;
    try {
      const data = await api(`/api/project/${encodeURIComponent(project.id)}`);
      if (
        run === state.projectPrefetchRun
        && catalogVersion === state.projectCatalogRefreshedAt
      ) {
        state.projectCache[project.id] = data;
        ready += 1;
        $("#projectRefreshState").textContent = `正在准备快速切换 ${ready}/${total}`;
      }
    } catch {
      // A failed background prefetch should not block normal project switching.
    }
  }
  if (
    run === state.projectPrefetchRun
    && catalogVersion === state.projectCatalogRefreshedAt
  ) {
    $("#projectRefreshState").textContent = `共 ${total} 个项目 · 切换已加速`;
  }
}

async function ensureProjectCatalog() {
  if (!state.projects.length) {
    const data = await api("/api/projects");
    state.projects = data.projects || [];
  }
  populateProjectControls();
  return state.projects;
}

function populateProjectControls() {
  const options = state.projects.map((project) =>
    `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)} · ${project.conversation_count}</option>`
  ).join("");
  const optional = `<option value="">全部项目</option>${options}`;
  const selected = state.selectedProjectId || state.projects[0]?.id || "";
  [
    ["#knowledgeProjectFilter", optional],
    ["#activityProjectFilter", optional],
    ["#exportProject", options],
    ["#contextProject", options],
    ["#classificationTargetProject", options],
  ].forEach(([selector, html]) => {
    const node = $(selector);
    if (!node) return;
    const previous = node.value;
    node.innerHTML = html;
    node.value = previous && [...node.options].some((item) => item.value === previous)
      ? previous
      : (["#knowledgeProjectFilter", "#activityProjectFilter"].includes(selector) ? "" : selected);
  });
  const merge = $("#mergeTargetProject");
  if (merge) {
    const previous = merge.value;
    merge.innerHTML = state.projects
      .filter((project) => project.id !== state.selectedProjectId)
      .map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`)
      .join("");
    if (previous && [...merge.options].some((item) => item.value === previous)) merge.value = previous;
  }
}

function knowledgeTypeLabel(value) {
  return {
    achievement: "成果",
    decision: "决策",
    task: "待办",
    project_state: "项目状态",
    method: "方法",
    fact: "事实",
    preference: "偏好",
  }[value] || value;
}

function renderKnowledgeList(data) {
  state.knowledge = data.items || [];
  $("#knowledgePendingCount").textContent = data.counts?.pending || 0;
  $("#knowledgeApprovedCount").textContent = data.counts?.approved || 0;
  $("#knowledgeList").innerHTML = state.knowledge.length ? state.knowledge.map((item) => `
    <article class="knowledge-item" data-knowledge-id="${escapeHtml(item.id)}">
      <div class="knowledge-item-head">
        <span class="knowledge-type">${escapeHtml(knowledgeTypeLabel(item.type))}</span>
        <span class="knowledge-status ${escapeHtml(item.effective_status || item.status)}">${
          {
            pending: "待审核", approved: "已确认", rejected: "已拒绝",
            superseded: "已替代", revoked: "已撤销", expired: "已过期",
          }[item.effective_status || item.status] || item.status
        }</span>
        ${item.open_conflict_count ? `<span class="knowledge-conflict-badge">${item.open_conflict_count} 个疑似冲突</span>` : ""}
        <span class="muted">r${item.revision_no || 0} · ${escapeHtml(item.project_name || "全局")} · ${Math.round((item.confidence || 0) * 100)}%</span>
      </div>
      <input class="knowledge-title-input" type="text" maxlength="180" value="${escapeHtml(item.title)}">
      <textarea class="knowledge-content-input" rows="4">${escapeHtml(item.content)}</textarea>
      <div class="knowledge-evidence">
        ${(item.evidence || []).map((evidence) => `
          <button type="button" data-source="${escapeHtml(evidence.source)}" data-id="${escapeHtml(evidence.conversation_id)}">
            证据 · ${escapeHtml(evidence.source)}:${escapeHtml(evidence.conversation_id.slice(0, 10))}
            · ${escapeHtml({
              unchecked: "未核验", linked: "已定位", verified: "已核验", missing: "已丢失",
            }[evidence.evidence_status] || evidence.evidence_status)}
          </button>
        `).join("") || `<span class="muted">这条候选来自摘要，暂无可定位证据。</span>`}
      </div>
      ${(item.relations || []).filter((relation) => relation.relation === "possible_conflict").map((relation) => `
        <div class="knowledge-conflict ${escapeHtml(relation.status)}">
          <span><strong>疑似冲突</strong> ${escapeHtml(relation.other_title || relation.other_id)}<small>${escapeHtml(relation.reason)}</small></span>
          ${relation.status === "open" ? `
            <button class="button ghost knowledge-relation-action" data-action="resolve"
              data-source-id="${escapeHtml(relation.source_knowledge_id)}"
              data-target-id="${escapeHtml(relation.target_knowledge_id)}" type="button">标记已处理</button>
            <button class="button ghost knowledge-relation-action" data-action="dismiss"
              data-source-id="${escapeHtml(relation.source_knowledge_id)}"
              data-target-id="${escapeHtml(relation.target_knowledge_id)}" type="button">不是冲突</button>
          ` : `<em>${relation.status === "resolved" ? "已处理" : "已忽略"}</em>`}
        </div>
      `).join("")}
      <div class="knowledge-lifecycle-fields">
        <label><span>有效至</span><input class="knowledge-expiry-input" type="date"
          value="${item.valid_until ? localDateIso(new Date(item.valid_until * 1000)) : ""}"></label>
        <label><span>敏感级别</span><select class="knowledge-sensitivity-select">
          ${[["normal", "普通"], ["sensitive", "敏感"], ["restricted", "严格限制"]].map(([value, label]) =>
            `<option value="${value}"${value === item.sensitivity ? " selected" : ""}>${label}</option>`
          ).join("")}
        </select></label>
      </div>
      <div class="knowledge-review-actions">
        <select class="knowledge-type-select">
          ${["achievement", "decision", "task", "project_state", "method", "fact", "preference"].map((value) =>
            `<option value="${value}"${value === item.type ? " selected" : ""}>${knowledgeTypeLabel(value)}</option>`
          ).join("")}
        </select>
        <select class="knowledge-scope-select">
          ${[
            ["project", "当前项目"], ["global", "全局"], ["workspace", "工作区"], ["agent", "指定 Agent"],
          ].map(([value, label]) => `<option value="${value}"${value === item.scope ? " selected" : ""}>${label}</option>`).join("")}
        </select>
        <button class="button ghost knowledge-history-button" type="button">历史</button>
        ${(item.evidence || []).length ? `<button class="button ghost knowledge-verify-button" type="button">核验证据</button>` : ""}
        ${item.effective_status !== "approved" ? `<button class="button primary knowledge-action" data-action="approve" type="button">确认</button>` : ""}
        ${item.effective_status === "approved" ? `<button class="button ghost knowledge-action" data-action="revoke" type="button">撤销</button>` : ""}
        ${item.status !== "rejected" ? `<button class="button ghost knowledge-action" data-action="reject" type="button">拒绝</button>` : ""}
        ${item.status !== "pending" ? `<button class="button ghost knowledge-action" data-action="restore" type="button">退回待审核</button>` : ""}
      </div>
    </article>
  `).join("") : `<div class="asset-empty"><strong>当前没有知识卡片</strong><p>选择项目和日期，从已有摘要中提取候选。</p></div>`;
}

async function loadKnowledge() {
  const status = $("#knowledgeStatusFilter")?.value || "pending";
  const projectId = $("#knowledgeProjectFilter")?.value || "";
  const params = new URLSearchParams({ status, project_id: projectId, limit: "200" });
  const data = await api(`/api/knowledge?${params}`);
  renderKnowledgeList(data);
}

async function openKnowledgeHistory(knowledgeId) {
  const dialog = $("#knowledgeHistoryDialog");
  $("#knowledgeHistoryBody").innerHTML = `<p class="muted">正在读取修订历史…</p>`;
  dialog.showModal();
  try {
    const data = await api(`/api/knowledge/history?id=${encodeURIComponent(knowledgeId)}`);
    const item = data.item;
    $("#knowledgeHistoryTitle").textContent = item.title;
    $("#knowledgeHistoryBody").innerHTML = `
      <section class="knowledge-audit-summary">
        <span>${escapeHtml(knowledgeTypeLabel(item.type))}</span>
        <strong>${escapeHtml(item.status)}</strong>
        <p>${escapeHtml(item.content)}</p>
        <small>${escapeHtml(item.scope)} · ${escapeHtml(item.project_id || "全局")} · 使用 ${item.usage_count || 0} 次</small>
      </section>
      <section>
        <h3>证据状态</h3>
        <div class="knowledge-audit-evidence">
          ${(data.evidence || []).map((evidence) => `
            <button type="button" data-source="${escapeHtml(evidence.source)}" data-id="${escapeHtml(evidence.conversation_id)}">
              <strong>${escapeHtml(evidence.evidence_status || "unchecked")}</strong>
              <span>${escapeHtml(evidence.source)}:${escapeHtml(evidence.conversation_id)}</span>
              <small>${escapeHtml(evidence.quote || "无摘录")}</small>
            </button>
          `).join("") || `<p class="muted">暂无可定位证据。</p>`}
        </div>
      </section>
      <section>
        <h3>关系与冲突</h3>
        <div class="knowledge-audit-relations">
          ${(data.relations || []).map((relation) => `
            <article><strong>${escapeHtml(relation.relation)} · ${escapeHtml(relation.status)}</strong>
              <p>${escapeHtml(relation.reason || "无附加说明")}</p></article>
          `).join("") || `<p class="muted">没有发现冲突或替代关系。</p>`}
        </div>
      </section>
      <section>
        <h3>不可变修订记录</h3>
        <div class="knowledge-revision-list">
          ${(data.revisions || []).map((revision) => `
            <article>
              <span>r${revision.revision_no}</span>
              <div><strong>${escapeHtml(revision.action)}</strong>
                <p>${escapeHtml(revision.snapshot?.title || "")}</p>
                <small>${dateTime(revision.changed_at)} · ${escapeHtml(revision.snapshot?.status || "")}</small>
              </div>
            </article>
          `).join("") || `<p class="muted">暂无历史修订。</p>`}
        </div>
      </section>
    `;
  } catch (error) {
    $("#knowledgeHistoryBody").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

async function loadAssets() {
  await ensureProjectCatalog();
  const today = localDateIso();
  ["#knowledgeDate", "#exportDate", "#contextDate"].forEach((selector) => {
    const node = $(selector);
    if (node && !node.value) node.value = today;
    if (node) node.max = today;
  });
  $("#exportProject").closest("label").hidden = $("#exportScope").value !== "project";
  $("#exportDate").closest("label").hidden = $("#exportScope").value !== "day";
  await loadKnowledge();
}

function exportPayload() {
  return {
    scope: $("#exportScope").value,
    project_id: $("#exportProject").value,
    day: $("#exportDate").value,
    format: $("#exportFormat").value,
    include_messages: $("#exportMessages").checked,
    include_notes: $("#exportNotes").checked,
    include_knowledge: $("#exportKnowledge").checked,
  };
}

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

async function generateContextPack() {
  const button = $("#generateContextPackButton");
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const result = await api("/api/context-pack", {
      method: "POST",
      body: JSON.stringify({
        project_id: $("#contextProject").value,
        destination: $("#contextDestination").value,
        day: $("#contextDate").value,
      }),
    });
    state.contextPack = result;
    $("#contextPackPreview").value = result.markdown;
    $("#contextPackMetrics").textContent =
      `${result.character_count} 字符 · 约 ${result.estimated_tokens} tokens · ${result.evidence.length} 条证据`;
    ["#copyContextPackButton", "#downloadContextPackButton", "#downloadContextJsonButton"]
      .forEach((selector) => { $(selector).disabled = false; });
    showToast("续接包已生成，请检查后再交给下一个 Agent");
  } finally {
    button.disabled = false;
    button.textContent = "生成并预览";
  }
}

function activityKindLabel(value) {
  return {
    export: "导出",
    context_pack: "续接包",
    knowledge_extract: "知识提取",
    knowledge_review: "知识审核",
    knowledge_conflict: "知识冲突",
    evidence_verify: "证据核验",
    project_assign: "项目归类",
    project_merge: "项目合并",
    project_root: "文件目录",
    file_scan: "文件扫描",
    artifact_pin: "成果确认",
    daily_summary: "每日摘要",
    project_summary: "项目摘要",
    conversation_note: "对话管理",
    project: "项目",
  }[value] || value;
}

function renderActivity(data) {
  state.activity = data;
  $("#activitySummary").textContent = `${data.runs?.length || 0} 条操作 · ${data.pinned_files?.length || 0} 个确认文件`;
  $("#activityRunList").innerHTML = (data.runs || []).length ? data.runs.map((run) => `
    <article class="activity-run ${escapeHtml(run.status)}">
      <span class="activity-kind">${escapeHtml(activityKindLabel(run.kind))}</span>
      <div>
        <strong>${escapeHtml(run.title)}</strong>
        <p>${escapeHtml(run.summary || run.error || "已记录安全元数据")}</p>
        <small>${dateTime(run.started_at)} · ${run.duration_ms} ms${run.model ? ` · ${escapeHtml(run.model)}` : ""}</small>
        ${(run.artifacts || []).map((artifact) => `
          <em>${escapeHtml(artifact.name)} · ${(artifact.size / 1024).toFixed(1)} KB · ${escapeHtml(artifact.kind)}</em>
        `).join("")}
      </div>
      <b>${run.status === "completed" ? "完成" : "失败"}</b>
    </article>
  `).join("") : `<div class="asset-empty"><strong>暂无操作记录</strong><p>生成摘要、导出或审核知识后会显示在这里。</p></div>`;
  const generatedArtifacts = (data.runs || []).flatMap((run) =>
    (run.artifacts || []).map((artifact) => ({ ...artifact, run_title: run.title })));
  const pinnedFiles = (data.pinned_files || []).map((file) => ({
    id: file.id,
    name: file.user_label || file.name,
    kind: `项目文件 · ${file.role}`,
    size: file.size,
    path: file.path,
  }));
  const artifacts = [...pinnedFiles, ...generatedArtifacts].slice(0, 100);
  $("#activityArtifactList").innerHTML = artifacts.length ? artifacts.map((artifact) => `
    <article class="activity-artifact">
      <span>${escapeHtml(artifact.kind)}</span>
      <strong>${escapeHtml(artifact.name)}</strong>
      <small>${artifact.path ? escapeHtml(artifact.path) : `${(artifact.size / 1024).toFixed(1)} KB · 仅保存元数据`}</small>
    </article>
  `).join("") : `<div class="asset-empty"><strong>暂无成果元数据</strong><p>导出、续接包及确认后的项目文件会列在这里。</p></div>`;
}

async function loadActivity() {
  const params = new URLSearchParams({
    project_id: $("#activityProjectFilter")?.value || "",
    kind: $("#activityKindFilter")?.value || "",
    limit: "120",
  });
  const data = await api(`/api/activity?${params}`);
  renderActivity(data);
}

function fileCategoryLabel(value) {
  return {
    code: "代码", document: "文档", image: "图片", data: "数据",
    archive: "压缩包", other: "其他",
  }[value] || value;
}

function renderProjectFiles(data) {
  state.projectFiles = data;
  const enabledRoots = (data.roots || []).filter((root) => root.enabled);
  $("#projectRootCandidates").innerHTML = (data.roots || []).length ? `
    <div class="project-root-head"><strong>项目目录</strong><span>先确认目录，才允许扫描</span></div>
    ${(data.roots || []).map((root) => `
      <label class="project-root-row">
        <input type="checkbox" data-root-id="${escapeHtml(root.id)}"${root.enabled ? " checked" : ""}>
        <span><strong>${escapeHtml(root.root_path)}</strong><small>${root.enabled ? "已确认，可读取文件元数据" : "来自项目对话工作目录，尚未启用"}</small></span>
      </label>
    `).join("")}
  ` : `<p class="muted">没有发现足够安全、足够具体的项目工作目录。</p>`;
  $("#refreshProjectFilesButton").disabled = !enabledRoots.length;
  const scan = data.scan;
  $("#projectFileScanState").textContent = scan
    ? `${dateTime(scan.finished_at)} · 访问 ${scan.visited_count} 项 · 返回 ${scan.returned_count} 个${scan.truncated ? " · 已达扫描上限" : ""}`
    : "尚未扫描；确认目录后手动刷新";
  $("#projectFilesList").innerHTML = (data.files || []).length ? (data.files || []).map((file) => `
    <article class="project-file-row${file.pinned ? " pinned" : ""}" data-file-id="${escapeHtml(file.id)}">
      <span class="project-file-kind">${escapeHtml(fileCategoryLabel(file.category))}</span>
      <div>
        <strong>${escapeHtml(file.user_label || file.name)}</strong>
        <small title="${escapeHtml(file.path)}">${escapeHtml(file.path)}</small>
        <em>${dateTime(file.modified_at)} · ${(file.size / 1024).toFixed(1)} KB · ${
          { new: "首次发现", modified: "本次有修改", seen: "未变化" }[file.change_state] || file.change_state
        }</em>
      </div>
      <select class="project-file-role" aria-label="成果类型">
        ${[["final", "最终成果"], ["support", "辅助文件"], ["reference", "参考资料"]].map(([value, label]) =>
          `<option value="${value}"${value === file.role ? " selected" : ""}>${label}</option>`
        ).join("")}
      </select>
      <button class="button ghost project-file-pin" type="button">${file.pinned ? "取消成果" : "标为成果"}</button>
      <button class="button ghost project-file-reveal" type="button">显示位置</button>
    </article>
  `).join("") : `<div class="asset-empty"><strong>暂无最近文件</strong><p>确认目录后点击“刷新最近文件”。系统只读取名称、时间和大小。</p></div>`;
}

async function loadProjectFiles(projectId = state.selectedProjectId) {
  if (!projectId) return;
  const data = await api(`/api/project/files?project_id=${encodeURIComponent(projectId)}&limit=200`);
  renderProjectFiles(data);
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

async function loadClassification(mode = $("#classificationMode").value) {
  const params = new URLSearchParams({ mode, limit: "500" });
  if (mode === "project") params.set("project_id", state.selectedProjectId);
  const data = await api(`/api/project/classification?${params}`);
  state.classificationItems = data.items || [];
  renderClassificationList();
}

async function openClassification(mode = "unassigned") {
  await ensureProjectCatalog();
  $("#classificationMode").value = mode;
  populateProjectControls();
  $("#classificationDialog").showModal();
  await loadClassification(mode);
}

function projectEvidenceButton(item, label = "证据") {
  if (!item?.source || !item?.conversation_id) return "";
  return `<button class="project-evidence-link" type="button"
    data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.conversation_id)}">${label}</button>`;
}

function projectSummaryCell(title, items, mode = "list") {
  const values = (items || []).slice(0, 1);
  if (!values.length) {
    return `<div class="project-daily-cell"><strong>${title}</strong><p class="muted">暂无明确记录</p></div>`;
  }
  return `<div class="project-daily-cell">
    <strong>${title}</strong>
    ${values.map((item) => `
      <div class="project-daily-value">
        <p>${escapeHtml(item.text)}</p>
        ${title === "未完成原因" && item.reason
          ? `<small><b>原因：</b>${escapeHtml(item.reason)}</small>` : ""}
        ${title === "明日第一步" && item.next_action
          ? `<small><b>后续：</b>${escapeHtml(item.next_action)}</small>` : ""}
        ${projectEvidenceButton(item)}
      </div>
    `).join("")}
  </div>`;
}

function projectStoryEvidence(items) {
  const linked = (items || []).filter((item) => item.source && item.conversation_id);
  if (!linked.length) return "";
  return `<div class="project-story-evidence">
    ${linked.map((item, index) => projectEvidenceButton(item, `证据 ${index + 1}`)).join("")}
  </div>`;
}

function projectStorySection(title, summaryText, items, tone = "") {
  return `<section class="project-story-section ${tone}">
    <h3>${escapeHtml(title)}</h3>
    <div>${readableParagraphsHtml(summaryText)}</div>
    ${projectStoryEvidence(items)}
  </section>`;
}

function projectDailyStory(summary) {
  return `<div class="project-summary-hierarchy">${summaryHierarchyHtml(summary, { project: true })}</div>`;
}

function renderProjectConfigAudit(audit) {
  const root = $("#projectConfigAudit");
  if (!audit) {
    $("#projectConfigSummary").textContent = "尚未完成配置对账";
    $("#projectConfigLogic").textContent = "";
    root.innerHTML = `<p class="muted">没有可显示的配置数据。</p>`;
    return;
  }
  const warningCount = audit.warning_count || 0;
  $("#projectConfigSummary").textContent = warningCount
    ? `${warningCount} 项需要核对 · ${audit.expected_agents.map((agent) => SOURCE_LABELS[agent] || agent).join(" / ")}`
    : `配置一致 · ${audit.expected_agents.map((agent) => SOURCE_LABELS[agent] || agent).join(" / ")}`;
  $("#projectConfigLogic").textContent = audit.logic || "";
  const statusLabel = {
    aligned: "一致",
    drift: "内容不同",
    missing: "一边缺失",
    single: "单 Agent 专用",
  };
  const skills = (audit.skills || []).map((skill) => `
    <article class="project-config-card ${escapeHtml(skill.status)}">
      <header>
        <strong>${escapeHtml(skill.name)}</strong>
        <span>${escapeHtml(statusLabel[skill.status] || skill.status)}</span>
      </header>
      <div class="project-config-versions">
        ${(skill.versions || []).map((version) => `
          <div>
            <span class="source-badge ${escapeHtml(version.agent)}">${escapeHtml(SOURCE_LABELS[version.agent] || version.agent)}</span>
            <code title="${escapeHtml(version.path)}">${escapeHtml(version.fingerprint.slice(0, 10))}</code>
            <time>${dateTime(version.modified_at)}</time>
            <small>${version.file_count} 个文件</small>
          </div>
        `).join("")}
        ${(skill.missing_agents || []).map((agent) => `
          <div class="missing">
            <span class="source-badge ${escapeHtml(agent)}">${escapeHtml(SOURCE_LABELS[agent] || agent)}</span>
            <b>未发现对应 Skill</b>
          </div>
        `).join("")}
      </div>
      ${skill.status === "drift" ? `<p>最近修改：${escapeHtml(SOURCE_LABELS[skill.newest_agent] || skill.newest_agent)} · ${dateTime(skill.newest_at)}</p>` : ""}
    </article>
  `).join("");
  const vaultRows = (audit.vaults || []).map((row) => `
    <div class="project-vault-row${!row.preferred ? " missing" : ""}">
      <span class="source-badge ${escapeHtml(row.agent)}">${escapeHtml(SOURCE_LABELS[row.agent] || row.agent)}</span>
      ${row.preferred
        ? `<code title="${escapeHtml(row.preferred.source || row.preferred.path)}">${escapeHtml(row.preferred.path)}</code>
           <small>${escapeHtml(row.preferred.origin)}</small>`
        : `<b>未识别到默认 Obsidian 仓库</b>`}
    </div>
  `).join("");
  root.innerHTML = `
    <div class="project-config-skills">
      ${skills || `<p class="muted">暂未从该项目识别到关联 Skill。</p>`}
    </div>
    <aside class="project-vault-audit${audit.vault_mismatch ? " mismatch" : ""}">
      <div>
        <strong>默认 Obsidian 仓库</strong>
        <span>${audit.vault_mismatch ? "路径不一致，需要核对" : "已按 Agent 对比"}</span>
      </div>
      ${vaultRows || `<p class="muted">没有发现知识库路径。</p>`}
    </aside>`;
}

function linesValue(values) {
  return (values || []).join("\n");
}

function renderProjectPlan(data) {
  const plan = data.project_plan || {};
  $("#projectPlanObjective").value = plan.objective || "";
  $("#projectPlanStage").value = plan.current_stage || "discover";
  $("#projectPlanNextAction").value = plan.next_action || "";
  $("#projectPlanSuccess").value = linesValue(plan.success_criteria);
  $("#projectPlanQuestions").value = linesValue(plan.open_questions);
  const generator = data.plan_generator === "model"
    ? `模型规划 · ${data.plan_model || ""}`
    : (data.plan_generator === "manual" ? "人工维护" : "新手基础模板");
  $("#projectPlanState").textContent = `${generator}${data.plan_stale ? " · 项目有新对话，建议更新" : ""}`;
  $("#projectPlanMilestones").innerHTML = (plan.milestones || []).map((item, index) => `
    <article class="project-plan-milestone" data-plan-index="${index}">
      <span class="plan-step">${String(index + 1).padStart(2, "0")}</span>
      <div>
        <input class="plan-title" type="text" maxlength="120" value="${escapeHtml(item.title || "")}" aria-label="里程碑名称">
        <textarea class="plan-outcome" rows="2" aria-label="预期结果">${escapeHtml(item.outcome || "")}</textarea>
        <textarea class="plan-acceptance" rows="2" aria-label="验收标准">${escapeHtml(item.acceptance || "")}</textarea>
        <input class="plan-dependencies" type="text" maxlength="240" value="${escapeHtml(item.dependencies || "")}" placeholder="依赖（可留空）">
      </div>
      <select class="plan-status" aria-label="里程碑状态">
        ${[["todo", "待开始"], ["in_progress", "进行中"], ["done", "已完成"], ["blocked", "受阻"]].map(([value, label]) =>
          `<option value="${value}"${value === item.status ? " selected" : ""}>${label}</option>`
        ).join("")}
      </select>
    </article>
  `).join("") || `<p class="muted">尚无里程碑，点击“让模型更新计划”生成。</p>`;
  $("#projectPlanRisks").innerHTML = (plan.risks || []).map((item) => `
    <div><strong>风险</strong><span>${escapeHtml(item.risk)}</span><small>${escapeHtml(item.mitigation || "尚未填写应对方式")}</small></div>
  `).join("") || `<p class="muted">当前没有记录明确风险。</p>`;
}

function collectProjectPlan() {
  const current = state.projectData?.project_plan || {};
  const values = (selector) => ($(selector).value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  return {
    ...current,
    objective: $("#projectPlanObjective").value.trim(),
    current_stage: $("#projectPlanStage").value,
    next_action: $("#projectPlanNextAction").value.trim(),
    success_criteria: values("#projectPlanSuccess"),
    open_questions: values("#projectPlanQuestions"),
    milestones: [...document.querySelectorAll(".project-plan-milestone")].map((node) => ({
      title: node.querySelector(".plan-title").value.trim(),
      outcome: node.querySelector(".plan-outcome").value.trim(),
      acceptance: node.querySelector(".plan-acceptance").value.trim(),
      dependencies: node.querySelector(".plan-dependencies").value.trim(),
      status: node.querySelector(".plan-status").value,
      target_date: current.milestones?.[Number(node.dataset.planIndex)]?.target_date || "",
    })),
  };
}

function renderProjectObsidian(data) {
  const config = data.obsidian || {};
  $("#obsidianEnabled").checked = Boolean(config.enabled);
  $("#obsidianVaultPath").value = config.vault_path || "";
  $("#obsidianSubfolder").value = config.subfolder || "AI 对话中心";
  $("#projectObsidianPolicy").textContent = config.policy || "";
  $("#projectObsidianState").textContent = config.error
    ? config.error
    : `${config.approved_count || 0} 条已审核 · ${config.exported_count || 0} 条已归档${
      config.valid ? (config.is_obsidian_vault ? " · 仓库有效" : " · 目录有效，未发现 .obsidian") : ""
    }`;
  $("#projectExportObsidianButton").disabled = !config.enabled || !config.valid || !(config.approved_count > 0);
}

function renderProjectData() {
  const data = state.projectData;
  if (!data) return;
  $("#projectEmptyState").hidden = true;
  $("#projectContent").hidden = false;
  $("#projectTitle").textContent = data.project.name;
  $("#projectMeta").textContent =
    `${projectStatusLabel(data.project.status)} · ${data.conversation_count} 个对话 · ${data.milestones.length} 个阶段`;
  $("#projectDailyDate").textContent = data.today;
  const generatorLabels = {
    model: `模型摘要 · ${data.today_model || ""}`,
    model_fallback: `备用模型摘要 · ${data.today_model || ""}`,
    rules_after_model_error: "模型失败 · 已回退模板摘要",
    rules: "模板摘要 · 本地规则",
  };
  $("#projectDailyGenerator").textContent =
    `${generatorLabels[data.today_generator] || "基础摘要"} · 每项可回到证据对话`;
  $("#generateProjectSummaryButton").textContent = data.today_model_available
    ? "使用模型生成"
    : "重新生成基础摘要";
  $("#projectPendingBadge").textContent = data.pending_count
    ? `${data.pending_count} 条待确认`
    : "归类已确认";
  const classification = data.classification || {};
  $("#projectClassificationSummary").textContent =
    `人工 ${classification.manual_count || 0} · 任务规则 ${classification.keyword_count || 0} · 原生项目仅作筛选`;
  $("#projectClassificationBody").className = "project-classification-body";
  $("#projectClassificationBody").innerHTML = `
    <div class="project-classification-stat"><strong>${classification.locked_count || 0}</strong><span>人工锁定，不会被覆盖</span></div>
    <div class="project-classification-stat"><strong>${classification.keyword_count || 0}</strong><span>规则与关键词识别</span></div>
    <div class="project-classification-stat"><strong>${data.pending_count || 0}</strong><span>仍需人工确认</span></div>
    <div class="project-classification-stat"><strong>${Math.round((classification.average_confidence || 0) * 100)}%</strong><span>平均归类置信度</span></div>
    <div class="project-source-coverage">
      <strong>跨 Agent 覆盖</strong>
      ${Object.entries(classification.sources || {}).map(([source, count]) =>
        `<span class="source-badge ${escapeHtml(source)}">${escapeHtml(source)}</span><b>${count}</b>`
      ).join("")}
    </div>
    <ol class="project-classification-logic">
      ${(classification.logic || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ol>`;
  renderProjectConfigAudit(data.config_audit);
  renderProjectPlan(data);
  renderProjectObsidian(data);
  const summary = data.today_summary;
  $("#projectDailyTemplate").innerHTML = projectDailyStory(summary);

  const columns = Math.max(1, data.milestones.length);
  const columnStyle = `--project-columns:${columns}`;
  $("#projectTimeline").style.setProperty("--project-columns", String(columns));
  $("#projectTimeline").innerHTML = data.milestones.length
    ? `<strong class="timeline-row-label">版本</strong>` + data.milestones.map((milestone, index) => `
      <button class="project-timeline-node${milestone.id === state.selectedMilestoneId ? " active" : ""}"
        type="button" data-milestone-id="${escapeHtml(milestone.id)}">
        <span class="timeline-node-mark"></span>
        <strong>${escapeHtml(milestone.version)}</strong>
        <small>${dateTime(milestone.occurred_at)}</small>
        ${index < data.milestones.length - 1 ? `<i></i>` : ""}
      </button>
    `).join("")
    : `<p class="muted">还没有可显示的版本节点。</p>`;

  const header = `<div class="workstream-row workstream-header" style="${columnStyle}">
    <strong>工作流</strong>
    ${data.milestones.map((milestone) => `<span>${escapeHtml(milestone.version)}</span>`).join("")}
  </div>`;
  const rows = data.workstreams.map((row) => `
    <div class="workstream-row" style="${columnStyle}">
      <strong>${escapeHtml(row.name)}</strong>
      ${row.cells.map((cell) => cell.title ? `
        <button type="button" class="workstream-cell ${escapeHtml(cell.status)}"
          data-milestone-id="${escapeHtml(cell.milestone_id)}"
          data-source="${escapeHtml(cell.source)}" data-id="${escapeHtml(cell.conversation_id)}">
          <span class="project-state-dot ${escapeHtml(cell.status)}"></span>
          <b>${escapeHtml(cell.title)}</b>
          ${cell.count > 1 ? `<small>${cell.count} 条变更</small>` : ""}
        </button>
      ` : `<span class="workstream-empty">—</span>`).join("")}
    </div>
  `).join("");
  $("#projectWorkstreams").style.setProperty("--project-columns", String(columns));
  $("#projectWorkstreams").innerHTML = data.milestones.length ? header + rows : "";
  renderProjectMilestone();
}

function projectRulePayload() {
  return {
    project_id: state.selectedProjectId,
    include_keywords: $("#projectRuleIncludes").value,
    exclude_keywords: $("#projectRuleExcludes").value,
    workspace_aliases: $("#projectRuleWorkspaces").value,
    path_patterns: $("#projectRulePaths").value,
    min_score: Number($("#projectRuleMinScore").value),
    enabled: $("#projectRuleEnabled").checked,
  };
}

function invalidateProjectRulePreview() {
  state.projectRulePreviewValid = false;
  $("#saveProjectRuleButton").disabled = true;
}

function openProjectRuleDialog() {
  if (!state.projectData) return;
  const rule = state.projectData.detection_rule || {};
  $("#projectRuleTitle").textContent = `${state.projectData.project.name} · 自动识别`;
  $("#projectRuleIncludes").value = (rule.include_keywords || []).join("\n");
  $("#projectRuleExcludes").value = (rule.exclude_keywords || []).join("\n");
  $("#projectRuleWorkspaces").value = (rule.workspace_aliases || []).join("\n");
  $("#projectRulePaths").value = (rule.path_patterns || []).join("\n");
  $("#projectRuleMinScore").value = Number(rule.min_score || 0.78).toFixed(2);
  $("#projectRuleEnabled").checked = rule.enabled !== false;
  $("#projectRulePreview").innerHTML =
    `<p class="muted">先预览规则会如何影响 Codex、Hermes 和 WorkBuddy，再决定是否应用。</p>`;
  invalidateProjectRulePreview();
  $("#projectRuleDialog").showModal();
}

function renderProjectRulePreview(data) {
  const preview = data.preview || {};
  const sources = preview.by_source || {};
  const samples = preview.samples || [];
  $("#projectRulePreview").innerHTML = `
    <div class="project-rule-preview-stats">
      <div><strong>${preview.matched_count || 0}</strong><span>预计归入</span></div>
      <div><strong>${preview.added_count || 0}</strong><span>从未归属新增</span></div>
      <div><strong>${preview.moved_count || 0}</strong><span>从其他项目移入</span></div>
      <div><strong>${preview.conflict_count || 0}</strong><span>规则冲突，暂不归类</span></div>
    </div>
    <p class="project-rule-source-line">
      ${Object.entries(SOURCE_LABELS).map(([source, label]) =>
        sources[source] ? `<span>${escapeHtml(label)} ${sources[source]}</span>` : ""
      ).join("")}
      ${preview.locked_skipped ? `<span>人工锁定保留 ${preview.locked_skipped}</span>` : ""}
    </p>
    ${samples.length ? `<div class="project-rule-samples">${samples.map((item) => `
      <div>
        <span class="source-badge ${escapeHtml(item.source)}">${escapeHtml(item.source)}</span>
        <strong>${escapeHtml(item.title || "未命名对话")}</strong>
        <small>${Math.round((item.confidence || 0) * 100)}% · ${escapeHtml(
          (item.evidence || []).map((value) => `${value.kind}：${value.keyword}`).join("；")
        )}</small>
      </div>`).join("")}</div>` : `<p class="muted">当前规则没有命中对话。</p>`}`;
}

function renderProjectMilestone() {
  const data = state.projectData;
  const milestone = data?.milestones.find((item) => item.id === state.selectedMilestoneId)
    || data?.milestones.at(-1);
  if (!milestone) {
    $("#projectDetailPane").innerHTML = `<div class="empty-detail"><h2>暂无版本</h2><p>随着项目对话增加，这里会自动形成阶段节点。</p></div>`;
    return;
  }
  state.selectedMilestoneId = milestone.id;
  document.querySelectorAll("[data-milestone-id]").forEach((node) =>
    node.classList.toggle("active", node.dataset.milestoneId === milestone.id));
  const firstStep = data.today_summary.first_step?.[0];
  $("#projectDetailPane").innerHTML = `
    <div class="project-detail-inner">
      <div class="project-detail-kicker">${escapeHtml(milestone.version)} · ${escapeHtml(projectStatusLabel(milestone.status))}</div>
      <h2>${escapeHtml(milestone.title)}</h2>
      <p class="project-detail-date">${dateTime(milestone.occurred_at)}</p>
      <section><h3>变更摘要</h3><p>${escapeHtml(milestone.summary || "暂无摘要")}</p></section>
      <section><div class="section-heading"><h3>关联对话</h3><span>${milestone.evidence.length}</span></div>
        <div class="project-evidence-list">${milestone.evidence.map((item) => `
          <button type="button" data-source="${escapeHtml(item.source)}" data-id="${escapeHtml(item.id)}">
            <span class="source-dot ${escapeHtml(item.source)}"></span>
            <span><strong>${escapeHtml(item.title)}</strong><small>${dateTime(item.updated_at)}</small></span>
          </button>
        `).join("") || `<p class="muted">暂无证据对话。</p>`}</div>
      </section>
      <section><h3>验证结果</h3><p>${milestone.status === "done" ? "该阶段已有完成记录；仍建议从证据对话核对实际执行结果。" : "该阶段仍在推进，尚未形成最终完成状态。"}</p></section>
      <section><h3>后续跟进</h3><p>${escapeHtml(firstStep?.text || "等待下一条明确行动记录。")}</p></section>
      ${milestone.evidence[0] ? `<button class="button primary project-open-evidence" type="button"
        data-source="${escapeHtml(milestone.evidence[0].source)}" data-id="${escapeHtml(milestone.evidence[0].id)}">打开关联对话</button>` : ""}
    </div>`;
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
  const populateProjects = (projects) => {
    projectSelect.innerHTML = `<option value="">未归属项目</option>` + projects.map((project) =>
      `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)} · ${project.conversation_count}</option>`
    ).join("");
    projectSelect.value = data.project_assignment?.project_id || "";
  };
  populateProjects(state.projects);
  if (!state.projects.length) {
    api("/api/projects").then((result) => {
      state.projects = result.projects || [];
      populateProjects(state.projects);
    }).catch(() => {});
  }
  projectSelect.addEventListener("change", async () => {
    projectSelect.disabled = true;
    try {
      await api("/api/project/assign", {
        method: "POST",
        body: JSON.stringify({
          source: item.source,
          conversation_id: item.id,
          project_id: projectSelect.value,
        }),
      });
      showToast(projectSelect.value ? "项目归属已确认并锁定" : "已移出项目");
      invalidateProjectCache();
    } catch (error) {
      showToast(error.message);
      projectSelect.value = data.project_assignment?.project_id || "";
    } finally {
      projectSelect.disabled = false;
    }
  });
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
    await saveDetail(fragment, item, true);
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
  fragment.querySelector(".save-note").addEventListener("click", () => saveDetail(fragment, item, false));
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

$("#openSetupButton").addEventListener("click", () => {
  loadSetupStatus({ open: true }).catch((error) => showToast(error.message));
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
    await Promise.all([loadSummary(), loadDaily(), loadConversations(), loadSourceHealth()]);
    showToast("数据源已验证并建立索引");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "验证并开始使用";
  }
});

$("#projectSearchInput").addEventListener("input", renderProjectList);

$("#projectList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-project-id]");
  if (button) loadProject(button.dataset.projectId).catch((error) => showToast(error.message));
});

$("#refreshProjectsButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "识别中…";
  try {
    await api("/api/projects/refresh", { method: "POST", body: "{}" });
    invalidateProjectCache();
    await loadProjects();
    showToast("项目归类与版本节点已更新");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "重新识别项目";
  }
});

$("#createProjectButton").addEventListener("click", async () => {
  const name = window.prompt("新项目名称");
  if (!name?.trim()) return;
  try {
    const result = await api("/api/project", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), status: "active" }),
    });
    state.selectedProjectId = result.project_id;
    invalidateProjectCache();
    await loadProjects();
    showToast("项目已创建");
  } catch (error) {
    showToast(error.message);
  }
});

$("#unassignedProjectRow").addEventListener("click", () => {
  openClassification("unassigned").catch((error) => showToast(error.message));
});

$("#manageProjectButton").addEventListener("click", () => {
  openClassification("project").catch((error) => showToast(error.message));
});

$("#projectRuleButton").addEventListener("click", openProjectRuleDialog);
$("#closeProjectRuleButton").addEventListener("click", () => $("#projectRuleDialog").close());
$("#refreshProjectConfigButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "对账中…";
  try {
    await loadProject(state.selectedProjectId);
    showToast("跨 Agent 配置对账已刷新");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "重新对账";
  }
});
$("#saveProjectPlanButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "保存中…";
  try {
    await api("/api/project/plan/save", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        plan: collectProjectPlan(),
        generator: "manual",
      }),
    });
    await loadProject(state.selectedProjectId);
    showToast("项目计划已保存");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "保存修改";
  }
});
$("#generateProjectPlanButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "规划中…";
  try {
    const result = await api("/api/project/plan/generate", {
      method: "POST",
      body: JSON.stringify({ project_id: state.selectedProjectId, use_model: true }),
    });
    await loadProject(state.selectedProjectId);
    showToast(result.warning || (result.generator === "model" ? "模型已更新项目计划" : "已生成新手基础计划"));
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "让模型更新计划";
  }
});
$("#saveObsidianConfigButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "验证中…";
  try {
    await api("/api/obsidian-config", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        enabled: $("#obsidianEnabled").checked,
        vault_path: $("#obsidianVaultPath").value.trim(),
        subfolder: $("#obsidianSubfolder").value.trim(),
      }),
    });
    await loadProject(state.selectedProjectId);
    showToast("Obsidian 归档设置已保存");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "保存并验证路径";
  }
});
$("#projectExtractKnowledgeButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "分析中…";
  try {
    const result = await api("/api/knowledge/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        day: state.projectData.today,
        use_model: true,
      }),
    });
    await loadProject(state.selectedProjectId);
    showToast(result.created ? `新增 ${result.created} 条待审核知识` : "模型摘要已分析，候选没有重复创建");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "模型分析今日对话";
  }
});
$("#projectReviewKnowledgeButton").addEventListener("click", async () => {
  const projectId = state.selectedProjectId;
  const day = state.projectData?.today || localDateIso();
  setView("assets");
  await ensureProjectCatalog();
  $("#knowledgeProjectFilter").value = projectId;
  $("#knowledgeDate").value = day;
  $("#knowledgeStatusFilter").value = "pending";
  await loadKnowledge();
  $("#knowledgeList").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("#projectExportObsidianButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "归档中…";
  try {
    const result = await api("/api/obsidian/export", {
      method: "POST",
      body: JSON.stringify({ project_id: state.selectedProjectId }),
    });
    await loadProject(state.selectedProjectId);
    showToast(`已登记 ${result.exported} 条知识，写入 ${result.written} 个文件`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = !state.projectData?.obsidian?.enabled
      || !state.projectData?.obsidian?.valid
      || !(state.projectData?.obsidian?.approved_count > 0);
    button.textContent = "导出已审核知识";
  }
});
$("#suggestProjectRuleButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const suggestion = await api("/api/project/rule/suggestions", {
      method: "POST",
      body: JSON.stringify({ project_id: state.selectedProjectId }),
    });
    $("#projectRuleIncludes").value = (suggestion.include_keywords || []).join("\n");
    $("#projectRuleExcludes").value = (suggestion.exclude_keywords || []).join("\n");
    $("#projectRuleWorkspaces").value = (suggestion.workspace_aliases || []).join("\n");
    $("#projectRulePaths").value = (suggestion.path_patterns || []).join("\n");
    $("#projectRulePreview").innerHTML = `<p>${escapeHtml(suggestion.rationale)}</p>
      <p class="muted">建议置信度 ${Math.round((suggestion.confidence || 0) * 100)}%；请先预览影响再保存。</p>`;
    invalidateProjectRulePreview();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "从已归类对话生成建议";
  }
});
[
  "#projectRuleIncludes",
  "#projectRuleExcludes",
  "#projectRuleWorkspaces",
  "#projectRulePaths",
  "#projectRuleMinScore",
  "#projectRuleEnabled",
].forEach((selector) => {
  $(selector).addEventListener("input", invalidateProjectRulePreview);
  $(selector).addEventListener("change", invalidateProjectRulePreview);
});
$("#previewProjectRuleButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "分析中…";
  try {
    const data = await api("/api/project/rule/preview", {
      method: "POST",
      body: JSON.stringify(projectRulePayload()),
    });
    renderProjectRulePreview(data);
    state.projectRulePreviewValid = true;
    $("#saveProjectRuleButton").disabled = false;
  } catch (error) {
    invalidateProjectRulePreview();
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "预览影响";
  }
});
$("#saveProjectRuleButton").addEventListener("click", async (event) => {
  if (!state.projectRulePreviewValid) {
    showToast("规则发生变化，请先重新预览");
    return;
  }
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "应用中…";
  try {
    await api("/api/project/rule", {
      method: "POST",
      body: JSON.stringify(projectRulePayload()),
    });
    invalidateProjectCache();
    await loadProjects();
    $("#projectRuleDialog").close();
    showToast("跨 Agent 项目识别规则已应用");
  } catch (error) {
    showToast(error.message);
  } finally {
    state.projectRulePreviewValid = false;
    button.disabled = true;
    button.textContent = "保存并应用";
  }
});

$("#projectContextPackButton").addEventListener("click", async () => {
  setView("assets");
  await ensureProjectCatalog();
  $("#contextProject").value = state.selectedProjectId;
  $("#contextPackDetails").open = true;
  $("#contextPackPreview").focus();
});

$("#closeClassificationButton").addEventListener("click", () => $("#classificationDialog").close());
$("#classificationMode").addEventListener("change", (event) => {
  loadClassification(event.target.value).catch((error) => showToast(error.message));
});
$("#classificationSearch").addEventListener("input", renderClassificationList);
$("#selectAllClassificationButton").addEventListener("click", () => {
  const boxes = [...$("#classificationList").querySelectorAll('input[type="checkbox"]')];
  const shouldSelect = boxes.some((box) => !box.checked);
  boxes.forEach((box) => { box.checked = shouldSelect; });
});
$("#assignClassificationButton").addEventListener("click", async (event) => {
  const conversations = [...$("#classificationList").querySelectorAll('input[type="checkbox"]:checked')]
    .map((box) => ({ source: box.dataset.source, id: box.dataset.id }));
  if (!conversations.length) {
    showToast("请先选择要归类的对话");
    return;
  }
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/project/assign-batch", {
      method: "POST",
      body: JSON.stringify({
        project_id: $("#classificationTargetProject").value,
        conversations,
      }),
    });
    invalidateProjectCache();
    await ensureProjectCatalog();
    await loadClassification($("#classificationMode").value);
    showToast(`已确认 ${conversations.length} 个对话的项目归属`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});
$("#mergeProjectButton").addEventListener("click", async (event) => {
  const target = $("#mergeTargetProject").value;
  if (!state.selectedProjectId || !target) {
    showToast("请选择合并目标项目");
    return;
  }
  const sourceName = state.projects.find((project) => project.id === state.selectedProjectId)?.name || "当前项目";
  const targetName = state.projects.find((project) => project.id === target)?.name || "目标项目";
  if (!window.confirm(`确认把「${sourceName}」合并到「${targetName}」？旧项目会保留为别名。`)) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/project/merge", {
      method: "POST",
      body: JSON.stringify({
        source_project_id: state.selectedProjectId,
        target_project_id: target,
      }),
    });
    state.selectedProjectId = target;
    invalidateProjectCache();
    $("#classificationDialog").close();
    await loadProjects();
    showToast("项目已合并，旧项目别名已保留");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#knowledgeProjectFilter").addEventListener("change", () => loadKnowledge().catch((error) => showToast(error.message)));
$("#knowledgeStatusFilter").addEventListener("change", () => loadKnowledge().catch((error) => showToast(error.message)));
$("#generateKnowledgeButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "提取中…";
  try {
    const result = await api("/api/knowledge/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: $("#knowledgeProjectFilter").value,
        day: $("#knowledgeDate").value,
      }),
    });
    $("#knowledgeStatusFilter").value = "pending";
    await loadKnowledge();
    showToast(result.created ? `新增 ${result.created} 条知识候选` : "候选已存在，没有重复创建");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "从摘要提取候选";
  }
});
$("#knowledgeList").addEventListener("click", async (event) => {
  const evidence = event.target.closest("[data-source][data-id]");
  if (evidence) {
    setView("find");
    await openDetail(evidence.dataset.source, evidence.dataset.id);
    return;
  }
  const historyButton = event.target.closest(".knowledge-history-button");
  if (historyButton) {
    const root = historyButton.closest(".knowledge-item");
    await openKnowledgeHistory(root.dataset.knowledgeId);
    return;
  }
  const verifyButton = event.target.closest(".knowledge-verify-button");
  if (verifyButton) {
    const root = verifyButton.closest(".knowledge-item");
    verifyButton.disabled = true;
    try {
      const result = await api("/api/knowledge/evidence/verify", {
        method: "POST",
        body: JSON.stringify({ knowledge_id: root.dataset.knowledgeId }),
      });
      await loadKnowledge();
      showToast(`证据核验完成：${Object.entries(result.counts).map(([key, value]) => `${key} ${value}`).join("，")}`);
    } catch (error) {
      showToast(error.message);
      verifyButton.disabled = false;
    }
    return;
  }
  const relationButton = event.target.closest(".knowledge-relation-action");
  if (relationButton) {
    relationButton.disabled = true;
    try {
      await api("/api/knowledge/relation", {
        method: "POST",
        body: JSON.stringify({
          source_knowledge_id: relationButton.dataset.sourceId,
          target_knowledge_id: relationButton.dataset.targetId,
          relation: "possible_conflict",
          action: relationButton.dataset.action,
        }),
      });
      await loadKnowledge();
      showToast("知识冲突状态已更新");
    } catch (error) {
      showToast(error.message);
      relationButton.disabled = false;
    }
    return;
  }
  const action = event.target.closest(".knowledge-action");
  if (!action) return;
  const root = action.closest(".knowledge-item");
  const currentItem = state.knowledge.find((item) => item.id === root.dataset.knowledgeId);
  if (action.dataset.action === "revoke" && !window.confirm("确认撤销这条长期知识？撤销后不会进入导出或续接包。")) return;
  action.disabled = true;
  try {
    await api("/api/knowledge/review", {
      method: "POST",
      body: JSON.stringify({
        id: root.dataset.knowledgeId,
        action: action.dataset.action,
        title: root.querySelector(".knowledge-title-input").value,
        content: root.querySelector(".knowledge-content-input").value,
        type: root.querySelector(".knowledge-type-select").value,
        scope: root.querySelector(".knowledge-scope-select").value,
        sensitivity: root.querySelector(".knowledge-sensitivity-select").value,
        valid_until: root.querySelector(".knowledge-expiry-input").value,
        expected_revision_no: currentItem?.revision_no ?? 0,
        project_id: currentItem?.project_id || $("#knowledgeProjectFilter").value,
      }),
    });
    await loadKnowledge();
    showToast(
      action.dataset.action === "approve"
        ? "知识已确认并写入新修订"
        : (action.dataset.action === "revoke" ? "知识已撤销，不再进入上下文" : "审核状态已更新")
    );
  } catch (error) {
    showToast(error.message);
    action.disabled = false;
  }
});

$("#exportScope").addEventListener("change", (event) => {
  $("#exportProject").closest("label").hidden = event.target.value !== "project";
  $("#exportDate").closest("label").hidden = event.target.value !== "day";
  state.exportResult = null;
  $("#downloadExportButton").disabled = true;
});
$("#previewExportButton").addEventListener("click", () => {
  previewExport().catch((error) => {
    $("#exportState").textContent = error.message;
    showToast(error.message);
  });
});
$("#downloadExportButton").addEventListener("click", () => {
  if (!state.exportResult) return;
  downloadText(state.exportResult.filename, state.exportResult.content, state.exportResult.mime);
  showToast("导出文件已下载");
});

$("#generateContextPackButton").addEventListener("click", () => {
  generateContextPack().catch((error) => showToast(error.message));
});
$("#copyContextPackButton").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#contextPackPreview").value);
  showToast("续接包已复制，可粘贴给下一个 Agent");
});
$("#downloadContextPackButton").addEventListener("click", () => {
  if (!state.contextPack) return;
  downloadText(state.contextPack.filename, $("#contextPackPreview").value, "text/markdown;charset=utf-8");
});
$("#downloadContextJsonButton").addEventListener("click", () => {
  if (!state.contextPack) return;
  downloadText(
    state.contextPack.filename.replace(/\.md$/i, ".json"),
    state.contextPack.json,
    "application/json;charset=utf-8",
  );
});

$("#activityProjectFilter").addEventListener("change", () => loadActivity().catch((error) => showToast(error.message)));
$("#activityKindFilter").addEventListener("change", () => loadActivity().catch((error) => showToast(error.message)));
$("#refreshActivityButton").addEventListener("click", () => loadActivity().catch((error) => showToast(error.message)));

$("#closeKnowledgeHistoryButton").addEventListener("click", () => $("#knowledgeHistoryDialog").close());
$("#knowledgeHistoryBody").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-source][data-id]");
  if (!button) return;
  $("#knowledgeHistoryDialog").close();
  setView("find");
  await openDetail(button.dataset.source, button.dataset.id);
});

$("#projectRootCandidates").addEventListener("change", async (event) => {
  const checkbox = event.target.closest("[data-root-id]");
  if (!checkbox) return;
  checkbox.disabled = true;
  try {
    const data = await api("/api/project/root/confirm", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        root_id: checkbox.dataset.rootId,
        enabled: checkbox.checked,
      }),
    });
    renderProjectFiles(data);
    showToast(checkbox.checked ? "项目目录已确认，可手动扫描" : "项目目录已停用");
  } catch (error) {
    checkbox.checked = !checkbox.checked;
    checkbox.disabled = false;
    showToast(error.message);
  }
});

$("#addProjectRootButton").addEventListener("click", async (event) => {
  const path = $("#projectRootPathInput").value.trim();
  if (!path) {
    showToast("请填写具体的项目目录");
    return;
  }
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const data = await api("/api/project/root/add", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        path,
        enabled: true,
      }),
    });
    $("#projectRootPathInput").value = "";
    renderProjectFiles(data);
    showToast("项目目录已添加；文件扫描仍需手动点击刷新");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
});

$("#refreshProjectFilesButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "扫描中…";
  $("#projectFileScanState").textContent = "正在读取安全文件元数据，最多 3 秒…";
  try {
    const data = await api("/api/project/files/refresh", {
      method: "POST",
      body: JSON.stringify({ project_id: state.selectedProjectId }),
    });
    renderProjectFiles(data);
    showToast(`已刷新 ${data.files.length} 个最近文件${data.scan?.truncated ? "，扫描达到安全上限" : ""}`);
  } catch (error) {
    showToast(error.message);
    $("#projectFileScanState").textContent = error.message;
  } finally {
    button.textContent = "刷新最近文件";
    button.disabled = !(state.projectFiles?.roots || []).some((root) => root.enabled);
  }
});

$("#projectFilesDetails").addEventListener("toggle", (event) => {
  if (!event.currentTarget.open || !state.selectedProjectId) return;
  loadProjectFiles(state.selectedProjectId).catch((error) => {
    $("#projectFileScanState").textContent = error.message;
  });
});

$("#activityDetails").addEventListener("toggle", (event) => {
  if (!event.currentTarget.open) return;
  loadActivity().catch((error) => showToast(error.message));
});

$("#projectFilesList").addEventListener("click", async (event) => {
  const root = event.target.closest("[data-file-id]");
  if (!root) return;
  const file = state.projectFiles?.files?.find((item) => item.id === root.dataset.fileId);
  if (!file) return;
  const pinButton = event.target.closest(".project-file-pin");
  const revealButton = event.target.closest(".project-file-reveal");
  if (pinButton) {
    pinButton.disabled = true;
    try {
      const data = await api("/api/project/file/pin", {
        method: "POST",
        body: JSON.stringify({
          project_id: state.selectedProjectId,
          file_id: file.id,
          pinned: !file.pinned,
          role: root.querySelector(".project-file-role").value,
          label: file.user_label || "",
        }),
      });
      renderProjectFiles(data);
      loadActivity().catch(() => {});
      showToast(file.pinned ? "已取消成果标记" : "文件已加入成果清单");
    } catch (error) {
      pinButton.disabled = false;
      showToast(error.message);
    }
  }
  if (revealButton) {
    revealButton.disabled = true;
    try {
      await api("/api/project/file/reveal", {
        method: "POST",
        body: JSON.stringify({ project_id: state.selectedProjectId, file_id: file.id }),
      });
      showToast("已在系统文件管理器中显示文件");
    } catch (error) {
      showToast(error.message);
    } finally {
      revealButton.disabled = false;
    }
  }
});

$("#projectTimeline").addEventListener("click", (event) => {
  const button = event.target.closest("[data-milestone-id]");
  if (!button) return;
  state.selectedMilestoneId = button.dataset.milestoneId;
  renderProjectMilestone();
});

$("#projectWorkstreams").addEventListener("click", (event) => {
  const button = event.target.closest("[data-milestone-id]");
  if (!button) return;
  state.selectedMilestoneId = button.dataset.milestoneId;
  renderProjectMilestone();
});

$("#projectDailyTemplate").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-source][data-id]");
  if (!button) return;
  setView("find");
  await openDetail(button.dataset.source, button.dataset.id);
});

$("#generateProjectSummaryButton").addEventListener("click", async (event) => {
  if (!state.projectData) return;
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "生成中…";
  try {
    const result = await api("/api/project/daily/generate", {
      method: "POST",
      body: JSON.stringify({
        project_id: state.selectedProjectId,
        day: state.projectData.today,
        use_model: Boolean(state.projectData.today_model_available),
      }),
    });
    state.projectData.today_summary = result.summary;
    state.projectData.today_generator = result.generator;
    state.projectData.today_model = result.model;
    state.projectData.today_generated_at = result.generated_at;
    renderProjectData();
    showToast(result.generator.startsWith("model") ? "项目摘要已由模型生成" : "项目基础摘要已重新生成");
    if (result.warning) showToast(result.warning);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = state.projectData?.today_model_available ? "使用模型生成" : "重新生成基础摘要";
  }
});

$("#projectDetailPane").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-source][data-id]");
  if (!button) return;
  setView("find");
  await openDetail(button.dataset.source, button.dataset.id);
});

$("#findDailyBrief").addEventListener("click", (event) => {
  if (event.target.closest("[data-open-daily]")) setView("daily");
});

$("#modelSettingsButton").addEventListener("click", async () => {
  const dialog = $("#modelSettingsDialog");
  dialog.showModal();
  $("#summaryModelTestState").textContent = "正在读取设置…";
  try {
    await loadSummaryConfig();
  } catch (error) {
    $("#summaryModelTestState").textContent = error.message;
  }
});

$("#closeModelSettingsButton").addEventListener("click", () => {
  $("#modelSettingsDialog").close();
});

$("#modelSettingsDialog").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) event.currentTarget.close();
});

const PRESET_URLS = {
  freellmapi: "http://127.0.0.1:3001/v1",
  agentrouter: "https://agentrouter.org/v1",
  paratera: "https://llmapi.paratera.com/v1",
  ollama: "http://127.0.0.1:11434/v1",
  lmstudio: "http://127.0.0.1:1234/v1",
  openai: "https://api.openai.com/v1",
};

const PRESET_HINTS = {
  freellmapi:
    '本地免费额度聚合：先安装 <a href="https://github.com/tashfeenahmed/freellmapi/releases/latest" target="_blank" rel="noopener">FreeLLMAPI</a>' +
    "（Windows 桌面 .exe 或 Docker），启动后打开 http://127.0.0.1:3001，在 Keys 页添加各家（Google/Groq 等）的免费额度密钥，" +
    "再把页面顶部的统一 API key 粘贴到上方 API 密钥。它聚合约 29 家免费额度（约 40 亿 token/月），自动路由并在限流时切换。" +
    "填好后点“读取模型列表”挑选模型。",
  agentrouter:
    '免费额度推荐：用 GitHub 登录 <a href="https://agentrouter.org" target="_blank" rel="noopener">agentrouter.org</a>，' +
    "在控制台 API Keys 页点 Create New Key，把生成的 sk- 密钥粘贴到上方 API 密钥；" +
    "新账号约有 $100 免费额度，足够日常日报与对话分析。填好后点“读取模型列表”挑选模型。",
  paratera: "填写 Paratera MaaS 提供的接口与密钥。",
  ollama: "本机 Ollama（默认端口 11434），无需密钥，需先在 Ollama 里拉取模型。",
  lmstudio: "本机 LM Studio（默认端口 1234），无需密钥，需在 LM Studio 里启动本地服务。",
  openai: "OpenAI 官方接口，需要 OpenAI API 密钥，按量计费。",
  custom: "填写任意 OpenAI Chat Completions 兼容接口地址与密钥。",
};

function updateSummaryPresetHint() {
  const preset = $("#summaryProviderPreset").value;
  const hint = $("#summaryPresetHint");
  if (!hint) return;
  hint.innerHTML = PRESET_HINTS[preset] || PRESET_HINTS.custom;
}

$("#summaryProviderPreset").addEventListener("change", (event) => {
  const preset = event.target.value;
  if (PRESET_URLS[preset]) $("#summaryApiUrl").value = PRESET_URLS[preset];
  updateSummaryPresetHint();
  state.summaryModels = [];
  $("#summaryModelCatalogState").textContent = "接口已改变，请重新读取模型列表";
  renderSummaryModelLibrary();
  if (preset !== "custom") $("#discoverSummaryModelsButton").focus();
});

$("#useFreeLLMAPIButton")?.addEventListener("click", () => {
  $("#summaryProviderPreset").value = "freellmapi";
  $("#summaryApiUrl").value = PRESET_URLS.freellmapi;
  updateSummaryPresetHint();
  state.summaryModels = [];
  $("#summaryModelCatalogState").textContent = "接口已改变，请粘贴密钥后读取模型列表";
  renderSummaryModelLibrary();
  $("#summaryFreeQuotaTip").hidden = true;
  $("#summaryApiKey").focus();
});

$("#discoverSummaryModelsButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const stateRoot = $("#summaryModelCatalogState");
  button.disabled = true;
  button.textContent = "读取中…";
  stateRoot.textContent = "正在读取账号可用模型…";
  try {
    const result = await api("/api/summary-config/models", {
      method: "POST",
      body: JSON.stringify(summaryConfigPayload()),
    });
    state.summaryModels = result.models || [];
    stateRoot.textContent = `${result.count} 个模型，其中 ${result.summary_compatible_count} 个可用于摘要 · ${result.elapsed_ms} ms`;
    $("#summaryConnectionBadge").textContent = "模型目录正常";
    $("#summaryConnectionBadge").classList.add("ok");
    renderSummaryModelLibrary();
    showToast(`已读取 ${result.count} 个账号可用模型`);
  } catch (error) {
    stateRoot.textContent = `读取失败：${error.message}`;
    $("#summaryConnectionBadge").textContent = "读取失败";
    $("#summaryConnectionBadge").classList.remove("ok");
  } finally {
    button.disabled = false;
    button.textContent = "刷新模型列表";
  }
});

$("#summaryModelSearch").addEventListener("input", renderSummaryModelLibrary);
$("#summaryCapabilityFilter").addEventListener("change", renderSummaryModelLibrary);
$("#summaryModelName").addEventListener("input", renderSummaryModelLibrary);
$("#summaryModelList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-model]");
  if (!button || button.disabled) return;
  $("#summaryModelName").value = button.dataset.model;
  renderSummaryModelLibrary();
});

$("#testSummaryModelButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const stateRoot = $("#summaryModelTestState");
  button.disabled = true;
  stateRoot.textContent = "正在发起最小测试请求…";
  try {
    const result = await api("/api/summary-config/test", {
      method: "POST",
      body: JSON.stringify(summaryConfigPayload()),
    });
    stateRoot.textContent = `连接成功 · ${result.model} · ${result.elapsed_ms} ms · 回复：${result.reply}`;
    $("#summaryConnectionBadge").textContent = "测试通过";
    $("#summaryConnectionBadge").classList.add("ok");
  } catch (error) {
    stateRoot.textContent = `连接失败：${error.message}`;
    $("#summaryConnectionBadge").textContent = "测试失败";
    $("#summaryConnectionBadge").classList.remove("ok");
  } finally {
    button.disabled = false;
  }
});

$("#saveSummaryModelButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const stateRoot = $("#summaryModelTestState");
  button.disabled = true;
  stateRoot.textContent = "正在保存…";
  try {
    const config = await api("/api/summary-config", {
      method: "POST",
      body: JSON.stringify(summaryConfigPayload()),
    });
    renderSummaryConfig(config);
    await loadDaily();
    stateRoot.textContent = config.enabled
      ? "设置已保存，日报可以主动调用该模型。"
      : "设置已保存，模型摘要目前处于关闭状态。";
    showToast("摘要模型设置已保存");
  } catch (error) {
    stateRoot.textContent = `保存失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
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
    invalidateProjectCache();
    await Promise.all([
      loadSummary(),
      loadConversations(),
      loadDaily(),
      state.view === "project" ? loadProjects() : Promise.resolve(),
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

$("#generateDailyButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "正在生成…";
  try {
    const data = await api("/api/daily/generate", {
      method: "POST",
      body: JSON.stringify({
        day: state.dailyDate,
        use_model: Boolean(state.daily?.model_available),
      }),
    });
    renderDaily(data);
    showToast(data.generator === "model" ? "模型摘要已生成" : "基础摘要已重新生成");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = state.daily?.model_available ? "使用模型重新生成" : "重新生成基础摘要";
  }
});

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

// ---- 全局搜索：自然语言 → 规范布尔检索式 ----

const SMART_TIME_RULES = [
  { re: /今\s*天|今\s*日/, range: "today" },
  { re: /昨\s*天|昨\s*日/, range: "3d" },
  { re: /近\s*3\s*天|最近\s*3\s*天|最近三天|近三天/, range: "3d" },
  { re: /上\s*周|近\s*7\s*天|近一周|最近一周|最近\s*7\s*天|最近七天|这\s*周|本\s*周/, range: "7d" },
  { re: /近\s*30\s*天|近一个月|最近一个月|最近\s*30\s*天|这\s*个\s*月|本\s*月/, range: "30d" },
];

const SMART_FILLERS = [
  "请帮我", "帮我找", "帮我搜", "帮我查", "给我找", "给我看", "帮我", "帮忙", "给我",
  "我想", "我要", "我需要", "找一下", "找找", "找下", "搜索一下", "搜一下", "搜索",
  "查找", "查一下", "查下", "看看", "看下", "看一看", "有没有", "有哪些", "有什么",
  "哪些", "哪个", "一下", "关于", "有关", "相关", "以及", "还有", "并且", "同时",
  "所有", "全部", "对话", "记录", "内容", "东西", "事情",
];

const SMART_SINGLE_STOP = new Set([
  "的", "了", "和", "跟", "与", "把", "将", "是", "在", "有", "个", "些", "这", "那",
  "我", "你", "他", "她", "它", "请", "帮", "找", "搜", "查", "看", "吗", "呢", "吧",
  "啊", "到", "给", "也", "都", "就", "还", "要", "会", "能", "需要", "可以",
]);

function looksLikeBoolean(text) {
  return /["“”()（）]/.test(text)
    || /(^|\s)(OR|AND|NOT)(\s|$)/i.test(text)
    || /(^|\s)-\S/.test(text);
}

function smartQuoteTerm(term) {
  return /^(AND|OR|NOT)$/i.test(term) ? `"${term}"` : term;
}

function interpretNaturalSearch(raw) {
  let text = ` ${raw.trim()} `;
  let range = "";
  const nots = [];

  for (const rule of SMART_TIME_RULES) {
    if (rule.re.test(text)) {
      range = rule.range;
      text = text.replace(rule.re, " ");
      break;
    }
  }

  text = text.replace(/(?:不含|不包含|不包括|排除|去掉)\s*([^\s，,。;；、和与跟或]+)/g, (match, term) => {
    const value = term.trim();
    if (value) nots.push(value);
    return " ";
  });

  text = text.replace(/或者|或|还是|、/g, " OR ");

  const fillers = [...SMART_FILLERS].sort((a, b) => b.length - a.length);
  for (const filler of fillers) text = text.split(filler).join(" ");
  text = text.replace(/[，,。！!？?；;：:、“”‘’"']/g, " ");

  const splitChars = "的了和跟与把将是在个些这那我你请帮吗呢吧啊到给也就还";
  const splitRe = new RegExp(`[\\s${splitChars}]+`);
  const tokens = text.split(splitRe).map((t) => t.trim()).filter(Boolean);
  const kept = tokens.filter(
    (t) => t.toUpperCase() === "OR" || !(t.length === 1 && SMART_SINGLE_STOP.has(t))
  );

  const segments = [];
  let segment = [];
  for (const token of kept) {
    if (token.toUpperCase() === "OR") {
      if (segment.length) { segments.push(segment); segment = []; }
    } else {
      segment.push(token);
    }
  }
  if (segment.length) segments.push(segment);

  let boolean;
  if (!segments.length) {
    boolean = "";
  } else if (segments.length === 1) {
    boolean = segments[0].map(smartQuoteTerm).join(" ");
  } else {
    boolean = segments
      .map((seg) => (seg.length > 1 ? `(${seg.map(smartQuoteTerm).join(" ")})` : smartQuoteTerm(seg[0])))
      .join(" OR ");
  }
  for (const n of nots) {
    boolean = `${boolean ? `${boolean} ` : ""}NOT ${smartQuoteTerm(n)}`;
  }

  const changed = Boolean(boolean) && (
    boolean.toLowerCase() !== raw.trim().toLowerCase() || Boolean(range) || nots.length > 0
  );
  return { boolean, range, nots, changed };
}

function applySmartSearch(rawValue) {
  const raw = rawValue.trim();
  state.smartRaw = raw;
  if (state.smartMode && raw && !looksLikeBoolean(raw)) {
    const interp = interpretNaturalSearch(raw);
    state.smartInterp = interp;
    state.query = interp.boolean || raw;
    // 仅当自然语言带时间词才改范围；否则保留用户手动选的范围，避免抹掉显式选择
    if (interp.range && VALID_RANGES.has(interp.range)) state.range = interp.range;
  } else {
    state.smartInterp = null;
    state.query = raw;
  }
  renderSmartSearchBar();
  resetAndLoad();
}

function renderSmartSearchBar() {
  const bar = $("#smartSearchBar");
  if (!bar) return;
  const interp = state.smartInterp;
  if (!state.smartMode || !interp || !interp.changed) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }
  bar.hidden = false;
  bar.innerHTML = `
    <span class="smart-bar-label">已理解为</span>
    <code class="smart-bar-query">${escapeHtml(interp.boolean)}</code>
    ${interp.range ? `<span class="smart-bar-range">时间：${escapeHtml(rangeLabel(interp.range))}</span>` : ""}
    <span class="smart-bar-actions">
      <button id="smartEditButton" class="button ghost" type="button">编辑检索式</button>
    </span>
  `;
  $("#smartEditButton").addEventListener("click", () => {
    state.smartMode = false;
    updateSmartToggleButton();
    state.query = interp.boolean;
    state.smartInterp = null;
    $("#searchInput").value = interp.boolean;
    renderSmartSearchBar();
    resetAndLoad();
  });
}

function updateSmartToggleButton() {
  const button = $("#smartSearchToggle");
  if (!button) return;
  button.classList.toggle("active", state.smartMode);
  button.setAttribute("aria-pressed", String(state.smartMode));
  button.title = state.smartMode ? "智能解析已开启：自然语言自动转布尔检索" : "智能解析已关闭：按原文检索";
}

$("#smartSearchToggle")?.addEventListener("click", () => {
  state.smartMode = !state.smartMode;
  updateSmartToggleButton();
  applySmartSearch($("#searchInput").value);
});

$("#searchInput").addEventListener("input", (event) => {
  event.target.removeAttribute("aria-invalid");
  clearTimeout(searchTimer);
  const value = event.target.value;
  searchTimer = setTimeout(() => applySmartSearch(value), 380);
});
$("#searchInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    clearTimeout(searchTimer);
    applySmartSearch(event.currentTarget.value);
  }
  if (event.key === "Escape" && event.currentTarget.value) {
    clearTimeout(searchTimer);
    event.currentTarget.value = "";
    state.query = "";
    state.smartRaw = "";
    state.smartInterp = null;
    renderSmartSearchBar();
    resetAndLoad();
  }
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
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
  $("#generateSummaryButton").disabled = count === 0 || count > 20;
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

$("#generateSummaryButton")?.addEventListener("click", openSummaryGenDialog);
$("#gotoSelectButton")?.addEventListener("click", () => setView("find"));
$("#closeSummaryGenButton")?.addEventListener("click", () => $("#summaryGenDialog").close());
$("#confirmSummaryGenButton")?.addEventListener("click", runSummaryGen);

function openSummaryGenDialog() {
  if (!state.checked.size) {
    showToast("请先在列表中勾选至少一个对话");
    return;
  }
  if (state.checked.size > 20) {
    showToast("一次最多分析 20 个对话");
    return;
  }
  $("#summaryGenMeta").textContent =
    `将对已选的 ${state.checked.size} 个对话生成内容分析，使用 设置 → 模型摘要 中配置的接口。`;
  $("#summaryGenFocus").value = "";
  $("#summaryGenName").value = "";
  $("#summaryGenState").textContent = "";
  $("#summaryGenDialog").showModal();
}

async function runSummaryGen() {
  const button = $("#confirmSummaryGenButton");
  button.disabled = true;
  $("#summaryGenState").textContent = "正在调用模型生成，可能需要十几秒到一分钟…";
  try {
    const conversations = [...state.checked.values()].map(({ source, id }) => ({ source, id }));
    const result = await api("/api/conversation-summary/generate", {
      method: "POST",
      body: JSON.stringify({
        conversations,
        focus: $("#summaryGenFocus").value.trim(),
        title: $("#summaryGenName").value.trim(),
      }),
    });
    $("#summaryGenDialog").close();
    state.checked.clear();
    renderList();
    updateSelectionBar();
    showToast("对话分析已生成并保存");
    setView("summaries");
    await loadSummaries();
    if (result.summary?.id) openSummary(result.summary.id);
  } catch (error) {
    $("#summaryGenState").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function loadSummaries() {
  const box = $("#summariesList");
  if (!box) return;
  box.innerHTML = `<span class="muted">正在读取…</span>`;
  try {
    const data = await api("/api/conversation-summaries");
    state.summaries = data.items || [];
    renderSummaries();
  } catch (error) {
    box.innerHTML = `<span class="muted">读取失败：${escapeHtml(error.message)}</span>`;
  }
}

function renderSummaries() {
  const box = $("#summariesList");
  if (!box) return;
  if (!state.summaries.length) {
    box.innerHTML = `<div class="summaries-empty"><p>还没有保存的对话分析。</p><p class="muted">去"找对话"勾选对话后点击"生成对话分析"。</p></div>`;
    return;
  }
  box.innerHTML = state.summaries.map((item) => `
    <button class="summary-row${item.id === state.currentSummaryId ? " active" : ""}"
      data-summary="${escapeHtml(item.id)}" type="button">
      <strong>${escapeHtml(item.title)}</strong>
      <small>${item.conversation_count} 个对话 · ${escapeHtml(item.model || "模型")} · ${dateTime(item.created_at)}</small>
    </button>
  `).join("");
}

$("#summariesList")?.addEventListener("click", (event) => {
  const row = event.target.closest(".summary-row");
  if (row) openSummary(row.dataset.summary);
});

async function openSummary(id) {
  state.currentSummaryId = id;
  renderSummaries();
  const pane = $("#summaryContent");
  pane.innerHTML = `<div class="empty-detail"><div><h2>正在读取…</h2></div></div>`;
  try {
    const data = await api(`/api/conversation-summary/${encodeURIComponent(id)}`);
    renderSummaryDetail(data);
  } catch (error) {
    pane.innerHTML = `<div class="empty-detail"><div><h2>读取失败</h2><p>${escapeHtml(error.message)}</p></div></div>`;
  }
}

function renderSummaryDetail(data) {
  const pane = $("#summaryContent");
  const refs = (data.source_refs || []).map((ref) => `
    <button class="summary-ref" data-source="${escapeHtml(ref.source)}"
      data-id="${escapeHtml(ref.conversation_id)}" type="button">
      <span class="source-dot ${escapeHtml(ref.source)}"></span>
      <span>${escapeHtml(ref.title || ref.conversation_id)}</span>
    </button>`).join("");
  pane.innerHTML = `
    <header class="summary-detail-head">
      <div>
        <p class="eyebrow">CONVERSATION ANALYSIS</p>
        <h2>${escapeHtml(data.title)}</h2>
        <p class="muted">${escapeHtml(data.model || "")} · 生成于 ${dateTime(data.created_at)}${
          data.focus ? ` · 重点：${escapeHtml(data.focus)}` : ""}</p>
      </div>
      <div class="summary-detail-actions">
        <button id="archiveSummaryButton" class="button ghost" type="button">归档</button>
      </div>
    </header>
    <div class="summary-md">${mdLite(data.content_md || "")}</div>
    <details class="summary-refs">
      <summary>来源对话（${(data.source_refs || []).length}）</summary>
      <div class="summary-ref-list">${refs}</div>
    </details>
  `;
  pane.scrollTop = 0;
  pane.querySelector("#archiveSummaryButton").addEventListener("click", async () => {
    if (!window.confirm("归档后这条分析将从列表隐藏（不会物理删除，数据仍保留）。确定归档？")) return;
    try {
      await api("/api/conversation-summary/archive", {
        method: "POST",
        body: JSON.stringify({ id: data.id }),
      });
      showToast("已归档");
      state.currentSummaryId = "";
      await loadSummaries();
      pane.innerHTML = `<div class="empty-detail"><div><h2>选择左侧的总结</h2></div></div>`;
    } catch (error) {
      showToast(error.message);
    }
  });
  pane.querySelectorAll(".summary-ref").forEach((el) => {
    el.addEventListener("click", () => {
      setView("find");
      openDetail(el.dataset.source, el.dataset.id);
    });
  });
}

function mdLite(md) {
  const esc = escapeHtml(String(md || ""));
  const lines = esc.split(/\r?\n/);
  const out = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  const inline = (text) => text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    let m;
    if ((m = line.match(/^###\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); }
    else if ((m = line.match(/^##\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = line.match(/^#\s+(.*)/))) { closeList(); out.push(`<h2>${inline(m[1])}</h2>`); }
    else if ((m = line.match(/^[-*]\s+(.*)/))) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (!line.trim()) {
      closeList();
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join("\n");
}

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
    await Promise.all([loadSummary(), loadConversations(), loadDaily()]);
    showToast("已从所有启用的数据来源重新读取");
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "刷新数据";
  }
});

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

$("#saveUpdateSettingsButton").addEventListener("click", () => {
  saveUpdateConfig().then(() => showToast("更新设置已保存")).catch((error) => showToast(error.message));
});

$("#checkUpdateButton").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "检查中…";
  try {
    const result = await api("/api/update/check", {
      method: "POST",
      body: JSON.stringify({ manifest_url: $("#updateManifestUrl").value.trim() }),
    });
    state.updateCandidate = result.available ? result : null;
    $("#updateState").innerHTML = result.available
      ? `发现 ${escapeHtml(result.version)}：${escapeHtml(result.notes || "有新版本")}
        <button id="downloadUpdateButton" class="button secondary" type="button">下载并校验</button>`
      : `已是最新版本 ${escapeHtml(result.current_version)}`;
    if (result.available) {
      $("#downloadUpdateButton").addEventListener("click", async () => {
        const downloaded = await api("/api/update/download", {
          method: "POST",
          body: JSON.stringify({ url: result.url, sha256: result.sha256 }),
        });
        $("#updateState").textContent = `${downloaded.message} 保存位置：${downloaded.path}`;
      });
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "检查更新";
  }
});

$("#skillList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-skill-id]");
  if (button) openSkillDetail(button.dataset.skillId);
});
$("#skillSearchInput").addEventListener("input", (event) => {
  clearTimeout(skillSearchTimer);
  skillSearchTimer = setTimeout(() => {
    state.skillFilters.query = event.target.value.trim();
    loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
  }, 280);
});
$("#skillAgentFilter").addEventListener("change", (event) => {
  state.skillFilters.agent = event.target.value;
  loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
});
$("#skillCapabilityFilter").addEventListener("change", (event) => {
  state.skillFilters.capability = event.target.value;
  loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
});
$("#skillStatusFilter").addEventListener("change", (event) => {
  state.skillFilters.status = event.target.value;
  loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
});
$("#skillFavoriteFilter").addEventListener("change", (event) => {
  state.skillFilters.favorites = event.target.checked;
  loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
});
document.querySelectorAll("[data-skill-stat]").forEach((button) => {
  button.addEventListener("click", () => {
    const value = button.dataset.skillStat;
    if (value === "all") {
      state.skillFilters = {
        query: "",
        agent: "all",
        capability: "all",
        status: "all",
        favorites: false,
        driftOnly: false,
      };
      $("#skillSearchInput").value = "";
      $("#skillAgentFilter").value = "all";
      $("#skillStatusFilter").value = "all";
      $("#skillFavoriteFilter").checked = false;
    } else if (value === "drift") {
      state.skillFilters.driftOnly = !state.skillFilters.driftOnly;
    } else if (value === "favorites") {
      state.skillFilters.favorites = true;
      state.skillFilters.driftOnly = false;
      $("#skillFavoriteFilter").checked = true;
    }
    document.querySelectorAll("[data-skill-stat]").forEach((candidate) => {
      candidate.classList.toggle(
        "active",
        (candidate.dataset.skillStat === "drift" && state.skillFilters.driftOnly)
          || (candidate.dataset.skillStat === "favorites" && state.skillFilters.favorites)
      );
    });
    loadSkills({ keepSelection: false }).catch((error) => showToast(error.message));
  });
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
  try {
    applyTheme(currentTheme(), { persist: false });
    initSidebarCollapse();
    updateSmartToggleButton();
    setDetailOpen(false);
    initDetailResizer();
    initProjectColumnResizers();
    initSourceDetails();
    $("#todayDate").textContent = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "long",
      day: "numeric",
      weekday: "short",
    }).format(new Date());
    renderSavedViews();
    state.token = (await api("/api/token")).token;
    await loadSetupStatus({ openIfRequired: true });
    readUrlState();
    setView(state.view, { sync: false });
    // Keep first paint fast: summary + conversations first; daily is lazy unless needed.
    await loadSummary();
    syncControls();
    await loadConversations();
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
