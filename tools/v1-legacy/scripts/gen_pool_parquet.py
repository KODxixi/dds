# -*- coding: utf-8 -*-
"""为数据池所有 新楼盘-*.csv / 二手房小区-*.csv 生成 parquet(缺失或过期才建)，并输出城市索引。
可重复运行(断点续):已最新的跳过。"""
import csv, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "Vault" / "2026新楼盘"
import pandas as pd

def fresh(csvp, pqp):
    return pqp.exists() and pqp.stat().st_mtime >= csvp.stat().st_mtime

def main():
    files = sorted(list(D.glob("新楼盘-*.csv")) + list(D.glob("二手房小区-*.csv")))
    todo = [p for p in files if not fresh(p, p.with_suffix(".parquet"))]
    print("总文件 %d，待建 parquet %d" % (len(files), len(todo)))
    t0 = time.time(); done = 0
    for p in todo:
        if time.time() - t0 > 40:   # 留余量给 45s 限制，未完下次续
            print("  时间到，已建 %d，剩 %d，请再运行一次续建" % (done, len(todo) - done)); break
        try:
            pd.read_csv(p, dtype=str, keep_default_na=False).to_parquet(p.with_suffix(".parquet"), index=False)
            done += 1
        except Exception as e:
            print("  [warn] %s: %s" % (p.name, e))
    print("本轮新建 parquet:", done, "| 剩余:", len(todo) - done)

if __name__ == "__main__":
    main()
