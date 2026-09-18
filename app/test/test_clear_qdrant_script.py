"""清空脚本的行为测试：重点是「不加 --yes 绝不删数据」这条安全约束。

用假客户端跑，不会连真实 Qdrant。
运行：python -m app.test.test_clear_qdrant_script
"""
from __future__ import annotations

import app.tool.clear_qdrant as script
from app.conf.qdrant_config import qdrant_config

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


class FakeCollection:
    def __init__(self, points: int):
        self.points_count = points
        self.payload_schema = {}


class FakeClient:
    """极简 Qdrant 替身：只需要脚本用到的那几个方法。"""

    def __init__(self, points: int = 5):
        self.store = {qdrant_config.chunks_collection: FakeCollection(points)}
        self.deleted_points = 0
        self.dropped: list[str] = []
        self.deleted_all_calls = 0

    def collection_exists(self, name):
        return name in self.store

    def get_collection(self, name):
        return self.store[name]

    def get_collections(self):
        return type("R", (), {"collections": [type("C", (), {"name": n})() for n in self.store]})()

    def delete(self, collection_name, points_selector, **kwargs):
        # 模拟「空 filter 匹配全部点」
        self.deleted_all_calls += 1
        self.deleted_points += self.store[collection_name].points_count
        self.store[collection_name].points_count = 0

    def delete_collection(self, name):
        self.dropped.append(name)
        self.store.pop(name, None)

    def scroll(self, **kwargs):
        return [], None


def install(points=5) -> FakeClient:
    fake = FakeClient(points)
    script.get_qdrant_client = lambda: fake
    return fake


def test_preview_does_not_delete():
    """核心安全约束：预览模式绝不能删数据。"""
    fake = install()
    code = script.main(["--target", qdrant_config.chunks_collection])
    check("预览模式返回 0", code, 0)
    check("预览模式没有调用 delete", fake.deleted_all_calls, 0)
    check("预览模式点数不变", fake.store[qdrant_config.chunks_collection].points_count, 5)


def test_yes_deletes_points_only():
    """--yes 且默认 points 模式：只清点，不删 collection。"""
    fake = install()
    code = script.main(["--target", qdrant_config.chunks_collection, "--yes"])
    check("执行返回 0", code, 0)
    check("点数清零", fake.store[qdrant_config.chunks_collection].points_count, 0)
    check("collection 仍然存在", fake.dropped, [])


def test_mode_collection_drops_collection():
    """--mode collection：直接删表。"""
    fake = install()
    code = script.main(["--target", qdrant_config.chunks_collection, "--mode", "collection", "--yes"])
    check("执行返回 0", code, 0)
    check("collection 被删除", fake.dropped, [qdrant_config.chunks_collection])


def test_all_flag_targets_item_name_too():
    """--all 会同时处理 chunks 和 item_name。"""
    fake = install()
    fake.store[qdrant_config.item_name_collection] = FakeCollection(1)
    code = script.main(["--all", "--yes"])
    check("执行返回 0", code, 0)
    check("两个 collection 都清空",
          (fake.store[qdrant_config.chunks_collection].points_count,
           fake.store[qdrant_config.item_name_collection].points_count), (0, 0))


def test_missing_collection_is_skipped():
    """collection 不存在时不报错，直接跳过。"""
    fake = install()
    code = script.main(["--target", "不存在的库", "--yes"])
    check("跳过时返回 0", code, 0)
    check("没有误删其他库", fake.store[qdrant_config.chunks_collection].points_count, 5)


def main():
    for test in (
        test_preview_does_not_delete,
        test_yes_deletes_points_only,
        test_mode_collection_drops_collection,
        test_all_flag_targets_item_name_too,
        test_missing_collection_is_skipped,
    ):
        test()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
