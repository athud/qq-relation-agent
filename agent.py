from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from pathlib import Path

from config import Config
from storage import Storage
from onebot import OneBotClient

log = logging.getLogger("agent")


def _clean_name(s: str) -> str:
    """
    去掉名字里的不可见字符：
    - Unicode 格式控制字符（Cf 类别），如零宽空格 \u200b、BOM \ufeff
    - 不换行空格 \u00a0 替换成普通空格
    - 首尾空白
    """
    if not s:
        return ""
    s = "".join(c for c in s if unicodedata.category(c) != "Cf")
    s = s.replace("\u00a0", " ")
    return s.strip()


# =====================================================================
# LLM 工具
# =====================================================================

def _llm_chat_sync(messages, api_key, base_url, model,
                   temperature, timeout, response_format=None):
    import urllib.request
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = response_format

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _llm_chat(messages, cfg: Config, temperature=None, response_format=None):
    return await asyncio.to_thread(
        _llm_chat_sync,
        messages,
        cfg.api_key, cfg.base_url, cfg.model,
        cfg.temperature if temperature is None else temperature,
        cfg.llm_timeout,
        response_format,
    )


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    l, r = text.find("{"), text.rfind("}")
    if l >= 0 and r > l:
        text = text[l:r + 1]
    return json.loads(text)


async def _llm_json(messages, cfg: Config, temperature=None):
    try:
        resp = await _llm_chat(messages, cfg, temperature=temperature,
                               response_format={"type": "json_object"})
    except Exception:
        resp = await _llm_chat(messages, cfg, temperature=temperature)
    content = resp["choices"][0]["message"]["content"]
    return _extract_json(content)


# =====================================================================
# 提示词
# =====================================================================

RELATION_PROMPT = """你是群聊人物关系分析引擎。

已知群成员列表（请严格只使用下列名称）：
__MEMBERS__

下面是该群最近 __N__ 条聊天记录（含时间戳和消息间隔）：

<<<
__TRANSCRIPT__
>>>

【上一轮已经确认的关系】（供参考，非常重要）
__PREV_RELATIONS__

对上一轮已有关系的处理原则（严格遵守）：
1. 如果本轮仍有互动证据支持 → **必须沿用原有的关系标签**。
   例如上轮判定"游戏搭子"，本轮不许改成"游戏队友"、"玩伴"等同义换名。
2. 如果本轮出现更强的证据，允许修正，但要在 reason 里说明为什么变。
3. 本轮新出现的关系正常输出。
4. 上一轮有、但本轮没有新证据的关系 → 保留一条，置信度降 0.05，type 标成 implicit。

请分析成员之间的关系。

【核心要求：只报"有直接互动"的关系】

判断两人是否"有互动"，必须满足以下**任一条**：

1. 一方 @ 了另一方
2. 一方发言明显是在**回应**另一方的上文（追问、回答、附和、反驳、调侃）
3. **多轮交替对话**（A 说 → B 回 → A 再说），同一话题内
4. 出现明确的**关系称呼**（"师兄"、"老板"、"亲爱的"、"死鬼"等）

以下情况**不算互动，不得报关系**：

- 两人各自单独发言，只是时间上临近
- 一人发文字、另一人发图片/表情/转发，内容不相干
- 中间间隔 ≥5 分钟、话题完全不同的独立发言
- 只是同框（两人都在聊天记录里出现过）

【其他规则】

1. source/target 必须严格取自成员列表，不得使用 QQ 号或自造名字
1.1 relation 标签必须优先复用上一轮已有的词。
    同类关系必须收敛到同一个词，不得同义换名。
    例如不能同时存在"游戏搭子"和"游戏队友"。
2. evidence 里**必须能看到"互动痕迹"**——只有单方面的一句发言、对面没有回应，不算证据
3. 证据不足宁可不报，不要硬凑

【置信度标定】
- 单次互动（只 @ 一次、只对一轮话）：≤0.5
- 2~3 次重复互动：0.55~0.7
- 多次互动 + 明显情感信号（互怼、护短、吃醋）：0.7~0.85
- 直接明确称呼：0.85 以上

【输出格式】
只输出 JSON，不要任何解释：
{
  "relations": [
    {"source": "张三", "target": "李四", "relation": "损友", "type": "implicit",
     "confidence": 0.72, "reason": "两人频繁互怼但语气轻松",
     "evidence": ["张三: 李四你又在这胡说", "李四: 我什么时候胡说了"]}
  ]
}
"""


PERSONA_PROMPT = """你是群聊观察员，任务是用**群里人自己的说话方式**，给某个群友画一张生动的像。

成员名称：__NAME__

【本群成员名单】（这些人都是群友，不是游戏名/物品名，请勿混淆）
__MEMBERS__

该成员最近 __N__ 条发言（带时间戳）：

<<<
__SAMPLES__
>>>

【最重要的原则】

不要用 AI 腔，不要用心理学报告腔。
- ❌ 禁止出现"该成员""该用户""表现出""倾向于""具有较强的"
- ❌ 禁止"性格外向、善于交际""思维活跃""富有创造力"这种空话
- ✅ 用**群友评价群友**的口吻写
- ✅ 每条判断必须**引用他的原话**作为依据
- ✅ 用词口语化，就像你在跟别人聊这个人

【输出 JSON】
{
  "summary": "一句话抓住这个人。要口语化，像朋友在八卦。例：'这人嘴是真毒，但谁被欺负他第一个上'",
  "traits": [
    {"trait": "毒舌", "evidence": "他说'你这代码是用脚写的吧'"},
    {"trait": "护短", "evidence": "看到有人骂群友，他立刻怼回去"}
  ],
  "big_five": {
    "openness": 0,
    "conscientiousness": 0,
    "extraversion": 0,
    "agreeableness": 0,
    "neuroticism": 0
  },
  "big_five_reason": "用一句话解释为什么是这组数值，要有具体例子",
  "language_style": "语言风格的**具体描述**。例：'短句为主，喜欢用句号结尾显得很丧，偶尔冒一句脏话但无恶意'",
  "catchphrases": ["他真正常说的原话"],
  "interests": ["他真正常聊的话题，不要瞎猜"],
  "social_role": "群里的角色。例：'毒舌担当'、'深夜陪聊'、'技术答疑机器'",
  "emotion_baseline": "整体情绪。例：'平时阴阳怪气，遇到技术问题会突然严肃'",
  "confidence": 0.0,
  "caveats": "样本不足时说明"
}

【规则】

- traits 必须 **2~5 条**，每条都带 evidence（引用原话），不得凭感觉
- catchphrases 必须是**他真说过的原话**，不许编
- interests 必须有**至少一句他的发言作为支撑**，猜的不写
- 发言少于 __SOFT_MIN__ 条时：confidence 应低于 0.5，traits 最多 3 条，宁可少写不要瞎编
- big_five 每项 0~100，**要有区分度**，不要全填 50，也不要全填 70
- 只输出 JSON，不要任何解释
- ⚠️ interests / catchphrases / traits 里**绝不能出现群成员名单里的昵称**。
  如果他的发言里提到"银狼"、"南城"、"La"等名字，那是**群友昵称**，
  不是游戏、物品或话题。不要把群友写成兴趣。
"""


# =====================================================================
# Agent
# =====================================================================

class Agent:
    def __init__(self, cfg: Config, storage: Storage, client: OneBotClient):
        self.cfg = cfg
        self.storage = storage
        self.client = client
        self._llm_sem = asyncio.Semaphore(cfg.llm_concurrency)

    # ------------------------------------------------ 事件入口

    async def handle_event(self, ev: dict):
        if ev.get("post_type") != "message":
            return
        if ev.get("message_type") != "group":
            return

        gid = int(ev.get("group_id") or 0)
        if gid <= 0:
            return
        if self.cfg.groups and gid not in self.cfg.groups:
            return

        uid = int(ev.get("user_id") or 0)
        sender = ev.get("sender") or {}
        nickname = sender.get("nickname") or ""
        card = sender.get("card") or ""
        content = self._extract_text(ev.get("message"))
        if not content.strip():
            return

        ts = int(ev.get("time") or time.time())
        msg_id = str(ev.get("message_id") or "")

        self.storage.add_message(
            msg_id=msg_id, group_id=gid, user_id=uid,
            nickname=nickname, card=card, content=content, ts=ts,
        )

    def _extract_text(self, message):
        if isinstance(message, str):
            return message
        if not isinstance(message, list):
            return ""
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            t = seg.get("type")
            d = seg.get("data") or {}
            if t == "text":
                parts.append(str(d.get("text", "")))
            elif t == "at":
                parts.append(f"@{d.get('qq', '')}")
            elif t == "face":
                parts.append("[表情]")
            elif t == "image":
                parts.append("[图片]")
            elif t == "record":
                parts.append("[语音]")
            elif t == "video":
                parts.append("[视频]")
        return "".join(parts)

    # ------------------------------------------------ 定时循环

    async def analysis_loop(self):
        await asyncio.sleep(self.cfg.analysis_startup_delay)

        while True:
            try:
                wait = self._seconds_until_offpeak()
                if wait > 0:
                    mins = wait // 60
                    log.info("当前为高峰时段，等待 %d 分钟至空闲时段再分析", mins)
                    await asyncio.sleep(wait)
                    continue

                groups = self.cfg.groups or self.storage.known_groups()
                if groups:
                    await asyncio.gather(*[self._safe_analyze(g) for g in groups])
                else:
                    log.info("尚无已知群，跳过本轮")
            except Exception:
                log.exception("分析循环异常")
            await asyncio.sleep(self.cfg.interval_seconds)

    def _seconds_until_offpeak(self):
        """
        返回距离下一个 DeepSeek 空闲时段还有多少秒。
        空闲时段 = 非「工作日 9:00-12:00 和 14:00-18:00」的所有时间。
        已经在空闲时段 -> 返回 0。
        """
        from datetime import datetime
        now = datetime.now()
        weekday = now.weekday()  # 0=周一, 6=周日
        hour = now.hour

        if weekday >= 5:
            return 0

        if 9 <= hour < 12:
            target = now.replace(hour=12, minute=0, second=0, microsecond=0)
            return int((target - now).total_seconds())
        if 14 <= hour < 18:
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
            return int((target - now).total_seconds())

        return 0

    async def _safe_analyze(self, gid):
        try:
            await self.analyze_group(gid)
        except Exception:
            log.exception("分析群 %s 失败", gid)

    # ------------------------------------------------ 单群分析

    async def analyze_group(self, gid: int):
        now = int(time.time())
        state = self.storage.get_state(gid, "last_analysis")
        since = state["last_ts"] if state else now - self.cfg.interval_seconds

        total = self.storage.count_messages(gid, since, now)
        if total < self.cfg.min_messages:
            log.info("群 %s 增量 %d 条 < %d，跳过", gid, total, self.cfg.min_messages)
            return

        rows = self.storage.messages_between(
            gid, since, now, limit=self.cfg.max_messages_per_analysis
        )
        log.info("群 %s 开始分析：本轮 %d 条（区间共 %d 条）", gid, len(rows), total)

        members = await self._ensure_members(gid)
        transcript = self._format_transcript(rows, members)

        # 1) 关系
        relations = await self._analyze_relations(transcript, members, len(rows), gid)

        # 过滤掉涉及屏蔽名单的关系
        if self.cfg.ignore_names:
            before = len(relations)
            relations = [
                r for r in relations
                if r["source"] not in self.cfg.ignore_names
                and r["target"] not in self.cfg.ignore_names
            ]
            dropped = before - len(relations)
            if dropped:
                log.info("群 %s 过滤掉 %d 条涉及屏蔽名单的关系", gid, dropped)

        # 按置信度过滤隐含关系
        threshold = self.cfg.min_implicit_confidence
        before = len(relations)
        relations = [
            r for r in relations
            if r["type"] != "implicit" or r["confidence"] >= threshold
        ]
        dropped = before - len(relations)
        if dropped:
            log.info("群 %s 过滤掉 %d 条低置信度隐含关系（阈值 %.2f）",
                     gid, dropped, threshold)

        # 2) 人格
        personas = await self._analyze_personas(gid, rows, members)

        # 3) 报告落盘
        paths = self._save_report(gid, now, relations, personas, members)
        log.info("群 %s 报告已更新：%s", gid, paths["html"])

        # 4) 推送
        if self.cfg.push_to_group:
            try:
                await self._push(gid, relations, personas, len(rows))
            except Exception:
                log.exception("推送群 %s 失败", gid)

        # 5) 记状态
        self.storage.set_state(gid, "last_analysis", now, {
            "relations": relations,
            "personas": personas,
            "report_paths": paths,
        })

    # ------------------------------------------------ 成员列表

    async def _ensure_members(self, gid):
        try:
            raw = await self.client.get_group_member_list(gid)
            if raw:
                for m in raw:
                    m["nickname"] = _clean_name(m.get("nickname", ""))
                    m["card"] = _clean_name(m.get("card", ""))
                self.storage.upsert_members(gid, raw)
        except Exception as e:
            log.warning("拉取群 %s 成员列表失败：%s", gid, e)
        return self.storage.get_members(gid)

    def _ensure_avatars(self, members) -> dict:
        """
        确保每个群成员的头像都在本地 data/avatars/ 下。
        返回 {群名片: 相对路径}，路径是相对于 HTML 文件（在 data/reports/）的。
        只下载缺失的，已存在的不重复下。
        """
        import urllib.request

        avatars_dir = Path(self.cfg.data_dir) / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)

        name2path = {}
        for m in members:
            name = (m.get("card") or m.get("nickname") or "").strip()
            qq = m["user_id"]
            if not name or not qq:
                continue

            file_path = avatars_dir / f"{qq}.jpg"

            if not file_path.exists() or file_path.stat().st_size < 200:
                url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=100"
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = resp.read()
                    file_path.write_bytes(data)
                except Exception as e:
                    log.warning("头像下载失败 %s(%s): %s", name, qq, e)
                    continue

            name2path[name] = "../avatars/" + file_path.name

        return name2path

    # ------------------------------------------------ transcript

    def _format_transcript(self, rows, members):
        uid2name = {}
        for m in members:
            uid2name[m["user_id"]] = (
                m.get("card") or m.get("nickname") or str(m["user_id"])
            ).strip()

        def replace_at(mo):
            qq = int(mo.group(1))
            return "@" + uid2name.get(qq, str(qq))

        lines = []
        prev_ts = None
        for r in rows:
            name = (uid2name.get(r["user_id"])
                    or (r.get("card") or r.get("nickname") or str(r["user_id"])))
            content = re.sub(r"@(\d+)", replace_at, r["content"] or "")
            t = time.strftime("%H:%M", time.localtime(r["ts"]))

            gap = ""
            if prev_ts is not None:
                delta = r["ts"] - prev_ts
                if delta >= 300:
                    mins = delta // 60
                    gap = f" [--- 间隔 {mins} 分钟 ---]"

            prev_ts = r["ts"]
            lines.append(f"{gap}[{t}] {name}: {content}")

        text = "\n".join(lines)
        if len(text) > self.cfg.max_transcript_chars:
            acc, total = [], 0
            for line in reversed(lines):
                if total + len(line) + 1 > self.cfg.max_transcript_chars:
                    break
                acc.append(line)
                total += len(line) + 1
            text = "\n".join(reversed(acc))
        return text

    # ------------------------------------------------ 关系分析

    async def _analyze_relations(self, transcript, members, n_msgs, gid: int):
        # 读上一轮已确认的关系，供 LLM 参考
        prev_lines = []
        state = self.storage.get_state(gid, "last_analysis")
        if state and state.get("payload"):
            prev_rels = state["payload"].get("relations", [])
            for r in prev_rels[:60]:
                conf = r.get("confidence", 0)
                prev_lines.append(
                    f"- {r['source']} --{r['relation']}--> {r['target']}"
                    f" (置信度 {conf:.2f})"
                )
        prev_text = "\n".join(prev_lines) if prev_lines else "（无，本轮是首次分析）"

        names = []
        for m in members:
            n = (m.get("card") or m.get("nickname") or "").strip()
            if n:
                names.append(n)

        member_list = ("、".join(names[:self.cfg.members_list_limit])
                       if names else "（未获取到成员列表，请从记录中自行识别人名）")

        prompt = (RELATION_PROMPT
                  .replace("__MEMBERS__", member_list)
                  .replace("__N__", str(n_msgs))
                  .replace("__EV_MAX__", str(self.cfg.evidence_max_per_relation))
                  .replace("__TRANSCRIPT__", transcript)
                  .replace("__PREV_RELATIONS__", prev_text))

        messages = [
            {"role": "system", "content": "你是群聊关系分析引擎，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ]

        async with self._llm_sem:
            data = await _llm_json(messages, self.cfg)

        out = []
        for r in (data.get("relations") or []):
            s = (r.get("source") or "").strip()
            t = (r.get("target") or "").strip()
            rel = (r.get("relation") or "").strip()
            if not s or not t or not rel or s == t:
                continue
            try:
                conf = float(r.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            ev = r.get("evidence") or []
            if isinstance(ev, str):
                ev = [ev]
            out.append({
                "source": s, "target": t, "relation": rel,
                "type": "implicit"
                        if str(r.get("type", "")).lower() == "implicit"
                        else "explicit",
                "confidence": max(0.0, min(1.0, conf)),
                "reason": (r.get("reason") or "").strip(),
                "evidence": [e for e in ev if isinstance(e, str)]
                            [:self.cfg.evidence_max_per_relation],
                "count": 1,
            })

        # 对称关系方向归一化，避免 A→B 和 B→A 都出现
        symmetric = {
            "损友", "朋友", "好友", "游戏搭子", "游戏队友",
            "同事", "同学", "情侣", "夫妻", "邻居", "闺蜜",
            "对头", "同好", "群友", "普通群友",
        }
        for r in out:
            if r["relation"] in symmetric and r["source"] > r["target"]:
                r["source"], r["target"] = r["target"], r["source"]

        out.sort(key=lambda x: (x["type"] != "explicit", -x["confidence"]))
        return out

    # ------------------------------------------------ 人格分析

    async def _analyze_personas(self, gid, rows, members):
        # 构造成员名单，供 prompt 用
        member_list = "、".join(
            (m.get("card") or m.get("nickname") or "").strip()
            for m in members
            if (m.get("card") or m.get("nickname"))
        )

        by_user = {}
        for r in rows:
            by_user.setdefault(r["user_id"], []).append(r)

        uid2name = {}
        for m in members:
            uid2name[m["user_id"]] = (
                m.get("card") or m.get("nickname") or str(m["user_id"])
            ).strip()

        targets = []
        for uid, msgs in by_user.items():
            if len(msgs) < self.cfg.min_user_messages:
                continue
            name = (uid2name.get(uid)
                    or msgs[-1].get("card")
                    or msgs[-1].get("nickname")
                    or str(uid))
            if name in self.cfg.ignore_names:
                continue
            targets.append((uid, name, msgs))

        if not targets:
            return {}

        targets.sort(key=lambda x: -len(x[2]))
        targets = targets[:self.cfg.max_personas_per_run]

        async def one(uid, name, msgs):
            samples = "\n".join(
                f"[{time.strftime('%m-%d %H:%M', time.localtime(m['ts']))}] {m['content']}"
                for m in msgs[-self.cfg.max_user_samples:]
            )
            prompt = (PERSONA_PROMPT
                      .replace("__NAME__", name)
                      .replace("__MEMBERS__", member_list)
                      .replace("__N__", str(len(msgs)))
                      .replace("__SOFT_MIN__", str(self.cfg.persona_min_messages_soft))
                      .replace("__SAMPLES__", samples))
            messages = [
                {"role": "system", "content": "你是群聊人格分析师，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ]
            try:
                async with self._llm_sem:
                    data = await _llm_json(messages, self.cfg)
                data["_user_id"] = uid
                data["_name"] = name
                data["_msg_count"] = len(msgs)
                return data
            except Exception as e:
                log.warning("人格分析失败 %s：%s", name, e)
                return None

        results = await asyncio.gather(*[one(*t) for t in targets])
        return {r["_name"]: r for r in results if r}

    # ------------------------------------------------ 报告

    def _build_report_md(self, gid, hist, members, now):
        def fmt(ts):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

        relations = list(hist["relations"].values())
        relations.sort(key=lambda x: (x["type"] != "explicit", -x.get("confidence", 0)))
        personas = hist["personas"]

        L = [f"# 群 {gid} 累积分析报告", ""]
        L.append(f"- 生成时间：{fmt(now)}")
        L.append(f"- 成员数：{len(members)}")
        L.append(f"- 累积关系：{len(relations)} 条")
        L.append(f"- 人物画像：{len(personas)} 人")
        L.append("")

        explicit = [r for r in relations if r["type"] == "explicit"]
        implicit = [r for r in relations if r["type"] == "implicit"]

        L.append("## 一、人物关系")
        L.append("")
        L.append(f"### 显式关系（{len(explicit)}）")
        L.append("")
        if not explicit:
            L.append("（无）")
        for r in explicit:
            L.append(f"- **{r['source']}** --{r['relation']}--> **{r['target']}**  "
                     f"（置信度 {r['confidence']:.2f}，出现 {r.get('seen_count', 1)} 次，"
                     f"{fmt(r.get('first_seen', now))} ~ {fmt(r.get('last_seen', now))}）")
            if r.get("reason"):
                L.append(f"  - 依据：{r['reason']}")
            for ev in r.get("evidence", [])[:2]:
                L.append(f"  - 原文：{ev}")
        L.append("")

        L.append(f"### 隐含关系（{len(implicit)}）")
        L.append("")
        if not implicit:
            L.append("（无）")
        for r in implicit:
            L.append(f"- **{r['source']}** --{r['relation']}--> **{r['target']}**  "
                     f"（置信度 {r['confidence']:.2f}，出现 {r.get('seen_count', 1)} 次）")
            if r.get("reason"):
                L.append(f"  - 依据：{r['reason']}")
            for ev in r.get("evidence", [])[:2]:
                L.append(f"  - 原文：{ev}")
        L.append("")

        L.append("## 二、人物画像")
        L.append("")
        if not personas:
            L.append("（样本不足）")
        for name, p in personas.items():
            L.append(f"### {name}")
            L.append("")
            if p.get("_msg_count"):
                L.append(f"**样本**：{p['_msg_count']} 条消息")
                L.append("")
            if p.get("summary"):
                L.append(f"**一句话**：{p['summary']}")
                L.append("")
            if p.get("traits"):
                trait_strs = []
                for t in p["traits"]:
                    if isinstance(t, str):
                        trait_strs.append(t)
                    elif isinstance(t, dict):
                        tname = t.get("trait", "")
                        ev = t.get("evidence", "")
                        if tname and ev:
                            trait_strs.append(f"{tname}（{ev}）")
                        elif tname:
                            trait_strs.append(tname)
                if trait_strs:
                    L.append(f"**特质**：{'、'.join(trait_strs)}")
                    L.append("")
            bf = p.get("big_five") or {}
            if bf:
                L.append("**大五人格**（0~100）：")
                L.append("")
                L.append("| 开放性 | 尽责性 | 外向性 | 宜人性 | 神经质 |")
                L.append("|---|---|---|---|---|")
                L.append(f"| {bf.get('openness', '-')} | {bf.get('conscientiousness', '-')} "
                         f"| {bf.get('extraversion', '-')} | {bf.get('agreeableness', '-')} "
                         f"| {bf.get('neuroticism', '-')} |")
                L.append("")
            if p.get("language_style"):
                L.append(f"**语言风格**：{p['language_style']}")
                L.append("")
            if p.get("catchphrases"):
                L.append(f"**口头禅**：{'、'.join(p['catchphrases'])}")
                L.append("")
            if p.get("interests"):
                L.append(f"**兴趣**：{'、'.join(p['interests'])}")
                L.append("")
            if p.get("social_role"):
                L.append(f"**群内角色**：{p['social_role']}")
                L.append("")
            if p.get("emotion_baseline"):
                L.append(f"**情绪基调**：{p['emotion_baseline']}")
                L.append("")
            if p.get("confidence") is not None:
                L.append(f"**画像置信度**：{p['confidence']}")
            if p.get("caveats"):
                L.append(f"**备注**：{p['caveats']}")
            L.append("")

        return "\n".join(L)

    # ------------------------------------------------ 历史池

    def _history_path(self, gid):
        d = Path(self.cfg.data_dir) / "history"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"g{gid}.json"

    def _load_history(self, gid):
        p = self._history_path(gid)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                log.exception("历史文件损坏，重置：%s", p)
        return {"relations": {}, "personas": {}}

    def _save_history(self, gid, hist):
        p = self._history_path(gid)
        p.write_text(json.dumps(hist, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    def _merge_history(self, gid, relations, personas, now):
        """把本轮结果合并进历史池。
        关系：同 (source, relation, target) 保留置信度高的版本，seen_count 累加
        人格：同 name 直接覆盖（取最新）
        """
        hist = self._load_history(gid)

        for r in relations:
            key = f"{r['source']}||{r['relation']}||{r['target']}"
            old = hist["relations"].get(key)
            if old:
                if r["confidence"] > old.get("confidence", 0):
                    old["confidence"] = r["confidence"]
                    old["reason"] = r.get("reason", "")
                    old["evidence"] = r.get("evidence", [])
                    old["type"] = r.get("type", old.get("type", "explicit"))
                old["last_seen"] = now
                old["seen_count"] = old.get("seen_count", 1) + 1
            else:
                hist["relations"][key] = {
                    "source": r["source"],
                    "relation": r["relation"],
                    "target": r["target"],
                    "type": r.get("type", "explicit"),
                    "confidence": r["confidence"],
                    "reason": r.get("reason", ""),
                    "evidence": r.get("evidence", []),
                    "first_seen": now,
                    "last_seen": now,
                    "seen_count": 1,
                }

        for name, p in personas.items():
            hist["personas"][name] = {**p, "last_analyzed": now}

        self._save_history(gid, hist)
        return hist

    def _save_report(self, gid, now, relations, personas, members):
        """合并进历史池 + 生成唯一报告（覆盖）。"""
        hist = self._merge_history(gid, relations, personas, now)

        merged_relations = list(hist["relations"].values())
        merged_relations.sort(key=lambda x: (
            x["type"] != "explicit", -x.get("confidence", 0)))

        out_dir = Path(self.cfg.data_dir) / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)

        md = self._build_report_md(gid, hist, members, now)
        md_path = out_dir / f"g{gid}.md"
        md_path.write_text(md, encoding="utf-8")

        html_path = None
        try:
            import person_relation as pr
            avatar_map = self._ensure_avatars(members)

            pc = Counter()
            for r in merged_relations:
                pc[r["source"]] += 1
                pc[r["target"]] += 1

            # 构造跨群映射
            cross_map = {}
            try:
                import sqlite3
                db = sqlite3.connect(str(Path(self.cfg.data_dir) / "agent.db"))
                db.row_factory = sqlite3.Row
                local_uids = {}
                for m in members:
                    nm = (m.get("card") or m.get("nickname") or "").strip()
                    if nm:
                        local_uids[m["user_id"]] = nm
                for uid, local_name in local_uids.items():
                    rows = db.execute(
                        "SELECT group_id, nickname, card FROM members WHERE user_id=?",
                        (uid,)
                    ).fetchall()
                    groups = []
                    for r in rows:
                        other_name = (r["card"] or r["nickname"] or "").strip()
                        if other_name:
                            groups.append({
                                "gid": r["group_id"],
                                "name": other_name,
                                "is_local": r["group_id"] == gid,
                            })
                    groups.sort(key=lambda x: x["gid"])
                    if len(groups) > 1:
                        cross_map[local_name] = {
                            "user_id": uid,
                            "groups": groups,
                        }
                db.close()
            except Exception:
                log.exception("构造 cross_map 失败")

            result = {
                "persons": [{"name": n, "count": c} for n, c in pc.most_common()],
                "relations": merged_relations,
            }

            html_path = out_dir / f"g{gid}.html"
            html_path.write_text(
                pr.to_html(
                    result,
                    title=f"群 {gid} 关系图",
                    avatar_map=avatar_map,
                    personas=hist["personas"],
                    embed=True,
                    cross_map=cross_map,
                    static_dir=str(Path(self.cfg.data_dir) / "static"),
                ),
                encoding="utf-8",
            )
        except Exception:
            log.exception("生成 HTML 关系图失败")

        return {"md": str(md_path), "html": str(html_path) if html_path else None}

    # ------------------------------------------------ 推送

    async def _push(self, gid, relations, personas, n_msgs):
        if not self.client._ws:
            log.warning("OneBot 未连接，跳过推送")
            return

        explicit = [r for r in relations if r["type"] == "explicit"]
        implicit = [r for r in relations if r["type"] == "implicit"]

        top_rel = self.cfg.push_top_relations
        top_per = self.cfg.push_top_personas

        lines = [f"📊 群聊分析完成（本轮 {n_msgs} 条消息）"]

        if explicit:
            lines.append("")
            lines.append(f"【显式关系 {len(explicit)} 条】")
            for r in explicit[:top_rel]:
                lines.append(f"  {r['source']} --{r['relation']}--> "
                             f"{r['target']}（{r['confidence']:.2f}）")

        if implicit:
            lines.append("")
            lines.append(f"【隐含关系 {len(implicit)} 条】")
            for r in implicit[:top_rel]:
                lines.append(f"  {r['source']} --{r['relation']}--> "
                             f"{r['target']}（{r['confidence']:.2f}）")

        if personas:
            lines.append("")
            lines.append(f"【人格画像 {len(personas)} 人】")
            for name, p in list(personas.items())[:top_per]:
                lines.append(f"  {name}：{p.get('summary', '—')}")

        lines.append("")
        lines.append("（完整报告已生成至 data/reports/）")

        text = "\n".join(lines)
        if len(text) > self.cfg.push_max_chars:
            text = text[:self.cfg.push_max_chars] + "…"

        await self.client.send_group_text(gid, text)