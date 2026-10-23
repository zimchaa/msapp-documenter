"""Build the PowerPoint from app_model.json (+ interpretation.json) on top of any .pptx template.

Template contract (so a company template drops in): layouts are found by name ("Title Slide",
"Section Header", "Title Only", falling back to "Title and Content" / position), slides already in
the template are removed, and every colour comes from the theme (accent 1-6, text/background) so
the deck takes on the company palette. Fonts are never set, so theme fonts apply.
Diagrams are drawn as native, editable PowerPoint shapes.
"""
import copy
import re

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

import fx
from render_md import callout_rows, merged_findings
from wireframe import MARKER_COLOR, MARKER_R, marker_pos

SEV_RGB = {"High": RGBColor(0xC0, 0x39, 0x2B), "Medium": RGBColor(0xD6, 0x8A, 0x00),
           "Low": RGBColor(0x5B, 0x6B, 0x7A), "Info": RGBColor(0x8A, 0x94, 0x9E)}
ACC_SCREEN, ACC_DATA, ACC_FLOW, ACC_CONN, ACC_AI = (MSO_THEME_COLOR.ACCENT_1, MSO_THEME_COLOR.ACCENT_2,
                                                    MSO_THEME_COLOR.ACCENT_4, MSO_THEME_COLOR.ACCENT_6,
                                                    MSO_THEME_COLOR.ACCENT_5)


def clip(text, n):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def fx_summary(formula, n=140):
    """One-line gist of a behaviour formula: its top-level calls in order."""
    parts = []
    for stmt in fx.split_top(formula, ";"):
        m = re.match(r"\s*([A-Za-z_][\w.']*)\s*\(", stmt)
        if not m:
            parts.append(clip(stmt, 40))
            continue
        args = fx.split_top(stmt[stmt.index("(") + 1: fx.matching_close(stmt, stmt.index("("))])
        first = clip(args[0], 30) if args else ""
        parts.append(f"{m.group(1)}({first}{', …' if len(args) > 1 else ''})")
    return clip("; ".join(parts), n)


class Deck:
    def __init__(self, template):
        self.prs = Presentation(template)
        sld = self.prs.slides._sldIdLst
        for s in list(sld):
            self.prs.part.drop_rel(s.rId)
            sld.remove(s)
        self.W, self.H = self.prs.slide_width, self.prs.slide_height
        self.M = int(self.W * 0.045)
        layouts = {l.name.lower(): l for m in self.prs.slide_masters for l in m.slide_layouts}
        all_l = [l for m in self.prs.slide_masters for l in m.slide_layouts]

        def pick(*names, idx=0):
            for n in names:
                if n.lower() in layouts:
                    return layouts[n.lower()]
            return all_l[min(idx, len(all_l) - 1)]
        self.L_TITLE = pick("Title Slide", "Title", idx=0)
        self.L_SECTION = pick("Section Header", "Section Title", "Section", idx=2)
        self.L_CONTENT = pick("Title Only", "Title and Content", "Title, Content", idx=5)

    # ---------------------------------------------------------------- slide basics
    def _strip_placeholders(self, slide, keep_title=True):
        for ph in list(slide.placeholders):
            if keep_title and ph.placeholder_format.type is not None and "TITLE" in str(ph.placeholder_format.type) \
                    and "SUB" not in str(ph.placeholder_format.type):
                continue
            ph._element.getparent().remove(ph._element)

    def title_slide(self, title, subtitle):
        s = self.prs.slides.add_slide(self.L_TITLE)
        if s.shapes.title is not None:
            s.shapes.title.text = title
        sub = [p for p in s.placeholders if p.placeholder_format.idx == 1]
        if sub:
            sub[0].text = subtitle
        for ph in list(s.placeholders):
            if not ph.has_text_frame or not ph.text_frame.text.strip():
                ph._element.getparent().remove(ph._element)
        return s

    def section(self, title, subtitle=""):
        s = self.prs.slides.add_slide(self.L_SECTION)
        if s.shapes.title is not None:
            s.shapes.title.text = title
        others = [p for p in s.placeholders if p.placeholder_format.idx != 0]
        if others and subtitle:
            others[0].text = subtitle
        for ph in list(s.placeholders):
            if not ph.has_text_frame or not ph.text_frame.text.strip():
                ph._element.getparent().remove(ph._element)
        return s

    def content(self, title):
        """New content slide; returns (slide, (left, top, width, height)) of the free body area."""
        s = self.prs.slides.add_slide(self.L_CONTENT)
        t = s.shapes.title
        self._strip_placeholders(s, keep_title=True)
        if t is not None:
            t.text = title
            top = t.top + t.height + int(self.H * 0.02)
        else:
            self.text(s, (self.M, int(self.H * 0.04), self.W - 2 * self.M, int(self.H * 0.1)), [(title, 28, True)])
            top = int(self.H * 0.16)
        top = min(top, int(self.H * 0.25))
        return s, (self.M, top, self.W - 2 * self.M, self.H - top - int(self.H * 0.06))

    # ---------------------------------------------------------------- primitives
    def text(self, slide, rect, paras, size=14, anchor=MSO_ANCHOR.TOP):
        """paras: list of str or (text, size, bold) or (text, size, bold, level)."""
        l, t, w, h = rect
        tb = slide.shapes.add_textbox(l, t, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        first = True
        for p in paras:
            if isinstance(p, str):
                p = (p, size, False)
            txt, sz, bold = p[0], p[1] or size, p[2]
            par = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            run = par.add_run()
            run.text = txt
            run.font.size = Pt(sz)
            run.font.bold = bold
            if len(p) > 3 and p[3]:
                par.level = p[3]
            par.space_after = Pt(4)
        return tb

    def bullets(self, slide, rect, items, size=14, heading=None):
        paras = [(heading, size + 2, True)] if heading else []
        paras += [("•  " + clip(i, 400), size, False) for i in items]
        return self.text(slide, rect, paras, size)

    def box(self, slide, rect, text, color, size=12, shape=MSO_SHAPE.ROUNDED_RECTANGLE, bold=False, dashed=False,
            outline_only=False, align=PP_ALIGN.CENTER):
        l, t, w, h = rect
        sh = slide.shapes.add_shape(shape, l, t, w, h)
        if outline_only:
            sh.fill.background()
            sh.line.color.theme_color = color
        else:
            sh.fill.solid()
            sh.fill.fore_color.theme_color = color
            sh.line.fill.background()
        if dashed:
            sh.line.dash_style = 4
        tf = sh.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Emu(45720)
        for i, line in enumerate(str(text).split("\n")):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            r = p.add_run()
            r.text = line
            r.font.size = Pt(size if i == 0 else max(size - 2, 8))
            r.font.bold = bold and i == 0
            if outline_only:
                r.font.color.theme_color = MSO_THEME_COLOR.TEXT_1
            else:
                r.font.color.theme_color = MSO_THEME_COLOR.BACKGROUND_1
        return sh

    def arrow(self, slide, a, b, a_idx=3, b_idx=1, curved=False, dashed=False, weight=1.25, color=MSO_THEME_COLOR.TEXT_1):
        c = slide.shapes.add_connector(MSO_CONNECTOR.CURVE if curved else MSO_CONNECTOR.STRAIGHT, 0, 0, 1, 1)
        c.begin_connect(a, a_idx)
        c.end_connect(b, b_idx)
        c.line.width = Pt(weight)
        c.line.color.theme_color = color
        if dashed:
            c.line.dash_style = 4
        ln = c.line._get_or_add_ln()
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tail)
        return c

    def tag_ai(self, slide):
        w, h = int(self.W * 0.15), int(self.H * 0.045)
        self.box(slide, (self.W - self.M - w, int(self.H * 0.025), w, h), "AI interpretation", ACC_AI, size=10, bold=True)

    def table(self, slide, rect, headers, rows, widths=None, size=10, header_size=None):
        l, t, w, h = rect
        n_rows = len(rows) + 1
        row_h = Emu(int(Pt(size) * 2.1))
        gf = slide.shapes.add_table(n_rows, len(headers), l, t, w, min(h, row_h * n_rows))
        tbl = gf.table
        widths = widths or [1] * len(headers)
        tot = sum(widths)
        for i, wt in enumerate(widths):
            tbl.columns[i].width = Emu(int(w * wt / tot))
        for r in range(n_rows):
            tbl.rows[r].height = row_h
            vals = headers if r == 0 else rows[r - 1]
            for c, v in enumerate(vals):
                cell = tbl.cell(r, c)
                cell.margin_left = cell.margin_right = Emu(45720)
                cell.margin_top = cell.margin_bottom = Emu(22860)
                cell.text = str(v if v is not None else "")
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(header_size or size) if r == 0 else Pt(size)
                        run.font.bold = r == 0
        return tbl

    def paged_table(self, title, headers, rows, widths=None, per_page=12, size=10, ai=False, intro=None):
        pages = [rows[i:i + per_page] for i in range(0, len(rows), per_page)] or [[]]
        for i, chunk in enumerate(pages):
            s, (l, t, w, h) = self.content(title + (f" ({i + 1}/{len(pages)})" if len(pages) > 1 else ""))
            if ai:
                self.tag_ai(s)
            if intro and i == 0:
                self.text(s, (l, t, w, int(h * 0.12)), [intro], size=12)
                t += int(h * 0.13)
                h -= int(h * 0.13)
            if chunk:
                self.table(s, (l, t, w, h), headers, chunk, widths, size)
            else:
                self.text(s, (l, t, w, h), ["None."])

    def tiles(self, slide, rect, items, color=ACC_SCREEN):
        l, t, w, h = rect
        n = len(items)
        gap = int(w * 0.015)
        tw = int((w - gap * (n - 1)) / n)
        for i, (num, label) in enumerate(items):
            col = color
            if isinstance(num, tuple):
                num, col = num
            sh = self.box(slide, (l + i * (tw + gap), t, tw, h), f"{num}\n{label}", col, size=28, bold=True)
            sh.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE

    def save(self, path):
        self.prs.save(path)


# ============================================================================= diagrams

def draw_navigation(d, slide, rect, m):
    a = m["analysis"]
    names = [s["name"] for s in m["screens"]]
    if not names:
        return
    edges = [(e["from"], e["to"], e["conditional"]) for e in a["navigation"]
             if e["from"] in names and e["to"] in names and e["from"] != e["to"]]
    # rank screens by first-reach distance from the start screen (BFS), unreachable last
    rank = {a["start_screen"]: 0} if a["start_screen"] in names else {}
    frontier = list(rank)
    while frontier:
        nxt = []
        for f in frontier:
            for x, y, _ in edges:
                if x == f and y not in rank:
                    rank[y] = rank[f] + 1
                    nxt.append(y)
        frontier = nxt
    maxr = max(rank.values(), default=-1)
    for n in names:
        if n not in rank:
            maxr += 1
            rank[n] = maxr
    cols = {}
    for n in names:
        cols.setdefault(rank[n], []).append(n)
    l, t, w, h = rect
    ncol = max(cols) + 1
    per_row = min(ncol, 6)
    bands = (ncol + per_row - 1) // per_row
    gap_x = int(w * 0.035)
    bw = int((w - gap_x * (per_row - 1)) / per_row)
    band_h = int(h / bands)
    shapes = {}
    for r, members in cols.items():
        band, ci = divmod(r, per_row)
        if band % 2 == 1:
            ci = per_row - 1 - ci  # snake layout keeps arrows short
        bh = min(int(band_h * 0.6 / len(members)), int(h * 0.16))
        x = l + ci * (bw + gap_x)
        top = t + band * band_h + int((band_h - len(members) * bh * 1.35) / 2)
        for k, n in enumerate(members):
            col = ACC_SCREEN if n != a["start_screen"] else MSO_THEME_COLOR.ACCENT_3
            shapes[n] = d.box(slide, (x, top + int(k * bh * 1.35), bw, bh), n, col, size=11, bold=True)
    for x, y, cond in edges:
        fwd = rank[y] > rank[x]
        same_band = rank[x] // per_row == rank[y] // per_row
        if fwd and same_band:
            left_to_right = (rank[x] // per_row) % 2 == 0
            d.arrow(slide, shapes[x], shapes[y], 3 if left_to_right else 1, 1 if left_to_right else 3, dashed=cond)
        elif fwd:
            d.arrow(slide, shapes[x], shapes[y], 2, 0, dashed=cond)
        else:
            d.arrow(slide, shapes[x], shapes[y], 2, 2, curved=True, dashed=True, weight=0.75,
                    color=MSO_THEME_COLOR.ACCENT_6)


def draw_architecture(d, slide, rect, m):
    a = m["analysis"]
    l, t, w, h = rect
    screens = a["screens"]
    ext = [x for x in m["data_sources"] if x["kind"] != "Collection"
           and (a["data_usage"].get(x["name"], {}).get("read_in") or a["data_usage"].get(x["name"], {}).get("written_in"))]
    col_w = int(w * 0.3)

    def stack(items, x, colorf, labelf):
        if not items:
            return {}
        gap = int(h * 0.025)
        bh = min(int((h - gap * (len(items) - 1)) / len(items)), int(h * 0.14))
        top = t + int((h - (bh * len(items) + gap * (len(items) - 1))) / 2)
        out = {}
        for i, it in enumerate(items):
            out[it] = d.box(slide, (x, top + i * (bh + gap), col_w, bh), labelf(it), colorf(it), size=11, bold=True)
        return out

    ss = stack([s["name"] for s in screens], l, lambda _: ACC_SCREEN, lambda n: n)
    by = {x["name"]: x for x in ext}
    kind_color = lambda n: ACC_FLOW if by[n]["kind"] == "Power Automate flow" else (ACC_CONN if by[n]["kind"] == "Connector" else ACC_DATA)
    es = stack([x["name"] for x in ext], l + w - col_w, kind_color,
               lambda n: f"{by[n].get('display_name') or n}\n{by[n]['kind'] if by[n]['kind'] != 'Connector' else by[n]['connector'] + ' connector'}")
    for s in screens:
        for nm in es:
            if nm in s["data_written"]:
                d.arrow(slide, ss[s["name"]], es[nm], 3, 1, weight=3, color=MSO_THEME_COLOR.ACCENT_2)
            elif nm in s["data_sources"] + s["flows"] + s["connectors"]:
                d.arrow(slide, ss[s["name"]], es[nm], 3, 1, weight=1)
    mid_l = l + col_w + int(w * 0.05)
    d.text(slide, (mid_l, t + h - int(h * 0.18), w - 2 * col_w - int(w * 0.1), int(h * 0.18)),
           [("Legend", 11, True), ("Thick arrow = writes data", 10, False), ("Thin arrow = reads / calls", 10, False),
            ("Colours: screens · data · flows · connectors", 10, False)], size=10)


def draw_er(d, slide, rect, entities):
    """entities: [(title, [(col, type, required, used)])]. Cards side by side."""
    l, t, w, h = rect
    n = len(entities)
    gap = int(w * 0.03)
    cw = int((w - gap * (n - 1)) / n)
    for i, (title, cols) in enumerate(entities):
        x = l + i * (cw + gap)
        hh = int(h * 0.1)
        d.box(slide, (x, t, cw, hh), title, ACC_DATA, size=13, bold=True, shape=MSO_SHAPE.RECTANGLE)
        maxrows = 20
        lines = [(f"{'★ ' if req else ''}{c}", t2, used) for c, t2, req, used in cols[:maxrows]]
        body = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, t + hh, cw, h - hh)
        body.fill.background()
        body.line.color.theme_color = ACC_DATA
        tf = body.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = MSO_ANCHOR.TOP
        size = 11 if len(lines) <= 12 else 9
        for k, (c, typ, used) in enumerate(lines):
            p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
            p.alignment = PP_ALIGN.LEFT
            r1 = p.add_run()
            r1.text = c
            r1.font.size = Pt(size)
            r1.font.bold = used
            r1.font.color.theme_color = MSO_THEME_COLOR.TEXT_1
            r2 = p.add_run()
            r2.text = f"  {typ}"
            r2.font.size = Pt(size - 1)
            r2.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
        if len(cols) > maxrows:
            p = tf.add_paragraph()
            r = p.add_run()
            r.text = f"+{len(cols) - maxrows} more (see Markdown)"
            r.font.size = Pt(9)
            r.font.italic = True


def draw_wireframe(d, slide, rect, wf):
    """Native-shape wireframe of one screen, scaled to fit rect and centred."""
    l, t, w, h = rect
    sc = min(w / wf["width"], h / wf["height"])
    ox = l + int((w - wf["width"] * sc) / 2)
    oy = t + int((h - wf["height"] * sc) / 2)
    E = lambda v: Emu(int(v * sc))
    grey, mid = RGBColor(0xC4, 0xCC, 0xD4), RGBColor(0x7A, 0x87, 0x94)

    frame = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, ox, oy, E(wf["width"]), E(wf["height"]))
    frame.fill.solid()
    frame.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    frame.line.color.rgb = mid
    frame.line.width = Pt(1.5)
    frame.shadow.inherit = False

    def rect_shape(b, fill=None, line=None, dashed=False, shape=MSO_SHAPE.RECTANGLE, lw=0.75):
        sh = slide.shapes.add_shape(shape, ox + E(b["x"]), oy + E(b["y"]), max(E(b["w"]), Emu(9525)), max(E(b["h"]), Emu(9525)))
        sh.shadow.inherit = False
        if fill is None:
            sh.fill.background()
        elif isinstance(fill, RGBColor):
            sh.fill.solid()
            sh.fill.fore_color.rgb = fill
        else:
            sh.fill.solid()
            sh.fill.fore_color.theme_color = fill
        if line is None:
            sh.line.fill.background()
        else:
            sh.line.color.rgb = line
            sh.line.width = Pt(lw)
            if dashed:
                sh.line.dash_style = 4
        return sh

    def label(b, text, color=RGBColor(0x33, 0x33, 0x33), italic=False, center=False, white=False, inset=0):
        pt = max(6, min(14, b["h"] * sc / 12700 * 0.5))
        tb = slide.shapes.add_textbox(ox + E(b["x"] + inset), oy + E(b["y"]), max(E(b["w"] - inset), Emu(9525)), E(b["h"]))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE if center or b["h"] < 60 else MSO_ANCHOR.TOP
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER if center else PP_ALIGN.LEFT
        r = p.add_run()
        r.text = text
        r.font.size = Pt(pt)
        r.font.italic = italic
        if white:
            r.font.color.theme_color = MSO_THEME_COLOR.BACKGROUND_1
        else:
            r.font.color.rgb = color

    for b in wf["boxes"]:
        k, dashed = b["kind"], b["conditional"] or b["approx"]
        txt = b["text"]
        if k == "container":
            if b["w"] < wf["width"] - 2 or b["h"] < wf["height"] - 2:
                rect_shape(b, line=RGBColor(0xDD, 0xE1, 0xE6), dashed=dashed, lw=0.5)
        elif k == "gallery":
            g = rect_shape(b, fill=RGBColor(0xF6, 0xF8, 0xFA), line=mid, dashed=dashed)
            rh = b.get("row_height") or 0
            for r in range(1, b.get("rows", 1)):
                y = b["y"] + r * rh
                if rh and y < b["y"] + b["h"]:
                    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, ox + E(b["x"] + 6), oy + E(y),
                                                    ox + E(b["x"] + b["w"] - 6), oy + E(y))
                    ln.line.color.rgb = grey
                    ln.line.width = Pt(0.5)
                    ln.line.dash_style = 4
        elif k == "button":
            rect_shape(b, fill=ACC_SCREEN, line=RGBColor(0x22, 0x32, 0x4A) if dashed else None, dashed=dashed,
                       shape=MSO_SHAPE.ROUNDED_RECTANGLE)
            label(b, txt or "Button", center=True, white=True)
        elif k in ("input", "dropdown", "richtext"):
            rect_shape(b, fill=RGBColor(0xFF, 0xFF, 0xFF), line=mid, dashed=dashed)
            hint = txt or {"input": "text input", "dropdown": "select…  ▾", "richtext": "rich text"}[k]
            label(dict(b, h=min(b["h"], 32)), hint, color=RGBColor(0x9A, 0xA3, 0xAD), inset=6)
        elif k in ("radio", "checkbox", "toggle"):
            label(dict(b, h=min(b["h"], 30)), {"radio": "◉ ", "checkbox": "☑ ", "toggle": "⬭ "}[k] + (txt or k))
        elif k in ("image", "icon"):
            rect_shape(b, fill=RGBColor(0xEE, 0xF1, 0xF4), line=grey, dashed=dashed)
            for a_, b_ in (((b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"])),
                           ((b["x"] + b["w"], b["y"]), (b["x"], b["y"] + b["h"]))):
                ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, ox + E(a_[0]), oy + E(a_[1]), ox + E(b_[0]), oy + E(b_[1]))
                ln.line.color.rgb = grey
                ln.line.width = Pt(0.5)
        elif k == "shape":
            rect_shape(b, fill=grey)
        elif k == "form":
            rect_shape(b, fill=RGBColor(0xFB, 0xFC, 0xFD), line=mid, dashed=dashed)
        elif txt:
            if txt == "ƒx":
                label(dict(b, h=min(b["h"], 30)), "ƒx", color=RGBColor(0x9A, 0xA3, 0xAD), italic=True)
            else:
                label(dict(b, h=min(b["h"], 40)), txt)

    r = max(E(MARKER_R), Emu(Pt(8)))
    for b in wf["boxes"]:  # markers last so they sit on top of everything
        if b.get("callout"):
            mx, my = marker_pos(b, wf["width"])
            add_marker(slide, ox + E(mx), oy + E(my), r, b["callout"])


MARKER_RGB = RGBColor.from_string(MARKER_COLOR.lstrip("#"))


def add_marker(slide, cx, cy, r, n):
    """Bright numbered circle centred on (cx, cy) in EMU."""
    m = slide.shapes.add_shape(MSO_SHAPE.OVAL, cx - r, cy - r, 2 * r, 2 * r)
    m.shadow.inherit = False
    m.fill.solid()
    m.fill.fore_color.rgb = MARKER_RGB
    m.line.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    m.line.width = Pt(1)
    tf = m.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = str(n)
    run.font.size = Pt(9 if n < 10 else 8)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return m


def callout_slides(d, title, rows, has_ai):
    per = 7 if has_ai else 9
    pages = [rows[i:i + per] for i in range(0, len(rows), per)]
    for pi, chunk in enumerate(pages):
        s, (l, t, w, h) = d.content(title + (f" ({pi + 1}/{len(pages)})" if len(pages) > 1 else ""))
        if has_ai:
            d.tag_ai(s)
            headers, widths = ["#", "Control", "What it does", "AI context"], [0.35, 1.6, 3.2, 2.6]
            data = [(n, f"{name}\n{typ}", clip(does, 260), clip(note, 220)) for n, name, typ, does, note in chunk]
        else:
            headers, widths = ["#", "Control", "What it does"], [0.35, 1.8, 4.6]
            data = [(n, f"{name}\n{typ}", clip(does, 320)) for n, name, typ, does, _ in chunk]
        tbl = d.table(s, (l, t, w, h), headers, data, widths, size=10)
        for r in range(1, len(data) + 1):
            cell = tbl.cell(r, 0)
            cell.fill.solid()
            cell.fill.fore_color.rgb = MARKER_RGB
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            for p in cell.text_frame.paragraphs:
                p.alignment = PP_ALIGN.CENTER
                for run in p.runs:
                    run.font.bold = True
                    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


def draw_flow_chain(d, slide, rect, flow):
    l, t, w, h = rect
    steps = [("Trigger: " + tr["name"], tr["kind"] or tr["type"]) for tr in flow["triggers"][:1]]
    steps += [(x["name"].replace("_", " "), x["operation"] or x["type"]) for x in flow["actions"] if x["depth"] == 0]
    per_row = 5
    rows = max(1, (len(steps) + per_row - 1) // per_row)
    gap = int(w * 0.03)
    bw = int((w - gap * (per_row - 1)) / per_row)
    bh = min(int(h / rows * 0.55), int(h * 0.2))
    prev = None
    for i, (name, sub) in enumerate(steps[:per_row * 4]):
        r, c = divmod(i, per_row)
        if r % 2:
            c = per_row - 1 - c
        sh = d.box(slide, (l + c * (bw + gap), t + r * int(h / rows), bw, bh), f"{clip(name, 40)}\n{clip(sub, 40)}",
                   ACC_FLOW if i else MSO_THEME_COLOR.ACCENT_3, size=10, bold=True)
        if prev is not None:
            pr, _ = divmod(i - 1, per_row)
            if pr == r:
                d.arrow(slide, prev, sh, 3 if r % 2 == 0 else 1, 1 if r % 2 == 0 else 3)
            else:
                d.arrow(slide, prev, sh, 2, 0)
        prev = sh


# ============================================================================= deck

def build(m, ai, template, out_path):
    d = Deck(template)
    info, a, st = m["app_info"], m["analysis"], m["stats"]
    has_ai = bool(ai)

    d.title_slide(info["name"], f"App documentation & maintenance guide\nGenerated from {m['source']['file']} · "
                                f"last saved {info['saved_utc']} UTC")

    # ---- at a glance
    s, (l, t, w, h) = d.content("At a glance")
    active, dismissed = merged_findings(m, ai)
    n_med_high = sum(1 for f in active if f["severity"] in ("High", "Medium"))
    d.tiles(s, (l, t, w, int(h * 0.27)), [
        (st["screens"], "screens"), (st["controls"], "controls"), ((st["tables"], ACC_DATA), "data sources"),
        ((st["flows"] + st["connectors"], ACC_FLOW), "flows & connectors"),
        ((len(active), MSO_THEME_COLOR.ACCENT_5), f"findings ({n_med_high} med/high)")])
    facts = [("App type", f"{info['app_type']} · {info['layout']['width']}×{info['layout']['height']} {info['layout']['orientation']}"),
             ("Created from", info["created_from"] or "-"), ("Start screen", a["start_screen"]),
             ("Last saved", f"{info['saved_utc']} UTC (studio {info['doc_version']})"),
             ("Row limit for non-delegable queries", str(info["row_limit"])),
             ("Connections", ", ".join(c["connector"] for c in info["connections"]) or "-")]
    top2 = t + int(h * 0.33)
    if ai.get("summary"):
        d.table(s, (l, top2, int(w * 0.48), h - int(h * 0.33)), ["Fact", "Value"], facts, [1, 1.6], size=11)
        d.text(s, (l + int(w * 0.52), top2, int(w * 0.48), h - int(h * 0.33)),
               [("What it does (AI interpretation)", 14, True), (ai["summary"], 13, False)]
               + ([("Who uses it", 14, True), (ai["audience"], 13, False)] if ai.get("audience") else []))
    else:
        d.table(s, (l, top2, w, h - int(h * 0.33)), ["Fact", "Value"], facts, [1, 2.5], size=12)

    if ai.get("user_journeys"):
        s, (l, t, w, h) = d.content("User journeys")
        d.tag_ai(s)
        js = ai["user_journeys"][:3]
        cw = int(w / len(js))
        for i, j in enumerate(js):
            d.text(s, (l + i * cw, t, cw - int(w * 0.02), h),
                   [(j.get("name", ""), 16, True)] + [(f"{k}. {x}", 12, False) for k, x in enumerate(j.get("steps", []), 1)])

    # ---- architecture
    d.section("How the app fits together", "Screens, data, flows and navigation")
    s, rect = d.content("Architecture: screens, data & services")
    draw_architecture(d, s, rect, m)
    s, (l, t, w, h) = d.content("Screen navigation")
    draw_navigation(d, s, (l, t, w, int(h * 0.88)), m)
    d.text(s, (l, t + int(h * 0.9), w, int(h * 0.1)),
           ["Solid arrow = Navigate() to the next screen · dashed = conditional · curved = back/return path · "
            "highlighted box = start screen"], size=10)

    # ---- screens
    d.section("Screens", f"{st['screens']} screens, {st['controls']} controls")
    d.paged_table("Screen inventory", ["#", "Screen", "Controls", "Inputs", "Data / services", "Goes to"],
                  [(i, x["name"], x["control_count"], len(x["inputs"]), ", ".join(x["data_sources"] + x["flows"] + x["connectors"]) or "-",
                    ", ".join(x["navigates_to"]) or "-") for i, x in enumerate(a["screens"], 1)],
                  [0.4, 2, 0.8, 0.7, 2, 2.4], per_page=12, size=11)
    for i, x in enumerate(a["screens"], 1):
        wf = next((sc.get("wireframe") for sc in m["screens"] if sc["name"] == x["name"]), None)
        if wf and wf["boxes"]:
            s, (l, t, w, h) = d.content(f"Screen {i}: {x['name']} (layout)")
            draw_wireframe(d, s, (l, t, w, int(h * 0.93)), wf)
            d.text(s, (l, t + int(h * 0.94), w, int(h * 0.06)),
                   ["Broad-strokes wireframe at design size · filled = buttons · outlined = inputs · crossed = images · "
                    "dashed = conditional/approximate · {Field} = data-bound · ƒx = computed text · numbers = see next slide"], size=9)
            rows, has_ai = callout_rows(m, x["name"], ai)
            if rows:
                callout_slides(d, f"Screen {i}: {x['name']} (key)", rows, has_ai)
        s, (l, t, w, h) = d.content(f"Screen {i}: {x['name']}")
        note = (ai.get("screens") or {}).get(x["name"])
        lw = int(w * 0.4) if not callout_rows(m, x["name"], ai)[0] else w
        paras = []
        if note:
            paras += [("Purpose (AI interpretation)", 13, True), (note, 12, False)]
        paras += [("Facts", 13, True)]
        for k, v in [("Reached from", ", ".join(x["navigated_from"]) or ("app start" if x["name"] == a["start_screen"] else "-")),
                     ("Goes to", ", ".join(x["navigates_to"]) or "-"),
                     ("Reads", ", ".join(x["data_sources"] + x["connectors"] + x["flows"]) or "-"),
                     ("Writes", ", ".join(x["data_written"]) or "-"),
                     ("Variables", ", ".join(sorted(set(x["variables_set"] + x["variables_read"]))) or "-"),
                     ("Collections", ", ".join(x["collections"]) or "-"),
                     ("Controls", f"{x['control_count']}")]:
            paras.append((f"{k}: {v}", 11, False))
        d.text(s, (l, t, lw, h), paras)
        rows = [(i2["name"], "input", i2["type"]) for i2 in x["inputs"]]
        rows += [(f"{b['control']}.{b['property']}", "binding", clip(b["formula"], 90)) for b in x["bindings"]]
        rows += [(f"{b['control']}.{b['property']}", "action", fx_summary(b["formula"], 110)) for b in x["actions"]]
        if rows and not callout_rows(m, x["name"], ai)[0]:  # the wireframe key already covers these
            more = len(rows) - 14
            d.table(s, (l + lw + int(w * 0.03), t, w - lw - int(w * 0.03), h), ["Control", "Kind", "What it does"],
                    rows[:14] + ([(f"+{more} more", "", "see Markdown")] if more > 0 else []), [1.6, 0.6, 2.6], size=9)

    # ---- data
    d.section("Data", "Lists, tables, variables and collections")
    ents = []
    for ds in m["data_sources"]:
        if ds.get("columns") and not ds["is_sample"]:
            used = set(a["data_usage"].get(ds["name"], {}).get("columns_used", []))
            cols = [(c["name"], c["type"], c["required"], c["name"] in used) for c in ds["columns"]
                    if not c["system"] or c["name"] in used or c["primary_key"]]
            ents.append((f"{ds.get('display_name') or ds['name']} ({ds['kind']})", cols))
    for c in a["collections"]:
        if c["inferred_fields"]:
            ents.append((f"{c['name']} (collection)", [(f, "field", False, True) for f in c["inferred_fields"]]))
    for i in range(0, len(ents), 3):
        s, (l, t, w, h) = d.content("Data model" + (f" ({i // 3 + 1})" if len(ents) > 3 else ""))
        draw_er(d, s, (l, t, w, int(h * 0.92)), ents[i:i + 3])
        d.text(s, (l, t + int(h * 0.93), w, int(h * 0.07)), ["★ required · bold = used by the app · system columns hidden unless used"], size=10)
    for ds in m["data_sources"]:
        if ds.get("columns") and not ds["is_sample"]:
            used = set(a["data_usage"].get(ds["name"], {}).get("columns_used", []))
            u = a["data_usage"].get(ds["name"], {})
            rows = [(c["name"], c["display_name"] if c["display_name"] != c["name"] else "", c["type"],
                     "yes" if c["required"] else "", "✓" if c["name"] in used else "")
                    for c in ds["columns"] if not c["system"] or c["name"] in used or c["primary_key"]]
            intro = (f"{ds['kind']} · {ds.get('site') or ''}  ·  written in: {'; '.join(u.get('written_in', [])) or '-'}")
            d.paged_table(f"{ds.get('display_name') or ds['name']}: columns", ["Column", "Display name", "Type", "Required", "Used"],
                          rows, [1.6, 1.6, 1.4, 0.7, 0.5], per_page=11, size=10, intro=intro)
    vrows = [(v["name"], v["scope"], "; ".join(v["set_in"][:2]), "; ".join(v["read_in"][:2]) + (" …" if len(v["read_in"]) > 2 else "") or "never")
             for v in a["variables"]]
    vrows += [(c["name"], "collection", "; ".join(c["written_in"][:2]), "; ".join(c["read_in"][:2]) + (" …" if len(c["read_in"]) > 2 else "") or "never")
              for c in a["collections"]]
    d.paged_table("Variables & collections", ["Name", "Kind", "Set / filled in", "Read in"], vrows, [1, 1, 2, 3], per_page=10, size=10)
    if ai.get("data_notes"):
        s, rect = d.content("Data: what to know")
        d.tag_ai(s)
        d.text(s, rect, [ai["data_notes"]], size=14)

    # ---- integrations
    ext = [x for x in m["data_sources"] if x["kind"] in ("Connector", "Power Automate flow")]
    if ext or m["solution_flows"]:
        d.section("Flows & integrations")
        rows = [(x["name"], x["kind"], x["connector"], ", ".join(a["data_usage"].get(x["name"], {}).get("operations", [])) or "-",
                 "; ".join(a["data_usage"].get(x["name"], {}).get("read_in", [])[:3]) or "not used") for x in ext]
        intro = ai.get("integration_notes") or ("Flow internals are not stored in a .msapp; supply the solution export to document them."
                                               if any(x["kind"] == "Power Automate flow" for x in ext) and not m["solution_flows"] else None)
        d.paged_table("Connectors & flows used", ["Name", "Kind", "Connector", "Operations", "Called from"], rows,
                      [1.2, 1.2, 1.2, 1.2, 2.6], per_page=10, size=10, intro=intro)
        for fl in m["solution_flows"]:
            s, (l, t, w, h) = d.content(f"Flow: {fl['name']}")
            draw_flow_chain(d, s, (l, t, w, int(h * 0.8)), fl)
            d.text(s, (l, t + int(h * 0.85), w, int(h * 0.15)), [f"Connections: {', '.join(fl['connections']) or '-'} · "
                                                                f"{len(fl['actions'])} steps in total (nested steps in Markdown)"], size=10)

    # ---- findings
    d.section("Possible defects & areas to fix", "A maintenance backlog — and patterns to avoid in a rebuild")
    s, (l, t, w, h) = d.content("Findings overview")
    counts = {k: sum(1 for f in active if f["severity"] == k) for k in ("High", "Medium", "Low")}
    for i, k in enumerate(("High", "Medium", "Low")):
        tw = int(w * 0.31)
        sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, l + i * int(w * 0.345), t, tw, int(h * 0.22))
        sh.fill.solid()
        sh.fill.fore_color.rgb = SEV_RGB[k]
        sh.line.fill.background()
        tf = sh.text_frame
        tf.text = f"{counts[k]}\n{k}"
        for j, p in enumerate(tf.paragraphs):
            p.alignment = PP_ALIGN.CENTER
            for r in p.runs:
                r.font.size = Pt(30 if j == 0 else 14)
                r.font.bold = True
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    cats = {}
    for f in active:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    d.text(s, (l, t + int(h * 0.28), w, int(h * 0.7)),
           [("By category", 14, True)] + [(f"{c}: {n}", 13, False) for c, n in sorted(cats.items(), key=lambda kv: -kv[1])]
           + [("Sources: rule-based checks on every formula, the Power Apps App Checker results saved in the app"
               + (", and an AI review" if has_ai else ""), 11, False)])
    rows = [(f["id"], f["severity"], f["title"] + (f"  [{f['verdict']}]" if f.get("verdict") else ""),
             str(f["where_count"]), clip(f["recommendation"], 170)) for f in active]
    d.paged_table("Findings & recommendations", ["ID", "Severity", "Finding", "Places", "Recommendation"], rows,
                  [0.45, 0.7, 2.2, 0.5, 3.4], per_page=6, size=10)

    # ---- rebuild
    rb = ai.get("rebuild") or {}
    d.section("Rebuilding as a code app", "What the replacement must do, and what not to copy")
    if rb.get("overview") or rb.get("suggested_architecture"):
        s, (l, t, w, h) = d.content("Rebuild approach")
        d.tag_ai(s)
        paras = []
        if rb.get("overview"):
            paras += [("Overview", 14, True), (rb["overview"], 12, False)]
        if rb.get("suggested_architecture"):
            paras += [("Suggested architecture", 14, True), (rb["suggested_architecture"], 12, False)]
        d.text(s, (l, t, w, h), paras)
    if rb.get("features"):
        d.paged_table("Features to rebuild", ["Feature", "Behaviour", "Where in the app"],
                      [(x.get("name", ""), clip(x.get("behaviour", ""), 200), clip(x.get("source", ""), 80)) for x in rb["features"]],
                      [1.3, 3.5, 1.6], per_page=7, size=10, ai=True)
    if rb.get("business_rules"):
        s, rect = d.content("Business rules & validation")
        d.tag_ai(s)
        d.bullets(s, rect, rb["business_rules"][:12], size=12)
    avoid = list(rb.get("avoid") or []) + [f["title"] for f in active if f["severity"] in ("High", "Medium")]
    s, rect = d.content("Patterns not to carry over")
    d.bullets(s, rect, avoid[:12], size=13)

    if ai.get("maintainer_notes") or ai.get("open_questions"):
        s, (l, t, w, h) = d.content("Notes for maintainers")
        d.tag_ai(s)
        half = int(w * 0.48)
        d.bullets(s, (l, t, half if ai.get("open_questions") else w, h), (ai.get("maintainer_notes") or [])[:10], size=12,
                  heading="Tips")
        if ai.get("open_questions"):
            d.bullets(s, (l + int(w * 0.52), t, half, h), ai["open_questions"][:8], size=12, heading="Open questions")

    s, rect = d.content("About this document")
    d.bullets(s, rect, [
        f"Generated by msapp-documenter {m['generator_version']} from {m['source']['file']} (sha256 {m['source']['sha256'][:16]}…).",
        f"{st['rules_total']} property formulas read; {st['rules_kept']} kept after removing layout, static styling, defaults and empty handlers "
        f"({st['raw_kb']} KB of app JSON → {st['clean_kb']} KB of clean source).",
        "Everything without an 'AI interpretation' label is extracted deterministically: re-running on the same file gives the same content.",
        "AI-labelled content was inferred from the app's formulas and metadata; verify it before acting on it.",
        "The companion Markdown file has full formulas, Mermaid diagrams and an AI-ready context pack (ai_context/).",
    ], size=13)
    d.save(out_path)
