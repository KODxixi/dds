"""基于已本地化数据生成单文件、离线可用的数据结构树 HTML。"""

from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path("Vault/建筑案例内容/kmlovemilk")
OUTPUT = ROOT / "数据结构树.html"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    data = json.loads((ROOT / "raw/site/data.json").read_text(encoding="utf-8"))
    items = read_jsonl(ROOT / "normalized/items.jsonl")
    media = read_jsonl(ROOT / "normalized/media.jsonl")
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    item_by_key = {item["source_key"]: item for item in items}
    hierarchy = []
    for category in data["categories"]:
        category_node = {"name": category["name"], "type": "category", "children": []}
        for developer in category["developers"]:
            developer_node = {
                "name": developer["name"], "type": "developer", "strategy": developer.get("strategy", ""), "children": []
            }
            for line in developer.get("lines", []):
                line_node = {
                    "name": line["line"], "type": "line", "description": line.get("description", ""), "design": line.get("design", ""), "children": []
                }
                for project in line.get("projects", []):
                    project_name = project.get("name") or project.get("displayName")
                    key = f"{category['name']}/{developer['name']}/{line['line']}/{project_name}"
                    item = item_by_key.get(key, {})
                    line_node["children"].append({
                        "name": project.get("displayName") or project_name,
                        "type": "project",
                        "tier": project.get("tier", ""),
                        "design": project.get("design") or line.get("design", ""),
                        "media": len(item.get("media_ids", [])),
                        "id": item.get("id", ""),
                    })
                developer_node["children"].append(line_node)
            category_node["children"].append(developer_node)
        hierarchy.append(category_node)

    ok_media = [row for row in media if row.get("status") == "ok"]
    payload = {
        "hierarchy": hierarchy,
        "stats": {
            "categories": len(hierarchy),
            "developers": sum(len(c["children"]) for c in hierarchy),
            "lines": sum(len(d["children"]) for c in hierarchy for d in c["children"]),
            "projects": len(items),
            "tags": manifest["counts"]["tags"],
            "media": len(ok_media),
            "objects": len({row["media_id"] for row in ok_media}),
            "errors": manifest["counts"]["media_errors"],
        },
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    document = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>kmlovemilk 本地知识包 · 数据结构树</title>
<style>
:root{--bg:#090b0f;--panel:#11151c;--panel2:#171c25;--line:#29313d;--text:#edf2f7;--muted:#8994a4;--gold:#d4b47c;--blue:#79b8ff;--green:#72d6a0;--red:#ff7b72}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#17202d 0,transparent 34%),var(--bg);color:var(--text);font:14px/1.55 Inter,"Microsoft YaHei",sans-serif}
.shell{min-height:100vh;display:grid;grid-template-columns:360px 1fr}.side{padding:28px 24px;border-right:1px solid var(--line);background:rgba(9,11,15,.82);backdrop-filter:blur(16px);position:sticky;top:0;height:100vh;overflow:auto}
h1{font:600 24px/1.2 Georgia,"Noto Serif SC",serif;margin:0 0 8px}.eyebrow{color:var(--gold);font-size:11px;letter-spacing:.18em;text-transform:uppercase}.desc{color:var(--muted);margin:10px 0 22px}.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}.stat b{display:block;font-size:20px;color:var(--gold)}.stat span{color:var(--muted);font-size:11px}
.legend{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}.legend div{display:flex;align-items:center;gap:8px;margin:8px 0;color:var(--muted)}.dot{width:9px;height:9px;border-radius:50%}.actions{display:flex;gap:8px;margin:18px 0}.actions button,.search{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:10px;padding:9px 11px}.actions button{cursor:pointer}.actions button:hover{border-color:var(--gold)}.search{width:100%;outline:none}.search:focus{border-color:var(--gold)}
main{padding:28px;min-width:0}.tabs{display:flex;gap:8px;margin-bottom:18px}.tab{border:1px solid var(--line);background:transparent;color:var(--muted);padding:9px 14px;border-radius:999px;cursor:pointer}.tab.active{background:var(--gold);border-color:var(--gold);color:#15110a;font-weight:700}.view{display:none}.view.active{display:block}.card{background:rgba(17,21,28,.9);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:14px}.card h2{margin:0 0 12px;font-size:16px}.tree{max-width:1200px}.node{margin-left:18px;border-left:1px solid var(--line);padding-left:12px}.node.hidden{display:none}.row{display:flex;align-items:center;gap:8px;min-height:34px;border-radius:8px;padding:4px 8px;cursor:pointer}.row:hover{background:var(--panel2)}.toggle{width:18px;color:var(--muted);text-align:center}.label{font-weight:600}.meta{color:var(--muted);font-size:12px}.pill{font-size:10px;border:1px solid var(--line);border-radius:999px;padding:1px 6px;color:var(--muted)}.children.collapsed{display:none}.category>.row .label{color:var(--gold)}.developer>.row .label{color:var(--blue)}.line>.row .label{color:var(--green)}.project>.row .label{font-weight:500}.empty{color:var(--red)}
.schema{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}.schema-box{background:var(--panel2);border:1px solid var(--line);border-radius:13px;padding:14px}.schema-box h3{margin:0 0 8px;color:var(--gold)}code{color:#b7c9e2}.arrow{color:var(--gold)}
.path-tree{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;color:#b7c9e2;line-height:1.8}.note{color:var(--muted);font-size:12px;margin-top:12px}@media(max-width:850px){.shell{display:block}.side{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--line)}main{padding:18px}}
</style></head>
<body><div class="shell"><aside class="side"><div class="eyebrow">LOCAL KNOWLEDGE PACKAGE</div><h1>数据结构树</h1><p class="desc">kmlovemilk.cn 全量本地知识包。业务结构、数据表关系与物理目录三种视角。</p><div class="stats" id="stats"></div><input class="search" id="search" placeholder="搜索项目 / 开发商 / 产品线"><div class="actions"><button id="expand">全部展开</button><button id="collapse">折叠到分类</button></div><div class="legend"><div><i class="dot" style="background:var(--gold)"></i>分类</div><div><i class="dot" style="background:var(--blue)"></i>开发商</div><div><i class="dot" style="background:var(--green)"></i>产品线</div><div><i class="dot" style="background:#d7dde7"></i>项目</div></div></aside>
<main><div class="tabs"><button class="tab active" data-view="business">业务树</button><button class="tab" data-view="schema">数据关系</button><button class="tab" data-view="files">物理目录</button></div>
<section class="view active" id="business"><div class="card"><h2>分类 → 开发商 → 产品线 → 项目</h2><div id="tree" class="tree"></div></div></section>
<section class="view" id="schema"><div class="card"><h2>Agent 调用关系</h2><div class="schema">
<div class="schema-box"><h3>items.jsonl</h3><code>id · title · category_path<br>developer · product_line · tier<br>designers[] · media_ids[]</code></div><div class="schema-box"><h3>item_tag_edges.jsonl</h3><code>item_id <span class="arrow">→</span> tag_id<br>tag_type</code></div><div class="schema-box"><h3>tags.jsonl</h3><code>tag_id · name_normalized<br>tag_type · item_count</code></div><div class="schema-box"><h3>media.jsonl</h3><code>source_url <span class="arrow">→</span> media_id<br>local_path · sha256 · owners[]</code></div><div class="schema-box"><h3>chunks.jsonl</h3><code>chunk_id <span class="arrow">→</span> item_id<br>text · tags · media_source_urls</code></div><div class="schema-box"><h3>media/objects</h3><code>sha256[:2]/sha256.ext<br>内容寻址 · 跨项目去重</code></div></div><p class="note">主关联键：items.id。媒体由源 URL 映射到 SHA-256 对象；标签使用边表，便于 Agent 组合过滤。</p></div></section>
<section class="view" id="files"><div class="card"><h2>本地目录</h2><div class="path-tree">kmlovemilk/
├─ raw/site/                 原始 HTML、JS、CSS、JSON、PWA 文件
│  ├─ data.json             业务四级层级
│  └─ image-map.json        项目 → 媒体 URL
├─ media/objects/            SHA-256 内容寻址对象
├─ normalized/
│  ├─ items.jsonl           656 个项目实体
│  ├─ tags.jsonl            602 个标签
│  ├─ item_tag_edges.jsonl  4,381 条关系
│  └─ media.jsonl           22,684 条媒体引用
├─ indexes/chunks.jsonl      Agent / RAG 分块
├─ crawl_state/media/        断点续传状态
├─ manifest.json             数据清单与响应元数据
├─ errors.jsonl              原站 404 引用
└─ QA_REPORT.md              完整性验收</div></div></section></main></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data=JSON.parse(document.getElementById('payload').textContent);const colors={categories:'分类',developers:'开发商',lines:'产品线',projects:'项目',tags:'标签',media:'本地媒体'};
document.getElementById('stats').innerHTML=Object.entries(colors).map(([k,v])=>`<div class="stat"><b>${data.stats[k].toLocaleString()}</b><span>${v}</span></div>`).join('');
function makeNode(n,depth=0){const box=document.createElement('div');box.className=`node ${n.type}`;box.dataset.text=[n.name,n.design,n.description,n.strategy].filter(Boolean).join(' ').toLowerCase();const has=n.children?.length;const row=document.createElement('div');row.className='row';let detail='';if(n.type==='project')detail=`<span class="pill">${n.tier||'未分级'}</span><span class="meta">${n.media} 媒体${n.design?' · '+n.design:''}</span>`;else if(has)detail=`<span class="meta">${n.children.length} 项</span>`;row.innerHTML=`<span class="toggle">${has?'▾':'·'}</span><span class="label"></span>${detail}`;row.querySelector('.label').textContent=n.name;box.appendChild(row);if(has){const children=document.createElement('div');children.className='children';n.children.forEach(c=>children.appendChild(makeNode(c,depth+1)));box.appendChild(children);row.onclick=()=>{children.classList.toggle('collapsed');row.querySelector('.toggle').textContent=children.classList.contains('collapsed')?'▸':'▾'}}return box}
const tree=document.getElementById('tree');data.hierarchy.forEach(n=>tree.appendChild(makeNode(n)));
document.getElementById('expand').onclick=()=>document.querySelectorAll('.children').forEach(x=>x.classList.remove('collapsed'));
document.getElementById('collapse').onclick=()=>document.querySelectorAll('.node').forEach(n=>{const c=n.querySelector(':scope > .children');if(c)c.classList.toggle('collapsed',!n.classList.contains('category'))});
document.getElementById('search').oninput=e=>{const q=e.target.value.trim().toLowerCase();document.querySelectorAll('.node').forEach(n=>n.classList.remove('hidden'));if(!q)return;document.querySelectorAll('.node.project,.node.line,.node.developer,.node.category').forEach(n=>{if(!n.dataset.text.includes(q)&&!n.querySelector(`[data-text*="${CSS.escape(q)}"]`))n.classList.add('hidden')});document.querySelectorAll('.children').forEach(x=>x.classList.remove('collapsed'))};
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{document.querySelectorAll('.tab,.view').forEach(x=>x.classList.remove('active'));t.classList.add('active');document.getElementById(t.dataset.view).classList.add('active')});
document.getElementById('collapse').click();
</script></body></html>'''.replace("__PAYLOAD__", payload_json)
    OUTPUT.write_text(document, encoding="utf-8")
    print(OUTPUT.resolve())
    print(json.dumps(payload["stats"], ensure_ascii=False))


if __name__ == "__main__":
    main()
