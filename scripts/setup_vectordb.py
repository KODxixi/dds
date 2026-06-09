"""
脚本: setup_vectordb.py
安装依赖: pip install chromadb sentence-transformers torch
功能: 初始化 ChromaDB，创建所有 Collection，验证 embedding 模型可用。
"""
import os
import sys
import json
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

# 配置常量
CHROMA_PATH = r"C:\Users\shiguanyu\DDS\vectordb"
MODEL_DIR = r"C:\Users\shiguanyu\DDS\models\bge-m3"
MODEL_NAME = "BAAI/bge-m3"

# 集合定义
COLLECTION_NAMES = [
    "land_parcels",
    "transactions",
    "competitor_projects",
    "pdf_extracts",
    "gis_context"
]

# 单例模式加载模型
_model_instance = None

def get_model():
    """获取或下载 Embedding 模型，单例保证整个进程仅加载一次"""
    global _model_instance
    if _model_instance is None:
        print(f"[*] 正在准备加载模型 {MODEL_NAME}...")
        
        if not os.path.exists(MODEL_DIR):
            print(f"[*] 目录未检测到模型，将自动下载并缓存至: {MODEL_DIR}")
            os.makedirs(MODEL_DIR, exist_ok=True)
        
        try:
            # 加载模型，并限制序列长度以符合 prompt 截断要求
            _model_instance = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_DIR)
            _model_instance.max_seq_length = 512
            print("[+] 模型加载成功！")
        except Exception as e:
            print(f"[!] 加载模型失败: {e}")
            sys.exit(1)
            
    return _model_instance

def setup_database():
    """初始化 ChromaDB 持久化存储及所有 Collection"""
    print(f"\n=== DDS 向量数据库初始化 ===")
    
    # 确保父级路径存在
    if not os.path.exists(CHROMA_PATH):
        os.makedirs(CHROMA_PATH, exist_ok=True)
        print(f"[*] 已创建持久化目录: {CHROMA_PATH}")
        
    # 初始化 PersistentClient
    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        print("[+] ChromaDB PersistentClient 连接成功。")
    except Exception as e:
        print(f"[!] 无法初始化 ChromaDB 客户端: {e}")
        return None

    print("\n[*] 开始检查/创建 Collection...")
    for name in COLLECTION_NAMES:
        try:
            # get_or_create_collection 确保幂等性，设置度量为 cosine 适合向量语义匹配
            coll = client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"}
            )
            doc_count = coll.count()
            print(f"  - [OK] Collection '{name}' 已就绪。现有记录数: {doc_count}")
        except Exception as e:
            print(f"  - [FAIL] 创建 Collection '{name}' 失败: {e}")
            
    return client

def verify_embedding():
    """测试模型推理及输出维度"""
    print("\n=== 验证 Embedding 模型 ===")
    
    model = get_model()
    test_text = "找三亚海棠区近两年容积率2.0以上的住宅用地"
    
    print(f"[*] 正在对测试文本进行编码: '{test_text}'")
    try:
        vector = model.encode(test_text, normalize_embeddings=True)
        dim = vector.shape[0]
        print(f"[+] 编码成功。测试向量维度为: {dim}")
        
        if dim == 1024:
            print("\n[✓] 验证通过: BGE-M3 维度符合预期（1024）。")
            return True
        else:
            print(f"\n[⚠] 警告: 维度为 {dim}，预期应为 1024。请核对模型版本。")
            return False
    except Exception as e:
        print(f"[!] 模型推理测试失败: {e}")
        return False

def main():
    # 1. 创建数据库和集合
    client = setup_database()
    if not client:
        sys.exit(1)
        
    # 2. 验证模型
    success = verify_embedding()
    
    if success:
        print("\n========================================")
        print("✨ DDS Vector DB 环境初始化全部完成！")
        print("========================================\n")
    else:
        print("\n[!] 数据库已创建，但模型验证未通过，请检查资源。")

if __name__ == "__main__":
    main()
