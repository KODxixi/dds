"""Inject the arch-front-html browser-QA API into compiled report HTML.

The V1 template remains byte-identical.  This module only transforms the
compiled string and fails closed when the private runtime markers drift.
"""

from __future__ import annotations


_RUNTIME_OPEN_MARKER = '    "use strict";\n'
_RUNTIME_CLOSE_MARKER = "    scheduleNavigationCollapse();\n  })();"
_EMPTY_MANIFEST_RETURN = """    if (!pageIndex.length) {
      renderChapters();
      renderRuntimeFallback(bootstrapIssue || "empty_manifest", bootstrapDetail);
      scheduleNavigationCollapse();
      return;
    }"""
_EMPTY_MANIFEST_RETURN_WITH_STATUS = """    if (!pageIndex.length) {
      renderChapters();
      renderRuntimeFallback(bootstrapIssue || "empty_manifest", bootstrapDetail);
      stage.dataset.runtimeReady = "error";
      stage.dataset.runtimeDiagnostic = bootstrapIssue || "empty_manifest";
      scheduleNavigationCollapse();
      return;
    }"""


_BRIDGE_JS = r"""
    function installBrowserQABridge() {
      const detachedSlot = document.createElement("span");
      detachedSlot.hidden = true;
      detachedSlot.setAttribute("aria-hidden", "true");
      const qaStyle = document.createElement("style");
      qaStyle.dataset.ddsBrowserQa = "runtime-contract";
      qaStyle.textContent = "html{scrollbar-width:none}html::-webkit-scrollbar{display:none;width:0;height:0}";
      document.head.appendChild(qaStyle);

      const mountedPages = () => new Map(
        [...stage.querySelectorAll(".page-mount[data-index]")].map(node => [
          Number(node.dataset.index),
          node
        ])
      );
      const slotAt = index => {
        const mount = mountedPages().get(Number(index));
        return mount?.querySelector(".page-surface") || mount || detachedSlot;
      };
      const slots = {
        get length() { return pageIndex.length; },
        filter(callback, thisArg) {
          const active = slotAt(currentIndex);
          return active !== detachedSlot && callback.call(thisArg, active, currentIndex, slots)
            ? [active]
            : [];
        }
      };
      pageIndex.forEach((entry, index) => {
        Object.defineProperty(slots, String(index), {
          enumerable: true,
          configurable: false,
          get: () => slotAt(index)
        });
      });

      const pages = pageIndex.map((entry, index) => pageAt(index) || {...entry});
      const state = {};
      Object.defineProperties(state, {
        activeIndex: {enumerable: true, get: () => currentIndex},
        activeMode: {enumerable: true, get: () => currentMode},
        slots: {enumerable: true, get: () => slots},
        mounted: {enumerable: true, get: mountedPages}
      });

      function syncSectionNavigation() {
        const root = document.querySelector("[data-chapter-list]");
        if (!root) return;
        root.setAttribute("data-section-nav", "");
        const sections = visibleSections();
        [...root.querySelectorAll(".chapter-button")].forEach((button, offset) => {
          const index = Number(sections[offset]?.start_index);
          if (!Number.isFinite(index)) return;
          button.dataset.navIndex = String(index);
          button.classList.toggle("is-active", index === currentIndex);
          if (button.dataset.qaNavigationBound === "true") return;
          button.dataset.qaNavigationBound = "true";
          button.addEventListener("click", syncSectionNavigation);
        });
      }

      function setActiveIndex(index, options = {}) {
        const numeric = Number(index);
        if (!Number.isFinite(numeric) || !pageIndex.length) return currentIndex;
        let target = Math.max(0, Math.min(pageIndex.length - 1, Math.trunc(numeric)));
        if (currentMode === "presentation" && !activeIndices().includes(target)) {
          target = activeIndices().at(-1) ?? 0;
        }
        currentIndex = target;
        renderWindow();
        if (options.updateHash !== false) writeHash();
        if (currentMode === "reading" && options.scroll !== false) {
          scheduleReadingAlignment(options.smooth === false || reducedMotion ? "auto" : "smooth");
        }
        syncSectionNavigation();
        return currentIndex;
      }

      function setRuntimeMode(mode, options = {}) {
        setMode(mode);
        if (options.restoreScroll === false && currentMode === "reading") {
          setActiveIndex(currentIndex, {scroll: false, updateHash: false});
        }
        syncSectionNavigation();
        return currentMode;
      }

      function moveRuntime(delta) {
        const sequence = activeIndices();
        const position = Math.max(
          0,
          Math.min(sequence.length - 1, activePosition() + Number(delta || 0))
        );
        return setActiveIndex(sequence[position] ?? currentIndex, {
          scroll: false,
          updateHash: false
        });
      }

      function preparePrint() {
        releaseMounted(printRoot);
        const sequence = activeIndices();
        const fragment = document.createDocumentFragment();
        sequence.slice(0, PRINT_BATCH_LIMIT).forEach(index => {
          const page = pageAt(index);
          if (!page) return;
          const mount = document.createElement("article");
          mount.className = "page-mount report-page print-page";
          mount.dataset.index = String(index);
          mount.dataset.distance = "0";
          mount.innerHTML = renderPageHTML(page, index);
          fragment.appendChild(mount);
        });
        printRoot.appendChild(fragment);
        printRoot.setAttribute("aria-hidden", "false");
        bindDecodedImages(printRoot);
        return printRoot.querySelectorAll(".print-page").length;
      }

      addEventListener("beforeprint", preparePrint);

      window.DDSReportRuntime = Object.freeze({
        apiVersion: "dds.browser-qa-runtime/1.0",
        documentData: Object.freeze({...metadata, page_chunks: {}, chunks: {}}),
        pages: Object.freeze(pages),
        state,
        setActiveIndex,
        preparePrint
      });
      window.DDSReportModes = Object.freeze({
        setMode: setRuntimeMode,
        next: () => moveRuntime(1),
        previous: () => moveRuntime(-1)
      });
      syncSectionNavigation();
      stage.dataset.runtimeReady = "true";
      delete stage.dataset.runtimeDiagnostic;
    }

    installBrowserQABridge();
"""


def inject_browser_qa_runtime(rendered: str) -> str:
    """Return compiled HTML with the stable QA API and runtime diagnostics."""
    if rendered.count(_RUNTIME_OPEN_MARKER) != 1:
        raise RuntimeError("DDS runtime opening marker is missing or ambiguous")
    if rendered.count(_RUNTIME_CLOSE_MARKER) != 1:
        raise RuntimeError("DDS runtime closing marker is missing or ambiguous")
    if rendered.count(_EMPTY_MANIFEST_RETURN) != 1:
        raise RuntimeError("DDS empty-manifest marker is missing or ambiguous")

    result = rendered.replace(
        _RUNTIME_OPEN_MARKER,
        f"{_RUNTIME_OPEN_MARKER}    try {{\n",
        1,
    )
    result = result.replace(
        _EMPTY_MANIFEST_RETURN,
        _EMPTY_MANIFEST_RETURN_WITH_STATUS,
        1,
    )
    runtime_tail = (
        "    scheduleNavigationCollapse();\n"
        f"{_BRIDGE_JS}\n"
        "    } catch (error) {\n"
        '      const failedStage = document.querySelector("[data-page-stage]");\n'
        "      const diagnostic = error instanceof Error ? error.message : String(error);\n"
        "      if (failedStage) {\n"
        '        failedStage.dataset.runtimeReady = "error";\n'
        "        failedStage.dataset.runtimeDiagnostic = diagnostic.slice(0, 500);\n"
        '        let notice = failedStage.querySelector("[data-runtime-error]");\n'
        "        if (!notice) {\n"
        '          notice = document.createElement("div");\n'
        '          notice.setAttribute("data-runtime-error", "");\n'
        '          notice.setAttribute("role", "alert");\n'
        '          notice.className = "dds-runtime-fallback";\n'
        "          failedStage.appendChild(notice);\n"
        "        }\n"
        '        notice.textContent = `DDS runtime initialization failed: ${diagnostic}`;\n'
        "      }\n"
        '      console.error("DDS runtime initialization failed", error);\n'
        "    }\n"
        "  })();"
    )
    return result.replace(_RUNTIME_CLOSE_MARKER, runtime_tail, 1)


__all__ = ["inject_browser_qa_runtime"]




