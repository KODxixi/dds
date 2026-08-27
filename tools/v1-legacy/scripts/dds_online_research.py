"""
DDS 线上补充证据模块。

本模块只定义线上证据结构和读写合并逻辑；线上搜索由 Claude Code 基座执行。
线上证据只能补充溯源，不覆盖本地数据库结论。
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE_DIR = ROOT / "data_out" / "online_evidence"


def make_evidence(query: str, title: str, url: str, summary: str, data_date: str = "", confidence: str = "supplemental") -> dict:
    return {
        "query": query,
        "title": title,
        "url": url,
        "summary": summary,
        "data_date": data_date,
        "confidence": confidence,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "role": "online_supplement_only",
    }


def load_evidence(path: str | Path | None) -> list[dict]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("evidence"), list):
        return data["evidence"]
    return []


def write_evidence(evidence: list[dict], name: str = "online_evidence", out_dir: Path = DEFAULT_EVIDENCE_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{ts}_{safe_slug(name)}.json"
    path.write_text(json.dumps({"evidence": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def evidence_summary(evidence: list[dict]) -> dict:
    return {
        "enabled": bool(evidence),
        "count": len(evidence),
        "sources": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "data_date": item.get("data_date"),
                "confidence": item.get("confidence", "supplemental"),
            }
            for item in evidence
        ],
    }


def safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_")[:60] or "online_evidence"


def main() -> int:
    parser = argparse.ArgumentParser(description="DDS 线上补充证据 JSON 工具")
    parser.add_argument("--query", help="搜索关键词")
    parser.add_argument("--title", help="来源标题")
    parser.add_argument("--url", help="来源 URL")
    parser.add_argument("--summary", help="摘要")
    parser.add_argument("--data-date", default="", help="数据日期")
    parser.add_argument("--out-name", default="online_evidence")
    args = parser.parse_args()

    if not (args.query and args.title and args.url and args.summary):
        parser.error("需要 --query --title --url --summary")

    evidence = [make_evidence(args.query, args.title, args.url, args.summary, args.data_date)]
    path = write_evidence(evidence, args.out_name)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
