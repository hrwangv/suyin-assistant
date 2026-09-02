import os
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


# client = OpenAI(
#     # 若没有配置环境变量，请用阿里云百炼API Key将下行替换为：api_key="sk-xxx",
#     # 各地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
#     api_key=os.getenv("VL_API_KEY"),  
#     # 以下是北京地域base-url，如果使用新加坡地域的模型，需要将base_url替换为：https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
#     base_url=os.getenv("VL_BASE_URL"))

# completion = client.embeddings.create(
#     model="text-embedding-v4",
#     input=input_text
# )

# print(completion.model_dump_json())

import dashscope
from http import HTTPStatus

dashscope.base_http_api_url = "https://ws-migjcmhfj2isn9wy.cn-beijing.maas.aliyuncs.com/api/v1"
input_text = "衣服的质量杠杠的"
resp = dashscope.TextEmbedding.call(
    model="text-embedding-v4",
    input=input_text,
    output_type="dense&sparse"
)

if resp.status_code == HTTPStatus.OK:
    print(resp)
    