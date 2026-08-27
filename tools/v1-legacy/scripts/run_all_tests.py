# -*- coding: utf-8 -*-
"""
DDS 自动化全量回归测试调度中心 (run_all_tests.py)

功能：
1. 一键顺序调度并验证所有测试套件（pytest、端到端真实地块 e2e、时空穿越 abm 决策）
2. 捕获各套件的输出与退出状态，给出高颜值的中文字幕汇总报告
3. 支持 --dry-run 参数，只打印将要运行的套件而不实际执行
4. 支持 --suite-timeout-seconds 参数，为每个套件设置独立硬超时
5. 支持在任何套件失败时立即以非零状态码退出
"""

import sys
import subprocess
import time
import argparse
import math


DEFAULT_SUITE_TIMEOUT_SECONDS = 600.0


def _positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return seconds


def main():
    parser = argparse.ArgumentParser(description="DDS 自动化全量回归测试调度中心")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示将要运行的测试命令与套件信息，不实际执行"
    )
    parser.add_argument(
        "--suite-timeout-seconds",
        type=_positive_seconds,
        default=DEFAULT_SUITE_TIMEOUT_SECONDS,
        help=f"单个测试套件的硬超时秒数（默认: {DEFAULT_SUITE_TIMEOUT_SECONDS:g}）",
    )
    args = parser.parse_args()

    suites = [
        {
            "name": "pytest Flask API 冒烟测试集",
            "command": [sys.executable, "-m", "pytest", "test_smoke.py"],
            "desc": "验证 Web 服务的核心路由、输入校验、流式对话 API 及 HTML 核心字段"
        },
        {
            "name": "pytest ABM 核心算法单元测试集",
            "command": [sys.executable, "-m", "pytest", "tests/test_abm.py"],
            "desc": "验证蒙特卡洛 ABM 引擎、MNL 随机效用客群选择、Newton-Raphson 财务及 IRR 精算核心"
        },
        {
            "name": "端到端三亚真实地块投拓决策验证",
            "command": [sys.executable, "tests/test_e2e_real_parcel.py"],
            "desc": "基于真实三亚海棠湾地块，执行 ABM 推演、配比优化、5年迁移、CEO 联动并导出报告全流程"
        },
        {
            "name": "时空穿越与物理直读高保真验证",
            "command": [sys.executable, "scripts/test_time_travel_abm.py"],
            "desc": "验证从物理 Vault 目录加载历史/现代 Persona 样本的高保真还原与多年代隔离"
        }
    ]

    print("=" * 70)
    print("           DDS 核心三角全量回归测试自动化调度中心            ")
    print("=" * 70)

    if args.dry_run:
        print("[!] 提示：当前正处于 Dry-Run 模式，仅输出将执行的测试套件信息：\n")
        for i, suite in enumerate(suites, 1):
            print(f"[套件 {i}] {suite['name']}")
            print(f"  - 描述: {suite['desc']}")
            print(f"  - 命令: {' '.join(suite['command'])}")
            print("-" * 50)
        print("\n[OK] Dry-Run 验证完毕，所有测试路径配置正确。")
        sys.exit(0)

    total_t0 = time.time()
    results = []
    has_failed = False

    for i, suite in enumerate(suites, 1):
        print(f"\n[*] 正在运行套件 [{i}/{len(suites)}]: {suite['name']}")
        print(f"  * 描述: {suite['desc']}")
        print(f"  * 命令: {' '.join(suite['command'])}")
        print("-" * 50)
        
        t0 = time.time()
        try:
            import os
            my_env = os.environ.copy()
            # 将多线程计算库强制单线程化，降低 Windows 平台并行 runtime 冲突概率。
            my_env["OMP_NUM_THREADS"] = "1"
            my_env["MKL_NUM_THREADS"] = "1"
            my_env["OPENBLAS_NUM_THREADS"] = "1"
            my_env["VECLIB_MAXIMUM_THREADS"] = "1"
            my_env["NUMEXPR_NUM_THREADS"] = "1"
            res = subprocess.run(
                suite['command'],
                check=False,
                env=my_env,
                timeout=args.suite_timeout_seconds,
            )
            elapsed = time.time() - t0
            
            if res.returncode == 0:
                print(f"\n[PASS] 成功：{suite['name']} 完美通过！耗时: {elapsed:.2f}s")
                results.append({"name": suite['name'], "status": "PASSED", "duration": elapsed})
            else:
                print(f"\n[FAIL] 失败：{suite['name']} 执行异常，退出码: {res.returncode}")
                results.append({"name": suite['name'], "status": "FAILED", "duration": elapsed})
                has_failed = True
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            print(
                f"\n[TIMEOUT] 超时：{suite['name']} 超过 "
                f"{args.suite_timeout_seconds:.2f}s 未完成"
            )
            results.append({"name": suite['name'], "status": "TIMEOUT", "duration": elapsed})
            has_failed = True
        except Exception as e:
            elapsed = time.time() - t0
            print(f"\n[ERROR] 错误：运行测试套件时发生异常: {e}")
            results.append({"name": suite['name'], "status": "ERROR", "duration": elapsed})
            has_failed = True

    total_elapsed = time.time() - total_t0
    print("\n" + "=" * 70)
    print("                    全量测试回归报告汇总                    ")
    print("=" * 70)
    
    for r in results:
        status_icon = "PASSED" if r["status"] == "PASSED" else r["status"]
        print(f" - {r['name']:<30} | 状态: {status_icon:<10} | 耗时: {r['duration']:.2f}s")
        
    print("-" * 70)
    print(f" - 总累计执行时间: {total_elapsed:.2f}s")
    
    if has_failed:
        print("\n[WARNING] 警告：有测试套件未能完全通过！请立即检查上方错误日志进行修复。")
        print("=" * 70)
        sys.exit(1)
    else:
        print("\n[CONGRATULATIONS] 恭喜！全量测试 100% 绿灯通过，系统安全稳定，符合上线标准！")
        print("=" * 70)
        sys.exit(0)

if __name__ == "__main__":
    main()
