# QQ 群关系分析

把 QQ 群聊记录交给 AI 分析，自动生成群成员的关系图 + 人格画像。

## 功能

- 📊 人物关系图（颜色 = 关系类型，粗细 = 置信度，虚线 = AI 推断）
- 🧠 人格画像（大五人格 + 特质 + 口头禅 + 兴趣）
- 🎨 5 种主题（亮色 / 暗色 / 复古 / 护眼绿 / 樱花粉）
- 🔗 跨群人物识别（同一个人在不同群的名字不同，可跳转）
- 📦 单文件导出（HTML + 头像 + 交互全部内嵌，可直接发人）

## 前置要求

- Python 3.10+
- **OneBot 客户端**（[SnowLuma](https://github.com/SnowLuma/SnowLuma/releases) 或 [NapCat](https://github.com/NapNeko/NapCatQQ)）
  - 装好后登录你的 QQ
  - **在客户端里开启 WebSocket 服务端，端口 3001**
- [DeepSeek API Key](https://platform.deepseek.com/api_keys)

## 快速开始

```bash
git clone https://github.com/athud/qq-relation-agent.git
cd qq-relation-agent

python -m venv .venv
.venv\Scripts\Activate.ps1     # Windows
# source .venv/bin/activate    # macOS/Linux

pip install -r requirements.txt

copy .env.example .env         # Windows
# cp .env.example .env         # macOS/Linux
# 编辑 .env，填入 DEEPSEEK_API_KEY 和 QQ_GROUPS

python main.py
