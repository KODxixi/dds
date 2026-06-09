# -*- coding: utf-8 -*-
"""
DDS CSV 物理一键 Parquet 列存转换工具 (convert_csv_to_parquet.py)

功能：
1. 遍历 Vault 目录下各年份分桶文件夹中的所有 CSV 文件
2. 零外部依赖，使用 DuckDB 极速将 CSV 镜像物理转换为同名 .parquet 列式存储文件
3. 支持 --dry-run 开关以打印转换计划而不执行实际落盘
4. 100% 兼容 Windows GBK 终端
"""

import sys
import os
import time
import argparse
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("[-] 错误：缺少 duckdb 依赖。运行：pip install duckdb")
    sys.exit(1)

VAULT_DIR = Path(__file__).resolve().parent.parent / "Vault"

def main():
    parser = argparse.ArgumentParser(description="DDS CSV 一键 Parquet 物理列式存储转换器")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示将要转换的 CSV 与 Parquet 文件路径，不实际执行"
    )
    args = parser.parse_args()

    if not VAULT_DIR.exists():
        print(f"[-] 错误：数据源目录不存在: {VAULT_DIR}")
        sys.exit(1)

    print("=" * 70)
    print("            DDS CSV 物理一键 Parquet 列式存储转换器            ")
    print("=" * 70)

    csv_files = []
    # 递归遍历 Vault 下所有子目录，搜寻所有 CSV 文件
    for root, dirs, files in os.walk(VAULT_DIR):
        for f in files:
            if f.endswith(".csv"):
                csv_path = Path(root) / f
                parquet_path = csv_path.with_suffix(".parquet")
                csv_files.append((csv_path, parquet_path))

    if not csv_files:
        print("[!] 提示：未搜寻到任何 CSV 文件。")
        sys.exit(0)

    print(f"[*] 共搜寻到 {len(csv_files)} 个 CSV 物理文件。")
    if args.dry_run:
        print("[!] 提示：当前正处于 Dry-Run 模式，仅输出转换对齐计划：\n")
        for i, (csv_p, par_p) in enumerate(csv_files, 1):
            print(f"  [{i}] CSV: {csv_p.relative_to(VAULT_DIR.parent)}")
            print(f"      -> PARQUET: {par_p.relative_to(VAULT_DIR.parent)}")
            print("-" * 50)
        print("\n[OK] Dry-Run 预检成功，文件映射正确。")
        sys.exit(0)

    print("[*] 正在启动物理转换...")
    t0 = time.time()
    success_count = 0

    for i, (csv_p, par_p) in enumerate(csv_files, 1):
        rel_csv = csv_p.relative_to(VAULT_DIR.parent)
        print(f"[*] 正在转换 [{i}/{len(csv_files)}]: {rel_csv} ...")
        
        csv_str = str(csv_p).replace("\\", "/")
        par_str = str(par_p).replace("\\", "/")
        
        try:
            # 物理写入 Parquet 文件
            duckdb.execute(f"""
                COPY (
                    SELECT * FROM read_csv_auto('{csv_str}', header=true)
                ) TO '{par_str}' (FORMAT PARQUET)
            """)
            print(f"  [OK] 转换成功: {par_p.name} (大小: {par_p.stat().st_size} 字节)")
            success_count += 1
        except Exception as e:
            print(f"  [FAIL] 转换失败 {rel_csv}: {e}")

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("                    物理列存转换汇总报告                    ")
    print("=" * 70)
    print(f" - 成功镜像物理转换: {success_count} / {len(csv_files)}")
    print(f" - 累计物理转换耗时: {elapsed:.2f}s")
    
    if success_count == len(csv_files):
        print("\n[CONGRATULATIONS] 恭喜！全量 CSV 物理 Parquet 列存镜像转换完美通过！")
        print("=" * 70)
        sys.exit(0)
    else:
        print("\n[WARNING] 警告：部分 CSV 物理文件转换失败，请检查报错日志！")
        print("=" * 70)
        sys.exit(1)

if __name__ == "__main__":
    main()
