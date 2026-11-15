"""Small layout engine used by build_deck.py.

Every drawing call writes the same geometry to two places:

* a python-pptx slide  -> the editable deliverable (native shapes, text, tables)
* a Pillow image       -> a preview used to check layout without PowerPoint

Text is wrapped with an Arial-metric font (Arial or Liberation Sans) so the
preview wraps where PowerPoint does, and every text box reports when its
content is taller than the box. Nothing here is specific to one presentation.
"""

from __future__ import annotations

import copy
import os
import re
import uuid
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

SLIDE_W, SLIDE_H = 13.333, 7.5          # inches, 16:9
SCALE = 144                             # preview pixels per inch
FONT = "Arial"
LINE_FACTOR = 1.15                      # Arial single-line height / font size

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


# --------------------------------------------------------------------------
# fonts and text measurement
# --------------------------------------------------------------------------
_FONT_FILES = {
    (False, False): ["LiberationSans-Regular.ttf", "arial.ttf", "Arial.ttf", "Arimo-Regular.ttf"],
    (True, False): ["LiberationSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "Arimo-Bold.ttf"],
    (False, True): ["LiberationSans-Italic.ttf", "ariali.ttf", "Arial Italic.ttf", "Arimo-Italic.ttf"],
    (True, True): ["LiberationSans-BoldItalic.ttf", "arialbi.ttf", "Arial Bold Italic.ttf",
                   "Arimo-BoldItalic.ttf"],
}
_FONT_DIRS = [
    os.environ.get("DECK_FONT_DIR", ""),
    str(Path.home() / ".fonts"),
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/liberation",
    "/usr/share/fonts/truetype/msttcorefonts",
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "C:/Windows/Fonts",
]
_font_cache: dict = {}
METRIC_FONTS_FOUND = True


def _font_path(bold: bool, italic: bool) -> str | None:
    for d in _FONT_DIRS:
        if not d:
            continue
        for name in _FONT_FILES[(bold, italic)]:
            p = Path(d) / name
            if p.exists():
                return str(p)
    return None


def font(size_pt: float, bold=False, italic=False):
    """Pillow font at preview scale. Falls back to Pillow's default face."""
    global METRIC_FONTS_FOUND
    key = (round(size_pt, 2), bool(bold), bool(italic))
    if key not in _font_cache:
        px = size_pt * SCALE / 72.0
        path = _font_path(bool(bold), bool(italic))
        if path:
            _font_cache[key] = ImageFont.truetype(path, px)
        else:
            METRIC_FONTS_FOUND = False
            _font_cache[key] = ImageFont.load_default(px)
    return _font_cache[key]


def rgb(hexstr: str):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _color(hexstr: str) -> RGBColor:
    return RGBColor.from_string(hexstr.lstrip("#").upper())


# --------------------------------------------------------------------------
# rich text model
# --------------------------------------------------------------------------
_MARK = re.compile(r"(\*\*.+?\*\*|__.+?__|\[\[.+?\]\]|<<.+?>>)")


def parse_runs(text: str, styles: dict) -> list:
    """Mini markup: **bold**  __italic__  [[to-verify]]  <<accent>>."""
    out = []
    for part in _MARK.split(text):
        if not part:
            continue
        if part.startswith("**"):
            out.append((part[2:-2], {"bold": True}))
        elif part.startswith("__"):
            out.append((part[2:-2], {"italic": True}))
        elif part.startswith("[["):
            out.append((part[2:-2], dict(styles.get("verify", {"italic": True}))))
        elif part.startswith("<<"):
            out.append((part[2:-2], dict(styles.get("accent", {"bold": True}))))
        else:
            out.append((part, {}))
    return out


class Para:
    """One paragraph. `text` uses the mini markup above."""

    def __init__(self, text="", size=None, color=None, bold=None, italic=None, align=None,
                 bullet=False, indent=0.0, sb=0.0, sa=0.0, ls=None, track=0.0):
        self.text = text
        self.size, self.color, self.bold, self.italic = size, color, bold, italic
        self.align, self.bullet, self.indent = align, bullet, indent
        self.sb, self.sa, self.ls, self.track = sb, sa, ls, track


def as_paras(content) -> list:
    if content is None:
        return []
    if isinstance(content, (str, Para)):
        content = [content]
    return [c if isinstance(c, Para) else Para(c) for c in content]


BULLET_GAP = 0.17        # inches from bullet to text


class TextLayout:
    """Wraps paragraphs to a width and records line positions (inches)."""

    def __init__(self, paras, width, base, styles):
        self.lines = []          # (y_top, line_h, x_offset, segs, align, para_width, size)
        self.height = 0.0
        self.max_word_overflow = 0.0
        y = 0.0
        for pi, p in enumerate(paras):
            size = p.size or base["size"]
            ls = p.ls or base.get("ls", 1.0)
            line_h = size * LINE_FACTOR * ls / 72.0
            indent = p.indent + (BULLET_GAP if p.bullet else 0.0)
            avail = max(width - indent, 0.2)
            if pi > 0:
                y += p.sb / 72.0
            segs_all = []
            for txt, st in parse_runs(p.text, styles):
                style = {
                    "bold": st.get("bold", p.bold if p.bold is not None else base.get("bold", False)),
                    "italic": st.get("italic", p.italic if p.italic is not None else base.get("italic", False)),
                    "color": st.get("color", p.color or base["color"]),
                    "size": size, "track": p.track or base.get("track", 0.0),
                }
                for tok in re.findall(r"\S+|\s+", txt):
                    segs_all.append((tok, style))
            cur, cur_w, first = [], 0.0, True
            lines = []
            for tok, st in segs_all:
                w = self._width(tok, st)
                if tok.isspace():
                    if cur:
                        cur.append((tok, st, w))
                        cur_w += w
                    continue
                if cur and cur_w + w > avail + 1e-6:
                    lines.append(cur)
                    cur, cur_w = [], 0.0
                if w > avail:
                    self.max_word_overflow = max(self.max_word_overflow, w - avail)
                cur.append((tok, st, w))
                cur_w += w
            lines.append(cur)
            for li, ln in enumerate(lines):
                while ln and ln[-1][0].isspace():
                    ln.pop()
                lw = sum(s[2] for s in ln)
                self.lines.append({
                    "y": y, "h": line_h, "indent": indent, "segs": ln, "w": lw,
                    "align": p.align or base.get("align", "l"), "avail": avail,
                    "bullet": p.bullet and li == 0, "bullet_x": p.indent, "size": size,
                    "color": p.color or base["color"],
                })
                y += line_h
            y += p.sa / 72.0
        self.height = y

    @staticmethod
    def _width(tok, st):
        f = font(st["size"], st["bold"], st["italic"])
        w = f.getlength(tok) / SCALE
        if st["track"]:
            w += st["track"] / 72.0 * len(tok)
        return w


# --------------------------------------------------------------------------
# deck / slide
# --------------------------------------------------------------------------
class Deck:
    def __init__(self, styles: dict, preview: bool = True):
        self.prs = Presentation()
        self.prs.slide_width = Inches(SLIDE_W)
        self.prs.slide_height = Inches(SLIDE_H)
        self.styles = styles
        self.preview = preview
        self.slides: list[Slide] = []
        self.warnings: list[str] = []
        self.layout = self.prs.slide_layouts[5]      # "Title Only"
        self._image_cache: dict = {}

    def slide(self, bg="#FFFFFF") -> "Slide":
        s = Slide(self, bg)
        self.slides.append(s)
        return s

    def warn(self, msg: str):
        self.warnings.append(msg)

    def set_theme(self, fonts: str, colors: dict):
        """Make new shapes/text the user adds default to the deck's font and palette."""
        master_part = self.prs.slide_master.part
        for rel in master_part.rels.values():
            if rel.reltype.endswith("/theme"):
                part = rel.target_part
                xml = part.blob.decode("utf-8")
                xml = re.sub(r'(<a:(?:major|minor)Font>\s*<a:latin typeface=")[^"]*(")',
                             rf"\g<1>{fonts}\g<2>", xml)
                for tag, hexv in colors.items():
                    xml = re.sub(rf"(<a:{tag}>)\s*<a:(?:srgbClr|sysClr)[^>]*/>\s*(</a:{tag}>)",
                                 rf'\g<1><a:srgbClr val="{hexv.lstrip("#").upper()}"/>\g<2>', xml)
                part._blob = xml.encode("utf-8")

    def save(self, path):
        self.prs.save(str(path))

    def save_previews(self, outdir):
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        for i, s in enumerate(self.slides, 1):
            s.img.convert("RGB").save(outdir / f"slide_{i:02d}.png")


class Slide:
    def __init__(self, deck: Deck, bg: str):
        self.deck = deck
        self.s = deck.prs.slides.add_slide(deck.layout)
        self.n = len(deck.prs.slides)
        self.title_ph = self.s.shapes.title
        self._title_used = False
        self.img = Image.new("RGB", (int(SLIDE_W * SCALE), int(SLIDE_H * SCALE)), rgb(bg))
        self.d = ImageDraw.Draw(self.img)
        if bg.upper() not in ("#FFFFFF", "FFFFFF"):
            fill = self.s.background.fill
            fill.solid()
            fill.fore_color.rgb = _color(bg)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _px(v):
        return int(round(v * SCALE))

    def _base(self, size, color, bold, italic, align, ls, track):
        return {"size": size, "color": color, "bold": bold, "italic": italic,
                "align": align, "ls": ls, "track": track}

    def measure(self, content, width, size=12, bold=False, italic=False, ls=1.0, track=0.0):
        """Height in inches that `content` needs at `width`."""
        lay = TextLayout(as_paras(content), width,
                         self._base(size, "#000000", bold, italic, "l", ls, track), self.deck.styles)
        return lay.height

    # ------------------------------------------------------------ text frame
    def _fill_frame(self, tf, paras, base, inset, anchor):
        tf.word_wrap = True
        tf.auto_size = MSO_AUTO_SIZE.NONE
        l, t, r, b = inset
        tf.margin_left, tf.margin_top = Inches(l), Inches(t)
        tf.margin_right, tf.margin_bottom = Inches(r), Inches(b)
        tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
        for i, p in enumerate(paras):
            par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            par.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER,
                             "r": PP_ALIGN.RIGHT}[p.align or base["align"]]
            par.line_spacing = p.ls or base["ls"]
            par.space_before = Pt(p.sb if i > 0 else 0)
            par.space_after = Pt(p.sa)
            size = p.size or base["size"]
            for txt, st in parse_runs(p.text, self.deck.styles):
                run = par.add_run()
                run.text = txt
                f = run.font
                f.name = FONT
                f.size = Pt(size)
                f.bold = st.get("bold", p.bold if p.bold is not None else base["bold"])
                f.italic = st.get("italic", p.italic if p.italic is not None else base["italic"])
                f.color.rgb = _color(st.get("color", p.color or base["color"]))
                track = p.track or base["track"]
                if track:
                    run._r.get_or_add_rPr().set("spc", str(int(track * 100)))
            pPr = par._p.get_or_add_pPr()
            if p.bullet or p.indent:
                indent = p.indent + (BULLET_GAP if p.bullet else 0.0)
                pPr.set("marL", str(int(Inches(indent))))
                pPr.set("indent", str(-int(Inches(BULLET_GAP))) if p.bullet else "0")
            if p.bullet:
                bu_clr = etree.SubElement(pPr, qn("a:buClr"))
                etree.SubElement(bu_clr, qn("a:srgbClr")).set(
                    "val", self.deck.styles.get("bullet_color", "000000").lstrip("#"))
                etree.SubElement(pPr, qn("a:buFont")).set("typeface", FONT)
                etree.SubElement(pPr, qn("a:buChar")).set("char", "•")
            else:
                etree.SubElement(pPr, qn("a:buNone"))

    def _draw_text(self, x, y, w, h, paras, base, inset, anchor, where=""):
        l, t, r, b = inset
        iw, ih = w - l - r, h - t - b
        lay = TextLayout(paras, iw, base, self.deck.styles)
        if lay.height > ih + 0.012:
            snippet = (paras[0].text if paras else "")[:48]
            self.deck.warn(f"slide {self.n}: text overflows box by {lay.height - ih:.2f} in "
                           f"(needs {lay.height:.2f}, has {ih:.2f}) :: {where}{snippet!r}")
        if lay.max_word_overflow > 0.01:
            self.deck.warn(f"slide {self.n}: a word is wider than its box by "
                           f"{lay.max_word_overflow:.2f} in :: {(paras[0].text if paras else '')[:48]!r}")
        if not self.deck.preview:
            return lay
        oy = y + t + {"t": 0.0, "m": (ih - lay.height) / 2, "b": ih - lay.height}[anchor]
        ox = x + l
        for ln in lay.lines:
            size = ln["size"]
            baseline = oy + ln["y"] + (ln["h"] - size * LINE_FACTOR / 72.0) + size * 0.905 / 72.0
            if ln["align"] == "c":
                cx = ox + ln["indent"] + (ln["avail"] - ln["w"]) / 2
            elif ln["align"] == "r":
                cx = ox + ln["indent"] + (ln["avail"] - ln["w"])
            else:
                cx = ox + ln["indent"]
            if ln["bullet"]:
                bf = font(size, False, False)
                self.d.text((self._px(ox + ln["bullet_x"]), self._px(baseline)), "•", font=bf,
                            fill=rgb(self.deck.styles.get("bullet_color", "#000000")), anchor="ls")
            for tok, st, tw in ln["segs"]:
                f = font(st["size"], st["bold"], st["italic"])
                if st["track"]:
                    xx = cx
                    for ch in tok:
                        self.d.text((self._px(xx), self._px(baseline)), ch, font=f,
                                    fill=rgb(st["color"]), anchor="ls")
                        xx += f.getlength(ch) / SCALE + st["track"] / 72.0
                else:
                    self.d.text((self._px(cx), self._px(baseline)), tok, font=f,
                                fill=rgb(st["color"]), anchor="ls")
                cx += tw
        return lay

    # ---------------------------------------------------------------- shapes
    def box(self, x, y, w, h, fill=None, line=None, lw=0.75, dash=False, radius=0.0,
            shape="rect", text=None, size=12, color="#000000", bold=False, italic=False,
            align="l", anchor="t", inset=0.08, ls=1.0, track=0.0, adj=0.3, name=None):
        """Auto-shape with optional text. shape: rect | round | oval | chevron | pentagon."""
        kinds = {"rect": MSO_SHAPE.RECTANGLE, "round": MSO_SHAPE.ROUNDED_RECTANGLE,
                 "oval": MSO_SHAPE.OVAL, "chevron": MSO_SHAPE.CHEVRON, "pentagon": MSO_SHAPE.PENTAGON}
        if radius and shape == "rect":
            shape = "round"
        shp = self.s.shapes.add_shape(kinds[shape], Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            shp.name = name
        shp.shadow.inherit = False
        if shape == "round":
            shp.adjustments[0] = min(0.5, radius / min(w, h))
        if shape in ("chevron", "pentagon"):
            shp.adjustments[0] = adj
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = _color(fill)
        else:
            shp.fill.background()
        if line:
            shp.line.color.rgb = _color(line)
            shp.line.width = Pt(lw)
            if dash:
                shp.line.dash_style = MSO_LINE.DASH
        else:
            shp.line.fill.background()
        ins = inset if isinstance(inset, tuple) else (inset, inset, inset, inset)
        if shape in ("chevron", "pentagon") and not isinstance(inset, tuple):
            a = adj * min(w, h)
            ins = ((a + 0.04) if shape == "chevron" else 0.1, 0.02, a * 0.9 + 0.02, 0.02)
        paras = as_paras(text)
        base = self._base(size, color, bold, italic, align, ls, track)
        if paras:
            self._fill_frame(shp.text_frame, paras, base, ins, anchor)

        if self.deck.preview:
            X0, Y0, X1, Y1 = self._px(x), self._px(y), self._px(x + w), self._px(y + h)
            fc = rgb(fill) if fill else None
            lc = rgb(line) if line else None
            lwp = max(1, int(round(lw * SCALE / 72.0)))
            if shape == "oval":
                self.d.ellipse([X0, Y0, X1, Y1], fill=fc, outline=None if dash else lc, width=lwp)
            elif shape in ("chevron", "pentagon"):
                a = self._px(adj * min(w, h))
                ym = (Y0 + Y1) // 2
                pts = [(X0, Y0), (X1 - a, Y0), (X1, ym), (X1 - a, Y1), (X0, Y1)]
                if shape == "chevron":
                    pts.append((X0 + a, ym))
                self.d.polygon(pts, fill=fc, outline=lc)
            else:
                rad = self._px(min(radius, min(w, h) / 2)) if shape == "round" else 0
                if rad:
                    self.d.rounded_rectangle([X0, Y0, X1, Y1], radius=rad, fill=fc,
                                             outline=None if dash else lc, width=lwp)
                else:
                    self.d.rectangle([X0, Y0, X1, Y1], fill=fc, outline=None if dash else lc, width=lwp)
            if dash and lc:
                self._dashed_rect(X0, Y0, X1, Y1, lc, lwp)
        if paras:
            self._draw_text(x, y, w, h, paras, base, ins, anchor)
        return shp

    def _dashed_rect(self, X0, Y0, X1, Y1, col, lwp):
        dash, gap = max(6, lwp * 4), max(4, lwp * 3)
        for (ax, ay, bx, by) in [(X0, Y0, X1, Y0), (X1, Y0, X1, Y1), (X1, Y1, X0, Y1), (X0, Y1, X0, Y0)]:
            length = max(abs(bx - ax), abs(by - ay))
            pos = 0
            while pos < length:
                e = min(pos + dash, length)
                if ax == bx:
                    sgn = 1 if by > ay else -1
                    self.d.line([(ax, ay + sgn * pos), (ax, ay + sgn * e)], fill=col, width=lwp)
                else:
                    sgn = 1 if bx > ax else -1
                    self.d.line([(ax + sgn * pos, ay), (ax + sgn * e, ay)], fill=col, width=lwp)
                pos += dash + gap

    def text(self, x, y, w, h, content, size=12, color="#000000", bold=False, italic=False,
             align="l", anchor="t", inset=0.0, ls=1.0, track=0.0, name=None):
        tb = self.s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            tb.name = name
        ins = inset if isinstance(inset, tuple) else (inset, inset, inset, inset)
        paras = as_paras(content)
        base = self._base(size, color, bold, italic, align, ls, track)
        self._fill_frame(tb.text_frame, paras, base, ins, anchor)
        self._draw_text(x, y, w, h, paras, base, ins, anchor)
        return tb

    def title(self, x, y, w, h, content, size=26, color="#000000", bold=True, anchor="t", ls=0.95):
        """Fill the layout's title placeholder so the slide has a real title."""
        ph = self.title_ph
        ph.left, ph.top, ph.width, ph.height = Inches(x), Inches(y), Inches(w), Inches(h)
        paras = as_paras(content)
        base = self._base(size, color, bold, False, "l", ls, 0.0)
        self._fill_frame(ph.text_frame, paras, base, (0, 0, 0, 0), anchor)
        self._draw_text(x, y, w, h, paras, base, (0, 0, 0, 0), anchor, where="TITLE ")
        self._title_used = True
        return ph

    def hidden_title(self, text):
        """Off-slide title for slides whose visible title is custom-built."""
        ph = self.title_ph
        ph.left, ph.top, ph.width, ph.height = Inches(0.5), Inches(-1.2), Inches(10), Inches(0.6)
        ph.text_frame.text = text
        for r in ph.text_frame.paragraphs[0].runs:
            r.font.size = Pt(18)
            r.font.name = FONT
        self._title_used = True

    def line(self, x1, y1, x2, y2, color="#000000", lw=1.0, dash=False, arrow=False):
        c = self.s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
        c.line.color.rgb = _color(color)
        c.line.width = Pt(lw)
        if dash:
            c.line.dash_style = MSO_LINE.DASH
        if arrow:
            ln = c.line._get_or_add_ln()
            tail = etree.SubElement(ln, qn("a:tailEnd"))
            tail.set("type", "triangle")
            tail.set("w", "med")
            tail.set("len", "med")
        if self.deck.preview:
            lwp = max(1, int(round(lw * SCALE / 72.0)))
            P = self._px
            self.d.line([(P(x1), P(y1)), (P(x2), P(y2))], fill=rgb(color), width=lwp)
            if arrow:
                import math
                ang = math.atan2(y2 - y1, x2 - x1)
                L = 0.11
                for da in (2.6, -2.6):
                    pass
                p1 = (x2 + L * math.cos(ang + 2.62), y2 + L * math.sin(ang + 2.62))
                p2 = (x2 + L * math.cos(ang - 2.62), y2 + L * math.sin(ang - 2.62))
                self.d.polygon([(P(x2), P(y2)), (P(p1[0]), P(p1[1])), (P(p2[0]), P(p2[1]))], fill=rgb(color))
        return c

    # ---------------------------------------------------------------- images
    def image(self, path, x, y, w, h, fit="contain", halign="c", valign="c", border=None,
              alt="", bg=None):
        """Place an image inside the (x, y, w, h) frame. Returns the drawn rect."""
        path = str(path)
        cache = self.deck._image_cache
        if path not in cache:
            cache[path] = Image.open(path)
        im = cache[path]
        iw, ih = im.size
        ar, fr = iw / ih, w / h
        crop = None
        if fit == "cover":
            dx, dy, dw, dh = x, y, w, h
            if ar > fr:                       # image too wide: crop left/right
                keep = fr / ar
                off = (1 - keep) * {"l": 0.0, "c": 0.5, "r": 1.0}[halign]
                crop = (off, 0.0, 1 - keep - off, 0.0)
            else:
                keep = ar / fr
                off = (1 - keep) * {"t": 0.0, "c": 0.5, "b": 1.0}[valign]
                crop = (0.0, off, 0.0, 1 - keep - off)
        else:
            if ar > fr:
                dw, dh = w, w / ar
            else:
                dw, dh = h * ar, h
            dx = x + (w - dw) * {"l": 0.0, "c": 0.5, "r": 1.0}[halign]
            dy = y + (h - dh) * {"t": 0.0, "c": 0.5, "b": 1.0}[valign]
        if bg:
            self.box(x, y, w, h, fill=bg)
        pic = self.s.shapes.add_picture(path, Inches(dx), Inches(dy), Inches(dw), Inches(dh))
        if crop:
            pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = crop
        if alt:
            pic._element.nvPicPr.cNvPr.set("descr", alt)
        if border:
            pic.line.color.rgb = _color(border)
            pic.line.width = Pt(0.75)
        if self.deck.preview:
            src = im
            if crop:
                src = im.crop((int(crop[0] * iw), int(crop[1] * ih),
                               int(iw - crop[2] * iw), int(ih - crop[3] * ih)))
            tgt = (max(1, self._px(dw)), max(1, self._px(dh)))
            small = src.resize(tgt, Image.LANCZOS)
            if small.mode in ("RGBA", "LA", "P"):
                small = small.convert("RGBA")
                self.img.paste(small, (self._px(dx), self._px(dy)), small)
            else:
                self.img.paste(small.convert("RGB"), (self._px(dx), self._px(dy)))
            if border:
                self.d.rectangle([self._px(dx), self._px(dy), self._px(dx + dw), self._px(dy + dh)],
                                 outline=rgb(border), width=1)
        return dx, dy, dw, dh

    # ---------------------------------------------------------------- tables
    def table(self, x, y, col_w, rows, size=10.5, color="#000000", header_fill="#000000",
              header_color="#FFFFFF", header_size=None, rule="#D0D5DA", pad=(0.09, 0.055),
              zebra=None, min_row_h=0.0, ls=1.0, first_col_bold=False, name=None):
        """Native PowerPoint table. rows[0] is the header. Cells use the mini markup.

        Row heights are measured from the wrapped text so the preview and
        PowerPoint agree. Returns the y coordinate of the table's bottom edge.
        """
        nrows, ncols = len(rows), len(col_w)
        header_size = header_size or size
        padx, pady = pad
        heights = []
        for ri, row in enumerate(rows):
            need = 0.0
            for ci, cell in enumerate(row):
                sz = header_size if ri == 0 else size
                lay = TextLayout(as_paras(cell), col_w[ci] - 2 * padx,
                                 self._base(sz, color, ri == 0, False, "l", ls, 0.0), self.deck.styles)
                need = max(need, lay.height)
            heights.append(max(need + 2 * pady, min_row_h if ri else 0.0))
        total_h = sum(heights)
        gf = self.s.shapes.add_table(nrows, ncols, Inches(x), Inches(y), Inches(sum(col_w)), Inches(total_h))
        if name:
            gf.name = name
        tbl = gf.table
        tblPr = tbl._tbl.tblPr
        for attr in ("firstRow", "bandRow", "firstCol", "lastRow", "lastCol", "bandCol"):
            if attr in tblPr.attrib:
                del tblPr.attrib[attr]
        style_id = tblPr.find(qn("a:tableStyleId"))
        if style_id is None:
            style_id = etree.SubElement(tblPr, qn("a:tableStyleId"))
        style_id.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"      # No Style, No Grid
        for ci, cw in enumerate(col_w):
            tbl.columns[ci].width = Inches(cw)
        cy = y
        for ri, row in enumerate(rows):
            tbl.rows[ri].height = Inches(heights[ri])
            cx = x
            fill = header_fill if ri == 0 else (zebra if (zebra and ri % 2 == 0) else None)
            for ci, cell_content in enumerate(row):
                cell = tbl.cell(ri, ci)
                sz = header_size if ri == 0 else size
                base = self._base(sz, header_color if ri == 0 else color,
                                  ri == 0 or (first_col_bold and ci == 0), False, "l", ls, 0.0)
                paras = as_paras(cell_content)
                cell.margin_left = cell.margin_right = Inches(padx)
                cell.margin_top = cell.margin_bottom = Inches(pady)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE if ri == 0 else MSO_ANCHOR.TOP
                tf = cell.text_frame
                tf.word_wrap = True
                for i, p in enumerate(paras):
                    par = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    par.line_spacing = p.ls or ls
                    par.space_before = Pt(p.sb if i > 0 else 0)
                    par.space_after = Pt(p.sa)
                    for txt, st in parse_runs(p.text, self.deck.styles):
                        run = par.add_run()
                        run.text = txt
                        f = run.font
                        f.name = FONT
                        f.size = Pt(p.size or sz)
                        f.bold = st.get("bold", p.bold if p.bold is not None else base["bold"])
                        f.italic = st.get("italic", bool(p.italic))
                        f.color.rgb = _color(st.get("color", p.color or base["color"]))
                tcPr = cell._tc.get_or_add_tcPr()
                for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                    ln = etree.Element(qn(tag))
                    if tag == "a:lnB" and ri > 0:
                        ln.set("w", str(int(Pt(0.75))))
                        sf = etree.SubElement(ln, qn("a:solidFill"))
                        etree.SubElement(sf, qn("a:srgbClr")).set("val", rule.lstrip("#"))
                    else:
                        ln.set("w", "0")
                        etree.SubElement(ln, qn("a:noFill"))
                    tcPr.append(ln)
                if fill:
                    sf = etree.SubElement(tcPr, qn("a:solidFill"))
                    etree.SubElement(sf, qn("a:srgbClr")).set("val", fill.lstrip("#"))
                else:
                    etree.SubElement(tcPr, qn("a:noFill"))
                # preview
                if self.deck.preview and fill:
                    self.d.rectangle([self._px(cx), self._px(cy), self._px(cx + col_w[ci]),
                                      self._px(cy + heights[ri])], fill=rgb(fill))
                lay = TextLayout(paras, col_w[ci] - 2 * padx, base, self.deck.styles)
                off = (heights[ri] - 2 * pady - lay.height) / 2 if ri == 0 else 0.0
                self._draw_text(cx, cy + off, col_w[ci], heights[ri] - off, paras, base,
                                (padx, pady, padx, pady), "t", where="TABLE ")
                cx += col_w[ci]
            cy += heights[ri]
            if self.deck.preview and ri > 0:
                self.d.line([(self._px(x), self._px(cy)), (self._px(x + sum(col_w)), self._px(cy))],
                            fill=rgb(rule), width=1)
        return cy

    # ----------------------------------------------------------------- misc
    def slide_number(self, x, y, w, h, size=9, color="#000000", align="r"):
        """Live slide-number field (updates if slides are reordered)."""
        tb = self.s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tb.name = "Slide number"
        tf = tb.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        par = tf.paragraphs[0]
        par.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        fld = etree.SubElement(par._p, qn("a:fld"))
        fld.set("id", "{" + str(uuid.uuid4()).upper() + "}")
        fld.set("type", "slidenum")
        rPr = etree.SubElement(fld, qn("a:rPr"))
        rPr.set("lang", "en-GB")
        rPr.set("sz", str(int(size * 100)))
        sf = etree.SubElement(rPr, qn("a:solidFill"))
        etree.SubElement(sf, qn("a:srgbClr")).set("val", color.lstrip("#"))
        etree.SubElement(rPr, qn("a:latin")).set("typeface", FONT)
        etree.SubElement(fld, qn("a:t")).text = str(self.n)
        self._draw_text(x, y, w, h, [Para(str(self.n))],
                        self._base(size, color, False, False, align, 1.0, 0.0), (0, 0, 0, 0), "m")

    def notes(self, text: str):
        tf = self.s.notes_slide.notes_text_frame
        tf.text = text.strip()
