# ============================================================================
# LSHIndexer 测试用例
# ============================================================================
# 使用方法:
#   python -m tests.preprocessing.test_lsh_index
#   或
#   pytest tests/preprocessing/test_lsh_index.py -v
# ============================================================================


import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.preprocessing.lsh_index import LSHIndexer


class TestLSHIndexerUnit(unittest.TestCase):
    """LSHIndexer 单元测试（不需要数据库）"""

    def test_01_create_minhash(self):
        """测试 1: 创建 MinHash"""
        print("\n" + "-"*40)
        print("[TEST] 测试创建 MinHash")
        print("-"*40)

        mh = LSHIndexer.create_minhash("Hamilton", signature_size=128, n_gram=3)
        self.assertIsNotNone(mh)
        print(f"[PASS] MinHash 创建成功，签名长度：{mh.num_perm}")

    def test_02_jaccard_similarity_same(self):
        """测试 2: 相同值的 Jaccard 相似度"""
        print("\n" + "-"*40)
        print("[TEST] 测试相同值相似度")
        print("-"*40)

        mh1 = LSHIndexer.create_minhash("Hamilton", 128, 3)
        mh2 = LSHIndexer.create_minhash("Hamilton", 128, 3)
        sim = LSHIndexer.jaccard_similarity(mh1, mh2)

        self.assertGreater(sim, 0.9, "相同值应该高度相似")
        print(f"[PASS] 相同值相似度：{sim:.4f}")

    def test_03_jaccard_similarity_similar(self):
        """测试 3: 相似值的 Jaccard 相似度"""
        print("\n" + "-"*40)
        print("[TEST] 测试相似值相似度")
        print("-"*40)

        mh1 = LSHIndexer.create_minhash("Hamilton", 128, 3)
        mh2 = LSHIndexer.create_minhash("Hamilt", 128, 3)
        sim = LSHIndexer.jaccard_similarity(mh1, mh2)

        self.assertGreater(sim, 0.3, "相似值应该有一定相似度")
        print(f"[PASS] 'Hamilton' vs 'Hamilt' 相似度：{sim:.4f}")

    def test_04_jaccard_similarity_different(self):
        """测试 4: 不同值的 Jaccard 相似度"""
        print("\n" + "-"*40)
        print("[TEST] 测试不同值相似度")
        print("-"*40)

        mh1 = LSHIndexer.create_minhash("Hamilton", 128, 3)
        mh2 = LSHIndexer.create_minhash("xyz123", 128, 3)
        sim = LSHIndexer.jaccard_similarity(mh1, mh2)

        self.assertLess(sim, 0.5, "不同值应该相似度较低")
        print(f"[PASS] 'Hamilton' vs 'xyz123' 相似度：{sim:.4f}")

    def test_05_build_and_query_index(self):
        """测试 5: 构建索引并查询"""
        print("\n" + "-"*40)
        print("[TEST] 测试构建索引和查询")
        print("-"*40)

        # 模拟数据
        unique_values = {
            "drivers": {
                "forename": ["Lewis", "Max", "Sebastian", "Fernando", "Charles"],
                "surname": ["Hamilton", "Verstappen", "Vettel", "Alonso", "Leclerc"]
            },
            "circuits": {
                "country": ["UK", "Monaco", "Germany", "Spain", "Italy", "Brazil"]
            }
        }

        indexer = LSHIndexer(signature_size=128, threshold=0.3)
        lsh, minhashes = indexer.build_index(unique_values, verbose=False)

        self.assertIsNotNone(lsh)
        self.assertEqual(len(minhashes), 16)  # 5 forename + 5 surname + 6 country

        # 查询
        results = indexer.query(lsh, minhashes, "Hamilton", top_k=5)

        print(f"[INFO] 查询 'Hamilton' 结果：{results}")
        self.assertIsInstance(results, dict)

        # Hamilton 应该匹配到 drivers.surname
        if "drivers" in results and "surname" in results["drivers"]:
            self.assertIn("Hamilton", results["drivers"]["surname"])
            print(f"[PASS] 'Hamilton' 匹配到 drivers.surname")

        print(f"[PASS] 索引构建和查询成功")


class TestLSHIndexerIntegration(unittest.TestCase):
    """LSHIndexer 集成测试（使用真实数据库）"""

    db_directory = None

    @classmethod
    def setUpClass(cls):
        base_dir = Path(__file__).parent.parent.parent / "data"
        # 找第一个有 .sqlite 文件的目录
        for item in base_dir.iterdir():
            if item.is_dir() and list(item.glob("*.sqlite")):
                cls.db_directory = item
                break

        if cls.db_directory is None:
            raise RuntimeError("未找到数据库目录")

        print(f"\n[INFO] 使用测试数据库：{cls.db_directory.name}")

    def test_06_get_unique_values(self):
        """测试 6: 从数据库提取唯一值"""
        print("\n" + "-"*40)
        print("[TEST] 测试提取唯一值")
        print("-"*40)

        db_file = list(self.db_directory.glob("*.sqlite"))[0]
        unique_values = LSHIndexer.get_unique_values(str(db_file))

        print(f"[INFO] 提取到 {len(unique_values)} 张表的唯一值:")
        total = 0
        for table, columns in unique_values.items():
            col_count = {col: len(vals) for col, vals in columns.items()}
            print(f"  - {table}: {col_count}")
            total += sum(len(v) for v in columns.values())

        print(f"[INFO] 总唯一值数：{total}")

        self.assertIsInstance(unique_values, dict)

        if len(unique_values) > 0:
            print("[PASS] 唯一值提取成功")
        else:
            print("[PASS] 唯一值提取完成（可能无 TEXT 列）")

    def test_07_build_db_lsh(self):
        """测试 7: 构建数据库 LSH 索引"""
        print("\n" + "-"*40)
        print("[TEST] 测试构建数据库 LSH 索引")
        print("-"*40)

        indexer = LSHIndexer(signature_size=128, threshold=0.5)
        indexer.build_db_lsh(str(self.db_directory), verbose=False)

        # 验证文件是否生成
        self.assertTrue(LSHIndexer.is_lsh_built(str(self.db_directory)))
        print("[PASS] LSH 索引构建并保存成功")

    def test_08_load_and_query(self):
        """测试 8: 加载索引并查询"""
        print("\n" + "-"*40)
        print("[TEST] 测试加载索引和查询")
        print("-"*40)

        # 先确保索引存在
        if not LSHIndexer.is_lsh_built(str(self.db_directory)):
            indexer = LSHIndexer(signature_size=128, threshold=0.5)
            indexer.build_db_lsh(str(self.db_directory), verbose=False)

        lsh, minhashes = LSHIndexer.load_db_lsh(str(self.db_directory))

        self.assertIsNotNone(lsh)
        self.assertTrue(len(minhashes) > 0)
        print(f"[INFO] 加载了 {len(minhashes)} 个 MinHash 条目")

        # 使用数据库中的某个值进行查询
        # 先从 minhashes 中取一个已知值
        sample_key = list(minhashes.keys())[0]
        _, table_name, column_name, sample_value = minhashes[sample_key]

        print(f"[INFO] 用已知值查询：'{sample_value}' (来自 {table_name}.{column_name})")

        indexer = LSHIndexer(signature_size=128, threshold=0.5)
        results = indexer.query(lsh, minhashes, sample_value, top_k=5)

        print(f"[INFO] 查询结果：")
        for tbl, cols in results.items():
            for col, vals in cols.items():
                print(f"  - {tbl}.{col}: {vals[:3]}")

        self.assertIsInstance(results, dict)
        print("[PASS] 加载和查询成功")

    def test_09_is_lsh_built(self):
        """测试 9: 检查索引是否存在"""
        print("\n" + "-"*40)
        print("[TEST] 测试检查索引是否存在")
        print("-"*40)

        # 真实数据库（应该已构建）
        result = LSHIndexer.is_lsh_built(str(self.db_directory))
        self.assertTrue(result, "应该已构建")
        print("[PASS] 索引存在检查正确")

        # 不存在的路径
        result = LSHIndexer.is_lsh_built("/nonexistent/path")
        self.assertFalse(result, "不存在的路径应该返回 False")
        print("[PASS] 不存在路径检查正确")


def run_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("LSHIndexer 测试套件")
    print("="*60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 先跑单元测试，再跑集成测试
    suite.addTests(loader.loadTestsFromTestCase(TestLSHIndexerUnit))
    suite.addTests(loader.loadTestsFromTestCase(TestLSHIndexerIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
