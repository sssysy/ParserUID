(function attachParserSettingsPage() {
    const API_PREFIX = "/api/parseruid/parser-settings";

  async function apiGet(endpoint) {
    if (!window.GsHubPlugin) {
      throw new Error("GsHubPlugin SDK 未加载");
    }
    const res = await window.GsHubPlugin.api(`${API_PREFIX}/${endpoint}`);
    if (res && typeof res.status === "number") {
      if (res.status !== 0) {
        throw new Error(res.msg || "请求失败");
      }
      return res.data;
    }
    return res;
  }

  async function apiPost(endpoint, body) {
    if (!window.GsHubPlugin) {
      throw new Error("GsHubPlugin SDK 未加载");
    }
    const res = await window.GsHubPlugin.api(`${API_PREFIX}/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res && typeof res.status === "number") {
      if (res.status !== 0) {
        throw new Error(res.msg || "请求失败");
      }
      return res.data;
    }
    return res;
  }

  const refs = {
    layout: document.getElementById("parser-layout"),
    parserSearch: document.getElementById("parser-search"),
    parserList: document.getElementById("parser-list"),
    parserDetail: document.getElementById("parser-detail"),
    skinList: document.getElementById("skin-list"),
    skinMessage: document.getElementById("skin-message"),
    snackbar: document.getElementById("snackbar"),
    snackbarMessage: document.getElementById("snackbar-message"),
    saveStatus: document.getElementById("save-status"),
    saveAll: document.getElementById("save-all"),
    discardAll: document.getElementById("discard-all"),
    accessLayout: document.getElementById("access-layout"),
    accessSearch: document.getElementById("access-search"),
    accessParserList: document.getElementById("access-parser-list"),
    accessDetail: document.getElementById("access-detail"),
    tabs: [...document.querySelectorAll("[data-tab]")],
    panels: {
      parsers: document.getElementById("panel-parsers"),
      skins: document.getElementById("panel-skins"),
      access: document.getElementById("panel-access"),
    },
  };

  const ICONS = {
    back: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.42-1.41L7.83 13H20v-2Z"/>
      </svg>`,
    eye: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="M12 5c-5.5 0-9.5 5.5-9.5 7s4 7 9.5 7 9.5-5.5 9.5-7-4-7-9.5-7Zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10Zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"/>
      </svg>`,
    eyeOff: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="m3.27 2 18.73 18.73-1.27 1.27L18.73 20H12C6.5 20 2.5 14.5 2.5 13c.45-.85 1.5-2.37 3.1-3.69L2 5.73 3.27 4.46 21.73 22.92l-1.27 1.27L18.73 22.46M12 7c5.5 0 9.5 5.5 9.5 7 0 .54-.8 1.88-2.2 3.12l-2.3-2.3A5 5 0 0 0 10.18 8.9L8.43 7.15A10.4 10.4 0 0 1 12 6.87M7.15 8.43l1.75 1.75A5 5 0 0 0 14.82 16l1.75 1.75A10.4 10.4 0 0 1 12 18.13c-5.5 0-9.5-5.5-9.5-7 0-.54.8-1.88 2.2-3.12Z"/>
      </svg>`,
    check: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="m9.55 17.55-5.1-5.1L6 10.9l3.55 3.55L18 6.9l1.55 1.55-9.99 9.1Z"/>
      </svg>`,
    arrowDown: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="m7 10 5 5 5-5H7Z"/>
      </svg>`,
    close: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="currentColor" d="m6.7 5.3 5.3 5.29 5.3-5.3 1.4 1.42-5.29 5.3 5.3 5.29-1.42 1.42-5.3-5.3-5.29 5.3-1.42-1.42 5.3-5.3-5.3-5.29 1.42-1.42Z"/>
      </svg>`,
  };

  const state = {
    parsers: [],
    selectedName: null,
    drafts: new Map(),
    query: "",
    loadError: "",
    saving: false,
    detailOpen: false,
    accessDetailOpen: false,
    accessQuery: "",
    listMotionKey: "",
    detailMotionKey: "",
    accessMotionKey: "",
  };

  let snackbarTimer = 0;

  function cloneValue(value) {
    return Array.isArray(value) ? [...value] : value;
  }

  function cloneValues(values) {
    return Object.fromEntries(
      Object.entries(values).map(([key, value]) => [key, cloneValue(value)]),
    );
  }

  function valueKey(value) {
    return JSON.stringify(value ?? null);
  }

  function createElement(tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function showSnackbar(message, duration = 3200) {
    window.clearTimeout(snackbarTimer);
    refs.snackbarMessage.textContent = message;
    refs.snackbar.classList.add("is-visible");
    snackbarTimer = window.setTimeout(() => {
      refs.snackbar.classList.remove("is-visible");
    }, duration);
  }

  function createDraft(parser) {
    const values = {};
    for (const field of parser.fields) {
      const saved = parser.values?.[field.key];
      values[field.key] = cloneValue(
        saved === undefined ? field.default : saved,
      );
    }
    return {
      enabled: Boolean(parser.enabled),
      baseEnabled: Boolean(parser.enabled),
      values,
      baseValues: cloneValues(values),
      clearedSecrets: new Set(),
    };
  }

  function getDraft(parser) {
    let draft = state.drafts.get(parser.name);
    if (!draft) {
      draft = createDraft(parser);
      state.drafts.set(parser.name, draft);
    }
    return draft;
  }

  function getChangedKeys(draft) {
    const changed = Object.keys(draft.values).filter(
      (key) => valueKey(draft.values[key]) !== valueKey(draft.baseValues[key]),
    );
    for (const key of draft.clearedSecrets) {
      if (!changed.includes(key)) changed.push(key);
    }
    return changed;
  }

  function isDraftDirty(draft) {
    return (
      draft.enabled !== draft.baseEnabled || getChangedKeys(draft).length > 0
    );
  }

  function isAccessDirty(parser) {
    return (
      parser.access_mode !== parser.base_access_mode ||
      valueKey(parser.whitelist) !== valueKey(parser.base_whitelist) ||
      valueKey(parser.blacklist) !== valueKey(parser.base_blacklist)
    );
  }

  function normalizeGroupIds(values) {
    if (!Array.isArray(values)) return [];
    return [...new Set(values.filter((value) => typeof value === "string" && /^\d+$/.test(value)))];
  }

  function currentParser() {
    return state.parsers.find((parser) => parser.name === state.selectedName);
  }

  function ensureSelection() {
    if (!state.parsers.length) {
      state.selectedName = null;
      return;
    }
    if (!state.parsers.some((parser) => parser.name === state.selectedName)) {
      const firstEnabled = state.parsers.find(
        (parser) => getDraft(parser).enabled,
      );
      state.selectedName = (firstEnabled || state.parsers[0]).name;
    }
  }

  function sortedParsers(parsers) {
    return [...parsers].sort((left, right) => {
      const enabledDelta =
        Number(getDraft(right).enabled) - Number(getDraft(left).enabled);
      if (enabledDelta) return enabledDelta;
      return left.display_name.localeCompare(right.display_name, "zh-CN");
    });
  }

  function dirtyEntries() {
    return state.parsers
      .map((parser) => ({ parser, draft: getDraft(parser) }))
      .filter(({ draft }) => isDraftDirty(draft));
  }

  function dirtyAccessEntries() {
    return state.parsers.filter(isAccessDirty);
  }

  function refreshGlobalSaveState() {
    const dirty = dirtyEntries();
    const dirtyAccess = dirtyAccessEntries();
    const count = dirty.length + dirtyAccess.length;
    refs.saveAll.disabled = !count || state.saving;
    refs.saveAll.textContent = state.saving ? "保存中" : "保存";
    refs.discardAll.hidden = !count || state.saving;
    refs.saveStatus.hidden = !count;
    refs.saveStatus.textContent = count
      ? `${count} 项设置未保存`
      : "";
    refs.saveStatus.classList.toggle("is-dirty", Boolean(count));
    for (const parser of state.parsers) {
      getDraft(parser);
    }
  }

  function renderParserList() {
    refs.parserList.replaceChildren();
    const query = state.query.trim().toLowerCase();
    const filtered = sortedParsers(
      state.parsers.filter(
        (parser) =>
          !query ||
          parser.name.toLowerCase().includes(query) ||
          parser.display_name.toLowerCase().includes(query),
      ),
    );
    const motionKey = `${query}|${filtered
      .map((parser) => `${parser.name}:${Number(getDraft(parser).enabled)}`)
      .join("|")}`;
    const animateItems = state.listMotionKey !== motionKey;
    state.listMotionKey = motionKey;

    const total = state.parsers.length;
    const enabled = state.parsers.filter(
      (parser) => getDraft(parser).enabled,
    ).length;
    document.getElementById("parser-count").textContent = state.loadError
      ? "加载失败"
      : `${enabled} / ${total} 已启用`;

    if (!filtered.length) {
      refs.parserList.append(
        createElement(
          "p",
          "empty-state",
          total ? "没有匹配的解析器。" : "没有可用的解析器。",
        ),
      );
      return;
    }

    filtered.forEach((parser, index) => {
      const draft = getDraft(parser);
      const item = createElement("button", "parser-item");
      item.type = "button";
      item.dataset.name = parser.name;
      item.setAttribute("role", "option");
      item.setAttribute(
        "aria-label",
        `${parser.display_name}，${draft.enabled ? "已启用" : "已停用"}`,
      );
      item.setAttribute(
        "aria-selected",
        parser.name === state.selectedName ? "true" : "false",
      );
      item.style.setProperty("--item-index", String(Math.min(index, 9)));
      if (animateItems) {
        item.classList.add("is-entering");
      }

      const copy = createElement("span", "parser-copy");
      const nameLine = createElement("span", "parser-name-line");
      const stateDot = createElement(
        "span",
        draft.enabled
          ? "parser-state-dot is-enabled"
          : "parser-state-dot is-disabled",
      );
      stateDot.setAttribute("aria-hidden", "true");
      nameLine.append(
        stateDot,
        createElement("span", "parser-name", parser.display_name),
      );
      copy.append(nameLine);
      item.append(copy);
      if (isDraftDirty(draft)) {
        item.append(createElement("span", "draft-badge"));
      }
      item.addEventListener("click", () => requestSelectParser(parser.name));
      item.addEventListener("animationend", () => {
        item.classList.remove("is-entering");
      });
      refs.parserList.append(item);
    });
  }

  function updateActiveRail() {
    updateParserActiveRail();
    updateAccessActiveRail();
  }

  function requestSelectParser(name) {
    selectParser(name);
  }

  function selectParser(name) {
    state.selectedName = name;
    state.detailOpen = true;
    refs.layout.classList.add("is-detail-open");
    updateActiveRail();
    renderDetail();
    renderAccessDetail();
    if (window.matchMedia("(max-width: 840px)").matches) {
      refs.parserDetail.scrollTop = 0;
    }
  }

  function closeDetail() {
    state.detailOpen = false;
    refs.layout.classList.remove("is-detail-open");
  }

  function closeAccessDetail() {
    state.accessDetailOpen = false;
    refs.accessLayout.classList.remove("is-detail-open");
  }

  function renderAccessParserList() {
    const query = state.accessQuery.trim().toLowerCase();
    const visible = state.parsers
      .filter(
        (parser) =>
          !query ||
          parser.name.toLowerCase().includes(query) ||
          parser.display_name.toLowerCase().includes(query),
      )
      .sort((left, right) =>
        left.display_name.localeCompare(right.display_name, "zh-CN"),
      );

    refs.accessParserList.replaceChildren();
    if (!visible.length) {
      refs.accessParserList.append(
        createElement(
          "p",
          "empty-state",
          state.parsers.length ? "没有匹配的解析器。" : "没有可用的解析器。",
        ),
      );
      return;
    }

    const motionKey = `${query}|${visible
      .map(
        (parser) =>
          `${parser.name}:${parser.access_mode}:${parser.whitelist.length}:${parser.blacklist.length}`,
      )
      .join("|")}`;
    const animateItems = state.accessMotionKey !== motionKey;
    state.accessMotionKey = motionKey;

    visible.forEach((parser, index) => {
      const item = createElement("button", "parser-item");
      item.type = "button";
      item.dataset.name = parser.name;
      item.setAttribute("role", "option");
      item.setAttribute(
        "aria-selected",
        parser.name === state.selectedName ? "true" : "false",
      );
      item.style.setProperty("--item-index", String(Math.min(index, 9)));
      if (animateItems) item.classList.add("is-entering");

      const copy = createElement("span", "parser-copy");
      copy.append(
        createElement("span", "parser-name", parser.display_name),
        createElement(
          "span",
          "parser-meta",
          `白名单 ${parser.whitelist.length} · 黑名单 ${parser.blacklist.length}${parser.access_mode === "white" && parser.whitelist.length === 0 ? " · 全部拦截" : ""}`,
        ),
      );
      item.append(copy);
      item.addEventListener("click", () => selectAccessParser(parser.name));
      item.addEventListener("animationend", () => {
        item.classList.remove("is-entering");
      });
      refs.accessParserList.append(item);
    });
  }

  function selectAccessParser(name) {
    state.selectedName = name;
    state.accessDetailOpen = true;
    refs.accessLayout.classList.add("is-detail-open");
    updateActiveRail();
    renderAccessDetail();
    if (window.matchMedia("(max-width: 840px)").matches) {
      refs.accessDetail.scrollTop = 0;
    }
  }

  function updateAccessActiveRail() {
    for (const item of refs.accessParserList.querySelectorAll(".parser-item")) {
      item.setAttribute(
        "aria-selected",
        item.dataset.name === state.selectedName ? "true" : "false",
      );
    }
    updateParserActiveRail();
  }

  function updateParserActiveRail() {
    for (const item of refs.parserList.querySelectorAll(".parser-item")) {
      item.setAttribute(
        "aria-selected",
        item.dataset.name === state.selectedName ? "true" : "false",
      );
    }
  }

  function createAccessModeRow(parser, kind) {
    const isWhite = kind === "white";
    const row = createElement("div", "setting-row setting-row--switch");
    const copy = createElement("div", "setting-copy");
    copy.append(
      createElement("span", "setting-label", isWhite ? "使用白名单" : "使用黑名单"),
      createElement(
        "p",
        "setting-hint",
        isWhite ? "开启后仅名单内的群聊可用。" : "开启后名单内的群聊不可用。",
      ),
    );
    const control = createSwitch(
      parser.access_mode === kind,
      (checked) => {
        if (checked) {
          parser.access_mode = kind;
        } else if (parser.access_mode === kind) {
          parser.access_mode = "none";
        }
        refreshAccessDirtyState();
        renderAccessDetail();
      },
      isWhite ? "使用白名单" : "使用黑名单",
    );
    row.append(copy, control);
    return row;
  }

  function createGroupList(parser, kind) {
    const listKey = kind === "white" ? "whitelist" : "blacklist";
    const list = createElement("div", "group-list");
    for (const groupId of parser[listKey]) {
      const chip = createElement("span", "group-chip");
      const label = createElement("span", "", groupId);
      const remove = createElement("button", "");
      remove.type = "button";
      remove.setAttribute("aria-label", `移除群号 ${groupId}`);
      remove.innerHTML = ICONS.close;
      remove.addEventListener("click", () => {
        parser[listKey] = parser[listKey].filter((item) => item !== groupId);
        refreshAccessDirtyState();
        renderAccessDetail();
      });
      chip.append(label, remove);
      list.append(chip);
    }
    return list;
  }

  function createAccessSection(parser, kind) {
    const isWhite = kind === "white";
    const listKey = isWhite ? "whitelist" : "blacklist";
    const section = createElement("section", "field-section");
    const header = createElement("header", "field-section__header group-section-head");
    header.append(createElement("h2", "", isWhite ? "白名单" : "黑名单"));
    const clear = createElement("button", "text-button text-button--danger", "清空");
    clear.type = "button";
    clear.disabled = parser[listKey].length === 0;
    clear.addEventListener("click", () => {
      parser[listKey] = [];
      refreshAccessDirtyState();
      renderAccessDetail();
    });
    header.append(clear);

    const row = createElement("div", "setting-row");
    const form = createElement("form", "group-add-row");
    const field = createElement("div", "filled-field");
    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.autocomplete = "off";
    input.id = `access-${parser.name}-${kind}`;
    input.setAttribute(
      "aria-label",
      `添加到${isWhite ? "白" : "黑"}名单的群号`,
    );
    const fieldLabel = document.createElement("label");
    fieldLabel.htmlFor = input.id;
    fieldLabel.textContent = "群号";
    field.append(fieldLabel, input);
    const add = createElement("button", "filled-button", "添加");
    add.type = "submit";
    form.append(field, add);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const groupId = input.value.trim();
      if (!/^\d+$/.test(groupId)) {
        showSnackbar("请输入有效的群号");
        input.focus();
        return;
      }
      if (parser[listKey].includes(groupId)) {
        showSnackbar(`${groupId} 已在名单中`);
        return;
      }
      parser[listKey] = [...parser[listKey], groupId];
      refreshAccessDirtyState();
      renderAccessDetail();
    });
    row.append(form, createGroupList(parser, kind));
    row.append(
      createElement(
        "p",
        "field-support",
        isWhite
          ? "填写群号，可通过命令 psr添加解析白名单{解析器} 来添加群聊"
          : "填写群号，可通过命令 psr添加解析黑名单{解析器} 来添加群聊",
      ),
    );
    section.append(header, row);
    return section;
  }

  function renderAccessDetail() {
    if (state.loadError) {
      refs.accessDetail.innerHTML = `<div class="empty-state"><p>${escapeHtml(state.loadError)}</p></div>`;
      return;
    }
    const parser = currentParser();
    if (!parser) {
      refs.accessDetail.innerHTML =
        '<div class="empty-state"><p>选择左侧的解析器以编辑名单。</p></div>';
      return;
    }

    if (parser.access_mode === "white" && parser.whitelist.length === 0) {
      showSnackbar("白名单已开启但为空，当前所有群聊都会被拦截", 4200);
    }

    refs.accessDetail.innerHTML = `
      <div class="detail-shell">
        <header class="detail-header">
          <div class="detail-title-row">
            <button
              class="icon-button back-button"
              id="access-back"
              type="button"
              aria-label="返回解析器列表"
              title="返回"
            >
              ${ICONS.back}
            </button>
            <div class="detail-heading">
              <h1>${escapeHtml(parser.display_name)}</h1>
              <p>${escapeHtml(parser.name)}</p>
            </div>
          </div>
        </header>
        <div class="detail-body"></div>
      </div>`;
    refs.accessDetail
      .querySelector("#access-back")
      .addEventListener("click", closeAccessDetail);
    const body = refs.accessDetail.querySelector(".detail-body");
    const modes = createElement("section", "field-section");
    modes.append(
      createAccessModeRow(parser, "white"),
      createAccessModeRow(parser, "black"),
    );
    body.append(
      modes,
      createAccessSection(parser, "white"),
      createAccessSection(parser, "black"),
    );
  }

  function createSwitch(checked, onChange, label) {
    const wrapper = createElement("span", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.setAttribute("aria-label", label);
    input.addEventListener("change", () => onChange(input.checked));
    const track = createElement("span", "switch-track");
    wrapper.append(input, track);
    return wrapper;
  }

  function makeFilledInput({
    id,
    type = "text",
    label,
    value,
    onInput,
    step,
  }) {
    const wrapper = createElement("div", "filled-field");
    const input = document.createElement("input");
    input.id = id;
    input.type = type;
    input.value = value;
    input.autocomplete = "off";
    if (step) input.step = step;
    input.addEventListener("input", () => onInput(input.value));
    const fieldLabel = createElement("label", "", label);
    fieldLabel.htmlFor = id;
    wrapper.append(fieldLabel, input);
    return { wrapper, input };
  }

  function makeFilledSelect({ id, label, options, value, onChange }) {
    const wrapper = createElement("div", "filled-select");
    const trigger = createElement("button", "select-trigger");
    trigger.type = "button";
    trigger.id = id;
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    const fieldLabel = createElement("span", "select-label", label);
    const selectValue = createElement("span", "select-value", value);
    const arrow = createElement("span", "select-arrow");
    arrow.innerHTML = ICONS.arrowDown;
    trigger.append(fieldLabel, selectValue, arrow);
    wrapper.append(trigger);

    const menu = createElement("div", "select-menu");
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    const focusables = [];
    let selectedValue = value;

    function closeMenu({ restoreFocus = false } = {}) {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      wrapper.classList.remove("is-open");
      refs.parserDetail.classList.remove("has-open-select");
      if (restoreFocus) trigger.focus();
    }

    function openMenu() {
      for (const other of document.querySelectorAll(".filled-select.is-open")) {
        if (other !== wrapper) {
          other.dispatchEvent(new CustomEvent("close-select"));
        }
      }
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      wrapper.classList.add("is-open");
      refs.parserDetail.classList.add("has-open-select");
      const rect = trigger.getBoundingClientRect();
      const estimatedHeight = Math.min(options.length * 48 + 16, 360);
      menu.classList.toggle(
        "is-above",
        rect.bottom + estimatedHeight > window.innerHeight - 8 &&
          rect.top > window.innerHeight - rect.bottom,
      );
      const selected = menu.querySelector('[aria-selected="true"]');
      (selected || focusables[0])?.focus();
    }

    for (const optionValue of options) {
      const option = createElement("button", "select-option");
      option.type = "button";
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", optionValue === selectedValue ? "true" : "false");
      option.dataset.value = optionValue;
      const check = createElement("span", "select-option__check");
      check.innerHTML = ICONS.check;
      option.append(createElement("span", "", optionValue), check);
      option.addEventListener("click", () => {
        selectedValue = optionValue;
        selectValue.textContent = optionValue;
        for (const item of menu.querySelectorAll(".select-option")) {
          item.setAttribute(
            "aria-selected",
            item.dataset.value === selectedValue ? "true" : "false",
          );
        }
        closeMenu({ restoreFocus: true });
        onChange(selectedValue);
      });
      option.addEventListener("keydown", (event) => {
        const index = focusables.indexOf(option);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const direction = event.key === "ArrowDown" ? 1 : -1;
          focusables[(index + direction + focusables.length) % focusables.length]?.focus();
        } else if (event.key === "Escape") {
          event.preventDefault();
          closeMenu({ restoreFocus: true });
        }
      });
      focusables.push(option);
      menu.append(option);
    }

    wrapper.addEventListener("close-select", () => closeMenu());
    trigger.addEventListener("click", () => {
      if (menu.hidden) openMenu();
      else closeMenu();
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        openMenu();
      } else if (event.key === "Escape") {
        closeMenu();
      }
    });
    trigger.addEventListener("blur", () => {
      window.setTimeout(() => {
        if (!wrapper.contains(document.activeElement) && !menu.hidden) {
          closeMenu();
        }
      }, 80);
    });
    wrapper.append(menu);
    return { wrapper, trigger, closeMenu };
  }

  function makeFilledTextarea({ id, label, value, onInput }) {
    const wrapper = createElement("div", "filled-textarea");
    const inner = createElement("div", "filled-textarea__inner");
    const textarea = document.createElement("textarea");
    textarea.id = id;
    textarea.value = value;
    textarea.rows = 3;
    textarea.addEventListener("input", () => onInput(textarea.value));
    const fieldLabel = createElement("label", "", label);
    fieldLabel.htmlFor = id;
    inner.append(fieldLabel, textarea);
    wrapper.append(inner);
    return { wrapper, textarea };
  }

  function renderDetail() {
    refs.parserDetail.setAttribute("aria-busy", "false");
    if (state.loadError) {
      refs.parserDetail.innerHTML = `
        <div class="empty-state">
          <p>${escapeHtml(state.loadError)}</p>
        </div>`;
      return;
    }

    const parser = currentParser();
    if (!parser) {
      refs.parserDetail.innerHTML = `
        <div class="empty-state">
          <p>选择左侧的解析器以编辑配置。</p>
        </div>`;
      return;
    }

    const draft = getDraft(parser);
    const regularFields = parser.fields.filter((field) => !field.secret);
    const secretFields = parser.fields.filter((field) => field.secret);
    const dirty = isDraftDirty(draft);
    const animateDetail = state.detailMotionKey !== parser.name;
    state.detailMotionKey = parser.name;

    refs.parserDetail.innerHTML = `
      <div class="detail-shell ${animateDetail ? "is-entering" : ""}">
        <header class="detail-header">
          <div class="detail-title-row">
            <button
              class="icon-button back-button"
              id="detail-back"
              type="button"
              aria-label="返回解析器列表"
              title="返回"
            >
              ${ICONS.back}
            </button>
            <div class="detail-heading">
              <h1>${escapeHtml(parser.display_name)}</h1>
              <p>${escapeHtml(parser.name)}</p>
            </div>
            <span class="status-chip ${
              draft.enabled ? "status-chip--enabled" : ""
            }">
              ${draft.enabled ? "已启用" : "已停用"}
            </span>
          </div>

          <div class="enabled-row">
            <label class="enabled-copy" for="parser-enabled">
              <strong>启用解析器</strong>
              <span>关闭后不再处理该平台的链接。</span>
            </label>
            <span class="switch">
              <input
                id="parser-enabled"
                type="checkbox"
                ${draft.enabled ? "checked" : ""}
                aria-label="启用 ${escapeHtml(parser.display_name)}"
              />
              <span class="switch-track"></span>
            </span>
          </div>
        </header>

        <div class="detail-body">
          <section class="field-section">
            <header class="field-section__header">
              <h2>解析偏好</h2>
              <p>控制媒体输出和平台行为。</p>
            </header>
            <div class="js-regular-fields"></div>
          </section>

          ${
            secretFields.length
              ? `<section class="field-section">
                  <header class="field-section__header">
                    <h2>访问凭证</h2>
                <p>默认遮罩显示，可点击眼睛查看或直接编辑。</p>
                  </header>
                  <div class="js-secret-fields"></div>
                </section>`
              : ""
          }
        </div>

      </div>`;

    const detailShell = refs.parserDetail.querySelector(".detail-shell");
    detailShell?.addEventListener("animationend", () => {
      detailShell.classList.remove("is-entering");
    });

    const regularContainer = refs.parserDetail.querySelector(
      ".js-regular-fields",
    );
    for (const field of regularFields) {
      regularContainer.append(
        field.type === "bool"
          ? renderBooleanField(field, draft, parser)
          : renderInputField(field, draft, parser),
      );
    }

    const secretContainer = refs.parserDetail.querySelector(
      ".js-secret-fields",
    );
    for (const field of secretFields) {
      secretContainer.append(renderSecretField(field, draft, parser));
    }

    refs.parserDetail
      .querySelector("#detail-back")
      ?.addEventListener("click", closeDetail);

    const enabled = refs.parserDetail.querySelector("#parser-enabled");
    if (enabled) {
      enabled.addEventListener("change", () => {
        draft.enabled = enabled.checked;
        renderParserList();
        renderDetail();
        refreshGlobalSaveState();
      });
    }
    refreshGlobalSaveState();
  }

  function renderBooleanField(field, draft, parser) {
    const id = `field-${parser.name}-${field.key}`.replace(
      /[^a-zA-Z0-9_-]/g,
      "-",
    );
    const row = createElement("div", "setting-row setting-row--switch");
    const copy = createElement("div", "setting-copy");
    copy.append(createElement("label", "setting-label", field.label));
    if (field.hint) {
      copy.append(createElement("p", "setting-hint", field.hint));
    }
    const control = createSwitch(
      Boolean(draft.values[field.key]),
      (checked) => {
        draft.values[field.key] = checked;
        refreshDirtyState();
      },
      field.label,
    );
    control.querySelector("input").id = id;
    row.append(copy, control);
    return row;
  }

  function renderInputField(field, draft, parser) {
    const id = `field-${parser.name}-${field.key}`.replace(
      /[^a-zA-Z0-9_-]/g,
      "-",
    );
    const row = createElement("div", "setting-row");

    const control = createElement("div", "setting-control");
    if (field.type === "select") {
      control.append(
        makeFilledSelect({
          id,
          label: field.label,
          options: field.options || [],
          value: draft.values[field.key],
          onChange: (value) => {
            draft.values[field.key] = value;
            refreshDirtyState();
          },
        }).wrapper,
      );
    } else if (field.type === "text" || field.type === "list") {
      const value =
        field.type === "list"
          ? Array.isArray(draft.values[field.key])
            ? draft.values[field.key].join("\n")
            : ""
          : String(draft.values[field.key] ?? "");
      const textarea = makeFilledTextarea({
        id,
        label: field.label,
        value,
        onInput: (nextValue) => {
          draft.values[field.key] =
            field.type === "list"
              ? nextValue
                  .split("\n")
                  .map((item) => item.trim())
                  .filter(Boolean)
              : nextValue;
          refreshDirtyState();
        },
      });
      control.append(textarea.wrapper);
    } else {
      const isNumber = field.type === "int" || field.type === "float";
      const input = makeFilledInput({
        id,
        type: isNumber ? "number" : "text",
        label: field.label,
        value: String(draft.values[field.key] ?? ""),
        step: field.type === "int" ? "1" : field.type === "float" ? "any" : "",
        onInput: (value) => {
          draft.values[field.key] = value;
          refreshDirtyState();
        },
      });
      control.append(input.wrapper);
    }
    row.append(control);
    if (field.hint) {
      row.append(createElement("p", "field-support", field.hint));
    }
    return row;
  }

  function renderSecretField(field, draft, parser) {
    const id = `field-${parser.name}-${field.key}`.replace(
      /[^a-zA-Z0-9_-]/g,
      "-",
    );
    const row = createElement("div", "setting-row");

    const control = createElement("div", "setting-control password-control");
    const input = makeFilledInput({
      id,
      type: "password",
      label: field.label,
      value: String(draft.values[field.key] ?? ""),
      onInput: (value) => {
        draft.values[field.key] = value;
        if (value && draft.clearedSecrets.has(field.key)) {
          draft.clearedSecrets.delete(field.key);
        }
        refreshDirtyState();
      },
    });
    const visibility = createElement("button", "icon-button");
    visibility.type = "button";
    visibility.setAttribute("aria-label", "显示凭证");
    visibility.innerHTML = ICONS.eye;
    visibility.addEventListener("click", () => {
      const visible = input.input.type === "text";
      input.input.type = visible ? "password" : "text";
      visibility.innerHTML = visible ? ICONS.eye : ICONS.eyeOff;
      visibility.setAttribute(
        "aria-label",
        visible ? "显示凭证" : "隐藏凭证",
      );
    });
    control.append(input.wrapper, visibility);
    row.append(control);

    const meta = createElement("div", "field-meta");
    const stateText = createElement(
      "p",
      "field-meta__hint",
      draft.clearedSecrets.has(field.key)
        ? "保存后将清除该凭证。"
        : field.configured
          ? "已配置。可查看、编辑或清除。"
          : "尚未配置。可直接输入凭证。",
    );
    const clear = createElement(
      "button",
      "text-button text-button--danger",
      draft.clearedSecrets.has(field.key) ? "取消清除" : "清除",
    );
    clear.type = "button";
    clear.disabled = !field.configured && !draft.clearedSecrets.has(field.key);
    clear.hidden = !field.configured && !draft.clearedSecrets.has(field.key);
    clear.addEventListener("click", () => {
      if (draft.clearedSecrets.has(field.key)) {
        draft.clearedSecrets.delete(field.key);
      } else {
        draft.clearedSecrets.add(field.key);
        draft.values[field.key] = "";
        input.input.value = "";
      }
      renderDetail();
    });
    meta.append(stateText, clear);
    row.append(meta);
    return row;
  }

  function refreshDirtyState() {
    renderParserList();
    updateActiveRail();
    refreshGlobalSaveState();
  }

  function refreshAccessDirtyState() {
    renderAccessParserList();
    refreshGlobalSaveState();
  }

  function readNumber(field, value) {
    const parsed =
      field.type === "int" ? Number(value) : Number.parseFloat(value);
    if (
      !Number.isFinite(parsed) ||
      (field.type === "int" && !Number.isInteger(parsed))
    ) {
      throw new Error(`「${field.label}」需要填写有效数字`);
    }
    return parsed;
  }

  function buildPayload(parser, draft) {
    const values = {};
    for (const field of parser.fields) {
      const value = draft.values[field.key];
      if (field.type === "int" || field.type === "float") {
        values[field.key] = readNumber(field, value);
      } else if (field.type === "bool") {
        values[field.key] = Boolean(value);
      } else if (field.type === "list") {
        values[field.key] = Array.isArray(value) ? value : [];
      } else {
        values[field.key] = String(value ?? "");
      }
    }
    return { enabled: draft.enabled, values };
  }

  function discardAllDrafts() {
    for (const parser of state.parsers) {
      state.drafts.set(parser.name, createDraft(parser));
      parser.access_mode = parser.base_access_mode;
      parser.whitelist = [...parser.base_whitelist];
      parser.blacklist = [...parser.base_blacklist];
    }
    renderParserList();
    renderDetail();
    renderAccessParserList();
    renderAccessDetail();
    refreshGlobalSaveState();
    showSnackbar("已撤销全部未保存的更改");
  }

  async function saveAllDrafts() {
    if (state.saving) return;
    const dirty = dirtyEntries();
    const dirtyAccess = dirtyAccessEntries();
    if (!dirty.length && !dirtyAccess.length) return;
    let payloads;
    let accessPayloads;
    try {
      payloads = dirty.map(({ parser, draft }) => ({
        name: parser.name,
        ...buildPayload(parser, draft),
      }));
      accessPayloads = dirtyAccess.map((parser) => ({
        name: parser.name,
        access_mode: parser.access_mode,
        whitelist: parser.whitelist,
        blacklist: parser.blacklist,
      }));
    } catch (error) {
      showSnackbar(error.message);
      return;
    }

    const hasToggle = dirty.some(
      ({ parser, draft }) => Boolean(parser.enabled) !== Boolean(draft.enabled),
    );

    state.saving = true;
    refreshGlobalSaveState();
    try {
      const response = payloads.length
        ? await apiPost("parsers", {
            parsers: payloads,
          })
        : null;
      if (accessPayloads.length) {
        await apiPost("access-lists", {
          parsers: accessPayloads,
        });
      }
      for (const { parser, draft } of dirty) {
        parser.enabled = draft.enabled;
        for (const field of parser.fields) {
          if (field.key in draft.values) {
            parser.values[field.key] = cloneValue(draft.values[field.key]);
          }
        }
        draft.baseEnabled = draft.enabled;
        draft.baseValues = cloneValues(draft.values);
        for (const field of parser.fields) {
          draft.clearedSecrets.delete(field.key);
        }
      }
      for (const parser of dirtyAccess) {
        parser.base_access_mode = parser.access_mode;
        parser.base_whitelist = [...parser.whitelist];
        parser.base_blacklist = [...parser.blacklist];
      }
      state.saving = false;
      ensureSelection();
      renderParserList();
      renderDetail();
      renderAccessParserList();
      renderAccessDetail();
      refreshGlobalSaveState();
      if (hasToggle || response?.reload_required) {
        showSnackbar(`${dirty.length} 个平台的配置已保存。已变更解析器启停状态，请重载插件以完全生效`, 4500);
      } else {
        showSnackbar("设置已保存");
      }
    } catch (error) {
      state.saving = false;
      refreshGlobalSaveState();
      showSnackbar(`保存失败：${error.message || "未知错误"}`);
    }
  }

  function renderSkins(data) {
    refs.skinList.replaceChildren();
    const skins = data?.skins || [];
    for (const skin of skins) {
      const item = createElement("button", "skin-item");
      item.type = "button";
      item.setAttribute("role", "radio");
      item.setAttribute("aria-checked", skin.name === data.current ? "true" : "false");
      item.dataset.name = skin.name;
      const copy = createElement("span", "skin-copy");
      copy.append(
        createElement("strong", "", skin.display_name),
      );
      item.append(copy, createElement("span", "radio-indicator"));
      item.addEventListener("click", () => selectSkin(skin.name));
      refs.skinList.append(item);
    }
    refs.skinMessage.textContent =
      data?.message || "当前仅支持内置默认皮肤。";
  }

  let skinData = {
    current: "default",
    skins: [{ name: "default", display_name: "默认皮肤" }],
    editable: false,
    message: "皮肤包切换功能预留，当前仅支持内置默认皮肤。",
  };

  async function selectSkin(name) {
    if (name === skinData.current) return;
    if (!skinData.editable) {
      showSnackbar("当前版本暂不支持切换皮肤");
      return;
    }
    try {
      await apiPost("skin", { name });
      skinData = { ...skinData, current: name };
      renderSkins(skinData);
      refs.skinMessage.textContent = `当前使用 ${skinData.skins.find((skin) => skin.name === name)?.display_name || name}。`;
      showSnackbar("皮肤已切换");
    } catch (error) {
      showSnackbar(`切换失败：${error.message || "未知错误"}`);
    }
  }

  async function loadSkins() {
    try {
      skinData = await apiGet("skins");
      renderSkins(skinData);
    } catch (error) {
      refs.skinMessage.textContent = `读取失败：${error.message || "未知错误"}`;
    }
  }

  async function loadParsers() {
    state.loadError = "";
    refs.parserList.innerHTML = `
      <div class="list-skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>`;
    refs.accessParserList.innerHTML = `
      <div class="list-skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>`;
    refs.parserDetail.setAttribute("aria-busy", "true");
    refs.parserDetail.innerHTML = `
      <div class="detail-skeleton" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>`;

    try {
      const data = await apiGet("parsers");
      state.parsers = Array.isArray(data?.parsers) ? data.parsers : [];
      state.drafts.clear();
      for (const parser of state.parsers) {
        parser.access_mode = parser.access_mode || "none";
        parser.whitelist = normalizeGroupIds(parser.whitelist);
        parser.blacklist = normalizeGroupIds(parser.blacklist);
        parser.base_access_mode = parser.access_mode;
        parser.base_whitelist = [...parser.whitelist];
        parser.base_blacklist = [...parser.blacklist];
        state.drafts.set(parser.name, createDraft(parser));
      }
      ensureSelection();
      renderParserList();
      renderDetail();
      renderAccessParserList();
      renderAccessDetail();
    } catch (error) {
      state.parsers = [];
      state.drafts.clear();
      state.loadError = `无法读取解析器配置：${error.message || "未知错误"}`;
      renderParserList();
      renderDetail();
    }
    refreshGlobalSaveState();
  }

  function setActiveTab(name, updateHash = true) {
    const active =
      name === "skins" ? "skins" : name === "access" ? "access" : "parsers";
    for (const tab of refs.tabs) {
      const selected = tab.dataset.tab === active;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
    }
    for (const [panelName, panel] of Object.entries(refs.panels)) {
      panel.hidden = panelName !== active;
    }
    if (updateHash && window.location.hash !== `#${active}`) {
      history.replaceState(null, "", `#${active}`);
    }
  }

  function bindTabs() {
    for (const tab of refs.tabs) {
      tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
    }
    window.addEventListener("hashchange", () => {
      setActiveTab(window.location.hash.slice(1), false);
    });
    setActiveTab(window.location.hash.slice(1));
    refs.saveAll.addEventListener("click", saveAllDrafts);
    refs.discardAll.addEventListener("click", discardAllDrafts);
  }

  function bindEvents() {
    refs.parserSearch.addEventListener("input", () => {
      state.query = refs.parserSearch.value;
      renderParserList();
    });
    refs.accessSearch.addEventListener("input", () => {
      state.accessQuery = refs.accessSearch.value;
      renderAccessParserList();
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  async function start() {
    bindTabs();
    bindEvents();

    if (window.GsHubPlugin) {
      await window.GsHubPlugin.ready;
      if (window.GsHubPlugin.onTheme) {
        window.GsHubPlugin.onTheme((theme) => {
          if (theme && theme.mode) {
            document.documentElement.dataset.theme = theme.mode;
          }
        });
      }
      if (window.GsHubPlugin.theme) {
        document.documentElement.dataset.theme = window.GsHubPlugin.theme;
      }
    }

    await Promise.all([loadParsers(), loadSkins()]);
    refreshGlobalSaveState();
  }

  start();
})();
