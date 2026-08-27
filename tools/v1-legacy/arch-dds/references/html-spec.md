# ArchDDS HTML Specification

The output is a single HTML report that feels like a dense strategy deck, not a landing page.

## File Contract

- One standalone `.html` saved in the project folder.
- Inline CSS/JS unless the user asks for a web app.
- Major sections use stable IDs and `data-layout` / `data-role` annotations.
- Section order follows `report-framework.md`.
- Navigation anchors must match real section IDs.
- Include reading progress and play/PPT-style scrolling.

## Visual System

Use restrained editorial/report styling:

- Body font size: 16px or larger for Chinese text.
- Body line-height: 1.65-1.8.
- Avoid negative letter spacing.
- Use at least three functional color roles: paper/background, ink/text, accent, muted, line.
- Avoid a one-note palette. Do not let the report become only beige, only dark blue/slate, or only purple.
- Use cards only for repeated items, KPI blocks, image cases, and framed tools. Avoid cards inside cards.

Suggested CSS tokens:

```css
:root{
  --paper:#f7f3ea;
  --surface:#fffdf8;
  --ink:#24221d;
  --muted:#716b5f;
  --line:#ddd2bf;
  --clay:#a9553b;
  --sage:#6f7f66;
  --night:#1e211d;
  --gold:#c99a4a;
}
```

## Layout Components

Required patterns:

- `.wrap`: constrained content width, generous vertical padding.
- `.band`: full-width background band with inner `.wrap`.
- `.grid`: responsive grid using `repeat(auto-fit,minmax(...))`.
- `.table-wrap`: horizontal scroll wrapper for dense tables.
- `.kpi`: stable-height metric card.
- `.case-card`: image + short transferable tactic.

Dense tables:

```css
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
.tbl{width:100%;min-width:760px;border-collapse:collapse}
.tbl th,.tbl td{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}
.tbl th{font-size:13px;text-align:left;color:var(--muted);background:rgba(0,0,0,.03)}
```

Responsive baseline:

```css
@media (max-width: 960px){
  .split{grid-template-columns:1fr}
  .table-wrap .tbl{min-width:680px}
}
@media (max-width: 640px){
  body{font-size:16px}
  .wrap{padding:48px 18px}
  nav{overflow-x:auto}
}
```

## Play/PPT Scroll Control

All play buttons must share the same state. Use `data-play-toggle` on both nav and floating buttons.

```html
<button data-play-toggle aria-pressed="false">▶ 播放</button>
<button class="play-fab" data-play-toggle aria-label="自动播放报告">▶</button>
```

```js
const playToggles=[...document.querySelectorAll('[data-play-toggle]')];
let playing=false;
let playTimer=null;
const sections=[...document.querySelectorAll('main section[id]')];
let currentIndex=0;

function syncPlayState(){
  playToggles.forEach(btn=>{
    btn.classList.toggle('running',playing);
    btn.setAttribute('aria-pressed',String(playing));
    if(btn.textContent.trim().length>1) btn.textContent=playing?'暂停':'播放';
  });
}
function stopPlay(){ playing=false; clearInterval(playTimer); syncPlayState(); }
function startPlay(){
  if(!sections.length) return;
  playing=true; syncPlayState();
  playTimer=setInterval(()=>{
    currentIndex=(currentIndex+1)%sections.length;
    sections[currentIndex].scrollIntoView({behavior:'smooth',block:'start'});
  },5200);
}
playToggles.forEach(btn=>btn.addEventListener('click',()=>playing?stopPlay():startPlay()));
document.addEventListener('keydown',e=>{ if(e.key==='Escape') stopPlay(); });
```

## Images

- Prefer real project/case imagery over abstract decoration.
- Compress oversized images before base64 embedding.
- Every image needs meaningful `alt` text and a caption/source.
- Case cards must state what to copy from the reference.
- If the user says they can download missing images, leave a precise download list with case names and required views.

## Data And Source Presentation

- Put source tags in table cells or captions, not hidden comments.
- Use status badges: `已确认`, `待校准`, `待接入`, `冲突`.
- Include a provenance table near the end.
- Use `data-role="source-matrix"` on the provenance section.

## QA Checklist

Run focused checks before completion:

- All anchors in nav exist.
- Play/PPT control starts, scrolls, pauses, and updates all controls.
- Reading progress moves while scrolling.
- No table text collides or overflows outside wrappers.
- Images load; no broken `src`, no placeholder base64.
- No raw script or CSS appears after `</html>`.
- No temporary DDS numbers conflict with the formal DDS markdown/json.
- Mobile width still has readable text and non-overlapping controls.

If browser testing is impossible, state exactly which visual checks could not be run.
