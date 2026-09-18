"""清空 Qdrant collection 的运维脚本（重导数据前使用）。

为什么要清空：导入的幂等删除是「按 item_name 匹配」的，
而现有数据的 item_name 有一部分是早期大模型识别出来的长串关键词，
和现在的文件名对不上，直接重导会留下旧数据 → 同一个文档在库里存两份。

安全设计：默认是**预览模式**，只打印现状不删任何东西；
必须显式加 --yes 才会真正执行。

用法：
    # 1) 先看现状（不删任何东西）
    python -m app.tool.clear_qdrant

    # 2) 确认无误后真正执行：清空 kb_chunks 的数据点，保留 collection 和索引
    python -m app.tool.clear_qdrant --yes

    # 3) 连 kb_item_name 一起清
    python -m app.tool.clear_qdrant --all --yes

    # 4) 连 collection 本身一起删（下次导入时自动重建，但索引需要重新创建）
    python -m app.tool.clear_qdrant --mode collection --yes

    # 5) 只看有哪些 collection
    python -m app.tool.clear_qdrant --list
"""
from __future__ import annotations

import argparse
import sys

from qdrant_client import models

from app.conf.qdrant_config import qdrant_config
from app.utils.qdrant_utils import get_qdrant_client

# 默认只处理主检索库；kb_item_name 需要 --all 才会一起清
_BATCH_SIZE = 1000


def _targets(args) -> list[str]:
    """解析要清理的 collection 列表。"""
    if args.all:
        return [qdrant_config.chunks_collection, qdrant_config.item_name_collection]
    if args.target:
        return args.target
    return [qdrant_config.chunks_collection]


def _describe(client, name: str) -> tuple[int, list[str]] | None:
    """返回 (点数, 已有索引字段)；collection 不存在时返回 None。"""
    if not client.collection_exists(name):
        return None
    info = client.get_collection(name)
    return info.points_count or 0, sorted((info.payload_schema or {}).keys())


def _delete_all_points(client, name: str, batch_size: int = _BATCH_SIZE) -> int:
    """删除 collection 里的全部数据点，返回实际删除数量。

    先用空 filter 一把删（Qdrant 里空 filter 表示匹配全部点），
    删完再看点数；如果没删干净，退回「翻页拿 id 再按 id 删」的方式兜底，
    避免因为过滤语义理解偏差而误以为已经清空了。
    """
    client.delete(
        collection_name=name,
        points_selector=models.FilterSelector(filter=models.Filter(must=[])),
        wait=True,
    )
    remaining = client.get_collection(name).points_count or 0
    if remaining == 0:
        return 0

    print(f"  [兜底] 仍有 {remaining} 个点，改用逐批按 id 删除…")
    removed = 0
    while True:
        records, _ = client.scroll(
            collection_name=name,
            limit=batch_size,
            with_payload=False,
            with_vectors=False,
        )
        if not records:
            break
        ids = [record.id for record in records]
        client.delete(collection_name=name, points_selector=ids, wait=True)
        removed += len(ids)
        print(f"  [兜底] 已删除 {removed} 个点")
        if len(ids) < batch_size:
            break
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清空 Qdrant collection（默认预览模式，加 --yes 才真正执行）",
    )
    parser.add_argument("--target", action="append", metavar="COLLECTION",
                        help="指定要清理的 collection，可重复传；默认取配置里的 chunks collection")
    parser.add_argument("--all", action="store_true",
                        help="清理 chunks 和 item_name 两个 collection")
    parser.add_argument("--mode", choices=("points", "collection"), default="points",
                        help="points=只删数据点(默认，保留索引)；collection=连 collection 一起删")
    parser.add_argument("--yes", action="store_true",
                        help="真正执行删除；不加这个参数只做预览")
    parser.add_argument("--list", action="store_true",
                        help="只列出所有 collection 和点数")
    parser.add_argument("--batch-size", type=int, default=_BATCH_SIZE,
                        help="兜底逐批删除时每批的大小，默认 1000")
    args = parser.parse_args(argv)

    client = get_qdrant_client()

    # 只列出所有 collection 的场合
    if args.list:
        all_collections = client.get_collections().collections
        print("当前 Qdrant 里的 collection：")
        for item in all_collections:
            detail = _describe(client, item.name) or (0, [])
            print(f"  - {item.name}: {detail[0]} 个点，索引 {detail[1] or '无'}")
        return 0

    targets = _targets(args)
    print(f"目标 collection：{targets}")
    print(f"执行模式：{'删除整个 collection' if args.mode == 'collection' else '只删数据点（保留 collection 与索引）'}")
    print(f"当前模式：{'执行删除' if args.yes else '预览（不会删任何数据）'}\n")

    # 1. 先打印现状，让执行者确认清理范围
    existing: list[str] = []
    for name in targets:
        detail = _describe(client, name)
        if detail is None:
            print(f"[{name}] 不存在，跳过")
            continue
        points, indexes = detail
        print(f"[{name}] 当前 {points} 个点，已有索引：{indexes or '无'}")
        existing.append(name)

    if not existing:
        print("\n没有需要处理的 collection。")
        return 0

    if not args.yes:
        print("\n以上为预览结果，未删除任何数据。")
        print("确认无误后，在命令末尾加上 --yes 重新执行即可：")
        print(f"    python -m app.tool.clear_qdrant {'--all' if args.all else '--target ' + ' '.join(targets)} "
              f"--mode {args.mode} --yes")
        return 0

    # 2. 真正执行
    print()
    failed = False
    for name in existing:
        before = _describe(client, name)
        before_points = before[0] if before else 0
        try:
            if args.mode == "collection":
                client.delete_collection(name)
                print(f"[{name}] 已删除整个 collection（原有 {before_points} 个点）；"
                      f"下次导入时会自动重建，索引也会重新创建")
            else:
                _delete_all_points(client, name, args.batch_size)
                after = _describe(client, name)
                after_points = after[0] if after else 0
                status = "已清空" if after_points == 0 else f"仍有 {after_points} 个点"
                print(f"[{name}] {status}（清理前 {before_points} 个点）")
                if after_points:
                    failed = True
        except Exception as e:  # noqa: BLE001 - 脚本层兜底，打印清楚比抛栈更有用
            failed = True
            print(f"[{name}] 清理失败：{type(e).__name__}: {e}")

    if failed:
        print("\n有 collection 未清理干净，可以改用 --mode collection 直接删表重建。")
        return 1

    print("\n全部完成。接下来可以重新运行导入流程，"
          "导入时会自动重建 collection（如果需要）并创建 payload 索引。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
