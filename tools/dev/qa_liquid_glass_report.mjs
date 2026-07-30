#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";

const VIEWPORTS = [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];
const GEOMETRY_TOLERANCE_PX = 1.5;
const CLIPPED_OVERFLOW_TOLERANCE_PX = 0;
const BROWSER_CHECK_NAMES = [
  "console_error_free",
  "first_to_last_scroll",
  "mounted_pages_bounded",
  "native_scrollbar_hidden",
  "offline_runtime",
  "presentation_navigation",
  "right_black_rail_absent",
  "source_drawer_union",
  "top_navigation",
  "visuals_readable",
];
const PRINT_CHECK_NAMES = [
  "console_error_free",
  "horizontal_overflow_absent",
  "images_exist_and_decode",
  "offline_runtime",
  "pages_exist",
  "print_media_active",
];

function failedChecks(names) {
  return Object.fromEntries(names.map(name => [name, false]));
}

function usage() {
  return [
    "Usage:",
    "  node tools/dev/qa_liquid_glass_report.mjs <report.html> <qa-dir>",
    "    --report-document-hash <64-hex-sha256>",
    "    [--expected-pages 12] [--pdf-name report-print.pdf]",
    "",
    "The script uses Playwright with an installed system Chrome. It never downloads a browser.",
  ].join("\n");
}

function parseArgs(argv) {
  const positional = [];
  const options = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) {
      positional.push(value);
      continue;
    }
    const [flag, inline] = value.split("=", 2);
    if (inline !== undefined) {
      options.set(flag, inline);
      continue;
    }
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) throw new Error(`Missing value for ${flag}`);
    options.set(flag, next);
    index += 1;
  }

  const report = options.get("--report") || positional[0];
  const qaDir = options.get("--qa-dir") || positional[1];
  const expectedPages = Number(options.get("--expected-pages") || "12");
  const pdfName = options.get("--pdf-name") || "report-print.pdf";
  const rawDocumentHash = options.get("--report-document-hash") || "";
  const reportDocumentHash = rawDocumentHash.replace(/^sha256:/i, "").toLowerCase();
  if (!report || !qaDir) throw new Error(usage());
  if (!/^[0-9a-f]{64}$/.test(reportDocumentHash)) {
    throw new Error("--report-document-hash must be a 64-hex SHA-256");
  }
  if (!Number.isInteger(expectedPages) || expectedPages < 1) {
    throw new Error("--expected-pages must be a positive integer");
  }
  if (path.basename(pdfName) !== pdfName || !pdfName.toLowerCase().endsWith(".pdf")) {
    throw new Error("--pdf-name must be a plain PDF file name");
  }
  return {
    reportPath: path.resolve(report),
    qaDir: path.resolve(qaDir),
    expectedPages,
    pdfName,
    reportDocumentHash,
  };
}

function serializeError(error) {
  if (error instanceof Error) {
    return { name: error.name, message: error.message, stack: error.stack || "" };
  }
  return { name: "Error", message: String(error), stack: "" };
}

function diagnosticStrings(values) {
  return values.map(value => (
    typeof value === "string" ? value : JSON.stringify(value)
  ));
}

async function loadPlaywright() {
  try {
    const imported = await import("playwright");
    return imported.default || imported;
  } catch (primaryError) {
    const roots = [
      ...(process.env.NODE_PATH || "").split(path.delimiter).filter(Boolean),
      path.join(
        homedir(),
        ".cache",
        "codex-runtimes",
        "codex-primary-runtime",
        "dependencies",
        "node",
        "node_modules",
      ),
    ];
    const require = createRequire(import.meta.url);
    for (const root of roots) {
      const packageRoot = path.join(root, "playwright");
      if (!existsSync(path.join(packageRoot, "package.json"))) continue;
      return require(packageRoot);
    }
    throw new Error(
      `Playwright is unavailable; no dependency was installed. Original error: ${primaryError.message}`,
    );
  }
}

function resolveSystemChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    path.join(
      process.env.LOCALAPPDATA || path.join(homedir(), "AppData", "Local"),
      "Google",
      "Chrome",
      "Application",
      "chrome.exe",
    ),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
  ].filter(Boolean);
  const chrome = candidates.find(candidate => existsSync(candidate));
  if (!chrome) {
    throw new Error(
      `System Chrome was not found. Checked: ${candidates.join(", ")}. Set CHROME_PATH explicitly.`,
    );
  }
  return chrome;
}

function normalizeRequestUrl(value) {
  try {
    const url = new URL(value);
    url.hash = "";
    return url.href;
  } catch {
    return value;
  }
}

function isExternalDependency(requestUrl, reportUrl) {
  const normalized = normalizeRequestUrl(requestUrl);
  if (normalized === normalizeRequestUrl(reportUrl)) return false;
  return !/^(?:data|blob|about):/i.test(normalized);
}

function sanitizeFilePart(value) {
  return String(value || "page")
    .normalize("NFKD")
    .replace(/[^\w.-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64) || "page";
}

async function sha256(filePath) {
  const data = await readFile(filePath);
  return createHash("sha256").update(data).digest("hex");
}

function attachDiagnostics(page, reportUrl) {
  const consoleErrors = [];
  const consoleWarnings = [];
  const pageErrors = [];
  const failedRequests = [];
  const externalRequests = new Set();

  page.on("console", message => {
    const record = {
      type: message.type(),
      text: message.text(),
      location: message.location(),
    };
    if (message.type() === "error") consoleErrors.push(record);
    if (message.type() === "warning") consoleWarnings.push(record);
  });
  page.on("pageerror", error => pageErrors.push(serializeError(error)));
  page.on("request", request => {
    if (isExternalDependency(request.url(), reportUrl)) externalRequests.add(request.url());
  });
  page.on("requestfailed", request => {
    failedRequests.push({
      url: request.url(),
      method: request.method(),
      failure: request.failure()?.errorText || "unknown",
    });
  });
  return {
    consoleErrors,
    consoleWarnings,
    pageErrors,
    failedRequests,
    externalRequests,
  };
}

async function waitForRuntime(page, expectedPages) {
  await page.waitForSelector('[data-page-stage][data-runtime-ready="true"]', {
    state: "attached",
    timeout: 30_000,
  });
  await page.waitForFunction(
    expected => (
      window.DDSReportRuntime?.pages?.length === expected
      && window.DDSReportRuntime?.state?.activeIndex >= 0
    ),
    expectedPages,
    { timeout: 30_000 },
  );
}

async function collectResourceDependencies(page, reportUrl) {
  const urls = await page.evaluate(() => (
    performance.getEntriesByType("resource").map(entry => entry.name)
  ));
  return [...new Set(urls.filter(url => isExternalDependency(url, reportUrl)))];
}

async function decodePayloadImages(page) {
  return page.evaluate(async () => {
    const safeImage = value => (
      typeof value === "string"
      && /^data:image\/(?:png|jpe?g|webp|avif|gif|svg\+xml);base64,/i.test(value)
    );
    const pages = Array.from(window.DDSReportRuntime?.pages || []);
    const sources = [...new Set(
      pages
        .flatMap(item => Array.isArray(item.blocks) ? item.blocks : [])
        .filter(block => ["image", "media"].includes(String(block?.type || "").toLowerCase()))
        .map(block => block.src || block.data_uri)
        .filter(safeImage),
    )];
    const results = [];
    for (let index = 0; index < sources.length; index += 1) {
      const source = sources[index];
      const image = new Image();
      image.src = source;
      let error = "";
      try {
        await image.decode();
      } catch (caught) {
        error = caught instanceof Error ? caught.message : String(caught);
      }
      results.push({
        image_index: index,
        mime: source.slice(5, source.indexOf(";")),
        bytes_base64: source.length - source.indexOf(",") - 1,
        complete: image.complete,
        natural_width: image.naturalWidth,
        natural_height: image.naturalHeight,
        decoded: !error && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0,
        error,
      });
    }
    return {
      count: results.length,
      passed: results.every(item => item.decoded),
      images: results,
    };
  });
}

async function inspectPresentationPage(page, expectedIndex) {
  return page.evaluate(({
    expectedIndex,
    geometryTolerance,
    clippedOverflowTolerance,
  }) => {
    const rectData = element => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        x: Number(rect.x.toFixed(2)),
        y: Number(rect.y.toFixed(2)),
        width: Number(rect.width.toFixed(2)),
        height: Number(rect.height.toFixed(2)),
        right: Number(rect.right.toFixed(2)),
        bottom: Number(rect.bottom.toFixed(2)),
      };
    };
    const nodeLabel = element => {
      const classes = Array.from(element.classList || []).slice(0, 3).join(".");
      return `${element.tagName.toLowerCase()}${classes ? `.${classes}` : ""}`;
    };
    const clipProbe = selector => {
      const root = document.querySelector(selector);
      if (!root) return { selector, exists: false, passed: false, reason: "missing" };
      const rootRect = root.getBoundingClientRect();
      const hiddenOverflow = [];
      for (const element of [root, ...root.querySelectorAll("*")]) {
        if (!(element instanceof HTMLElement)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = getComputedStyle(element);
        const clipsX = ["hidden", "clip"].includes(style.overflowX);
        const clipsY = ["hidden", "clip"].includes(style.overflowY);
        const overflowX = element.scrollWidth - element.clientWidth;
        const overflowY = element.scrollHeight - element.clientHeight;
        if (
          (clipsX && overflowX > clippedOverflowTolerance)
          || (clipsY && overflowY > clippedOverflowTolerance)
        ) {
          hiddenOverflow.push({
            node: nodeLabel(element),
            overflow_x_px: Number(overflowX.toFixed(2)),
            overflow_y_px: Number(overflowY.toFixed(2)),
          });
        }
      }
      const outOfBounds = [];
      for (const element of root.querySelectorAll("*")) {
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (
          rect.left < rootRect.left - geometryTolerance
          || rect.right > rootRect.right + geometryTolerance
          || rect.top < rootRect.top - geometryTolerance
          || rect.bottom > rootRect.bottom + geometryTolerance
        ) {
          outOfBounds.push({
            node: nodeLabel(element),
            rect: rectData(element),
          });
        }
      }
      const overflowX = root.scrollWidth - root.clientWidth;
      const overflowY = root.scrollHeight - root.clientHeight;
      return {
        selector,
        exists: true,
        rect: rectData(root),
        client: { width: root.clientWidth, height: root.clientHeight },
        scroll: { width: root.scrollWidth, height: root.scrollHeight },
        overflow_x_px: Number(overflowX.toFixed(2)),
        overflow_y_px: Number(overflowY.toFixed(2)),
        hidden_overflow: hiddenOverflow,
        out_of_bounds: outOfBounds.slice(0, 20),
        passed: (
          overflowX <= clippedOverflowTolerance
          && overflowY <= clippedOverflowTolerance
          && hiddenOverflow.length === 0
          && outOfBounds.length === 0
        ),
      };
    };

    const frame = document.querySelector("[data-presentation-frame]");
    const article = frame?.querySelector(".report-page");
    const bodyOverflow = document.body.scrollWidth - document.documentElement.clientWidth;
    const htmlOverflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    const frameOverflow = frame ? frame.scrollWidth - frame.clientWidth : Infinity;
    const articleOverflow = article ? article.scrollWidth - article.clientWidth : Infinity;
    const frameRect = rectData(frame);
    const articleRect = rectData(article);
    const withinViewport = Boolean(frameRect) && (
      frameRect.x >= -geometryTolerance
      && frameRect.y >= -geometryTolerance
      && frameRect.right <= innerWidth + geometryTolerance
      && frameRect.bottom <= innerHeight + geometryTolerance
    );
    const runtimeIndex = Number(window.DDSReportRuntime?.state?.activeIndex);
    const renderedId = article?.dataset.pageId || "";
    const runtimePage = window.DDSReportRuntime?.pages?.[runtimeIndex] || {};
    const primary = clipProbe("[data-presentation-frame] .primary-visual");
    const decision = clipProbe("[data-presentation-frame] .decision-card");
    const images = Array.from(article?.querySelectorAll("img") || []).map(image => ({
      alt: image.alt,
      complete: image.complete,
      natural_width: image.naturalWidth,
      natural_height: image.naturalHeight,
      decoded: image.complete && image.naturalWidth > 0 && image.naturalHeight > 0,
    }));
    const passed = (
      runtimeIndex === expectedIndex
      && bodyOverflow <= clippedOverflowTolerance
      && htmlOverflow <= clippedOverflowTolerance
      && frameOverflow <= clippedOverflowTolerance
      && articleOverflow <= clippedOverflowTolerance
      && withinViewport
      && primary.passed
      && decision.passed
      && images.every(image => image.decoded)
    );
    return {
      expected_index: expectedIndex,
      active_index: runtimeIndex,
      page_id: renderedId,
      display_code: runtimePage.display_code || runtimePage.page_code || "",
      display_title: runtimePage.display_title || runtimePage.title || "",
      frame: frameRect,
      article: articleRect,
      body_horizontal_overflow_px: Number(bodyOverflow.toFixed(2)),
      html_horizontal_overflow_px: Number(htmlOverflow.toFixed(2)),
      frame_horizontal_overflow_px: Number(frameOverflow.toFixed(2)),
      article_horizontal_overflow_px: Number(articleOverflow.toFixed(2)),
      frame_within_viewport: withinViewport,
      primary_visual: primary,
      decision_card: decision,
      rendered_images: images,
      passed,
    };
  }, {
    expectedIndex,
    geometryTolerance: GEOMETRY_TOLERANCE_PX,
    clippedOverflowTolerance: CLIPPED_OVERFLOW_TOLERANCE_PX,
  });
}

async function inspectNavigation(page, qaDir, viewport, saveScreenshots) {
  const buttons = page.locator("[data-section-nav] [data-nav-index]");
  const count = await buttons.count();
  const metadata = await page.evaluate(() => {
    const documentData = window.DDSReportRuntime?.documentData || {};
    const entries = Array.isArray(documentData.section_index)
      ? documentData.section_index
      : (Array.isArray(documentData.chapter_index) ? documentData.chapter_index : []);
    return {
      expected_visible_count: Math.min(entries.length, 8),
      entries: entries.slice(0, 8),
    };
  });
  const checks = [];
  for (let index = 0; index < count; index += 1) {
    const button = buttons.nth(index);
    const startIndex = Number(await button.getAttribute("data-nav-index"));
    const label = (await button.textContent() || "").trim();
    await button.click();
    await page.waitForFunction(
      expected => window.DDSReportRuntime?.state?.activeIndex === expected,
      startIndex,
      { timeout: 5_000 },
    );
    const state = await page.evaluate(expected => {
      const activeIndex = Number(window.DDSReportRuntime?.state?.activeIndex);
      const expectedPage = window.DDSReportRuntime?.pages?.[expected] || {};
      const article = document.querySelector("[data-presentation-frame] .report-page");
      return {
        active_index: activeIndex,
        expected_page_id: String(expectedPage.page_id || ""),
        rendered_page_id: String(article?.dataset.pageId || ""),
        active_nav_count: document.querySelectorAll(
          "[data-section-nav] [data-nav-index].is-active",
        ).length,
      };
    }, startIndex);
    const passed = (
      state.active_index === startIndex
      && state.expected_page_id === state.rendered_page_id
      && state.active_nav_count === 1
    );
    const check = {
      nav_index: index,
      label,
      start_index: startIndex,
      ...state,
      passed,
    };
    checks.push(check);
    if (saveScreenshots) {
      const screenshotName = [
        "key",
        `${viewport.width}x${viewport.height}`,
        String(index + 1).padStart(2, "0"),
        sanitizeFilePart(state.rendered_page_id),
      ].join("-") + ".png";
      await page.screenshot({
        path: path.join(qaDir, screenshotName),
        fullPage: false,
      });
      check.screenshot = screenshotName;
    }
  }
  return {
    rendered_count: count,
    expected_count: metadata.expected_visible_count,
    metadata_entries: metadata.entries,
    checks,
    passed: count === metadata.expected_visible_count && checks.every(check => check.passed),
  };
}

async function inspectReadingContract(page, expectedPages) {
  await page.evaluate(() => window.DDSReportModes.setMode("reading"));
  await page.evaluate(() => window.DDSReportRuntime.setActiveIndex(0));
  await page.waitForFunction(
    () => (
      window.DDSReportRuntime?.state?.activeMode === "reading"
      && window.DDSReportRuntime?.state?.activeIndex === 0
    ),
    { timeout: 5_000 },
  );
  const first = await page.evaluate(() => ({
    active_index: Number(window.DDSReportRuntime?.state?.activeIndex),
    mounted_pages: Number(window.DDSReportRuntime?.state?.mounted?.size),
    dataset_mounted_pages: Number(
      document.querySelector("[data-page-stage]")?.dataset.mountedPages,
    ),
    scroll_y: Number(scrollY),
  }));

  const lastIndex = Math.max(0, expectedPages - 1);
  await page.evaluate(
    index => window.DDSReportRuntime.setActiveIndex(index),
    lastIndex,
  );
  await page.waitForFunction(
    expected => window.DDSReportRuntime?.state?.activeIndex === expected,
    lastIndex,
    { timeout: 5_000 },
  );
  await page.waitForTimeout(80);
  const last = await page.evaluate(lastPageIndex => {
    const pageSources = item => [...new Set([
      ...(Array.isArray(item?.source_refs) ? item.source_refs : []),
      ...(Array.isArray(item?.blocks) ? item.blocks : [])
        .flatMap(block => Array.isArray(block?.source_refs) ? block.source_refs : []),
      ...(Array.isArray(item?.chart_specs) ? item.chart_specs : [])
        .flatMap(chart => Array.isArray(chart?.source_refs) ? chart.source_refs : []),
      ...(Array.isArray(item?.diagram_specs) ? item.diagram_specs : [])
        .flatMap(diagram => Array.isArray(diagram?.source_refs) ? diagram.source_refs : []),
    ].map(String).filter(Boolean))];
    const referencedSources = [...new Set(
      Array.from(window.DDSReportRuntime?.pages || []).flatMap(pageSources),
    )].sort();
    const displayedSources = [...new Set(
      Array.from(document.querySelectorAll("[data-source-list] .source-entry"))
        .map(entry => entry.querySelector("span")?.textContent?.trim() || "")
        .filter(Boolean),
    )].sort();
    const htmlStyle = getComputedStyle(document.documentElement);
    const bodyStyle = getComputedStyle(document.body);
    const scrollingElement = document.scrollingElement
      || document.documentElement;
    const scrollingStyle = getComputedStyle(scrollingElement);
    const sceneCoverage = [".scene-background", ".scene-mask"].map(selector => {
      const element = document.querySelector(selector);
      const rect = element?.getBoundingClientRect();
      return {
        selector,
        exists: Boolean(rect),
        covers_viewport: Boolean(rect) && (
          rect.left <= 0
          && rect.top <= 0
          && rect.right >= innerWidth
          && rect.bottom >= innerHeight
        ),
      };
    });
    const slot = document.querySelector(
      `.report-slot[data-index="${lastPageIndex}"]`,
    );
    const slotRect = slot?.getBoundingClientRect();
    return {
      active_index: Number(window.DDSReportRuntime?.state?.activeIndex),
      mounted_pages: Number(window.DDSReportRuntime?.state?.mounted?.size),
      dataset_mounted_pages: Number(
        document.querySelector("[data-page-stage]")?.dataset.mountedPages,
      ),
      scroll_y: Number(scrollY),
      last_slot_visible: Boolean(slotRect) && (
        slotRect.bottom > 0 && slotRect.top < innerHeight
      ),
      native_scrollbar_hidden: (
        scrollingStyle.scrollbarWidth === "none"
      ),
      scrollbar_width: {
        scrolling_element: scrollingElement.tagName.toLowerCase(),
        scrolling: scrollingStyle.scrollbarWidth,
        html: htmlStyle.scrollbarWidth,
        body: bodyStyle.scrollbarWidth,
      },
      referenced_sources: referencedSources,
      displayed_sources: displayedSources,
      source_drawer_union: (
        referencedSources.length === displayedSources.length
        && referencedSources.every(
          (sourceId, index) => sourceId === displayedSources[index],
        )
      ),
      scene_coverage: sceneCoverage,
      right_edge_covered: sceneCoverage.every(item => item.covers_viewport),
      body_horizontal_overflow_px: Math.max(
        0,
        document.body.scrollWidth - document.documentElement.clientWidth,
      ),
      html_horizontal_overflow_px: Math.max(
        0,
        document.documentElement.scrollWidth
          - document.documentElement.clientWidth,
      ),
    };
  }, lastIndex);
  const mountedPages = Math.max(
    first.mounted_pages,
    first.dataset_mounted_pages,
    last.mounted_pages,
    last.dataset_mounted_pages,
  );
  return {
    first,
    last,
    max_mounted_pages: mountedPages,
    first_to_last_scroll: (
      first.active_index === 0
      && last.active_index === lastIndex
      && last.last_slot_visible
      && (expectedPages === 1 || last.scroll_y > first.scroll_y)
    ),
    mounted_pages_bounded: (
      Number.isFinite(mountedPages)
      && mountedPages >= 1
      && mountedPages <= 7
    ),
  };
}

async function runViewport(browser, reportUrl, qaDir, viewport, expectedPages) {
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "zh-CN",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const diagnostics = attachDiagnostics(page, reportUrl);
  try {
    await page.goto(reportUrl, { waitUntil: "load", timeout: 30_000 });
    await waitForRuntime(page, expectedPages);
    const runtime = await page.evaluate(() => ({
      ready: document.querySelector("[data-page-stage]")?.dataset.runtimeReady || "",
      page_count: window.DDSReportRuntime?.pages?.length || 0,
      api_version: window.DDSReportRuntime?.apiVersion || "",
      error_visible: Boolean(document.querySelector("[data-runtime-error]")),
    }));
    const reading = await inspectReadingContract(page, expectedPages);
    const imagePayloads = await decodePayloadImages(page);

    await page.locator('[data-mode="presentation"]').click();
    await page.waitForSelector('body[data-mode="presentation"]', { state: "attached" });
    await page.evaluate(() => window.DDSReportRuntime.setActiveIndex(0));
    await page.waitForFunction(() => window.DDSReportRuntime?.state?.activeIndex === 0);

    const navigation = await inspectNavigation(
      page,
      qaDir,
      viewport,
      viewport.width === 1920 && viewport.height === 1080,
    );
    await page.evaluate(() => window.DDSReportRuntime.setActiveIndex(0));

    const pageChecks = [];
    for (let index = 0; index < expectedPages; index += 1) {
      if (index > 0) {
        await page.keyboard.press("ArrowRight");
        await page.waitForFunction(
          expected => window.DDSReportRuntime?.state?.activeIndex === expected,
          index,
          { timeout: 5_000 },
        );
      }
      pageChecks.push(await inspectPresentationPage(page, index));
    }

    const performanceExternal = await collectResourceDependencies(page, reportUrl);
    for (const url of performanceExternal) diagnostics.externalRequests.add(url);
    const externalRequests = [...diagnostics.externalRequests].sort();
    const horizontalOverflow = Math.max(
      0,
      reading.last.body_horizontal_overflow_px,
      reading.last.html_horizontal_overflow_px,
      ...pageChecks.flatMap(check => [
        check.body_horizontal_overflow_px,
        check.html_horizontal_overflow_px,
        check.frame_horizontal_overflow_px,
        check.article_horizontal_overflow_px,
      ]),
    );
    const checks = {
      console_error_free: (
        diagnostics.consoleErrors.length === 0
        && diagnostics.pageErrors.length === 0
      ),
      first_to_last_scroll: reading.first_to_last_scroll,
      mounted_pages_bounded: reading.mounted_pages_bounded,
      native_scrollbar_hidden: reading.last.native_scrollbar_hidden,
      offline_runtime: (
        diagnostics.failedRequests.length === 0
        && externalRequests.length === 0
      ),
      presentation_navigation: (
        pageChecks.length === expectedPages
        && pageChecks.every(check => (
          check.passed
          && check.expected_index === check.active_index
        ))
      ),
      right_black_rail_absent: (
        reading.last.right_edge_covered
        && horizontalOverflow === 0
      ),
      source_drawer_union: reading.last.source_drawer_union,
      top_navigation: navigation.passed,
      visuals_readable: (
        imagePayloads.passed
        && pageChecks.every(check => check.passed)
      ),
    };
    const dependencyRequests = [
      ...diagnosticStrings(diagnostics.failedRequests),
      ...externalRequests,
    ];
    const passed = (
      runtime.ready === "true"
      && runtime.page_count === expectedPages
      && runtime.error_visible === false
      && Object.values(checks).every(Boolean)
      && dependencyRequests.length === 0
    );
    return {
      width: viewport.width,
      height: viewport.height,
      checks,
      console_errors: diagnosticStrings(diagnostics.consoleErrors),
      page_errors: diagnosticStrings(diagnostics.pageErrors),
      dependency_requests: dependencyRequests,
      metrics: {
        horizontal_overflow_px: horizontalOverflow,
        runtime_ready: runtime.ready === "true",
        runtime_error_visible: runtime.error_visible,
        mounted_pages: reading.max_mounted_pages,
      },
      evidence: {
        runtime,
        reading,
        navigation,
        page_checks: pageChecks,
        image_payload_decode: imagePayloads,
        console_warnings: diagnostics.consoleWarnings,
      },
      passed,
    };
  } catch (error) {
    return {
      width: viewport.width,
      height: viewport.height,
      checks: failedChecks(BROWSER_CHECK_NAMES),
      console_errors: diagnosticStrings(diagnostics.consoleErrors),
      page_errors: [
        ...diagnosticStrings(diagnostics.pageErrors),
        JSON.stringify(serializeError(error)),
      ],
      dependency_requests: [
        ...diagnosticStrings(diagnostics.failedRequests),
        ...[...diagnostics.externalRequests].sort(),
      ],
      metrics: {
        horizontal_overflow_px: null,
        runtime_ready: false,
        runtime_error_visible: true,
        mounted_pages: 0,
      },
      passed: false,
      evidence: {
        fatal_error: serializeError(error),
        console_warnings: diagnostics.consoleWarnings,
      },
    };
  } finally {
    await context.close();
  }
}

function probePdf(pdfPath) {
  const pathCandidates = (process.env.PATH || "")
    .split(path.delimiter)
    .map(entry => entry.trim().replace(/^"(.*)"$/, "$1"))
    .filter(Boolean)
    .flatMap(directory => [
      path.join(directory, "pdfinfo.exe"),
      path.join(directory, "pdfinfo"),
    ]);
  const executable = [
    process.env.PDFINFO_PATH,
    path.join(
      homedir(),
      ".cache",
      "codex-runtimes",
      "codex-primary-runtime",
      "dependencies",
      "native",
      "poppler",
      "Library",
      "bin",
      "pdfinfo.exe",
    ),
    ...pathCandidates,
  ]
    .filter(Boolean)
    .find(candidate => existsSync(candidate));
  if (!executable) {
    return {
      method: "pdfinfo",
      executable: null,
      exit_code: null,
      error: {
        name: "MissingDependency",
        message: "Native pdfinfo executable was not found.",
        stack: "",
      },
      pages: null,
      width_points: null,
      height_points: null,
      raw_summary: "",
    };
  }
  const result = spawnSync(executable, [pdfPath], {
    encoding: "utf8",
    windowsHide: true,
  });
  const output = `${result.stdout || ""}\n${result.stderr || ""}`;
  const pagesMatch = output.match(/^Pages:\s+(\d+)/mi);
  const sizeMatch = output.match(/^Page size:\s+([\d.]+)\s+x\s+([\d.]+)\s+pts/mi);
  return {
    method: "pdfinfo",
    executable,
    exit_code: result.status,
    error: result.error ? serializeError(result.error) : null,
    pages: pagesMatch ? Number(pagesMatch[1]) : null,
    width_points: sizeMatch ? Number(sizeMatch[1]) : null,
    height_points: sizeMatch ? Number(sizeMatch[2]) : null,
    raw_summary: output
      .split(/\r?\n/)
      .filter(line => /^(Pages|Page size|File size|PDF version):/i.test(line))
      .join("\n"),
  };
}

async function inspectPrintLayout(page, expectedPages) {
  return page.evaluate(({
    expectedPages,
    geometryTolerance,
    clippedOverflowTolerance,
  }) => {
    const nodeLabel = element => {
      const classes = Array.from(element.classList || []).slice(0, 3).join(".");
      return `${element.tagName.toLowerCase()}${classes ? `.${classes}` : ""}`;
    };
    const clipProbe = root => {
      if (!root) return { exists: false, passed: false, reason: "missing" };
      const rootRect = root.getBoundingClientRect();
      const hiddenOverflow = [];
      const outOfBounds = [];
      for (const element of [root, ...root.querySelectorAll("*")]) {
        if (!(element instanceof HTMLElement)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const style = getComputedStyle(element);
        const overflowX = element.scrollWidth - element.clientWidth;
        const overflowY = element.scrollHeight - element.clientHeight;
        if (
          (
            ["hidden", "clip"].includes(style.overflowX)
            && overflowX > clippedOverflowTolerance
          )
          || (
            ["hidden", "clip"].includes(style.overflowY)
            && overflowY > clippedOverflowTolerance
          )
        ) {
          hiddenOverflow.push({
            node: nodeLabel(element),
            overflow_x_px: Number(overflowX.toFixed(2)),
            overflow_y_px: Number(overflowY.toFixed(2)),
          });
        }
        if (
          element !== root
          && (
            rect.left < rootRect.left - geometryTolerance
            || rect.right > rootRect.right + geometryTolerance
            || rect.top < rootRect.top - geometryTolerance
            || rect.bottom > rootRect.bottom + geometryTolerance
          )
        ) {
          outOfBounds.push(nodeLabel(element));
        }
      }
      const overflowX = root.scrollWidth - root.clientWidth;
      const overflowY = root.scrollHeight - root.clientHeight;
      return {
        exists: true,
        client: { width: root.clientWidth, height: root.clientHeight },
        scroll: { width: root.scrollWidth, height: root.scrollHeight },
        overflow_x_px: Number(overflowX.toFixed(2)),
        overflow_y_px: Number(overflowY.toFixed(2)),
        hidden_overflow: hiddenOverflow,
        out_of_bounds: outOfBounds.slice(0, 20),
        passed: (
          overflowX <= clippedOverflowTolerance
          && overflowY <= clippedOverflowTolerance
          && hiddenOverflow.length === 0
          && outOfBounds.length === 0
        ),
      };
    };
    const selfOverflowProbe = root => {
      if (!root) return { exists: false, passed: false, reason: "missing" };
      const overflowX = root.scrollWidth - root.clientWidth;
      const overflowY = root.scrollHeight - root.clientHeight;
      return {
        exists: true,
        client: { width: root.clientWidth, height: root.clientHeight },
        scroll: { width: root.scrollWidth, height: root.scrollHeight },
        overflow_x_px: Number(overflowX.toFixed(2)),
        overflow_y_px: Number(overflowY.toFixed(2)),
        hidden_overflow: [],
        out_of_bounds: [],
        passed: (
          overflowX <= clippedOverflowTolerance
          && overflowY <= clippedOverflowTolerance
        ),
      };
    };
    const pages = Array.from(document.querySelectorAll(".print-root .print-page"));
    const checks = pages.map((printPage, index) => {
      const rect = printPage.getBoundingClientRect();
      const primary = clipProbe(printPage.querySelector(".primary-visual"));
      const decision = clipProbe(printPage.querySelector(".decision-card"));
      const article = selfOverflowProbe(printPage.querySelector(".report-page"));
      return {
        index,
        page_id: printPage.querySelector(".report-page")?.dataset.pageId || "",
        width_px: Number(rect.width.toFixed(2)),
        height_px: Number(rect.height.toFixed(2)),
        aspect_ratio: Number((rect.width / rect.height).toFixed(6)),
        article,
        primary_visual: primary,
        decision_card: decision,
        passed: article.passed && primary.passed && decision.passed,
      };
    });
    const bodyHorizontalOverflow = document.body.scrollWidth - document.documentElement.clientWidth;
    return {
      expected_page_count: expectedPages,
      dom_page_count: pages.length,
      body_horizontal_overflow_px: Number(bodyHorizontalOverflow.toFixed(2)),
      pages: checks,
      passed: (
        pages.length === expectedPages
        && bodyHorizontalOverflow <= clippedOverflowTolerance
        && checks.every(check => check.passed)
      ),
    };
  }, {
    expectedPages,
    geometryTolerance: GEOMETRY_TOLERANCE_PX,
    clippedOverflowTolerance: CLIPPED_OVERFLOW_TOLERANCE_PX,
  });
}

async function inspectPrintContract(page) {
  return page.evaluate(async () => {
    const images = Array.from(
      document.querySelectorAll("[data-print-root] img"),
    );
    await Promise.all(images.map(async image => {
      try {
        await image.decode();
      } catch {
        // The returned decoded count keeps this failure visible to the gate.
      }
    }));
    const printPages = Array.from(
      document.querySelectorAll("[data-print-root] .print-page"),
    );
    const surfaces = Array.from(
      document.querySelectorAll("[data-print-root] .report-page"),
    );
    const visiblePages = printPages.filter(item => {
      const rect = item.getBoundingClientRect();
      const style = getComputedStyle(item);
      return (
        style.display !== "none"
        && style.visibility !== "hidden"
        && rect.width > 0
        && rect.height > 0
      );
    });
    return {
      media_print: matchMedia("print").matches,
      images: images.length,
      decoded_images: images.filter(
        image => (
          image.complete
          && image.naturalWidth > 0
          && image.naturalHeight > 0
        ),
      ).length,
      prepared_pages: printPages.length,
      surfaces: surfaces.length,
      visible_pages: visiblePages.length,
      horizontal_overflow_px: Math.max(
        0,
        document.body.scrollWidth - document.documentElement.clientWidth,
        document.documentElement.scrollWidth
          - document.documentElement.clientWidth,
      ),
    };
  });
}

async function runPrintQa(browser, reportUrl, qaDir, expectedPages, pdfName) {
  const viewport = { width: 1920, height: 1080 };
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    locale: "zh-CN",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const diagnostics = attachDiagnostics(page, reportUrl);
  const pdfPath = path.join(qaDir, pdfName);
  try {
    await page.goto(reportUrl, { waitUntil: "load", timeout: 30_000 });
    await waitForRuntime(page, expectedPages);
    const preparedPages = await page.evaluate(() => window.DDSReportRuntime.preparePrint());
    await page.emulateMedia({ media: "print" });
    await page.waitForTimeout(100);
    const layout = await inspectPrintLayout(page, expectedPages);
    const printContract = await inspectPrintContract(page);
    await page.pdf({
      path: pdfPath,
      printBackground: true,
      preferCSSPageSize: true,
    });
    const pdfInfo = probePdf(pdfPath);
    const pdfRatio = (
      pdfInfo.width_points && pdfInfo.height_points
        ? pdfInfo.width_points / pdfInfo.height_points
        : null
    );
    const performanceExternal = await collectResourceDependencies(page, reportUrl);
    for (const url of performanceExternal) diagnostics.externalRequests.add(url);
    const externalRequests = [...diagnostics.externalRequests].sort();
    const pdfPassed = (
      pdfInfo.pages === expectedPages
      && pdfRatio !== null
      && Math.abs(pdfRatio - (16 / 9)) <= 0.002
    );
    const dependencyRequests = [
      ...diagnosticStrings(diagnostics.failedRequests),
      ...externalRequests,
    ];
    const checks = {
      console_error_free: (
        diagnostics.consoleErrors.length === 0
        && diagnostics.pageErrors.length === 0
      ),
      horizontal_overflow_absent: (
        printContract.horizontal_overflow_px === 0
        && layout.body_horizontal_overflow_px <= 0
        && layout.pages.every(item => item.passed)
      ),
      images_exist_and_decode: (
        printContract.images === printContract.decoded_images
      ),
      offline_runtime: dependencyRequests.length === 0,
      pages_exist: (
        printContract.prepared_pages === expectedPages
        && printContract.surfaces === expectedPages
        && printContract.visible_pages === expectedPages
      ),
      print_media_active: printContract.media_print,
    };
    const passed = (
      preparedPages === expectedPages
      && layout.passed
      && pdfPassed
      && Object.values(checks).every(Boolean)
    );
    return {
      schema_version: "dds.print-browser-qa/1.0",
      html_hash: reportUrl,
      checks,
      console_errors: diagnosticStrings(diagnostics.consoleErrors),
      page_errors: diagnosticStrings(diagnostics.pageErrors),
      dependency_requests: dependencyRequests,
      metrics: printContract,
      viewport,
      screenshot: "",
      evidence: {
        media: "print",
        expected_page_count: expectedPages,
        prepared_page_count: preparedPages,
        geometry_tolerance_px: GEOMETRY_TOLERANCE_PX,
        clipped_overflow_tolerance_px: CLIPPED_OVERFLOW_TOLERANCE_PX,
        layout,
        pdf: {
          file: pdfPath,
          sha256: await sha256(pdfPath),
          ...pdfInfo,
          aspect_ratio: pdfRatio === null ? null : Number(pdfRatio.toFixed(6)),
          passed: pdfPassed,
        },
        console_warnings: diagnostics.consoleWarnings,
      },
      passed,
    };
  } catch (error) {
    return {
      schema_version: "dds.print-browser-qa/1.0",
      html_hash: "",
      checks: failedChecks(PRINT_CHECK_NAMES),
      console_errors: diagnosticStrings(diagnostics.consoleErrors),
      page_errors: [
        ...diagnosticStrings(diagnostics.pageErrors),
        JSON.stringify(serializeError(error)),
      ],
      dependency_requests: [
        ...diagnosticStrings(diagnostics.failedRequests),
        ...[...diagnostics.externalRequests].sort(),
      ],
      metrics: {
        decoded_images: 0,
        horizontal_overflow_px: null,
        images: 0,
        media_print: false,
        prepared_pages: 0,
        surfaces: 0,
        visible_pages: 0,
      },
      viewport,
      passed: false,
      screenshot: "",
      evidence: {
        fatal_error: serializeError(error),
        console_warnings: diagnostics.consoleWarnings,
      },
    };
  } finally {
    await context.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!existsSync(args.reportPath)) throw new Error(`Report not found: ${args.reportPath}`);
  await mkdir(args.qaDir, { recursive: true });
  const reportUrl = pathToFileURL(args.reportPath).href;
  const reportHash = await sha256(args.reportPath);
  const chromePath = resolveSystemChrome();
  const playwright = await loadPlaywright();
  const browser = await playwright.chromium.launch({
    executablePath: chromePath,
    headless: true,
    args: ["--allow-file-access-from-files"],
  });

  let browserQa;
  let printQa;
  try {
    const viewportResults = [];
    for (const viewport of VIEWPORTS) {
      viewportResults.push(
        await runViewport(
          browser,
          reportUrl,
          args.qaDir,
          viewport,
          args.expectedPages,
        ),
      );
    }
    const browserChecks = Object.fromEntries(
      BROWSER_CHECK_NAMES.map(name => [
        name,
        viewportResults.every(result => result.checks?.[name] === true),
      ]),
    );
    const dependencyRequests = [...new Set(
      viewportResults.flatMap(result => result.dependency_requests || []),
    )].sort();
    browserQa = {
      schema_version: "dds.browser-qa/1.0",
      runner: "playwright-node-system-chrome",
      generated_at: new Date().toISOString(),
      passed: (
        viewportResults.every(result => result.passed)
        && Object.values(browserChecks).every(Boolean)
        && dependencyRequests.length === 0
      ),
      report_document_hash: `sha256:${args.reportDocumentHash}`,
      html_hash: `sha256:${reportHash}`,
      checks: browserChecks,
      viewports: viewportResults,
      metrics: {
        dependency_request_count: dependencyRequests.length,
        max_mounted_pages: Math.max(
          ...viewportResults.map(
            result => Number(result.metrics?.mounted_pages || 0),
          ),
        ),
      },
      dependency_requests: dependencyRequests,
      artifact_directory: args.qaDir,
      evidence: {
        expected_page_count: args.expectedPages,
        geometry_tolerance_px: GEOMETRY_TOLERANCE_PX,
        clipped_overflow_tolerance_px: CLIPPED_OVERFLOW_TOLERANCE_PX,
        report: {
          file: args.reportPath,
          url: reportUrl,
        },
        browser: {
          engine: "playwright.chromium",
          product: "system Google Chrome",
          executable: chromePath,
          version: await browser.version(),
          headless: true,
        },
      },
    };
    await writeFile(
      path.join(args.qaDir, "browser_qa.json"),
      `${JSON.stringify(browserQa, null, 2)}\n`,
      "utf8",
    );

    printQa = await runPrintQa(
      browser,
      reportUrl,
      args.qaDir,
      args.expectedPages,
      args.pdfName,
    );
    printQa.schema_version = "dds.print-browser-qa/1.0";
    printQa.generated_at = new Date().toISOString();
    printQa.html_hash = `sha256:${reportHash}`;
    printQa.evidence = {
      ...printQa.evidence,
      report: {
        file: args.reportPath,
      },
      browser: {
        product: "system Google Chrome",
        executable: chromePath,
        version: await browser.version(),
      },
    };
    await writeFile(
      path.join(args.qaDir, "print_qa.json"),
      `${JSON.stringify(printQa, null, 2)}\n`,
      "utf8",
    );
  } finally {
    await browser.close();
  }

  const passed = Boolean(browserQa?.passed && printQa?.passed);
  process.stdout.write(`${JSON.stringify({
    passed,
    browser_qa: path.join(args.qaDir, "browser_qa.json"),
    print_qa: path.join(args.qaDir, "print_qa.json"),
    pdf: path.join(args.qaDir, args.pdfName),
  }, null, 2)}\n`);
  if (!passed) process.exitCode = 1;
}

try {
  await main();
} catch (error) {
  process.stderr.write(`${JSON.stringify(serializeError(error), null, 2)}\n`);
  process.exitCode = 1;
}
