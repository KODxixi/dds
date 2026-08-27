"""
ArchRefer 摄取管线(二线/发现用):扫描案例库 .md 笔记 → 比对 benchmark_library.json
→ 输出"未入库的新项目"候选(含来源链接 + 原文摘要 + 待打分模板)。

⚠ 主接入口已改为 `scripts/sync_archlib_benchmark.py`：案例库已迁至 D:/ArchLib，其检索系统
  自带逐图 VLM 打标并导出 `_检索系统/data/dds_benchmark.json`(= DDS 对标库)。常规更新请用
  sync 脚本直接同步该导出；本脚本仅在需要"从笔记里发现尚未入 ArchLib 的新项目"时使用，
  且 ArchLib 的 README/打标非 `|字段|值|` 表格，parse_note 抽取能力有限。

用法:
  python scripts/ingest_archref.py                       # 默认 vault 路径,打印新项目报告
  python scripts/ingest_archref.py --vault "D:/ArchLib"
  python scripts/ingest_archref.py --out data_out/archref_candidates.json   # 落盘候选

纯标准库。打分(10 维)需人工/LLM 判断,脚本只做"发现 + 抽取 + 去重"。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "data" / "benchmark_library.json"
DEFAULT_VAULT = r"D:/ArchLib"

DIMS = ["demo_zone", "floorplan", "podium_lift", "display_zone", "sunken_club",
        "policy_leverage", "open_floor", "landscape", "facade", "narrative"]

# ArchLib 新结构(业态单轴 8 大类，跳过 90_资源 非案例)。详见 D:/ArchLib/AGENTS.md
RELEVANT_DIRS = ("10_居住", "20_酒店旅居", "30_营销空间", "40_商业办公",
                 "50_文化公共", "60_城市·更新", "70_室内", "80_专项库")

_URL = re.compile(r"https?://[^\s)\]]+")


def _norm(s: str) -> str:
    """归一化项目名:去标点空格,留中英数字,用于去重比对"""
    return re.sub(r"[^\w一-鿿]", "", s or "").lower()


def parse_note(p: Path) -> dict:
    """从一篇 .md 笔记抽取:标题/来源URL/设计方/开发商/城市/年份/摘要"""
    text = p.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), p.stem)
    urls = list(dict.fromkeys(_URL.findall(text)))            # 去重保序
    def field(*keys):
        for k in keys:
            m = re.search(rf"\|\s*{k}[^\|]*\|\s*([^\|]+?)\s*\|", text)
            if m:
                return re.sub(r"[*`]", "", m.group(1)).strip()
        return None
    year = None
    ym = re.search(r"(20\d{2})", title) or re.search(r"(20\d{2})\s*年", text)
    if ym:
        year = int(ym.group(1))
    # 正文摘要:跳过表格/引用,取前 600 字
    body = "\n".join(l for l in lines if not l.startswith(("|", ">", "#"))).strip()
    return {
        "note_path": str(p),
        "title": title,
        "designer": field("建筑设计", "设计方", "设计师", "建筑"),
        "developer": field("开发商", "客户"),
        "city": field("位置", "所在地", "落址"),
        "year": year,
        "sources": [u for u in urls][:4],
        "excerpt": re.sub(r"\n{2,}", "\n", body)[:600],
    }


def scan_vault(vault: Path) -> list[dict]:
    notes = []
    for p in vault.rglob("*.md"):
        rel = str(p.relative_to(vault))
        if not any(d in rel for d in RELEVANT_DIRS):
            continue
        try:
            notes.append(parse_note(p))
        except Exception as e:
            print(f"[warn] 解析失败 {p.name}: {e}", file=sys.stderr)
    return notes


def diff_against_library(notes: list[dict], lib: dict) -> dict:
    """去重:优先按来源 URL 精确比对(入库案例已存原始链接),兜底按项目名归一化"""
    lib_urls = {s["url"].strip() for c in lib["cases"] for s in c.get("sources", []) if s.get("url")}
    existing = [_norm(c["name"]) for c in lib["cases"]]
    new, known = [], []
    for n in notes:
        by_url = any(u.strip() in lib_urls for u in n["sources"])
        key = _norm(re.split(r"[·\-—:：(（|丨｜]", re.sub(r"^(goa进行时|goa作品|【[^】]*】|首发)", "", n["title"]))[0])
        by_name = key and len(key) >= 3 and any(key in e for e in existing)
        (known if (by_url or by_name) else new).append(n)
    return {"new": new, "known": known}


def candidate(n: dict) -> dict:
    """生成待打分案例草稿(scores 留空,等人工/LLM 判定)"""
    return {
        "id": "TODO_" + _norm(n["title"])[:24],
        "name": n["title"],
        "developer": n.get("developer"), "designer": n.get("designer"),
        "city": n.get("city"), "year": n.get("year"),
        "category": "现象级产品",
        "scores": {d: None for d in DIMS},
        "moves": {}, "key_tactics": [],
        "data_quality": "media_verified", "confidence": None,
        "sources": [{"title": "ArchRefer 笔记", "url": u} for u in n["sources"]],
        "_excerpt": n["excerpt"], "_note": n["note_path"],
    }


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ArchRefer → benchmark 摄取/去重")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--out", help="候选案例落盘路径(JSON)")
    a = ap.parse_args(argv)

    vault = Path(a.vault)
    if not vault.exists():
        print(f"[error] vault 不存在: {vault}")
        return
    lib = json.loads(LIB.read_text(encoding="utf-8"))
    notes = scan_vault(vault)
    diff = diff_against_library(notes, lib)
    print(f"扫描 {len(notes)} 篇笔记 | 已入库 {len(diff['known'])} | 新发现 {len(diff['new'])}\n")
    print("== 新发现(待入库)==")
    for n in diff["new"]:
        src = n["sources"][0] if n["sources"] else "(无链接)"
        print(f"  · {n['title']}  [{n.get('designer') or '-'}/{n.get('city') or '-'}]  {src}")
    if a.out:
        cands = [candidate(n) for n in diff["new"]]
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[OK] {len(cands)} 个候选(含来源+摘要+待打分模板)已写入 {a.out}")


if __name__ == "__main__":
    main()
