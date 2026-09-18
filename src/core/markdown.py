"""Tiny markdown -> HTML converter for the chat transcript.

Supports: paragraphs, headings (#..###), bold, italic, inline code, fenced
code blocks, unordered/ordered lists, links (http/https only). Everything is
HTML-escaped first, so model output can never inject markup.
"""
from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)\"'<>]+)\)")


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = _INLINE_CODE.sub(lambda m: "<code>%s</code>" % m.group(1), text)
    text = _BOLD.sub(lambda m: "<strong>%s</strong>" % m.group(1), text)
    text = _ITALIC.sub(lambda m: "<em>%s</em>" % m.group(1), text)
    text = _LINK.sub(lambda m: '<a href="%s" target="_blank">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)), text)
    return text


def render(md: str) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    para = []
    list_type = None

    def flush_para():
        if para:
            out.append("<p>%s</p>" % "<br>".join(_inline(l) for l in para))
            para.clear()

    def close_list():
        nonlocal list_type
        if list_type:
            out.append("</%s>" % list_type)
            list_type = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para()
            close_list()
            lang = stripped[3:].strip()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            cls = ' class="lang-%s"' % html.escape(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(code))))
            i += 1
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            flush_para()
            close_list()
            level = len(m.group(1)) + 2  # h3..h5 inside the chat
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2)), level))
            i += 1
            continue
        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
        if m:
            flush_para()
            want = "ol" if m.group(1)[0].isdigit() else "ul"
            if list_type != want:
                close_list()
                out.append("<%s>" % want)
                list_type = want
            out.append("<li>%s</li>" % _inline(m.group(2)))
            i += 1
            continue
        if not stripped:
            flush_para()
            close_list()
            i += 1
            continue
        para.append(stripped)
        i += 1
    flush_para()
    close_list()
    return "\n".join(out)
