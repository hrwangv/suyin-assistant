"""RAG 评测目录。

设计约束：本目录**只读** app/ 下的业务代码，不修改任何现有流程逻辑。
所有检索 / 查询改写 / HyDE / RRF / Rerank / 回答生成都直接调用现有函数，
评测脚本只负责「编排 + 打分 + 出表」。

运行方式（必须在项目根目录，app.* 是绝对导入）：
    PYTHONPATH=. python -m evaluation.selftest        # 离线自检，不联网
    PYTHONPATH=. python -m evaluation.build_dataset   # 生成 100 条测试问题
    PYTHONPATH=. python -m evaluation.run_eval        # 跑评测
    PYTHONPATH=. python -m evaluation.ablation        # 跑完整消融阶梯出表
"""

__all__ = ["config", "dataset", "metrics", "retrieval", "answering", "judge", "report"]
