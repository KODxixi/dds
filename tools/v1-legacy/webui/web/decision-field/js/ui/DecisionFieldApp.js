const STAGES = [
  { id: "location", label: "位置解析", detail: "地块与城市数据" },
  { id: "evidence", label: "证据检索", detail: "市场、资源与约束" },
  { id: "council", label: "联席判断", detail: "投资、产品、设计与营销" },
  { id: "report", label: "报告组装", detail: "结论、来源与场景书签" },
];

const STAGE_ORDER = Object.freeze({ queued: -1, starting: -1, location: 0, evidence: 1, council: 2, report: 3, done: 4, error: 4 });

function unwrap(payload, key, fallback = null) {
  return payload?.[key] ?? payload?.data?.[key] ?? fallback;
}

function formatCoordinate(location) {
  const point = location?.wgs84 || location;
  const lng = Number(point?.lng);
  const lat = Number(point?.lat);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return "—";
  return `${lng.toFixed(6)}°E  ${lat.toFixed(6)}°N`;
}

function locationName(location) {
  return location?.display_name || location?.address || formatCoordinate(location);
}

function latestDecision(state, report = null) {
  const direct = report?.decision || state.decision;
  if (direct) return direct;
  const revisions = state.project?.decision_revisions || [];
  return revisions[revisions.length - 1] || null;
}

function decisionView(decision, project) {
  const rawConfidence = Number(
    decision?.confidence
    ?? decision?.overall_confidence
    ?? decision?.score
    ?? project?.report?.confidence,
  );
  const confidence = Number.isFinite(rawConfidence)
    ? `${Math.round(rawConfidence <= 1 ? rawConfidence * 100 : rawConfidence)}%`
    : "待核验";
  return {
    verdict: decision?.summary || decision?.verdict || decision?.recommendation || decision?.status || "联席判断已形成",
    confidence,
    gaps: Number(decision?.evidence_gap_count ?? project?.evidence_gaps?.length ?? 0),
  };
}

function isRequiredBriefReady(project) {
  const answered = new Set(project?.brief_answered || []);
  return answered.has("primary_goal") && answered.has("project_stage");
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function normalizeSuggestionPayload(payload) {
  const suggestions = unwrap(payload, "suggestions", unwrap(payload, "locations", []));
  return Array.isArray(suggestions) ? suggestions.slice(0, 5) : [];
}

function eventJob(payload) {
  return payload?.job || payload?.data?.job || payload || {};
}

export class DecisionFieldApp {
  constructor({ root, config, api, store, scene, director, evidenceLayers }) {
    if (!root) throw new TypeError("Decision Field root is required");
    this.root = root;
    this.config = config;
    this.api = api;
    this.store = store;
    this.scene = scene;
    this.director = director;
    this.evidenceLayers = evidenceLayers;
    this.surface = String(root.dataset.surface || "hero").toLowerCase();
    this.elements = {};
    this.destroyed = false;
    this.unsubscribe = null;
    this.searchTimer = 0;
    this.searchSequence = 0;
    this.jobController = null;
    this.currentCity = "";
    this.lastRetry = null;
    this.pointerStart = null;
    this.bound = [];
    this._renderQueued = false;
  }

  async mount() {
    this._cacheElements();
    this._bindEvents();
    this.unsubscribe = this.store.subscribe((state) => this._scheduleRender(state), { immediate: true });
    this.director.bindUserControls(this.elements.sceneFrame);
    this._setDataStatus("正在连接 DDS", "neutral");

    let bootstrap = {};
    let amapConfig = null;
    try {
      bootstrap = await this.api.bootstrap();
      try {
        const mapConfig = await fetch("/api/map_config", { credentials: "same-origin" }).then((response) => response.ok ? response.json() : null);
        if (mapConfig?.amap_js_key) amapConfig = {
          key: mapConfig.amap_js_key,
          securityCode: mapConfig.amap_security_code || "",
          mapStyle: mapConfig.amap_map_style ? `amap://styles/${mapConfig.amap_map_style}` : "",
        };
      } catch { /* AMap is optional; Cesium remains the fallback. */ }
      this.store.setBootstrap(bootstrap);
      this.currentCity = (bootstrap.cities || [])[0] || "";
      this._setDataStatus("数据服务已连接", "ok");
    } catch (error) {
      this.store.setError(error);
      this._setDataStatus("数据服务暂不可用", "warning");
      this.lastRetry = () => this._reloadBootstrap();
    }

    const sceneConfig = {
      ...this.config.scene,
      ...(bootstrap.scene || {}),
      ionToken: bootstrap.scene?.cesium_ion_token
        || bootstrap.scene?.ionToken
        || this.config.scene.ionToken,
      cesiumBase: this.config.cesiumBase,
      worldTextureUrl: this.config.asset("assets/earth_real_50m.png"),
      hero: this.surface === "hero",
      preferWorldTexture: this.surface === "hero",
      quality: this.config.quality,
      amap: amapConfig || this.config.amap,
      reducedMotion: this.config.reducedMotion,
      container: this.elements.cesiumContainer,
      fallbackCanvas: this.elements.fallbackCanvas,
      onStatus: (status) => this._handleSceneStatus(status),
      onUserInteraction: (input) => this.director.cancel(input || "user_interaction"),
      onFeatureSelected: (id) => this.root.dispatchEvent(new CustomEvent("dds:map-feature", { detail: { id }, bubbles: true })),
      onPoiUpdate: (payload) => this.root.dispatchEvent(new CustomEvent("dds:map-poi", { detail: payload, bubbles: true })),
    };
    await this.scene.initialize(sceneConfig);
    this.store.setUI({ quality: this.scene.quality || this.config.quality });

    const projectId = this.config.projectId || new URL(globalThis.location.href).searchParams.get("project");
    if (this.surface === "map") {
      if (projectId) await this.resumeProject(projectId);
      else await this._loadMapEntry(bootstrap);
    } else {
      const defaultView = bootstrap.scene?.default_view || { lng: 104.1, lat: 35.6 };
      await this.director.execute({
        type: "fly_to",
        target: { lng: Number(defaultView.lng || 104.1), lat: Number(defaultView.lat || 35.6), height: 18_500_000 },
        preset: "globe",
      });
      this._setAgentCue("输入地址或坐标，进入独立地图研判。", "定位");
    }
    this._announce("DDS Decision Field 已就绪");
    return this;
  }

  _cacheElements() {
    const byId = (id) => this.root.querySelector(`#${id}`);
    [
      "sceneFrame", "cesiumContainer", "fallbackCanvas", "projectName", "dataStatus", "sceneMode",
      "coordinateReadout", "qualityReadout", "layerMenu", "qualityMenu", "taskDrawer", "taskSummary",
      "taskStages", "recentProjects", "interactionStack", "agentCueText", "contextPanel", "decisionBar",
      "decisionVerdict", "decisionConfidence", "decisionGaps", "locationDock", "dockMode", "dockHint",
      "commandInput", "dockSecondary", "dockSubmit", "liveRegion",
    ].forEach((id) => { this.elements[id] = byId(id); });
    const missing = Object.entries(this.elements).filter(([, value]) => !value).map(([key]) => key);
    if (missing.length) throw new Error(`Decision Field DOM is incomplete: ${missing.join(", ")}`);
  }

  _listen(target, type, listener, options) {
    target?.addEventListener(type, listener, options);
    this.bound.push([target, type, listener, options]);
  }

  _bindEvents() {
    this._listen(this.elements.locationDock, "submit", (event) => {
      event.preventDefault();
      this._handleDockSubmit();
    });
    this._listen(this.elements.commandInput, "input", () => this._handleInput());
    this._listen(this.elements.commandInput, "keydown", (event) => this._handleInputKeydown(event));
    this._listen(this.root, "click", (event) => this._handleRootClick(event));
    this._listen(this.root, "change", (event) => this._handleRootChange(event));
    this._listen(this.elements.sceneFrame, "pointerdown", (event) => {
      if (event.target.closest?.(".scene-menu")) return;
      this.pointerStart = { x: event.clientX, y: event.clientY, at: performance.now() };
    }, { passive: true });
    this._listen(this.elements.sceneFrame, "pointerup", (event) => this._handleScenePointerUp(event), { passive: true });
    this._listen(document, "keydown", (event) => {
      if (event.key === "Escape") this._handleEscape();
    });
    this._listen(globalThis, "beforeunload", () => this.destroy(), { once: true });
  }

  async _reloadBootstrap() {
    try {
      const bootstrap = await this.api.bootstrap();
      this.store.setBootstrap(bootstrap);
      this.currentCity = (bootstrap.cities || [])[0] || this.currentCity;
      this._setDataStatus("数据服务已连接", "ok");
      this.lastRetry = null;
    } catch (error) {
      this.store.setError(error);
    }
  }

  async _loadMapEntry(bootstrap = {}) {
    const params = new URL(globalThis.location.href).searchParams;
    const lng = Number(params.get("lng"));
    const lat = Number(params.get("lat"));
    const fallback = bootstrap.scene?.default_view || { lng: 117.12, lat: 36.67 };
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) {
      const location = {
        gcj02: { lng: Number(fallback.lng || 117.12), lat: Number(fallback.lat || 36.67) },
        wgs84: { lng: Number(fallback.lng || 117.12), lat: Number(fallback.lat || 36.67) },
        display_name: "待定位项目",
        source: "map-default",
        confidence: 0,
      };
      await this._setResolvedLocation(location);
      this._setAgentCue("输入新的地址或坐标以定位项目。", "地图");
      return;
    }
    this.store.setUI({ busy: true });
    try {
      const payload = await this.api.resolveLocation({
        lng,
        lat,
        coordinate_system: params.get("crs") || "wgs84",
        city: params.get("city") || this.currentCity,
      });
      const location = unwrap(payload, "location") || {
        wgs84: { lng, lat },
        gcj02: { lng, lat },
        display_name: params.get("label") || `${lng}, ${lat}`,
      };
      await this._setResolvedLocation(location);
    } catch (error) {
      this.store.setError(error);
      await this._setResolvedLocation({
        wgs84: { lng, lat },
        gcj02: { lng, lat },
        display_name: params.get("label") || `${lng}, ${lat}`,
        source: "url-fallback",
        confidence: 0.5,
      });
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  _handleSceneStatus(status) {
    if (!status) return;
    this.elements.sceneMode.textContent = status.label || status.mode || "场景";
    if (status.quality) {
      this.elements.qualityReadout.textContent = String(status.quality).toUpperCase();
      this.store.setUI({ quality: status.quality });
    }
    if (status.reason && status.tone !== "ok") this._announce(status.reason);
  }

  _handleInput() {
    const state = this.store.snapshot();
    if (state.project || state.activeQuestion || state.location) return;
    const query = this.elements.commandInput.value.trim();
    globalThis.clearTimeout(this.searchTimer);
    if (query.length < 2) {
      this.api.cancelSuggest();
      this.store.setCandidates([]);
      return;
    }
    const sequence = ++this.searchSequence;
    this.searchTimer = globalThis.setTimeout(async () => {
      try {
        const payload = await this.api.suggest(query, this.currentCity);
        if (sequence !== this.searchSequence) return;
        this.store.setCandidates(normalizeSuggestionPayload(payload), 0);
      } catch (error) {
        if (error.name !== "AbortError") {
          this.store.setCandidates([]);
          if (error.code !== "NETWORK_ERROR") this.store.setError(error);
        }
      }
    }, 240);
  }

  _handleInputKeydown(event) {
    const state = this.store.snapshot();
    if (!state.candidates.length) {
      if (event.key === "Escape") this._handleEscape();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      const current = state.ui.candidateIndex < 0 ? 0 : state.ui.candidateIndex;
      const next = (current + delta + state.candidates.length) % state.candidates.length;
      this.store.setCandidateIndex(next);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      this.store.setCandidates([]);
    }
  }

  async _handleDockSubmit() {
    const state = this.store.snapshot();
    const input = this.elements.commandInput.value.trim();
    if (state.ui.busy) return;
    if (state.activeQuestion) {
      if (state.activeQuestion.type === "choice") {
        const choice = (state.activeQuestion.choices || []).find((item) => item.value === input || item.label === input);
        if (!choice) {
          this._announce("请选择当前问题提供的一个选项");
          return;
        }
        await this._submitAnswer(choice.value);
      } else if (state.activeQuestion.type === "numbers") {
        await this._submitNumberAnswer();
      } else if (input) {
        await this._submitAnswer(input);
      }
      return;
    }
    if (!state.project) {
      if (state.candidates.length && state.ui.candidateIndex >= 0) {
        await this._chooseCandidate(state.ui.candidateIndex);
      } else if (input) {
        await this._resolveInput(input);
      }
      return;
    }
    if (["ready_for_analysis", "error_recoverable"].includes(state.project.state) && !input) {
      await this._startAnalysis();
      return;
    }
    if (input) {
      const result = await this.director.command(input, state);
      this.elements.commandInput.value = "";
      this._announce(result?.unknown ? "未识别该场景命令" : "场景命令已执行");
    }
  }

  async _resolveInput(query) {
    this.store.setUI({ busy: true });
    this._setAgentCue("正在解析位置，原始输入会被保留。", "定位");
    this.lastRetry = () => this._resolveInput(query);
    try {
      const payload = await this.api.resolveLocation({ query, city: this.currentCity });
      const location = unwrap(payload, "location");
      if (!location) throw new Error("位置服务未返回地块坐标");
      await this._setResolvedLocation(location);
      this.lastRetry = null;
    } catch (error) {
      this.store.setError(error);
      this._setAgentCue(error.message || "位置解析失败，请改用坐标。", "定位");
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  async _chooseCandidate(index) {
    const candidate = this.store.snapshot().candidates[index];
    if (!candidate) return;
    this.store.setUI({ busy: true });
    try {
      const payload = await this.api.resolveLocation({ candidate, city: candidate.city || this.currentCity });
      await this._setResolvedLocation(unwrap(payload, "location", candidate));
    } catch (error) {
      this.store.setError(error);
      this.lastRetry = () => this._chooseCandidate(index);
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  async _setResolvedLocation(location) {
    if (this.surface !== "map") {
      globalThis.sessionStorage?.setItem("dds.map.location", JSON.stringify(location));
      globalThis.location.assign(this.config.mapUrl(location));
      return;
    }
    this.store.setLocation(location);
    this.store.setUI({ mode: "site_locked" });
    this.evidenceLayers.sync({ project: { location } });
    this.elements.coordinateReadout.textContent = formatCoordinate(location);
    this.elements.commandInput.value = "";
    let sceneMessage = "位置已解析。核对场景与坐标后确认地块。";
    try {
      await this.scene.enableAmap3D?.(location);
    } catch (error) {
      sceneMessage = `${error.message || "高德三维场景暂不可用"}，当前保留 Cesium / 2D 场景。`;
    }
    await this.director.execute({ type: "fly_to", target: location.wgs84, preset: "site" });
    this._setAgentCue(sceneMessage, "确认");
  }

  async _handleScenePointerUp(event) {
    if (!this.pointerStart || event.target.closest?.(".scene-menu")) return;
    const distance = Math.hypot(event.clientX - this.pointerStart.x, event.clientY - this.pointerStart.y);
    const duration = performance.now() - this.pointerStart.at;
    this.pointerStart = null;
    if (distance > 6 || duration > 600) return;
    const state = this.store.snapshot();
    if (state.project || state.ui.busy) return;
    const picked = this.scene.pick(event);
    if (!picked) {
      this._announce("此处未拾取到地表，请换一个位置");
      return;
    }
    this.store.setUI({ busy: true });
    const request = {
      lng: picked.lng,
      lat: picked.lat,
      coordinate_system: "wgs84",
      city: this.currentCity,
    };
    this.lastRetry = () => this._resolveMapPick(request);
    await this._resolveMapPick(request);
  }

  async _resolveMapPick(request) {
    try {
      const payload = await this.api.resolveLocation(request);
      await this._setResolvedLocation(unwrap(payload, "location"));
      this.lastRetry = null;
    } catch (error) {
      this.store.setError(error);
      this._setAgentCue("地图点位已保留；地址待补，可重试或输入坐标。", "选点");
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  async _confirmSite() {
    const state = this.store.snapshot();
    if (!state.location || state.ui.busy) return;
    this.store.setUI({ busy: true });
    this.lastRetry = () => this._confirmSite();
    try {
      const payload = await this.api.createProject(state.location);
      this.store.ingest(payload, "project-created");
      const project = unwrap(payload, "project");
      this._emitHostEvent("project-created", { project });
      if (project?.id) globalThis.history.replaceState({}, "", this.config.mapUrl(project.id));
      this.elements.commandInput.value = "";
      this.lastRetry = null;
    } catch (error) {
      this.store.setError(error);
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  _cancelSite() {
    this.store.clearLocation();
    this.evidenceLayers.sync({});
    this.elements.coordinateReadout.textContent = "—";
    this._setAgentCue("输入地址、坐标，或直接在场景中选点。", "定位");
    this.elements.commandInput.focus();
  }

  async _submitAnswer(value, { skip = false, direct = false } = {}) {
    const state = this.store.snapshot();
    const question = state.activeQuestion;
    if (!state.project?.id || !question || state.ui.busy) return;
    this.store.setUI({ busy: true });
    const body = { question_id: question.id, value, skip, direct_analysis: direct };
    this.lastRetry = () => this._submitAnswer(value, { skip, direct });
    let startAfterAnswer = false;
    try {
      const payload = await this.api.answer(state.project.id, body);
      this.store.ingest(payload, "answer");
      this.elements.commandInput.value = "";
      this.lastRetry = null;
      const project = unwrap(payload, "project");
      startAfterAnswer = project?.state === "ready_for_analysis" && direct;
    } catch (error) {
      this.store.setError(error);
    } finally {
      this.store.setUI({ busy: false });
    }
    if (startAfterAnswer) await this._startAnalysis();
  }

  async _submitNumberAnswer() {
    const state = this.store.snapshot();
    if (state.activeQuestion?.type !== "numbers") return;
    const value = {};
    this.elements.contextPanel.querySelectorAll("[data-number-field]").forEach((input) => {
      if (input.value !== "") value[input.dataset.numberField] = Number(input.value);
    });
    await this._submitAnswer(value);
  }

  async _directAnalysis() {
    const state = this.store.snapshot();
    if (!isRequiredBriefReady(state.project)) {
      this._announce("先回答研判目标和项目阶段，再直接研判");
      return;
    }
    if (state.project.state === "ready_for_analysis") {
      await this._startAnalysis();
      return;
    }
    if (state.activeQuestion) {
      await this._submitAnswer(null, { skip: !state.activeQuestion.required, direct: true });
    }
  }

  async _startAnalysis() {
    const state = this.store.snapshot();
    if (!state.project?.id || state.ui.busy) return;
    this.store.setUI({ busy: true, mode: "analyzing" });
    this._setAgentCue("正在启动联席研判；场景只响应真实任务阶段。", "分析");
    this.lastRetry = () => this._startAnalysis();
    try {
      const payload = await this.api.startAnalysis(state.project.id);
      this.store.ingest(payload, "analysis-started");
      const job = unwrap(payload, "job", {});
      this.store.setJob(job);
      this.evidenceLayers.sync(payload, { location: state.location });
      this.jobController?.abort();
      this.jobController = new AbortController();
      if (["done", "completed"].includes(String(job.status).toLowerCase())) {
        await this._finishAnalysis(payload);
      } else {
        const finalPayload = await this.api.watchJob(job.id, {
          signal: this.jobController.signal,
          onConnection: (connection) => this.store.setConnection(connection),
          onUpdate: (eventPayload, eventJobState) => this._handleJobUpdate(eventPayload, eventJobState),
        });
        await this._finishAnalysis(finalPayload);
      }
      this.lastRetry = null;
    } catch (error) {
      if (error.name !== "AbortError") {
        this.store.setError(error);
        this._setAgentCue(error.message || "分析任务失败，可从稳定项目状态重试。", "恢复");
      }
    } finally {
      this.store.setUI({ busy: false });
    }
  }

  _handleJobUpdate(payload, job) {
    this.store.setJob(job);
    const stage = String(job.stage || "").toLowerCase();
    if (job.message) this._setAgentCue(job.message, "分析");
    const location = this.store.snapshot().location?.wgs84;
    if (stage === "location" && location) this.director.execute({ type: "fly_to", target: location, preset: "site" });
    if (stage === "evidence") this.director.execute({ type: "set_layers", layers: { parcel: true, competitors: true, resources: true } });
    if (stage === "council" && location) this.director.execute({ type: "fly_to", target: location, preset: "context" });
    if (stage === "report") this.director.captureBookmark("site");
    const directives = payload?.directives || job?.directives || [];
    (Array.isArray(directives) ? directives : [directives]).filter(Boolean).forEach((directive) => {
      this.director.execute(directive).catch(() => {});
    });
  }

  async _finishAnalysis(payload) {
    const state = this.store.snapshot();
    const projectId = unwrap(payload, "project", state.project)?.id
      || eventJob(payload)?.result?.project_id
      || state.project?.id;
    let projectPayload = payload;
    let reportPayload = null;
    try {
      projectPayload = await this.api.getProject(projectId);
      this.store.ingest(projectPayload, "analysis-project");
      reportPayload = await this.api.getReportData(projectId);
      this.store.ingest(reportPayload, "analysis-report");
      this.evidenceLayers.sync(reportPayload, { location: this.store.snapshot().location });
    } catch (error) {
      if (error.code !== "REPORT_NOT_READY") throw error;
    }
    const finalState = this.store.snapshot();
    const report = unwrap(reportPayload, "report", null);
    const decision = latestDecision(finalState, report) || { summary: "联席判断已完成" };
    this.store.setDecision(decision, report || finalState.report);
    this.director.captureBookmark("site");
    this._setAgentCue("联席判断已形成。可以打开报告或输入场景命令。", "结论");
    this._emitHostEvent("analysis-ready", {
      project: unwrap(projectPayload, "project", finalState.project),
      report,
    });
  }

  async resumeProject(projectId) {
    this.store.setUI({ busy: true });
    let resumedJob = null;
    try {
      const payload = await this.api.getProject(projectId);
      this.store.ingest(payload, "project-resumed");
      const project = unwrap(payload, "project");
      this.evidenceLayers.sync(payload, { location: project?.location });
      if (this.surface === "map" && project?.location) await this.scene.enableAmap3D?.(project.location);
      await this.director.syncProject(project);
      if (project?.analysis_job_id && project.state === "analyzing") {
        const jobPayload = await this.api.getJob(project.analysis_job_id);
        this.store.ingest(jobPayload, "job-resumed");
        resumedJob = unwrap(jobPayload, "job");
      }
    } catch (error) {
      this.store.setError(error);
    } finally {
      this.store.setUI({ busy: false });
    }
    if (resumedJob) {
      if (["done", "completed"].includes(String(resumedJob.status).toLowerCase())) {
        await this._finishAnalysis({ job: resumedJob });
      } else {
        this._watchExistingJob(resumedJob.id);
      }
    }
  }

  async _watchExistingJob(jobId) {
    this.jobController?.abort();
    this.jobController = new AbortController();
    try {
      const payload = await this.api.watchJob(jobId, {
        signal: this.jobController.signal,
        onConnection: (connection) => this.store.setConnection(connection),
        onUpdate: (eventPayload, job) => this._handleJobUpdate(eventPayload, job),
      });
      await this._finishAnalysis(payload);
    } catch (error) {
      if (error.name !== "AbortError") this.store.setError(error);
    }
  }

  async _openReport() {
    const state = this.store.snapshot();
    if (!state.project?.id) return;
    let url = state.project.report?.live_url
      || state.report?.live_url
      || state.job?.result?.report_url;
    this.store.setUI({ busy: true });
    if (state.project.state === "decision_ready") {
      try {
        const payload = await this.api.createReport(state.project.id, state.project.report?.revision);
        url = payload.report_url || payload.live_url || url;
      } catch (error) {
        if (!url) {
          this.store.setError(error);
          this.store.setUI({ busy: false });
          return;
        }
      }
    }
    url ||= this.config.reportUrl(state.project.id);
    this.store.setUI({ busy: false });
    this._emitHostEvent("report-opened", { projectId: state.project.id, url });
    globalThis.location.assign(url);
  }

  _handleRootClick(event) {
    const candidateButton = event.target.closest?.("[data-candidate-index]");
    if (candidateButton) {
      this._chooseCandidate(Number(candidateButton.dataset.candidateIndex));
      return;
    }
    const choiceButton = event.target.closest?.("[data-choice-value]");
    if (choiceButton) {
      this._submitAnswer(choiceButton.dataset.choiceValue);
      return;
    }
    const qualityTarget = event.target.closest?.("[data-quality]");
    if (qualityTarget) {
      const quality = qualityTarget.dataset.quality;
      this.director.execute({ type: "set_quality", profile: quality });
      this.store.setUI({ quality });
      this.elements.qualityMenu.hidden = true;
      this.root.querySelector('[data-action="toggle-quality"]')?.setAttribute("aria-expanded", "false");
      return;
    }
    const actionTarget = event.target.closest?.("[data-action]");
    if (!actionTarget) return;
    const action = actionTarget.dataset.action;
    const actions = {
      "toggle-layers": () => this._toggleMenu("layerMenu", actionTarget),
      "toggle-quality": () => this._toggleMenu("qualityMenu", actionTarget),
      "return-site": () => this.director.command("返回地块", this.store.snapshot()),
      "toggle-drawer": () => this._toggleDrawer(),
      "close-drawer": () => this._toggleDrawer(false),
      "confirm-site": () => this._confirmSite(),
      "cancel-site": () => this._cancelSite(),
      "skip-question": () => this._submitAnswer(null, { skip: true }),
      "direct-analysis": () => this._directAnalysis(),
      "start-analysis": () => this._startAnalysis(),
      "submit-numbers": () => this._submitNumberAnswer(),
      "open-report": () => this._openReport(),
      "continue-questions": () => this._continueQuestions(),
      "retry": () => this.lastRetry?.(),
      "secondary": () => this._handleSecondary(),
    };
    actions[action]?.();

  }

  _handleRootChange(event) {
    const layer = event.target.dataset?.layer;
    if (layer) this.evidenceLayers.setVisible(layer, event.target.checked);
  }

  _handleSecondary() {
    const state = this.store.snapshot();
    if (state.activeQuestion && !state.activeQuestion.required) this._submitAnswer(null, { skip: true });
    else if (state.location && !state.project) this._cancelSite();
    else this.store.clearTransient();
  }

  _continueQuestions() {
    this.elements.commandInput.focus();
    this.elements.commandInput.placeholder = "输入场景命令或报告关注点";
    this._setAgentCue("当前 revision 已锁定；可先查看证据场景，新的业务追问会形成下一版判断。", "追问");
  }

  _toggleMenu(id, button) {
    const menu = this.elements[id];
    const willOpen = menu.hidden;
    ["layerMenu", "qualityMenu"].forEach((menuId) => {
      this.elements[menuId].hidden = true;
      this.root.querySelector(`[aria-controls="${menuId}"]`)?.setAttribute("aria-expanded", "false");
    });
    menu.hidden = !willOpen;
    button.setAttribute("aria-expanded", String(willOpen));
    if (willOpen && globalThis.matchMedia?.("(max-width: 1279px)")?.matches) this._toggleDrawer(false);
  }

  _toggleDrawer(open) {
    const drawer = this.elements.taskDrawer;
    const isOpen = open ?? drawer.getAttribute("aria-hidden") === "true";
    drawer.setAttribute("aria-hidden", String(!isOpen));
    this.root.querySelector('[data-action="toggle-drawer"]')?.setAttribute("aria-expanded", String(isOpen));
    this.store.setUI({ drawerOpen: isOpen });
  }

  _handleEscape() {
    this.director.cancel("escape");
    this.store.setCandidates([]);
    ["layerMenu", "qualityMenu"].forEach((id) => { this.elements[id].hidden = true; });
    this.root.querySelectorAll("[aria-controls='layerMenu'], [aria-controls='qualityMenu']").forEach((button) => button.setAttribute("aria-expanded", "false"));
    if (this.elements.taskDrawer.getAttribute("aria-hidden") === "false") this._toggleDrawer(false);
  }

  _scheduleRender(state) {
    this._latestState = state;
    if (this._renderQueued) return;
    this._renderQueued = true;
    requestAnimationFrame(() => {
      this._renderQueued = false;
      if (!this.destroyed) this._render(this._latestState);
    });
  }

  _render(state) {
    const mode = state.project?.state || state.ui.mode || "locating";
    this.root.dataset.state = mode;
    this.elements.projectName.textContent = state.project ? locationName(state.project.location) : state.location ? locationName(state.location) : "尚未锁定地块";
    this.elements.coordinateReadout.textContent = formatCoordinate(state.location || state.project?.location);
    this.elements.qualityReadout.textContent = String(state.ui.quality || "balanced").toUpperCase();
    this.elements.dockSubmit.disabled = Boolean(state.ui.busy);
    if (state.activeQuestion?.prompt && this.elements.agentCueText.textContent !== state.activeQuestion.prompt) {
      this.elements.agentCueText.textContent = state.activeQuestion.prompt;
    }
    this._renderContext(state);
    this._renderDecision(state);
    this._renderTasks(state);
    this._renderRecentProjects();
    this._renderDock(state);
  }

  _renderContext(state) {
    const panel = this.elements.contextPanel;
    panel.replaceChildren();
    if (state.error) {
      const box = createElement("div", "panel-message");
      box.dataset.tone = "error";
      box.append(createElement("p", "", state.error.message));
      if (state.error.retryable && this.lastRetry) {
        const retry = createElement("button", "text-button", "重试");
        retry.type = "button";
        retry.dataset.action = "retry";
        box.append(retry);
      }
      panel.append(box);
      panel.hidden = false;
      return;
    }
    if (state.candidates.length && !state.project && !state.location) {
      const list = createElement("ul", "candidate-list");
      list.setAttribute("role", "listbox");
      state.candidates.forEach((candidate, index) => {
        const item = createElement("li", "candidate-item");
        const button = createElement("button", "candidate-button");
        button.type = "button";
        button.dataset.candidateIndex = String(index);
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", String(index === state.ui.candidateIndex));
        button.append(
          createElement("strong", "", locationName(candidate)),
          createElement("span", "", formatCoordinate(candidate)),
          createElement("small", "", [candidate.city, candidate.district, candidate.address].filter(Boolean).join(" · ")),
        );
        item.append(button);
        list.append(item);
      });
      panel.append(list);
      panel.hidden = false;
      return;
    }
    if (state.location && !state.project) {
      const confirmation = createElement("div", "site-confirmation");
      const copy = createElement("div", "site-confirmation__copy");
      copy.append(
        createElement("span", "", "SITE LOCK"),
        createElement("h2", "", locationName(state.location)),
        createElement("p", "", `${formatCoordinate(state.location)} · ${Math.round(Number(state.location.confidence || 0) * 100)}%`),
      );
      const actions = createElement("div", "site-confirmation__actions");
      const cancel = createElement("button", "text-button", "取消");
      cancel.type = "button"; cancel.dataset.action = "cancel-site";
      const confirm = createElement("button", "primary-button", "确认地块");
      confirm.type = "button"; confirm.dataset.action = "confirm-site";
      actions.append(cancel, confirm);
      confirmation.append(copy, actions);
      panel.append(confirmation);
      panel.hidden = false;
      return;
    }
    if (state.activeQuestion) {
      panel.append(this._buildQuestion(state.activeQuestion, state.project));
      panel.hidden = false;
      return;
    }
    if (state.project?.state === "ready_for_analysis") {
      panel.append(this._buildAnalysisPanel({ message: "任务书已具备两个必答字段", action: true }));
      panel.hidden = false;
      return;
    }
    if (state.project?.state === "error_recoverable") {
      panel.append(this._buildAnalysisPanel({ message: state.project.last_error?.message || "上次分析中断，可从稳定项目状态重试", action: true }));
      panel.hidden = false;
      return;
    }
    if (state.project?.state === "analyzing" || state.ui.mode === "analyzing") {
      panel.append(this._buildAnalysisPanel({ job: state.job }));
      panel.hidden = false;
      return;
    }
    panel.hidden = true;
  }

  _buildQuestion(question, project) {
    const wrapper = createElement("div", "agent-question");
    const header = createElement("div", "agent-question__header");
    const index = Math.max(1, (project?.brief_answered?.length || 0) + 1);
    header.append(
      createElement("span", "agent-question__index", `${String(index).padStart(2, "0")}/05`),
      createElement("h2", "", question.prompt || question.label || "请补充项目条件"),
      createElement("span", "agent-question__required", question.required ? "必答" : "可跳过"),
    );
    wrapper.append(header);
    if (question.why) wrapper.append(createElement("p", "agent-question__why", question.why));
    if (question.type === "choice") {
      const choices = createElement("div", "choice-grid");
      (question.choices || question.options || []).forEach((choice) => {
        const button = createElement("button", "choice-button", choice.label || choice.value);
        button.type = "button";
        button.dataset.choiceValue = choice.value;
        choices.append(button);
      });
      wrapper.append(choices);
    }
    if (question.type === "numbers") {
      const fields = createElement("div", "number-fields");
      (question.fields || []).forEach((field) => {
        const label = createElement("label", "number-field");
        label.append(createElement("span", "", field.label));
        const input = document.createElement("input");
        input.type = "number";
        input.min = field.min;
        input.max = field.max;
        input.step = field.id === "far" ? "0.1" : "100";
        input.inputMode = "decimal";
        input.dataset.numberField = field.id;
        label.append(input);
        fields.append(label);
      });
      wrapper.append(fields);
    }
    const actions = createElement("div", "question-actions");
    if (!question.required) {
      const skip = createElement("button", "text-button", "跳过");
      skip.type = "button"; skip.dataset.action = "skip-question";
      actions.append(skip);
    }
    if (question.type === "numbers") {
      const submit = createElement("button", "primary-button", "保存回答");
      submit.type = "button"; submit.dataset.action = "submit-numbers";
      actions.append(submit);
    }
    if (isRequiredBriefReady(project)) {
      const direct = createElement("button", "text-button", "直接研判");
      direct.type = "button"; direct.dataset.action = "direct-analysis";
      actions.append(direct);
    }
    if (actions.childElementCount) wrapper.append(actions);
    return wrapper;
  }

  _buildAnalysisPanel({ job = null, message = "", action = false } = {}) {
    const panel = createElement("div", "analysis-panel");
    const copy = createElement("div", "analysis-panel__copy");
    copy.append(
      createElement("strong", "", job?.message || message || "分析任务运行中"),
      createElement("span", "", job ? `${job.stage || "starting"} · ${Number(job.progress || 0)}%` : "可随时返回任务书继续补充"),
    );
    panel.append(copy);
    if (job) {
      const progress = createElement("div", "analysis-progress");
      progress.setAttribute("role", "progressbar");
      progress.setAttribute("aria-valuemin", "0");
      progress.setAttribute("aria-valuemax", "100");
      progress.setAttribute("aria-valuenow", String(Number(job.progress || 0)));
      const fill = createElement("span");
      fill.style.setProperty("--progress", `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`);
      progress.append(fill);
      panel.append(progress);
    } else if (action) {
      const button = createElement("button", "primary-button", "开始研判");
      button.type = "button"; button.dataset.action = "start-analysis";
      panel.append(button);
    }
    return panel;
  }

  _renderDecision(state) {
    const decision = latestDecision(state, state.report);
    const ready = state.project?.state === "decision_ready" || Boolean(decision && state.project?.report);
    this.elements.decisionBar.hidden = !ready;
    if (!ready) return;
    const view = decisionView(decision, state.project);
    this.elements.decisionVerdict.textContent = view.verdict;
    this.elements.decisionConfidence.textContent = view.confidence;
    this.elements.decisionGaps.textContent = String(view.gaps);
  }

  _renderTasks(state) {
    const job = state.job || {};
    const current = STAGE_ORDER[String(job.stage || "queued").toLowerCase()] ?? -1;
    const isError = String(job.status).toLowerCase() === "error";
    this.elements.taskStages.replaceChildren(...STAGES.map((stage, index) => {
      const item = createElement("li", "task-stage");
      const status = isError && index === Math.max(0, current) ? "error" : index < current || current >= 4 ? "done" : index === current ? "active" : "pending";
      item.dataset.status = status;
      const mark = createElement("span", "task-stage__mark", status === "done" ? "✓" : status === "active" ? "•" : "");
      const text = createElement("div");
      text.append(createElement("span", "task-stage__name", stage.label), createElement("span", "task-stage__detail", index === current && job.message ? job.message : stage.detail));
      const progress = createElement("span", "task-stage__progress", index === current ? `${Number(job.progress || 0)}%` : status === "done" ? "100%" : "—");
      item.append(mark, text, progress);
      return item;
    }));
    this.elements.taskSummary.textContent = state.project
      ? `${locationName(state.project.location)} · ${state.project.state}`
      : "地块确认后，位置解析、证据检索、联席判断与报告组装会在这里留下真实状态。";
  }

  _renderRecentProjects() {
    const projects = this.store.getRecentProjects();
    this.elements.recentProjects.replaceChildren(...projects.map((project) => {
      const button = createElement("button", "recent-project");
      button.type = "button";
      button.addEventListener("click", () => this.resumeProject(project.id), { once: true });
      button.append(
        createElement("span", "", project.name),
        createElement("small", "", project.state),
        createElement("em", "", new Date(project.updated_at).toLocaleString("zh-CN", { hour12: false })),
      );
      return button;
    }));
    if (!projects.length) this.elements.recentProjects.append(createElement("p", "report-empty", "暂无最近项目"));
  }

  _renderDock(state) {
    const input = this.elements.commandInput;
    let mode = "定位";
    let hint = "地址 / lng,lat / 命令";
    let placeholder = "输入地址、坐标或命令";
    let secondary = "";
    if (state.location && !state.project) {
      mode = "确认"; hint = "核对 WGS84 坐标"; placeholder = "可重新输入位置"; secondary = "取消";
    }
    if (state.activeQuestion) {
      mode = `Q${String((state.project?.brief_answered?.length || 0) + 1).padStart(2, "0")}`;
      hint = state.activeQuestion.required ? "必答" : "可跳过";
      placeholder = state.activeQuestion.placeholder || (state.activeQuestion.type === "choice" ? "选择上方选项" : "输入本题回答");
      if (!state.activeQuestion.required) secondary = "跳过";
    } else if (state.project?.state === "ready_for_analysis") {
      mode = "就绪"; hint = "任务书可审计"; placeholder = "留空发送即可开始研判";
    } else if (state.project?.state === "analyzing" || state.ui.mode === "analyzing") {
      mode = "分析"; hint = state.connection || "任务运行中"; placeholder = "分析中仍可操作场景";
    } else if (state.project?.state === "decision_ready") {
      mode = "结论"; hint = "Scene command"; placeholder = "返回地块、查看竞品、只看风险…";
    } else if (state.project?.state === "error_recoverable") {
      mode = "恢复"; hint = "稳定状态已保存"; placeholder = "留空发送即可重试分析";
    }
    this.elements.dockMode.textContent = mode;
    this.elements.dockHint.textContent = hint;
    input.placeholder = placeholder;
    input.disabled = Boolean(state.ui.busy && state.activeQuestion);
    this.elements.dockSecondary.hidden = !secondary;
    this.elements.dockSecondary.textContent = secondary;
  }

  _setAgentCue(text, label = null) {
    this.elements.agentCueText.textContent = text;
    if (label) this.elements.dockMode.textContent = label;
    this._announce(text);
  }

  _setDataStatus(text, tone) {
    this.elements.dataStatus.dataset.tone = tone;
    const label = this.elements.dataStatus.querySelector("span:last-child");
    if (label) label.textContent = text;
  }

  _announce(message) {
    this.elements.liveRegion.textContent = "";
    requestAnimationFrame(() => { this.elements.liveRegion.textContent = String(message || ""); });
  }

  _emitHostEvent(type, detail) {
    this.root.dispatchEvent(new CustomEvent(type, { detail, bubbles: true }));
    if (globalThis.parent && globalThis.parent !== globalThis) {
      globalThis.parent.postMessage({ source: "dds-decision-field", type, ...detail }, this.config.hostOrigin);
    }
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    globalThis.clearTimeout(this.searchTimer);
    this.api.cancelSuggest();
    this.jobController?.abort();
    this.unsubscribe?.();
    this.bound.forEach(([target, type, listener, options]) => target?.removeEventListener(type, listener, options));
    this.bound = [];
    this.director.destroy();
  }
}
