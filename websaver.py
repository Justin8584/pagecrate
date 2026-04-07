#!/usr/bin/env python3
"""
WebSaver — Save any webpage as a clean, self-contained offline HTML + PDF.
============================================================================

Works on any website. Handles lazy-loaded images, SPAs, login-protected content.

Usage:
  # Save a single page
  python3 websaver.py https://example.com/article

  # Save multiple URLs
  python3 websaver.py https://site.com/page1 https://site.com/page2

  # Save from a file containing URLs (one per line)
  python3 websaver.py --file urls.txt

  # Login-protected site (opens browser for manual login first)
  python3 websaver.py --login https://members-only-site.com/article

  # Auto-crawl: grab all links matching a pattern from a start page
  python3 websaver.py --crawl https://docs.example.com/intro --pattern "/docs/"

  # ByteByteGo mode (shortcut)
  python3 websaver.py --bbg system-design-interview

  # Output as PDF too
  python3 websaver.py https://example.com/article --pdf

  # Custom output directory
  python3 websaver.py https://example.com/article -o ./saved/

Requirements:
  pip install playwright beautifulsoup4 lxml
  playwright install chromium
"""

import asyncio
import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Install Playwright first:")
    print("  pip install playwright && playwright install chromium")
    sys.exit(1)


PROFILE_DIR = Path.home() / ".websaver_profile"

# ─────────────────────────────────────────────
# JavaScript Injections
# ─────────────────────────────────────────────

JS_SCROLL_FULL = """
async () => {
    const delay = ms => new Promise(r => setTimeout(r, ms));
    let prev = -1, curr = 0, tries = 0;
    while (prev !== curr && tries++ < 40) {
        prev = curr;
        window.scrollTo(0, document.body.scrollHeight);
        await delay(500);
        curr = document.body.scrollHeight;
    }
    // Slow re-scroll to catch mid-page lazy images
    const step = window.innerHeight;
    for (let y = 0; y < document.body.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await delay(150);
    }
    window.scrollTo(0, 0);
    return { height: document.body.scrollHeight, scrolls: tries };
}
"""

JS_EMBED_IMAGES = """
async () => {
    const results = { total: 0, embedded: 0, failed: 0, skipped: 0 };

    async function fetchAsDataUri(url) {
        try {
            const resp = await fetch(url, { credentials: 'include', mode: 'cors' });
            if (!resp.ok) return null;
            const blob = await resp.blob();
            return new Promise(resolve => {
                const r = new FileReader();
                r.onload = () => resolve(r.result);
                r.onerror = () => resolve(null);
                r.readAsDataURL(blob);
            });
        } catch { return null; }
    }

    function canvasToDataUri(img) {
        try {
            if (!img.naturalWidth || !img.naturalHeight) return null;
            const c = document.createElement('canvas');
            c.width = img.naturalWidth;
            c.height = img.naturalHeight;
            c.getContext('2d').drawImage(img, 0, 0);
            try { return c.toDataURL('image/webp', 0.92); }
            catch { return c.toDataURL('image/png'); }
        } catch { return null; }
    }

    const imgs = document.querySelectorAll('img');
    results.total = imgs.length;

    for (const img of imgs) {
        const src = img.src || '';

        // Already a substantial data URI
        if (src.startsWith('data:') && src.length > 500) {
            results.skipped++;
            continue;
        }

        // Tiny placeholder — skip (don't count as failure)
        if (src.startsWith('data:') && src.length <= 500) {
            results.skipped++;
            continue;
        }

        let dataUri = null;

        // Method 1: canvas (works for loaded same-origin/CORS images)
        if (img.complete && img.naturalWidth > 0) {
            dataUri = canvasToDataUri(img);
        }

        // Method 2: fetch the src
        if (!dataUri && src.startsWith('http')) {
            dataUri = await fetchAsDataUri(src);
        }

        // Method 3: try alternative src attributes
        if (!dataUri) {
            const alts = [
                img.getAttribute('data-src'),
                img.getAttribute('data-lazy-src'),
                img.getAttribute('data-original'),
                img.getAttribute('data-savepage-src'),
                (img.getAttribute('srcset') || '').split(',').pop()?.trim().split(' ')[0]
            ].filter(Boolean);

            for (const alt of alts) {
                const fullUrl = alt.startsWith('http') ? alt : new URL(alt, location.href).href;
                dataUri = await fetchAsDataUri(fullUrl);
                if (dataUri && dataUri.length > 500) break;
                dataUri = null;
            }
        }

        if (dataUri && dataUri.length > 500) {
            img.src = dataUri;
            img.removeAttribute('srcset');
            img.removeAttribute('data-src');
            img.removeAttribute('loading');
            // Fix common broken styles (Next.js, Gatsby, etc.)
            if (img.style.position === 'absolute' ||
                img.style.width === '0px' ||
                img.style.height === '0px') {
                img.style.cssText = 'display:block; max-width:100%; height:auto; margin:0 auto;';
            }
            results.embedded++;
        } else {
            results.failed++;
        }
    }

    // Embed CSS background images on key containers
    const bgEls = document.querySelectorAll('[style*="background-image"]');
    for (const el of bgEls) {
        const match = el.style.backgroundImage.match(/url\\(['"]?(https?:\\/\\/[^'"\\)]+)['"]?\\)/);
        if (match) {
            const dataUri = await fetchAsDataUri(match[1]);
            if (dataUri) {
                el.style.backgroundImage = `url(${dataUri})`;
                results.embedded++;
            }
        }
    }

    // Remove aria-hidden placeholder images (Next.js pattern)
    document.querySelectorAll('img[aria-hidden="true"]').forEach(img => {
        if (img.src.startsWith('data:image/svg') && img.src.length < 500) img.remove();
    });

    return results;
}
"""

JS_STRIP = """
() => {
    // Remove scripts
    document.querySelectorAll('script, noscript').forEach(s => s.remove());
    // Remove preloads
    document.querySelectorAll('link[rel="preload"], link[rel="prefetch"], link[rel="dns-prefetch"], link[rel="preconnect"]').forEach(s => s.remove());
    // Remove iframes (ads, trackers)
    document.querySelectorAll('iframe').forEach(s => s.remove());
    // Remove tracking pixels
    document.querySelectorAll('img[width="1"], img[height="1"]').forEach(s => s.remove());
    return 'done';
}
"""

JS_EXTRACT_PAGE_INFO = """
() => {
    const title = document.title || '';
    const h1 = document.querySelector('h1');
    const h1Text = h1 ? h1.textContent.trim() : '';
    const canonical = document.querySelector('link[rel="canonical"]');
    const ogTitle = document.querySelector('meta[property="og:title"]');
    return {
        title: ogTitle?.content || h1Text || title,
        url: canonical?.href || location.href,
        h1: h1Text,
        domain: location.hostname
    };
}
"""

JS_EXTRACT_LINKS = """
(pattern) => {
    const links = new Map();
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.href;
        if (href && href.includes(pattern) && !links.has(href)) {
            links.set(href, a.textContent.trim().substring(0, 120));
        }
    });
    return [...links.entries()].map(([url, text]) => ({ url, text }));
}
"""

# ─────────────────────────────────────────────
# ByteByteGo-specific helpers
# ─────────────────────────────────────────────

BBG_BASE = "https://bytebytego.com"

JS_BBG_EXPAND_SUBMENUS = """
async () => {
    const delay = ms => new Promise(r => setTimeout(r, ms));
    const closed = document.querySelectorAll('.ant-menu-submenu:not(.ant-menu-submenu-open) .ant-menu-submenu-title');
    for (const title of closed) {
        title.click();
        await delay(400);
    }
    return closed.length;
}
"""

JS_BBG_CHAPTERS = """
() => {
    const seen = new Set();
    const chapters = [];
    document.querySelectorAll('li[role="menuitem"]').forEach(li => {
        const menuId = li.dataset.menuId || '';
        const pathMatch = menuId.match(/\\/courses\\/[^/]+\\/.+$/);
        if (pathMatch && !seen.has(pathMatch[0])) {
            seen.add(pathMatch[0]);
            const parts = pathMatch[0].split('/').filter(Boolean);
            // Flat: ['courses','slug','chapter']  Nested: ['courses','slug','section','chapter']
            const section = parts.length > 3 ? parts[2] : '';
            const parentSub = li.closest('.ant-menu-submenu');
            const sectionTitle = parentSub
                ? parentSub.querySelector('.ant-menu-submenu-title')?.textContent?.trim() || ''
                : '';
            chapters.push({
                url: location.origin + pathMatch[0],
                path: pathMatch[0],
                title: li.textContent.trim().substring(0, 100),
                section,
                sectionTitle,
            });
        }
    });
    return chapters;
}
"""


# ─────────────────────────────────────────────
# Core Save Engine
# ─────────────────────────────────────────────

def slugify(text, max_len=80):
    """Convert text to a safe filename slug."""
    text = re.sub(r'[^\w\s-]', '', text).strip()
    text = re.sub(r'[-\s]+', '_', text)
    return text[:max_len] or 'untitled'


def _unique_path(output_dir, name):
    """Return a unique path, appending _2, _3, etc. if name already exists."""
    path = output_dir / name
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for n in range(2, 100):
        candidate = output_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
    import hashlib, time
    h = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
    return output_dir / f"{stem}_{h}{suffix}"


async def _async_input(prompt=""):
    """Non-blocking input for use inside async functions."""
    return await asyncio.get_event_loop().run_in_executor(None, input, prompt)


async def save_page(page, url, output_dir, index=None, total=None, wait_sec=2):
    """Navigate to a URL, embed resources, and save as self-contained HTML."""
    prefix = f"  [{index+1}/{total}]" if index is not None else "  "

    try:
        print(f"{prefix} Loading: {url[:80]}...")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        await asyncio.sleep(wait_sec)

        await page.evaluate(JS_SCROLL_FULL)
        await asyncio.sleep(1.5)

        img_results = await page.evaluate(JS_EMBED_IMAGES)
        print(f"{prefix}   Images: {img_results['embedded']} embedded, "
              f"{img_results['failed']} failed, {img_results['skipped']} skipped")

        await page.evaluate(JS_STRIP)

        info = await page.evaluate(JS_EXTRACT_PAGE_INFO)

        if index is not None:
            name = f"{index+1:02d}_{slugify(info['title'])}.html"
        else:
            name = f"{slugify(info['title'])}.html"

        html = await page.content()

        if '<style' not in html[:5000]:
            style = '<style>img{max-width:100%;height:auto}</style>'
            html = html.replace('</head>', f'{style}</head>', 1)

        output_path = _unique_path(output_dir, name)
        output_path.write_text(html, encoding='utf-8')
        size_kb = output_path.stat().st_size / 1024
        print(f"{prefix}   Saved: {name} ({size_kb:.0f} KB)")

        return output_path

    except Exception as e:
        print(f"{prefix}   ERROR: {e}")
        return None


async def convert_to_pdf(html_path, pdf_path, browser=None):
    """Convert local HTML file to PDF. Reuses browser if provided."""
    own_browser = browser is None
    try:
        if own_browser:
            from playwright.async_api import async_playwright
            pw_instance = await async_playwright().start()
            browser = await pw_instance.chromium.launch(headless=True)
        page = await browser.new_page()
        file_url = f"file://{Path(html_path).resolve()}"
        await page.goto(file_url, wait_until="networkidle", timeout=30000)
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "18mm", "bottom": "18mm", "left": "20mm", "right": "20mm"},
            print_background=True,
        )
        await page.close()
        if own_browser:
            await browser.close()
            await pw_instance.stop()
        return True
    except Exception as e:
        print(f"    PDF error: {e}")
        return False


# ─────────────────────────────────────────────
# Main Modes
# ─────────────────────────────────────────────

async def mode_single_urls(args, urls):
    """Save a list of URLs."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        _browser_instance = None

        if args.login:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
        else:
            _browser_instance = await pw.chromium.launch(headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            browser = await _browser_instance.new_context(viewport={"width": 1280, "height": 900})

        page = await browser.new_page() if not args.login else (browser.pages[0] if browser.pages else await browser.new_page())

        if args.login:
            await page.goto(urls[0], wait_until="networkidle", timeout=30000)
            print("\n" + "=" * 50)
            print("Browser is open — log in if needed.")
            print("=" * 50)
            await _async_input("Press ENTER when ready... ")
            await asyncio.sleep(2)

        pdf_browser = None
        if args.pdf:
            pdf_browser = await pw.chromium.launch(headless=True)

        saved = []
        use_index = len(urls) > 1
        for i, url in enumerate(urls):
            path = await save_page(page, url, output_dir,
                                   index=i if use_index else None,
                                   total=len(urls) if use_index else None)
            if path:
                saved.append(path)
                if args.pdf:
                    pdf_path = path.with_suffix('.pdf')
                    ok = await convert_to_pdf(path, pdf_path, browser=pdf_browser)
                    if ok:
                        print(f"    PDF: {pdf_path.name}")

            if i < len(urls) - 1:
                await asyncio.sleep(1.5)

        if pdf_browser:
            await pdf_browser.close()
        await browser.close()
        if _browser_instance:
            await _browser_instance.close()

    print(f"\nDone! {len(saved)}/{len(urls)} pages saved to: {output_dir.resolve()}")
    return saved


async def mode_crawl(args):
    """Crawl: start from a page, find all links matching pattern, save them all."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        _browser_instance = None

        if args.login:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.pages[0] if browser.pages else await browser.new_page()
        else:
            _browser_instance = await pw.chromium.launch(headless=True,
                args=["--disable-blink-features=AutomationControlled"])
            ctx = await _browser_instance.new_context(viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            browser = ctx

        start_url = args.crawl
        print(f"\nCrawling from: {start_url}")
        print(f"Link pattern:  {args.pattern}")

        await page.goto(start_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        if args.login:
            print("\n" + "=" * 50)
            print("Browser is open — log in if needed.")
            print("=" * 50)
            await _async_input("Press ENTER when ready... ")
            await page.goto(start_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

        links = await page.evaluate(JS_EXTRACT_LINKS, args.pattern)

        seen = set()
        unique = []
        for link in links:
            if link['url'] not in seen:
                seen.add(link['url'])
                unique.append(link)

        print(f"\nFound {len(unique)} pages matching '{args.pattern}':")
        for i, link in enumerate(unique):
            print(f"  {i+1:3d}. {link['text'][:60]:60s}  {link['url']}")

        if not unique:
            print("No matching links found.")
            await browser.close()
            if _browser_instance:
                await _browser_instance.close()
            return

        if not args.yes:
            resp = (await _async_input(f"\nSave all {len(unique)} pages? [Y/n] ")).strip().lower()
            if resp == 'n':
                await browser.close()
                if _browser_instance:
                    await _browser_instance.close()
                return

        pdf_browser = None
        if args.pdf:
            pdf_browser = await pw.chromium.launch(headless=True)

        saved = []
        for i, link in enumerate(unique):
            path = await save_page(page, link['url'], output_dir, i, len(unique))
            if path:
                saved.append(path)
                if args.pdf:
                    pdf_path = path.with_suffix('.pdf')
                    ok = await convert_to_pdf(path, pdf_path, browser=pdf_browser)
                    if ok:
                        print(f"    PDF: {pdf_path.name}")
            if i < len(unique) - 1:
                await asyncio.sleep(1.5)

        if pdf_browser:
            await pdf_browser.close()
        await browser.close()
        if _browser_instance:
            await _browser_instance.close()

    print(f"\nDone! {len(saved)}/{len(unique)} pages saved to: {output_dir.resolve()}")
    return saved


async def mode_bbg(args):
    """ByteByteGo course mode."""
    output_dir = Path(args.output)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        await page.goto(BBG_BASE, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector('a[href*="/my-courses"]', timeout=15000)
            logged_in = True
        except Exception:
            logged_in = False
        if not logged_in:
            print("\n" + "=" * 50)
            print("Log in to your ByteByteGo account in the browser.")
            print("=" * 50)
            await _async_input("Press ENTER after logging in... ")
            await asyncio.sleep(2)
        else:
            print("  Already logged in (reusing session)")

        # Navigate to course — BBG auto-redirects to the first chapter
        course_url = f"{BBG_BASE}/courses/{args.bbg}"
        sidebar_sel = 'li[role="menuitem"]'
        await page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector(sidebar_sel, timeout=30000)
        except Exception:
            print("  Warning: sidebar menu not detected, continuing anyway...")
        await asyncio.sleep(2)

        expanded = await page.evaluate(JS_BBG_EXPAND_SUBMENUS)
        if expanded:
            print(f"  Expanded {expanded} sidebar sections")
            await asyncio.sleep(2)

        chapters = await page.evaluate(JS_BBG_CHAPTERS)
        print(f"\nFound {len(chapters)} chapters in '{args.bbg}':")
        for i, ch in enumerate(chapters):
            print(f"  {i+1:3d}. {ch['title'][:70]}")

        if args.chapters:
            indices = parse_range(args.chapters, len(chapters))
            chapters = [chapters[i] for i in indices]
            print(f"\nFiltered to {len(chapters)} chapters")

        if not args.yes:
            resp = (await _async_input(f"\nSave {len(chapters)} chapters? [Y/n] ")).strip().lower()
            if resp == 'n':
                await browser.close()
                return

        saved = []
        has_sections = any(ch.get('section') for ch in chapters)
        for i, ch in enumerate(chapters):
            if has_sections and ch.get('section'):
                num = re.match(r'^(\d+)', ch.get('sectionTitle', ''))
                prefix = f"{num.group(1)}_" if num else ''
                section_dir = raw_dir / f"{prefix}{ch['section']}"
                section_dir.mkdir(parents=True, exist_ok=True)
                save_dir = section_dir
            else:
                save_dir = raw_dir
            path = await save_page(page, ch['url'], save_dir, i, len(chapters))
            if path:
                saved.append(path)
            if i < len(chapters) - 1:
                await asyncio.sleep(1.5)

        await browser.close()

    print(f"\n{'='*60}")
    print(f"Saved {len(saved)}/{len(chapters)} chapters to: {raw_dir.resolve()}")

    if args.cleanup:
        clean_dir = output_dir / "clean"
        clean_dir.mkdir(exist_ok=True)
        cleanup_script = Path(__file__).parent / "bbg_cleanup.py"
        if cleanup_script.exists():
            print(f"Running cleanup → {clean_dir.resolve()}")
            cmd = [sys.executable, str(cleanup_script), str(raw_dir),
                   "--output-dir", str(clean_dir)]
            if args.no_pdf:
                cmd.append("--no-pdf")
            subprocess.run(cmd)
        else:
            print(f"WARNING: bbg_cleanup.py not found next to websaver.py")

    print(f"{'='*60}")


def parse_range(spec, total):
    """Parse '1-5,10,12' into sorted list of 0-based indices."""
    indices = set()
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            if '-' in part:
                a, b = part.split('-', 1)
                a, b = int(a), int(b)
                for i in range(a, b + 1):
                    if 1 <= i <= total:
                        indices.add(i - 1)
            else:
                i = int(part)
                if 1 <= i <= total:
                    indices.add(i - 1)
        except ValueError:
            print(f"  Warning: ignoring invalid chapter spec '{part}'")
    return sorted(indices)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="WebSaver — Save any webpage as self-contained offline HTML + PDF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single page
  %(prog)s https://blog.example.com/great-article

  # Multiple pages
  %(prog)s https://site.com/p1 https://site.com/p2 --pdf

  # Login-protected page
  %(prog)s --login https://members.site.com/content

  # Crawl all matching links from a start page
  %(prog)s --crawl https://docs.site.com/intro --pattern "/docs/"

  # Crawl + login (e.g., paid content)
  %(prog)s --crawl https://paid.site.com/course/ch1 --pattern "/course/" --login

  # URLs from a file
  %(prog)s --file urls.txt --pdf

  # ByteByteGo course (shortcut)
  %(prog)s --bbg system-design-interview --cleanup
        """
    )
    p.add_argument("urls", nargs="*", help="URLs to save")
    p.add_argument("--file", "-f", help="File with URLs (one per line)")
    p.add_argument("--crawl", help="Start URL for crawl mode")
    p.add_argument("--pattern", default="/", help="URL pattern to match when crawling (default: /)")
    p.add_argument("--bbg", help="ByteByteGo course slug (e.g., system-design-interview)")
    p.add_argument("--chapters", help="Chapter range for --bbg mode (e.g., 1-5,10)")
    p.add_argument("--cleanup", action="store_true", help="Run bbg_cleanup.py after saving (BBG mode)")
    p.add_argument("--login", action="store_true", help="Open visible browser for manual login")
    p.add_argument("--pdf", action="store_true", help="Also generate PDF for each page")
    p.add_argument("--no-pdf", dest="no_pdf", action="store_true", help="Skip PDF during cleanup (BBG mode)")
    p.add_argument("--output", "-o", default="./websaver_output", help="Output directory")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmations")

    args = p.parse_args()

    if args.bbg:
        args.login = True  # BBG always needs login
        asyncio.run(mode_bbg(args))

    elif args.crawl:
        asyncio.run(mode_crawl(args))

    elif args.urls or args.file:
        urls = list(args.urls) if args.urls else []
        if args.file:
            with open(args.file) as f:
                urls.extend(line.strip() for line in f if line.strip() and not line.startswith('#'))
        if not urls:
            print("No URLs provided.")
            sys.exit(1)
        asyncio.run(mode_single_urls(args, urls))

    else:
        p.print_help()


if __name__ == "__main__":
    main()