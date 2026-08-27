"""Architecture Director learning-event store.

Stores user/model feedback that can improve ranking, templates and role weights
without overwriting L1/L2 fact tables.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha1
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENT_DIR = ROOT / "data_out" / "architecture_learning"
EVENT_PATH = EVENT_DIR / "learning_events.jsonl"
SUMMARY_PATH = EVENT_DIR / "learning_summary.json"

ALLOWED_ACTIONS = {
    "case_accepted",
    "case_rejected",
    "section_rewritten",
    "prediction_backtest",
    "knowledge_gap_repeated",
    "weight_adjusted",
    "template_preferred",
}
FORBIDDEN_TARGETS = {"L1_fact_values", "L2_raw_fact_values", "source_provenance_history"}


def _event_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "learn_" + sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:14]


def _safe_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text[:limit]


def validate_learning_event(event: dict[str, Any]) -> tuple[bool, str | None]:
    action = event.get("action")
    if action not in ALLOWED_ACTIONS:
        return False, f"unsupported action: {action}"
    target = event.get("target")
    if target in FORBIDDEN_TARGETS:
        return False, f"forbidden learning target: {target}"
    return True, None


def record_learning_event(event: dict[str, Any], path: Path = EVENT_PATH) -> dict[str, Any]:
    payload = {
        "query_context": event.get("query_context") or {},
        "evidence_id": event.get("evidence_id") or event.get("asset_id") or "",
        "action": event.get("action"),
        "reason": _safe_text(event.get("reason")),
        "target": event.get("target") or "retrieval_ranking",
        "effect": event.get("effect") or {},
        "user": _safe_text(event.get("user") or "anonymous", 120),
        "model_version": event.get("model_version") or "dds_architecture_director_v1",
        "created_at": event.get("created_at") or datetime.now().isoformat(timespec="seconds"),
    }
    ok, error = validate_learning_event(payload)
    if not ok:
        return {"status": "error", "code": "INVALID_EVENT", "message": error}
    payload["event_id"] = event.get("event_id") or _event_id(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    summary = summarize_learning_events(path)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "event": payload, "summary": summary}


def load_learning_events(path: Path = EVENT_PATH, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    if limit:
        rows = rows[-limit:]
    return rows


def summarize_learning_events(path: Path = EVENT_PATH) -> dict[str, Any]:
    events = load_learning_events(path)
    actions = Counter(e.get("action") for e in events)
    targets = Counter(e.get("target") for e in events)
    accepted = Counter()
    rejected = Counter()
    gaps = Counter()
    for e in events:
        eid = e.get("evidence_id") or "unknown"
        if e.get("action") == "case_accepted":
            accepted[eid] += 1
        elif e.get("action") == "case_rejected":
            rejected[eid] += 1
        elif e.get("action") == "knowledge_gap_repeated":
            gaps[eid or e.get("reason") or "gap"] += 1
    retrieval_adjustments = []
    for eid, n in accepted.items():
        retrieval_adjustments.append({"evidence_id": eid, "delta": round(min(0.3, n * 0.05), 3), "reason": "accepted_cases"})
    for eid, n in rejected.items():
        retrieval_adjustments.append({"evidence_id": eid, "delta": round(max(-0.4, -n * 0.08), 3), "reason": "rejected_cases"})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "event_count": len(events),
        "actions": dict(actions),
        "targets": dict(targets),
        "accepted_top": accepted.most_common(10),
        "rejected_top": rejected.most_common(10),
        "knowledge_gap_top": gaps.most_common(10),
        "retrieval_adjustments": retrieval_adjustments[:30],
        "rules": {
            "allowed_updates": ["retrieval_ranking", "role_weights", "template_preference", "case_negative_samples", "gap_queue_priority"],
            "forbidden_updates": sorted(FORBIDDEN_TARGETS),
        }
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="DDS Architecture Director learning events")
    parser.add_argument("cmd", choices=["record", "summary", "list"])
    parser.add_argument("--action", default="case_accepted")
    parser.add_argument("--evidence-id", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--target", default="retrieval_ranking")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.cmd == "record":
        result = record_learning_event({
            "action": args.action,
            "evidence_id": args.evidence_id,
            "reason": args.reason,
            "target": args.target,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.cmd == "summary":
        print(json.dumps(summarize_learning_events(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(load_learning_events(limit=args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())