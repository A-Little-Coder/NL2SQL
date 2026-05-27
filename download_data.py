# 强制走国内镜像，必须在 import datasets 之前！
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DOWNLOAD_URL"] = "https://hf-mirror.com"
# 关闭离线模式，允许联网
os.environ["HF_DATASETS_OFFLINE"] = "0"

from datasets import load_dataset

# 加载数据集（自动走 hf-mirror）
dataset = load_dataset("birdsql/bird_mini_dev")
print(dataset)