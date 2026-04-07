# PageCrate

Save any webpage — or an entire ByteByteGo course — as self-contained offline HTML + PDF.

## Setup

```bash
pip install playwright beautifulsoup4 lxml
playwright install chromium
```

No extra tools needed. PDF uses Playwright's built-in Chromium print.

## Usage

### Save any webpage

```bash
# Single page
python3 websaver.py https://blog.example.com/great-article --pdf

# Multiple pages
python3 websaver.py https://site.com/page1 https://site.com/page2 --pdf

# Login-protected (opens browser for manual login)
python3 websaver.py --login https://members.site.com/article --pdf

# URLs from a text file (one per line)
python3 websaver.py --file urls.txt --pdf

# Custom output directory
python3 websaver.py https://example.com/article --pdf -o ./saved/
```

### Crawl mode — auto-discover and save all pages

```bash
# Start from a page, find all links matching a pattern, save them all
python3 websaver.py --crawl https://react.dev/learn --pattern "/learn" --pdf

# Crawl Python docs
python3 websaver.py --crawl https://docs.python.org/3/tutorial/ --pattern "/tutorial" --pdf

# Crawl with login (paid content)
python3 websaver.py --crawl https://paid-course.com/ch1 --pattern "/chapter" --login --pdf

# GitHub wiki
python3 websaver.py --crawl https://github.com/org/repo/wiki --pattern "/wiki" --pdf
```

### ByteByteGo mode

```bash
# Save entire course + clean up + PDF
python3 websaver.py --bbg system-design-interview --cleanup --pdf

# ML System Design
python3 websaver.py --bbg machine-learning-system-design-interview --cleanup --pdf

# GenAI course
python3 websaver.py --bbg genai-system-design-interview --cleanup --pdf

# Only specific chapters
python3 websaver.py --bbg system-design-interview --chapters 1-5,10 --cleanup --pdf

# Skip PDF (faster)
python3 websaver.py --bbg system-design-interview --cleanup --no-pdf

# Custom output
python3 websaver.py --bbg system-design-interview --cleanup --pdf -o ~/Desktop/reading/
```

Available course slugs:

| Slug | Course |
|------|--------|
| `system-design-interview` | System Design Interview (Vol 1 & 2) |
| `machine-learning-system-design-interview` | ML System Design |
| `genai-system-design-interview` | Generative AI System Design |
| `mobile-system-design-interview` | Mobile System Design |
| `coding-interview-patterns` | Coding Interview Patterns |
| `ood-design-interview` | Object-Oriented Design |
| `behavioral-interview` | Behavioral Interview |

### Cleanup only (already-saved files)

```bash
# Clean saved HTML files (e.g. from Save Page WE extension)
python3 bbg_cleanup.py ./saved_pages/ --output-dir ./clean/

# Single file
python3 bbg_cleanup.py my_page.html

# HTML only, no PDF
python3 bbg_cleanup.py ./saved_pages/ --no-pdf
```

## How it works

1. Opens page in Chromium via Playwright
2. Auto-scrolls to trigger lazy-loaded images
3. Embeds every image as base64 (fully self-contained)
4. Strips scripts, trackers, iframes
5. Saves single `.html` file that works offline
6. Generates PDF via Chromium print (with `--pdf`)
7. BBG mode: `bbg_cleanup.py` strips sidebar, fixes Next.js images, auto-renames by chapter title

## Output structure

```
output/
├── raw/                              # Full saved pages
│   ├── 01_foreword.html
│   ├── 02_scale-from-zero-to-millions.html
│   └── ...
└── clean/                            # Reader-friendly (BBG --cleanup only)
    ├── Ch01_Foreword.html
    ├── Ch01_Foreword.pdf
    ├── Ch02_Scale_From_Zero_To_Millions.html
    ├── Ch02_Scale_From_Zero_To_Millions.pdf
    └── ...
```

## Login sessions

- `--login` stores browser session in `~/.websaver_profile/`
- First run: browser opens → you log in → press Enter in terminal
- Next runs: session reused automatically
- Reset: `rm -rf ~/.websaver_profile/`
- BBG mode enables `--login` automatically

## All flags

| Flag | Description |
|------|-------------|
| `--pdf` | Generate PDF for each page |
| `--login` | Open visible browser for manual login |
| `--file urls.txt` | Read URLs from a text file |
| `--crawl URL` | Start URL for crawl mode |
| `--pattern STR` | URL pattern filter for crawl mode |
| `--bbg SLUG` | ByteByteGo course slug |
| `--chapters 1-5,10` | Chapter range (BBG mode) |
| `--cleanup` | Run bbg_cleanup.py after saving (BBG mode) |
| `--no-pdf` | Skip PDF during cleanup |
| `-o DIR` | Output directory (default: `./websaver_output`) |
| `-y` | Skip confirmation prompts |

## Files

| File | Purpose |
|------|---------|
| `websaver.py` | Main tool — save any site, crawl, or BBG mode |
| `bbg_cleanup.py` | BBG-specific: strip sidebar, fix images, rename, clean PDF |
| `requirements.txt` | Dependencies |
| `setup.sh` | Quick setup script |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Images blank | Some CDNs block cross-origin. Open raw HTML in Chrome to verify. |
| Login expired | `rm -rf ~/.websaver_profile/` and run again |
| Chromium not found | `playwright install chromium` |
| Crawl finds 0 links | Try `--login` (non-headless) for JS-rendered pages |
| PDF looks wrong | Use `--no-pdf`, open HTML in Chrome → Ctrl+P → Save as PDF |
