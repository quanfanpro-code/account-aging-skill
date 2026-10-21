#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 DataFrame 生成深海蓝标准格式 Excel 报表。

值与排版一次成型写入：字体、填充、边框、对齐、列宽、行高、冻结、页签色
都在同一次 XML 拼接里完成，不再写一遍值再读回来排版。只依赖标准库。
"""

import math
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape as _esc

import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════
# 格式常量 — 摩根士丹利标准
# ═══════════════════════════════════════════════════════════════════════════

# 框线（只有上下，无竖线）
THICK = ("medium", "000000")
DASHED = ("dashed", "808080")

# 表头：深海蓝底白字；合计行浅蓝底
HDR_COLOR = "1F4E79"
SUM_COLOR = "D6E4F0"

# 合计行关键词（当最后一行的首列文本匹配时，视为合计行）
SUM_KEYWORDS = ('合计', '小计', '总计', 'sum', 'total', '小計', '合計')

# 数字格式
FMT = {
    'money': '#,##0.00',
    'number': '#,##0',
    'pct': '0.00%',
    'date': 'yyyy/mm/dd',
    'text': '@',
    'unknown': '@',
}

# 布局
ROW_HEIGHT = 18
A_COL_WIDTH = 2
MIN_COL_WIDTH = 10
MAX_COL_WIDTH = 50


# ═══════════════════════════════════════════════════════════════════════════
# 列类型推断
# ═══════════════════════════════════════════════════════════════════════════

def _infer_column_type(name: str, series: pd.Series) -> str:
    """根据列名关键词 + 值特征推断列类型。"""
    n = name.lower()

    # ── 关键词匹配（优先级：pct > date > text > money）──
    # "率"优先检测，避免"毛利率"被 money 关键词误匹配
    if re.search(r'(率$|占比|百分比|增长率)', n):
        return 'pct'
    if re.search(r'(日期|时间|年月|期间|年份|月份|date|time)', n):
        return 'date'
    if re.search(r'(编号|id|单号|编码|电话|手机|备注|说明|名称|地址|描述|号码|订单号|负责人|姓名|联系人|部门|岗位)', n):
        return 'text'
    if re.search(r'(金额|价格|收入|成本|费用|毛利额|净利|合计|总额|售价|单价|预算|支出|毛利(?!率))', n):
        return 'money'

    # ── 值特征 ──
    clean = series.dropna()
    if len(clean) == 0:
        return 'unknown'

    try:
        if pd.api.types.is_datetime64_any_dtype(clean):
            return 'date'

        if pd.api.types.is_numeric_dtype(clean):
            # 浮点一律按金额：0~1 的小额数据（如万元单位）不能误判为百分比
            if pd.api.types.is_float_dtype(clean.head(20)):
                return 'money'
            return 'number'

        # 长数字字符串 → text
        str_sample = clean.astype(str).head(20)
        if str_sample.str.match(r'^\d{12,}$').any():
            return 'text'
    except Exception:
        pass

    return 'unknown'


# ═══════════════════════════════════════════════════════════════════════════
# 列宽估算
# ═══════════════════════════════════════════════════════════════════════════

def _char_width(text: str) -> int:
    """中文字符计2，拉丁/数字计1。"""
    return sum(2 if ord(ch) > 127 else 1 for ch in str(text))


def _estimate_column_width(header: str, series: pd.Series) -> int:
    """估算列宽：取表头和数据中最大字符宽度 + padding。"""
    max_w = _char_width(header)
    for v in series.dropna():
        try:
            w = _char_width(str(v))
            if w > max_w:
                max_w = w
            if max_w + 2 >= MAX_COL_WIDTH:
                break
        except Exception:
            pass
    return max(MIN_COL_WIDTH, min(max_w + 2, MAX_COL_WIDTH))


# ═══════════════════════════════════════════════════════════════════════════
# 写入器核心 — 值与排版一次成型（标准库）
# ═══════════════════════════════════════════════════════════════════════════

HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
NS = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')

_列缓存 = {}


def col_letter(n):
    """1 基列号 -> 列字母。"""
    s = _列缓存.get(n)
    if s is None:
        s, m = "", n
        while m:
            m, r = divmod(m - 1, 26)
            s = chr(65 + r) + s
        _列缓存[n] = s
    return s


def _rgb(c):
    return c if len(c) == 8 else "00" + c


def _num(v):
    """数字 -> XML 文本。%.16g 与 openpyxl 一致，能抹掉浮点尾噪。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, datetime):
        # datetime 是 date 的子类，必须先判断
        return repr((v - datetime(1899, 12, 30)).total_seconds() / 86400.0)
    if isinstance(v, date):
        return str((v - date(1899, 12, 30)).days)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return "%.16g" % v
    return None


def parse_ref(ref):
    """"B2" -> (2, 2)；"A1:C3" -> ((1, 1), (3, 3))。"""
    def one(p):
        m = re.fullmatch(r"([A-Z]+)(\d+)", p.strip().upper())
        if not m:
            raise ValueError("区域写法不对: %s" % p)
        col = 0
        for ch in m.group(1):
            col = col * 26 + ord(ch) - 64
        return int(m.group(2)), col
    if ":" in ref:
        a, b = ref.split(":", 1)
        return one(a), one(b)
    return one(ref), one(ref)


class StyleBook:
    """styles.xml 索引表：字体/填充/边框/数字格式/xf/dxf 全部按内容去重。"""

    def __init__(self):
        self._fmt, self._fmt_list = {}, []
        self._font, self._font_list = {}, []
        self._fill, self._fill_list = {}, []
        self._border, self._border_list = {}, []
        self._xf, self._xf_list = {}, []
        self._dxf, self._dxf_list = {}, []
        # 0 号位必须是默认字体（Calibri 11、主题色 1），否则没设字体的格子读回会变
        self.font("Calibri", 11, None, "theme:1", None)
        self.fill(None, None)
        self._fill[("gray125", None)] = 1
        self._fill_list.append('<fill><patternFill patternType="gray125"/></fill>')
        self._xf[("默认",)] = 0
        self._xf_list.append('<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>')

    def fmt(self, code):
        if code in (None, "", "General"):
            return 0
        builtin = {"@": 49, "0.00": 2, "0.00%": 10, "#,##0": 3, "#,##0.00": 4}
        if code in builtin:
            return builtin[code]
        i = self._fmt.get(code)
        if i is None:
            i = 164 + len(self._fmt_list)
            self._fmt[code] = i
            self._fmt_list.append(code)
        return i

    def font(self, name, size, bold, color, underline):
        key = (name, size, bold, color, underline)
        i = self._font.get(key)
        if i is None:
            p = ["<font>"]
            if bold:
                p.append("<b/>")
            if size is not None:
                p.append('<sz val="%g"/>' % size)
            if name:
                p.append('<name val="%s"/>' % _esc(name))
            if color == "theme:1":
                p.append('<color theme="1"/>')
            elif color:
                p.append('<color rgb="%s"/>' % _rgb(color))
            if underline:
                p.append('<u val="%s"/>' % _esc(underline))
            p.append("</font>")
            i = len(self._font_list)
            self._font[key] = i
            self._font_list.append("".join(p))
        return i

    def fill(self, kind, color):
        if not color and kind in (None, "none"):
            key, piece = ("none", None), '<fill><patternFill patternType="none"/></fill>'
        elif kind == "gray125" and not color:
            key, piece = ("gray125", None), '<fill><patternFill patternType="gray125"/></fill>'
        else:
            key = (kind, color)
            piece = ('<fill><patternFill patternType="%s">'
                     '<fgColor rgb="%s"/><bgColor indexed="64"/></patternFill></fill>'
                     % (_esc(kind or "solid"), _rgb(color or "00000000")))
        i = self._fill.get(key)
        if i is None:
            i = len(self._fill_list)
            self._fill[key] = i
            self._fill_list.append(piece)
        return i

    def border(self, top, bottom, left, right):
        key = (top, bottom, left, right)
        i = self._border.get(key)
        if i is None:
            p = ["<border>"]
            for tag, spec in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
                if not spec:
                    p.append("<%s/>" % tag)
                else:
                    style, color = spec if isinstance(spec, tuple) else (spec, "D9D9D9")
                    c = '<color rgb="%s"/>' % _rgb(color) if color else ""
                    p.append('<%s style="%s">%s</%s>' % (tag, _esc(style), c, tag))
            p.append("<diagonal/></border>")
            i = len(self._border_list)
            self._border[key] = i
            self._border_list.append("".join(p))
        return i

    def xf(self, spec):
        spec = spec or {}
        fmt = self.fmt(spec.get("数字格式"))
        f = self.font(spec.get("字体"), spec.get("字号"),
                      spec.get("加粗"), spec.get("字色"), spec.get("下划线"))
        fl = self.fill(spec.get("填充类型"), spec.get("底色"))
        bd = self.border(spec.get("上框"), spec.get("下框"), spec.get("左框"), spec.get("右框"))
        align = (spec.get("对齐"), spec.get("垂直"), spec.get("自动换行"))
        key = (fmt, f, fl, bd, align)
        i = self._xf.get(key)
        if i is None:
            p = ['<xf numFmtId="%d" fontId="%d" fillId="%d" borderId="%d" xfId="0"' % (fmt, f, fl, bd)]
            for flag, idx in (("applyNumberFormat", fmt), ("applyFont", f),
                              ("applyFill", fl), ("applyBorder", bd)):
                if idx:
                    p.append(' %s="1"' % flag)
            if any(align):
                p.append(' applyAlignment="1"><alignment')
                if align[0]:
                    p.append(' horizontal="%s"' % _esc(align[0]))
                if align[1]:
                    p.append(' vertical="%s"' % _esc(align[1]))
                if align[2]:
                    p.append(' wrapText="1"')
                p.append("/></xf>")
            else:
                p.append("/>")
            i = len(self._xf_list)
            self._xf[key] = i
            self._xf_list.append("".join(p))
        return i

    def dxf(self, rule):
        key = (rule.get("字色"), rule.get("底色"), rule.get("加粗"))
        i = self._dxf.get(key)
        if i is None:
            p = ["<dxf>"]
            font = ""
            if rule.get("加粗"):
                font += "<b/>"
            if rule.get("字色"):
                font += '<color rgb="%s"/>' % _rgb(rule["字色"])
            if font:
                p.append("<font>%s</font>" % font)
            if rule.get("底色"):
                p.append('<fill><patternFill patternType="solid"><fgColor rgb="%s"/></patternFill></fill>'
                         % _rgb(rule["底色"]))
            p.append("</dxf>")
            i = len(self._dxf_list)
            self._dxf[key] = i
            self._dxf_list.append("".join(p))
        return i

    def xml(self):
        p = [HEADER, "<styleSheet %s>" % NS]
        if self._fmt_list:
            p.append('<numFmts count="%d">' % len(self._fmt_list))
            for code in self._fmt_list:
                p.append('<numFmt numFmtId="%d" formatCode="%s"/>' % (self._fmt[code], _esc(code)))
            p.append("</numFmts>")
        p.append('<fonts count="%d">' % len(self._font_list))
        p.extend(self._font_list)
        p.append("</fonts>")
        p.append('<fills count="%d">' % len(self._fill_list))
        p.extend(self._fill_list)
        p.append("</fills>")
        p.append('<borders count="%d">' % max(1, len(self._border_list)))
        p.extend(self._border_list or ['<border><left/><right/><top/><bottom/><diagonal/></border>'])
        p.append("</borders>")
        p.append('<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>')
        p.append('<cellXfs count="%d">' % len(self._xf_list))
        p.extend(self._xf_list)
        p.append("</cellXfs>")
        p.append('<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>')
        if self._dxf_list:
            p.append('<dxfs count="%d">' % len(self._dxf_list))
            p.extend(self._dxf_list)
            p.append("</dxfs>")
        p.append("</styleSheet>")
        return "".join(p)


class _SharedStrings:
    def __init__(self):
        self.index, self.items = {}, []

    def add(self, s):
        i = self.index.get(s)
        if i is None:
            i = len(self.items)
            self.index[s] = i
            self.items.append(s)
        return i

    def xml(self):
        body = "".join('<si><t xml:space="preserve">%s</t></si>' % _esc(s) for s in self.items)
        return (HEADER + '<sst %s count="%d" uniqueCount="%d">%s</sst>'
                % (NS, len(self.items), len(self.items), body))


class 表计划(dict):
    """支持属性访问的表计划，postprocess 回调直接改属性或调方法。"""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def apply_columns(self, rules):
        """按列设整列兜底格式，写进 <col style>，普通格不写 s。"""
        for col, spec in rules.items():
            self.setdefault("列样式", {}).setdefault(int(col), {}).update(spec)

    def apply_rows(self, rules):
        """整行样式，按当前最大列数展开成区域。"""
        width = max((len(r) for r in self.get("行") or []), default=0)
        if not width:
            return
        for row, spec in rules.items():
            ref = "A%d:%s%d" % (row, col_letter(width), row)
            self.setdefault("样式", {}).setdefault(ref, {}).update(spec)

    def apply_overrides(self, overrides):
        """局部区域覆盖，优先级高于行规则和列规则。"""
        for ref, spec in overrides.items():
            self.setdefault("样式", {}).setdefault(ref, {}).update(spec)

    def apply_conditions(self, rules):
        """条件格式，单独落在 conditionalFormatting 节点。"""
        self.setdefault("条件格式", []).extend(rules)


def new_sheet(name, rows, **opts):
    """新建表计划。rows 第一行通常当表头。"""
    tp = 表计划(名称=name, 行=rows, 样式={}, 列样式={})
    tp.update(opts)
    return tp


def _parse_rules(style_map):
    """区域样式预先解析成 (r1, c1, r2, c2, spec)，书写顺序即覆盖顺序。"""
    rules = []
    for ref, spec in style_map.items():
        (r1, c1), (r2, c2) = parse_ref(ref)
        rules.append((r1, c1, r2, c2, spec))
    return rules


def _style_at(rules, row, col):
    merged = {}
    for r1, c1, r2, c2, spec in rules:
        if r1 <= row <= r2 and c1 <= col <= c2:
            merged.update(spec)
    return merged


def _cell(ref, style_idx, value, sst):
    attr = ' r="%s"' % ref
    if style_idx:
        attr += ' s="%d"' % style_idx
    if value is None:
        return "<c%s/>" % attr if style_idx else ""
    if isinstance(value, str) and value.startswith("="):
        return "<c%s><f>%s</f><v></v></c>" % (attr, _esc(value[1:]))
    if isinstance(value, str):
        return '<c%s t="s"><v>%d</v></c>' % (attr, sst.add(value))
    if isinstance(value, bool):
        return '<c%s t="b"><v>%d</v></c>' % (attr, 1 if value else 0)
    n = _num(value)
    return "<c%s><v>%s</v></c>" % (attr, n) if n is not None else "<c%s/>" % attr


def _sheet_xml(tp, sb, sst):
    rows = tp.get("行") or []
    style_map = tp.get("样式") or {}
    rules = _parse_rules(style_map) if style_map else []
    widths = tp.get("列宽") or []
    col_styles = {int(k): v for k, v in (tp.get("列样式") or {}).items()}
    p = [HEADER, "<worksheet %s>" % NS]

    # sheetPr -> sheetViews -> sheetFormatPr -> cols -> sheetData 顺序不能变
    if tp.get("标签色"):
        p.append('<sheetPr><tabColor rgb="%s"/></sheetPr>' % _rgb(tp["标签色"]))

    view = ['<sheetView workbookViewId="0"']
    if tp.get("网格线") is False:
        view.append(' showGridLines="0"')
    freeze = tp.get("冻结")
    if freeze:
        r, c = parse_ref(freeze)[0]
        split = 'xSplit="%d" ' % (c - 1) if c > 1 else ''
        view.append('><pane %sySplit="%d" topLeftCell="%s" activePane="bottomRight" state="frozen"/>'
                    '</sheetView>' % (split, r - 1, freeze))
    else:
        view.append("/>")
    p.append("<sheetViews>%s</sheetViews>" % "".join(view))
    p.append('<sheetFormatPr defaultRowHeight="%g"/>' % (tp.get("默认行高") or 15))

    base = tp.get("默认样式") or {}
    if widths or col_styles or base:
        ncol = max([len(widths)] + list(col_styles) + [max((len(r) for r in rows), default=0)])
        cols = []
        for i in range(1, ncol + 1):
            attr = ' min="%d" max="%d"' % (i, i)
            if i <= len(widths) and widths[i - 1]:
                attr += ' width="%g" customWidth="1"' % widths[i - 1]
            spec = dict(base)
            spec.update(col_styles.get(i, {}))
            if spec:
                # 整列兜底样式：这一列的普通格就不用写 s 了
                attr += ' style="%d"' % sb.xf(spec)
            cols.append("<col%s/>" % attr)
        p.append("<cols>%s</cols>" % "".join(cols))

    p.append("<sheetData>")
    heights = tp.get("行高") or {}
    for r, values in enumerate(rows, 1):
        attr = '<row r="%d"' % r
        if r in heights:
            attr += ' ht="%g" customHeight="1"' % heights[r]
        cells = []
        for c, value in enumerate(values, 1):
            spec = _style_at(rules, r, c) if rules else {}
            if spec:
                # 格子自己写了 s，列样式就不再生效，必须把列格式合并进来
                merged = dict(base)
                merged.update(col_styles.get(c, {}))
                merged.update(spec)
                idx = sb.xf(merged)
            else:
                idx = 0          # 交给列样式，不写属性
            cell = _cell("%s%d" % (col_letter(c), r), idx, value, sst)
            if cell:
                cells.append(cell)
        p.append(attr + ">" + "".join(cells) + "</row>")
    p.append("</sheetData>")

    if tp.get("自动筛选"):
        p.append('<autoFilter ref="%s"/>' % _esc(tp["自动筛选"]))
    if tp.get("合并"):
        p.append('<mergeCells count="%d">' % len(tp["合并"]))
        p.extend('<mergeCell ref="%s"/>' % _esc(x) for x in tp["合并"])
        p.append("</mergeCells>")

    for i, rule in enumerate(tp.get("条件格式") or [], 1):
        formulas = "".join("<formula>%s</formula>" % _esc(str(f)) for f in (rule.get("公式") or []))
        p.append('<conditionalFormatting sqref="%s"><cfRule type="expression" dxfId="%d" priority="%d">%s'
                 '</cfRule></conditionalFormatting>' % (_esc(rule["区域"]), sb.dxf(rule), i, formulas))

    margin = (tp.get("打印") or {}).get("页边距") or {}
    # 默认值与 openpyxl 写出的 PageMargins 一致，避免新旧输出有边距差
    p.append('<pageMargins left="%g" right="%g" top="%g" bottom="%g" header="%g" footer="%g"/>'
             % (margin.get("左", 0.75), margin.get("右", 0.75),
                margin.get("上", 1.0), margin.get("下", 1.0),
                margin.get("页眉", 0.5), margin.get("页脚", 0.5)))
    prt = tp.get("打印") or {}
    if prt.get("方向") or prt.get("纸张") or prt.get("缩放"):
        attr = ""
        if prt.get("方向"):
            attr += ' orientation="%s"' % _esc(prt["方向"])
        if prt.get("纸张"):
            attr += ' paperSize="%d"' % prt["纸张"]
        if prt.get("缩放"):
            attr += ' fitToWidth="1" fitToHeight="0"'
        p.append("<pageSetup%s/>" % attr)
    p.append("</worksheet>")
    return "".join(p)


def write_workbook(tables, path):
    """表计划列表 -> xlsx。值和排版一次成型，一次 zip。"""
    sb = StyleBook()
    sst = _SharedStrings()
    sheets = [_sheet_xml(tp, sb, sst) for tp in tables]

    sheet_parts, defined = [], []
    for i, tp in enumerate(tables, 1):
        name = _esc(tp["名称"])
        sheet_parts.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (name, i, i))
        pr = tp.get("打印") or {}
        if pr.get("区域"):
            defined.append('<definedName name="_xlnm.Print_Area" localSheetId="%d">\'%s\'!%s</definedName>'
                           % (i - 1, name, pr["区域"]))
        if pr.get("重复行"):
            defined.append('<definedName name="_xlnm.Print_Titles" localSheetId="%d">\'%s\'!$%s</definedName>'
                           % (i - 1, name, pr["重复行"].replace(":", ":$")))
    workbook = (HEADER + "<workbook %s><workbookPr/><bookViews><workbookView activeTab=\"0\"/></bookViews>"
                "<sheets>%s</sheets>%s"
                '<calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>'
                % (NS, "".join(sheet_parts),
                   ("<definedNames>%s</definedNames>" % "".join(defined)) if defined else ""))

    n = len(tables)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        types = [HEADER, '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                 '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                 '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>']
        for i in range(n):
            types.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1))
        types.append("</Types>")
        z.writestr("[Content_Types].xml", "".join(types))

        z.writestr("_rels/.rels",
                   HEADER + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>')

        rels = [HEADER, '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        for i in range(n):
            rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1))
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (n + 1))
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>' % (n + 2))
        rels.append("</Relationships>")
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))

        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/styles.xml", sb.xml())
        z.writestr("xl/sharedStrings.xml", sst.xml())

        for i, (tp, xml) in enumerate(zip(tables, sheets), 1):
            z.writestr("xl/worksheets/sheet%d.xml" % i, xml)


# ═══════════════════════════════════════════════════════════════════════════
# DataFrame → 表计划
# ═══════════════════════════════════════════════════════════════════════════

def _df_to_plan(name: str, df: pd.DataFrame) -> 表计划:
    """把一张 DataFrame 转成深海蓝标准表计划（A 列留空，数据从 B2 起）。"""
    ncols = len(df.columns)
    last = col_letter(ncols + 1)

    header = [None] + [str(c) for c in df.columns]
    raw = df.astype(object)
    raw = raw.where(raw.notna(), None)
    rows = [header]
    for row in raw.to_numpy().tolist():
        # 空字符串与 openpyxl 行为一致，落成空格子而不是存一个空文本
        rows.append([None] + [None if v == '' else v for v in row])

    col_widths, col_styles_map = [], {}
    for ci in range(ncols):
        col = ci + 2
        col_name = df.columns[ci]
        series = df.iloc[:, ci]
        ct = _infer_column_type(str(col_name), series)
        is_num = ct in ('money', 'number', 'pct')
        spec = {"字体": "Arial", "字号": 11,
                "对齐": "right" if is_num else "left", "垂直": "center"}
        nf = FMT.get(ct)
        if nf:
            spec["数字格式"] = nf
        if is_num:
            spec["字色"] = "0000FF"
        col_styles_map[col] = spec
        col_widths.append(_estimate_column_width(str(col_name), series))

    tp = new_sheet(re.sub(r'[\\/*?:\[\]]', '_', str(name))[:31], rows)
    tp["列宽"] = [A_COL_WIDTH] + col_widths + [3]
    tp["列样式"] = col_styles_map
    tp["默认行高"] = ROW_HEIGHT
    tp["网格线"] = False
    tp["冻结"] = "A2"

    nrows = 1 + len(df)
    tp["样式"] = {
        # 表头是文本，数字格式归 General，不继承数字列的列样式格式
        "B1:%s1" % last: {"底色": HDR_COLOR, "字色": "FFFFFF", "加粗": True,
                          "对齐": "center", "垂直": "center", "数字格式": "General",
                          "上框": THICK, "下框": DASHED},
    }
    if nrows > 1:
        # 数据区统一上下虚线；末行覆盖为下粗线，合计行再叠底色加粗
        tp.setdefault("样式", {}).setdefault("B2:%s%d" % (last, nrows), {}).update(
            {"上框": DASHED, "下框": DASHED})
        last_spec = {"下框": THICK}
        first_val = rows[-1][1]
        if nrows > 2 and first_val is not None and any(kw in str(first_val) for kw in SUM_KEYWORDS):
            # 合计行黑字加粗，不继承数字列的蓝字
            last_spec.update({"底色": SUM_COLOR, "加粗": True, "字色": None})
        tp["样式"].setdefault("B%d:%s%d" % (nrows, last, nrows), {}).update(last_spec)
    return tp


# ═══════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════

def make_excel(data, output_path: str, sheet_name: str = 'Sheet1', postprocess=None) -> str:
    """生成摩根系标准格式 Excel。

    参数
    ----
    data : DataFrame | list[tuple[str, DataFrame]]
        单 sheet 传 df，多 sheet 传 [(名称, df), ...]。
    output_path : str
        输出文件路径。
    sheet_name : str
        单 sheet 模式的工作表名称。
    postprocess : callable, optional
        写入前对表计划列表做最终排版（页签色、备注高亮、图表等），
        改动随同一次写入生效，不再重新打开文件。
    返回
    ----
    str : 输出文件的绝对路径。
    """
    # 统一为多 sheet 格式
    if isinstance(data, pd.DataFrame):
        sheets = [(sheet_name, data)]
    else:
        sheets = list(data)

    plans = []
    for name, df in sheets:
        if df is None:
            continue
        if hasattr(df, 'empty') and df.empty:
            # 空表也建 sheet 并写占位行，避免 sheet 静默消失（与汇总表"(无数据)"行为一致）
            df = pd.DataFrame([["（无数据）"]], columns=["提示"])
        plans.append(_df_to_plan(name, df))

    if postprocess is not None:
        postprocess(plans)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_workbook(plans, str(out))
    return str(out.absolute())
