import os
import sys

from pathlib import Path
from app.rag.import_process.agent.state import ImportGraphState, state_summary
from app.utils.task_utils import add_running_task, add_done_task
from app.core.logger import logger

def node_entry(state: ImportGraphState) -> ImportGraphState:
    """
    节点: 入口节点 (node_entry):
    作为图的 Entry Point，负责接收外部输入并决定流程走向。
    state需要包含的字段: 
    - input_file_path 
    - is_read_md_enabled 
    - is_read_pdf_enabled 
    - md_path :输入就是md则md路径、否则是输出的md路径
    - pdf_path :输入的PDF路径
    - file_title:读取的文件名
    主要功能：
       1.进入节点的日志输出 【节点 + 核心参数】
        记录任务状态 【哪个任务开始了】 -》 给前端推送信息 （埋点）
       2. 参数校验 
        input_file_path -> 没有传入文件 -> end  
        local_dir -> 没有传入输出文件夹 -> 创建一个临时目录
       3. 解析文件类型，修改state对应的参数 
       input_file_path -> md | pdf或者其他类型的文件
          -> is_md_read_enabled True  ||   is_pdf_read_enabled True
          -> md_path = input_file_path | pdf_path = input_file_path
          -> file_title = 读取文件名
       4.结束节点的日志输出 【节点 + 核心参数】
         记录任务状态 【哪个任务结束了】 -》 给前端推送信息 （埋点）
    """
    # 1. 进入节点的日志输出 【节点 + 核心参数】 记录任务状态（给前端推送信息）
    function_name = sys._getframe().f_code.co_name
    logger.info(f">>> [{function_name}]开始执行了！现在的状态为：{state_summary(state)}")
    add_running_task(state['task_id'],function_name)

    # 2. 进行必要的非空校验判定
    input_file_path = state['input_file_path']
    if not input_file_path: # 如果输入的文件路径为空，则直接结束流程
        logger.error(f"[{function_name}]检查发现没有输入文件，无法继续解析！！")
        return state

    # 3. 判定是md文件还是其他格式文件，并且完成state属性赋值
    if input_file_path.endswith(".md"):
        # 处理md
        state['is_md_read_enabled'] = True
        state['md_path'] = input_file_path
        # 处理其他格式的文件，字符元组，以下结尾的文件都能识别处理
    elif input_file_path.lower().endswith((
        ".pdf", ".doc", ".docx", ".xls", ".xlsx",".ppt",".pptx",".png",".jpg",".jpeg",".jp2",".webp",".gif",".bmp"
        )):
        # 处理需要转化成md的文件
        state['is_pdf_read_enabled'] = True
        state['other_path'] = input_file_path
    else:
        logger.error(f"[{function_name}]文件格式不满足，无法继续解析，请上传pdf doc xls 等格式文件")

    # 提取file_title  
    # xx/xxx/aaaa.pdf  应当提取出文件名aaa 
    # 为了后期大模型没有识别出来当前文件对应item_name
    # 使用file_title进行兜底 aaaa.pdf
    #                              
    file_title_os = os.path.basename(input_file_path).split(".")[0]
    file_title = Path(input_file_path).stem # stem方法去掉后缀的文件名    .suffix
    state['file_title'] = file_title  # 保存文件名称
    # 4. 结束节点的日志输出 【节点 + 核心参数】 记录任务状态（给前端推送信息）
    logger.info(f">>> [{function_name}]开始结束了！现在的状态为：{state_summary(state)}")
    add_done_task(state['task_id'], function_name)
    return state