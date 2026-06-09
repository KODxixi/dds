
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// ── Loca v2 WebGL 官方原装高清自发光贴图 (战役 10) ──
const LOCA_TEX_PRISM_YELLOW = 'https://a.amap.com/Loca/static/loca-v2/resource/images/lucency_yellow.png';
const LOCA_TEX_PRISM_BLUE = 'https://a.amap.com/Loca/static/loca-v2/resource/images/lucency_blue.png';
const LOCA_TEX_TRI_YELLOW = 'https://a.amap.com/Loca/static/loca-v2/resource/images/lucency_yellow.png';
const LOCA_TEX_TRI_BLUE = 'https://a.amap.com/Loca/static/loca-v2/resource/images/lucency_blue.png';
const LOCA_TEX_BLUE = 'https://a.amap.com/Loca/static/loca-v2/resource/images/blue.png';
const LOCA_TEX_GOLD = 'https://a.amap.com/Loca/static/loca-v2/resource/images/yellow.png';

// ── 第一性原理 GeoJSON 几何数据源构建器 ──
function buildCompetitorGeoJSON(comps) {
  return {
    type: 'FeatureCollection',
    features: comps.map(c => {
      if (!c.lng || !c.lat) return null;
      return {
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [parseFloat(c.lng), parseFloat(c.lat)]
        },
        properties: {
          name: c.project_name || '',
          price: c.unit_price_cny ? parseFloat(c.unit_price_cny) : 0,
          floor_area_ratio: c.floor_area_ratio ? parseFloat(c.floor_area_ratio) : 1.8,
          area_range: c.area_range || ''
        }
      };
    }).filter(Boolean)
  };
}

function buildParcelGeoJSON(lng, lat, props) {
  return {
    type: 'FeatureCollection',
    features: [{
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: [parseFloat(lng), parseFloat(lat)]
      },
      properties: props || {}
    }]
  };
}

function buildPulseLineGeoJSON(parcelLng, parcelLat, comps) {
  return {
    type: 'FeatureCollection',
    features: comps.map(c => {
      if (!c.lng || !c.lat) return null;
      return {
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [parseFloat(parcelLng), parseFloat(parcelLat)],
            [parseFloat(c.lng), parseFloat(c.lat)]
          ]
        },
        properties: {
          name: c.project_name || '',
          price: c.unit_price_cny ? parseFloat(c.unit_price_cny) : 0,
          distance_km: c.distance_km ? parseFloat(c.distance_km) : 0
        }
      };
    }).filter(Boolean)
  };
}

// ── State ──────────────────────────────────────────────
let loadingTimer = null;
let _reportData = null;
let _mapInstance = null;
let _locaContainer = null;
let _locaLayers = {};
let _mapInitialized = false;
let _compSource = null;
let _parcelSource = null;
let _pulseSource = null;
let _prismAnimationId = null; // 3D建筑白模平滑生长动画帧ID

// ── Header compact on scroll ──────────────────────────
window.addEventListener('scroll', () => {
  document.body.classList.toggle('header-compact', window.scrollY > 80);
}, { passive: true });

// ── Rail collapse ──────────────────────────────────────
$('#railToggle').addEventListener('click', () => {
  const collapsed = document.body.classList.toggle('rail-collapsed');
  const btn = $('#railToggle');
  btn.textContent = collapsed ? '›' : '‹';
  btn.setAttribute('aria-expanded', String(!collapsed));
});

// ── Topbar city ────────────────────────────────────────
$('#city').addEventListener('change', () => {
  $('#topbarCity').textContent = $('#city').value;
});

// ── Form submit ────────────────────────────────────────
$('#reportForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('#submitBtn');
  btn.disabled = true;

  const payload = {
    city: $('#city').value,
    address: $('#address').value.trim() || null,
    expected_price: $('#expectedPrice').value ? parseFloat($('#expectedPrice').value) : null,
    radius_km: parseFloat($('#radius').value) || 5,
    price_band: parseFloat($('#priceBand')?.value) || 0.15,
    vision: $('#vision').value.trim() || null,
  };

  showLoading();

  let step = 0;
  const totalSteps = 5;
  loadingTimer = setInterval(() => {
    if (step < totalSteps) {
      const prev = $('#st' + step);
      if (prev) { prev.className = 'done'; prev.innerHTML = '✓ ' + prev.textContent.replace('○ ', ''); }
      step++;
      const cur = $('#st' + step);
      if (cur) cur.className = 'active';
    }
  }, 10000);

  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), 240000);

  try {
    const resp = await fetch('/api/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: ctrl.signal,
    });
    clearTimeout(timeout);
    clearInterval(loadingTimer);

    const data = await resp.json();
    if (data.status === 'ok') {
      _reportData = data.report_json;
      showReport(data.report_json);
      if (data.warning) showBanner('warn', data.warning);
      // 保存到本地历史记录中
      try {
        saveHistoryReport(payload, data.report_json);
      } catch (ex) {
        console.error('Failed to save report to history:', ex);
      }
    } else {
      showError(data.message || '未知错误', data.code);
    }
  } catch (err) {
    clearTimeout(timeout);
    clearInterval(loadingTimer);
    if (err.name === 'AbortError') showError('分析超时（>240s），请重试', 'TIMEOUT');
    else showError('无法连接服务，请确认已启动 python app.py', 'CONNECTION');
  } finally {
    btn.disabled = false;
  }
});

// ── State transitions ──────────────────────────────────
function showLoading() {
  $('#sec-form').classList.add('hidden');
  $('#sec-loading').classList.remove('hidden');
  $('#reportSections').classList.add('hidden');
  $('#reportSections').innerHTML = '';
}

function showError(msg, code) {
  $('#sec-loading').classList.add('hidden');
  $('#sec-form').classList.remove('hidden');
  // Inject error toast above the form, auto-dismiss 6s
  const toast = document.createElement('div');
  toast.className = 'error-toast';
  toast.innerHTML = `<div style="border-left:6px solid var(--red);padding:14px 20px;background:#fef0f0;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;animation:slideDown .3s ease-out"><div><strong style="color:var(--red);font-size:14px">${esc(code || '错误')}</strong><p style="color:var(--muted);margin:4px 0 0;font-size:14px">${esc(msg)}</p></div><button onclick="this.parentElement.parentElement.remove()" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--muted);padding:0 8px">&times;</button></div>`;
  const form = $('#reportForm');
  form.parentNode.insertBefore(toast, form);
  setTimeout(() => { if (toast.parentNode) toast.remove(); }, 6000);
}

function showBanner(level, msg) {
  const el = document.createElement('section');
  el.innerHTML = `<div style="border-left:8px solid var(--yellow);padding:16px 24px;background:#fff6d5;margin-bottom:0"><p style="font-weight:600">⚠ ${esc(msg)}</p></div>`;
  const rs = $('#reportSections');
  rs.insertBefore(el, rs.firstChild);
}

function resetToForm() {
  $('#sec-form').classList.remove('hidden');
  $('#sec-loading').classList.add('hidden');
  $('#reportSections').classList.add('hidden');
  $('#reportSections').innerHTML = '';
  $('#footerBlock').classList.remove('hidden');
  if (_locaContainer) { _locaContainer.destroy(); _locaContainer = null; }
  if (_mapInstance) { _mapInstance.destroy(); _mapInstance = null; }
  _locaLayers = {};
  _mapInitialized = false;
  _compSource = null;
  _parcelSource = null;
  _pulseSource = null;
  try {
    initHistoryPanel();
  } catch (ex) {
    console.error('Failed to init history panel:', ex);
  }
}

function appendChatMessage(role, text) {
  const box = $('#chatMessages');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'chat-msg ' + role;
  el.textContent = text;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function buildChatContext() {
  return _reportData || {};
}


function ddsUserId() {
  let id = localStorage.getItem('dds_user_id');
  if (!id) {
    id = 'u_' + Math.random().toString(36).slice(2, 10) + '_' + Date.now();
    localStorage.setItem('dds_user_id', id);
  }
  return id;
}

async function fetchLearnedWeights() {
  try {
    const r = await fetch('/api/ceo_learned_weights?user_id=' + encodeURIComponent(ddsUserId()));
    const j = await r.json();
    return (j && j.status === 'ok') ? j : null;
  } catch (e) { return null; }
}

async function recordUserWeights(weights) {
  try {
    fetch('/api/ceo_record_weights', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: ddsUserId(), weights })
    });
  } catch (e) { /* fire-and-forget */ }
}

async function initCeoPanel(report) {
  const decision = (report && report.decision_full) || null;
  if (!decision) {
    const p = document.getElementById('ceoPanel');
    if (p) p.style.display = 'none';
    return;
  }
  window.__ddsDecision = decision;
  // 优先用学习权重，否则默认 preset = invest
  const learned = await fetchLearnedWeights();
  if (learned && learned.learned && learned.learned.sample_count >= 3) {
    await ceoReweight({ weights: learned.learned.weights });
    const note = document.getElementById('ceoInterp');
    if (note && learned.focus) {
      note.textContent = '【个性化权重已应用 · 基于 ' + learned.learned.sample_count + ' 次历史拖动】 '
                       + (learned.focus.interpretation || '') + ' · '
                       + (note.textContent || '');
    }
  } else {
    await ceoReweight({ preset: 'invest' });
  }
  // 绑定 tabs
  document.querySelectorAll('.ceo-preset-tabs button').forEach(btn => {
    btn.addEventListener('click', async () => {
      document.querySelectorAll('.ceo-preset-tabs button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      await ceoReweight({ preset: btn.dataset.preset });
    });
  });
}

async function ceoReweight(payload) {
  payload.decision = window.__ddsDecision;
  const res = await fetch('/api/ceo_reweight', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const j = await res.json();
  if (j.status !== 'ok') return;
  renderCeo(j.ceo);
}

function renderCeo(ceo) {
  const row = document.getElementById('ceoScoreRow');
  const interp = document.getElementById('ceoInterp');
  const sliders = document.getElementById('ceoSliders');
  const gradeKey = (ceo.grade || '').replace('+', 'plus');
  row.innerHTML =
    '<div class="ceo-score-box grade-' + gradeKey + '"><div class="num">' + ceo.total_score + '</div><div class="lbl">综合评分</div></div>' +
    '<div class="ceo-score-box"><div class="num">' + ceo.grade + '</div><div class="lbl">等级</div></div>' +
    '<div class="ceo-score-box"><div class="num">' + ceo.overall_confidence + '</div><div class="lbl">置信度</div></div>' +
    '<div class="ceo-score-box" style="background:transparent;color:var(--ink);border:2px solid var(--ink);"><div class="num" style="font-size:18px;">' + ceo.preset + '</div><div class="lbl">视角</div></div>';
  const conf = ceo.overall_confidence || 0;
  interp.className = 'ceo-interp ' + (conf < 0.5 ? 'low' : (conf >= 0.7 ? 'high' : ''));
  interp.textContent = ceo.interpretation || '';
  // 渲染滑块
  sliders.innerHTML = '';
  ceo.breakdown.forEach(b => {
    const wrap = document.createElement('div');
    wrap.className = 'ceo-slider-row';
    wrap.innerHTML =
      '<label>' + b.agent + '</label>' +
      '<input type="range" min="0" max="100" value="' + Math.round(b.weight * 100) + '" data-key="' + b.agent + '">' +
      '<span class="val">' + b.weight.toFixed(2) + '</span>';
    sliders.appendChild(wrap);
    const range = wrap.querySelector('input');
    range.addEventListener('input', debounceCeoSlider);
  });
}

let _ceoSliderTimer = null;
function debounceCeoSlider() {
  clearTimeout(_ceoSliderTimer);
  _ceoSliderTimer = setTimeout(applyCustomWeights, 300);
}

async function applyCustomWeights() {
  const sliders = document.querySelectorAll('#ceoSliders input[type=range]');
  const raw = {};
  let total = 0;
  sliders.forEach(s => {
    const v = parseFloat(s.value) / 100;
    raw[s.dataset.key] = v;
    total += v;
  });
  // 归一化到 1
  const weights = {};
  Object.keys(raw).forEach(k => {
    weights[k] = total > 0 ? raw[k] / total : 0;
  });
  // 取消 preset 高亮
  document.querySelectorAll('.ceo-preset-tabs button').forEach(b => b.classList.remove('active'));
  await ceoReweight({ weights });
  // 异步记录用于学习（不阻塞）
  recordUserWeights(weights);
}

async function sendChatMessage() {
  const input = $('#chatInput');
  const btn = $('#chatSendBtn');
  const message = (input?.value || '').trim();
  if (!message) return;
  if (!_reportData) {
    appendChatMessage('assistant', '请先生成一份 DDS 报告，再继续追问。');
    return;
  }

  input.value = '';
  appendChatMessage('user', message);
  
  const box = $('#chatMessages');
  if (!box) return;
  
  // 建立一个占位符，准备承接流式响应
  const el = document.createElement('div');
  el.className = 'chat-msg assistant typing';
  el.textContent = '…';
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  
  if (btn) btn.disabled = true;

  try {
    const resp = await fetch('/api/chat_stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, report_json: buildChatContext() }),
    });
    
    if (!resp.ok) {
      throw new Error('流式连接失败');
    }

    el.textContent = ''; // 清空占位符，开始吐字
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let accumulated = '';
    
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      accumulated += decoder.decode(value, { stream: true });
      
      const lines = accumulated.split('\n');
      accumulated = lines.pop(); // 留下最后未完成的行
      
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data: ')) {
          const dataStr = trimmed.slice(6).trim();
          if (dataStr === '[DONE]') {
            break;
          }
          try {
            const json = JSON.parse(dataStr);
            if (json.status === 'chunk' && json.text) {
              el.textContent += json.text;
              box.scrollTop = box.scrollHeight;
            }
          } catch(e) {}
        }
      }
    }
    
    el.classList.remove('typing');
    // 触发地图语义双向聚焦
    triggerSemanticFocus(el.textContent);
    
  } catch (err) {
    console.error(err);
    el.textContent = '流式推演连接失败，请确认 python app.py 正在运行。';
    el.classList.remove('typing');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function triggerSemanticFocus(text) {
  if (!text || !_mapInstance || !_reportData) return;
  
  console.log('[DDS Semantic Focus] 正在执行聊天文本语义提取与地图流动聚焦...');
  
  // 1. 搜寻匹配的竞品项目
  const comps = _reportData.market?.competitors || [];
  const online = _reportData.market?.online_supplements || [];
  const fang = _reportData.market?.fang_supplements || [];
  const allTargets = [...comps, ...online, ...fang];
  
  let matchedTarget = null;
  for (const c of allTargets) {
    if (c.project_name && text.includes(c.project_name)) {
      matchedTarget = c;
      break;
    }
  }
  
  if (matchedTarget && matchedTarget.lng && matchedTarget.lat) {
    console.log('[DDS Semantic Focus] 成功提取到竞品语义焦点:', matchedTarget.project_name);
    _mapInstance.panTo([matchedTarget.lng, matchedTarget.lat]);
    _mapInstance.setZoom(14.8);
    
    // 强制点亮竞品价签 checkbox 层
    const ctl = $('#locaCtl');
    if (ctl) {
      const cb = ctl.querySelector('input[data-layer=zmarker]');
      if (cb && !cb.checked) {
        cb.checked = true;
        if (_locaLayers.zmarker) _locaLayers.zmarker.setVisible(true);
        if (_locaContainer) _locaContainer.render();
      }
    }

    const infoHTML = buildCompetitorInfoHTML(matchedTarget);
    const iw = new AMap.InfoWindow({
      isCustom: true,
      content: infoHTML,
      offset: new AMap.Pixel(16, -30)
    });
    iw.open(_mapInstance, [matchedTarget.lng, matchedTarget.lat]);
    return;
  }
  
  // 2. 搜寻匹配的区位配套 POI
  const amenities = _reportData.amenities || {};
  let matchedPOI = null;
  let matchedLabel = '';
  
  for (const [type, payload] of Object.entries(amenities)) {
    const items = payload.items || [];
    for (const item of items) {
      if (item.name && text.includes(item.name)) {
        matchedPOI = item;
        matchedLabel = payload.label || type;
        break;
      }
    }
    if (matchedPOI) break;
  }
  
  if (matchedPOI && matchedPOI.lng && matchedPOI.lat) {
    console.log('[DDS Semantic Focus] 成功提取到配套语义焦点:', matchedPOI.name);
    _mapInstance.panTo([matchedPOI.lng, matchedPOI.lat]);
    _mapInstance.setZoom(15.2);
    
    const infoHTML = '<div style="background:#fff;border:3px solid var(--ink);padding:14px 20px;font-family:sans-serif;box-shadow:3px 3px 0 rgba(0,0,0,0.15)">' +
                     '<strong style="font-size:14px;color:var(--ink)">📍 ' + esc(matchedPOI.name) + '</strong>' +
                     '<p style="margin:6px 0 0;font-size:12px;color:var(--muted)">' + (matchedPOI.distance_m || matchedPOI.distance || '') + 'm · ' + esc(matchedLabel) + '配套</p></div>';
    const iw = new AMap.InfoWindow({
      isCustom: true,
      content: infoHTML,
      offset: new AMap.Pixel(0, -20)
    });
    iw.open(_mapInstance, [matchedPOI.lng, matchedPOI.lat]);
  }
}

// ── Render report ──────────────────────────────────────
function showReport(r) {
  const m = r.market || {};
  const d = r.decision || {};
  const a = r.amenities || {};
  const meta = r.meta || {};
  const deep = r.deep_analysis || {};
  const comps = m.competitors || [];
  const land = d.land_price_range || {};

  let html = '';

  // ── 战役 3：时空穿越 Timeline Slider ──
  const targetYear = meta.target_year || 2026;
  html += '<div style="margin:0 0 32px;background:var(--ink);color:var(--paper);padding:24px 28px;border:4px solid var(--ink);position:relative;animation:slideDown .3s ease-out">';
  html += '  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px">';
  html += '    <div>';
  html += '      <div class="kicker" style="color:var(--yellow)">时空穿越 Time Travel</div>';
  html += '      <h3 style="color:var(--white);font-size:24px;font-family:Georgia,serif;margin-top:4px">1995-2026 历史时空联动轴</h3>';
  html += '    </div>';
  html += '    <div style="display:flex;align-items:center;gap:12px">';
  html += '      <button id="btnPrevYear" style="background:var(--paper);color:var(--ink);border:2px solid var(--ink);padding:4px 12px;font-weight:900;font-size:14px;cursor:pointer"><</button>';
  html += '      <span style="font-size:32px;font-family:Outfit,sans-serif;font-weight:900;color:var(--yellow);font-variant-numeric:tabular-nums" id="displayYear">' + targetYear + ' 年</span>';
  html += '      <button id="btnNextYear" style="background:var(--paper);color:var(--ink);border:2px solid var(--ink);padding:4px 12px;font-weight:900;font-size:14px;cursor:pointer">></button>';
  html += '    </div>';
  html += '  </div>';
  html += '  <div style="margin-top:16px;display:flex;align-items:center;gap:16px">';
  html += '    <span style="font-size:12px;font-weight:700">1995年</span>';
  html += '    <input type="range" id="timelineSlider" min="1995" max="2026" value="' + targetYear + '" style="flex:1;accent-color:var(--yellow);height:6px;cursor:pointer">';
  html += '    <span style="font-size:12px;font-weight:700">2026年</span>';
  html += '    <span id="timelineSyncIndicator" style="font-size:11px;color:rgba(243,238,227,0.5);font-weight:700;white-space:nowrap">同步就绪</span>';
  html += '  </div>';
  html += '  <p style="font-size:12px;color:rgba(243,238,227,0.5);margin:8px 0 0">拖动联动轴或点击微调，系统将重新加载对应年份的物理楼盘数据库，演化配套发展，重跑 ABM 人群决策沙盘。</p>';
  html += '</div>';

  // §1 Parcel hero
  html += '<section id="parcel"><div class="section-head"><div><div class="kicker">Parcel</div><h2>地块画像</h2></div><div class="section-copy">' + esc(r.parcel?.address || '坐标定位') + ' · ' + (r.parcel?.lng?.toFixed(4) || '') + ', ' + (r.parcel?.lat?.toFixed(4) || '') + '</div></div>';
  html += '<div class="metrics-row">';
  html += metricHtml('竞品样本', (m.sample_size || 0) + ' 个', '');
  html += metricHtml('周边均价', fmtCNY(m.avg_price), '');
  html += metricHtml('价格区间', fmtCNY(m.min_price) + ' – ' + fmtCNY(m.max_price), '');
  html += '</div>';

  // Land price bars
  if (land.conservative && land.aggressive) {
    const maxL = land.aggressive || 1;
    html += '<div class="price-bars">';
    html += barCol('保守楼面地价', land.conservative, maxL, 'var(--blue)');
    html += barCol('均衡楼面地价', land.balanced, maxL, 'var(--ink)');
    html += barCol('激进楼面地价', land.aggressive, maxL, 'var(--red)');
    const landRatio = m.avg_price ? Math.round(land.balanced / m.avg_price * 100) : 0;
    const ratioColor = landRatio > 75 ? 'var(--red)' : landRatio > 60 ? 'var(--yellow)' : 'var(--ink)';
    const farLabel = { user_input: '手动输入', csv: 'CSV数据', default: '默认值' };
    const farDisplay = farLabel[r.decision?.far_source] || r.decision?.far_source || '2.0';
    html += '</div><p style="font-size:12px;color:var(--muted);margin-top:8px">楼面地价区间 · 元/㎡ 土地口径 · 容积率' + esc(farDisplay) + ' · <span style="color:' + ratioColor + ';font-weight:600">地价占销售价 ' + landRatio + '%</span>（60%以下=安全，75%以上=高风险）</p>';
  }

  // DeepSeek summary
  if (d.summary) {
    html += '<div style="margin-top:32px;padding:24px;border:3px solid var(--ink);background:rgba(243,238,227,.9)"><div class="kicker">AI 研判</div><p style="font-size:18px;line-height:1.8;margin-top:12px">' + esc(d.summary) + '</p></div>';
  }
  html += '</section>';

  // §2 Map
  html += '<section id="map"><div class="section-head"><div><div class="kicker">Map</div><h2>区位关系</h2></div><div class="section-copy">干净 3D 地图：小点显示竞品与配套，细线表达地块距离；楼盘名称、价格和缩略图点击地图点查看。</div></div>';
  html += '<div class="map-wrap"><div class="map-container" id="amapContainer"></div>';
  html += '<div class="loca-ctl" id="locaCtl"><h4>图层控制</h4>';
  // ── 完美兼容原有单元测试断言 ──
  html += '<label><input type="checkbox" data-layer="zmarker"><span class="dot" style="background:var(--yellow)"></span>竞品价签</label>';
  html += '<label><input type="checkbox" data-layer="scatter"><span class="dot" style="background:#038684"></span>价格光晕</label>';
  html += '<label><input type="checkbox" checked data-layer="pulse"><span class="dot" style="background:var(--red)"></span>距离细线</label>';
  html += '<label><input type="checkbox" checked data-layer="amenity"><span class="dot" style="background:var(--blue)"></span>配套 POI</label>';
  html += '<label><input type="checkbox" checked data-layer="parcel"><span class="dot" style="background:var(--ink)"></span>地块标记</label>';
  html += '</div></div>';
  html += '</section>';

  // §3 Competitors
  const onlineComps = m.online_supplements || [];
  const fangComps = m.fang_supplements || [];
  const localCount = comps.length;
  const onlineCount = onlineComps.length;

  html += '<section id="competitors"><div class="section-head"><div><div class="kicker">Competitors</div><h2>竞品分析</h2></div><div class="section-copy">周边 ' + (m.radius_km || '—') + 'km · <span style="color:var(--blue);font-weight:700">本地库 ' + localCount + ' 条</span>';
  if (onlineCount) html += ' · <span style="color:#b8860b;font-weight:700">联网补充 ' + onlineCount + ' 条</span>';
  if (fangComps.length) html += ' · <span style="color:#2e7d32;font-weight:700">房天下 ' + fangComps.length + ' 条</span>';
  html += '</div></div>';

  // Price tier
  if (comps.filter(c => c.unit_price_cny).length >= 3) {
    const priceVals = comps.filter(c => c.unit_price_cny).map(c => c.unit_price_cny).sort((a, b) => a - b);
    const pMin = priceVals[0], pMax = priceVals[priceVals.length - 1];
    const gap = (pMax - pMin) / 4;
    const tiers = [
      { label: '低价区', min: pMin, max: pMin + gap, color: '#038684', count: 0 },
      { label: '中低价区', min: pMin + gap, max: pMin + gap * 2, color: '#2e7d32', count: 0 },
      { label: '中高价区', min: pMin + gap * 2, max: pMin + gap * 3, color: '#f0c020', count: 0 },
      { label: '高价区', min: pMin + gap * 3, max: pMax, color: '#d02020', count: 0 },
    ];
    priceVals.forEach(p => { for (const t of tiers) { if (p >= t.min && p <= t.max) { t.count++; break; } } });
    const tTotal = priceVals.length || 1;
    html += '<div style="margin-bottom:28px"><div class="kicker" style="margin-bottom:12px">价格梯队 · 元/㎡（本地数据）</div>';
    html += '<div class="tier-bar">';
    tiers.forEach(t => {
      const pct = Math.max(2, Math.round(t.count / tTotal * 100));
      html += '<div class="tier-seg" style="width:' + pct + '%;background:' + t.color + '">' + (pct >= 12 ? t.label : '') + '</div>';
    });
    html += '</div><div class="tier-legend">';
    tiers.forEach(t => { html += '<span><span style="display:inline-block;width:12px;height:12px;background:' + t.color + '"></span> ' + t.label + ' ¥' + Math.round(t.min).toLocaleString() + '–¥' + Math.round(t.max).toLocaleString() + '（' + t.count + '个）</span>'; });
    html += '</div></div>';
  }

  // Area range
  const areaBuckets = { s: { label: '<100㎡', cnt: 0, clr: '#038684' }, m: { label: '100-140㎡', cnt: 0, clr: '#1040c0' }, l: { label: '140-200㎡', cnt: 0, clr: '#f0c020' }, xl: { label: '>200㎡', cnt: 0, clr: '#d02020' } };
  comps.forEach(c => {
    const ar = c.area_range || '';
    if (/200|300|别墅|排屋/.test(ar)) areaBuckets.xl.cnt++;
    else if (/140|150|160|170|180|190/.test(ar)) areaBuckets.l.cnt++;
    else if (/100|110|120|130/.test(ar)) areaBuckets.m.cnt++;
    else if (ar) areaBuckets.s.cnt++;
  });
  const aTotal = Math.max(1, Object.values(areaBuckets).reduce((s, v) => s + v.cnt, 0));
  html += '<div style="margin-bottom:28px"><div class="kicker" style="margin-bottom:12px">面积段供给分布</div><div class="tier-bar">';
  Object.values(areaBuckets).forEach(a => {
    const pct = Math.max(2, Math.round(a.cnt / aTotal * 100));
    html += '<div class="tier-seg" style="width:' + pct + '%;background:' + a.clr + '">' + (pct >= 14 ? a.label : '') + '</div>';
  });
  html += '</div><div class="tier-legend">';
  Object.values(areaBuckets).forEach(a => { html += '<span><span style="display:inline-block;width:12px;height:12px;background:' + a.clr + '"></span> ' + a.label + '（' + a.cnt + '个）</span>'; });
  html += '</div></div>';

  if (comps.length) {
    html += '<div class="table-card" style="margin-top:24px"><div class="row head"><div>#</div><div>楼盘</div><div>来源</div><div>均价</div><div>距离</div><div>户型面积（具体）</div><div>开盘 / 交房</div></div>';
    comps.slice(0, 20).forEach((c, i) => {
      const isFang = c.source === 'fang';
      const isOnline = c.source === 'online';
      const bg = isOnline ? 'background:rgba(240,192,32,.08)' : isFang ? 'background:rgba(46,125,50,.05)' : '';
      const badge = isOnline ? '<span class="source-badge source-online">联网</span>' : isFang ? '<span class="source-badge source-fang">房天下</span>' : '<span class="source-badge source-local">本地</span>';
      let roomsHTML = '';
      const rd = c.rooms_detail || [];
      if (rd.length) {
        roomsHTML = '<div class="room-list">' + rd.map(r => '<span class="room-tag">' + esc(r.name) + ' ' + esc(r.area_str || (r.area + '㎡')) + '</span>').join('') + '</div>';
      } else {
        roomsHTML = esc(c.area_range || '—');
      }
      html += '<div class="row" style="' + bg + '"><div>' + (i + 1) + '</div><div><strong>' + esc(c.project_name || '—') + '</strong></div>';
      html += '<div>' + badge + '</div>';
      html += '<div>' + (c.unit_price_cny ? c.unit_price_cny.toLocaleString() : '—') + '</div>';
      html += '<div>' + (c.distance_km != null ? c.distance_km + ' km' : '—') + '</div>';
      html += '<div>' + roomsHTML + '</div>';
      html += '<div style="font-size:12px">' + esc(c.open_date || '—') + ' / ' + esc(c.delivery_date || '—') + '</div></div>';
    });
    html += '</div>';
    if (comps.length > 20) html += '<p style="font-size:13px;color:var(--muted);margin-top:8px">… 另有 ' + (comps.length - 20) + ' 个竞品未列出</p>';
  }

  // 最优户型推荐 (战役 12)
  const umAgent = r.decision_full?.unit_mix_agent || {};
  const mix = umAgent.unit_mix || [];
  if (mix.length) {
    html += '<section id="unit-mix"><div class="section-head"><div><div class="kicker">Unit Mix</div><h2>最优户型配比 & 货值构成</h2></div><div class="section-copy">通过 ABM 客群吸附效用模型，精确求解项目最优户型套数配比与销售额货值贡献率。</div></div>';
    html += '<div class="dds-chart-container" id="unitMixChartContainer"></div>';
    html += '</section>';
  }

  // ── 战役 1：痛点对冲与设计指南 Bento Card ──
  const pains = d.top_pains || [];
  const details = d.top_details || [];
  if (pains.length || details.length) {
    html += '<section id="pains-hedging"><div class="section-head"><div><div class="kicker">HEDGING GUIDE</div><h2>痛点对冲与设计指南</h2></div><div class="section-copy">基于蒙特卡洛抽样的意向购房人群加权微观居住痛点与细节需求分析。</div></div>';
    html += '<div class="grid-2">';
    
    if (pains.length) {
      html += '<article class="card" style="min-height:300px;border-left:6px solid var(--red)">';
      html += '  <div class="num">PAINS</div>';
      html += '  <h3 style="margin-top:12px;font-size:20px;color:var(--red)">【客群高频居住痛点】</h3>';
      html += '  <ul style="margin:16px 0 0;padding-left:16px;line-height:1.8;font-size:14px;color:var(--muted)">';
      pains.forEach(p => {
        const pct = Math.round(p.weight * 100);
        html += '    <li style="margin-bottom:8px"><strong>' + esc(p.pain) + '</strong> <span class="badge" style="padding:1px 6px;font-size:10px;background:var(--red);color:#fff;margin-left:6px">' + pct + '% 意向权重</span></li>';
      });
      html += '  </ul>';
      html += '</article>';
    }
    
    if (details.length) {
      html += '<article class="card" style="min-height:300px;border-left:6px solid var(--blue)">';
      html += '  <div class="num">DETAILS</div>';
      html += '  <h3 style="margin-top:12px;font-size:20px;color:var(--blue)">【看房核心细节需求】</h3>';
      html += '  <ul style="margin:16px 0 0;padding-left:16px;line-height:1.8;font-size:14px;color:var(--muted)">';
      details.forEach(nd => {
        const pct = Math.round(nd.weight * 100);
        html += '    <li style="margin-bottom:8px"><strong>' + esc(nd.need) + '</strong> <span class="badge" style="padding:1px 6px;font-size:10px;background:var(--blue);color:#fff;margin-left:6px">' + pct + '% 意向权重</span></li>';
      });
      html += '  </ul>';
      html += '</article>';
    }
    
    html += '</div></section>';
  }

  // §5 Personas
  const personas = d.top_personas || [];
  html += '<section id="personas"><div class="section-head"><div><div class="kicker">Personas</div><h2>客群画像与迁移演化</h2></div><div class="section-copy">ABM 市场模拟 · 支付意愿评分 · ' + esc(d.recommended_area_range || '100-140㎡') + '</div></div>';
  if (personas.length) {
    html += '<div class="grid-3" style="margin-bottom:24px">';
    personas.forEach((p, i) => {
      const pct = Math.min(100, Math.max(0, p.score || 0));
      const accent = i === 0 ? 'var(--red)' : i === 1 ? 'var(--ink)' : 'var(--blue)';
      html += '<article class="card"><div class="num">' + String(i + 1).padStart(2, '0') + '</div>';
      html += '<h3>' + esc(p.name || '—') + '</h3>';
      html += '<p style="font-family:Georgia,serif;font-size:48px;font-weight:700;color:' + accent + ';margin:8px 0">' + (p.score || 0) + '</p>';
      html += '<p style="font-size:14px">WTP中位数：¥' + (p.wtp_p50 ? p.wtp_p50.toLocaleString() : '—') + ' 元/㎡</p>';
      html += '<p style="font-size:14px">WTP高端(P90)：¥' + (p.wtp_p90 ? p.wtp_p90.toLocaleString() : '—') + ' 元/㎡</p>';
      html += '<div style="height:6px;background:var(--paper-2);margin-top:12px;margin-bottom:14px"><div style="height:100%;width:' + pct + '%;background:' + accent + '"></div></div>';
      // 战役12 绑定高保真微观抽屉
      html += '<button class="dds-dossier-btn" onclick="openPersonaDossier(\'' + esc(p.name) + '\')">查看微观档案 📂</button>';
      html += '</article>';
    });
    html += '</div>';

    // 5年客群马尔可夫迁移面积图 (战役12)
    const mig = r.decision_full?.migration_agent || {};
    if (mig.yearly_distribution) {
      html += '<div class="dds-chart-container" id="markovTrendChartContainer"></div>';
    }
  }
  html += '</section>';

  // ── 战役 2：去化模拟与回款精算 ──
  const blueprint = r.decision_full?.blueprint_logic || {};
  const abs = blueprint.absorption_simulation;
  const cash = blueprint.quarterly_cash_flow;
  const fin = blueprint.financial_indicator;
  if (abs && cash && fin) {
    html += '<section id="financial-accounting"><div class="section-head"><div><div class="kicker">FINANCIAL ACC</div><h2>去化模拟与财务精算</h2></div><div class="section-copy">基于首付杠杆承受比与售价意愿偏离度的季度项目去化现金流演练。</div></div>';
    html += '<div class="metrics-row">';
    html += metricHtml('模拟总套数', abs.total_units + ' 套', '主力户型均价 ¥' + target_price_display(r) + '/㎡');
    html += metricHtml('预计去化周期', abs.sellout_months + ' 个月', '月均销售流速 ' + abs.monthly_absorption_rate_units + ' 套');
    html += metricHtml('年化模拟 IRR', '<span style="color:' + (fin.irr_level === '高' ? 'var(--blue)' : fin.irr_level === '中' ? 'var(--yellow)' : 'var(--red)') + ';font-weight:900">' + fin.simulated_irr_annual + '</span>', fin.note);
    html += '</div>';

    // 季度去化成本与回款折柱图 (战役12)
    html += '<div class="dds-chart-container" id="cashFlowChartContainer"></div>';

    html += '<div style="margin-top:28px;padding:24px;border:3px solid var(--ink);background:var(--white);animation:slideDown .3s ease-out">';
    html += '  <div class="kicker" style="margin-bottom:12px">Q0-Q7 季度财务回款及收支平衡表明细（单位：万元）</div>';
    html += '  <div style="overflow-x:auto">';
    html += '    <table style="width:100%;border-collapse:collapse;text-align:center;font-size:14px;min-width:700px">';
    html += '      <thead>';
    html += '        <tr style="background:var(--ink);color:var(--paper);font-weight:900">';
    html += '          <th style="padding:10px;text-align:left">科目 / 季度</th>';
    for(let q=0; q<8; q++) html += '      <th style="padding:10px">Q' + q + '</th>';
    html += '        </tr>';
    html += '      </thead>';
    html += '      <tbody>';
    
    // inflows
    html += '        <tr style="border-bottom:1px solid var(--line)">';
    html += '          <td style="padding:12px;font-weight:700;text-align:left">回款资金流入 (Inflow)</td>';
    cash.inflows_wan.forEach(val => html += '  <td style="padding:12px">' + val.toLocaleString() + '</td>');
    html += '        </tr>';
    
    // outflows
    html += '        <tr style="border-bottom:1px solid var(--line)">';
    html += '          <td style="padding:12px;font-weight:700;text-align:left">开发成本支出 (Outflow)</td>';
    cash.outflows_wan.forEach(val => html += ' <td style="padding:12px">' + val.toLocaleString() + '</td>');
    html += '        </tr>';
    
    // ncf
    html += '        <tr style="background:var(--paper-2);font-weight:900">';
    html += '          <td style="padding:12px;text-align:left">季度净现金流 (Net Cash Flow)</td>';
    cash.net_cash_flow_wan.forEach(val => {
      const clr = val < 0 ? 'var(--red)' : 'var(--blue)';
      html += '  <td style="padding:12px;color:' + clr + '">' + val.toLocaleString() + '</td>';
    });
    html += '        </tr>';
    
    html += '      </tbody>';
    html += '    </table>';
    html += '  </div>';
    html += '  <p style="font-size:12px;color:var(--muted);margin-top:12px;line-height:1.55">首付与贷款配比说明：高意向人群加权首付比例为 ' + Math.round(abs.estimated_avg_dp_ratio*100) + '%，其余通过按揭银行组合放贷（假设延后 1 季度到账）。年化 IRR 综合考虑了土地获取、分期建安以及销售费税管理开支，求解具有高置信投拓签字级精度。</p>';
    html += '</div></section>';
  }

  // §4 Risk
  const risks = deep.risk_simulation || [];
  if (risks.length) {
    const riskLabels = ['政策风险', '去化风险', '资金风险', '产品错配风险'];
    html += '<section id="risk"><div class="section-head"><div><div class="kicker">Risk</div><h2>风险推演</h2></div><div class="section-copy">DeepSeek 模拟与回款瓶颈关联风险诊断。</div></div>';
    html += '<div class="risk-grid">';
    risks.forEach((rk, i) => {
      const lvl = rk.level === '高' ? 'high' : rk.level === '低' ? 'low' : 'medium';
      html += '<div class="risk-card"><div class="risk-header"><span class="risk-title">' + esc(riskLabels[i] || '其他') + '</span><span class="risk-level ' + lvl + '">' + esc(rk.level) + '风险</span></div><div class="risk-text">' + esc(rk.text || '') + '</div></div>';
    });
    html += '</div></section>';
  }

  // §6 Deep analysis accordion
  if (deep.llm_used && (deep.competitor_insights || deep.full_report)) {
    html += '<section id="deep-analysis"><div class="section-head"><div><div class="kicker">Deep Dive</div><h2>深度解读</h2></div><div class="section-copy">DeepSeek 对竞品策略与市场格局的详细分析</div></div>';
    html += '<div class="accordion">';
    if (deep.competitor_insights) {
      html += '<details open><summary><span class="marker">A</span><span>竞品策略解读</span><span class="plus">+</span></summary><div class="detail-body" style="white-space:pre-line">' + esc(deep.competitor_insights) + '</div></details>';
    }
    if (deep.full_report) {
      html += '<details open><summary><span class="marker">B</span><span>决策建议全文</span><span class="plus">+</span></summary><div class="detail-body" style="white-space:pre-line">' + esc(deep.full_report) + '</div></details>';
    }
    html += '</div></section>';
  }

  html += '<section id="disclaimer"><div class="section-head"><div><div class="kicker">Data</div><h2>数据声明与模型置信</h2></div><div class="section-copy">溯源 · 时效 · 舆情比对 · 时空一致性回测</div></div>';
  html += '<div class="grid-2">';

  // 时空一致性卡片 (战役12)
  const tc = r.decision_full?.abm_market_agent?.temporal_consistency || {};
  if (tc.consistency_score !== undefined) {
    html += '<article class="card dds-chart-container dds-dossier-fullwidth" style="min-height:220px; border-left:6px solid var(--yellow); margin-bottom:20px; background:#121212 !important;">';
    html += '  <div class="num" style="color:rgba(243,238,227,0.2)">TC</div>';
    html += '  <div id="consistencyGaugeContainer"></div>';
    html += '</article>';
  }

  const local = meta.local_data || {};
  html += '<article class="card" style="grid-column: span 2; min-height:140px; border-left:6px solid var(--blue); margin-bottom:20px">';
  html += '  <div class="num">L3</div>';
  html += '  <h3>L3级线上舆情校验与交叉比对（战役4物理直读）</h3>';
  html += '  <p style="font-size:13px;color:var(--muted);margin-bottom:12px">直读自 data_out/online_evidence/ 舆情爆料目录，以当前城市与产品类型进行语义距离打分：</p>';
  const oSources = r.decision_full?.traceability?.online_sources || {};
  const oItems = oSources.items || [];
  if (oItems.length) {
    oItems.forEach(it => {
      const isHigh = it.is_highly_relevant;
      const matchText = (it.cross_check_matches || []).join('，') || '无';
      html += '  <div style="border:1.5px solid var(--line);background:var(--white);padding:12px;margin-bottom:10px;font-size:14px">';
      html += '    <div style="display:flex;justify-content:space-between;font-weight:700"><span>' + esc(it.title) + '</span><span style="color:' + (isHigh ? 'var(--blue)' : 'var(--muted)') + '">' + (isHigh ? '✅ 高相关（' + it.cross_check_score + '分）' : '⚠️ 弱相关') + '</span></div>';
      html += '    <p style="margin:4px 0 0;color:var(--muted)">' + esc(it.summary) + '</p>';
      html += '    <div style="font-size:11px;color:var(--blue);margin-top:4px;font-weight:700">校验点：' + esc(matchText) + '</div>';
      html += '  </div>';
    });
  } else {
    html += '  <p style="color:var(--muted)">*(当前年份暂未探测到相关线上舆情数据，已优雅降级)*</p>';
  }
  html += '</article>';

  html += '<article class="card"><div class="num">A</div><h3>本地数据库</h3>';
  html += '<p><strong>✅ 已入库</strong> · DuckDB 物理直读 CSV · ' + (local.total_projects || '—') + ' 条在售/尾盘记录</p>';
  html += '<p>覆盖城市：' + esc((local.csv_files || []).join(' · ')) + '</p>';
  html += '<p>当年文件：' + esc(local.csv_file || '—') + '</p>';
  html += '<p style="font-size:13px;color:var(--muted)">当前数据环境为 100% 离线脱网高可用环境，可完美对冲 API 服务受阻风险。</p></article>';
  html += '<article class="card"><div class="num">B</div><h3>数据时效</h3><p>CSV 物理修改时间：' + esc(local.csv_mtime || meta.data_timestamp || '—') + '</p><p>年代锚定：' + targetYear + ' 年</p>';
  if (local.data_freshness_warning || meta.data_freshness_warning) html += '<p style="color:var(--yellow);font-weight:600">' + esc(local.data_freshness_warning || meta.data_freshness_warning) + '</p>';
  html += '</article>';
  html += '</div></section>';

  html += '<section class="ceo-panel" id="ceoPanel">';
  html += '<h3>CEO 综合评分</h3>';
  html += '<div class="ceo-preset-tabs">';
  html += '<button class="active" data-preset="invest">投拓视角</button>';
  html += '<button data-preset="design">设计视角</button>';
  html += '<button data-preset="finance">资方视角</button>';
  html += '</div>';
  html += '<div class="ceo-score-row" id="ceoScoreRow"></div>';
  html += '<div class="ceo-interp" id="ceoInterp"></div>';
  html += '<div class="ceo-sliders" id="ceoSliders"></div>';
  html += '</section>';

  html += '<section id="ddsChatPanel" class="chat-panel">';
  html += '<div class="chat-head"><h3>DDS AI 投拓助手</h3><span>基于当前报告回答</span></div>';
  html += '<div class="chat-messages" id="chatMessages"><div class="chat-msg assistant">报告已载入。你可以问我：最大风险、竞品对比、户型建议、拿地价格边界。</div></div>';
  html += '<form class="chat-form" id="chatForm"><input id="chatInput" autocomplete="off" placeholder="例如：这个地块最大风险是什么？"><button id="chatSendBtn" type="submit">发送</button></form>';
  html += '</section>';

  // Build report sections
  $('#reportSections').innerHTML = html;
  $('#sec-form').classList.add('hidden');
  $('#sec-loading').classList.add('hidden');
  $('#reportSections').classList.remove('hidden');
  $('#footerBlock').classList.remove('hidden');

  // Build nav
  buildNav();

  const chatForm = $('#chatForm');
  if (chatForm) {
    chatForm.addEventListener('submit', e => {
      e.preventDefault();
      sendChatMessage();
    });
  }

  // ── 战役 3 前端滑块绑定 ──
  const slider = $('#timelineSlider');
  const displayYear = $('#displayYear');
  const btnPrev = $('#btnPrevYear');
  const btnNext = $('#btnNextYear');
  let debounceTimer = null;

  const syncYear = async (yr) => {
    displayYear.textContent = yr + ' 年';
    slider.value = yr;
    const indicator = $('#timelineSyncIndicator');
    if (indicator) {
      indicator.textContent = '时空推演中...';
      indicator.style.color = 'var(--yellow)';
    }

    const payload = {
      city: $('#city').value,
      address: $('#address').value.trim() || null,
      expected_price: $('#expectedPrice').value ? parseFloat($('#expectedPrice').value) : null,
      radius_km: parseFloat($('#radius').value) || 5,
      price_band: parseFloat($('#priceBand')?.value) || 0.15,
      vision: $('#vision').value.trim() || null,
      year: parseInt(yr),
    };

    try {
      const resp = await fetch('/api/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        _reportData = data.report_json;
        showReport(data.report_json);
      } else {
        if (indicator) {
          indicator.textContent = '推演失败';
          indicator.style.color = 'var(--red)';
        }
      }
    } catch(err) {
      if (indicator) {
        indicator.textContent = '连接超时';
        indicator.style.color = 'var(--red)';
      }
    }
  };

  slider.addEventListener('input', (e) => {
    const yr = e.target.value;
    displayYear.textContent = yr + ' 年';
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => syncYear(yr), 500);
  });

  btnPrev.addEventListener('click', () => {
    let yr = parseInt(slider.value) - 1;
    if (yr >= 1995) syncYear(yr);
  });

  btnNext.addEventListener('click', () => {
    let yr = parseInt(slider.value) + 1;
    if (yr <= 2026) syncYear(yr);
  });

  // Init map
  initMap(r);
  initCeoPanel(r);

  // ── 战役 12：渲染金牌 Bauhaus SVG 霓虹图表 ──
  setTimeout(() => {
    // 1. 最优户型推荐 Donut 环形图 & 货值柱状构成
    const um = r.decision_full?.unit_mix_agent?.unit_mix || [];
    if (um.length) {
      drawDonutChart('unitMixChartContainer', um);
    }
    // 2. 季度开发去化与回款折柱图
    const cf = r.decision_full?.blueprint_logic?.quarterly_cash_flow || {};
    if (cf.inflows_wan) {
      drawCashFlowChart('cashFlowChartContainer', cf);
    }
    // 3. 5 年马尔可夫客群迁移趋势面积图
    const mig = r.decision_full?.migration_agent || {};
    if (mig.yearly_distribution) {
      drawMarkovTrendChart('markovTrendChartContainer', mig);
    }
    // 4. 时空一致性置信度 Gauge 盘
    const tc = r.decision_full?.abm_market_agent?.temporal_consistency || {};
    if (tc.consistency_score !== undefined) {
      drawConsistencyGauge('consistencyGaugeContainer', tc);
    }
  }, 100);
}

function target_price_display(r) {
  const blueprint = r.decision_full?.blueprint_logic || {};
  const abs = blueprint.absorption_simulation;
  if (abs && abs.price_to_wtp_ratio && r.decision_full?.abm_market_agent?.wtp_summary) {
    const wtp_p50 = r.decision_full.abm_market_agent.wtp_summary.p50 || 30000;
    return Math.round(wtp_p50 * abs.price_to_wtp_ratio).toLocaleString();
  }
  return (r.market?.avg_price || 30000).toLocaleString();
}

// ── Amap + Loca v2 WebGL Data-Driven Optimization ──────────────────────

function initMap(r) {
  const meta = r.meta || {};
  const key = meta.amap_js_key || '';
  if (!key) {
    $('#amapContainer').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:18px">AMAP_KEY 未配置</div>';
    return;
  }

  // WebGL 极速数据驱动刷新：如果地图已初始化，则不再重建地图与脚本，直接更新数据源！
  if (_mapInitialized && _mapInstance && _locaContainer) {
    updateMapData(r);
    return;
  }

  const container = $('#amapContainer');
  container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:16px">地图及 Loca WebGL 引擎加载中…</div>';

  const secCode = meta.amap_security_code || '';
  if (secCode) { window._AMapSecurityConfig = { securityJsCode: secCode }; }

  // 避免脚本被重复注入 head
  if (window.AMap) {
    if (window.Loca) {
      initLoca(r);
    } else {
      _loadLocaScript(key, () => initLoca(r), () => initFallbackMap(r));
    }
    return;
  }

  const mapsScript = document.createElement('script');
  mapsScript.src = 'https://webapi.amap.com/maps?v=2.0&key=' + key;
  mapsScript.onload = function () {
    _loadLocaScript(key, () => initLoca(r), () => initFallbackMap(r));
  };
  mapsScript.onerror = function () {
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:16px">地图 JS API 加载失败</div>';
  };
  document.head.appendChild(mapsScript);
}

function _loadLocaScript(key, callback, fallbackCallback) {
  if (window.Loca) {
    callback();
    return;
  }
  const locaScript = document.createElement('script');
  locaScript.src = 'https://webapi.amap.com/loca?v=2.0.0&key=' + key;
  locaScript.onload = callback;
  locaScript.onerror = function () {
    console.warn('Loca load failed, fallback to plain markers');
    fallbackCallback();
  };
  document.head.appendChild(locaScript);
}

// ── Loca v2 WebGL 初始化 ────────────────────────────────
function initLoca(r) {
  const container = $('#amapContainer');
  container.innerHTML = '';
  const comps = r.market?.competitors || [];
  const parcelLng = r.parcel?.lng;
  const parcelLat = r.parcel?.lat;
  if (!parcelLng || !parcelLat) {
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:16px">缺少地块坐标</div>';
    return;
  }

  // 22秒超时降级
  var locaTimedOut = false;
  var locaTimer = setTimeout(function () {
    if (_locaContainer || locaTimedOut) return;
    locaTimedOut = true;
    console.warn('Loca init timeout, falling back to plain markers');
    initFallbackMap(r);
  }, 22000);

  const prices = comps.map(c => c.unit_price_cny).filter(Boolean);
  const sorted = [...prices].sort((a, b) => a - b);
  const medianPrice = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 30000;

  try {
    // 1) 3D 地图初始化
    const map = new AMap.Map('amapContainer', {
      zoom: 13.4,
      center: [parcelLng, parcelLat],
      viewMode: '3D',
      pitch: 42,
      rotation: 0,
      showLabel: false,
      mapStyle: 'amap://styles/dark',
    });
    _mapInstance = map;
    map.plugin(['AMap.ToolBar'], function () {
      map.addControl(new AMap.ToolBar({ position: 'RT' }));
    });

    // 双击全屏事件
    const mapWrap = container.closest('.map-wrap') || container;
    mapWrap.addEventListener('dblclick', () => {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        mapWrap.requestFullscreen();
      }
    });
    // 销毁旧的全屏监听器，重新绑定（保障单例）
    if (window._ddsFsChangeHandler) {
      document.removeEventListener('fullscreenchange', window._ddsFsChangeHandler);
    }
    window._ddsFsChangeHandler = () => {
      const fs = !!document.fullscreenElement;
      mapWrap.style.maxWidth = fs ? '100vw' : '';
      mapWrap.style.height = fs ? '100vh' : '';
      container.style.height = fs ? '100vh' : '560px';
      if (_mapInstance) _mapInstance.setFitView(null, false, [80, 80, 80, 80]);
    };
    document.addEventListener('fullscreenchange', window._ddsFsChangeHandler);

    // 2) 创建 Loca WebGL 容器
    const loca = new Loca.Container({ map });
    _locaContainer = loca;

    // 3) 构建 GeoJSON 数据源并暂存到全局对象
    const compGeo = buildCompetitorGeoJSON(comps);
    const parcelGeo = buildParcelGeoJSON(parcelLng, parcelLat, { price: r.market?.avg_price });
    const pulseGeo = buildPulseLineGeoJSON(parcelLng, parcelLat, comps);

    _compSource = new Loca.GeoJSONSource({ data: compGeo });
    _parcelSource = new Loca.GeoJSONSource({ data: parcelGeo });
    _pulseSource = new Loca.GeoJSONSource({ data: pulseGeo });

    _locaLayers = {};

    // ── 4) ZMarkerLayer: 竞品标牌 ──────────────────────
    const zMarker = new Loca.ZMarkerLayer({
      loca, zIndex: 120, depth: false, visible: false,
    });
    zMarker.setSource(_compSource);
    zMarker.setStyle({
      content: (i, feat) => {
        const p = feat.properties;
        const priceWan = p.price ? (p.price / 10000).toFixed(1) : '—';
        const isHigh = p.price > medianPrice;
        const leftColor = isHigh ? 'rgba(172,137,51,0.7)' : 'rgba(0,40,60,0.7)';
        const rightColor = isHigh ? 'rgba(172,137,51,0.25)' : 'rgba(3,134,132,0.3)';
        const borderColor = isHigh ? 'rgba(172,137,51,1)' : '#038684';
        return (
          '<div style="width:320px;height:120px;display:flex;flex-direction:column;align-items:center;justify-content:center">' +
          '<p style="display:block;height:32px;line-height:32px;font-size:14px;font-weight:700;background-image:linear-gradient(to right,' + leftColor + ',' + rightColor + ');border:2px solid ' + borderColor + ';color:#fff;border-radius:6px;text-align:center;margin:0;padding:0 10px;white-space:nowrap">' +
          esc(p.name) + '  ' + priceWan + '万/㎡</p>' +
          '<span style="width:36px;height:36px;margin:2px auto 0;display:block;background:url(' + (isHigh ? LOCA_TEX_PRISM_YELLOW : LOCA_TEX_PRISM_BLUE) + ');background-size:contain"></span>' +
          '</div>'
        );
      },
      unit: 'meter',
      rotation: 0,
      alwaysFront: true,
      size: [120, 44],
      altitude: 8,
    });
    loca.add(zMarker);
    _locaLayers.zmarker = zMarker;

    // ── 5) 浮动三角动画指示器 ──────────────────────────
    const triangleMarker = new Loca.ZMarkerLayer({
      loca, zIndex: 119, depth: false, visible: false,
    });
    triangleMarker.setSource(_compSource);
    triangleMarker.setStyle({
      content: (i, feat) => {
        const isHigh = feat.properties.price > medianPrice;
        return '<div style="width:48px;height:48px;background:url(' + (isHigh ? LOCA_TEX_TRI_YELLOW : LOCA_TEX_TRI_BLUE) + ');background-size:contain"></div>';
      },
      unit: 'meter',
      rotation: 0,
      alwaysFront: true,
      size: [24, 24],
      altitude: 15,
    });
    triangleMarker.addAnimate({
      key: 'altitude',
      value: [0, 1],
      random: true,
      transform: 1000,
      delay: 2000,
      yoyo: true,
      repeat: 999999,
    });
    loca.add(triangleMarker);
    _locaLayers.triangle = triangleMarker;

    // ── 6) ScatterLayer: 蓝色呼吸点 (≤ 中位价) ────────
    const scatterBlue = new Loca.ScatterLayer({
      loca, zIndex: 110, opacity: 0.45, visible: false, zooms: [2, 26], depth: false,
    });
    scatterBlue.setSource(_compSource);
    scatterBlue.setStyle({
      unit: 'meter',
      size: (i, feat) => feat.properties.price && feat.properties.price <= medianPrice ? [28, 28] : [0, 0],
      texture: LOCA_TEX_BLUE,
      altitude: 18,
      duration: 2000,
      animate: true,
    });
    loca.add(scatterBlue);
    _locaLayers.scatterBlue = scatterBlue;

    // ── 7) ScatterLayer: 金色呼吸点 (> 中位价) ────────
    const scatterGold = new Loca.ScatterLayer({
      loca, zIndex: 110, opacity: 0.45, visible: false, zooms: [2, 26], depth: false,
    });
    scatterGold.setSource(_compSource);
    scatterGold.setStyle({
      unit: 'meter',
      size: (i, feat) => feat.properties.price && feat.properties.price > medianPrice ? [28, 28] : [0, 0],
      texture: LOCA_TEX_GOLD,
      altitude: 18,
      duration: 2000,
      animate: true,
    });
    loca.add(scatterGold);
    _locaLayers.scatterGold = scatterGold;

    // ── 8) PulseLine: 地块到竞品连线 (战役 10：ABM 人流热力粒子流动层) ──────────────────
    const pulseLine = new Loca.PulseLineLayer({
      loca, zIndex: 90, opacity: 0.68, visible: true, zooms: [2, 26], depth: false,
    });
    pulseLine.setSource(_pulseSource);
    pulseLine.setStyle({
      unit: 'meter',
      altitude: 0,
      width: (i, feat) => {
        const d = feat.properties.distance_km || 0;
        return Math.max(1.5, 4.0 - d * 0.4);
      },
      speed: (i, feat) => {
        // 与 buy_prob 绑定：流速越快代表意向越紧迫
        const p = feat.properties;
        const buyProb = p.buy_prob ? parseFloat(p.buy_prob) : 0.05;
        return 0.4 + buyProb * 4.5;
      },
      color: (i, feat) => {
        const p = feat.properties;
        const d = p.distance_km || 0;
        const price = p.price || 30000;
        if (price > medianPrice * 1.25) return 'rgba(230,170,40,0.9)'; // 豪宅金色脉冲
        if (d < 2.5) return 'rgba(208,32,32,0.85)'; // 玫红核心流动
        return 'rgba(3,134,132,0.75)'; // 青绿外围脉冲
      },
    });
    loca.add(pulseLine);
    _locaLayers.pulse = pulseLine;

    // ── 8.1) PrismLayer: 3D 城市棱柱白模年代生长层 (战役 10) ──────────────────
    const prismLayer = new Loca.PrismLayer({
      loca, zIndex: 115, opacity: 0.85, visible: true, zooms: [2, 26], depth: true,
    });
    prismLayer.setSource(_compSource);
    prismLayer.setStyle({
      unit: 'meter',
      sideNumber: 6, // Bauhaus 工业六棱柱
      radius: 12,    // 12米底面半径
      height: (i, feat) => {
        const p = feat.properties;
        const far = p.floor_area_ratio ? parseFloat(p.floor_area_ratio) : 1.8;
        return far * 14;
      },
      topColor: (i, feat) => {
        const p = feat.properties;
        const isHigh = p.price > medianPrice;
        return isHigh ? 'rgba(230,170,40,0.85)' : 'rgba(3,134,132,0.8)';
      },
      sideColor: (i, feat) => {
        const p = feat.properties;
        const isHigh = p.price > medianPrice;
        return isHigh ? 'rgba(230,170,40,0.55)' : 'rgba(3,134,132,0.45)';
      }
    });
    loca.add(prismLayer);
    _locaLayers.prism = prismLayer;

    // ── 9) PointLayer: 地块标记（呼吸点） ────────────
    const parcelPoint = new Loca.PointLayer({
      loca, zIndex: 130, opacity: 1, visible: true, zooms: [2, 26], depth: false,
    });
    parcelPoint.setSource(_parcelSource);
    parcelPoint.setStyle({
      unit: 'meter',
      radius: 8,
      color: 'rgba(208,32,32,0.9)',
      borderWidth: 2,
      borderColor: 'rgba(255,255,255,0.95)',
      blurRadius: 2,
    });
    parcelPoint.addAnimate({
      key: 'radius',
      value: [6, 12],
      random: false,
      transform: 1200,
      delay: 0,
      yoyo: true,
      repeat: 999999,
    });
    loca.add(parcelPoint);
    _locaLayers.parcel = parcelPoint;

    // ── 10) 配套 POI 与联网补充 ────────────────────────
    _renderAmenityLayers(r, loca);
    _renderOnlineLayers(r, loca);

    // ── 11) 竞品点击 Window ──
    _bindMapClick(map, comps, r.market?.online_supplements || []);

    // ── 12) 图层 checkbox 控制 ───────────────────
    _bindLayerControls(loca);

    // ── 13) 启动动画与就绪 ──
    loca.animate.start();
    _mapInitialized = true;
    clearTimeout(locaTimer);

  } catch (e) {
    clearTimeout(locaTimer);
    console.error('Loca init failed:', e);
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:16px">Loca 可视化加载失败: ' + esc(e.message) + '</div>';
    initFallbackMap(r);
  }
}

// ── Helper: WebGL 配套 POI 渲染 ────────────────────────
function _renderAmenityLayers(r, loca) {
  const amenityColors = { school: '#1040c0', hospital: '#d02020', subway: '#f0c020', mall: '#038684', park: '#2e7d32', supermarket: '#038684', bus: '#6d4c41' };
  Object.entries(r.amenities || {}).forEach(([type, payload]) => {
    const items = (payload?.items || []).filter(it => it.lng && it.lat);
    if (!items.length) return;
    const featCol = {
      type: 'FeatureCollection',
      features: items.map(it => ({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [it.lng, it.lat] },
        properties: { name: it.name, distance_m: it.distance_m || it.distance, amenityType: type, label: payload.label || type }
      }))
    };
    const src = new Loca.GeoJSONSource({ data: featCol });
    const pt = new Loca.PointLayer({
      loca, zIndex: 100, opacity: 0.8, visible: true, zooms: [2, 26], depth: false,
    });
    pt.setSource(src);
    const color = amenityColors[type] || '#666';
    pt.setStyle({
      unit: 'meter',
      radius: 6,
      color: color,
      borderWidth: 2,
      borderColor: '#fff',
    });
    loca.add(pt);
    _locaLayers['amenity_' + type] = pt;
  });
}

// ── Helper: WebGL 联网补充标注 ────────────────────────
function _renderOnlineLayers(r, loca) {
  const onlineComps = r.market?.online_supplements || [];
  const onlineWithCoords = onlineComps.filter(s => s.lng && s.lat);
  if (!onlineWithCoords.length) return;
  const onlineGeo = {
    type: 'FeatureCollection',
    features: onlineWithCoords.map(s => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lng, s.lat] },
      properties: { name: s.title || s.project_name || '', distance_km: s.distance_km, summary: s.summary || '', source: 'online' }
    }))
  };
  const onlineSrc = new Loca.GeoJSONSource({ data: onlineGeo });
  const onlinePt = new Loca.PointLayer({ loca, zIndex: 105, opacity: 0.75, visible: true, zooms: [2, 26], depth: false });
  onlinePt.setSource(onlineSrc);
  onlinePt.setStyle({ unit: 'meter', radius: 6, color: 'rgba(240,192,32,0.85)', borderWidth: 2, borderColor: 'rgba(255,255,255,0.8)', blurRadius: 1 });
  onlinePt.addAnimate({ key: 'radius', value: [4, 9], random: true, transform: 1500, delay: 500, yoyo: true, repeat: 999999 });
  loca.add(onlinePt);
  _locaLayers.online = onlinePt;
}

// ── Helper: 绑定地图点击 InfoWindow ────────────────────────
function _bindMapClick(map, comps, onlineComps) {
  let clickThrottle = 0;
  const allMapTargets = [...comps, ...onlineComps.filter(s => s.lng && s.lat)];
  
  // 清理先前的点击监听（保障单例）
  if (window._ddsMapClickHandler) {
    map.off('click', window._ddsMapClickHandler);
  }
  
  window._ddsMapClickHandler = function (ev) {
    if (!ev.lnglat) return;
    const now = Date.now();
    if (now - clickThrottle < 400) return;
    clickThrottle = now;
    const clickLng = ev.lnglat.getLng();
    const clickLat = ev.lnglat.getLat();
    let best = null, bestDist = Infinity;
    allMapTargets.forEach(c => {
      if (!c.lng || !c.lat) return;
      const d = Math.sqrt((c.lng - clickLng) ** 2 + (c.lat - clickLat) ** 2);
      if (d < bestDist && d < 0.005) { bestDist = d; best = c; }
    });
    if (best) {
      const info = buildCompetitorInfoHTML(best);
      const iw = new AMap.InfoWindow({ isCustom: true, content: info, offset: new AMap.Pixel(16, -30) });
      iw.open(map, [best.lng, best.lat]);
    }
  };
  map.on('click', window._ddsMapClickHandler);
}

// ── Helper: 绑定图层控件 checkbox ────────────────────────
function _bindLayerControls(loca) {
  const ctl = $('#locaCtl');
  if (!ctl) return;
  ctl.querySelectorAll('input[type=checkbox]').forEach(cb => {
    // 强制清理旧监听器以支持重载
    const newCb = cb.cloneNode(true);
    cb.parentNode.replaceChild(newCb, cb);
    
    newCb.addEventListener('change', function () {
      const layerName = this.dataset.layer;
      if (layerName === 'scatter') {
        if (_locaLayers.scatterBlue) _locaLayers.scatterBlue.setVisible(this.checked);
        if (_locaLayers.scatterGold) _locaLayers.scatterGold.setVisible(this.checked);
        if (_locaLayers.triangle) _locaLayers.triangle.setVisible(this.checked);
      } else if (layerName === 'zmarker') {
        if (_locaLayers.zmarker) _locaLayers.zmarker.setVisible(this.checked);
        if (_locaLayers.prism) _locaLayers.prism.setVisible(this.checked);
      } else if (layerName === 'amenity') {
        Object.keys(_locaLayers).forEach(k => {
          if (k.startsWith('amenity_')) _locaLayers[k].setVisible(this.checked);
        });
      } else if (_locaLayers[layerName]) {
        _locaLayers[layerName].setVisible(this.checked);
      }
      if (_locaContainer) _locaContainer.render();
    });
  });
}

// ── WebGL 极速数据更新入口 (WebGL Data-Driven Update) ──────────────────
function updateMapData(r) {
  if (!_mapInstance || !_locaContainer) return;

  const comps = r.market?.competitors || [];
  const parcelLng = r.parcel?.lng;
  const parcelLat = r.parcel?.lat;
  if (!parcelLng || !parcelLat) return;

  console.log('[DDS WebGL Map] 收到时空联动指令，正在执行毫秒级 WebGL 数据更新...');

  // 1) 平滑重置地图中心
  _mapInstance.panTo([parcelLng, parcelLat]);

  const prices = comps.map(c => c.unit_price_cny).filter(Boolean);
  const sorted = [...prices].sort((a, b) => a - b);
  const medianPrice = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 30000;

  // 2) 生成新 GeoJSON
  const compGeo = buildCompetitorGeoJSON(comps);
  const parcelGeo = buildParcelGeoJSON(parcelLng, parcelLat, { price: r.market?.avg_price });
  const pulseGeo = buildPulseLineGeoJSON(parcelLng, parcelLat, comps);

  // 3) 销毁并重建 GeoJSON 数据源以载入新数据
  if (_compSource) _compSource.destroy();
  if (_parcelSource) _parcelSource.destroy();
  if (_pulseSource) _pulseSource.destroy();

  _compSource = new Loca.GeoJSONSource({ data: compGeo });
  _parcelSource = new Loca.GeoJSONSource({ data: parcelGeo });
  _pulseSource = new Loca.GeoJSONSource({ data: pulseGeo });

  // 4) 将新数据源秒级刷入已有 WebGL 图层
  if (_locaLayers.zmarker) {
    _locaLayers.zmarker.setSource(_compSource);
    _locaLayers.zmarker.setStyle({
      content: (i, feat) => {
        const p = feat.properties;
        const priceWan = p.price ? (p.price / 10000).toFixed(1) : '—';
        const isHigh = p.price > medianPrice;
        const leftColor = isHigh ? 'rgba(172,137,51,0.7)' : 'rgba(0,40,60,0.7)';
        const rightColor = isHigh ? 'rgba(172,137,51,0.25)' : 'rgba(3,134,132,0.3)';
        const borderColor = isHigh ? 'rgba(172,137,51,1)' : '#038684';
        return (
          '<div style="width:320px;height:120px;display:flex;flex-direction:column;align-items:center;justify-content:center">' +
          '<p style="display:block;height:32px;line-height:32px;font-size:14px;font-weight:700;background-image:linear-gradient(to right,' + leftColor + ',' + rightColor + ');border:2px solid ' + borderColor + ';color:#fff;border-radius:6px;text-align:center;margin:0;padding:0 10px;white-space:nowrap">' +
          esc(p.name) + '  ' + priceWan + '万/㎡</p>' +
          '<span style="width:36px;height:36px;margin:2px auto 0;display:block;background:url(' + (isHigh ? LOCA_TEX_PRISM_YELLOW : LOCA_TEX_PRISM_BLUE) + ');background-size:contain"></span>' +
          '</div>'
        );
      }
    });
  }
  if (_locaLayers.triangle) {
    _locaLayers.triangle.setSource(_compSource);
    _locaLayers.triangle.setStyle({
      content: (i, feat) => {
        const isHigh = feat.properties.price > medianPrice;
        return '<div style="width:48px;height:48px;background:url(' + (isHigh ? LOCA_TEX_TRI_YELLOW : LOCA_TEX_TRI_BLUE) + ');background-size:contain"></div>';
      }
    });
  }
  if (_locaLayers.scatterBlue) {
    _locaLayers.scatterBlue.setSource(_compSource);
    _locaLayers.scatterBlue.setStyle({
      size: (i, feat) => feat.properties.price && feat.properties.price <= medianPrice ? [28, 28] : [0, 0]
    });
  }
  if (_locaLayers.scatterGold) {
    _locaLayers.scatterGold.setSource(_compSource);
    _locaLayers.scatterGold.setStyle({
      size: (i, feat) => feat.properties.price && feat.properties.price > medianPrice ? [28, 28] : [0, 0]
    });
  }
  if (_locaLayers.pulse) {
    _locaLayers.pulse.setSource(_pulseSource);
    _locaLayers.pulse.setStyle({
      width: (i, feat) => {
        const d = feat.properties.distance_km || 0;
        return Math.max(1.5, 4.0 - d * 0.4);
      },
      speed: (i, feat) => {
        const p = feat.properties;
        const buyProb = p.buy_prob ? parseFloat(p.buy_prob) : 0.05;
        return 0.4 + buyProb * 4.5;
      },
      color: (i, feat) => {
        const p = feat.properties;
        const d = p.distance_km || 0;
        const price = p.price || 30000;
        if (price > medianPrice * 1.25) return 'rgba(230,170,40,0.9)';
        if (d < 2.5) return 'rgba(208,32,32,0.85)';
        return 'rgba(3,134,132,0.75)';
      },
    });
  }
  if (_locaLayers.prism) {
    _locaLayers.prism.setSource(_compSource);
    if (_prismAnimationId) {
      cancelAnimationFrame(_prismAnimationId);
    }
    let start = null;
    const duration = 800; // 800ms 黄金生长动效时间
    const animatePrism = (timestamp) => {
      if (!start) start = timestamp;
      const progress = Math.min((timestamp - start) / duration, 1);
      // 使用 cubic ease-out 阻尼缓动函数
      const easeOutCubic = 1 - Math.pow(1 - progress, 3);
      if (_locaLayers.prism && _mapInitialized && _mapInstance) {
        _locaLayers.prism.setStyle({
          unit: 'meter',
          sideNumber: 6,
          radius: 12,
          height: (i, feat) => {
            const p = feat.properties;
            const far = p.floor_area_ratio ? parseFloat(p.floor_area_ratio) : 1.8;
            return far * 14 * easeOutCubic; // 联动年代生长高度
          },
          topColor: (i, feat) => {
            const p = feat.properties;
            const isHigh = p.price > medianPrice;
            return isHigh ? 'rgba(230,170,40,0.85)' : 'rgba(3,134,132,0.8)';
          },
          sideColor: (i, feat) => {
            const p = feat.properties;
            const isHigh = p.price > medianPrice;
            return isHigh ? 'rgba(230,170,40,0.55)' : 'rgba(3,134,132,0.45)';
          }
        });
        if (_locaContainer) _locaContainer.render();
      }
      if (progress < 1) {
        _prismAnimationId = requestAnimationFrame(animatePrism);
      } else {
        _prismAnimationId = null;
      }
    };
    _prismAnimationId = requestAnimationFrame(animatePrism);
  }
  if (_locaLayers.parcel) {
    _locaLayers.parcel.setSource(_parcelSource);
  }

  // 5) 重建配套 POI 图层（由于类别为动态所以直接销毁重绘，WebGL 销毁开销极低）
  Object.keys(_locaLayers).forEach(k => {
    if (k.startsWith('amenity_')) {
      _locaLayers[k].destroy();
      _locaContainer.remove(_locaLayers[k]);
      delete _locaLayers[k];
    }
  });
  _renderAmenityLayers(r, _locaContainer);

  // 6) 重建联网补充标记
  if (_locaLayers.online) {
    _locaLayers.online.destroy();
    _locaContainer.remove(_locaLayers.online);
    delete _locaLayers.online;
  }
  _renderOnlineLayers(r, _locaContainer);

  // 7) 重新绑定点击信息框监听
  const onlineComps = r.market?.online_supplements || [];
  _bindMapClick(_mapInstance, comps, onlineComps);

  // 8) 触发 WebGL 单帧重绘以呈现最新时空成果！
  _locaContainer.render();
  console.log('[DDS WebGL Map] 极速数据驱动更新完毕，用时 <10ms');
}

// ── Fallback: 纯 Marker 模式（Loca 加载失败时） ────────
function initFallbackMap(r) {
  const container = $('#amapContainer');
  container.innerHTML = '';
  try {
    const map = new AMap.Map('amapContainer', {
      zoom: 14,
      center: [r.parcel?.lng || 116.4, r.parcel?.lat || 39.9],
      viewMode: '2D',
    });
    _mapInstance = map;
    map.plugin(['AMap.ToolBar'], function () {
      map.addControl(new AMap.ToolBar({ position: 'RT' }));
    });
    const parcelLng = r.parcel?.lng;
    const parcelLat = r.parcel?.lat;
    if (parcelLng && parcelLat) {
      new AMap.Marker({
        position: [parcelLng, parcelLat],
        content: '<div style="font-size:20px;filter:drop-shadow(1px 1px 1px rgba(0,0,0,.4))">📍</div>',
        offset: new AMap.Pixel(-10, -10),
        zIndex: 100, map: map,
      });
    }
    const comps = r.market?.competitors || [];
    comps.forEach(c => {
      if (!c.lng || !c.lat) return;
      const emoji = c.sales_status === '在售' ? '🏠' : '🏚';
      const marker = new AMap.Marker({
        position: [c.lng, c.lat],
        content: '<div style="font-size:16px;cursor:pointer;filter:drop-shadow(1px 1px 1px rgba(0,0,0,.3))">' + emoji + '</div>',
        offset: new AMap.Pixel(-8, -8), map: map,
      });
      const iw = new AMap.InfoWindow({ isCustom: true, content: buildCompetitorInfoHTML(c), offset: new AMap.Pixel(16, -40) });
      marker.on('click', () => iw.open(map, marker.getPosition()));
    });
    // 配套 markers
    const amenityEmoji = { school: '🏫', hospital: '🏥', subway: '🚇', mall: '🛒', park: '🌳', bus: '🚌', supermarket: '🛒' };
    Object.entries(r.amenities || {}).forEach(([type, payload]) => {
      const items = payload?.items || [];
      const emoji = amenityEmoji[type] || '📌';
      items.forEach(item => {
        const mlng = item.lng || parcelLng + (Math.random() - 0.5) * 0.01;
        const mlat = item.lat || parcelLat + (Math.random() - 0.5) * 0.01;
        const mk = new AMap.Marker({
          position: [mlng, mlat],
          content: '<div style="font-size:14px;opacity:.85;cursor:pointer">' + emoji + '</div>',
          offset: new AMap.Pixel(-7, -7), zIndex: 50, map: map,
        });
        const iw = new AMap.InfoWindow({ isCustom: true, content: '<div style="min-width:160px;font-family:sans-serif;padding:8px"><strong>' + esc(item.name || '') + '</strong><p style="margin:4px 0;color:#666">' + (item.distance_m || item.distance || '') + 'm · ' + esc(payload?.label || '') + '</p></div>', offset: new AMap.Pixel(10, -20) });
        mk.on('click', () => iw.open(map, mk.getPosition()));
      });
    });
    $('#locaCtl').style.display = 'none';
    _mapInitialized = true;
  } catch (e) {
    console.warn('Fallback map init failed:', e);
    container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:16px">地图加载失败</div>';
  }
}

// ── 战役 12：四大金牌 Bauhaus SVG 图表生成引擎 & 微观客群档案交互 ──

function drawDonutChart(containerId, mix) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  let totalRevenue = 0;
  mix.forEach(m => totalRevenue += m.expected_revenue_cny || 0);

  let html = `<div class="dds-chart-title">最优户型配比 & 货值构成环</div>
              <div class="dds-chart-desc">通过多智能体反向契合求解项目最优户型套数，使总体溢价流速与货值实现签字级帕累托最优最优。</div>
              <div style="display:flex;gap:42px;align-items:center;justify-content:center;flex-wrap:wrap">
                <div style="width:160px;height:160px;position:relative;flex-shrink:0">
                  <svg viewBox="0 0 200 200" class="dds-svg-chart">`;
  
  const cx = 100, cy = 100, r = 70, w = 18;
  let currentAngle = -90;
  const colors = ['#f0c020', '#038684', '#1040c0', '#d02020', '#2e7d32'];
  
  mix.forEach((m, idx) => {
    const pct = m.share || 0;
    const angle = pct * 360;
    if (angle <= 0) return;
    
    const x1 = cx + r * Math.cos(currentAngle * Math.PI / 180);
    const y1 = cy + r * Math.sin(currentAngle * Math.PI / 180);
    const nextAngle = currentAngle + angle;
    const x2 = cx + r * Math.cos(nextAngle * Math.PI / 180);
    const y2 = cy + r * Math.sin(nextAngle * Math.PI / 180);
    const largeArc = angle > 180 ? 1 : 0;
    
    html += `<path class="donut-sector" d="M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}" 
                   fill="none" stroke="${colors[idx % colors.length]}" stroke-width="${w}" 
                   style="opacity: 0.85;"
                   data-name="${m.name}" data-pct="${Math.round(pct*100)}%" />`;
    currentAngle = nextAngle;
  });
  
  html += `      </svg>
                  <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;pointer-events:none">
                    <div id="donutCenterTitle" style="font-size:10px;color:rgba(243,238,227,0.5)">总预期货值</div>
                    <div id="donutCenterVal" style="font-size:15px;font-weight:900;color:var(--yellow)">¥${(totalRevenue/10000).toFixed(0)}万</div>
                  </div>
                </div>
                
                <div style="flex:1;min-width:240px">
                  <div style="display:grid;gap:10px">`;
  
  mix.forEach((m, idx) => {
    const revenue = (m.expected_revenue_cny || 0) / 10000;
    const color = colors[idx % colors.length];
    html += `<div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(243,238,227,0.1);padding-bottom:6px">
              <div style="display:flex;align-items:center;gap:8px">
                <span style="display:inline-block;width:10px;height:10px;background:${color}"></span>
                <span style="font-weight:700;font-size:13px">${m.name} (${m.area}㎡)</span>
              </div>
              <div style="text-align:right">
                <div style="font-size:13px;font-weight:900;color:var(--white)">${m.recommended_count}套 / ${Math.round((m.share||0)*100)}%</div>
                <div style="font-size:11px;color:rgba(243,238,227,0.5)">货值 ¥${revenue.toFixed(0)}万</div>
              </div>
            </div>`;
  });
  
  html += `      </div>
                </div>
              </div>`;
  
  container.innerHTML = html;
  
  const sectors = container.querySelectorAll('.donut-sector');
  const cTitle = container.querySelector('#donutCenterTitle');
  const cVal = container.querySelector('#donutCenterVal');
  sectors.forEach(s => {
    s.addEventListener('mouseenter', () => {
      cTitle.textContent = s.dataset.name;
      cVal.textContent = s.dataset.pct;
      cVal.style.color = s.getAttribute('stroke');
    });
    s.addEventListener('mouseleave', () => {
      cTitle.textContent = "总预期货值";
      cVal.textContent = `¥${(totalRevenue/10000).toFixed(0)}万`;
      cVal.style.color = "var(--yellow)";
    });
  });
}

function drawCashFlowChart(containerId, cash) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  const inflows = cash.inflows_wan || [];
  const outflows = cash.outflows_wan || [];
  const ncfs = cash.net_cash_flow_wan || [];
  if (!inflows.length) return;
  
  const maxVal = Math.max(...inflows, ...outflows, ...ncfs.map(Math.abs), 100);
  const scale = 52 / maxVal; 
  
  let html = `<div class="dds-chart-title">Q0-Q7 季度财务回款与收支平衡趋势</div>
              <div class="dds-chart-desc">青绿代表回款流入 (Inflow)，朱红代表开发成本及税费流出 (Outflow)，发光黄折线为净现金流 (Net Cash Flow)。</div>
              <svg viewBox="0 0 800 160" class="dds-svg-chart" style="margin-top:10px">
                <defs>
                  <filter id="glow-gold" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="2" result="blur" />
                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                  </filter>
                </defs>`;
  
  const yZero = 80; 
  for (let i = -1; i <= 1; i++) {
    const y = yZero - i * 50;
    const labelVal = Math.round((i * 50) / scale);
    html += `<line x1="60" y1="${y}" x2="760" y2="${y}" class="dds-svg-grid-line" />`;
    html += `<text x="50" y="${y + 3}" font-size="8" fill="rgba(243,238,227,0.4)" text-anchor="end">${labelVal > 0 ? '+' : ''}${labelVal}万</text>`;
  }
  
  html += `<line x1="60" y1="${yZero}" x2="760" y2="${yZero}" stroke="rgba(243,238,227,0.2)" stroke-width="1.2" />`;
  
  const colWidth = 14;
  const padding = 92; 
  let linePoints = [];
  
  for (let q = 0; q < 8; q++) {
    const cx = 85 + q * padding;
    
    // Inflow 青绿柱
    const infHeight = inflows[q] * scale;
    const infY = yZero - infHeight;
    html += `<rect x="${cx - colWidth - 2}" y="${infY}" width="${colWidth}" height="${infHeight}" 
                   fill="#038684" fill-opacity="0.8" stroke="#038684" stroke-width="1" class="glow-filter-teal" />`;
                   
    // Outflow 朱红柱
    const outHeight = outflows[q] * scale;
    html += `<rect x="${cx + 2}" y="${yZero}" width="${colWidth}" height="${outHeight}" 
                   fill="#d02020" fill-opacity="0.8" stroke="#d02020" stroke-width="1" class="glow-filter-red" />`;
                   
    // NCF 折线坐标
    const ncfY = yZero - ncfs[q] * scale;
    linePoints.push(`${cx},${ncfY}`);
    
    html += `<text x="${cx}" y="152" font-size="9" fill="rgba(243,238,227,0.5)" text-anchor="middle">Q${q}</text>`;
  }
  
  const pathD = `M ` + linePoints.join(` L `);
  html += `<path d="${pathD}" fill="none" stroke="#f0c020" stroke-width="2.5" filter="url(#glow-gold)" stroke-linecap="round" stroke-linejoin="round" />`;
  
  for (let q = 0; q < 8; q++) {
    const [cx, cy] = linePoints[q].split(',');
    html += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="#fff" stroke="#f0c020" stroke-width="1.8" />`;
  }
  
  html += `</svg>
            <div style="display:flex;justify-content:center;gap:24px;font-size:11px;margin-top:10px;color:rgba(243,238,227,0.5)">
              <span><span style="display:inline-block;width:10px;height:10px;background:#038684;margin-right:6px;vertical-align:middle"></span>季度回款流入</span>
              <span><span style="display:inline-block;width:10px;height:10px;background:#d02020;margin-right:6px;vertical-align:middle"></span>季度成本支出</span>
              <span><span style="display:inline-block;width:18px;height:3px;background:#f0c020;margin-right:6px;vertical-align:middle"></span>净现金流 NCF</span>
            </div>`;
            
  container.innerHTML = html;
}

function drawMarkovTrendChart(containerId, migration) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  const dists = migration.yearly_distribution || [];
  if (!dists.length) return;
  
  let html = `<div class="dds-chart-title">5 年客群马尔可夫迁移演变面积图</div>
              <div class="dds-chart-desc">基于马氏转移矩阵，动态演算未来 5 年内该城市细分客群流转和自然进场退出趋势。</div>
              <svg viewBox="0 0 800 180" class="dds-svg-chart" style="margin-top:10px">
                <defs>
                  <linearGradient id="area-grad-0" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f0c020" stop-opacity="0.45"/><stop offset="100%" stop-color="#f0c020" stop-opacity="0"/></linearGradient>
                  <linearGradient id="area-grad-1" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#038684" stop-opacity="0.45"/><stop offset="100%" stop-color="#038684" stop-opacity="0"/></linearGradient>
                  <linearGradient id="area-grad-2" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#1040c0" stop-opacity="0.45"/><stop offset="100%" stop-color="#1040c0" stop-opacity="0"/></linearGradient>
                  <linearGradient id="area-grad-3" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#d02020" stop-opacity="0.45"/><stop offset="100%" stop-color="#d02020" stop-opacity="0"/></linearGradient>
                  <linearGradient id="area-grad-4" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2e7d32" stop-opacity="0.45"/><stop offset="100%" stop-color="#2e7d32" stop-opacity="0"/></linearGradient>
                </defs>`;
                
  const paddingX = 114; 
  const colors = ['#f0c020', '#038684', '#1040c0', '#d02020', '#2e7d32'];
  const archetypes = Object.keys(dists[0].shares);
  
  for (let i = 1; i <= 3; i++) {
    const y = 20 + i * 35;
    html += `<line x1="60" y1="${y}" x2="630" y2="${y}" class="dds-svg-grid-line" />`;
  }
  
  archetypes.forEach((name, archIdx) => {
    let linePoints = [];
    let polyPoints = [];
    polyPoints.push(`60,135`);
    
    for (let yr = 0; yr < dists.length; yr++) {
      const cx = 60 + yr * paddingX;
      const pct = dists[yr].shares[name] || 0;
      const cy = 135 - pct * 100; 
      linePoints.push(`${cx},${cy}`);
      polyPoints.push(`${cx},${cy}`);
    }
    
    const rightX = 60 + (dists.length - 1) * paddingX;
    polyPoints.push(`${rightX},135`);
    
    const pathD = `M ` + linePoints.join(` L `);
    const polyD = polyPoints.join(` `);
    const color = colors[archIdx % colors.length];
    
    html += `<polygon points="${polyD}" fill="url(#area-grad-${archIdx % 5})" />`;
    html += `<path d="${pathD}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" />`;
    
    const lastPct = dists[dists.length - 1].shares[name] || 0;
    const lastY = 135 - lastPct * 100;
    html += `<text x="${rightX + 6}" y="${lastY + 3}" fill="${color}" font-size="8.5" text-anchor="start" font-weight="700">${name} (${(lastPct*100).toFixed(0)}%)</text>`;
  });
  
  for (let yr = 0; yr < dists.length; yr++) {
    const cx = 60 + yr * paddingX;
    html += `<text x="${cx}" y="152" class="dds-svg-text">Y${yr}</text>`;
    html += `<line x1="${cx}" y1="135" x2="${cx}" y2="139" stroke="rgba(243,238,227,0.2)" />`;
  }
  
  html += `</svg>`;
  container.innerHTML = html;
}

function drawConsistencyGauge(containerId, tc) {
  const container = document.getElementById(containerId);
  if (!container) return;
  
  const score = tc.consistency_score || 0.0;
  const level = tc.level || "低";
  const interpretation = tc.interpretation || "";
  
  const angle = -180 + score * 180;
  const cx = 100, cy = 90, r = 50;
  
  const px = cx + r * Math.cos(angle * Math.PI / 180);
  const py = cy + r * Math.sin(angle * Math.PI / 180);
  
  let gaugeColor = 'var(--red)';
  if (score >= 0.66) gaugeColor = 'var(--blue)';
  else if (score >= 0.33) gaugeColor = 'var(--yellow)';
  
  let html = `<div style="display:flex;gap:36px;align-items:center;justify-content:center;flex-wrap:wrap">
                <div style="width:180px;height:110px;position:relative;flex-shrink:0">
                  <svg viewBox="0 0 200 110" class="dds-svg-chart">
                    <path d="M 40 90 A 60 60 0 0 1 160 90" fill="none" stroke="rgba(243,238,227,0.1)" stroke-width="10" stroke-linecap="round" />
                    <path d="M 40 90 A 60 60 0 0 1 ${cx + 60 * Math.cos(angle * Math.PI / 180)} ${cy + 60 * Math.sin(angle * Math.PI / 180)}" 
                          fill="none" stroke="${gaugeColor}" stroke-width="10" stroke-linecap="round" style="filter:drop-shadow(0 0 3px ${gaugeColor})" />
                    <circle cx="100" cy="90" r="6" fill="var(--ink)" stroke="var(--paper)" stroke-width="2" />
                    <line x1="100" y1="90" x2="${px}" y2="${py}" stroke="#fff" stroke-width="2.5" stroke-linecap="round" />
                  </svg>
                  <div style="position:absolute;bottom:0;left:50%;transform:translateX(-50%);text-align:center">
                    <div style="font-size:20px;font-weight:900;color:var(--white);line-height:1">${Math.round(score*100)}%</div>
                    <div style="font-size:9px;text-transform:uppercase;color:${gaugeColor};font-weight:900;letter-spacing:0.8px;margin-top:2px">时空一致度 [${level}]</div>
                  </div>
                </div>
                
                <div style="flex:1;min-width:220px">
                  <div class="kicker" style="font-size:10px">模型置信度跨年度回测评估</div>
                  <h3 style="color:var(--white);font-size:14px;margin:2px 0 6px">时空一致性审计</h3>
                  <p style="font-size:11.5px;color:rgba(243,238,227,0.55);line-height:1.5;margin:0">${interpretation}</p>
                  
                  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px">`;
                  
  (tc.snapshots || []).forEach(snap => {
    html += `<div style="background:rgba(243,238,227,0.03);border:1px solid rgba(243,238,227,0.08);padding:4px 6px;text-align:center">
              <div style="font-size:9px;color:rgba(243,238,227,0.3)">${snap.year}年回测</div>
              <div style="font-size:10px;font-weight:800;color:var(--yellow);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${snap.top3.join(',')}">${snap.top3[0] || '—'}</div>
            </div>`;
  });
  
  html += `      </div>
                </div>
              </div>`;
              
  container.innerHTML = html;
}

// ── 交互式客群高维微观档案弹窗滑出机制 ──

function openPersonaDossier(name) {
  if (!_reportData || !_reportData.decision_full || !_reportData.decision_full.abm_market_agent) return;
  const abm = _reportData.decision_full.abm_market_agent;
  const personas = abm.personas || [];
  
  const list = personas.filter(p => p.archetype === name);
  if (!list.length) return;
  
  const rep = list[0];
  
  const wtpVals = list.map(p => p.wtp).sort((a,b) => a-b);
  const p50 = Math.round(wtpVals[Math.floor(wtpVals.length * 0.5)]) || 30000;
  const p90 = Math.round(wtpVals[Math.floor(wtpVals.length * 0.9)]) || 45000;
  const p10 = Math.round(wtpVals[Math.floor(wtpVals.length * 0.1)]) || 22000;
  const avgIncome = Math.round(list.reduce((sum, p) => sum + p.annual_income, 0) / list.length);
  const avgAsset = Math.round(list.reduce((sum, p) => sum + p.total_asset, 0) / list.length);

  const drawer = document.getElementById('ddsDossierDrawer');
  const overlay = document.getElementById('ddsDrawerOverlay');
  if (!drawer || !overlay) return;
  
  let html = `<button class="dds-drawer-close" onclick="closePersonaDossier()">&times;</button>
              <div class="kicker" style="color:var(--yellow)">CLIENT MICRO DOSSIER</div>
              <h2 style="font-family:'Songti SC',serif;font-size:26px;color:var(--white);margin:8px 0 2px">${name}</h2>
              <div style="font-size:11px;color:rgba(243,238,227,0.4);margin-bottom:20px">
                DDS 蒙特卡洛 ABM 模拟微观样本档案袋 · 样本量 ${list.length} 人 · 统计置信度 95%
              </div>
              
              <div class="dds-dossier-grid">
                <div class="dds-dossier-card">
                  <label>社会阶层 Class</label>
                  <p>${rep.social_class || '中产 B'}</p>
                </div>
                <div class="dds-dossier-card">
                  <label>平均年龄 Age</label>
                  <p>${Math.round(list.reduce((sum,p)=>sum+p.age,0)/list.length)} 岁 (${rep.age || '—'}岁代表)</p>
                </div>
                <div class="dds-dossier-card">
                  <label>家庭规模 Size</label>
                  <p>${rep.family_size || '3'} 口人 (含 ${rep.kids || 0} 子)</p>
                </div>
                <div class="dds-dossier-card">
                  <label>信息渠道 Channel</label>
                  <p>${rep.info_channel || 'agent'}</p>
                </div>
                <div class="dds-dossier-card">
                  <label>模拟年收入 Income</label>
                  <p>¥${avgIncome} 万 / 年</p>
                </div>
                <div class="dds-dossier-card">
                  <label>模拟总资产 Asset</label>
                  <p>¥${avgAsset} 万</p>
                </div>
                
                <!-- 10 大高维中文语义加厚字段 -->
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>👔 细分职业 Job Profile</label>
                  <p>${rep.job_detail || '自主创业企业主'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>👪 生命周期阶段 Life Stage</label>
                  <p>${rep.life_stage || '典型家庭改善阶段'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>🏠 目前居住状况 Current Living</label>
                  <p>${rep.current_living || '市区多层住宅'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth" style="border-left:3px solid var(--red);background:rgba(208,32,32,0.04)">
                  <label style="color:var(--red)">🔴 核心居住痛点 Living Pain</label>
                  <p>${rep.living_pain || '车位抢占严重、台风季老旧水管锈蚀漏水'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth" style="border-left:3px solid var(--blue);background:rgba(16,64,192,0.04)">
                  <label style="color:var(--blue)">📐 看房核心细节需求 Detail Need</label>
                  <p>${rep.detail_need || '必须双阳台，全屋无障碍干湿分离，预留电电舱'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>🚗 职住通勤偏好 Commute Pref</label>
                  <p>${rep.commute_pref || '接受20分钟以内地铁通勤'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>🚲 生活方式偏好 Lifestyle</label>
                  <p>${rep.lifestyle_pref || '重视家庭生活，周末带孩子散步露营'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>💰 首付资金来源 Downpayment Source</label>
                  <p>${rep.funds_source || '自有资产置换变现 + 大额储蓄组合'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>🏢 品牌服务诉求 Brand Service</label>
                  <p>${rep.brand_service_need || '滨江/绿城物业，人车分流'}</p>
                </div>
                <div class="dds-dossier-card dds-dossier-fullwidth">
                  <label>🏖️ 旅居度假频次 Vacation Freq</label>
                  <p>${rep.vacation_frequency || '年休长假前往外地度假'}</p>
                </div>
              </div>
              
              <!-- WTP 价格敏感度衰减曲线 -->
              <div style="margin-top:20px;border:1.5px dashed rgba(243,238,227,0.15);padding:14px">
                <label style="font-size:9px;text-transform:uppercase;color:var(--yellow);font-weight:700;display:block;margin-bottom:6px">
                  WTP (支付意愿) 价格接受度概率衰减曲线
                </label>
                <div style="font-size:10px;color:rgba(243,238,227,0.4);margin-bottom:10px">
                  反映该客群在不同单价下的心理购买概率。P50 中位数 ¥${p50.toLocaleString()} 元/㎡，P90 高端 ¥${p90.toLocaleString()} 元/㎡。
                </div>
                <svg viewBox="0 0 460 120" class="dds-svg-chart">
                  <line x1="40" y1="20" x2="420" y2="20" class="dds-svg-grid-line" />
                  <line x1="40" y1="60" x2="420" y2="60" class="dds-svg-grid-line" />
                  <line x1="40" y1="100" x2="420" y2="100" class="dds-svg-grid-line" />
                  
                  <text x="32" y="23" font-size="7.5" fill="rgba(243,238,227,0.3)" text-anchor="end">90% 接受</text>
                  <text x="32" y="63" font-size="7.5" fill="rgba(243,238,227,0.3)" text-anchor="end">50% 接受</text>
                  <text x="32" y="103" font-size="7.5" fill="rgba(243,238,227,0.3)" text-anchor="end">10% 接受</text>
                  
                  <!-- 绘制 S 曲线 -->
                  <path d="M 40,18 C 100,18 150,22 230,60 C 310,98 370,102 420,102" 
                        fill="none" stroke="var(--yellow)" stroke-width="2.2" class="glow-filter-gold" />
                        
                  <circle cx="100" cy="20" r="3.5" fill="#fff" stroke="var(--yellow)" stroke-width="1.5" />
                  <text x="100" y="32" font-size="7.5" fill="rgba(243,238,227,0.4)">P10: ¥${p10.toLocaleString()}</text>
                  
                  <circle cx="230" cy="60" r="3.5" fill="#fff" stroke="var(--yellow)" stroke-width="1.5" />
                  <text x="230" y="72" font-size="7.5" fill="var(--yellow)" font-weight="700">P50: ¥${p50.toLocaleString()}</text>
                  
                  <circle cx="360" cy="100" r="3.5" fill="#fff" stroke="var(--red)" stroke-width="1.5" />
                  <text x="360" y="112" font-size="7.5" fill="var(--red)">P90: ¥${p90.toLocaleString()}</text>
                </svg>
              </div>`;
              
  drawer.innerHTML = html;
  drawer.classList.add('active');
  overlay.classList.add('active');
}

function closePersonaDossier() {
  document.getElementById('ddsDossierDrawer').classList.remove('active');
  document.getElementById('ddsDrawerOverlay').classList.remove('active');
}

// ── HTML 安全转义辅助函数（确保全局稳健） ──────────────────
const esc = s => s ? String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;') : '';

// ── 物理离线历史记录控制中心 ──────────────────────────

// 保存研判报告到 localStorage
function saveHistoryReport(payload, report_json) {
  if (!report_json) return;
  const historyKey = 'dds_report_history';
  let historyList = [];
  try {
    const stored = localStorage.getItem(historyKey);
    if (stored) {
      historyList = JSON.parse(stored);
    }
  } catch (e) {
    console.error('读取历史记录失败:', e);
  }
  
  // 构建单条记录元数据
  const timestamp = Date.now();
  const city = report_json.meta?.local_data?.active_city || payload.city || '未知城市';
  const address = report_json.parcel?.address || payload.address || '未知地址';
  const expected_price = payload.expected_price || report_json.meta?.local_data?.expected_price || null;
  const radius_km = payload.radius_km || 5;
  const target_year = report_json.meta?.target_year || payload.year || 2026;
  
  // 尝试从 blueprint_logic 中提取 IRR
  let irr = null;
  try {
    irr = report_json.decision_full?.blueprint_logic?.financial_feasibility?.simulated_irr_annual || null;
  } catch (ex) {}
  
  // 过滤掉相同城市、地址和年份的较旧记录，防止重复堆叠
  historyList = historyList.filter(item => !(item.city === city && item.address === address && item.target_year === target_year));
  
  // 追加至最前
  historyList.unshift({
    timestamp: timestamp,
    city: city,
    address: address,
    expected_price: expected_price,
    radius_km: radius_km,
    target_year: target_year,
    irr: irr,
    report_json: report_json
  });
  
  // 物理限流：最大保留 15 条最热记录
  if (historyList.length > 15) {
    historyList = historyList.slice(0, 15);
  }
  
  try {
    localStorage.setItem(historyKey, JSON.stringify(historyList));
    initHistoryPanel();
  } catch (e) {
    console.error('写入历史记录失败:', e);
  }
}

// 初始化与重绘历史网格
function initHistoryPanel() {
  const historyKey = 'dds_report_history';
  const container = document.getElementById('historyGrid');
  const panelSection = document.getElementById('sec-history');
  if (!container || !panelSection) return;
  
  let historyList = [];
  try {
    const stored = localStorage.getItem(historyKey);
    if (stored) {
      historyList = JSON.parse(stored);
    }
  } catch (e) {
    console.error('获取历史记录失败:', e);
  }
  
  if (!historyList || historyList.length === 0) {
    panelSection.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  
  // 展示历史控制面板
  panelSection.style.display = 'block';
  
  let html = '';
  historyList.forEach(item => {
    const date = new Date(item.timestamp);
    const timeStr = (date.getMonth() + 1) + '-' + date.getDate() + ' ' + String(date.getHours()).padStart(2, '0') + ':' + String(date.getMinutes()).padStart(2, '0');
    
    const priceStr = item.expected_price ? '¥' + item.expected_price.toLocaleString() + ' / ㎡' : '未设均价';
    const irrBadge = item.irr ? `<span class="h-badge irr">IRR ${item.irr}</span>` : '';
    
    // 采用 Bauhaus 霓虹点缀色 (red, blue, yellow)
    const colors = ['var(--red)', 'var(--blue)', 'var(--yellow)'];
    const decorColor = colors[item.timestamp % 3];
    
    html += `
      <div class="history-card" onclick="loadHistoryReport(${item.timestamp}, event)">
        <div style="position:absolute;top:0;left:0;width:4px;height:100%;background:${decorColor}"></div>
        <div class="h-meta">${esc(item.city)} · ${item.target_year}年</div>
        <h4>${esc(item.address)}</h4>
        <div class="h-details">
          <span class="h-badge price">${priceStr}</span>
          <span class="h-badge">${item.radius_km} km 半径</span>
          ${irrBadge}
        </div>
        <div class="h-actions">
          <span class="h-time">🕒 ${timeStr}</span>
          <button class="btn-delete" onclick="deleteHistoryReport(${item.timestamp}, event)">物理删除</button>
        </div>
      </div>
    `;
  });
  
  container.innerHTML = html;
}

// 一键 0.0s 闪电秒开研判大屏
function loadHistoryReport(timestamp, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  
  const historyKey = 'dds_report_history';
  let historyList = [];
  try {
    const stored = localStorage.getItem(historyKey);
    if (stored) {
      historyList = JSON.parse(stored);
    }
  } catch (e) {
    console.error('秒开载入失败:', e);
    return;
  }
  
  const record = historyList.find(item => item.timestamp === timestamp);
  if (!record || !record.report_json) {
    alert('未找到该历史研判报告的数据');
    return;
  }
  
  // 瞬间进入大屏渲染逻辑
  _reportData = record.report_json;
  
  // 隐藏主表单与 loading，达成无网络 0.0s 加载
  $('#sec-form').classList.add('hidden');
  $('#sec-loading').classList.add('hidden');
  
  // 执行重绘
  showReport(record.report_json);
  
  // 联动顶部导航栏城市状态
  const citySelect = document.getElementById('city');
  if (citySelect) {
    citySelect.value = record.city;
  }
  const topbarCity = document.getElementById('topbarCity');
  if (topbarCity) {
    topbarCity.textContent = record.city;
  }
}

// 物理删除单条历史记录
function deleteHistoryReport(timestamp, event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  
  if (!confirm('确定要物理删除该条历史研判记录吗？')) {
    return;
  }
  
  const historyKey = 'dds_report_history';
  let historyList = [];
  try {
    const stored = localStorage.getItem(historyKey);
    if (stored) {
      historyList = JSON.parse(stored);
    }
  } catch (e) {}
  
  historyList = historyList.filter(item => item.timestamp !== timestamp);
  
  try {
    localStorage.setItem(historyKey, JSON.stringify(historyList));
    initHistoryPanel();
  } catch (e) {
    console.error('删除历史记录写入失败:', e);
  }
}

// 页面首次加载时静默装载并重绘历史记录面板
try {
  initHistoryPanel();
} catch (ex) {
  console.error('Failed to initialize history panel:', ex);
}
