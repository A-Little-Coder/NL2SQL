"""快速检查 ChromaDB 中向量索引的实际内容"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.preprocessing.vector_store import VectorStoreManager

vs = VectorStoreManager(
    collection_name="nl2sql_columns",
    persist_directory=str(Path(__file__).parent.parent / "data" / "preprocessed" / "chroma"),
)

# 取 satscores 表的所有数据
collection = vs.collection
results = collection.get(
    where={"table_name": "satscores"},
    include=["documents", "metadatas"],
)

print(f"共 {len(results['ids'])} 条\n")
for i, doc_id in enumerate(results["ids"]):
    print(f"--- [{i}] id: {doc_id} ---")
    print(f"  document: {results['documents'][i]}")
    meta = results["metadatas"][i]
    for k, v in meta.items():
        print(f"  {k}: {repr(v)}")
    print()
