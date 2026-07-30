(() => {
  "use strict";

  const instances = new WeakMap();

  function mount(root, options = {}) {
    if (!root || typeof root.querySelectorAll !== "function") {
      throw new TypeError("GaryDecisionReport.mount requires a root element");
    }
    if (instances.has(root)) return instances.get(root);

    const documentRef = root.ownerDocument;
    const view = documentRef.defaultView || globalThis;
    const mobile = view.matchMedia("(max-width: 720px)");
    const pages = Array.from(root.querySelectorAll("[data-gary-report-page]"));
    const pageButtons = Array.from(
      root.querySelectorAll("[data-gary-report-page-target]")
    );
    const modeButtons = Array.from(
      root.querySelectorAll("[data-gary-report-mode-target]")
    );
    const sources = root.querySelector("[data-gary-report-sources]");
    const openSources = root.querySelector("[data-gary-report-open-sources]");
    const closeSources = root.querySelector("[data-gary-report-close-sources]");
    const removers = [];
    let activeIndex = 0;
    let destroyed = false;

    function listen(target, type, handler) {
      if (!target) return;
      target.addEventListener(type, handler);
      removers.push(() => target.removeEventListener(type, handler));
    }

    function clampIndex(index) {
      if (!pages.length) return 0;
      return Math.max(0, Math.min(pages.length - 1, Number(index) || 0));
    }

    function syncPageState() {
      const presentation = root.dataset.garyReportMode === "presentation";
      pages.forEach((page, index) => {
        const active = index === activeIndex;
        page.classList.toggle("is-active", active);
        page.hidden = presentation && !active;
      });
      pageButtons.forEach((button) => {
        const active = Number(button.dataset.garyReportPageTarget) === activeIndex;
        button.classList.toggle("is-active", active);
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      });
    }

    function setActivePage(index, settings = {}) {
      if (destroyed) return activeIndex;
      activeIndex = clampIndex(index);
      syncPageState();
      if (
        pages[activeIndex] &&
        root.dataset.garyReportMode === "reading" &&
        settings.scroll !== false
      ) {
        pages[activeIndex].scrollIntoView({
          behavior: settings.smooth === false ? "auto" : "smooth",
          block: "start",
        });
      }
      options.onPageChange?.(activeIndex);
      return activeIndex;
    }

    function setMode(mode) {
      if (destroyed) return root.dataset.garyReportMode;
      const nextMode =
        mode === "presentation" && !mobile.matches ? "presentation" : "reading";
      root.dataset.garyReportMode = nextMode;
      modeButtons.forEach((button) => {
        const active = button.dataset.garyReportModeTarget === nextMode;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      syncPageState();
      options.onModeChange?.(nextMode);
      return nextMode;
    }

    function setSourcesOpen(open) {
      if (destroyed || !sources || !openSources) return false;
      const nextOpen = Boolean(open);
      sources.hidden = false;
      sources.classList.toggle("is-open", nextOpen);
      sources.setAttribute("aria-hidden", String(!nextOpen));
      openSources.setAttribute("aria-expanded", String(nextOpen));
      if (!nextOpen) {
        sources.hidden = true;
        openSources.focus();
      } else {
        closeSources?.focus();
      }
      options.onSourcesChange?.(nextOpen);
      return nextOpen;
    }

    pageButtons.forEach((button) => {
      listen(button, "click", () => {
        setActivePage(Number(button.dataset.garyReportPageTarget), {
          smooth: false,
        });
      });
    });
    modeButtons.forEach((button) => {
      listen(button, "click", () => setMode(button.dataset.garyReportModeTarget));
    });
    listen(openSources, "click", () => setSourcesOpen(true));
    listen(closeSources, "click", () => setSourcesOpen(false));
    listen(documentRef, "keydown", (event) => {
      if (event.key === "Escape" && sources?.classList.contains("is-open")) {
        setSourcesOpen(false);
        return;
      }
      if (root.dataset.garyReportMode !== "presentation") return;
      if (
        event.target instanceof view.HTMLElement &&
        event.target.closest("button, a, input, textarea, select")
      ) {
        return;
      }
      if (["ArrowRight", "ArrowDown", "PageDown"].includes(event.key)) {
        event.preventDefault();
        setActivePage(activeIndex + 1, { scroll: false });
      } else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(event.key)) {
        event.preventDefault();
        setActivePage(activeIndex - 1, { scroll: false });
      } else if (event.key === "Escape") {
        setMode("reading");
      }
    });
    listen(mobile, "change", () => {
      if (mobile.matches && root.dataset.garyReportMode === "presentation") {
        setMode("reading");
      }
    });

    const api = Object.freeze({
      setActivePage,
      setMode,
      setSourcesOpen,
      next: () => setActivePage(activeIndex + 1, { scroll: false }),
      previous: () => setActivePage(activeIndex - 1, { scroll: false }),
      destroy() {
        if (destroyed) return;
        destroyed = true;
        removers.splice(0).forEach((remove) => remove());
        instances.delete(root);
        delete root.dataset.garyControllerMounted;
      },
    });

    root.dataset.garyControllerMounted = "true";
    instances.set(root, api);
    setMode(root.dataset.garyReportMode);
    setActivePage(options.initialPage || 0, { scroll: false });
    return api;
  }

  globalThis.GaryDecisionReport = Object.freeze({ mount });
})();
