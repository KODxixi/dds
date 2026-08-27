# -*- coding: utf-8 -*-
"""
ABM 客群偏好「贴真」校准：从真实楼盘的标签/物业类型/户型，推导每城 7 维真实市场偏好信号
（市场实际供给在每个偏好维度上的强度），对比 abm_engine.CITY_POOLS 的专家预设，输出漂移报告
+ 真实校准 JSON。供 abm_engine 加载覆盖预设（监督式接地，替代纯拍脑袋）。
说明：这是**供给侧**接地（真实在售盘在各维度的供给比例≈该市场对该维度的真实侧重）；
需求侧（谁真买了什么）需真实网签/成交数据，本仓暂无，已如实标注。
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
csv.field_size_limit(10**7)
DIMS = ["景观", "私密", "圈层", "户型", "通勤", "学校", "品牌"]

# 真实标签/类型/字段 -> 7 维偏好信号
SIGNAL = {
    "景观": ["景观", "绿化", "公园", "生态", "湖", "江", "海", "山", "园林"],
    "私密": ["低密", "别墅", "洋房", "叠拼", "联排", "合院", "院子", "独栋"],
    "圈层": ["豪华", "国际化", "高端", "改善", "豪宅", "圈层", "尊", "府", "墅"],
    "户型": ["大平层", "平层", "舒适", "三居", "四居", "五居", "大户", "全明"],
    "通勤": ["地铁", "轨交", "通勤", "tod", "交通", "高铁", "枢纽"],
    "学校": ["学区", "名校", "学校", "教育", "双语", "国际学校"],
    "品牌": ["品牌", "央企", "国企", "精装", "装修交付", "物业", "服务", "智慧"],
}


def real_profile(city):
    """真实市场 7 维信号(0..1, 相对归一)。"""
    p = ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{city}.csv"
    if not p.exists():
        return None, 0
    cnt = defaultdict(int); n = 0
    rows = list(csv.reader(open(p, encoding="utf-8-sig"))); h = rows[0]
    it = h.index("标签列表"); pt = h.index("物业类型"); rt = h.index("户型文本描述") if "户型文本描述" in h else it
    for r in rows[1:]:
        n += 1
        blob = ((r[it] if it < len(r) else "") + (r[pt] if pt < len(r) else "") + (r[rt] if rt < len(r) else "")).lower()
        for dim, kws in SIGNAL.items():
            if any(k.lower() in blob for k in kws):
                cnt[dim] += 1
    if not n:
        return None, 0
    raw = {d: cnt[d] / n for d in DIMS}
    mx = max(raw.values()) or 1.0
    return {d: round(raw[d] / mx, 3) for d in DIMS}, n   # 相对归一到 0..1


def main():
    from abm_engine import CITY_POOLS
    out = {"_note": "供给侧真实接地;需求侧需网签数据", "cities": {}}
    print("城市 | 样本 | 真实市场信号(7维) vs 预设均值 漂移Top")
    for city, pool in CITY_POOLS.items():
        prof, n = real_profile(city)
        if not prof:
            continue
        # 预设均值(按占比加权)
        tw = sum(a.weight for a in pool) or 1.0
        preset = {d: round(sum(a.weight * a.pref.get(d, 0) for a in pool) / tw, 3) for d in DIMS}
        drift = {d: round(prof[d] - preset[d], 3) for d in DIMS}
        top = sorted(drift.items(), key=lambda x: -abs(x[1]))[:3]
        out["cities"][city] = {"n_projects": n, "real_signal": prof, "preset_avg": preset, "drift": drift}
        print(f"{city}({n}) Top漂移: " + ", ".join(f"{d}{v:+.2f}" for d, v in top))
    op = ROOT / "data" / "archetype_real_calibration.json"
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[saved]", op, "| 覆盖城市:", len(out["cities"]))


if __name__ == "__main__":
    main()
