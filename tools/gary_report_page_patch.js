/* Project-local Gary decision-report renderer patch.
 * The canonical DDS runtime remains the source of data, charts and diagrams;
 * this patch only changes the page composition so every evidence block is
 * rendered in the continuous reading view instead of being truncated to a
 * two-card visual rail.
 */

  function renderPromptBlock(block) {
    if (!block || typeof block !== "object") return "";
    const title = block.title || block.label || "数据抓取任务";
    const text = block.text || block.content || "";
    const items = Array.isArray(block.items) ? block.items : [];
    const refs = textList(block.source_refs);
    return `<section class="report-block gary-prompt-block gary-solid-plate" data-report-field="prompt" data-gary-node-id="prompt-${escapeHtml(title)}">
      <div class="gary-prompt-head"><span class="gary-block-eyebrow">可复制任务</span><button type="button" class="gary-copy-button" data-copy-prompt>复制提示词</button></div>
      <h3>${escapeHtml(title)}</h3>
      <pre data-prompt-text>${escapeHtml(String(text))}</pre>
      ${items.length ? `<ul class="gary-prompt-meta">${items.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : item?.text || item?.label || JSON.stringify(item))}</li>`).join("")}</ul>` : ""}
      ${refs.length ? `<p class="gary-prompt-source">关联证据：${escapeHtml(refs.join(" · "))}</p>` : ""}
    </section>`;
  }

  function renderGaryPage(page, index) {
    const pageCode = page.page_code || page.display_code || String(index + 1).padStart(2, "0");
    const title = page.title || page.heading || page.display_title || "未命名页面";
    const question = scalarText(page.decision_question || page.question || page.subtitle || "");
    const takeaway = scalarText(page.takeaway || page.conclusion || page.decision_message || page.summary || "");
    const impact = scalarText(page.decision_impact || page.action || page.recommendation || "");
    const gate = scalarText(page.decision_gate || page.gate || "");
    const owner = scalarText(page.owner || "");
    const conclusion = scalarText(page.conclusion || page.decision_message || page.summary || takeaway);
    const metrics = page.hero_metrics || page.metrics || [];
    const assetRefs = values(page.asset_refs);
    const blocks = [
      ...values(page.table), ...values(page.tables),
      ...values(page.case_mechanism).map((item) => ({ type: "case_mechanism", case_payload: item })),
      ...values(page.market_outcome_case).map((item) => ({ type: "market_outcome_case", case_payload: item })),
      ...(Array.isArray(page.blocks) ? page.blocks : []),
    ];
    if (!blocks.length && page.body) blocks.push({ type: "narrative", title: "正文", text: page.body });
    const charts = pageCharts(page);
    const diagrams = pageDiagrams(page);
    const hasVisual = Boolean(assetRefs.length || charts.length || diagrams.length);
    const visualStages = (charts.length || diagrams.length)
      ? `<div class="page-runtime-visuals ${charts.length ? "has-charts" : ""} ${diagrams.length ? "has-diagrams" : ""}" data-runtime-visuals><div data-chart-stage aria-label="数据图表"></div><div data-diagram-stage aria-label="建筑概念图"></div></div>`
      : "";
    const assetGallery = assetRefs.length
      ? `<div class="asset-gallery gary-asset-gallery asset-count-${Math.min(assetRefs.length, 3)}">${assetRefs.map((ref) => renderAsset(ref, title)).join("")}</div>`
      : "";
    const visualFallback = !hasVisual
      ? `<div class="gary-visual-empty"><span>证据形态</span><strong>本页以结构化数据、表格与原文为主</strong><p>没有可用比例图/现场图；不要用装饰性图片替代证据。</p></div>`
      : "";
    const heroMetrics = metrics.length
      ? `<div class="gary-metric-grid">${metrics.map(renderMetric).join("")}</div>`
      : "";
    const sourceCount = pageSourceRefs(page).length;
    const status = page.unit_status || page.confidence?.level || "partial";
    const statusText = status === "ready" ? "已具备" : status === "blocked" ? "阻断" : status === "partial" ? "部分支持" : String(status);
    const hero = index === 0 || page.is_hero;
    return `<article class="report-page-canvas prototype-v5 gary-report-page${hero ? " is-hero" : ""}" data-mounted-page="${index}" data-gary-report-page data-gary-material="regular" aria-label="第 ${index + 1} 页：${escapeHtml(title)}">
      ${renderBackdrop(page)}
      <div class="gary-page-layout">
        <section class="gary-page-primary" data-gary-node-id="primary-${escapeHtml(pageCode)}">
          <header class="gary-page-head">
            <div class="gary-page-title-group"><p class="gary-kicker">${escapeHtml(pageCode)} <span>／ ${escapeHtml(statusText)}</span></p><h1>${escapeHtml(title)}</h1></div>
            ${question ? `<p class="gary-page-question">${escapeHtml(question)}</p>` : ""}
          </header>
          <div class="gary-visual-frame${hasVisual ? " has-visual" : " no-visual"}" data-gary-node-id="visual-${escapeHtml(pageCode)}">
            ${assetGallery}${visualStages}${visualFallback}
          </div>
          ${heroMetrics}
          <div class="gary-evidence-grid" data-gary-node-id="evidence-${escapeHtml(pageCode)}">${blocks.map(renderBlock).join("")}</div>
        </section>
        <aside class="gary-decision-panel gary-glass gary-thick" data-gary-node-id="decision-${escapeHtml(pageCode)}" aria-label="本页判断">
          <div class="gary-decision-label">前策问题</div>
          <p class="gary-decision-question">${escapeHtml(question || "本页要回答什么？")}</p>
          <div class="gary-decision-label">明确结论</div>
          <p class="gary-decision-takeaway">${escapeHtml(takeaway || "当前没有可升级为结论的文本")}</p>
          ${impact ? `<div class="gary-decision-label">决策影响</div><p>${escapeHtml(impact)}</p>` : ""}
          ${gate ? `<div class="gary-gate"><span>放行闸门</span><p>${escapeHtml(gate)}</p></div>` : ""}
          ${owner ? `<p class="gary-owner"><span>责任</span>${escapeHtml(owner)}</p>` : ""}
        </aside>
        <section class="gary-conclusion-bar gary-solid-plate" data-gary-node-id="conclusion-${escapeHtml(pageCode)}">
          <span>本页结论</span><strong>${escapeHtml(conclusion || takeaway || "请回到证据块核对")}</strong>
        </section>
        <footer class="gary-page-footer"><span>${escapeHtml(pageCode)}</span><span>${sourceCount}项来源引用</span><span>DDS advisory · 仅供前期研判与设计输入</span></footer>
      </div>
      ${renderSources(page)}
    </article>`;
  }

  function attachPromptCopy() {
    if (document.body.dataset.promptCopyAttached === "true") return;
    document.body.dataset.promptCopyAttached = "true";
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-copy-prompt]");
      if (!button) return;
      const pre = button.closest("[data-report-field=prompt]")?.querySelector("[data-prompt-text]");
      if (!pre) return;
      const value = pre.textContent || "";
      const fallback = () => {
        const area = document.createElement("textarea");
        area.value = value;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
        area.remove();
        return ok;
      };
      const finish = (ok) => {
        const old = button.textContent;
        button.textContent = ok ? "已复制" : "请手动复制";
        window.setTimeout(() => { button.textContent = old; }, 1600);
      };
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(value).then(() => finish(true)).catch(() => finish(fallback()));
      } else finish(fallback());
    });
  }
