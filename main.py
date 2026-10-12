from dotenv import load_dotenv
load_dotenv()
import asyncio
import logging
import sys
from pathlib import Path

from config import load_config
from storage import Storage
from onebot import OneBotClient
from agent import Agent


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def amain():
    cfg = load_config()
    if not cfg.api_key:
        print("错误：请设置 DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量", file=sys.stderr)
        return 1

    storage = Storage(Path(cfg.data_dir) / "agent.db")
    client = OneBotClient(cfg.onebot_url, cfg.onebot_token)
    agent = Agent(cfg, storage, client)
    client.on_event = agent.handle_event  # 绑定事件回调

    log = logging.getLogger("main")
    log.info("启动：OneBot=%s，分析间隔=%d 秒，模型=%s",
             cfg.onebot_url, cfg.interval_seconds, cfg.model)

    try:
        await asyncio.gather(
            client.run(),
            agent.analysis_loop(),
        )
    except asyncio.CancelledError:
        pass
    return 0


def main():
    setup_logging()
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("已退出")


if __name__ == "__main__":
    main()