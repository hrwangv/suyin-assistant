# -*- coding=utf-8
import os
from dotenv import load_dotenv
from qcloud_cos import CosConfig, CosS3Client

# 提前加载.env配置文件（必须在读取环境变量前执行，确保os.getenv能获取到值）
load_dotenv()

# COS 客户端配置
# SecretId/SecretKey 建议使用子账号密钥，授权遵循最小权限指引
# https://cloud.tencent.com/document/product/598/37140
config = CosConfig(
    Region='ap-nanjing',
    SecretId=os.getenv('COS_SECRET_ID'),
    SecretKey=os.getenv('COS_SECRET_KEY'),
    Token=None,
    Scheme='https',
)
client = CosS3Client(config)

