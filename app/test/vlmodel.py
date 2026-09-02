import os
from openai import OpenAI
import base64
from app.utils.path_util import PROJECT_ROOT
from dotenv import load_dotenv
load_dotenv()



#  编码函数： 将本地文件转换为 Base64 编码的字符串
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

# 将xxxx/eagle.png替换为你本地图像的绝对路径
base64_image = encode_image(PROJECT_ROOT / "output/万用表RS-12的使用/images/fa346a1d7d8210805ee2d5d1409ac4f0c24cecfd32b7e1da7d81b62c08c136b4.jpg")

client = OpenAI(
    api_key=os.getenv("VL_API_KEY"),
    base_url=os.getenv("VL_BASE_URL"),
)


completion = client.chat.completions.create(
    model="qwen3.7-plus", # 此处以qwen3.7-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/models
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    # 需要注意，传入Base64，图像格式（即image/{format}）需要与支持的图片列表中的Content Type保持一致。"f"是字符串格式化的方法。
                    # PNG图像：  f"data:image/png;base64,{base64_image}"
                    # JPEG图像： f"data:image/jpeg;base64,{base64_image}"
                    # WEBP图像： f"data:image/webp;base64,{base64_image}"
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}, 
                },
                {"type": "text", "text": "图片描述了什么"},
            ],
        }
    ],
)

print(completion.choices[0].message.content)










