"""Main Agent 的节点。

这里刻意不做 `from ... import node_xxx` 的再导出：
包内模块名与函数名同名（node_answer.py / node_answer），
再导出会让 `import app.agent.nodes.node_answer as m` 拿到函数而不是模块，
给打桩和调试埋坑。需要函数请从具体模块导入。
"""
