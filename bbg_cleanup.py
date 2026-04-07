#!/usr/bin/env python3
"""
ByteByteGo Offline Reader - Cleanup Tool v3
=============================================
Converts "Save Page WE" saved HTML files from ByteByteGo
into clean, reader-friendly HTML and PDF files.

Fixes Next.js image rendering (placeholder + absolute-positioned pairs).
"""

import sys, re, asyncio, html as html_mod
from string import Template
from bs4 import BeautifulSoup, NavigableString
from pathlib import Path

HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>$title</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { font-size: 17px; -webkit-font-smoothing: antialiased; }
  body {
    font-family: 'Georgia', 'Times New Roman', serif;
    line-height: 1.75; color: #1a1a2e; background: #fff;
    max-width: 780px; margin: 0 auto; padding: 40px 32px 80px;
  }
  .chapter-header { margin-bottom: 40px; padding-bottom: 24px; border-bottom: 3px solid #16a34a; }
  .chapter-number {
    display: block; font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 3.2rem; font-weight: 800; color: #16a34a; line-height: 1; margin-bottom: 8px;
  }
  h1 { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 2rem; font-weight: 700; color: #1a1a2e; line-height: 1.25; }
  h2 { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 1.55rem; font-weight: 700; color: #1a1a2e; margin: 48px 0 16px; padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }
  h3 { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 1.3rem; font-weight: 600; color: #374151; margin: 36px 0 12px; }
  h4 { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 1.1rem; font-weight: 600; color: #4b5563; margin: 28px 0 10px; }
  p { margin: 0 0 16px; text-align: justify; hyphens: auto; }
  strong, b { font-weight: 700; color: #111827; }
  ul, ol { margin: 0 0 16px; padding-left: 28px; }
  li { margin-bottom: 8px; }
  .figure-wrap { margin: 28px 0; text-align: center; }
  .figure-wrap img { display: block; max-width: 100%; height: auto; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  .figure-caption { display: block; margin-top: 10px; font-size: 0.88rem; color: #6b7280; font-style: italic; text-align: center; }
  img { max-width: 100%; height: auto; }
  .table-wrap { margin: 24px 0; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.92rem; font-family: 'Helvetica Neue', Arial, sans-serif; }
  thead { background: #f8fafc; }
  th { font-weight: 700; text-align: left; padding: 12px 14px; border-bottom: 2px solid #d1d5db; color: #1f2937; }
  td { padding: 10px 14px; border-bottom: 1px solid #e5e7eb; vertical-align: top; color: #374151; }
  tr:last-child td { border-bottom: 2px solid #d1d5db; }
  .katex-display { margin: 20px 0; overflow-x: auto; padding: 8px 0; }
  .math { font-size: 1.05em; }
  blockquote { margin: 20px 0; padding: 14px 20px; border-left: 4px solid #16a34a; background: #f0fdf4; color: #374151; border-radius: 0 6px 6px 0; }
  code { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 0.88em; background: #f1f5f9; padding: 2px 6px; border-radius: 4px; }
  pre { margin: 16px 0; padding: 16px; background: #1e293b; color: #e2e8f0; border-radius: 8px; overflow-x: auto; font-size: 0.85rem; line-height: 1.6; }
  pre code { background: transparent; padding: 0; color: inherit; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 40px 0; }
  @media print {
    body { padding: 0; font-size: 11pt; max-width: none; }
    .chapter-header { page-break-after: avoid; }
    h2, h3, h4 { page-break-after: avoid; }
    .figure-wrap, table { page-break-inside: avoid; }
    img { max-height: 400px; object-fit: contain; }
  }
  @media (max-width: 600px) {
    body { padding: 20px 16px 60px; font-size: 16px; }
    .chapter-number { font-size: 2.4rem; }
    h1 { font-size: 1.6rem; }
  }
</style>
</head>
<body>
$content
</body>
</html>
""")


def _esc(text):
    return html_mod.escape(str(text), quote=True)


def is_placeholder_img(img):
    if img.get('aria-hidden') == 'true':
        return True
    src = img.get('src', '')
    if src.startswith('data:image/svg+xml') and len(src) < 500:
        return True
    if src.startswith('data:image/gif') and len(src) < 200:
        return True
    return False


def is_real_img(img):
    src = img.get('src', '')
    if not src:
        return False
    for prefix in ['data:image/webp', 'data:image/png', 'data:image/jpeg', 'data:image/jpg']:
        if src.startswith(prefix) and len(src) > 500:
            return True
    if src.startswith('http'):
        return True
    return False


def get_placeholder_dimensions(img):
    src = img.get('src', '')
    w = re.search(r"width[=:]['\"]?(\d+)", src)
    h = re.search(r"height[=:]['\"]?(\d+)", src)
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None)


def fix_images_in_article(article):
    all_imgs = article.find_all('img')
    i = 0
    fixed = 0
    removed = 0

    while i < len(all_imgs):
        img = all_imgs[i]

        if is_placeholder_img(img):
            width, height = get_placeholder_dimensions(img)
            real_img = None
            if i + 1 < len(all_imgs):
                candidate = all_imgs[i + 1]
                if is_real_img(candidate):
                    real_img = candidate

            if real_img:
                style_parts = ['display: block', 'max-width: 100%', 'height: auto', 'margin: 0 auto']
                if width:
                    style_parts.append(f'width: {width}px')
                real_img['style'] = '; '.join(style_parts)
                for attr in ['data-nimg', 'data-savepage-src', 'data-savepage-currentsrc',
                             'data-savepage-srcset', 'srcset', 'decoding', 'aria-hidden']:
                    real_img.attrs.pop(attr, None)
                fixed += 1

            img.decompose()
            removed += 1
            i += 2 if real_img else 1

        elif is_real_img(img):
            style_parts = ['display: block', 'max-width: 100%', 'height: auto', 'margin: 0 auto']
            img['style'] = '; '.join(style_parts)
            for attr in ['data-nimg', 'data-savepage-src', 'data-savepage-currentsrc',
                         'data-savepage-srcset', 'srcset', 'decoding']:
                img.attrs.pop(attr, None)
            fixed += 1
            i += 1
        else:
            i += 1

    print(f"  Images: {fixed} fixed, {removed} placeholders removed")


def extract_and_clean(input_path):
    with open(input_path, 'r', encoding='utf-8-sig') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')

    article = soup.find(class_=lambda c: c and 'learnContent' in c)
    if not article:
        wrap = soup.find(class_=lambda c: c and 'articleWrap' in c)
        article = (wrap.find('article') or wrap) if wrap else None
        if not article:
            raise ValueError(f"Could not find article content in {input_path}")

    fix_images_in_article(article)

    chapter_num = ""
    strong = article.find('strong')
    if strong:
        text = strong.get_text().strip()
        if re.match(r'^\d+$', text):
            chapter_num = text
            strong.decompose()

    h1 = article.find('h1')
    title = h1.get_text().strip() if h1 else "Untitled"
    if h1:
        h1.decompose()

    header = '<div class="chapter-header">\n'
    if chapter_num:
        header += f'  <span class="chapter-number">{_esc(chapter_num)}</span>\n'
    header += f'  <h1>{_esc(title)}</h1>\n</div>\n\n'

    body = process_content(article)
    full_title = f"Ch.{chapter_num} - {title}" if chapter_num else title
    return full_title, header + body


def process_content(article):
    parts = []
    for el in article.children:
        if isinstance(el, NavigableString):
            t = str(el).strip()
            if t:
                parts.append(f"<p>{t}</p>")
            continue
        if not hasattr(el, 'name') or not el.name:
            continue

        tag = el.name
        cls = ' '.join(el.get('class', []))

        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            continue
        if tag == 'h3':
            parts.append(f'\n<h2>{el.get_text().strip()}</h2>')
            continue
        if tag == 'h4':
            parts.append(f'\n<h3>{el.get_text().strip()}</h3>')
            continue
        if tag in ('h1', 'h2', 'h5', 'h6'):
            parts.append(f'\n<{tag}>{el.get_text().strip()}</{tag}>')
            continue

        if tag == 'center' or (tag == 'div' and 'flex' in el.get('style', '') and el.find('img')):
            r = build_figure(el)
            if r:
                parts.append(r)
            continue

        if tag == 'div' and el.find('img', recursive=True):
            imgs = [i for i in el.find_all('img') if is_real_img(i)]
            text = el.get_text(strip=True)
            if imgs and len(text) < 200:
                r = build_figure(el)
                if r:
                    parts.append(r)
                continue

        if tag == 'table' or (tag == 'div' and el.find('table')) or 'table-wrap' in cls:
            tbl = el if tag == 'table' else el.find('table')
            if tbl:
                parts.append(f'<div class="table-wrap">{clean_table(tbl)}</div>')
            continue

        if tag in ('ul', 'ol'):
            parts.append(clean_list(el))
            continue
        if tag == 'p':
            inner = clean_inner(el)
            if inner.strip():
                parts.append(f'<p>{inner}</p>')
            continue
        if tag == 'div' and ('math' in cls or 'katex' in cls):
            parts.append(str(el))
            continue
        if tag == 'div':
            inner = clean_inner(el)
            if inner.strip():
                parts.append(f'<div>{inner}</div>')
            continue
        if tag == 'blockquote':
            parts.append(f'<blockquote>{clean_inner(el)}</blockquote>')
            continue
        if tag == 'pre':
            parts.append(str(el))
            continue
        if el.get_text().strip():
            parts.append(str(el))

    return '\n'.join(parts)


def _extract_caption_text(el):
    """Extract caption text from container, excluding img alt text."""
    parts = []
    for child in el.children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                parts.append(t)
        elif child.name == 'img':
            continue
        elif child.name in ('em', 'figcaption', 'span', 'p'):
            t = child.get_text(strip=True)
            if t:
                parts.append(t)
        # Don't recurse into deep children to avoid picking up nested alt text
    return ' '.join(parts).strip()


def build_figure(el):
    imgs = el.find_all('img')
    real = [i for i in imgs if is_real_img(i)]
    if not real:
        real = [i for i in imgs if not is_placeholder_img(i)]
    if not real:
        return None

    caption = _extract_caption_text(el)

    html_parts = ['<div class="figure-wrap">']
    for img in real:
        src = img.get('src', '')
        alt = _esc(img.get('alt', ''))
        style = _esc(img.get('style', ''))
        html_parts.append(f'  <img src="{src}" alt="{alt}" style="{style}" loading="lazy">')
    if caption:
        html_parts.append(f'  <span class="figure-caption">{_esc(caption)}</span>')
    html_parts.append('</div>')
    return '\n'.join(html_parts)


def clean_inner(el):
    parts = []
    for child in el.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name in ('strong', 'b', 'em', 'i', 'code', 'a', 'span', 'sub', 'sup', 'br'):
            parts.append(str(child))
        elif child.name == 'img' and is_real_img(child):
            src = child.get('src', '')
            alt = _esc(child.get('alt', ''))
            style = _esc(child.get('style', ''))
            parts.append(f'<img src="{src}" alt="{alt}" style="{style}">')
        elif child.name and child.find(class_=lambda c: c and ('math' in c or 'katex' in c)):
            parts.append(str(child))
        else:
            inner = clean_inner(child)
            if inner.strip():
                parts.append(inner)
    return ''.join(parts)


def clean_table(table):
    out = '<table>'
    thead = table.find('thead')
    if thead:
        out += '<thead>'
        for tr in thead.find_all('tr'):
            out += '<tr>'
            for cell in tr.find_all(['th', 'td']):
                out += f'<th>{_esc(cell.get_text().strip())}</th>'
            out += '</tr>'
        out += '</thead>'

    tbody = table.find('tbody')
    rows = tbody.find_all('tr') if tbody else table.find_all('tr')
    if thead:
        hrows = set(id(r) for r in thead.find_all('tr'))
        rows = [r for r in rows if id(r) not in hrows]

    out += '<tbody>'
    for tr in rows:
        out += '<tr>'
        for cell in tr.find_all(['th', 'td']):
            t = cell.name
            out += f'<{t}>{cell.get_text(separator=" ").strip()}</{t}>'
        out += '</tr>'
    out += '</tbody></table>'
    return out


def clean_list(el):
    tag = el.name
    items = [f'  <li>{clean_inner(li).strip()}</li>' for li in el.find_all('li', recursive=False)]
    return f'<{tag}>\n' + '\n'.join(items) + f'\n</{tag}>'


def _slugify(text):
    """Convert text to safe filename, with fallback."""
    safe = re.sub(r'[^\w\s-]', '', text).strip()
    safe = re.sub(r'[-\s]+', '_', safe)
    return safe or 'untitled'


def generate_pdf(html_path, pdf_path):
    """Convert local HTML to PDF using Playwright's Chromium."""
    async def _generate():
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page()
                file_url = f"file://{Path(html_path).resolve()}"
                await page.goto(file_url, wait_until="networkidle", timeout=30000)
                await page.pdf(
                    path=str(pdf_path),
                    format="A4",
                    margin={"top": "20mm", "bottom": "20mm", "left": "22mm", "right": "22mm"},
                    print_background=True,
                )
                await browser.close()
            return True
        except ImportError:
            print("  Playwright not installed — skipping PDF")
            return False
        except Exception as e:
            print(f"  PDF error: {e}")
            return False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(_generate())).result()
    else:
        return asyncio.run(_generate())


def process_file(input_path, output_dir=None, make_pdf=True, base_input_dir=None):
    input_path = Path(input_path)
    output_dir = Path(output_dir) if output_dir else input_path.parent / "clean_output"

    if base_input_dir and input_path.parent != Path(base_input_dir):
        rel = input_path.parent.relative_to(base_input_dir)
        output_dir = output_dir / rel

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Processing: {input_path.name}")
    print(f"{'='*60}")

    title, content = extract_and_clean(str(input_path))
    print(f"  Title: {title}")

    safe = _slugify(title)
    html_out = HTML_TEMPLATE.safe_substitute(title=title, content=content)
    html_path = output_dir / f"{safe}.html"
    html_path.write_text(html_out, encoding='utf-8')

    soup = BeautifulSoup(html_out, 'html.parser')
    real_count = sum(1 for img in soup.find_all('img') if len(img.get('src', '')) > 500)
    print(f"  HTML: {html_path.name} ({real_count} images)")

    pdf_path = None
    if make_pdf:
        pdf_path = output_dir / f"{safe}.pdf"
        ok = generate_pdf(html_path, pdf_path)
        h = html_path.stat().st_size / 1024
        p = pdf_path.stat().st_size / 1024 if ok and pdf_path.exists() else 0
        print(f"  PDF:  {pdf_path.name}")
        print(f"  Sizes: HTML={h:.0f}KB  PDF={p:.0f}KB")

    return html_path, pdf_path


def main():
    import glob
    if len(sys.argv) < 2:
        print("Usage: python3 bbg_cleanup.py <file.html or dir> [--no-pdf] [--output-dir path]")
        sys.exit(1)

    make_pdf = '--no-pdf' not in sys.argv
    output_dir = None
    if '--output-dir' in sys.argv:
        idx = sys.argv.index('--output-dir')
        output_dir = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None

    files = []
    base_input_dir = None
    for arg in sys.argv[1:]:
        if arg.startswith('--') or arg == output_dir:
            continue
        p = Path(arg)
        if p.is_dir():
            base_input_dir = p
            files.extend(sorted(p.rglob('*.html')))
        elif p.exists() and p.suffix == '.html':
            files.append(p)
        else:
            files.extend(Path(m) for m in glob.glob(arg) if m.endswith('.html'))

    if not files:
        print("No HTML files found.")
        sys.exit(1)

    print(f"\nByteByteGo Cleanup v3 — {len(files)} file(s)")
    for f in files:
        try:
            process_file(f, output_dir, make_pdf, base_input_dir)
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\n{'='*60}\nDone!\n{'='*60}\n")

if __name__ == '__main__':
    main()