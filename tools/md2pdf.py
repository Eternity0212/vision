"""极简 Markdown -> PDF（纯 cairo，A4，中文字体 WenQuanYi Zen Hei）

用法:
    python3 tools/md2pdf.py 输入.md 输出.pdf
    MD2PDF_PNG=1 python3 tools/md2pdf.py 输入.md 输出.png   # 每页导出一张 PNG，便于检查排版

支持: #/##/### 标题、段落、有序/无序列表、> 引用、``` 代码块、| 表格 |、--- 分隔线
页脚版本号自动读取 Markdown 头部的「版本：vX.Y」。
"""
import os
import re
import sys

import cairo

MM = 72 / 25.4
PAGE_W, PAGE_H = 210 * MM, 297 * MM
ML, MR, MT, MB = 16 * MM, 16 * MM, 15 * MM, 16 * MM
BODY_W = PAGE_W - ML - MR

FONT = "WenQuanYi Zen Hei"
MONO = "DejaVu Sans Mono"
VERSION = "v1.x"          # 由 Markdown 头部「版本：」覆盖

BODY = 9.5
SMALL = 9.2
TABLE = 8.2
CODE = 8.2


class Doc:
    def __init__(self, path):
        self._png = path if os.environ.get("MD2PDF_PNG") else None
        if self._png:
            self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(PAGE_W), int(PAGE_H))
        else:
            self.surf = cairo.PDFSurface(path, PAGE_W, PAGE_H)
        self.ctx = cairo.Context(self.surf)
        self.page = 0
        self.new_page()

    # ---------- 页面 ----------
    def new_page(self):
        if self.page:
            if self._png:
                self.surf.write_to_png(f"{self._png}.{self.page}.png")
            self.surf.show_page()
        self.page += 1
        self.ctx.set_source_rgb(1, 1, 1)
        self.ctx.paint()
        self.y = MT

    def footer(self):
        ctx = self.ctx
        ctx.set_source_rgb(0.45, 0.45, 0.45)
        self.set_font(7.5)
        t = f"电控对接确认单 {VERSION} · 第 {self.page} 页"
        ext = ctx.text_extents(t)
        ctx.move_to((PAGE_W - ext.width) / 2, PAGE_H - MB / 2)
        ctx.show_text(t)

    def need(self, h):
        if self.y + h > PAGE_H - MB:
            self.footer()
            self.new_page()

    def color(self, rgb):
        self.ctx.set_source_rgb(*rgb)

    # ---------- 字体 ----------
    def set_font(self, size, bold=False, mono=False):
        face = MONO if mono else FONT
        weight = cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL
        self.ctx.select_font_face(face, cairo.FONT_SLANT_NORMAL, weight)
        self.ctx.set_font_size(size)

    def width_of(self, text):
        return self.ctx.text_extents(text)[2] if text else 0.0

    def wrap(self, text, max_w, size, bold=False, mono=False):
        self.set_font(size, bold, mono)
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if self.width_of(cur + ch) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        return lines

    # ---------- 块元素 ----------
    def heading(self, text, level):
        size = {1: 16, 2: 12.5, 3: 10.5}[level]
        gap = 9 if level == 1 else 7
        self.need(size * 2 + gap)
        if level == 1:
            self.color((0.10, 0.10, 0.10))
            for ln in self.wrap(text, BODY_W, size, bold=True):
                self.set_font(size, bold=True)
                self.ctx.move_to(ML, self.y + size)
                self.ctx.show_text(ln)
                self.y += size * 1.35
            self.y += 4
        else:
            self.color((0.12, 0.12, 0.12) if level == 2 else (0.2, 0.2, 0.2))
            self.y += gap
            for ln in self.wrap(text, BODY_W, size, bold=True):
                self.set_font(size, bold=True)
                self.ctx.move_to(ML, self.y + size)
                self.ctx.show_text(ln)
                self.y += size * 1.4
            self.y += gap * 0.5
        self.color((0, 0, 0))

    def para(self, text):
        size, lh = BODY, BODY * 1.62
        self.color((0, 0, 0))
        for ln in self.wrap(text, BODY_W, size):
            self.need(lh)
            self.set_font(size)
            self.ctx.move_to(ML, self.y + size)
            self.ctx.show_text(ln)
            self.y += lh
        self.y += 3.5

    def item(self, text, mark="·"):
        size, lh = BODY, BODY * 1.62
        indent = 5.5 * MM
        started = False
        for ln in self.wrap(text, BODY_W - indent, size):
            self.need(lh)
            self.set_font(size)
            if not started:
                self.ctx.move_to(ML + 1 * MM, self.y + size)
                self.ctx.show_text(mark)
                started = True
            self.ctx.move_to(ML + indent, self.y + size)
            self.ctx.show_text(ln)
            self.y += lh
        self.y += 1.6

    def bullet(self, text, mark=None):
        if mark is None:
            if text.startswith("[ ] "):
                mark, text = "[ ]", text[4:]
            elif text.startswith("[x] "):
                mark, text = "[x]", text[4:]
            else:
                mark = "·"
        self.item(text, mark)

    def quote(self, lines):
        size, lh = SMALL, SMALL * 1.6
        pad = 2.5 * MM
        wrapped = []
        for t in lines:
            wrapped += self.wrap(t, BODY_W - 2 * pad - 2 * MM, size)
        h = len(wrapped) * lh + pad
        self.need(h)
        top = self.y
        self.color((0.96, 0.96, 0.93))
        self.ctx.rectangle(ML, top, BODY_W, h)
        self.ctx.fill()
        self.color((0.85, 0.72, 0.25))
        self.ctx.rectangle(ML, top, 1.6 * MM, h)
        self.ctx.fill()
        y = top + pad / 2 + size
        self.color((0.25, 0.25, 0.2))
        self.set_font(size)
        for ln in wrapped:
            self.ctx.move_to(ML + 2 * MM + pad / 2, y)
            self.ctx.show_text(ln)
            y += lh
        self.color((0, 0, 0))
        self.y = top + h + 4

    def code_block(self, lines):
        size, lh = CODE, CODE * 1.55
        pad = 2.2 * MM
        wrapped = []
        for t in lines:
            wrapped += self.wrap(t if t else " ", BODY_W - 2 * pad, size, mono=True)
        h = len(wrapped) * lh + pad
        self.need(h)
        top = self.y
        self.color((0.955, 0.955, 0.96))
        self.ctx.rectangle(ML, top, BODY_W, h)
        self.ctx.fill()
        y = top + pad / 2 + size
        self.color((0.15, 0.15, 0.15))
        self.set_font(size, mono=True)
        for ln in wrapped:
            self.ctx.move_to(ML + pad, y)
            self.ctx.show_text(ln)
            y += lh
        self.color((0, 0, 0))
        self.y = top + h + 4

    def rule(self):
        self.need(12)
        self.y += 4
        self.color((0.85, 0.85, 0.85))
        self.ctx.set_line_width(0.4)
        self.ctx.move_to(ML, self.y)
        self.ctx.line_to(PAGE_W - MR, self.y)
        self.ctx.stroke()
        self.y += 6

    # ---------- 表格 ----------
    def table(self, rows):
        size, pad = TABLE, 1.6 * MM
        lh = size * 1.55
        ncol = max(len(r) for r in rows)

        def units(s):
            return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s)

        weights = []
        for c in range(ncol):
            w = max(units(r[c]) if c < len(r) else 0 for r in rows)
            weights.append(min(max(w, 6), 42))
        total = sum(weights)
        widths = [BODY_W * w / total for w in weights]

        def cells_of(row, bold):
            return [self.wrap((row[c] if c < len(row) else "").replace("**", ""),
                              widths[c] - 2 * pad, size, bold=bold) for c in range(ncol)]

        for ri, row in enumerate(rows):
            header = ri == 0
            cells = cells_of(row, header)
            h = max(len(x) for x in cells) * lh + pad
            if self.y + h > PAGE_H - MB:
                self.footer()
                self.new_page()
                if not header:      # 跨页重复表头
                    hc = cells_of(rows[0], True)
                    self._draw_row(hc, widths, max(len(x) for x in hc) * lh + pad,
                                   size, lh, pad, True)
                    cells = cells_of(row, False)
                    h = max(len(x) for x in cells) * lh + pad
            self._draw_row(cells, widths, h, size, lh, pad, header)
        self.y += 5

    def _draw_row(self, cells, widths, h, size, lh, pad, header):
        top = self.y
        if header:
            self.color((0.93, 0.93, 0.90))
            self.ctx.rectangle(ML, top, sum(widths), h)
            self.ctx.fill()
        self.color((0.78, 0.78, 0.78))
        self.ctx.set_line_width(0.35)
        for i, w in enumerate(widths):
            self.ctx.rectangle(ML + sum(widths[:i]), top, w, h)
            self.ctx.stroke()
        self.color((0.12, 0.12, 0.12))
        for i, lines in enumerate(cells):
            x = ML + sum(widths[:i]) + pad / 2
            yy = top + pad / 2 + size
            self.set_font(size, bold=header)
            for ln in lines:
                self.ctx.move_to(x, yy)
                self.ctx.show_text(ln)
                yy += lh
        self.color((0, 0, 0))
        self.y = top + h


def inline(text):
    return text.replace("**", "").replace("`", "").strip()


def main(src, dst):
    global VERSION
    with open(src, encoding="utf-8") as f:
        lines = f.read().split("\n")

    for ln in lines[:12]:
        if "版本：" in ln:
            VERSION = ln.split("版本：")[1].split()[0].strip()
            break

    doc = Doc(dst)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            doc.code_block(buf)
            continue

        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                doc.table(rows)
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            doc.heading(inline(stripped[level:]), min(level, 3))
            i += 1
            continue

        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(inline(lines[i].strip().lstrip(">")))
                i += 1
            doc.quote(buf)
            continue

        if stripped == "---":
            doc.rule()
            i += 1
            continue

        m_ord = re.match(r"^(\d+)\.\s+(.*)", stripped)
        if m_ord:
            while i < len(lines):
                m2 = re.match(r"^(\d+)\.\s+(.*)", lines[i].strip())
                if not m2:
                    break
                doc.item(inline(m2.group(2)), mark=m2.group(1) + ".")
                i += 1
            continue

        if stripped.startswith("- "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                buf.append(inline(lines[i].strip()[2:]))
                i += 1
            for b in buf:
                doc.bullet(b)
            continue

        buf = [inline(stripped)]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">", "```", "- "))
                    or nxt == "---" or re.match(r"^\d+\.\s", nxt)):
                break
            buf.append(inline(nxt))
            i += 1
        doc.para("".join(buf))

    doc.footer()
    if doc._png:
        doc.surf.write_to_png(doc._png)
        print(f"已生成 {doc._png}（仅末页），共 {doc.page} 页")
        return
    doc.surf.finish()
    print(f"已生成 {dst}，共 {doc.page} 页")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
