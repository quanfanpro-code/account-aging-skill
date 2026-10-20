# -*- coding: utf-8 -*-
"""归一引擎：只处理名字本身，不读写任何文件。"""
import difflib
import re
import unicodedata

# 表头残留的编码前缀，如 YF0001_、YCL0078_
编码前缀 = re.compile(r"^[A-Z]+\d+_")
# 摘要解析等带进来的业务残渣前缀（NFKC 归一后括号均为半角）
残渣前缀 = ("入库(", "冲销", "暂估", "003号凭证(")
# 公司后缀词，比较相似度前先去掉
后缀词 = ("股份有限公司", "有限责任公司", "有限公司", "(个体工商户)", "分公司")


def 清洗名称(名称):
    """洗掉格式差异：全半角、空格、编码前缀、业务残渣前缀。"""
    s = unicodedata.normalize("NFKC", str(名称))
    s = re.sub(r"\s+", "", s)
    s = 编码前缀.sub("", s)
    去掉了一个 = True
    while 去掉了一个:
        去掉了一个 = False
        for 前缀 in 残渣前缀:
            if s.startswith(前缀):
                s = s[len(前缀):]
                去掉了一个 = True
    return s


def _去后缀(s):
    for 后缀 in sorted(后缀词, key=len, reverse=True):
        if s.endswith(后缀):
            return s[: -len(后缀)]
    return s


def 配对候选(名称们):
    """把洗完还像的名字配成对：(名称甲, 名称乙, 差异说明)。"""
    对 = []
    洗 = [(原, 清洗名称(原)) for 原 in 名称们]
    for i in range(len(洗)):
        for j in range(i + 1, len(洗)):
            原甲, 甲 = 洗[i]
            原乙, 乙 = 洗[j]
            if not 甲 or not 乙:
                continue
            甲去, 乙去 = _去后缀(甲), _去后缀(乙)
            包含 = 甲去 and 乙去 and (甲去 in 乙去 or 乙去 in 甲去)
            if 包含:
                if 甲[:2] == 乙[:2]:
                    说明 = "一方包含另一方（疑似简称，可自动归一）"
                else:
                    说明 = "一方包含另一方但开头不同（须人工确认）"
            else:
                if difflib.SequenceMatcher(None, 甲去, 乙去).ratio() < 0.6:
                    continue
                if 甲[:2] != 乙[:2]:
                    说明 = "地名开头不同（须人工确认，防误并不同公司）"
                else:
                    说明 = "高度相似（疑似笔误）"
            对.append((原甲, 原乙, 说明))
    return 对


def 归一分档(名称们):
    """分三档：自动归一（含简称包含）、候选（须人确认）、单独。"""
    组 = {}
    for 原 in 名称们:
        组.setdefault(清洗名称(原), set()).add(原)
    候选 = []
    for 甲原, 乙原, 说明 in 配对候选(名称们):
        甲, 乙 = 清洗名称(甲原), 清洗名称(乙原)
        if "可自动归一" in 说明 and 甲 in 组 and 乙 in 组:
            规范, 并入 = (甲, 乙) if len(甲) >= len(乙) else (乙, 甲)
            组[规范] |= 组.pop(并入)
        else:
            候选.append((甲原, 乙原, 说明))
    在候选里的 = {名 for 对 in 候选 for 名 in 对[:2]}
    自动 = {key: sorted(原名们) for key, 原名们 in 组.items() if len(原名们) > 1}
    单独 = sorted(原名 for key, 原名们 in 组.items()
                if len(原名们) == 1 for 原名 in 原名们 if 原名 not in 在候选里的)
    return {"自动归一": 自动, "候选": 候选, "单独": 单独}
