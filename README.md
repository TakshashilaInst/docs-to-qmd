# Takshashila QMD Converter

Internal tool for converting Google Docs research papers and blog posts into Quarto Markdown (`.qmd`) + rendered PDF, ready to upload to the Takshashila publications website.

**Live converter:** https://shakunasanaxe.github.io/docs-to-qmd/

---

## How it works

1. **Write** your paper or blog post in Google Docs using normal heading styles, Google footnotes, and optionally `[aside]` / `[/aside]` tags for sidenotes.
2. **Share** the document as **Anyone with the link can view** (Share → Change → Anyone with the link).
3. **Paste** the URL into the converter, fill in the metadata form, and click **Convert**.
4. **Download** the `.zip` containing:
   - `{filename}.qmd` — ready to upload to the publications repo
   - `assets/{filename}.pdf` — the rendered PDF *(Research Paper mode only)*
   - `images/` — all extracted images

---

## Google Docs conventions

| Feature | How to write it |
|---|---|
| Headings | Use Google Docs heading styles (Heading 1, Heading 2, Heading 3) |
| Bold / Italic | Normal Google Docs bold / italic |
| Links | Normal hyperlinks |
| Footnotes | Insert → Footnote in Google Docs |
| Sidenotes / asides | Wrap text in `[aside]` and `[/aside]` on their own lines |
| Images | Insert normally; add a short caption as the paragraph immediately after |
| Quarto power users | Can write `:::{.aside}`, `[^N]`, etc. directly — these are passed through unchanged |

### Aside / sidenote example

In your Google Doc body, write:

```
[aside]
Bond Credit Ratings such as AAA (highest), AA (second-highest)…
[/aside]
```

This renders as a styled callout box in both the PDF and the website HTML.

---

## Document modes

### Research Paper
- Produces `.qmd` + PDF (rendered with [Typst](https://typst.app))
- Full YAML frontmatter: title, subtitle, authors, date, TL;DR, categories, document type, version
- PDF has Takshashila cover page, running headers, footnotes, and back page
- Sidenotes appear as tinted callout boxes in the PDF

### Blog Post
- Produces `.qmd` only (no PDF)
- Minimal frontmatter: title, authors, date, categories
- Faster conversion (~1–2 min vs 3–5 min for papers)

---

## Architecture

```
docs/          → GitHub Pages frontend (static HTML/CSS/JS)
app/           → Python backend (converter logic)
  cli.py             → CLI entry point used by GitHub Actions
  converter.py       → DOCX → QMD conversion logic
  renderer.py        → Typst PDF rendering + ZIP packaging
  typst_template/    → Takshashila Typst template
worker/        → Cloudflare Worker (proxies GitHub Actions API, holds GH_TOKEN)
.github/workflows/
  convert.yml        → Main conversion workflow (paper + blog)
  deploy-frontend.yml → GitHub Pages deployment
```

**Request flow:**
1. Browser → Cloudflare Worker `/dispatch` → GitHub Actions `convert.yml`
2. Actions runs `app/cli.py`, produces `output.zip`, commits it to `gh-pages`
3. Browser polls Worker `/output-ready` → downloads via Worker `/download`

---

## Setup (first time)

### Prerequisites
- GitHub account with access to this repo
- Cloudflare account (free tier is sufficient)
- Node.js (for Wrangler CLI): `brew install node`

### Steps

```bash
# 1. Clone
git clone https://github.com/shakunasanaxe/docs-to-qmd.git
cd docs-to-qmd

# 2. Deploy the Cloudflare Worker
cd worker
npx wrangler login
npx wrangler deploy
# Copy the URL printed (https://tsh-converter-proxy.YOUR-SUBDOMAIN.workers.dev)

# 3. Set the GitHub token in the worker (classic PAT with 'workflow' scope)
npx wrangler secret put GH_TOKEN

# 4. If the worker URL differs from the one in docs/app.js, update WORKER_URL and push
```

In your GitHub repo:
- **Settings → Pages → Source: GitHub Actions**
- Push any change to `docs/` to trigger the Deploy Frontend workflow

---

## Local development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000
```
