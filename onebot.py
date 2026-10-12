from __future__ import annotations

import asyncio
import json
import logging

import websockets

log = logging.getLogger("onebot")


class OneBotClient:
    """OneBot v11 正向 WebSocket 客户端（带自动重连 + API 调用）"""

    def __init__(self, url: str, token: str = "", on_event=None):
        self.url = url
        self.token = token or ""
        self.on_event = on_event          # async def(ev: dict) 或普通函数
        self._ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self._echo = 0
        self._running = False

    # ---------------- 连接 ----------------

    async def _connect(self):
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        # websockets 新版用 additional_headers，老版用 extra_headers
        try:
            return await websockets.connect(
                self.url, additional_headers=headers,
                ping_interval=30, ping_timeout=30,
                max_size=16 * 1024 * 1024,
            )
        except TypeError:
            return await websockets.connect(
                self.url, extra_headers=headers,
                ping_interval=30, ping_timeout=30,
                max_size=16 * 1024 * 1024,
            )

    async def run(self):
        self._running = True
        backoff = 3
        while self._running:
            try:
                async with await self._connect() as ws:
                    self._ws = ws
                    log.info("OneBot 已连接: %s", self.url)
                    backoff = 3
                    async for raw in ws:
                        await self._on_raw(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("OneBot 断开: %s（%ds 后重连）", e, backoff)
            finally:
                self._ws = None
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(RuntimeError("connection closed"))
                self._pending.clear()
            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _on_raw(self, raw):
        try:
            data = json.loads(raw)
        except Exception:
            return

        # API 响应
        echo = data.get("echo")
        if echo is not None and echo in self._pending:
            fut = self._pending.pop(echo)
            if not fut.done():
                fut.set_result(data)
            return

        # 事件推送
        if self.on_event and data.get("post_type"):
            try:
                r = self.on_event(data)
                if asyncio.iscoroutine(r):
                    asyncio.create_task(r)
            except Exception:
                log.exception("on_event 异常")

    # ---------------- API ----------------

    async def call(self, action: str, **params):
        if not self._ws:
            raise RuntimeError("OneBot 未连接")
        self._echo += 1
        echo = str(self._echo)
        fut = asyncio.get_running_loop().create_future()
        self._pending[echo] = fut
        await self._ws.send(json.dumps(
            {"action": action, "params": params, "echo": echo},
            ensure_ascii=False,
        ))
        try:
            return await asyncio.wait_for(fut, timeout=30)
        finally:
            self._pending.pop(echo, None)

    async def send_group_msg(self, group_id: int, message):
        return await self.call("send_group_msg", group_id=group_id, message=message)

    async def send_group_text(self, group_id: int, text: str):
        return await self.send_group_msg(
            group_id, [{"type": "text", "data": {"text": text}}]
        )

    async def get_group_member_list(self, group_id: int):
        resp = await self.call("get_group_member_list", group_id=group_id)
        return resp.get("data") or []

    def stop(self):
        self._running = False