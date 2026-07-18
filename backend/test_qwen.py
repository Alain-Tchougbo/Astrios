import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("QWEN_API_KEY"),
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
)

response = client.chat.completions.create(
    model="qwen3.7-plus",
    messages=[
        {"role": "user", "content": "Bonjour, confirme que tu es bien connecté"},
    ],
)

print(response.choices[0].message.content)
