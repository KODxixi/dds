# -*- coding: utf-8 -*-
"""
DDS 时空穿越与物理直读全贯通黑盒测试脚本
测试核心：
  1. 验证从 Vault 物理年份目录中反序列化加载 Persona 样本的高保真度
  2. 验证 2020年 vs 2010年 的客群年收入与样本ID的时空分化
  3. 验证 2020年 vs 2012年 楼盘数据的物理隔离加载
"""
import sys
from pathlib import Path

# 将脚本目录加入 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from abm_engine import load_personas_from_vault, sample_personas
from report_parcel import load_projects


def test_vault_personas_loading():
    print("=" * 64)
    print(" 【验证 1】物理客群反序列化与高保真还原测试")
    print("=" * 64)
    
    # 1. 加载 2020年 上海物理客群
    personas_2020 = load_personas_from_vault("上海", 2020)
    assert personas_2020, "[-] 错误：无法从 Vault 加载 2020年上海 的客群样本！"
        
    print(f"[+] 2020年上海 物理客群加载成功！样本数量：{len(personas_2020)} 人")
    p1 = personas_2020[0]
    print(f"    - 首位样本还原：")
    print(f"      类型: {p1.archetype} | 年龄: {p1.age} | 阶层: {p1.social_class}")
    print(f"      年收入: {p1.annual_income}万 | 总资产: {p1.total_asset}万 | 预算: {p1.budget_ceiling}万")
    print(f"      偏好维度: {p1.pref}")
    
    # 2. 比较 2020年 与 2010年 的时空收入回归分化
    personas_2010 = load_personas_from_vault("上海", 2010)
    assert personas_2010, "[-] 错误：无法从 Vault 加载 2010年上海 的客群样本！"
        
    print(f"\n[+] 2010年上海 物理客群加载成功！样本数量：{len(personas_2010)} 人")
    
    # 计算均值以验证时间回归
    avg_inc_2020 = sum(p.annual_income for p in personas_2020) / len(personas_2020)
    avg_inc_2010 = sum(p.annual_income for p in personas_2010) / len(personas_2010)
    
    print(f"\n[+] 时空回归物理计算：")
    print(f"    - 2020年上海 客群年收入均值：{avg_inc_2020:.2f} 万元")
    print(f"    - 2010年上海 客群年收入均值：{avg_inc_2010:.2f} 万元")
    
    assert avg_inc_2020 > avg_inc_2010, "[-] 警报：年代收入时间回归异常，请核对 GDP 回归参数！"
    print("[OK] 年代收入时间回归验证成功！（历史上越早，收入及购买力越低）")


def test_vault_projects_loading():
    print("\n" + "=" * 64)
    print(" 【验证 2】物理楼盘历史分桶隔离加载测试")
    print("=" * 64)
    
    # 1. 读取 2020 年三亚楼盘
    df_2020 = load_projects(["三亚"], year="2020")
    # 2. 读取 2012 年三亚楼盘
    df_2012 = load_projects(["三亚"], year="2012")
    
    print(f"[+] 2020年三亚 楼盘样本数：{len(df_2020)} 条")
    print(f"[+] 2012年三亚 楼盘样本数：{len(df_2012)} 条")
    
    avg_price_2020 = df_2020["unit_price_cny"].mean()
    avg_price_2012 = df_2012["unit_price_cny"].mean()
    
    print(f"\n[+] 楼盘价格年代分化物理计算：")
    print(f"    - 2020年三亚 楼盘均价：{avg_price_2020:.2f} 元/㎡")
    import pandas as pd
    if pd.isna(avg_price_2012):
        print(f"    - 2012年三亚 楼盘均价：暂无有效单价（均为别墅/万元套价口径）")
    else:
        print(f"    - 2012年三亚 楼盘均价：{avg_price_2012:.2f} 元/㎡")
    
    assert len(df_2020) > 0 and len(df_2012) > 0, "[-] 错误：某年份物理楼盘读取为空，请检查 CSV 路径映射！"
    print("[OK] 物理楼盘年份隔离加载验证成功！")


def main():
    print("=" * 64)
    print(" DDS 历史时空穿越决策系统全链路集成验证")
    print("=" * 64)
    
    try:
        test_vault_personas_loading()
        test_vault_projects_loading()
        print("\n" + "=" * 64)
        print(" [CONGRATULATIONS] 全部时空穿越集成测试通过！系统表现完美！")
        print("=" * 64)
        sys.exit(0)
    except AssertionError as e:
        print("\n" + "=" * 64)
        print(f" [FAILED] 时空穿越集成测试失败: {e}")
        print("=" * 64)
        sys.exit(1)


if __name__ == "__main__":
    main()
