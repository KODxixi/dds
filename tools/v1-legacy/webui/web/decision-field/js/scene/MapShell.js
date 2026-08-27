export class MapShell {
  constructor(scene, { onMode = null } = {}) {
    this.scene = scene;
    this.onMode = onMode;
    this.mode = "2d";
    this.basemap = "color";
    this.location = null;
    this.retryTimer = null;
    this.retrying = false;
  }

  async initialize(location, { scheduleRetry = true } = {}) {
    this.location = location;
    try {
      await this.scene.enableAmap(location, { initialMode: "2d" });
      if (this.retryTimer) globalThis.clearTimeout(this.retryTimer);
      this.retryTimer = null;
      this.retrying = false;
      await this.setMode("2d", { animate: false, notify: false });
      this.scene.setAmapBasemap(this.basemap);
      return { mode: this.mode, engine: "amap" };
    } catch (error) {
      this.mode = "2d";
      this.scene.setFallbackLocation?.(location?.gcj02 || location?.wgs84 || location);
      this.scene._emitStatus?.({ mode: "2d", label: "本地数据视图 · 正在重连地图", tone: "warning", reason: error.message });
      this.onMode?.(this.mode);
      if (scheduleRetry) this._scheduleRetry();
      return { mode: this.mode, engine: "fallback", error };
    }
  }

  _scheduleRetry() {
    if (this.retryTimer || this.retrying || !this.location) return;
    this.retryTimer = globalThis.setTimeout(async () => {
      this.retryTimer = null;
      this.retrying = true;
      try {
        await this.initialize(this.location, { scheduleRetry: false });
      } finally {
        this.retrying = false;
      }
    }, 3000);
  }

  async setMode(mode, { animate = true, notify = true } = {}) {
    const next = mode === "3d" ? "3d" : "2d";
    if (!this.scene.amapMap) {
      this.mode = "2d";
      if (notify) this.onMode?.(this.mode);
      return this.mode;
    }
    this.scene.cancelFlight?.();
    this.mode = next;
    await this.scene.setAmapMode(next, { animate });
    if (next === "3d") this.scene.setAmapBasemap("color");
    else this.scene.setAmapBasemap(this.basemap);
    this.scene._emitStatus?.({
      mode: `amap-${next}`,
      label: next === "2d" ? "高德二维分析场" : "高德三维决策场",
      tone: "ok",
    });
    if (notify) this.onMode?.(next);
    return next;
  }

  setBasemap(mode = "color") {
    this.basemap = mode === "satellite" ? "satellite" : "color";
    if (this.mode === "2d") this.scene.setAmapBasemap(this.basemap);
    return this.basemap;
  }

  searchNearby(category, options = {}) {
    const city = options.city || this.location?.city || "";
    return this.scene.searchNearby(category, { center: this.location?.gcj02 || this.location, city, ...options });
  }

  getSiteContext() {
    return this.scene.getAmapSiteContext({
      center: this.location?.gcj02 || this.location,
      city: this.location?.city || "",
      district: this.location?.district || "",
    });
  }

  setLiveVisibility(visible) {
    this.scene.setAmapLiveVisibility(visible);
  }

  startMeasure(type) {
    return this.scene.startMeasure(type);
  }

  clearMeasure() {
    this.scene.clearMeasure();
  }

  focusPoi(id) {
    return this.scene.focusPoi(id);
  }

  routeToPoi(id, mode = "walking") {
    return this.scene.routeToPoi(id, mode);
  }

  reset() {
    const focus = this.scene.amapFocus;
    if (!focus || !this.scene.amapMap) return;
    this.scene.amapMap.setZoomAndCenter?.(this.mode === "2d" ? 14 : 15.2, [focus.lng, focus.lat]);
    this.scene.setAmapMode(this.mode, { animate: false });
  }
}
