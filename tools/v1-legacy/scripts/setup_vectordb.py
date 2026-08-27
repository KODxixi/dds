"""
脚本: setup_vectordb.py (已切换至 Gavis LanceDB)
功能: 验证 Gavis 统一索引中枢和 embedding 模型可用。
不再独立创建 ChromaDB —— DDS 使用 Gavis_Core/index/lancedb/。
"""
import os
import sys
from pathlib import Path

# 指向 Gavis 统一索引器
GAVIS_CORE = Path(os.path.expanduser("~")) / "Gavis" / "gavis-core"
if str(GAVIS_CORE) not in sys.path:
    sys.path.insert(0, str(GAVIS_CORE))

from unified_indexer import get_indexer, ALL_COLLECTIONS

# 模型目录（保留用于离线检测）
MODEL_DIR = r"D:\Vault-assets\AI_Projects\DDS\models\bge-m3"


def verify_embedding():
    """测试 BGE-M3 模型推理及输出维度"""
    print("\n=== 验证 Embedding 模型 ===")

    idx = get_indexer()
    test_text = "找三亚海棠区近两年容积率2.0以上的住宅用地"

    print(f"[*] 正在对测试文本进行编码: '{test_text}'")
    try:
        vector = idx.embed_query_text(test_text)
        dim = len(vector)
        print(f"[+] 编码成功。测试向量维度为: {dim}")

        if dim == 1024:
            print("\n[✓] 验证通过: BGE-M3 维度符合预期（1024）。")
            return True
        else:
            print(f"\n[⚠] 警告: 维度为 {dim}，预期应为 1024。")
            return False
    except Exception as e:
        print(f"[!] 模型推理测试失败: {e}")
        return False


def main():
    print(f"\n=== DDS 向量数据库初始化（Gavis LanceDB）===")

    idx = get_indexer()
    print(f"[+] LanceDB 连接成功: {idx.persist_dir}")

    # 显示 DDS 相关表状态
    print("\n[*] DDS 表状态:")
    for name, spec in ALL_COLLECTIONS.items():
        if spec.source == "dds":
            try:
                tbl = idx._get_table(spec)
                n = tbl.count_rows()
                print(f"  - [OK] {name}: {n} 条记录")
            except Exception:
                print(f"  - [--] {name}: 待创建（首次入库时自动初始化）")

    success = verify_embedding()

    if success:
        print("\n========================================")
        print("✨ DDS LanceDB 环境初始化全部完成！")
        print("   统一中枢: Gavis_Core/index/lancedb/")
        print("========================================\n")
    else:
        print("\n[!] 数据库已连接，但模型验证未通过，请检查 BGE-M3 缓存。")


if __name__ == "__main__":
    main()
