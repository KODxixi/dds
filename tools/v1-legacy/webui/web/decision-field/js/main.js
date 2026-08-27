import { createConfig } from "./config.js";
import { ApiClient } from "./api/ApiClient.js";
import { ProjectStore } from "./state/ProjectStore.js";
import { CesiumSceneAdapter } from "./scene/CesiumSceneAdapter.js";
import { SceneDirector } from "./scene/SceneDirector.js";
import { EvidenceLayers } from "./scene/EvidenceLayers.js";
import { DecisionFieldApp } from "./ui/DecisionFieldApp.js";
import { MapWorkspaceChrome } from "./ui/MapWorkspaceChrome.js";

export async function mount(root, options = {}) {
  if (!root) throw new TypeError("mount(root) requires a Decision Field root element");
  if (root.__ddsDecisionFieldApp) return root.__ddsDecisionFieldApp;
  const config = createConfig({ ...options, root });
  const api = new ApiClient({ baseUrl: config.apiBase, pollInterval: config.pollInterval });
  const store = new ProjectStore({ quality: config.quality });
  const scene = new CesiumSceneAdapter();
  const director = new SceneDirector(scene);
  const evidenceLayers = new EvidenceLayers(director);
  const app = new DecisionFieldApp({ root, config, api, store, scene, director, evidenceLayers });
  root.__ddsDecisionFieldApp = app;
  try {
    await app.mount();
    if (String(root.dataset.surface || "hero").toLowerCase() === "map") {
      root.__ddsMapWorkspaceChrome = new MapWorkspaceChrome(app).mount();
    }
    root.dataset.mounted = "true";
    return app;
  } catch (error) {
    root.dataset.mounted = "error";
    const liveRegion = root.querySelector("#liveRegion");
    if (liveRegion) liveRegion.textContent = `Decision Field 启动失败：${error.message || error}`;
    console.error("DDS Decision Field failed to mount", error);
    throw error;
  }
}

globalThis.DDSDecisionField = Object.assign(globalThis.DDSDecisionField || {}, { mount });

function autoMount() {
  const root = document.querySelector("#decisionFieldRoot");
  if (!root || root.dataset.autoMount === "false") return;
  mount(root).catch(() => {});
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount, { once: true });
else autoMount();
