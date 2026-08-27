import { createConfig } from "./config.js";
import { ApiClient } from "./api/ApiClient.js";
import { ProjectStore } from "./state/ProjectStore.js";
import { SceneStore } from "./state/SceneStore.js";
import { CesiumSceneAdapter } from "./scene/CesiumSceneAdapter.js";
import { SceneDirector } from "./scene/SceneDirector.js";
import { EvidenceLayers } from "./scene/EvidenceLayers.js";
import { LayerRegistry } from "./scene/LayerRegistry.js";
import { MapShell } from "./scene/MapShell.js";
import { AnalysisPanel } from "./ui/AnalysisPanel.js";
import { ReportApp } from "./ui/ReportApp.js";

export async function mountReport(root, options = {}) {
  if (!root) throw new TypeError("mountReport(root) requires a report root element");
  if (root.__ddsReportApp) return root.__ddsReportApp;
  const config = createConfig({ ...options, root });
  const api = new ApiClient({ baseUrl: config.apiBase, pollInterval: config.pollInterval });
  const store = new ProjectStore({ quality: config.quality });
  const sceneStore = new SceneStore();
  const scene = new CesiumSceneAdapter();
  const director = new SceneDirector(scene);
  const evidenceLayers = new EvidenceLayers(director);
  const layerRegistry = new LayerRegistry(evidenceLayers);
  const mapShell = new MapShell(scene, { onMode: (mode) => sceneStore.patch({ mode }, "map-mode") });
  const analysisPanel = new AnalysisPanel(root.querySelector("#reportAnalysisPanel"), {
    onClose: () => root.classList.add("analysis-panel-collapsed"),
    onSelect: (id) => sceneStore.select(id),
    onPoiSelect: (id) => mapShell.focusPoi(id),
    onPoiRoute: (id, mode) => mapShell.routeToPoi(id, mode),
  });
  const app = new ReportApp({ root, config, api, store, scene, director, evidenceLayers, sceneStore, layerRegistry, mapShell, analysisPanel });
  root.__ddsReportApp = app;
  try {
    await app.mount();
    root.dataset.mounted = "true";
    return app;
  } catch (error) {
    root.dataset.mounted = "error";
    console.error("DDS report failed to mount", error);
    throw error;
  }
}

globalThis.DDSDecisionField = Object.assign(globalThis.DDSDecisionField || {}, { mountReport });

function autoMount() {
  const root = document.querySelector("#reportRoot");
  if (!root || root.dataset.autoMount === "false") return;
  mountReport(root).catch(() => {});
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount, { once: true });
else autoMount();
