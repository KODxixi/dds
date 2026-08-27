"""
客群语义反向校验：从 Vault 真实样本反推 archetype pref 均值，对比预设输出偏差报告
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CALIB_DIR = ROOT / "data_out" / "calibration"
CALIB_DIR.mkdir(parents=True, exist_ok=True)

PREF_KEYS = ("景观", "私密", "圈层", "户型", "通勤", "学校", "品牌")


def calibrate_from_vault(city: str, year: int) -> dict:
    """聚合 Vault 样本里每个 archetype 在 7 维 pref 上的均值 + 样本数"""
    csv_path = VAULT / f"{year}年" / f"客群样本-{city}.csv"
    if not csv_path.exists():
        return {"status": "no_data", "reason": f"{csv_path.name} 不存在"}
    arch_pref = {}  # archetype -> {pref_key: [vals]}
    arch_risk = {}  # 风险厌恶均值
    with open(csv_path, encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            arch = (row.get("客群类型") or "").strip()
            if not arch:
                continue
            arch_pref.setdefault(arch, {k: [] for k in PREF_KEYS})
            for k in PREF_KEYS:
                v = row.get(f"偏好_{k}")
                try:
                    arch_pref[arch][k].append(float(v))
                except (TypeError, ValueError):
                    continue
            try:
                arch_risk.setdefault(arch, []).append(float(row.get("风险厌恶", 0.5)))
            except (TypeError, ValueError):
                pass
    out = {}
    for arch, prefs in arch_pref.items():
        n = max(len(v) for v in prefs.values())
        out[arch] = {
            "n_samples": n,
            "pref": {k: round(statistics.mean(v), 3) if v else None for k, v in prefs.items()},
            "risk_aversion": round(statistics.mean(arch_risk[arch]), 3) if arch_risk.get(arch) else None,
        }
    return {
        "status": "ok",
        "city": city, "year": year,
        "n_archetypes": len(out),
        "calibration": out,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


def diff_against_preset(calibration: dict) -> list[dict]:
    """对比 calibration 与 CITY_POOLS 预设，列出每 archetype × 维度 的偏差"""
    from scripts.abm_engine import CITY_POOLS
    if calibration.get("status") != "ok":
        return []
    pool = CITY_POOLS.get(calibration["city"]) or []
    preset_map = {a.name: a for a in pool}
    diffs = []
    for arch, info in calibration["calibration"].items():
        a = preset_map.get(arch)
        if not a:
            continue
        for k in PREF_KEYS:
            real = (info.get("pref") or {}).get(k)
            pre = a.pref.get(k)
            if real is None or pre is None:
                continue
            d = round(real - pre, 3)
            diffs.append({
                "archetype": arch, "pref": k,
                "preset": pre, "real": real, "delta": d,
                "drift_level": "高" if abs(d) >= 0.15 else ("中" if abs(d) >= 0.07 else "低"),
            })
    diffs.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return diffs


def apply_calibration(calibration: dict) -> int:
    """把校准均值写回 CITY_POOLS 实例的 pref；返回更新的 archetype 数"""
    from scripts.abm_engine import CITY_POOLS
    if calibration.get("status") != "ok":
        return 0
    pool = CITY_POOLS.get(calibration["city"]) or []
    preset_map = {a.name: a for a in pool}
    updated = 0
    for arch, info in calibration["calibration"].items():
        a = preset_map.get(arch)
        if not a:
            continue
        real_pref = info.get("pref") or {}
        for k, v in real_pref.items():
            if v is not None:
                a.pref[k] = v
        if info.get("risk_aversion") is not None:
            a.risk_aversion = info["risk_aversion"]
        updated += 1
    return updated


def save_calibration(calibration: dict, suffix: str = "") -> Path:
    """落盘到 data_out/calibration/{city}_{year}{suffix}.json"""
    if calibration.get("status") != "ok":
        return CALIB_DIR / "noop.json"
    fname = f"{calibration['city']}_{calibration['year']}{suffix}.json"
    path = CALIB_DIR / fname
    path.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def latest_calibration_for(city: str) -> dict | None:
    """加载某城最新一份校准（按文件名年份倒序）"""
    files = sorted(CALIB_DIR.glob(f"{city}_*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def summary_text(diffs: list[dict], top_n: int = 5) -> str:
    """生成偏差摘要：高漂移项 + 整体一致性"""
    if not diffs:
        return "暂无校验数据"
    high = sum(1 for d in diffs if d["drift_level"] == "高")
    mid = sum(1 for d in diffs if d["drift_level"] == "中")
    total = len(diffs)
    lines = [f"共校验 {total} 项 · 高漂移 {high} · 中漂移 {mid} · 一致性 {(1 - high/total)*100:.0f}%"]
    if high:
        lines.append("Top 漂移：")
        for d in diffs[:top_n]:
            lines.append(f"  - {d['archetype']}.{d['pref']}: 预设 {d['preset']} → 真实 {d['real']} ({d['delta']:+.2f})")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="校准后写入 CITY_POOLS")
    args = ap.parse_args()
    calib = calibrate_from_vault(args.city, args.year)
    if calib["status"] != "ok":
        print(calib)
        raise SystemExit(1)
    diffs = diff_against_preset(calib)
    calib["diffs"] = diffs
    path = save_calibration(calib)
    print(f"[saved] {path}")
    print(summary_text(diffs))
    if args.apply:
        n = apply_calibration(calib)
        print(f"[applied] {n} archetypes 已校准到 CITY_POOLS")
