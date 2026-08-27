const INITIAL_STATE = Object.freeze({
  bootstrap: null,
  project: null,
  location: null,
  candidates: [],
  activeQuestion: null,
  job: null,
  decision: null,
  report: null,
  error: null,
  connection: "idle",
  ui: {
    mode: "locating",
    busy: false,
    candidateIndex: -1,
    drawerOpen: false,
    quality: "balanced",
  },
});

function clone(value) {
  if (value == null) return value;
  if (typeof globalThis.structuredClone === "function") return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

function getProject(payload) {
  return payload?.project || payload?.data?.project || null;
}

function getQuestion(payload, project) {
  return payload?.question
    || payload?.next_question
    || payload?.data?.question
    || payload?.data?.next_question
    || project?.question
    || project?.next_question
    || null;
}

function getLatestDecision(project, payload = null) {
  const direct = payload?.decision || payload?.result?.decision || payload?.data?.decision;
  if (direct) return direct;
  const revisions = project?.decision_revisions;
  return Array.isArray(revisions) && revisions.length ? revisions[revisions.length - 1] : null;
}

function storageAvailable() {
  try {
    return Boolean(globalThis.localStorage);
  } catch {
    return false;
  }
}

export class ProjectStore {
  constructor({ storageKey = "dds.decision-field.recent-projects", quality = "balanced" } = {}) {
    this.storageKey = storageKey;
    this.listeners = new Set();
    this.state = clone(INITIAL_STATE);
    this.state.ui.quality = quality;
  }

  snapshot() {
    return clone(this.state);
  }

  subscribe(listener, { immediate = false } = {}) {
    if (typeof listener !== "function") throw new TypeError("listener must be a function");
    this.listeners.add(listener);
    if (immediate) listener(this.snapshot(), { type: "subscribe" });
    return () => this.listeners.delete(listener);
  }

  _emit(type, detail = null) {
    const snapshot = this.snapshot();
    this.listeners.forEach((listener) => listener(snapshot, { type, detail }));
  }

  _set(patch, type, detail) {
    this.state = { ...this.state, ...patch };
    this._emit(type, detail);
  }

  setBootstrap(bootstrap) {
    this._set({ bootstrap, error: null }, "bootstrap", bootstrap);
  }

  setCandidates(candidates, candidateIndex = -1) {
    const normalized = Array.isArray(candidates) ? candidates.slice(0, 5) : [];
    this.state = {
      ...this.state,
      candidates: normalized,
      ui: { ...this.state.ui, candidateIndex: normalized.length ? candidateIndex : -1 },
    };
    this._emit("candidates", normalized);
  }

  setCandidateIndex(index) {
    const maximum = this.state.candidates.length - 1;
    const next = maximum < 0 ? -1 : Math.max(0, Math.min(maximum, Number(index)));
    this.state = { ...this.state, ui: { ...this.state.ui, candidateIndex: next } };
    this._emit("candidate-index", next);
  }

  setLocation(location) {
    this._set({ location, candidates: [], error: null }, "location", location);
  }

  clearLocation() {
    this.state = {
      ...this.state,
      location: null,
      candidates: [],
      error: null,
      ui: { ...this.state.ui, mode: "locating", candidateIndex: -1 },
    };
    this._emit("location-cleared");
  }

  setProject(project, question = undefined) {
    if (!project) return;
    const activeQuestion = question === undefined ? getQuestion({}, project) : question;
    const decision = getLatestDecision(project);
    this.state = {
      ...this.state,
      project,
      location: project.location || this.state.location,
      activeQuestion,
      decision,
      report: project.report || this.state.report,
      error: null,
      ui: {
        ...this.state.ui,
        mode: project.state || this.state.ui.mode,
        busy: ["analyzing", "reporting"].includes(project.state),
      },
    };
    this.rememberProject(project);
    this._emit("project", project);
  }

  ingest(payload, type = "payload") {
    if (!payload || typeof payload !== "object") return;
    const project = getProject(payload) || this.state.project;
    const question = getQuestion(payload, project);
    const job = payload.job || payload.data?.job || (payload.kind === "analysis" ? payload : null);
    const decision = getLatestDecision(project, payload) || this.state.decision;
    const report = payload.report || payload.data?.report || project?.report || this.state.report;
    this.state = {
      ...this.state,
      project,
      location: project?.location || this.state.location,
      activeQuestion: question,
      job: job || this.state.job,
      decision,
      report,
      error: null,
      ui: {
        ...this.state.ui,
        mode: project?.state || this.state.ui.mode,
        busy: ["queued", "running", "analyzing"].includes(String(job?.status || job?.state || project?.state || "").toLowerCase()),
      },
    };
    if (project) this.rememberProject(project);
    this._emit(type, payload);
  }

  setQuestion(question) {
    this._set({ activeQuestion: question, error: null }, "question", question);
  }

  setJob(job) {
    const status = String(job?.status || job?.state || "").toLowerCase();
    this.state = {
      ...this.state,
      job,
      ui: { ...this.state.ui, busy: ["queued", "running", "analyzing"].includes(status) },
    };
    this._emit("job", job);
  }

  setDecision(decision, report = null) {
    this.state = {
      ...this.state,
      decision,
      report: report || this.state.report,
      ui: { ...this.state.ui, mode: "decision_ready", busy: false },
    };
    this._emit("decision", decision);
  }

  setConnection(connection) {
    this._set({ connection }, "connection", connection);
  }

  setError(error) {
    const normalized = error ? {
      code: error.code || "UNEXPECTED_ERROR",
      message: error.message || String(error),
      retryable: Boolean(error.retryable),
      details: error.details || null,
    } : null;
    this.state = {
      ...this.state,
      error: normalized,
      ui: { ...this.state.ui, busy: false },
    };
    this._emit("error", normalized);
  }

  setUI(patch) {
    this.state = { ...this.state, ui: { ...this.state.ui, ...patch } };
    this._emit("ui", patch);
  }

  rememberProject(project) {
    if (!storageAvailable() || !project?.id) return;
    const location = project.location || {};
    const entry = {
      id: project.id,
      name: location.display_name || location.address || `项目 ${String(project.id).slice(0, 8)}`,
      state: project.state || "briefing",
      updated_at: project.updated_at || new Date().toISOString(),
    };
    try {
      const current = this.getRecentProjects().filter((item) => item.id !== entry.id);
      localStorage.setItem(this.storageKey, JSON.stringify([entry, ...current].slice(0, 5)));
    } catch {
      // Private browsing or a full storage quota must not block the project flow.
    }
  }

  getRecentProjects() {
    if (!storageAvailable()) return [];
    try {
      const parsed = JSON.parse(localStorage.getItem(this.storageKey) || "[]");
      return Array.isArray(parsed) ? parsed.slice(0, 5) : [];
    } catch {
      return [];
    }
  }

  clearTransient() {
    this.state = {
      ...this.state,
      candidates: [],
      error: null,
      ui: { ...this.state.ui, candidateIndex: -1 },
    };
    this._emit("transient-cleared");
  }
}
