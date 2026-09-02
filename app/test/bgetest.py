from langchain_openai import OpenAI
from dotenv import load_dotenv
import os
import requests
load_dotenv()

response = requests.post(
    "https://api.siliconflow.cn/v1/embeddings",
    headers={
        "Authorization": f"Bearer {os.getenv('SILICONFLOW_API_KEY')}",
        "Content-Type": "application/json"
    },
    json={
        "input": "你好",
        "model": "BAAI/bge-m3"
    }
)

print("状态码：", response.status_code)
print(response.contentw)

# client = OpenAI(api_key="SILICONFLOW_API_KEY", base_url="https://api.siliconflow.cn/v1/embeddings")

