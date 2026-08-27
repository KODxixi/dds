const DEFAULT_STATE = Object.freeze({ mode: "2d", basemap: "color", chapter: "decision", topic: "overview", selectedId: null, filters: {}, timelineIndex: 0, scenarioId: "baseline", playing: false });

export class SceneStore {
  constructor(initial = {}) { this.state = { ...DEFAULT_STATE, ...initial }; this.manifest = null; this.listeners = new Set(); }
  ingest(manifest) {
    if (!manifest || manifest.schema !== "dds-map-scene/v2") throw new TypeError("SceneManifest v2 is required");
    this.manifest = manifest; this._emit("manifest"); return manifest;
  }
  subscribe(listener) { this.listeners.add(listener); listener(this.snapshot(), "subscribe"); return () => this.listeners.delete(listener); }
  patch(next, reason = "update") { const value = typeof next === "function" ? next(this.snapshot()) : next; this.state = { ...this.state, ...(value || {}) }; this._emit(reason); return this.snapshot(); }
  snapshot() { return { ...this.state, manifest: this.manifest }; }
  chapter(id) { return this.manifest?.chapters?.find((item) => item.id === id) || null; }
  select(id) { return this.patch({ selectedId: id || null }, "select"); }
  _emit(reason) { const snapshot = this.snapshot(); this.listeners.forEach((listener) => listener(snapshot, reason)); }
}
