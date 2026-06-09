# DDS 网页内 AI 助手实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 `http://localhost:8080` DDS 报告页内加入一个安全的 AI 对话助手，围绕当前 `report_json` 解读地块、竞品、配套、风险和产品定位。

**架构：** 前端在报告渲染后显示 Chat 面板，并把用户问题与当前报告上下文发送到 Flask 新增的 `/api/chat`。后端复用现有 Anthropic-compatible DeepSeek 配置调用 LLM，但只返回文本建议，不执行 shell、不调用 Claude Code CLI、不修改本地文件。

**技术栈：** Flask、Anthropic Python SDK、Vanilla JS、现有 `index.html` Bauhaus UI、`pytest` Flask test client。

---

## 文件结构

- 修改：`app.py`
  - 新增 `build_chat_prompt(message, report_json)`：把用户问题和报告上下文压缩为投拓分析提示词。
  - 新增 `run_report_chat(message, report_json, timeout=45)`：调用 DeepSeek / Anthropic-compatible API，并在失败时返回规则兜底。
  - 新增 `POST /api/chat`：校验输入、调用 chat、返回 `{status, answer, llm_used}`。
- 修改：`index.html`
  - 新增 Chat 面板 CSS。
  - 在 `showReport(r)` 的报告区末尾渲染 Chat 面板。
  - 新增 `sendChatMessage()`、`appendChatMessage()`、`buildChatContext()`。
- 修改：`test_smoke.py`
  - 新增后端 prompt 单元测试。
  - 新增 `/api/chat` 输入校验测试。
  - 新增前端 HTML 结构烟测。

---

### 任务 1：后端 Chat Prompt 与输入边界

**文件：**
- 修改：`app.py`
- 测试：`test_smoke.py`

- [ ] **步骤 1：编写失败的测试**

在 `test_smoke.py` 现有测试后添加：

```python
def test_build_chat_prompt_limits_scope():
    from app import build_chat_prompt

    report = {
        "parcel": {"address": "三亚海棠区南田路16号", "lng": 109.7261, "lat": 18.3960},
        "market": {
            "sample_size": 30,
            "avg_price": 35712,
            "competitors": [
                {"project_name": "开创·白玉海棠", "unit_price_cny": 33000, "distance_km": 0.39, "room_types": "三室,四室"}
            ],
        },
        "decision": {"summary": "建议有条件进入", "top_personas": [{"name": "度假改善", "score": 58}]},
        "amenities": {"school": {"label": "学校", "items": [{"name": "人才基地幼儿园", "distance_m": 791}]}},
    }

    prompt = build_chat_prompt("这个地块最大风险是什么？", report)

    assert "这个地块最大风险是什么？" in prompt
    assert "三亚海棠区南田路16号" in prompt
    assert "开创·白玉海棠" in prompt
    assert "不执行命令" in prompt
    assert "不要编造" in prompt
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest test_smoke.py::test_build_chat_prompt_limits_scope -q
```

预期：FAIL，报错包含 `ImportError` 或 `cannot import name 'build_chat_prompt'`。

- [ ] **步骤 3：编写最少实现代码**

在 `app.py` 的 `run_deep_analysis()` 后、路由声明前添加：

```python
def build_chat_prompt(message: str, report_json: dict) -> str:
    parcel = report_json.get("parcel", {}) or {}
    market = report_json.get("market", {}) or {}
    decision = report_json.get("decision", {}) or {}
    amenities = report_json.get("amenities", {}) or {}
    comps = market.get("competitors", []) or []

    comp_lines = []
    for c in comps[:10]:
        comp_lines.append(
            f"- {c.get('project_name') or '未知项目'}：{c.get('unit_price_cny') or '—'}元/㎡，"
            f"距地块{c.get('distance_km') or '—'}km，户型{c.get('room_types') or '—'}"
        )

    amenity_lines = []
    for key, payload in amenities.items():
        items = (payload or {}).get("items") or []
        if items:
            first = items[0]
            amenity_lines.append(f"- {(payload or {}).get('label', key)}：{first.get('name', '—')}，{first.get('distance_m') or first.get('distance') or '—'}m")

    return f"""你是 DDS 网页内 AI 投拓助手，只能围绕当前 DDS 报告回答。

安全边界：
- 不执行命令，不调用 shell，不修改代码，不调用 Claude Code CLI。
- 不暴露、猜测或要求用户提供 API Key。
- 不要编造报告中没有的数据；缺数据时明确说明。
- 回答必须基于当前报告 JSON、竞品、配套、风险和产品定位。

当前地块：
- 地址：{parcel.get('address') or '未提供'}
- 坐标：{parcel.get('lng')}, {parcel.get('lat')}
- 竞品样本：{market.get('sample_size') or 0} 个
- 周边均价：{market.get('avg_price') or '—'} 元/㎡

竞品摘要：
{chr(10).join(comp_lines) or '暂无竞品'}

配套摘要：
{chr(10).join(amenity_lines) or '暂无配套'}

已有决策摘要：
{decision.get('summary') or '暂无'}

用户问题：
{message}

请用中文回答，先给结论，再给 3-5 条依据。"""
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest test_smoke.py::test_build_chat_prompt_limits_scope -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

如果当前目录是 git 仓库，运行：

```powershell
git add app.py test_smoke.py
git commit -m "feat: add DDS chat prompt builder"
```

如果不是 git 仓库，跳过 commit，并在最终报告说明环境不是 git 仓库。

---

### 任务 2：新增 `/api/chat` 后端接口

**文件：**
- 修改：`app.py`
- 测试：`test_smoke.py`

- [ ] **步骤 1：编写失败的测试**

在 `test_smoke.py` 添加：

```python
def test_chat_endpoint_requires_message(client):
    resp = client.post("/api/chat", json={"report_json": {}})
    data = resp.get_json()

    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"


def test_chat_endpoint_requires_report(client):
    resp = client.post("/api/chat", json={"message": "分析风险"})
    data = resp.get_json()

    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest test_smoke.py::test_chat_endpoint_requires_message test_smoke.py::test_chat_endpoint_requires_report -q
```

预期：FAIL，当前接口不存在，返回 404。

- [ ] **步骤 3：编写最少实现代码**

在 `app.py` 的 `/api/report` 路由后、`web_search_supplement()` 前添加：

```python
def run_report_chat(message: str, report_json: dict, timeout: int = 45) -> dict:
    prompt = build_chat_prompt(message, report_json)
    fallback = {
        "llm_used": False,
        "answer": "AI 助手暂时不可用。当前报告已生成，可以先查看竞品分析、风险推演和数据声明；如果需要，我可以基于页面现有数据继续做规则化解读。",
    }

    try:
        from anthropic import Anthropic
        client = Anthropic(
            api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
            base_url=os.environ["ANTHROPIC_BASE_URL"],
            timeout=timeout,
        )
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro[1m]"),
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = ""
        for block in resp.content:
            if hasattr(block, "text"):
                raw += block.text
        raw = raw.strip()
        if not raw:
            return fallback
        return {"llm_used": True, "answer": raw}
    except Exception as e:
        log.warning("DDS chat failed, using fallback: %s", e)
        return fallback


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    report_json = data.get("report_json")
    if not message or not isinstance(report_json, dict):
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "缺少问题或报告上下文"}), 400
    if len(message) > 1000:
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "问题过长，请缩短到 1000 字以内"}), 400

    result = run_report_chat(message, report_json)
    return jsonify({"status": "ok", "answer": result["answer"], "llm_used": result["llm_used"]})
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```powershell
python -m pytest test_smoke.py::test_chat_endpoint_requires_message test_smoke.py::test_chat_endpoint_requires_report -q
```

预期：PASS。

- [ ] **步骤 5：Commit**

如果当前目录是 git 仓库，运行：

```powershell
git add app.py test_smoke.py
git commit -m "feat: add DDS report chat endpoint"
```

如果不是 git 仓库，跳过 commit，并在最终报告说明环境不是 git 仓库。

---

### 任务 3：前端 Chat 面板骨架

**文件：**
- 修改：`index.html`
- 测试：`test_smoke.py`

- [ ] **步骤 1：编写失败的测试**

在 `test_smoke.py` 添加：

```python
def test_chat_panel_markup_exists():
    html = open("index.html", encoding="utf-8").read()

    assert "ddsChatPanel" in html
    assert "chatMessages" in html
    assert "chatInput" in html
    assert "sendChatMessage" in html
    assert "POST', headers: { 'Content-Type': 'application/json' }" in html
    assert "/api/chat" in html
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```powershell
python -m pytest test_smoke.py::test_chat_panel_markup_exists -q
```

预期：FAIL，断言找不到 `ddsChatPanel`。

- [ ] **步骤 3：添加最少 CSS**

在 `index.html` 的 `<style>` 区域中，靠近 `.card` 或 `.map-wrap` 样式后添加：

```css
.chat-panel { position: sticky; bottom: 0; z-index: 20; margin-top: 36px; border: 3px solid var(--ink); background: rgba(243,238,227,.96); box-shadow: 10px 10px 0 var(--ink); }
.chat-head { display: flex; justify-content: space-between; align-items: center; padding: 16px 18px; border-bottom: 3px solid var(--ink); background: var(--yellow); }
.chat-head h3 { margin: 0; font-size: 18px; font-weight: 900; }
.chat-messages { max-height: 320px; overflow: auto; padding: 18px; display: grid; gap: 12px; }
.chat-msg { padding: 12px 14px; border: 2px solid var(--ink); background: var(--white); line-height: 1.7; white-space: pre-wrap; }
.chat-msg.user { margin-left: 12%; background: #eef3ff; border-color: var(--blue); }
.chat-msg.assistant { margin-right: 12%; }
.chat-form { display: grid; grid-template-columns: 1fr auto; gap: 10px; padding: 14px 18px; border-top: 3px solid var(--ink); }
.chat-form input { border: 2px solid var(--ink); padding: 12px; font-size: 15px; background: var(--white); }
.chat-form button { border: 2px solid var(--ink); background: var(--ink); color: var(--paper); padding: 0 18px; font-weight: 900; cursor: pointer; }
.chat-form button:disabled { opacity: .55; cursor: wait; }
```

- [ ] **步骤 4：在报告渲染中加入 Chat 面板**

在 `showReport(r)` 中，所有报告 section 拼接完成、`$('#reportSections').innerHTML = html;` 之前加入：

```javascript
  html += '<section id="ddsChatPanel" class="chat-panel">';
  html += '<div class="chat-head"><h3>DDS AI 投拓助手</h3><span>基于当前报告回答</span></div>';
  html += '<div class="chat-messages" id="chatMessages"><div class="chat-msg assistant">报告已载入。你可以问我：最大风险、竞品对比、户型建议、拿地价格边界。</div></div>';
  html += '<form class="chat-form" id="chatForm"><input id="chatInput" autocomplete="off" placeholder="例如：这个地块最大风险是什么？"><button id="chatSendBtn" type="submit">发送</button></form>';
  html += '</section>';
```

然后在 `showReport(r)` 设置完 `innerHTML` 后添加事件绑定：

```javascript
  const chatForm = $('#chatForm');
  if (chatForm) {
    chatForm.addEventListener('submit', e => {
      e.preventDefault();
      sendChatMessage();
    });
  }
```

- [ ] **步骤 5：运行测试验证仍失败在 JS 函数缺失**

运行：

```powershell
python -m pytest test_smoke.py::test_chat_panel_markup_exists -q
```

预期：FAIL，断言找不到 `sendChatMessage` 或 `/api/chat`。

- [ ] **步骤 6：Commit**

如果当前目录是 git 仓库，运行：

```powershell
git add index.html test_smoke.py
git commit -m "feat: add DDS chat panel markup"
```

如果不是 git 仓库，跳过 commit，并在最终报告说明环境不是 git 仓库。

---

### 任务 4：前端 Chat 交互与 `/api/chat` 调用

**文件：**
- 修改：`index.html`
- 测试：`test_smoke.py`

- [ ] **步骤 1：补齐 JS 实现代码**

在 `index.html` 中 `resetToForm()` 后、`showReport(r)` 前添加：

```javascript
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
  appendChatMessage('assistant', '分析中…');
  if (btn) btn.disabled = true;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, report_json: buildChatContext() }),
    });
    const data = await resp.json();
    const pending = $('#chatMessages .chat-msg.assistant:last-child');
    if (pending && pending.textContent === '分析中…') pending.remove();
    if (data.status === 'ok') appendChatMessage('assistant', data.answer || '没有返回内容');
    else appendChatMessage('assistant', data.message || 'AI 助手暂时不可用');
  } catch (err) {
    const pending = $('#chatMessages .chat-msg.assistant:last-child');
    if (pending && pending.textContent === '分析中…') pending.remove();
    appendChatMessage('assistant', '无法连接 DDS AI 助手，请确认 python app.py 正在运行。');
  } finally {
    if (btn) btn.disabled = false;
  }
}
```

- [ ] **步骤 2：运行前端 HTML 烟测验证通过**

运行：

```powershell
python -m pytest test_smoke.py::test_chat_panel_markup_exists -q
```

预期：PASS。

- [ ] **步骤 3：运行全量烟测**

运行：

```powershell
python -m pytest test_smoke.py -q
```

预期：全部 PASS。

- [ ] **步骤 4：Commit**

如果当前目录是 git 仓库，运行：

```powershell
git add index.html test_smoke.py
git commit -m "feat: wire DDS chat panel to backend"
```

如果不是 git 仓库，跳过 commit，并在最终报告说明环境不是 git 仓库。

---

### 任务 5：浏览器验证网页内 DDS AI 助手

**文件：**
- 不修改文件
- 浏览器验证：`http://localhost:8080`

- [ ] **步骤 1：启动服务**

运行：

```powershell
python app.py
```

预期：服务监听 `http://localhost:8080`。

- [ ] **步骤 2：生成报告**

在浏览器打开：

```text
http://localhost:8080
```

输入：

```text
城市：三亚
地址：三亚海棠区南田路16号
预期均价：35000
```

点击“生成报告”。

预期：报告显示 `地块画像`、`区位关系`、`竞品分析`、`DDS AI 投拓助手`。

- [ ] **步骤 3：发送 Chat 问题**

在 Chat 面板输入：

```text
这个地块最大风险是什么？
```

预期：助手返回中文回答，内容提到当前报告的竞品、价格或风险，不要求 API Key，不执行命令。

- [ ] **步骤 4：检查控制台**

打开浏览器控制台。

预期：没有新的 JavaScript error；如果 DeepSeek API 暂不可用，页面显示兜底回答但不崩溃。

---

## 自检结果

- 规格覆盖：覆盖网页内聊天、当前报告上下文、后端代理、API Key 不暴露、不执行 shell。
- 范围控制：第一版不做 Claude Code CLI 嵌入、不做任意命令执行、不做多会话持久历史。
- 类型一致性：后端统一使用 `report_json`，前端 `_reportData` 作为上下文来源，接口返回 `answer` 和 `llm_used`。
- 测试路径：每个后端行为都有 pytest；前端用 HTML 烟测 + 浏览器手测验证。
