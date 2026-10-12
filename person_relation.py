#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
person_relation.py —— 人物关系抽取器（支持显式抽取 + 隐含关系推断）

两种模式：
  1. 规则模式（默认，零依赖）：姓氏表 + 句式模板，抽显式关系
  2. 大模型模式（--llm）：调用 OpenAI 兼容接口，既能抽显式关系，
     也能从亲昵举动、特殊称呼、情绪表现等细节里推断隐含关系

命令行用法：
    python person_relation.py "张三是李四的父亲，李四和王五是好朋友。"
    python person_relation.py -f novel.txt --format html -o graph.html
    python person_relation.py -f novel.txt --llm --api-key sk-xxx
    python person_relation.py -f novel.txt --llm --no-infer
    python person_relation.py -f novel.txt --llm -o out.html --format html
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

# =====================================================================
# 一、词表资源
# =====================================================================

SURNAMES = set(
    "赵钱孙李周吴郑王冯陈蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹喻柏窦章苏潘葛范彭鲁韦"
    "马苗凤花俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常傅皮齐康伍余元卜顾孟平黄穆萧"
    "尹姚邵汪祁毛禹狄米贝臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童"
    "颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左"
    "石崔吉龚程邢滑裴陆荣翁荀羊惠甄曲家封芮储靳汲邴糜松井段富巫乌焦巴牧山谷车侯全班仰秋"
    "仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸司韶黎薄印宿白怀蒲邰鄂索赖卓蔺屠蒙池乔阴胥"
    "苍双闻莘党翟谭贡劳姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀浦尚农温别庄晏柴瞿阎充慕连茹"
    "习艾鱼容向古易慎戈廖庾终居衡步都耿满弘匡国文寇广禄阙东欧沃利蔚越夔隆师巩聂晁勾敖融"
    "冷辛阚那简饶空曾沙鞠丰巢关蒯查荆红游竺权盖益桓公"
)

COMPOUND_SURNAMES = [
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "万俟", "闻人",
    "夏侯", "诸葛", "尉迟", "公羊", "赫连", "澹台", "皇甫", "宗政", "濮阳", "公冶",
    "太叔", "申屠", "公孙", "慕容", "仲孙", "钟离", "长孙", "宇文", "司徒", "鲜于",
    "司空", "闾丘", "子车", "亓官", "司寇", "巫马", "公西", "颛孙", "壤驷", "公良",
    "漆雕", "乐正", "宰父", "谷梁", "拓跋", "夹谷", "轩辕", "令狐", "段干", "百里",
    "呼延", "东郭", "南门", "羊舌", "微生", "梁丘", "左丘", "西门", "第五", "公乘",
]

EXCLUDE_FIRST = set(
    "明了着过和与及或但而就都也还又很把被给让对从向在是之其此该某各每任凡如若因为"
    "所以并且然后于是可是但是不过只是这那什么怎么没有不是就是还有以及以为可以能够应"
    "该需要一些一下一直一起一样一定非常特别十分更加比较稍微几乎完全终于突然忽然立刻"
    "马上已经曾经从来始终永远明天昨天今天现在时间时候一个"
)

BAD_IN_NAME = set(
    "的了着过和与及或但而就都也还又很把被给让对从向在是之其此该某各每任凡如若因为"
    "所以并且然后于是可是但是不过只是这那吗呢吧啊呀哦嗯将已经正在"
)

NON_NAMES = set(
    """我们 他们 你们 她们 它们 咱们 大家 自己 别人 人家 什么 怎么 这个 那个 这里
    那里 时候 现在 以前 以后 之后 之前 一个 一些 这些 那些 东西 事情 问题 地方
    样子 方面 情况 结果 原因 朋友 老师 学生 同学 同事 父亲 母亲 爸爸 妈妈 儿子
    女儿 哥哥 弟弟 姐姐 妹妹 丈夫 妻子 爷爷 奶奶 公司 学校 医院 政府 国家 社会
    世界 时间 中国 北京 上海 广州 深圳 杭州 南京 武汉 成都 西安 今天 明天 昨天
    上午 下午 晚上 早上 中午 那么 这么 如果 虽然 因为 所以 但是 可是 而且 并且
    于是 然后 不过 只是 这样 那样 一样 一起 一直 一定 非常 特别 十分 更加 比较""".split()
)

# =====================================================================
# 二、人名识别
# =====================================================================

_HAN = re.compile(r"[\u4e00-\u9fa5]")


def _is_han(s: str) -> bool:
    return bool(s) and all("\u4e00" <= c <= "\u9fa5" for c in s)


def _surname_len(word: str) -> int:
    if len(word) >= 2 and word[:2] in COMPOUND_SURNAMES:
        return 2
    if word and word[0] in SURNAMES and word[0] not in EXCLUDE_FIRST:
        return 1
    return 0


def _valid_name(word: str) -> bool:
    if not (2 <= len(word) <= 4):
        return False
    if not _is_han(word):
        return False
    if word in NON_NAMES:
        return False
    sl = _surname_len(word)
    if sl == 0 or sl >= len(word):
        return False
    given = word[sl:]
    if any(c in BAD_IN_NAME for c in given):
        return False
    return True


def _rule_names(sentence: str):
    out, i, n = [], 0, len(sentence)
    while i < n:
        matched = False
        for L in (4, 3, 2):
            if i + L > n:
                continue
            w = sentence[i:i + L]
            if _valid_name(w):
                out.append((i, i + L, w))
                i += L
                matched = True
                break
        if not matched:
            i += 1
    return out


_JIEBA = None


def _get_jieba():
    global _JIEBA
    if _JIEBA is None:
        try:
            import jieba.posseg as pseg  # type: ignore
            _JIEBA = pseg
        except Exception:
            _JIEBA = False
    return _JIEBA


def find_names(sentence: str):
    slots = {}

    pseg = _get_jieba()
    if pseg:
        pos = 0
        try:
            for w, flag in pseg.cut(sentence):
                idx = sentence.find(w, pos)
                if idx < 0:
                    idx = pos
                pos = idx + len(w)
                if flag == "nr" and _valid_name(w):
                    slots[idx] = (idx, idx + len(w), w)
        except Exception:
            pass

    for s, e, w in _rule_names(sentence):
        if any(not (e <= s2 or s >= e2) for s2, e2, _ in slots.values()):
            continue
        slots[s] = (s, e, w)

    return sorted(slots.values())


# =====================================================================
# 三、关系词表
# =====================================================================

REL_KEYWORDS = {
    "父亲": ["父亲", "爸爸", "老爸", "爹"],
    "母亲": ["母亲", "妈妈", "老妈", "娘"],
    "儿子": ["儿子"],
    "女儿": ["女儿"],
    "丈夫": ["丈夫", "老公", "夫君", "相公"],
    "妻子": ["妻子", "老婆", "夫人", "太太", "媳妇", "内人"],
    "哥哥": ["哥哥", "大哥", "兄长", "家兄"],
    "弟弟": ["弟弟", "小弟", "胞弟"],
    "姐姐": ["姐姐", "大姐", "家姐"],
    "妹妹": ["妹妹", "小妹", "胞妹"],
    "祖父": ["爷爷", "祖父"],
    "祖母": ["奶奶", "祖母"],
    "外祖父": ["外公", "姥爷"],
    "外祖母": ["外婆", "姥姥"],
    "孙子": ["孙子"],
    "孙女": ["孙女"],
    "叔伯": ["叔叔", "伯伯", "舅舅", "姑姑", "姨妈"],
    "朋友": ["朋友", "好友", "好朋友", "哥们", "闺蜜", "挚友", "知己", "伙伴"],
    "同学": ["同学", "同窗"],
    "同事": ["同事"],
    "老师": ["老师", "教师", "师父", "师傅", "恩师", "教练"],
    "学生": ["学生", "徒弟", "弟子", "门生"],
    "上级": ["上司", "领导", "老板", "上级", "长官"],
    "下属": ["下属", "手下", "下级", "部下"],
    "敌人": ["敌人", "仇人", "死对头", "宿敌"],
    "恋人": ["恋人", "女朋友", "男朋友", "女友", "男友", "情人", "爱人"],
    "夫妻": ["夫妻", "夫妇", "两口子"],
    "邻居": ["邻居", "街坊"],
    "合作伙伴": ["合作伙伴", "搭档", "合伙人", "队友"],
    "兄弟": ["兄弟"],
    "姐妹": ["姐妹"],
}

KW2REL = {}
for _rel, _kws in REL_KEYWORDS.items():
    for _kw in _kws:
        KW2REL[_kw] = _rel
KEYWORDS_SORTED = sorted(KW2REL, key=len, reverse=True)

SYMMETRIC = {
    "朋友", "同学", "同事", "敌人", "恋人", "夫妻",
    "邻居", "合作伙伴", "兄弟", "姐妹",
}

# =====================================================================
# 四、规则抽取核心
# =====================================================================

_IS_BE = re.compile(r"(?:是|就是|叫|名叫|称为|乃|成了|成为|当上)\s*$")
_BE_AFTER = re.compile(r"\s*(?:是|就是|叫|名叫|称为|乃|成了|成为)\s*")
_CONJ_BEFORE = re.compile(r"(?:和|与|跟|同|、|以及)\s*$")


def _name_before(names_pos, pos):
    best = None
    for s, e, n in names_pos:
        if e <= pos:
            best = (s, e, n)
        else:
            break
    return best


def _name_after(names_pos, pos):
    for s, e, n in names_pos:
        if s >= pos:
            return (s, e, n)
    return None


def _match_pattern(sent: str, p: int, e: int, rel: str, names_pos):
    out = []

    if p > 0 and sent[p - 1] == "的":
        owner = _name_before(names_pos, p - 1)
        if not owner:
            return out

        m = _IS_BE.search(sent[: owner[0]])
        if m:
            subj = _name_before(names_pos, m.start())
            if subj and subj[2] != owner[2]:
                out.append((subj[2], rel, owner[2]))
                return out

        m2 = _BE_AFTER.match(sent[e:])
        if m2:
            z = _name_after(names_pos, e + m2.end())
            if z and z[0] - (e + m2.end()) <= 1:
                out.append((z[2], rel, owner[2]))
                return out

        z = _name_after(names_pos, e)
        if z and z[0] - e <= 1:
            out.append((z[2], rel, owner[2]))
            return out

        return out

    m = _IS_BE.search(sent[:p])
    if m:
        y = _name_before(names_pos, m.start())
        if y:
            m2 = _CONJ_BEFORE.search(sent[: y[0]])
            if m2:
                x = _name_before(names_pos, m2.start())
                if x and x[2] != y[2]:
                    if rel in SYMMETRIC:
                        out.append((x[2], rel, y[2]))
                        out.append((y[2], rel, x[2]))
                    else:
                        out.append((x[2], rel, y[2]))
                    return out
    return out


def extract_sentence(sent: str, names_pos):
    out, used = [], []
    for kw in KEYWORDS_SORTED:
        for m in re.finditer(re.escape(kw), sent):
            p, e = m.start(), m.end()
            if any(not (e <= u0 or p >= u1) for u0, u1 in used):
                continue
            got = _match_pattern(sent, p, e, KW2REL[kw], names_pos)
            if got:
                used.append((p, e))
                out.extend(got)
    return out


def split_sentences(text: str):
    text = re.sub(r"[ \t\u3000]+", " ", text)
    parts = re.split(r"(?<=[。！？!?；;\n\r])", text)
    return [p.strip() for p in parts if p and p.strip()]


# =====================================================================
# 五、规则模式主函数
# =====================================================================

def analyze(text: str, cooccur: bool = True, cooccur_min: int = 2) -> dict:
    person_counter = Counter()
    rel_stat = {}
    cooc = Counter()

    for sent in split_sentences(text):
        names_pos = find_names(sent)
        if not names_pos:
            continue

        for _, _, n in names_pos:
            person_counter[n] += 1

        uniq_names, seen = [], set()
        for _, _, n in names_pos:
            if n not in seen:
                seen.add(n)
                uniq_names.append(n)

        triples = extract_sentence(sent, names_pos)
        linked = set()
        for s, r, o in triples:
            if s == o:
                continue
            key = (s, r, o)
            item = rel_stat.setdefault(key, {"count": 0, "evidence": []})
            item["count"] += 1
            if len(item["evidence"]) < 3 and sent[:100] not in item["evidence"]:
                item["evidence"].append(sent[:100])
            linked.add(frozenset((s, o)))

        if cooccur:
            for a, b in combinations(uniq_names, 2):
                if frozenset((a, b)) in linked:
                    continue
                cooc[tuple(sorted((a, b)))] += 1

    relations = [
        {
            "source": s,
            "relation": r,
            "target": o,
            "type": "explicit",
            "confidence": 1.0,
            "reason": "",
            "count": v["count"],
            "evidence": v["evidence"],
        }
        for (s, r, o), v in sorted(rel_stat.items(), key=lambda x: -x[1]["count"])
    ]

    if cooccur:
        for (a, b), c in cooc.most_common():
            if c < cooccur_min:
                continue
            relations.append(
                {"source": a, "relation": "同现", "target": b,
                 "type": "cooccur", "confidence": 0.0, "reason": "",
                 "count": c, "evidence": []}
            )

    return {
        "persons": [{"name": n, "count": c} for n, c in person_counter.most_common()],
        "relations": relations,
    }


# =====================================================================
# 六、大模型抽取（支持隐含关系推断）
# =====================================================================

LLM_PROMPT_PLAIN = """你是一个人物关系抽取引擎。请从下面的文本中抽取所有人物以及人物之间的关系。

要求：
1. 只输出 JSON，不要任何解释，不要 markdown 代码块标记。
2. JSON 结构：
{"persons": ["人物1","人物2"], "relations": [{"source":"人物A","relation":"关系","target":"人物B","evidence":"原文依据"}]}
3. relation 用简洁中文，如：父亲、母亲、夫妻、朋友、师生、同事、上级、下属、敌人、恋人、兄弟、姐妹 等。
4. 方向约定：source 是 target 的 relation。
   例如"张三的父亲是李四" → source=李四, relation=父亲, target=张三。
5. 不要编造文本中不存在的人物和关系。

文本：
<<<
__TEXT__
>>>
"""

LLM_PROMPT_INFER = """你是一位专业的人物关系分析引擎，擅长从文本细节中挖掘人物关系，包括没有直接说明的隐含关系。

请仔细阅读文本，完成两件事：
1. 【显式关系】文本中直接写明的关系（如"A是B的父亲""C和D是朋友"）。
2. 【隐含关系】文本没有直说，但通过行为、称呼、互动可以合理推断出来的关系。

推断依据可以包括（但不限于）：
- 亲昵举动：牵手、拥抱、依偎、亲吻、抚摸头发、挽手臂、十指相扣
- 特殊称呼：昵称、爱称、"亲爱的"、去姓直呼其名、敬语/谦辞混用
- 情绪表现：吃醋、脸红、心跳、注视、害羞、紧张、维护、偏袒
- 行为模式：照顾起居、深夜独处、共处一室、长期同行、经济往来
- 社交语境：见家长、共养宠物、共用物品、亲密距离、身体接触
- 冲突表现：激烈争吵后的和好、背叛、冷战、报复

对每条隐含关系，必须给出：
- relation：推断的关系（恋人、暗恋、暧昧、前任、情人、地下恋情、青梅竹马、情敌……）
- confidence：0~1 之间的小数，表示把握程度
- reason：一句话说明推断依据
- evidence：原文中支持该推断的片段（必须原文引用，不许改写）

置信度标定（严格遵守，避免过度推断）：
- 只有"牵手、拥抱"等一般亲密动作 → confidence 不超过 0.6
- "亲吻、同床、互称老公/老婆、见家长"等强信号 → 可给到 0.8 以上
- 仅同框、仅对话、仅并肩而行 → 应低于 0.4，宁可不报
- 无法确定时宁可不报，不要硬凑

输出格式（只输出 JSON，不要任何解释，不要 markdown 代码块标记）：
{
  "persons": ["人物1", "人物2"],
  "relations": [
    {
      "source": "人物A", "relation": "关系名", "target": "人物B",
      "type": "explicit", "confidence": 1.0,
      "reason": "", "evidence": ["原文片段"]
    },
    {
      "source": "人物A", "relation": "恋人", "target": "人物B",
      "type": "implicit", "confidence": 0.75,
      "reason": "雨中共撑一把伞，A把外套披在B身上并低声叫她傻瓜",
      "evidence": ["他把外套脱下来披在她肩上，低声说了句傻瓜。"]
    }
  ]
}

方向约定：source 是 target 的 relation。
例如"张三的父亲是李四" → source=李四, relation=父亲, target=张三。

现在分析下面这段文本：
<<<
__TEXT__
>>>
"""


def _parse_llm_json(content: str) -> dict:
    content = content.strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.M).strip()
    l, r = content.find("{"), content.rfind("}")
    if l >= 0 and r > l:
        content = content[l:r + 1]
    return json.loads(content)


def llm_extract(text: str, api_key: str,
                base_url: str = "https://api.deepseek.com",
                model: str = "deepseek-chat",
                timeout: int = 180,
                infer: bool = True,
                temperature: float = 0.2) -> dict:
    import urllib.request

    prompt = (LLM_PROMPT_INFER if infer else LLM_PROMPT_PLAIN).replace("__TEXT__", text)

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": "你是专业的人物关系抽取与推断引擎，只输出 JSON，不输出任何解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    content = body["choices"][0]["message"]["content"]
    data = _parse_llm_json(content)

    persons = [{"name": n, "count": 1} for n in data.get("persons", []) if n]

    relations = []
    for x in data.get("relations", []):
        s = (x.get("source") or "").strip()
        o = (x.get("target") or "").strip()
        rel = (x.get("relation") or "").strip()
        if not s or not o or not rel or s == o:
            continue
        try:
            conf = float(x.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        rtype = "implicit" if str(x.get("type", "")).lower() == "implicit" else "explicit"
        ev = x.get("evidence") or []
        if isinstance(ev, str):
            ev = [ev]
        relations.append({
            "source": s,
            "relation": rel,
            "target": o,
            "type": rtype,
            "confidence": round(conf, 2),
            "reason": (x.get("reason") or "").strip(),
            "count": 1,
            "evidence": [e for e in ev if isinstance(e, str)],
        })

    merged = {}
    for r in relations:
        key = (r["source"], r["relation"], r["target"])
        old = merged.get(key)
        if old is None:
            merged[key] = r
            continue
        better = (
            (r["type"] == "explicit" and old["type"] != "explicit")
            or (r["type"] == old["type"] and r["confidence"] > old["confidence"])
        )
        if better:
            merged[key] = r
        else:
            for e in r["evidence"]:
                if e not in old["evidence"] and len(old["evidence"]) < 3:
                    old["evidence"].append(e)

    relations = sorted(
        merged.values(),
        key=lambda x: (x["type"] != "explicit", -x["confidence"]),
    )
    return {"persons": persons, "relations": relations}


# =====================================================================
# 七、输出格式化
# =====================================================================

def to_text(result: dict) -> str:
    lines = ["【人物】（按出现次数排序）"]
    if not result["persons"]:
        lines.append("  （未识别到人物）")
    for p in result["persons"]:
        lines.append(f"  {p['name']}  ×{p['count']}")

    explicit = [r for r in result["relations"]
                if r.get("type", "explicit") == "explicit"]
    implicit = [r for r in result["relations"] if r.get("type") == "implicit"]
    cooc = [r for r in result["relations"] if r.get("type") == "cooccur"
            or r["relation"] == "同现"]

    def _emit(title, items, show_conf=False, show_reason=False):
        lines.append("")
        lines.append(title)
        if not items:
            lines.append("  （无）")
            return
        shown = set()
        for r in items:
            if r["relation"] in SYMMETRIC:
                k = (frozenset((r["source"], r["target"])), r["relation"])
                if k in shown:
                    continue
                shown.add(k)
                head = f"  {r['source']} ←→ {r['target']}  （{r['relation']}"
            else:
                head = f"  {r['source']} --{r['relation']}--> {r['target']}  （"
            if show_conf:
                head += f"，置信度 {r.get('confidence', 0):.2f}"
            head += f"，{r['count']} 次）"
            lines.append(head)
            if show_reason and r.get("reason"):
                lines.append(f"      推断依据：{r['reason']}")
            for ev in r["evidence"][:3]:
                lines.append(f"      原文：{ev}")

    _emit("【显式关系】", explicit)
    _emit("【隐含关系（模型推断，附置信度）】", implicit,
          show_conf=True, show_reason=True)
    _emit("【同现关系（仅共现，无明确语义）】", cooc)
    return "\n".join(lines)


_HTML_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
__VIS_JS__
<style>
  :root{
    --bg:#f5f6fa;
    --panel-bg:#fff;
    --panel-shadow:0 2px 12px rgba(0,0,0,.12);
    --text:#333;
    --text-dim:#666;
    --text-faint:#999;
    --accent:#3b4cca;
    --tag-bg:#eef2ff;
    --tag-text:#3b4cca;
    --border:#eee;
    --type-bg:#f0f2f8;
    --type-checked-bg:#dbe4ff;
    --focus-bg:#eef2ff;
    --focus-text:#3b4cca;
    --slider-accent:#6c8cff;
  }
  html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    transition:background .25s,color .25s;}
  #net{width:100%;height:100%;}
  #panel{position:absolute;top:12px;left:12px;background:var(--panel-bg);
    border-radius:10px;padding:12px 16px;box-shadow:var(--panel-shadow);
    font-size:13px;max-width:320px;transition:background .25s,box-shadow .25s;}
  #panel h3{margin:0 0 8px;font-size:14px;}
  #panel span.tag{display:inline-block;margin:2px 6px 2px 0;padding:1px 8px;
    background:var(--tag-bg);border-radius:10px;color:var(--tag-text);
    transition:background .15s;}
  .tag.clickable{cursor:pointer;}
  .tag.clickable:hover{filter:brightness(0.92);}
  .legend{margin-top:10px;padding-top:8px;border-top:1px solid var(--border);
    color:var(--text-dim);}
  .legend i{display:inline-block;width:22px;height:0;vertical-align:middle;
    border-top:2px solid var(--text-faint);margin-right:6px;}
  .legend i.dash{border-top:2px dashed #e8853a;}
  .filter{margin-top:12px;padding-top:10px;border-top:1px solid var(--border);}
  .filter-row{display:flex;justify-content:space-between;align-items:center;
    font-size:13px;color:var(--text);margin-bottom:6px;}
  .filter-row b{color:var(--accent);}
  .filter-row a{color:var(--accent);}
  #confSlider,#connSlider{width:100%;accent-color:var(--slider-accent);}
  .filter-hint{font-size:11px;color:var(--text-faint);margin-top:2px;}
  #focusHint{margin:6px 0;padding:6px 10px;background:var(--focus-bg);
    border-radius:6px;font-size:12px;color:var(--focus-text);}
  .type-list{display:flex;flex-direction:column;gap:2px;margin-top:6px;}
  .type-list label{display:flex;align-items:center;gap:8px;
    font-size:12px;padding:4px 8px;border-radius:6px;
    cursor:pointer;user-select:none;color:var(--text);
    transition:background .15s,opacity .15s;}
  .type-list label:hover{background:var(--type-bg);}
  .type-list label:not(.checked){opacity:0.3;text-decoration:line-through;}
  .type-list input[type=checkbox]{display:none;}
  .type-list .color-dot{width:12px;height:12px;border-radius:50%;
    flex-shrink:0;box-shadow:0 0 0 1px rgba(0,0,0,0.1);}
  .type-list .type-name{flex:1;font-weight:500;}
  .type-list .type-count{color:var(--text-faint);font-size:11px;}
  #themeBox{position:absolute;top:12px;right:12px;background:var(--panel-bg);
    border-radius:10px;padding:8px 12px;box-shadow:var(--panel-shadow);
    display:flex;gap:6px;align-items:center;font-size:12px;
    color:var(--text-dim);transition:background .25s,box-shadow .25s;}
  .theme-swatch{width:22px;height:22px;border-radius:50%;cursor:pointer;
    border:2px solid transparent;transition:all 0.15s;box-sizing:border-box;}
  .theme-swatch:hover{transform:scale(1.15);}
  .theme-swatch.active{border-color:var(--accent);box-shadow:0 0 0 2px var(--panel-bg) inset;}
  #personaBtn{position:absolute;top:58px;right:12px;
    background:var(--panel-bg);color:var(--text);border:none;
    border-radius:10px;padding:8px 14px;box-shadow:var(--panel-shadow);
    font-size:12px;cursor:pointer;display:flex;align-items:center;gap:6px;
    transition:background .25s,box-shadow .25s;font-family:inherit;}
  #personaBtn:hover{filter:brightness(0.96);}
  #personaBtn .pb-icon{font-size:14px;}
  #personaPanel{position:absolute;top:12px;right:12px;width:360px;
    max-height:calc(100% - 24px);background:var(--panel-bg);border-radius:12px;
    box-shadow:var(--panel-shadow);display:none;flex-direction:column;z-index:200;
    transition:background .25s,box-shadow .25s;}
  #personaPanel.show{display:flex;animation:ppSlideIn .2s ease;}
  @keyframes ppSlideIn{from{transform:translateX(20px);opacity:0;}
    to{transform:translateX(0);opacity:1;}}
  .pp-header{display:flex;justify-content:space-between;align-items:center;
    padding:12px 16px;border-bottom:1px solid var(--border);
    font-weight:600;color:var(--text);font-size:14px;}
  .pp-close{cursor:pointer;font-size:22px;color:var(--text-faint);
    line-height:1;padding:0 4px;user-select:none;}
  .pp-close:hover{color:var(--text);}
  .pp-body{flex:1;overflow-y:auto;padding:8px;}
  .pp-item{display:flex;align-items:center;gap:10px;padding:8px 10px;
    border-radius:8px;cursor:pointer;transition:background .15s;}
  .pp-item:hover{background:var(--type-bg);}
  .pp-thumb{width:40px;height:40px;border-radius:50%;object-fit:cover;
    flex-shrink:0;background:var(--tag-bg);}
  .pp-thumb-fallback{display:flex;align-items:center;justify-content:center;
    font-size:16px;color:var(--tag-text);font-weight:600;}
  .pp-info{flex:1;min-width:0;}
  .pp-name{font-size:13px;font-weight:600;color:var(--text);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .pp-summary{font-size:12px;color:var(--text-dim);margin-top:2px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .pp-empty{text-align:center;color:var(--text-faint);padding:30px 20px;font-size:13px;}
  .pp-back{display:inline-block;cursor:pointer;color:var(--accent);
    font-size:12px;margin-bottom:10px;padding:4px 8px;border-radius:6px;}
  .pp-back:hover{background:var(--tag-bg);}
  .pp-detail-header{display:flex;align-items:center;gap:12px;
    margin-bottom:12px;padding:0 4px;}
  .pp-detail-avatar{width:56px;height:56px;border-radius:50%;object-fit:cover;
    background:var(--tag-bg);}
  .pp-detail-name{font-size:16px;font-weight:600;color:var(--text);}
  .pp-detail-meta{font-size:11px;color:var(--text-faint);margin-top:2px;}
  .pp-section{margin-bottom:12px;padding:0 4px;}
  .pp-label{font-size:11px;color:var(--text-faint);margin-bottom:6px;
    letter-spacing:.5px;}
  .pp-tag{display:inline-block;padding:2px 8px;background:var(--tag-bg);
    color:var(--tag-text);border-radius:10px;font-size:12px;margin:2px 4px 2px 0;}
  .pp-text{font-size:13px;color:var(--text);line-height:1.5;}
  .pp-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:12px;}
  .pp-bar-row>span:first-child{width:52px;color:var(--text-dim);flex-shrink:0;}
  .pp-bar-row .pp-bar-val{width:28px;text-align:right;color:var(--text-faint);flex-shrink:0;}
  .pp-bar{flex:1;height:6px;background:var(--type-bg);border-radius:3px;overflow:hidden;}
  .pp-bar>div{height:100%;background:var(--slider-accent);border-radius:3px;}
  .pp-summary-box{font-size:13px;color:var(--text);line-height:1.5;
    padding:8px 10px;background:var(--tag-bg);border-radius:8px;margin-bottom:12px;}
  .cyber-amber-btn{width:100%;padding:10px 14px;border:none;border-radius:8px;
    background:linear-gradient(135deg,#f5a623,#f7b955);color:#fff;
    font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
    transition:all .15s;box-shadow:0 2px 6px rgba(245,166,35,.3);}
  .cyber-amber-btn:hover{transform:translateY(-1px);
    box-shadow:0 4px 10px rgba(245,166,35,.4);}
  .cyber-amber-btn:active{transform:translateY(0);}
  .cyber-amber-btn.copied{background:linear-gradient(135deg,#27ae60,#2ecc71);}
    .cross-badge{display:inline-flex;align-items:center;gap:4px;
    font-size:11px;color:#27ae60;margin-left:8px;
    padding:1px 8px;border-radius:10px;background:rgba(39,174,96,0.1);
    cursor:pointer;transition:background .15s;user-select:none;}
  .cross-badge:hover{background:rgba(39,174,96,0.22);}
  .cross-badge::before{content:'';width:6px;height:6px;border-radius:50%;
    background:#27ae60;flex-shrink:0;}

  .cross-menu{position:fixed;z-index:99999;background:#fff;border-radius:10px;
    box-shadow:0 8px 24px rgba(0,0,0,.18);padding:6px 0;min-width:260px;
    display:none;font-size:13px;color:#333;overflow:hidden;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}
  .cross-menu.show{display:block;animation:cmFadeIn .15s ease;}
  @keyframes cmFadeIn{from{opacity:0;transform:translateY(-4px);}
    to{opacity:1;transform:translateY(0);}}
  .cross-menu-title{padding:8px 14px;font-size:11px;color:#999;
    border-bottom:1px solid #f0f0f0;margin-bottom:4px;}
  .cross-menu-item{padding:10px 14px;cursor:pointer;transition:background .12s;
    display:flex;justify-content:space-between;align-items:center;gap:8px;}
  .cross-menu-item:hover{background:#f5f6fa;}
  .cross-menu-item.current{opacity:0.55;cursor:default;}
  .cross-menu-item.current:hover{background:transparent;}
  .cross-menu-left{flex:1;min-width:0;}
  .cross-menu-gname{font-size:12px;font-weight:600;color:#333;}
  .cross-menu-lname{font-size:11px;color:#999;margin-top:2px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .cross-menu-arrow{color:#3b4cca;font-size:11px;flex-shrink:0;}
</style>
</head>
<body>
<div id="net"></div>
<div id="themeBox">
  <span style="margin-right:4px;">主题</span>
  <div class="theme-swatch" data-theme="light" style="background:#f5f6fa;border-color:#ddd;"></div>
  <div class="theme-swatch" data-theme="dark"  style="background:#1a1d24;border-color:#444;"></div>
  <div class="theme-swatch" data-theme="sepia" style="background:#f4ecd8;border-color:#c8b894;"></div>
  <div class="theme-swatch" data-theme="forest" style="background:#e8f0e8;border-color:#8fa88f;"></div>
  <div class="theme-swatch" data-theme="sakura" style="background:#fdf0f5;border-color:#e8b8c8;"></div>
</div>

<button id="personaBtn" title="查看人物档案">
  <span class="pb-icon">📋</span>
  <span>人物档案</span>
</button>

<div id="personaPanel">
  <div class="pp-header">
    <span>人物档案</span>
    <span class="pp-close" onclick="closePersonaPanel()">×</span>
  </div>
  <div class="pp-body" id="ppBody"></div>
</div>

<div id="panel">
  <h3>人物（<span id="npCount">__NP__</span>）</h3>
  <div id="focusHint" style="display:none;margin:6px 0;padding:6px 10px;
     background:#eef2ff;border-radius:6px;font-size:12px;color:#3b4cca;"></div>
  <div id="tags"></div>

  <div class="filter">
    <div class="filter-row">
      <span>关系这么好？ ≥ <b id="confVal">0.00</b></span>
      <span id="edgeCount" style="color:#999;font-size:12px;"></span>
    </div>
    <input type="range" id="confSlider" min="0" max="100" value="0" step="5">
    <div class="filter-hint">← 越靠右，越贴贴</div>
  </div>

  <div class="filter">
    <div class="filter-row">
      <span>广交好友？ ≥ <b id="connVal">0</b></span>
      <span id="nodeCount" style="color:#999;font-size:12px;"></span>
    </div>
    <input type="range" id="connSlider" min="0" max="10" value="0" step="1">
    <div class="filter-hint">← 向右拖，看群宠</div>
  </div>

  <div class="filter">
    <div class="filter-row">
      <span>关系图例</span>
      <span style="font-size:12px;">
        <a href="javascript:void(0)" onclick="toggleAllTypes(true)" style="color:#3b4cca;">全选</a>
        <span style="color:#ccc;"> | </span>
        <a href="javascript:void(0)" onclick="toggleAllTypes(false)" style="color:#3b4cca;">全不选</a>
      </span>
    </div>
    <div id="typeCheckboxes" class="type-list"></div>
    <div class="filter-hint">颜色 = 关系类型 · 越粗越铁 · 虚线 = AI 推断</div>
  </div>

  <div class="legend">
    <div><i></i>显式关系</div>
    <div><i class="dash"></i>隐含关系（悬停查看置信度与依据）</div>
  </div>
</div>
<script>
var nodesData = __NODES__;
var edgesData = __EDGES__;
var personasData = __PERSONAS__;
var crossMap = __CROSS_MAP__;
var container = document.getElementById('net');

// 关系类型 → 固定颜色（同一类型永远同色）
var REL_PALETTE = [
  '#e74c3c', '#27ae60', '#3498db', '#9b59b6', '#f39c12',
  '#16a085', '#e91e63', '#2c3e50', '#d35400', '#8e44ad',
  '#1abc9c', '#c0392b', '#2980b9', '#f1c40f', '#7f8c8d'
];

function colorForRelation(rel) {
  var hash = 0;
  for (var i = 0; i < rel.length; i++) {
    hash = ((hash << 5) - hash + rel.charCodeAt(i)) | 0;
  }
  return REL_PALETTE[Math.abs(hash) % REL_PALETTE.length];
}

function buildNode(n) {
  var t = THEMES[currentTheme].graph;
  var node = {
    id: n.id, label: n.label, value: n.value,
    shape: n.shape || 'dot',
    scaling: {min:12, max:45},
    font: {size:16, face:'PingFang SC, Microsoft YaHei', color:t.nodeFont,
           strokeWidth: currentTheme === 'dark' ? 3 : 2,
           strokeColor: THEMES[currentTheme].vars['--bg']},
    color: {background:t.nodeBg, border:t.nodeBorder}
  };
  if (n.image) {
    node.image = n.image;
    node.brokenImage = t.nodeBg;
  }
  return node;
}

function buildEdge(e) {
  var t = THEMES[currentTheme].graph;
  var rel = e.relation || '未知';
  var conf = (e.confidence === undefined) ? 1.0 : e.confidence;
  var isImplicit = e.dashes === true;

  // 粗细：低置信度 0.5px，高置信度 3.3px
  var width = 0.5 + Math.max(0, conf - 0.3) * 4.0;

  return {
    from: e.from,
    to: e.to,
    label: e.label,
    title: e.title,
    arrows: {to: {enabled: true, scaleFactor: 0.5}},
    arrowStrikethrough: false,
    dashes: isImplicit ? [12, 8] : false,
    width: width,
    color: {color: colorForRelation(rel), highlight: t.edgeHighlight},
    font: {
      size: 11,
      align: 'middle',
      color: t.edgeFont,
      strokeWidth: 0.5,
      strokeColor: THEMES[currentTheme].vars['--bg']
    },
    smooth: {type: 'continuous'}
  };
}


var THEMES = {
  light: {
    vars: {
      '--bg':'#f5f6fa','--panel-bg':'#fff','--panel-shadow':'0 2px 12px rgba(0,0,0,.12)',
      '--text':'#333','--text-dim':'#666','--text-faint':'#999',
      '--accent':'#3b4cca','--tag-bg':'#eef2ff','--tag-text':'#3b4cca','--border':'#eee',
      '--type-bg':'#f0f2f8','--type-checked-bg':'#dbe4ff',
      '--focus-bg':'#eef2ff','--focus-text':'#3b4cca','--slider-accent':'#6c8cff'
    },
    graph: {
      nodeBg:'#6c8cff', nodeBorder:'#3b4cca', nodeFont:'#333',
      edgeExplicit:'#b8c2d8', edgeImplicit:'#e8853a', edgeCooccur:'#c8ccd6',
      edgeFont:'#555', edgeHighlight:'#3b4cca'
    }
  },
  dark: {
    vars: {
      '--bg':'#1a1d24','--panel-bg':'#242832','--panel-shadow':'0 2px 12px rgba(0,0,0,.5)',
      '--text':'#e4e6eb','--text-dim':'#9aa0b0','--text-faint':'#6b7280',
      '--accent':'#8fa5ff','--tag-bg':'#2a3454','--tag-text':'#8fa5ff','--border':'#323848',
      '--type-bg':'#2a2f3d','--type-checked-bg':'#354066',
      '--focus-bg':'#2a3454','--focus-text':'#8fa5ff','--slider-accent':'#8fa5ff'
    },
    graph: {
      nodeBg:'#4a6cf7', nodeBorder:'#8fa5ff', nodeFont:'#e4e6eb',
      edgeExplicit:'#4a5570', edgeImplicit:'#f0a050', edgeCooccur:'#3a4050',
      edgeFont:'#9aa0b0', edgeHighlight:'#8fa5ff'
    }
  },
  sepia: {
    vars: {
      '--bg':'#f4ecd8','--panel-bg':'#fbf6e8','--panel-shadow':'0 2px 12px rgba(120,90,50,.15)',
      '--text':'#4a3820','--text-dim':'#7a6548','--text-faint':'#a89878',
      '--accent':'#8b5a2b','--tag-bg':'#ede0c4','--tag-text':'#8b5a2b','--border':'#e0d2b0',
      '--type-bg':'#ede0c4','--type-checked-bg':'#e0cba0',
      '--focus-bg':'#ede0c4','--focus-text':'#8b5a2b','--slider-accent':'#b8804a'
    },
    graph: {
      nodeBg:'#c89a5a', nodeBorder:'#8b5a2b', nodeFont:'#4a3820',
      edgeExplicit:'#c8b894', edgeImplicit:'#d4783a', edgeCooccur:'#d8c8a8',
      edgeFont:'#7a6548', edgeHighlight:'#8b5a2b'
    }
  },
  forest: {
    vars: {
      '--bg':'#e8f0e8','--panel-bg':'#f3f8f3','--panel-shadow':'0 2px 12px rgba(40,80,40,.12)',
      '--text':'#2a3d2a','--text-dim':'#5a6d5a','--text-faint':'#8a9d8a',
      '--accent':'#3d6b3d','--tag-bg':'#d8e8d8','--tag-text':'#3d6b3d','--border':'#c8d8c8',
      '--type-bg':'#d8e8d8','--type-checked-bg':'#c0d8c0',
      '--focus-bg':'#d8e8d8','--focus-text':'#3d6b3d','--slider-accent':'#5a9a5a'
    },
    graph: {
      nodeBg:'#5a9a5a', nodeBorder:'#3d6b3d', nodeFont:'#2a3d2a',
      edgeExplicit:'#a8b8a8', edgeImplicit:'#c8903a', edgeCooccur:'#b8c8b8',
      edgeFont:'#5a6d5a', edgeHighlight:'#3d6b3d'
    }
  },
  sakura: {
    vars: {
      '--bg':'#fdf0f5','--panel-bg':'#fff7fa','--panel-shadow':'0 2px 12px rgba(180,100,140,.15)',
      '--text':'#5a3a48','--text-dim':'#8a6a78','--text-faint':'#b8a0a8',
      '--accent':'#c85a8a','--tag-bg':'#fce4ee','--tag-text':'#c85a8a','--border':'#f0d8e0',
      '--type-bg':'#fce4ee','--type-checked-bg':'#f8c8dc',
      '--focus-bg':'#fce4ee','--focus-text':'#c85a8a','--slider-accent':'#e878a8'
    },
    graph: {
      nodeBg:'#e878a8', nodeBorder:'#c85a8a', nodeFont:'#5a3a48',
      edgeExplicit:'#e8c0d0', edgeImplicit:'#d8903a', edgeCooccur:'#e8d0d8',
      edgeFont:'#8a6a78', edgeHighlight:'#c85a8a'
    }
  }
};

var currentTheme = 'light';
var focusNode = null;

// 收集所有关系类型及出现次数
var allTypes = {};
edgesData.forEach(function(e){
  var t = e.relation;
  allTypes[t] = (allTypes[t] || 0) + 1;
});

// 当前选中的类型集合
var selectedTypes = new Set(Object.keys(allTypes));

var nodes = new vis.DataSet([]);
var edges = new vis.DataSet([]);

function applyFilter(minConf, minConn) {
  if (minConn === undefined) minConn = 0;

  var keptEdges = edgesData.filter(function(e){
    var c = (e.confidence === undefined) ? 1.0 : e.confidence;
    return c >= minConf;
  });

  keptEdges = keptEdges.filter(function(e){
    var t = e.relation || e.label;
    return selectedTypes.has(t);
  });

  if (focusNode) {
    keptEdges = keptEdges.filter(function(e){
      return e.from === focusNode || e.to === focusNode;
    });
  }

  var connCount = {};
  keptEdges.forEach(function(e){
    connCount[e.from] = (connCount[e.from] || 0) + 1;
    connCount[e.to] = (connCount[e.to] || 0) + 1;
  });

  var usedIds = {};
  keptEdges.forEach(function(e){
    if ((connCount[e.from] || 0) >= minConn) usedIds[e.from] = true;
    if ((connCount[e.to] || 0) >= minConn) usedIds[e.to] = true;
  });

  if (focusNode) {
    usedIds[focusNode] = true;
  }

  var keptNodes = nodesData.filter(function(n){
    return usedIds[n.id];
  });

  keptEdges = keptEdges.filter(function(e){
    return usedIds[e.from] && usedIds[e.to];
  });

  nodes.clear();
  nodes.add(keptNodes.map(buildNode));
  edges.clear();
  edges.add(keptEdges.map(buildEdge));

  var tagsBox = document.getElementById('tags');
  tagsBox.innerHTML = '';
  keptNodes.forEach(function(n){
    var span = document.createElement('span');
    span.className = 'tag clickable';
    span.textContent = n.id + ' ×' + n.value;
    span.onclick = function(){ goToNode(n.id); };
    tagsBox.appendChild(span);
  });

  document.getElementById('npCount').textContent = keptNodes.length;
  document.getElementById('edgeCount').textContent =
    keptEdges.length + ' / ' + edgesData.length + ' 条关系';
  document.getElementById('nodeCount').textContent =
    keptNodes.length + ' / ' + nodesData.length + ' 人';
}

function clearFocus() {
  focusNode = null;
  var confSlider = document.getElementById('confSlider');
  applyFilter(confSlider.value / 100);
}

function focusOn(name) {
  focusNode = name;
  var confSlider = document.getElementById('confSlider');
  applyFilter(confSlider.value / 100);
}

// 初始化 network（edges.scaling 关掉，让 buildEdge 的 width 生效）
var network = new vis.Network(container, {nodes:nodes, edges:edges}, {
  physics:{
    solver:'barnesHut',
    barnesHut:{
      gravitationalConstant:-1200,
      centralGravity:0.6,
      springLength:120,
      springConstant:0.06,
      damping:0.35,
      avoidOverlap:0.5,
      theta:1.0
    },
    stabilization:{enabled:true, iterations:200, updateInterval:25},
    minVelocity:0.05,
    maxVelocity:30,
    timestep:0.35,
    adaptiveTimestep:true
  },
  interaction:{hover:true, tooltipDelay:120},
  edges:{
    scaling:{min:1, max:1}
  }
});

var confSlider = document.getElementById('confSlider');
confSlider.addEventListener('input', function(){
  var v = this.value / 100;
  document.getElementById('confVal').textContent = v.toFixed(2);
  applyFilter(v, parseInt(connSlider.value));
});

var connSlider = document.getElementById('connSlider');
connSlider.addEventListener('input', function(){
  var v = parseInt(this.value);
  document.getElementById('connVal').textContent = v;
  applyFilter(confSlider.value / 100, v);
});

(function buildTypeCheckboxes() {
  var box = document.getElementById('typeCheckboxes');
  var types = Object.keys(allTypes).sort(function(a, b){
    return allTypes[b] - allTypes[a];
  });

  types.forEach(function(t){
    var color = colorForRelation(t);

    var label = document.createElement('label');
    label.className = 'checked';

    var dot = document.createElement('span');
    dot.className = 'color-dot';
    dot.style.background = color;

    var name = document.createElement('span');
    name.className = 'type-name';
    name.textContent = t;

    var count = document.createElement('span');
    count.className = 'type-count';
    count.textContent = allTypes[t] + ' 条';

    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.dataset.type = t;

    label.appendChild(dot);
    label.appendChild(cb);
    label.appendChild(name);
    label.appendChild(count);

    label.onclick = function(e) {
      e.preventDefault();
      var now = !cb.checked;
      cb.checked = now;
      if (now) {
        selectedTypes.add(t);
        label.classList.add('checked');
      } else {
        selectedTypes.delete(t);
        label.classList.remove('checked');
      }
      applyFilter(confSlider.value / 100, parseInt(connSlider.value));
    };

    box.appendChild(label);
  });
})();

function toggleAllTypes(checked) {
  document.querySelectorAll('#typeCheckboxes input[type=checkbox]').forEach(function(cb){
    cb.checked = checked;
    var label = cb.parentElement;
    if (checked) {
      selectedTypes.add(cb.dataset.type);
      label.classList.add('checked');
    } else {
      selectedTypes.delete(cb.dataset.type);
      label.classList.remove('checked');
    }
  });
  applyFilter(confSlider.value / 100, parseInt(connSlider.value));
}

function setTheme(name) {
  if (!THEMES[name]) return;
  currentTheme = name;
  var vars = THEMES[name].vars;
  Object.keys(vars).forEach(function(k){
    document.documentElement.style.setProperty(k, vars[k]);
  });
  document.querySelectorAll('.theme-swatch').forEach(function(el){
    el.classList.toggle('active', el.dataset.theme === name);
  });
  applyFilter(confSlider.value / 100, parseInt(connSlider.value));
  try { localStorage.setItem('qq_rel_theme', name); } catch(e) {}
}

document.querySelectorAll('.theme-swatch').forEach(function(el){
  el.addEventListener('click', function(){
    setTheme(this.dataset.theme);
  });
});

try {
  var saved = localStorage.getItem('qq_rel_theme');
  if (saved && THEMES[saved]) setTheme(saved);
  else setTheme('light');
} catch(e) { setTheme('light'); }

applyFilter(0, 0);

function goToNode(nodeId) {
  var currentScale = network.getScale();
  var targetScale = Math.min(currentScale * 1.5, 2.0);
  network.focus(nodeId, {
    scale: targetScale,
    animation: {duration: 500, easingFunction: 'easeInOutQuad'}
  });
  network.selectNodes([nodeId]);
}

network.on('click', function(params){
  if (params.nodes.length > 0) {
    focusOn(params.nodes[0]);
  } else {
    clearFocus();
  }
});
// ========== 人物档案 ==========

function esc(s){
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

function openPersonaPanel(){
  document.getElementById('personaPanel').classList.add('show');
  renderPersonaList();
}
function closePersonaPanel(){
  document.getElementById('personaPanel').classList.remove('show');
}
document.getElementById('personaBtn').addEventListener('click', openPersonaPanel);

function renderPersonaList(){
  var body = document.getElementById('ppBody');
  var names = Object.keys(personasData).sort();
  if (!names.length){
    body.innerHTML = '<div class="pp-empty">暂无人物档案<br>'
      + '<span style="font-size:11px;">（样本不足或还没分析）</span></div>';
    return;
  }
  var html = '';
  names.forEach(function(name){
    var p = personasData[name];
    var avatar = '';
    var nd = nodesData.find(function(n){ return n.id === name; });
    if (nd && nd.image) avatar = nd.image;

    html += '<div class="pp-item" data-name="' + esc(name) + '">';
    if (avatar){
      html += '<img class="pp-thumb" src="' + avatar + '" alt="">';
    } else {
      html += '<div class="pp-thumb pp-thumb-fallback">' + esc(name.charAt(0)) + '</div>';
    }
    html += '<div class="pp-info">';
    html += '<div class="pp-name">' + esc(name) + '</div>';
    if (p.summary) html += '<div class="pp-summary">' + esc(p.summary) + '</div>';
    html += '</div></div>';
  });


  body.innerHTML = html;

  body.querySelectorAll('.pp-item').forEach(function(el){
    el.addEventListener('click', function(){
      showPersonaDetail(this.dataset.name);
    });
  });
}

function showPersonaDetail(name){
  var p = personasData[name];
  if (!p) return;
  var body = document.getElementById('ppBody');

  var avatar = '';
  var nd = nodesData.find(function(n){ return n.id === name; });
  if (nd && nd.image) avatar = nd.image;

  var html = '';
  html += '<div class="pp-back" onclick="renderPersonaList()">← 返回列表</div>';
  html += '<div class="pp-detail-header">';
  if (avatar) html += '<img class="pp-detail-avatar" src="' + avatar + '" alt="">';
  html += '<div>';
  html += '<div class="pp-detail-name">' + esc(name);
  var cross = crossMap[name];
  if (cross && cross.groups && cross.groups.length > 1) {
    html += '<span class="cross-badge" data-cn="' + esc(name)
         +  '" onclick="showCrossMenu(this, this.dataset.cn)">活跃于 '
         +  cross.groups.length + ' 个群</span>';
  }
  html += '</div>';
  var meta = [];
  if (p._msg_count) meta.push('样本 ' + p._msg_count + ' 条');
  if (p.confidence != null) meta.push('置信度 ' + p.confidence);
  if (meta.length) html += '<div class="pp-detail-meta">' + meta.join(' · ') + '</div>';
  html += '</div></div>';

  if (p.summary){
    html += '<div class="pp-summary-box">' + esc(p.summary) + '</div>';
  }

  if (p.traits && p.traits.length){
    html += '<div class="pp-section"><div class="pp-label">特质</div>';
    p.traits.forEach(function(t){
      if (typeof t === 'string') {
        html += '<span class="pp-tag">' + esc(t) + '</span>';
      } else {
        html += '<div style="margin-bottom:8px;">'
             +  '<div style="font-size:13px;color:var(--text);font-weight:600;">'
             +  esc(t.trait) + '</div>'
             +  '<div style="font-size:11px;color:var(--text-dim);'
             +  'margin-top:2px;font-style:italic;">'
             +  '「' + esc(t.evidence) + '」</div>'
             +  '</div>';
      }
    });
    html += '</div>';
  }

  if (p.big_five){
    html += '<div class="pp-section"><div class="pp-label">大五人格</div>';
    var L = {openness:'开放性', conscientiousness:'尽责性', extraversion:'外向性',
             agreeableness:'宜人性', neuroticism:'神经质'};
    Object.keys(L).forEach(function(k){
      var v = p.big_five[k] || 0;
      html += '<div class="pp-bar-row"><span>' + L[k] + '</span>'
            + '<div class="pp-bar"><div style="width:' + v + '%"></div></div>'
            + '<span class="pp-bar-val">' + v + '</span></div>';
    });
    html += '</div>';
  }

  if (p.language_style){
    html += '<div class="pp-section"><div class="pp-label">语言风格</div>'
          + '<div class="pp-text">' + esc(p.language_style) + '</div></div>';
  }

  if (p.catchphrases && p.catchphrases.length){
    html += '<div class="pp-section"><div class="pp-label">口头禅</div>';
    p.catchphrases.forEach(function(x){ html += '<span class="pp-tag">' + esc(x) + '</span>'; });
    html += '</div>';
  }

  if (p.interests && p.interests.length){
    html += '<div class="pp-section"><div class="pp-label">兴趣</div>';
    p.interests.forEach(function(x){ html += '<span class="pp-tag">' + esc(x) + '</span>'; });
    html += '</div>';
  }

  if (p.social_role){
    html += '<div class="pp-section"><div class="pp-label">群内角色</div>'
          + '<div class="pp-text">' + esc(p.social_role) + '</div></div>';
  }

  if (p.emotion_baseline){
    html += '<div class="pp-section"><div class="pp-label">情绪基调</div>'
          + '<div class="pp-text">' + esc(p.emotion_baseline) + '</div></div>';
  }

  if (p.caveats){
    html += '<div class="pp-section"><div class="pp-label">备注</div>'
          + '<div class="pp-text">' + esc(p.caveats) + '</div></div>';
  }

  html += '<div style="margin-top:20px;padding-top:12px;border-top:1px solid var(--border);">'
       +  '<button class="cyber-amber-btn" data-copy-name="' + esc(name) + '" '
       +  'onclick="copyPersona(this)">📋 赛博琥珀</button>'
       +  '</div>';

  body.innerHTML = html;
}
function buildPersonaPrompt(name){
  var p = personasData[name];
  if (!p) return null;

  var L = [];
  L.push('你现在扮演一个QQ群里的群友，昵称叫「' + name + '」。');
  L.push('');
  L.push('请始终用第一人称，像真人一样在群里聊天。不要暴露你是AI。');
  L.push('');

  if (p.summary) {
    L.push('## 你的人设');
    L.push(p.summary);
    L.push('');
  }

  if (p.traits && p.traits.length) {
    L.push('## 性格特点');
    p.traits.forEach(function(t){
      if (typeof t === 'string') {
        L.push('- ' + t);
      } else if (t && t.trait) {
        var line = '- ' + t.trait;
        if (t.evidence) line += '（例如：' + t.evidence + '）';
        L.push(line);
      }
    });
    L.push('');
  }

  if (p.big_five) {
    L.push('## 人格数值（0~100）');
    var B = {openness:'开放性', conscientiousness:'尽责性', extraversion:'外向性',
             agreeableness:'宜人性', neuroticism:'神经质'};
    Object.keys(B).forEach(function(k){
      if (p.big_five[k] != null) L.push('- ' + B[k] + '：' + p.big_five[k]);
    });
    if (p.big_five_reason) L.push('（依据：' + p.big_five_reason + '）');
    L.push('');
  }

  if (p.language_style) {
    L.push('## 语言风格');
    L.push(p.language_style);
    L.push('');
  }

  if (p.catchphrases && p.catchphrases.length) {
    L.push('## 你的口头禅');
    L.push(p.catchphrases.join('、'));
    L.push('');
  }

  if (p.interests && p.interests.length) {
    L.push('## 你常聊的话题');
    L.push(p.interests.join('、'));
    L.push('');
  }

  if (p.social_role) {
    L.push('## 你在群里的角色');
    L.push(p.social_role);
    L.push('');
  }

  if (p.emotion_baseline) {
    L.push('## 你的情绪基调');
    L.push(p.emotion_baseline);
    L.push('');
  }

  L.push('---');
  L.push('请始终保持这个人的语气、用词、说话习惯和性格。');
  L.push('像一个真正的群友那样自然地聊天，别太正经。');

  return L.join('\\n');
}

function showCrossMenu(anchorEl, currentName){
  var cross = crossMap[currentName];
  if (!cross) return;

  var old = document.getElementById('crossMenu');
  if (old) old.remove();

  var menu = document.createElement('div');
  menu.id = 'crossMenu';
  menu.className = 'cross-menu';

  var html = '<div class="cross-menu-title">在 ' + cross.groups.length
           + ' 个群中出现</div>';
  cross.groups.forEach(function(g){
    var cls = g.is_local ? 'cross-menu-item current' : 'cross-menu-item';
    var arrow = g.is_local ? '当前群' : '打开 →';
    html += '<div class="' + cls + '" data-gid="' + g.gid + '">'
         +  '<div class="cross-menu-left">'
         +  '<div class="cross-menu-gname">群 ' + g.gid + '</div>';
    if (g.name !== currentName) {
      html += '<div class="cross-menu-lname">那里他叫：'
           +  esc(g.name) + '</div>';
    }
    html += '</div>'
         +  '<div class="cross-menu-arrow">' + arrow + '</div>'
         +  '</div>';
  });
  menu.innerHTML = html;

  document.body.appendChild(menu);

  var rect = anchorEl.getBoundingClientRect();
  var left = Math.min(rect.left, window.innerWidth - 280);
  var top = rect.bottom + 6;
  if (top + 300 > window.innerHeight) {
    top = rect.top - menu.offsetHeight - 6;
    if (top < 0) top = 10;
  }
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
  menu.classList.add('show');

  menu.querySelectorAll('.cross-menu-item').forEach(function(el){
    if (el.classList.contains('current')) return;
    el.addEventListener('click', function(e){
      e.stopPropagation();
      window.open('g' + this.dataset.gid + '.html', '_blank');
      closeCrossMenu();
    });
  });

  setTimeout(function(){
    document.addEventListener('click', _outsideClose);
  }, 0);
}

function closeCrossMenu(){
  var m = document.getElementById('crossMenu');
  if (m) m.remove();
  document.removeEventListener('click', _outsideClose);
}

function _outsideClose(e){
  var m = document.getElementById('crossMenu');
  if (m && !m.contains(e.target)) {
    closeCrossMenu();
  }
}

function copyPersona(btn){
  var name = btn.dataset.copyName;
  var text = buildPersonaPrompt(name);
  if (!text) { alert('没有找到 ' + name + ' 的画像数据'); return; }

  function onDone(){
    var old = btn.innerHTML;
    btn.innerHTML = '✓ 已复制';
    btn.classList.add('copied');
    setTimeout(function(){
      btn.innerHTML = old;
      btn.classList.remove('copied');
    }, 1500);
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onDone).catch(function(){
      fallbackCopy(text, onDone);
    });
  } else {
    fallbackCopy(text, onDone);
  }
}

function fallbackCopy(text, onDone){
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    onDone();
  } catch(e) {
    alert('复制失败：' + e);
  }
  document.body.removeChild(ta);
}
</script>
</body>
</html>
"""


def _ensure_vis_network(static_dir) -> Path:
    """确保 vis-network.min.js 下载到本地，返回其路径。首次调用时会下载。"""
    import urllib.request
    d = Path(static_dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "vis-network.min.js"
    if not f.exists() or f.stat().st_size < 10000:
        url = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            f.write_bytes(resp.read())
    return f


def _safe_json(obj) -> str:
    r"""
    把 Python 对象序列化成可以直接嵌到 <script> 里的 JSON 字符串。
    - ensure_ascii=True：所有非 ASCII 字符转义成 \uXXXX
      （修 U+2028/U+2029 导致的 JS SyntaxError）
    - 替换 </ 为 <\/：防止 </script> 提前闭合
    """
    s = json.dumps(obj, ensure_ascii=True)
    s = s.replace("</", "<\\/")
    return s


def to_html(result: dict, title: str = "人物关系图",
            avatar_map: dict = None, personas: dict = None,
            cross_map: dict = None,
            embed: bool = False, static_dir: str = "data/static") -> str:
    avatar_map = avatar_map or {}
    personas = personas or {}
    cross_map = cross_map or {}

    import base64

    if embed:
        vjs_path = _ensure_vis_network(static_dir)
        vjs_content = vjs_path.read_text(encoding="utf-8")
        vis_script = f"<script>{vjs_content}</script>"
    else:
        vis_script = ('<script src="https://unpkg.com/'
                      'vis-network/standalone/umd/vis-network.min.js"></script>')
    avatars_dir = Path(static_dir).parent / "avatars"

    nodes = []
    for p in result["persons"]:
        node = {"id": p["name"], "label": "", "value": p["count"], "title": p["name"]}
        img = avatar_map.get(p["name"])
        if img:
            if embed:
                actual = avatars_dir / Path(img).name
                if actual.exists():
                    data = actual.read_bytes()
                    b64 = base64.b64encode(data).decode("ascii")
                    node["image"] = f"data:image/jpeg;base64,{b64}"
                else:
                    node["image"] = img
            else:
                node["image"] = img
            node["shape"] = "circularImage"
        else:
            node["shape"] = "dot"
            node["label"] = p["name"]
        nodes.append(node)

    edges = []
    for r in result["relations"]:
        is_implicit = r.get("type") == "implicit"
        is_cooccur = r.get("type") == "cooccur" or r["relation"] == "同现"

        parts = []
        if is_implicit:
            parts.append(f"置信度 {r.get('confidence', 0):.2f}")
            if r.get("reason"):
                parts.append(r["reason"])
        parts.extend(r.get("evidence", [])[:2])
        title_txt = " / ".join(parts) if parts else r["relation"]
        # 清洗：去掉换行和回车，防止破坏 JS 字符串
        title_txt = (title_txt
                     .replace("\r\n", " ")
                     .replace("\n", " ")
                     .replace("\r", " "))

        if is_implicit:
            color, dashes = "#e8853a", True
        elif is_cooccur:
            color, dashes = "#c8ccd6", True
        else:
            color, dashes = "#b8c2d8", False

        edges.append({
            "from": r["source"],
            "to": r["target"],
            "label": r["relation"] if r.get("confidence", 0) >= 0.5 else "",
            "confidence": float(r.get("confidence", 1.0)),
            "title": title_txt,
            "dashes": dashes,
            "color": color,
            "relation": r["relation"],
        })

    return (_HTML_TPL
            .replace("__TITLE__", title)
            .replace("__VIS_JS__", vis_script)
            .replace("__NODES__", _safe_json(nodes))
            .replace("__EDGES__", _safe_json(edges))
            .replace("__PERSONAS__", _safe_json(personas))
            .replace("__CROSS_MAP__", _safe_json(cross_map))
            .replace("__NP__", str(len(nodes))))


# =====================================================================
# 八、插件入口（给宿主程序 / dsh 之类调用）
# =====================================================================

def plugin_entry(payload: dict) -> dict:
    try:
        text = (payload or {}).get("text", "") or ""
        if not text.strip():
            return {"ok": False, "error": "text 不能为空"}

        if payload.get("mode") == "llm":
            api_key = (payload.get("api_key")
                       or os.environ.get("DEEPSEEK_API_KEY")
                       or os.environ.get("OPENAI_API_KEY"))
            if not api_key:
                return {"ok": False, "error": "缺少 api_key"}
            data = llm_extract(
                text,
                api_key=api_key,
                base_url=payload.get("base_url", "https://api.deepseek.com"),
                model=payload.get("model", "deepseek-chat"),
                infer=payload.get("infer", True),
                temperature=payload.get("temperature", 0.2),
            )
        else:
            data = analyze(text, cooccur=payload.get("cooccur", True))

        return {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def async_plugin_entry(payload: dict) -> dict:
    return plugin_entry(payload)


# =====================================================================
# 九、命令行
# =====================================================================

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="人物关系抽取器：输入文本，输出人物关系（显式 + 隐含）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='示例：python person_relation.py "张三是李四的父亲，李四和王五是好朋友。"',
    )
    ap.add_argument("text", nargs="?", help="要分析的文本")
    ap.add_argument("-f", "--file", help="从文件读取文本")
    ap.add_argument("--format", choices=["text", "json", "html"], default="text")
    ap.add_argument("-o", "--output", help="输出文件路径")

    ap.add_argument("--no-cooccur", action="store_true",
                    help="不输出'同现'关系")
    ap.add_argument("--cooccur-min", type=int, default=2,
                    help="同现关系最少出现次数（默认 2）")

    ap.add_argument("--llm", action="store_true",
                    help="使用大模型抽取（需要 API Key）")
    ap.add_argument("--no-infer", action="store_true",
                    help="关闭隐含关系推断，只做显式抽取")
    ap.add_argument("--temperature", type=float, default=0.2,
                    help="大模型采样温度（默认 0.2）")
    ap.add_argument("--api-key",
                    default=os.environ.get("DEEPSEEK_API_KEY")
                    or os.environ.get("OPENAI_API_KEY"))
    ap.add_argument("--base-url",
                    default=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"))
    ap.add_argument("--model",
                    default=os.environ.get("LLM_MODEL", "deepseek-chat"))

    args = ap.parse_args(argv)

    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    elif args.text:
        text = args.text
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        ap.print_help()
        return 1

    if args.llm:
        if not args.api_key:
            print("错误：使用 --llm 需要提供 --api-key，或设置 DEEPSEEK_API_KEY 环境变量",
                  file=sys.stderr)
            return 2
        try:
            result = llm_extract(
                text,
                api_key=args.api_key,
                base_url=args.base_url,
                model=args.model,
                infer=not args.no_infer,
                temperature=args.temperature,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"大模型调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 3
    else:
        result = analyze(text,
                         cooccur=not args.no_cooccur,
                         cooccur_min=args.cooccur_min)

    if args.format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
    elif args.format == "html":
        content = to_html(result)
    else:
        content = to_text(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"已写入 {args.output}")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())