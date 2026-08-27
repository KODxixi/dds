import { CAMERA_PRESETS, SCENE_DIRECTIVE_TYPES } from "../config.js";

const ALLOWED = new Set(SCENE_DIRECTIVE_TYPES);
const PRESETS = new Set(Object.keys(CAMERA_PRESETS));

function abortResult(reason = "interrupted") {
  return { completed: false, cancelled: true, reason };
}

function directiveSignature(directive) {
  const safe = { ...directive };
  delete safe.id;
  delete safe.issued_at;
  return JSON.stringify(safe);
}

function normalizeDirective(directive) {
  if (!directive || typeof directive !== "object") throw new TypeError("SceneDirective must be an object");
  if (!ALLOWED.has(directive.type)) throw new TypeError(`SceneDirective type is not allowed: ${directive.type}`);
  if (directive.preset && !PRESETS.has(directive.preset)) throw new TypeError(`Unknown camera preset: ${directive.preset}`);
  if (directive.type === "fly_to" && !directive.target) throw new TypeError("fly_to requires target");
  return {
    interruptible: true,
    ...directive,
    preset: directive.preset || "site",
  };
}

export class SceneDirector {
  constructor(adapter, { onEvent = null } = {}) {
    if (!adapter) throw new TypeError("Scene adapter is required");
    this.adapter = adapter;
    this.onEvent = onEvent;
    this.active = null;
    this.queue = [];
    this.lastSignature = null;
    this.bookmarks = new Map();
    this.layerState = { parcel: true, competitors: true, resources: true, risks: true };
    this.errors = [];
    this._interactionListeners = [];
    this._sequence = 0;
  }

  bindUserControls(target = document) {
    this.unbindUserControls();
    const interrupt = (event) => {
      if (event.type === "keydown" && event.key !== "Escape") return;
      this.cancel("user_interaction");
      this.onEvent?.({ type: "control_returned", input: event.type });
    };
    [[target, "pointerdown", interrupt, { passive: true }], [target, "wheel", interrupt, { passive: true }], [target, "touchstart", interrupt, { passive: true }], [document, "keydown", interrupt, undefined]].forEach(([node, type, listener, options]) => {
      node?.addEventListener(type, listener, options);
      this._interactionListeners.push([node, type, listener, options]);
    });
    return () => this.unbindUserControls();
  }

  unbindUserControls() {
    this._interactionListeners.forEach(([node, type, listener, options]) => node?.removeEventListener(type, listener, options));
    this._interactionListeners = [];
  }

  execute(rawDirective) {
    let directive;
    try {
      directive = normalizeDirective(rawDirective);
    } catch (error) {
      this._recordError(error, rawDirective);
      return Promise.reject(error);
    }
    const signature = directiveSignature(directive);
    if (signature === this.lastSignature && (this.active || this.queue.length)) {
      return Promise.resolve({ completed: false, deduplicated: true });
    }
    this.lastSignature = signature;

    if (this.active?.directive.interruptible) this.cancel("superseded", { clearQueue: false });
    return new Promise((resolve) => {
      this.queue.push({ directive, resolve, sequence: ++this._sequence });
      if (this.queue.length > 5) {
        const dropped = this.queue.splice(0, this.queue.length - 5);
        dropped.forEach((item) => item.resolve(abortResult("queue_compacted")));
      }
      this._drain();
    });
  }

  async _drain() {
    if (this.active || !this.queue.length) return;
    const item = this.queue.shift();
    this.active = item;
    this.onEvent?.({ type: "directive_started", directive: item.directive });
    try {
      const result = await this._perform(item.directive);
      item.resolve(result || { completed: true });
      this.onEvent?.({ type: "directive_finished", directive: item.directive, result });
    } catch (error) {
      this._recordError(error, item.directive);
      item.resolve({ completed: false, error });
    } finally {
      if (this.active === item) this.active = null;
      this._drain();
    }
  }

  _perform(directive) {
    switch (directive.type) {
      case "fly_to":
        return this.adapter.flyTo(directive.target, directive.preset);
      case "focus_bounds": {
        const bounds = directive.bounds || directive.target?.bounds || directive.target;
        const target = {
          lng: (Number(bounds.west ?? bounds[0]) + Number(bounds.east ?? bounds[2])) / 2,
          lat: (Number(bounds.south ?? bounds[1]) + Number(bounds.north ?? bounds[3])) / 2,
        };
        return this.adapter.flyTo(target, directive.preset || "context");
      }
      case "set_layers":
        this.layerState = { ...this.layerState, ...(directive.layers || directive.target || {}) };
        return Promise.resolve(this.adapter.setLayers(this.layerState));
      case "highlight":
        this.adapter.highlight(directive.entity_ids || directive.entityIds || []);
        return Promise.resolve({ completed: true });
      case "restore_bookmark": {
        const bookmark = typeof directive.bookmark === "string"
          ? this.bookmarks.get(directive.bookmark)
          : directive.bookmark || this.bookmarks.get(directive.name);
        return this.adapter.restoreBookmark(bookmark);
      }
      case "play_tour": {
        const source = directive.bookmarks || [];
        const bookmarks = source.map((item) => typeof item === "string" ? this.bookmarks.get(item) : item).filter(Boolean);
        return this.adapter.playTour(bookmarks);
      }
      case "set_quality":
        return Promise.resolve({ completed: true, quality: this.adapter.setQuality(directive.profile || directive.quality) });
      default:
        throw new TypeError(`Unsupported directive: ${directive.type}`);
    }
  }

  cancel(reason = "interrupted", { clearQueue = true } = {}) {
    this.adapter.cancelFlight?.();
    if (this.active) {
      this.active.resolve(abortResult(reason));
      this.active = null;
    }
    if (clearQueue) {
      this.queue.splice(0).forEach((item) => item.resolve(abortResult(reason)));
    }
  }

  captureBookmark(name) {
    const bookmark = this.adapter.captureBookmark(name);
    if (bookmark) this.bookmarks.set(name, bookmark);
    return bookmark;
  }

  registerBookmarks(bookmarks = []) {
    (Array.isArray(bookmarks) ? bookmarks : Object.values(bookmarks || {})).forEach((bookmark) => {
      const name = bookmark?.name || bookmark?.id || bookmark?.section;
      if (name) this.bookmarks.set(name, bookmark);
    });
  }

  getBookmark(name) {
    return this.bookmarks.get(name) || null;
  }

  setEvidence(descriptors) {
    return this.adapter.setLayers({ ...this.layerState, evidence: descriptors });
  }

  setLayerVisibility(layer, visible) {
    if (!["parcel", "competitors", "resources", "risks", "buildings"].includes(layer)) return false;
    this.layerState = { ...this.layerState, [layer]: Boolean(visible) };
    this.adapter.setLayers({ [layer]: Boolean(visible) });
    return true;
  }

  syncProject(project) {
    const location = project?.location?.wgs84;
    if (!location) return Promise.resolve({ completed: false });
    const presetByState = {
      locating: "globe",
      site_locked: "site",
      briefing: "site",
      ready_for_analysis: "context",
      analyzing: "evidence",
      decision_ready: "presentation",
      reporting: "presentation",
    };
    return this.execute({
      type: "fly_to",
      target: location,
      preset: presetByState[project.state] || "site",
      interruptible: true,
    });
  }

  command(text, context = {}) {
    const command = String(text || "").trim().toLowerCase();
    const location = context.location?.wgs84 || context.location || context.project?.location?.wgs84;
    if (!command) return Promise.resolve({ completed: false });
    if (/(返回地块|回到地块|地块视角|site)/i.test(command) && location) {
      return this.execute({ type: "fly_to", target: location, preset: "site" });
    }
    if (/(查看竞品|竞品|market)/i.test(command)) {
      this.setLayerVisibility("competitors", true);
      this.setLayerVisibility("risks", false);
      return location ? this.execute({ type: "fly_to", target: location, preset: "evidence" }) : Promise.resolve({ completed: true });
    }
    if (/(只看风险|风险|risk)/i.test(command)) {
      ["competitors", "resources"].forEach((layer) => this.setLayerVisibility(layer, false));
      this.setLayerVisibility("risks", true);
      return location ? this.execute({ type: "fly_to", target: location, preset: "context" }) : Promise.resolve({ completed: true });
    }
    if (/(全部图层|显示全部|all layers)/i.test(command)) {
      ["parcel", "competitors", "resources", "risks"].forEach((layer) => this.setLayerVisibility(layer, true));
      return Promise.resolve({ completed: true });
    }
    if (/(全球|地球|globe)/i.test(command)) {
      return this.execute({ type: "fly_to", target: location || { lng: 105, lat: 35 }, preset: "globe" });
    }
    if (/(低画质|低负载|low)/i.test(command)) return this.execute({ type: "set_quality", profile: "low" });
    if (/(高画质|高质量|high)/i.test(command)) return this.execute({ type: "set_quality", profile: "high" });
    return Promise.resolve({ completed: false, unknown: true });
  }

  _recordError(error, directive) {
    const entry = { message: error.message || String(error), directive, at: new Date().toISOString() };
    this.errors.push(entry);
    if (this.errors.length > 20) this.errors.shift();
    this.onEvent?.({ type: "scene_error", ...entry });
  }

  destroy() {
    this.cancel("destroyed");
    this.unbindUserControls();
    this.bookmarks.clear();
    this.adapter.destroy();
  }
}
