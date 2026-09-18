"""导入阶段「主体识别」开关的行为测试（不调用大模型、不连数据库）。

验证两件事：
1. 关闭时（默认）：不调用大模型、不写 kb_item_name，但 item_name 字段依然存在（文件名兜底）；
2. 打开时：恢复原来的完整流程（LLM 识别 + 向量化 + 写库）。

运行：python -m app.test.test_item_name_recognition_switch
"""
from __future__ import annotations

from dataclasses import replace

from app.conf.item_name_config import item_name_config
import app.rag.import_process.agent.nodes.node_item_name_recognition as node

passed = 0
failed: list[str] = []


def check(name: str, actual, expected):
    global passed
    if actual == expected:
        passed += 1
    else:
        failed.append(f"{name}\n     期望：{expected}\n     实际：{actual}")


def build_state():
    return {
        "task_id": "test_task",
        "file_title": "经营晨报 20260410",
        "file_id": "file-1",
        "chunks": [
            {"title": "## 重点要闻", "content": "协创数据拟申请800亿融资租赁额度"},
            {"title": "## 东华能源", "content": "签约百万吨石墨产业链"},
        ],
    }


def install_spies(enabled: bool):
    """把会产生外部调用的步骤替换成探针，只关心「有没有被调用」。"""
    calls = {"llm": 0, "embed": 0, "vector_db": 0}

    node.item_name_config = replace(item_name_config, enabled=enabled)
    node.step_3_call_llm = lambda context, file_title: (calls.__setitem__("llm", calls["llm"] + 1)
                                                       or "识别出的主体")
    node.step_5_generate_embeddings = lambda name: (calls.__setitem__("embed", calls["embed"] + 1)
                                                   or ([0.1], {1: 0.5}))
    node.step_6_save_to_vector_db = lambda *a, **k: calls.__setitem__("vector_db", calls["vector_db"] + 1)
    return calls


def test_disabled_by_default():
    """默认关闭：省掉大模型和向量库写入，但 item_name 必须兜底上。"""
    check("默认开关状态", item_name_config.enabled, False)

    calls = install_spies(enabled=False)
    state = node.node_item_name_recognition(build_state())

    check("关闭时不调用大模型", calls["llm"], 0)
    check("关闭时不生成向量", calls["embed"], 0)
    check("关闭时不写 kb_item_name", calls["vector_db"], 0)
    # 下游依赖 item_name：导入前按它删旧数据、写入时放进 payload
    check("用文件名兜底", state["item_name"], "经营晨报 20260410")
    check("每个 chunk 都带上 item_name",
          {c.get("item_name") for c in state["chunks"]}, {"经营晨报 20260410"})


def test_enabled_restores_full_flow():
    """打开开关时恢复原流程，代码路径没有被破坏。"""
    calls = install_spies(enabled=True)
    state = node.node_item_name_recognition(build_state())

    check("开启时调用大模型", calls["llm"], 1)
    check("开启时生成向量", calls["embed"], 1)
    check("开启时写 kb_item_name", calls["vector_db"], 1)
    check("使用识别结果", state["item_name"], "识别出的主体")
    check("chunk 使用识别结果",
          {c.get("item_name") for c in state["chunks"]}, {"识别出的主体"})


def test_query_side_ignores_item_name_filter_when_disabled():
    """联动：识别关闭时，查询侧不应该再拿 item_name 做过滤（必然匹配不到）。"""
    import app.rag.query_process.agent.nodes.node_item_name_confirm as confirm

    class FakeResponse:
        content = ('{"rewritten_query": "欣旺达相关信息",'
                   ' "filters": {"item_name": "欣旺达"}}')

    class FakeLLM:
        def invoke(self, messages):
            return FakeResponse()

    confirm.get_llm_client = lambda *a, **k: FakeLLM()

    # 关闭：item_name 过滤被丢掉，主体名仍在语义查询里
    confirm.item_name_config = replace(item_name_config, enabled=False)
    disabled = confirm.step_3_analyze_query("只看欣旺达", [])
    check("关闭时丢弃 item_name 过滤", disabled["filters"]["item_name"], None)
    check("关闭时主体仍在语义查询里", disabled["rewritten_query"], "欣旺达相关信息")

    # 打开：保留过滤条件
    confirm.item_name_config = replace(item_name_config, enabled=True)
    enabled = confirm.step_3_analyze_query("只看欣旺达", [])
    check("开启时保留 item_name 过滤", enabled["filters"]["item_name"], "欣旺达")


def main():
    test_disabled_by_default()
    test_enabled_restores_full_flow()
    test_query_side_ignores_item_name_filter_when_disabled()

    print(f"\n通过 {passed} 项，失败 {len(failed)} 项")
    for item in failed:
        print("  [FAIL]", item)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
