(() => {
  const panelDefs = [
    { id: "profit_curve", label: "盈利曲线", live: true },
    { id: "when_i_raise", label: "When I Raise", live: true },
    { id: "preflop_analysis", label: "翻前分析", live: true },
    { id: "tools", label: "小工具集合", live: false },
  ];

  const pfPositionOpts = [
    { id: "UTG", label: "UTG" },
    { id: "HJ", label: "HJ" },
    { id: "CO", label: "CO" },
    { id: "BTN", label: "BTN" },
    { id: "SB", label: "SB" },
    { id: "BB", label: "BB" },
  ];
  const pfPositionOrder = pfPositionOpts.map((o) => o.id);
  const pfActionOpts = [
    { id: "open_raise", label: "Open Raise" },
    { id: "3bet", label: "3bet" },
    { id: "4bet", label: "4bet" },
    { id: "5bet", label: "5bet" },
  ];

  const wirStreetOpts = [
    { id: "ALL", label: "ALL" },
    { id: "preflop", label: "Preflop" },
    { id: "flop", label: "Flop" },
    { id: "turn", label: "Turn" },
    { id: "river", label: "River" },
  ];
  const wirPlayerOpts = [
    { id: "2", label: "2人" },
    { id: "3+", label: "2人以上" },
  ];
  const wirSizeOpts = [
    { id: "33", label: "33% pot" },
    { id: "66", label: "66% pot" },
    { id: "110", label: "110% pot" },
  ];
  const wirPositionOpts = [
    { id: "IP", label: "IP" },
    { id: "OOP", label: "OOP" },
    { id: "OTHER", label: "OTHER" },
  ];
  const wirFlopTextureOpts = [
    { id: "high_card", label: "High card", hint: "含 ≥1 张 AKQJ" },
    { id: "has_ace", label: "有 A", hint: "" },
    { id: "straight_made", label: "能成顺", hint: "" },
    { id: "straight_draw", label: "能听顺", hint: "" },
    { id: "flush_draw", label: "能听花", hint: "两同花及以上" },
    { id: "monotone", label: "三张同色", hint: "" },
    { id: "paired", label: "有公对", hint: "" },
  ];
  const wirTurnFlopLineOpts = [
    { id: "flop_checkcheck", label: "Check-Check", hint: "Flop 所有人 check 到 Turn" },
    { id: "flop_call", label: "Flop Call", hint: "Hero 最后 call 进入 Turn" },
    { id: "flop_raise", label: "Flop Raise", hint: "Hero 最后 raise/bet 被 call 进入 Turn" },
  ];

  const state = {
    open: new Set(),
    profitChart: null,
    analyzed: false,
    summary: null,
    filterDefaults: null,
    wirRequestId: 0,
    pfRequestId: 0,
    pfMatrixRequestId: 0,
    pfMatrixCells: null,
  };

  const $ = (sel) => document.querySelector(sel);

  function moneyClass(n) {
    if (n > 0) return "pos";
    if (n < 0) return "neg";
    return "";
  }

  function fmtMoney(n) {
    const sign = n > 0 ? "+" : "";
    return `${sign}${Number(n).toFixed(2)}`;
  }

  function fmtPct(n) {
    if (n === null || n === undefined) return "—";
    return `${Number(n).toFixed(2)}%`;
  }

  async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = payload.detail || JSON.stringify(payload);
      } catch {
        try {
          detail = await res.text();
        } catch {
          /* ignore */
        }
      }
      throw new Error(detail || res.statusText);
    }
    return res.json();
  }

  function renderToggles() {
    const host = $("#panelToggles");
    host.innerHTML = "";
    for (const def of panelDefs) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toggle-btn" + (state.open.has(def.id) ? " active" : "");
      btn.dataset.panel = def.id;
      btn.textContent = def.label;
      btn.addEventListener("click", () => togglePanel(def.id));
      host.appendChild(btn);
    }
  }

  function togglePanel(id) {
    if (state.open.has(id)) {
      state.open.delete(id);
    } else {
      state.open.add(id);
    }
    syncPanels();
    renderToggles();
    if (state.open.has(id) && state.analyzed) {
      analyze();
    }
  }

  function syncPanels() {
    for (const def of panelDefs) {
      const panel = document.getElementById(`panel-${def.id}`);
      if (!panel) continue;
      panel.hidden = !state.open.has(def.id);
    }
  }

  function fillChipGroup(host, opts, { multi, name, checkedIds }) {
    host.innerHTML = "";
    for (const opt of opts) {
      const label = document.createElement("label");
      const disabled = !!opt.disabled;
      label.className = "stake-chip has-data" + (disabled ? " is-disabled" : "");
      const checked = checkedIds.has(opt.id) ? "checked" : "";
      const type = multi ? "checkbox" : "radio";
      const disabledAttr = disabled ? "disabled" : "";
      const title = disabled ? ' title="后期开放"' : "";
      label.innerHTML = `
        <input type="${type}" name="${name}" value="${opt.id}" ${checked} ${disabledAttr} />
        <span${title}>${opt.label}</span>
      `;
      host.appendChild(label);
    }
  }

  function setupWhenIRaiseFilters() {
    fillChipGroup($("#wirStreetGroup"), wirStreetOpts, {
      multi: true,
      name: "wir-street",
      checkedIds: new Set(["ALL"]),
    });
    fillChipGroup($("#wirPlayersGroup"), wirPlayerOpts, {
      multi: true,
      name: "wir-players",
      checkedIds: new Set(wirPlayerOpts.map((o) => o.id)),
    });
    fillChipGroup($("#wirSizeGroup"), wirSizeOpts, {
      multi: true,
      name: "wir-size",
      checkedIds: new Set(wirSizeOpts.map((o) => o.id)),
    });
    fillChipGroup($("#wirPositionGroup"), wirPositionOpts, {
      multi: true,
      name: "wir-position",
      checkedIds: new Set(wirPositionOpts.map((o) => o.id)),
    });
    fillFlopTextureControls($("#wirFlopTextureGroup"));
    fillTurnFlopLineControls($("#wirTurnFlopLineGroup"));
    const flopDetailEnable = $("#wirFlopDetailEnable");
    if (flopDetailEnable) flopDetailEnable.checked = false;
    const turnDetailEnable = $("#wirTurnDetailEnable");
    if (turnDetailEnable) turnDetailEnable.checked = false;
    syncDetailModeUI();

    const host = $("#whenIRaiseFilters");
    host.addEventListener("change", (event) => {
      const target = event.target;
      if (!target) {
        scheduleWhenIRaiseRefresh();
        return;
      }

      if (target.id === "wirFlopDetailEnable") {
        if (target.checked && !isTurnDetailEnabled()) {
          applyFlopDetailStreetDefaults();
        }
        syncDetailModeUI();
        scheduleWhenIRaiseRefresh();
        return;
      }

      if (target.id === "wirTurnDetailEnable") {
        if (target.checked) {
          applyTurnDetailStreetDefaults();
        }
        syncDetailModeUI();
        scheduleWhenIRaiseRefresh();
        return;
      }

      if (target.name === "wir-street") {
        normalizeStreetSelection(target);
        syncDetailModeUI();
        scheduleWhenIRaiseRefresh();
        return;
      }

      if (target.classList && target.classList.contains("flop-tex-enable")) {
        const row = target.closest(".flop-tex-row");
        if (row) syncFlopTextureRowState(row);
      }

      scheduleWhenIRaiseRefresh();
    });
  }

  function setupPreflopFilters() {
    fillChipGroup($("#pfHeroPosGroup"), pfPositionOpts, {
      multi: false,
      name: "pf-hero-pos",
      checkedIds: new Set(["BTN"]),
    });
    fillChipGroup($("#pfActionGroup"), pfActionOpts, {
      multi: false,
      name: "pf-action",
      checkedIds: new Set(["open_raise"]),
    });
    const allowLimp = $("#pfAllowLimp");
    const allowCall = $("#pfAllowCall");
    if (allowLimp) allowLimp.checked = true;
    if (allowCall) allowCall.checked = true;
    syncPreflopOpenerRow();

    const host = $("#preflopFilters");
    host.addEventListener("change", (event) => {
      const target = event.target;
      if (target && (target.name === "pf-hero-pos" || target.name === "pf-action")) {
        syncPreflopOpenerRow();
      }
      const panel = $("#panel-preflop_analysis");
      if (!state.open.has("preflop_analysis") || (panel && panel.hidden)) return;
      analyzePreflop().catch((err) => {
        $("#filterStatus").textContent = `分析失败: ${err.message}`;
        console.error(err);
      });
      if (target && (target.id === "pfAllowLimp" || target.id === "pfAllowCall")) {
        analyzePreflopMatrix().catch((err) => {
          $("#filterStatus").textContent = `分析失败: ${err.message}`;
          console.error(err);
        });
      }
    });
  }

  function selectedPreflopHeroPos() {
    const el = document.querySelector("#pfHeroPosGroup input:checked");
    return el ? el.value : "BTN";
  }

  function selectedPreflopAction() {
    const el = document.querySelector("#pfActionGroup input:checked");
    return el ? el.value : "open_raise";
  }

  function syncPreflopOpenerRow() {
    const row = $("#pfOpenerRow");
    const host = $("#pfOpenerPosGroup");
    const label = $("#pfVillainLabel");
    const action = selectedPreflopAction();
    const heroPos = selectedPreflopHeroPos();
    const show = action === "3bet" || action === "4bet" || action === "5bet";
    row.hidden = !show;
    if (!show) {
      host.innerHTML = "";
      return;
    }

    let villainOpts;
    if (action === "3bet") {
      if (label) label.textContent = "Open 对手";
      const heroIdx = pfPositionOrder.indexOf(heroPos);
      villainOpts = pfPositionOpts.filter((_, i) => i < heroIdx);
    } else if (action === "4bet") {
      if (label) label.textContent = "3bet 对手";
      villainOpts = pfPositionOpts.filter((o) => o.id !== heroPos);
    } else {
      if (label) label.textContent = "4bet 对手";
      villainOpts = pfPositionOpts.filter((o) => o.id !== heroPos);
    }

    const prev = document.querySelector("#pfOpenerPosGroup input:checked");
    const prevId = prev ? prev.value : "";
    const fallback = villainOpts.some((o) => o.id === "BB")
      ? "BB"
      : villainOpts.length
        ? villainOpts[villainOpts.length - 1].id
        : "";
    const nextId = villainOpts.some((o) => o.id === prevId) ? prevId : fallback;
    fillChipGroup(host, villainOpts, {
      multi: false,
      name: "pf-opener-pos",
      checkedIds: new Set(nextId ? [nextId] : []),
    });
  }

  function readPreflopOptions() {
    const hero_position = selectedPreflopHeroPos();
    const action = selectedPreflopAction();
    const allowLimp = $("#pfAllowLimp");
    const allowCall = $("#pfAllowCall");
    const options = {
      hero_position,
      action,
      allow_limp: allowLimp ? allowLimp.checked : true,
      allow_call: allowCall ? allowCall.checked : true,
    };
    const villain = document.querySelector("#pfOpenerPosGroup input:checked");
    if (action === "3bet" && villain) options.opener_position = villain.value;
    if (action === "4bet" && villain) options.threebettor_position = villain.value;
    if (action === "5bet" && villain) options.fourbettor_position = villain.value;
    return options;
  }

  function scheduleWhenIRaiseRefresh() {
    const panel = $("#panel-when_i_raise");
    if (!state.open.has("when_i_raise") || (panel && panel.hidden)) return;
    analyzeWhenIRaise().catch((err) => {
      $("#filterStatus").textContent = `分析失败: ${err.message}`;
      console.error(err);
    });
  }

  function isFlopDetailEnabled() {
    const el = $("#wirFlopDetailEnable");
    return !!(el && el.checked);
  }

  function isTurnDetailEnabled() {
    const el = $("#wirTurnDetailEnable");
    return !!(el && el.checked);
  }

  function applyFlopDetailStreetDefaults() {
    document.querySelectorAll("#wirStreetGroup input").forEach((el) => {
      if (el.value === "ALL" || el.value === "preflop") {
        el.checked = false;
      } else if (el.value === "flop" || el.value === "turn" || el.value === "river") {
        el.checked = true;
      }
    });
  }

  function applyTurnDetailStreetDefaults() {
    document.querySelectorAll("#wirStreetGroup input").forEach((el) => {
      if (el.value === "ALL" || el.value === "preflop" || el.value === "flop") {
        el.checked = false;
      } else if (el.value === "turn" || el.value === "river") {
        el.checked = true;
      }
    });
  }

  function normalizeStreetSelection(changed) {
    const flopDetail = isFlopDetailEnabled();
    const turnDetail = isTurnDetailEnabled();
    const inputs = [...document.querySelectorAll("#wirStreetGroup input")];
    if (!inputs.length) return;

    if (turnDetail) {
      inputs.forEach((el) => {
        if (el.value === "ALL" || el.value === "preflop" || el.value === "flop") {
          el.checked = false;
        }
      });
      const turnStreets = inputs.filter((el) => ["turn", "river"].includes(el.value));
      if (!turnStreets.some((el) => el.checked)) {
        if (changed && ["turn", "river"].includes(changed.value)) {
          changed.checked = true;
        } else {
          const turn = turnStreets.find((el) => el.value === "turn");
          if (turn) turn.checked = true;
        }
      }
      return;
    }

    if (flopDetail) {
      // ALL / preflop are not allowed under flop_detail.
      inputs.forEach((el) => {
        if (el.value === "ALL" || el.value === "preflop") el.checked = false;
      });
      const postflop = inputs.filter((el) => ["flop", "turn", "river"].includes(el.value));
      if (!postflop.some((el) => el.checked)) {
        // Keep at least the street the user just interacted with, else flop.
        if (changed && ["flop", "turn", "river"].includes(changed.value)) {
          changed.checked = true;
        } else {
          const flop = postflop.find((el) => el.value === "flop");
          if (flop) flop.checked = true;
        }
      }
      return;
    }

    if (changed && changed.value === "ALL" && changed.checked) {
      inputs.forEach((el) => {
        if (el.value !== "ALL") el.checked = false;
      });
      return;
    }

    if (changed && changed.value !== "ALL" && changed.checked) {
      const allEl = inputs.find((el) => el.value === "ALL");
      if (allEl) allEl.checked = false;
    }

    if (!inputs.some((el) => el.checked)) {
      const allEl = inputs.find((el) => el.value === "ALL");
      if (allEl) allEl.checked = true;
    }
  }

  function syncDetailModeUI() {
    const flopEnabled = isFlopDetailEnabled();
    const turnEnabled = isTurnDetailEnabled();
    const textureRow = $("#wirFlopTextureRow");
    if (textureRow) textureRow.hidden = !flopEnabled;

    const turnLineRow = $("#wirTurnFlopLineRow");
    if (turnLineRow) turnLineRow.hidden = !turnEnabled;

    document.querySelectorAll("#wirStreetGroup input").forEach((el) => {
      let blocked = false;
      if (turnEnabled) {
        blocked = el.value === "ALL" || el.value === "preflop" || el.value === "flop";
      } else if (flopEnabled) {
        blocked = el.value === "ALL" || el.value === "preflop";
      }
      el.disabled = blocked;
      const chip = el.closest(".stake-chip");
      if (chip) chip.classList.toggle("is-disabled", blocked);
    });
  }

  function fillTurnFlopLineControls(host) {
    host.innerHTML = "";
    for (const opt of wirTurnFlopLineOpts) {
      const label = document.createElement("label");
      label.className = "stake-chip has-data";
      const hint = opt.hint ? ` title="${opt.hint}"` : "";
      label.innerHTML = `
        <input type="checkbox" name="wir-turn-flop-line" value="${opt.id}"${hint} />
        <span>${opt.label}</span>
      `;
      host.appendChild(label);
    }
  }

  function fillFlopTextureControls(host) {
    host.innerHTML = "";
    for (const opt of wirFlopTextureOpts) {
      const row = document.createElement("div");
      row.className = "flop-tex-row is-off";
      row.dataset.key = opt.id;
      const hint = opt.hint ? `<span class="flop-tex-hint">${opt.hint}</span>` : "";
      row.innerHTML = `
        <label class="flop-tex-switch" title="启用此条件">
          <input type="checkbox" class="flop-tex-enable" data-key="${opt.id}" />
          <span class="flop-tex-slider" aria-hidden="true"></span>
        </label>
        <div class="flop-tex-meta">
          <span class="flop-tex-name">${opt.label}</span>
          ${hint}
        </div>
        <div class="flop-tex-polarity" role="group" aria-label="${opt.label} 是或否">
          <label class="flop-tex-yn">
            <input type="radio" name="wir-flop-${opt.id}" value="true" checked disabled />
            <span>是</span>
          </label>
          <label class="flop-tex-yn">
            <input type="radio" name="wir-flop-${opt.id}" value="false" disabled />
            <span>否</span>
          </label>
        </div>
      `;
      host.appendChild(row);
    }
  }

  function syncFlopTextureRowState(row) {
    const enabled = row.querySelector(".flop-tex-enable").checked;
    row.classList.toggle("is-off", !enabled);
    row.querySelectorAll('.flop-tex-polarity input[type="radio"]').forEach((el) => {
      el.disabled = !enabled;
    });
  }

  function readWhenIRaiseOptions() {
    const flop_detail = isFlopDetailEnabled();
    const turn_detail = isTurnDetailEnabled();
    let streets = [...document.querySelectorAll("#wirStreetGroup input:checked")].map(
      (el) => el.value
    );
    if (turn_detail) {
      streets = streets.filter((s) => s === "turn" || s === "river");
      if (!streets.length) streets = ["turn", "river"];
    } else if (flop_detail) {
      streets = streets.filter((s) => s === "flop" || s === "turn" || s === "river");
      if (!streets.length) streets = ["flop", "turn", "river"];
    } else if (!streets.length || streets.includes("ALL")) {
      streets = ["ALL"];
    }

    const player_counts = [...document.querySelectorAll("#wirPlayersGroup input:checked")].map(
      (el) => el.value
    );
    const sizes = [...document.querySelectorAll("#wirSizeGroup input:checked")].map(
      (el) => el.value
    );
    const positions = [...document.querySelectorAll("#wirPositionGroup input:checked")].map(
      (el) => el.value
    );
    const options = {
      streets,
      flop_detail,
      turn_detail,
      player_counts,
      sizes,
      positions,
    };
    if (flop_detail || turn_detail) {
      const flop_textures = {};
      document.querySelectorAll("#wirFlopTextureGroup .flop-tex-row").forEach((row) => {
        const enable = row.querySelector(".flop-tex-enable");
        if (!enable || !enable.checked) return;
        const key = enable.dataset.key;
        const wantEl = row.querySelector('.flop-tex-polarity input[type="radio"]:checked');
        flop_textures[key] = wantEl ? wantEl.value === "true" : true;
      });
      if (Object.keys(flop_textures).length) {
        options.flop_textures = flop_textures;
      }
    }
    if (turn_detail) {
      const turn_flop_lines = [
        ...document.querySelectorAll("#wirTurnFlopLineGroup input:checked"),
      ].map((el) => el.value);
      if (turn_flop_lines.length) {
        options.turn_flop_lines = turn_flop_lines;
      }
    }
    return options;
  }

  function setupFilter(summary) {
    const filter = summary.filter || {};
    state.filterDefaults = {
      date_from: filter.date_from || "",
      date_to: filter.date_to || "",
      stakes: (filter.stakes_presets || []).map((s) => s.id),
      game_types: (filter.game_types_presets || []).map((g) => g.id),
    };

    const dateFrom = $("#dateFrom");
    const dateTo = $("#dateTo");
    dateFrom.min = filter.date_from || "";
    dateFrom.max = filter.date_to || "";
    dateTo.min = filter.date_from || "";
    dateTo.max = filter.date_to || "";
    dateFrom.value = state.filterDefaults.date_from;
    dateTo.value = state.filterDefaults.date_to;

    const gameHost = $("#gameTypeGroup");
    gameHost.innerHTML = "";
    for (const gt of filter.game_types_presets || []) {
      const label = document.createElement("label");
      label.className = "stake-chip" + (gt.has_data ? " has-data" : "");
      label.innerHTML = `
        <input type="checkbox" value="${gt.id}" checked />
        <span>${gt.label}</span>
        ${gt.has_data ? "" : '<span class="tag">预留</span>'}
      `;
      gameHost.appendChild(label);
    }

    const host = $("#stakesGroup");
    host.innerHTML = "";
    for (const stake of filter.stakes_presets || []) {
      const label = document.createElement("label");
      label.className = "stake-chip" + (stake.has_data ? " has-data" : "");
      label.innerHTML = `
        <input type="checkbox" value="${stake.id}" checked />
        <span>${stake.label}</span>
        ${stake.has_data ? "" : '<span class="tag">预留</span>'}
      `;
      host.appendChild(label);
    }
  }

  function resetFilter() {
    if (!state.filterDefaults) return;
    $("#dateFrom").value = state.filterDefaults.date_from;
    $("#dateTo").value = state.filterDefaults.date_to;
    for (const input of document.querySelectorAll("#stakesGroup input[type=checkbox]")) {
      input.checked = true;
    }
    for (const input of document.querySelectorAll("#gameTypeGroup input[type=checkbox]")) {
      input.checked = true;
    }
  }

  function readFilter() {
    const stakes = [...document.querySelectorAll("#stakesGroup input[type=checkbox]:checked")]
      .map((el) => el.value);
    const game_types = [...document.querySelectorAll("#gameTypeGroup input[type=checkbox]:checked")]
      .map((el) => el.value);
    return {
      date_from: $("#dateFrom").value || null,
      date_to: $("#dateTo").value || null,
      stakes,
      game_types,
    };
  }

  function applySummary(data) {
    const prevDir = state.summary?.data_dir_resolved;
    state.summary = data;
    if (data.data_dir) {
      $("#dataDirInput").value = data.data_dir;
      const resolved = data.data_dir_resolved || data.data_dir;
      $("#dataDirStatus").textContent =
        resolved && resolved !== data.data_dir
          ? `当前: ${data.data_dir} (${resolved})`
          : `当前: ${data.data_dir}`;
    }
    if (data.error) {
      $("#summaryText").textContent = `目录无法读取: ${data.error}`;
      setupFilter(data);
      return data;
    }
    const dirChanged = prevDir && prevDir !== data.data_dir_resolved;
    if (!data.loaded) {
      const files = data.file_count || 0;
      $("#summaryText").textContent = files
        ? `尚未加载牌谱 · 目录中有 ${files} 个 .txt 文件 · 设置筛选后点击「分析」`
        : "尚未加载牌谱 · 设置筛选后点击「分析」";
      if (!state.filterDefaults || dirChanged) setupFilter(data);
      return data;
    }
    const range =
      data.date_range?.start && data.date_range?.end
        ? `${data.date_range.start} → ${data.date_range.end}`
        : "无数据";
    $("#summaryText").textContent =
      `已加载 ${data.hand_count} 手 · ${data.file_count} 个文件 · ${range}`;
    setupFilter(data);
    return data;
  }

  async function loadSummary() {
    const data = await fetchJSON("/api/summary");
    return applySummary(data);
  }

  async function ensureDataLoaded() {
    if (state.summary?.loaded) return state.summary;
    const saved = readFilter();
    $("#filterStatus").textContent = "正在加载并解析牌谱…";
    const data = await fetchJSON("/api/load", { method: "POST" });
    if (data.error) throw new Error(data.error);
    applySummary(data);
    if (saved.date_from) $("#dateFrom").value = saved.date_from;
    if (saved.date_to) $("#dateTo").value = saved.date_to;
    if (saved.stakes.length) {
      for (const input of document.querySelectorAll("#stakesGroup input[type=checkbox]")) {
        input.checked = saved.stakes.includes(input.value);
      }
    }
    if (saved.game_types.length) {
      for (const input of document.querySelectorAll("#gameTypeGroup input[type=checkbox]")) {
        input.checked = saved.game_types.includes(input.value);
      }
    }
    return data;
  }

  async function applyDataDir() {
    const path = $("#dataDirInput").value.trim();
    if (!path) {
      $("#dataDirStatus").textContent = "请先填写或浏览选择目录。";
      return;
    }
    const btn = $("#applyDirBtn");
    btn.disabled = true;
    btn.textContent = "加载中…";
    $("#dataDirStatus").textContent = "正在切换数据目录…";
    try {
      const result = await fetchJSON("/api/data-dir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (result.summary) {
        state.filterDefaults = null;
        applySummary(result.summary);
      } else {
        $("#dataDirInput").value = result.data_dir || path;
        $("#dataDirStatus").textContent = result.warning
          ? `已切换，但加载失败: ${result.warning}`
          : `已切换到: ${result.data_dir}`;
      }
      state.analyzed = false;
      $("#filterStatus").textContent = "数据目录已更新，请点击「分析」。";
    } catch (err) {
      $("#dataDirStatus").textContent = `切换失败: ${err.message}`;
      console.error(err);
    } finally {
      btn.disabled = false;
      btn.textContent = "加载";
    }
  }

  async function browseDataDir() {
    const btn = $("#browseDirBtn");
    btn.disabled = true;
    $("#dataDirStatus").textContent = "正在打开文件夹窗口，请看任务栏或桌面弹窗…";
    try {
      const started = await fetchJSON("/api/browse-dir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial: $("#dataDirInput").value.trim() || null }),
      });
      if (started.message) {
        $("#dataDirStatus").textContent = started.message;
      }

      const deadline = Date.now() + 10 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 400));
        const status = await fetchJSON("/api/browse-dir/status");
        if (status.status === "pending") continue;
        if (status.status === "cancelled") {
          $("#dataDirStatus").textContent = "已取消选择。";
          return;
        }
        if (status.status === "error") {
          throw new Error(status.error || "未知错误");
        }
        if (status.status === "done" && status.path) {
          $("#dataDirInput").value = status.path;
          $("#dataDirStatus").textContent = `已选择: ${status.path}（点击「加载」生效）`;
          return;
        }
        $("#dataDirStatus").textContent = "未选择目录。";
        return;
      }
      $("#dataDirStatus").textContent = "选择超时。也可直接粘贴路径后点「加载」。";
    } catch (err) {
      $("#dataDirStatus").textContent =
        `浏览失败: ${err.message}（也可直接粘贴路径后点「加载」）`;
      console.error(err);
    } finally {
      btn.disabled = false;
    }
  }

  async function analyzeWhenIRaise() {
    const requestId = ++state.wirRequestId;
    const filter = readFilter();
    const options = readWhenIRaiseOptions();
    const stats = $("#whenIRaiseStats");
    if (stats) {
      stats.innerHTML = `
        <div class="stat">
          <span class="label">样本数</span>
          <span class="value" style="color:var(--muted)">计算中…</span>
        </div>
      `;
    }
    const data = await fetchJSON("/api/metrics/when_i_raise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...filter, options }),
    });
    // Ignore stale responses when filters change quickly.
    if (requestId !== state.wirRequestId) return;
    renderWhenIRaise(data);
  }

  async function analyze() {
    let filter = readFilter();
    if (!filter.game_types.length) {
      $("#filterStatus").textContent = "请至少选择一种游戏类型。";
      return;
    }
    if (!filter.stakes.length) {
      $("#filterStatus").textContent = "请至少选择一个游戏级别。";
      return;
    }

    const btn = $("#analyzeBtn");
    btn.disabled = true;
    btn.textContent = "分析中…";

    try {
      await ensureDataLoaded();
      filter = readFilter();
      $("#filterStatus").textContent = "正在按筛选条件计算…";

      for (const def of panelDefs) {
        if (!state.open.has(def.id) || !def.live) continue;
        if (def.id === "profit_curve") {
          const data = await fetchJSON("/api/metrics/profit_curve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(filter),
          });
          renderProfit(data);
        } else if (def.id === "when_i_raise") {
          await analyzeWhenIRaise();
        } else if (def.id === "preflop_analysis") {
          await Promise.all([analyzePreflop(), analyzePreflopMatrix()]);
        }
      }

      state.analyzed = true;
      const stakesLabel = filter.stakes.join(", ") || "无";
      const gameTypeLabels = {
        nlh: "普通桌",
        rush: "极速桌",
      };
      const gameLabel =
        filter.game_types.map((id) => gameTypeLabels[id] || id).join(", ") || "无";
      $("#filterStatus").textContent =
        `已分析：${filter.date_from || "?"} ~ ${filter.date_to || "?"} · ${gameLabel} · 级别 ${stakesLabel}`;
    } catch (err) {
      $("#filterStatus").textContent = `分析失败: ${err.message}`;
      console.error(err);
    } finally {
      btn.disabled = false;
      btn.textContent = "分析";
    }
  }

  async function analyzePreflop() {
    const requestId = ++state.pfRequestId;
    const filter = readFilter();
    const options = readPreflopOptions();
    const stats = $("#preflopStats");
    if (stats) {
      stats.innerHTML = `
        <div class="stat">
          <span class="label">样本数</span>
          <span class="value" style="color:var(--muted)">计算中…</span>
        </div>
      `;
    }
    const data = await fetchJSON("/api/metrics/preflop_analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...filter, options }),
    });
    if (requestId !== state.pfRequestId) return;
    renderPreflop(data);
  }

  function readPreflopMatrixOptions() {
    const allowLimp = $("#pfAllowLimp");
    const allowCall = $("#pfAllowCall");
    return {
      action: "3bet_matrix",
      allow_limp: allowLimp ? allowLimp.checked : true,
      allow_call: allowCall ? allowCall.checked : true,
    };
  }

  async function analyzePreflopMatrix() {
    const requestId = ++state.pfMatrixRequestId;
    const table = $("#preflop3betMatrix");
    if (table) {
      table.innerHTML = `<tbody><tr><td class="pf-matrix-na">计算中…</td></tr></tbody>`;
    }
    const detail = $("#preflop3betDetail");
    if (detail) detail.hidden = true;
    const data = await fetchJSON("/api/metrics/preflop_analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...readFilter(), options: readPreflopMatrixOptions() }),
    });
    if (requestId !== state.pfMatrixRequestId) return;
    renderPreflopMatrix(data);
  }

  function fmtMatrixPct(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toFixed(0);
  }

  function renderPreflopMatrixHandTable(tableSel, rows) {
    const tbody = document.querySelector(`${tableSel} tbody`);
    if (!tbody) return;
    if (!rows || !rows.length) {
      tbody.innerHTML = `<tr><td colspan="3" class="unknown">无</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((row) => {
        const cls = row.hand === "未知" ? "unknown" : "";
        return `<tr class="${cls}"><td>${row.hand}</td><td>${row.count}</td><td>${fmtPct(row.pct)}</td></tr>`;
      })
      .join("");
  }

  function showPreflopMatrixDetail(cell) {
    const detail = $("#preflop3betDetail");
    const title = $("#preflop3betDetailTitle");
    if (!detail || !title || !cell || !cell.valid) return;
    title.textContent = `${cell.opener} open 被 ${cell.threebettor} 3bet · 面对样本 ${cell.faced || 0} · 跟注 ${cell.call_hand_count || 0} · 4bet ${cell.fourbet_hand_count || 0}`;
    renderPreflopMatrixHandTable("#preflop3betCallTable", cell.call_hands);
    renderPreflopMatrixHandTable("#preflop3betFourbetTable", cell.fourbet_hands);
    detail.hidden = false;
    document.querySelectorAll("#preflop3betMatrix td.pf-matrix-cell").forEach((td) => {
      const on =
        td.dataset.three === cell.threebettor && td.dataset.opener === cell.opener;
      td.classList.toggle("is-active", on);
    });
  }

  function renderPreflopMatrix(data) {
    const table = $("#preflop3betMatrix");
    const warn = $("#preflop3betWarn");
    if (!table) return;
    const positions = data.positions || pfPositionOrder;
    const cellMap = new Map();
    for (const cell of data.cells || []) {
      cellMap.set(`${cell.threebettor}|${cell.opener}`, cell);
    }
    state.pfMatrixCells = cellMap;

    let facedSum = 0;
    let foldW = 0;
    for (const cell of cellMap.values()) {
      if (!cell.valid || !cell.faced) continue;
      facedSum += cell.faced;
      foldW += (cell.fold && cell.fold.count) || 0;
    }
    if (warn) {
      const foldPct = facedSum ? (100 * foldW) / facedSum : null;
      if (foldPct != null && foldPct < 25) {
        warn.hidden = false;
        warn.textContent =
          `当前样本整体弃牌仅 ${foldPct.toFixed(0)}%，很可能是「仅摊牌」牌谱（如 opp_hand）：开牌人弃牌后通常无亮牌，弃牌样本会缺失，导致 F 偏低、C/4b 虚高。请改用 all_hand 全量牌谱看 fold to 3bet。`;
      } else {
        warn.hidden = true;
        warn.textContent = "";
      }
    }

    // 行 = 被 3bet 方(opener)，列 = 3bet 方(threebettor)；格内 F / C / 4b
    const head = positions.map((p) => `<th>${p}</th>`).join("");
    const body = positions
      .map((opener) => {
        const cells = positions
          .map((three) => {
            const cell = cellMap.get(`${three}|${opener}`);
            if (!cell || !cell.valid) {
              return `<td class="pf-matrix-na">—</td>`;
            }
            const fold = cell.fold || {};
            const call = cell.call || {};
            const four = cell.fourbet || {};
            const faced = cell.faced || 0;
            const rates = faced
              ? `<span class="pf-r pf-r-f"><em>F</em>${fmtMatrixPct(fold.pct)}</span>
                 <span class="pf-r pf-r-c"><em>C</em>${fmtMatrixPct(call.pct)}</span>
                 <span class="pf-r pf-r-4b"><em>4b</em>${fmtMatrixPct(four.pct)}</span>`
              : `<span class="pf-matrix-na">—</span>`;
            return `<td class="pf-matrix-cell" data-three="${three}" data-opener="${opener}" title="弃牌 / 跟注 / 4bet">
              <div class="pf-matrix-stack">${rates}</div>
              <span class="pf-matrix-n">n=${faced}</span>
            </td>`;
          })
          .join("");
        return `<tr><th class="pf-matrix-rowhead">${opener}</th>${cells}</tr>`;
      })
      .join("");

    table.innerHTML = `
      <thead>
        <tr>
          <th class="pf-matrix-corner">被3bet \\ 3bet</th>
          ${head}
        </tr>
      </thead>
      <tbody>${body}</tbody>
    `;

    table.querySelectorAll("td.pf-matrix-cell").forEach((td) => {
      td.addEventListener("click", () => {
        const key = `${td.dataset.three}|${td.dataset.opener}`;
        const cell = state.pfMatrixCells && state.pfMatrixCells.get(key);
        if (cell) showPreflopMatrixDetail(cell);
      });
    });
  }

  function renderPreflopHands(data) {
    const wrap = $("#preflopHandsWrap");
    const hint = $("#preflopHandsHint");
    const tbody = document.querySelector("#preflopHandsTable tbody");
    if (!wrap || !tbody) return;
    const rows = data.call_hands || [];
    const show = data.action === "4bet" || data.action === "5bet";
    wrap.hidden = !show;
    if (!show) {
      tbody.innerHTML = "";
      return;
    }
    const n = data.call_hand_count || 0;
    if (hint) {
      hint.textContent = n
        ? `共 ${n} 次跟注；能摊开的按 AKs/AKo 计，看不到记为「未知」。`
        : "还没有跟注样本。";
    }
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="3" class="unknown">无</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((row) => {
        const cls = row.hand === "未知" ? "unknown" : "";
        return `<tr class="${cls}"><td>${row.hand}</td><td>${row.count}</td><td>${fmtPct(row.pct)}</td></tr>`;
      })
      .join("");
  }

  function renderPreflop(data) {
    const empty = $("#preflopEmpty");
    const stats = $("#preflopStats");
    const action = data.action || "open_raise";

    if (!data.spot_count) {
      empty.hidden = false;
      if (action === "3bet" && !((data.options && data.options.positions_in_front) || []).length) {
        empty.textContent = "当前英雄位置前面没有 open raise 座位（例如 UTG），无法统计 3bet。";
      } else {
        empty.textContent = "当前条件下没有符合的翻前样本。";
      }
      stats.innerHTML = `
        <div class="stat">
          <span class="label">样本数</span>
          <span class="value">0</span>
        </div>
      `;
      renderPreflopHands({ action, call_hands: [], call_hand_count: 0 });
      return;
    }

    empty.hidden = true;
    const cells = [
      ["样本数", String(data.spot_count)],
    ];
    if (action === "open_raise") {
      const fold = data.all_fold || {};
      const three = data.faced_3bet || {};
      cells.push(["直接收池", `${fmtPct(fold.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${fold.count || 0})</span>`]);
      cells.push(["被 3bet", `${fmtPct(three.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${three.count || 0})</span>`]);
    } else if (action === "3bet") {
      const ofold = data.opener_fold || {};
      const ocall = data.opener_call || {};
      const o4 = data.opener_4bet || {};
      const pot = data.all_fold || {};
      const c4 = data.cold_4bet || {};
      if (data.opener_responded != null) {
        cells.push(["对手面对 3bet", String(data.opener_responded)]);
      }
      cells.push(["对手弃牌", `${fmtPct(ofold.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${ofold.count || 0})</span>`]);
      cells.push(["对手跟注", `${fmtPct(ocall.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${ocall.count || 0})</span>`]);
      cells.push(["对手 4bet", `${fmtPct(o4.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${o4.count || 0})</span>`]);
      cells.push(["直接收池", `${fmtPct(pot.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${pot.count || 0})</span>`]);
      cells.push(["后位冷 4bet", `${fmtPct(c4.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${c4.count || 0})</span>`]);
    } else if (action === "4bet") {
      const pot = data.all_fold || {};
      const five = data.faced_5bet || {};
      const call = data.threebettor_call || {};
      if (data.threebettor_faced != null) {
        cells.push(["3bet 者面对 4bet", String(data.threebettor_faced)]);
      }
      cells.push(["直接收池", `${fmtPct(pot.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${pot.count || 0})</span>`]);
      cells.push(["被 5bet", `${fmtPct(five.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${five.count || 0})</span>`]);
      cells.push(["3bet 者跟注", `${fmtPct(call.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${call.count || 0})</span>`]);
    } else if (action === "5bet") {
      const fold = data.fourbettor_fold || {};
      const call = data.fourbettor_call || {};
      const th = data.theoretical_equity || {};
      const ac = data.actual_winrate || {};
      if (data.fourbettor_faced != null) {
        cells.push(["4bet 者面对 5bet", String(data.fourbettor_faced)]);
      }
      cells.push(["对方弃牌", `${fmtPct(fold.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${fold.count || 0})</span>`]);
      cells.push(["对方跟注", `${fmtPct(call.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${call.count || 0})</span>`]);
      cells.push(["理论胜率", `${fmtPct(th.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${th.count || 0} 手已知牌)</span>`]);
      cells.push(["实际胜率", `${fmtPct(ac.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${ac.count || 0})</span>`]);
    }
    stats.innerHTML = cells
      .map(
        ([label, value]) => `
        <div class="stat">
          <span class="label">${label}</span>
          <span class="value">${value}</span>
        </div>`
      )
      .join("");
    renderPreflopHands(data);
  }

  function renderWhenIRaise(data) {
    const empty = $("#whenIRaiseEmpty");
    const stats = $("#whenIRaiseStats");

    if (!data.spot_count) {
      empty.hidden = false;
      stats.innerHTML = `
        <div class="stat">
          <span class="label">样本数</span>
          <span class="value">0</span>
        </div>
      `;
      return;
    }

    empty.hidden = true;
    const allFold = data.all_fold || {};
    const call = data.call || {};
    const reraise = data.reraise || {};
    stats.innerHTML = `
      <div class="stat">
        <span class="label">样本数</span>
        <span class="value">${data.spot_count}</span>
      </div>
      <div class="stat">
        <span class="label">涉及手数</span>
        <span class="value">${data.hand_count}</span>
      </div>
      <div class="stat">
        <span class="label">All Fold</span>
        <span class="value">${fmtPct(allFold.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${allFold.count || 0})</span></span>
      </div>
      <div class="stat">
        <span class="label">Call</span>
        <span class="value">${fmtPct(call.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${call.count || 0})</span></span>
      </div>
      <div class="stat">
        <span class="label">Reraise</span>
        <span class="value">${fmtPct(reraise.pct)} <span style="color:var(--muted);font-weight:500;font-size:0.85rem">(${reraise.count || 0})</span></span>
      </div>
    `;
  }

  function renderProfit(data) {
    const empty = $("#profitEmpty");
    const chartWrap = document.querySelector("#panel-profit_curve .chart-wrap");
    const stats = $("#profitStats");

    if (!data.hand_count) {
      empty.hidden = false;
      if (chartWrap) chartWrap.hidden = true;
      stats.innerHTML = "";
      if (state.profitChart) {
        state.profitChart.destroy();
        state.profitChart = null;
      }
      return;
    }

    empty.hidden = true;
    if (chartWrap) chartWrap.hidden = false;

    const before = data.total_profit_before_rake;
    const after = data.total_profit_after_rake;
    const fees = data.total_rake_paid;
    const rakeOnly = data.total_rake_only;
    const jackpot = data.total_jackpot_share;
    stats.innerHTML = `
      <div class="stat">
        <span class="label">手数</span>
        <span class="value">${data.hand_count}</span>
      </div>
      <div class="stat">
        <span class="label">总计盈利（费用前）</span>
        <span class="value ${moneyClass(before)}">${fmtMoney(before)}</span>
      </div>
      <div class="stat">
        <span class="label">总计真实盈利（费用后）</span>
        <span class="value ${moneyClass(after)}">${fmtMoney(after)}</span>
      </div>
      <div class="stat">
        <span class="label">累计费用（Rake+JP）</span>
        <span class="value">${fmtMoney(fees)}</span>
      </div>
      <div class="stat">
        <span class="label">其中 Rake / Jackpot</span>
        <span class="value">${fmtMoney(rakeOnly ?? 0)} / ${fmtMoney(jackpot ?? 0)}</span>
      </div>
    `;

    const ctx = $("#profitChart");
    const labels = data.series.hand_index;
    const datasetCommon = {
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 3,
      tension: 0.15,
    };

    if (state.profitChart) {
      state.profitChart.destroy();
    }

    state.profitChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "费用前",
            data: data.series.profit_before_rake,
            borderColor: "#c4a35a",
            backgroundColor: "rgba(196, 163, 90, 0.12)",
            ...datasetCommon,
          },
          {
            label: "费用后",
            data: data.series.profit_after_rake,
            borderColor: "#3d9b7a",
            backgroundColor: "rgba(61, 155, 122, 0.12)",
            ...datasetCommon,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: { color: "#c5d0db" },
          },
          tooltip: {
            callbacks: {
              title(items) {
                return `第 ${items[0].label} 手`;
              },
              label(ctx) {
                return `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y)}`;
              },
            },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "手数", color: "#8b9aab" },
            ticks: {
              color: "#8b9aab",
              maxTicksLimit: 12,
            },
            grid: { color: "rgba(44, 58, 74, 0.55)" },
          },
          y: {
            title: { display: true, text: "累计盈利 ($)", color: "#8b9aab" },
            ticks: {
              color: "#8b9aab",
              callback: (v) => Number(v).toFixed(2),
            },
            grid: { color: "rgba(44, 58, 74, 0.55)" },
          },
        },
      },
    });
  }

  async function init() {
    renderToggles();
    setupWhenIRaiseFilters();
    setupPreflopFilters();
    await loadSummary();
    renderToggles();
    syncPanels();

    $("#analyzeBtn").addEventListener("click", () => analyze());
    $("#resetFilterBtn").addEventListener("click", () => {
      resetFilter();
      $("#filterStatus").textContent = "已重置为全部数据，点击「分析」生效。";
    });
    $("#browseDirBtn").addEventListener("click", () => browseDataDir());
    $("#applyDirBtn").addEventListener("click", () => applyDataDir());
    $("#dataDirInput").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        applyDataDir();
      }
    });

    $("#reloadBtn").addEventListener("click", async () => {
      await fetchJSON("/api/reload", { method: "POST" });
      state.filterDefaults = null;
      await loadSummary();
      state.analyzed = false;
      $("#filterStatus").textContent = "数据已重新扫描，请再次点击「分析」。";
    });

    const pfReplayBtn = $("#pfReplayBtn");
    if (pfReplayBtn) {
      pfReplayBtn.addEventListener("click", () => {
        window.PokerReplay.open("preflop_analysis", () => ({
          filter: readFilter(),
          options: readPreflopOptions(),
        }));
      });
    }
    const wirReplayBtn = $("#wirReplayBtn");
    if (wirReplayBtn) {
      wirReplayBtn.addEventListener("click", () => {
        window.PokerReplay.open("when_i_raise", () => ({
          filter: readFilter(),
          options: readWhenIRaiseOptions(),
        }));
      });
    }
  }

  init().catch((err) => {
    $("#summaryText").textContent = `加载失败: ${err.message}`;
    console.error(err);
  });
})();
