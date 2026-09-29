"""把业务原件（`templates/苏银金租备案材料.docx`）转成渲染模板。

为什么要这一步：原件里填的是**示例企业**（盐城市环保产业发展投资有限公司），
渲染前必须换成占位符，否则生成出来的申请书还是这家公司的信息。

用法（项目根目录）：
    PYTHONPATH=. python scripts/prepare_application_template.py

产物：`templates/business_application.docx`（原件不动，可反复重跑）。

替换规则：值类字段换成 {{ 占位符 }}；**选项类文本**（"有担保   无担保"、
"抵押质押保证其他"、"/" 空格线）和**签字栏**保持原样——那些是让人在打印件上勾选的，
不是数据。我们暂时不采集的字段（注册资本、企业性质、租赁物…）也换成占位符，
渲染时为空，签字前人工补；等确定要系统填，再加进 ApplicationData。

按业务确认：租赁物名称固定"设备"、租赁物状态固定"正常使用"（与原模板一致）；
企业性质 / 注册资本由 Agent 输出填（见 ApplicationData.enterprise_nature /
registered_capital）；原模板空着的字段（租赁物预估价值、融资用途、拟还款来源）
保持空着，不做替换。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document  # noqa: E402

from app.conf.agent_config import agent_config  # noqa: E402

SOURCE_FILE = "苏银金租备案材料.docx"
TARGET_FILE = "business_application.docx"

SAMPLE_COMPANY = "盐城市环保产业发展投资有限公司"
SAMPLE_CREDIT_CODE = "913209006921075291"
SAMPLE_ADDRESS = "江苏省盐城市亭湖区环保大道105号A、B、C、D、E幢（28）"
SAMPLE_DATE_CN = "2026年02月25日"
SAMPLE_MONTH = "2026.02"
SAMPLE_LEGAL_PERSON = "金楠"
SAMPLE_CAPITAL = "200000万元人民币"

# 顺序有讲究：先替换长的、具体的，再替换短的（避免 "2026.02" 先命中年份）
REPLACEMENTS = [
    (SAMPLE_COMPANY, "{{ company_name }}"),
    (SAMPLE_CREDIT_CODE, "{{ unified_social_credit_code }}"),
    (SAMPLE_ADDRESS, "{{ registered_address }}"),
    (SAMPLE_DATE_CN, "{{ application_date_cn }}"),
    (SAMPLE_MONTH, "{{ application_month }}"),
    (SAMPLE_LEGAL_PERSON, "{{ legal_person }}"),
    # 下面这两个由 Agent 输出（营业执照 OCR / 企业 MCP）填，取不到就渲染为空
    (SAMPLE_CAPITAL, "{{ registered_capital }}"),
    ("有限责任公司", "{{ enterprise_nature }}"),
    # 租赁物名称 / 状态按原模板固定（"设备" / "正常使用"），不做替换
]


def _replace_in_paragraph(paragraph) -> int:
    """替换一个段落里的示例值；返回替换了多少处。

    两步走：先在单个 run 内替换（保留原有格式），只有当目标被 Word 拆到多个 run
    里时才合并整段重写（会丢 run 级格式，所以放在第二步）。
    """
    hits = 0
    for run in paragraph.runs:
        for old, new in REPLACEMENTS:
            if old in run.text:
                run.text = run.text.replace(old, new)
                hits += 1

    for old, new in REPLACEMENTS:
        full = "".join(run.text for run in paragraph.runs)
        if old not in full:
            continue
        merged = full.replace(old, new)
        for index, run in enumerate(paragraph.runs):
            run.text = merged if index == 0 else ""
        hits += 1
    return hits


def _iter_paragraphs(doc):
    for paragraph in doc.paragraphs:
        yield paragraph
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    yield paragraph
                for nested in cell.tables:
                    for row2 in nested.rows:
                        for cell2 in row2.cells:
                            yield from cell2.paragraphs


def main() -> None:
    source = agent_config.templates_dir / SOURCE_FILE
    target = agent_config.templates_dir / TARGET_FILE
    if not source.exists():
        raise SystemExit(f"找不到原件：{source}")

    doc = Document(str(source))
    hits = sum(_replace_in_paragraph(p) for p in _iter_paragraphs(doc))
    doc.save(str(target))

    print(f"原件  ：{source}")
    print(f"渲染模板：{target}（替换 {hits} 处）")
    print("提示：原件保持不变；改了原件或替换规则后重新跑本脚本即可。")


if __name__ == "__main__":
    main()
