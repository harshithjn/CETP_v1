"""
Render a Markdown document to a styled, printable PDF.

WHY NOT PANDOC. It is not installed here and pulling a LaTeX toolchain in for two documents
is disproportionate. Chrome is already present, and renders HTML to PDF faithfully, so the
chain is Markdown -> HTML -> Chrome --print-to-pdf.

WHY A HAND-WRITTEN CONVERTER. This handles only the Markdown this project actually writes:
ATX headings, fenced code, pipe tables, lists, blockquotes, rules, and inline
bold/italic/strikethrough/code/links. That is a deliberate limit. A partial converter that
is honest about its subset beats a dependency that has to be installed; anything
unsupported passes through as visible plain text rather than being silently mangled.

  python3 scripts/docs/build_pdf.py docs/LLD.md
"""

import argparse
import html
import re
import subprocess
import sys
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
* { box-sizing: border-box; }
body { font: 10.5pt/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  color: #15181d; margin: 0; }
h1 { font-size: 21pt; margin: 0 0 4pt; letter-spacing: -.01em; line-height: 1.2; }
h2 { font-size: 14pt; margin: 20pt 0 6pt; padding-bottom: 3pt; border-bottom: 1px solid #d8dce1;
  break-after: avoid; letter-spacing: -.01em; }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; break-after: avoid; }
h4 { font-size: 10.5pt; margin: 11pt 0 3pt; color: #39414c; break-after: avoid; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
ul, ol { margin: 0 0 8pt; padding-left: 17pt; }
li { margin: 2pt 0; }
a { color: #1142a8; text-decoration: none; }
code { font: 9pt ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #f1f3f5; padding: .5pt 3pt; border-radius: 2.5pt; }
pre { font: 8.6pt/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #f7f8fa; border: 1px solid #e3e6ea; border-radius: 4pt; padding: 8pt 10pt;
  overflow-wrap: anywhere; white-space: pre-wrap; margin: 0 0 9pt; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
table { width: 100%; border-collapse: collapse; margin: 0 0 10pt; font-size: 9pt;
  break-inside: avoid; }
th, td { border: 1px solid #dfe3e8; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #f3f5f7; font-weight: 650; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
blockquote { margin: 0 0 9pt; padding: 6pt 10pt; border-left: 2.5pt solid #1142a8;
  background: #f5f7fb; break-inside: avoid; }
blockquote p:last-child { margin-bottom: 0; }
hr { border: 0; border-top: 1px solid #d8dce1; margin: 16pt 0; }
"""

def inline(text):
    out = html.escape(text, quote=False)
    stash = []
    def keep(m):
        stash.append("<code>" + m.group(1) + "</code>")
        return "\x00%d\x00" % (len(stash) - 1)
    out = re.sub(r"`([^`]+)`", keep, out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], out)


def _is_num(cell):
    return bool(re.fullmatch(r"[\s*_~`]*[-+]?[\d.,]+\s*(%|pp|x|ms|GB/s|×)?[\s*_~`]*", cell or ""))


def convert(md):
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue
        if re.match(r"^\s*\|.*\|\s*$", ln) and i + 1 < len(lines) \
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            head = cells(ln); i += 2; body = []
            while i < len(lines) and re.match(r"^\s*\|.*\|\s*$", lines[i]):
                body.append(cells(lines[i])); i += 1
            numeric = [all(_is_num(r[c]) for r in body if c < len(r)) and bool(body)
                       for c in range(len(head))]
            t = ["<table><thead><tr>"]
            for c, h in enumerate(head):
                t.append(('<th class="num">%s</th>' if numeric[c] else "<th>%s</th>") % inline(h))
            t.append("</tr></thead><tbody>")
            for r in body:
                t.append("<tr>")
                for c in range(len(head)):
                    v = r[c] if c < len(r) else ""
                    t.append(('<td class="num">%s</td>' if numeric[c] else "<td>%s</td>") % inline(v))
                t.append("</tr>")
            t.append("</tbody></table>")
            out.append("".join(t)); continue
        m = re.match(r"^(#{1,5})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl)); i += 1; continue
        if re.match(r"^\s*(---+|\*\*\*+)\s*$", ln):
            out.append("<hr>"); i += 1; continue
        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip()); i += 1
            out.append("<blockquote><p>" + inline(" ".join(buf)) + "</p></blockquote>"); continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", ln):
            ordered = bool(re.match(r"^\s*\d+\.", ln))
            items, cur = [], None
            while i < len(lines):
                mm = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$", lines[i])
                if mm:
                    if cur is not None: items.append(cur)
                    cur = mm.group(1)
                elif lines[i].strip() and lines[i].startswith(("  ", "\t")) and cur is not None:
                    cur += " " + lines[i].strip()
                else:
                    break
                i += 1
            if cur is not None: items.append(cur)
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % inline(x) for x in items), tag))
            continue
        if not ln.strip():
            i += 1; continue
        buf = []
        while i < len(lines) and lines[i].strip() \
                and not re.match(r"^(#{1,5}\s|```|>|\s*([-*+]|\d+\.)\s|\s*\|)", lines[i]) \
                and not re.match(r"^\s*(---+|\*\*\*+)\s*$", lines[i]):
            buf.append(lines[i].strip()); i += 1
        if buf:
            out.append("<p>" + inline(" ".join(buf)) + "</p>")
    return "\n".join(out)


def find_chrome():
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("markdown")
    ap.add_argument("-o", "--out")
    ap.add_argument("--keep-html", action="store_true")
    args = ap.parse_args()
    src = Path(args.markdown)
    if not src.exists():
        sys.exit("no such file: %s" % src)
    out_pdf = Path(args.out) if args.out else src.with_suffix(".pdf")
    md = src.read_text()
    title = next((l[2:].strip() for l in md.split("\n") if l.startswith("# ")), src.stem)
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title>"
            "<style>%s</style></head><body>%s</body></html>"
            % (html.escape(title), CSS, convert(md)))
    tmp_html = out_pdf.with_suffix(".build.html")
    tmp_html.write_text(page)
    chrome = find_chrome()
    if not chrome:
        sys.exit("no Chrome/Chromium found; HTML left at %s" % tmp_html)
    r = subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                        "--no-pdf-header-footer", "--print-to-pdf=%s" % out_pdf,
                        tmp_html.resolve().as_uri()],
                       capture_output=True, text=True, timeout=240)
    if not out_pdf.exists():
        sys.exit("Chrome produced no PDF:\n%s" % r.stderr[-800:])
    if not args.keep_html:
        tmp_html.unlink(missing_ok=True)
    print("wrote %s (%.0f KB)" % (out_pdf, out_pdf.stat().st_size / 1024))


if __name__ == "__main__":
    main()
