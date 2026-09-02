import re
def split_pdf_text_with_tags(text, top_level_keywords):
    chunks = []
    current_top_tag = None
    current_sub_tag = None
    lines = text.split('\n')
    current_chunk_content = []
    current_chunk_title = None
    def save_chunk():
        nonlocal current_chunk_content, current_chunk_title
        if current_chunk_title and current_chunk_content:
            chunk_text = current_chunk_title + "\n" + "\n".join(current_chunk_content).strip()
            chunks.append({
                "content": chunk_text,
                "metadata": {
                    "top_tag": current_top_tag,
                    "sub_tag": current_sub_tag
                }
            })
        current_chunk_content = []
        current_chunk_title = None
    for line in lines:
        if line.startswith("## "):
            # 遇到新标题，先保存上一个块
            save_chunk()
            title = line.replace("## ", "").strip()
            # 规则A：判断是否为一级标题
            if title in top_level_keywords:
                current_top_tag = title
                current_sub_tag = None
                continue
            # 规则B：判断是否为二级标题（带编号）
            if re.match(r'^（[一二三四五六七八九十]+）', title) or re.match(r'^\d+\.', title):
                # 提取核心词作为标签，如 "1.新能源领域" -> "新能源领域"
                current_sub_tag = re.sub(r'^（[一二三四五六七八九十]+）|^\d+\.\s*', '', title)
                continue
            # 规则C：具体条目标题
            current_chunk_title = title
        else:
            if current_chunk_title:
                current_chunk_content.append(line)
    save_chunk() # 保存最后一个块
    return chunks
# 定义一级标题词典
top_level_keywords = ["重点要闻", "重点客户", "行业动态", "同业资讯", "监管动态及政策要闻"]
# 执行切分
# chunks = split_pdf_text_with_tags(your_pdf_text, top_level_keywords)