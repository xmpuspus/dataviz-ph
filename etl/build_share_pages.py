"""Generate per-story social share pages and the journalist embed kit.

A pure static site can't carry per-view Open Graph cards through a URL hash: the
fragment never reaches a crawler and crawlers don't run JS. So for each preset we
write a real static page at /s/<id> whose <meta og:*> tags are filled from the
SAME computed finding the chart ships (never hand-typed), with an instant redirect
to the live chart at /#story=<id>. Sharing /s/<id> gets a rich card; the human
lands on the interactive view.

The embed kit at /embed-kit is a one-stop page for journalists: a live preview and
a copy-paste iframe snippet per preset, plus sizing and attribution notes.

write_share_pages() is called from etl.build at the end of a build so the pages can
never drift from stories.json. It can also be run standalone against the committed
data: `python -m etl.build_share_pages`.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

SITE = "https://dataviz.ph"


def _first_claim(sentence: str) -> str:
    """The finding's headline answer: everything up to the first sentence end.

    The finding sentence is "In {y}, across ..., showing {strength} {dir} link.
    {k} of {n} areas sat above the median on both axes." The first sentence is the
    rho claim — the shareable answer; the quadrant tail is detail. Kept short so OG
    descriptions don't get truncated mid-word by social platforms.
    """
    head = sentence.split(". ", 1)[0].strip()
    if not head.endswith("."):
        head += "."
    return head


def _share_page(story: dict) -> str:
    sid = story["id"]
    headline = story.get("headline", "dataviz.ph")
    finding = story.get("finding") or {}
    sentence = finding.get("sentence") or story.get("tagline", "")
    desc = _first_claim(sentence) if sentence else story.get("tagline", "")
    caveat = finding.get("caveat", "")

    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    chart_url = f"/#story={sid}"
    page_url = f"{SITE}/s/{sid}"
    img_url = f"{SITE}/og/{sid}.png"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(headline)} — dataviz.ph</title>
  <meta name="description" content="{e(desc)}">
  <link rel="canonical" href="{e(page_url)}">
  <meta http-equiv="refresh" content="0; url={e(chart_url)}">

  <meta property="og:type" content="article">
  <meta property="og:url" content="{e(page_url)}">
  <meta property="og:title" content="{e(headline)}">
  <meta property="og:description" content="{e(desc)}">
  <meta property="og:site_name" content="dataviz.ph">
  <meta property="og:image" content="{e(img_url)}">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta property="og:image:alt" content="{e(headline)}">

  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{e(headline)}">
  <meta name="twitter:description" content="{e(desc)}">
  <meta name="twitter:image" content="{e(img_url)}">

  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{min-height:100vh;display:flex;align-items:center;justify-content:center;
      background:#fff;color:#111;padding:32px;
      font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif}}
    .wrap{{max-width:640px}}
    .brand{{font-weight:700;letter-spacing:-0.02em;font-size:18px}}
    .brand span{{color:#0e7c86}}
    h1{{font-family:Georgia,"Iowan Old Style",Palatino,serif;font-weight:600;
      font-size:30px;line-height:1.18;margin:18px 0 14px}}
    .finding{{color:#333;font-size:17px;line-height:1.5}}
    .caveat{{color:#777;font-size:14px;line-height:1.5;margin-top:10px}}
    .go{{margin-top:22px;font-size:17px}}
    .go a{{color:#0e7c86;font-weight:600;text-decoration:none}}
    .go a:hover{{text-decoration:underline}}
    .note{{color:#999;font-size:13px;margin-top:8px}}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="brand">dataviz.<span>ph</span></div>
    <h1>{e(headline)}</h1>
    <p class="finding">{e(sentence)}</p>
    <p class="caveat">{e(caveat)}</p>
    <p class="go"><a href="{e(chart_url)}">Open the live, animated chart &rarr;</a></p>
    <p class="note">Taking you to the interactive version&hellip;</p>
  </main>
</body>
</html>
"""


def _embed_kit(stories: list[dict]) -> str:
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    cards = []
    for s in stories:
        sid = s["id"]
        headline = s.get("headline", sid)
        tagline = s.get("tagline", "")
        src = f"{SITE}/#story={sid}&embed=1"
        snippet = (
            f'<iframe src="{src}" width="800" height="560" '
            f'style="border:0;width:100%;max-width:800px;aspect-ratio:800/560" '
            f'loading="lazy" title="dataviz.ph: {e(headline)}"></iframe>'
        )
        cards.append(f"""
    <section class="story">
      <h2>{e(headline)}</h2>
      <p class="tagline">{e(tagline)}</p>
      <div class="preview">
        <iframe src="{e(src)}" loading="lazy" title="Live preview: {e(headline)}"></iframe>
      </div>
      <label>Copy-paste embed code</label>
      <div class="codewrap">
        <pre><code>{e(snippet)}</code></pre>
        <button class="copy" type="button" data-copy="{e(snippet)}">Copy</button>
      </div>
    </section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Embed kit — dataviz.ph</title>
  <meta name="description" content="Embed any dataviz.ph chart in your story. Copy-paste iframe snippets, responsive, attribution-ready.">
  <link rel="canonical" href="{SITE}/embed-kit">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{SITE}/embed-kit">
  <meta property="og:title" content="Embed kit — dataviz.ph">
  <meta property="og:description" content="Drop any dataviz.ph chart into your article with one line of HTML. Free, open data, attribution-ready.">
  <meta property="og:image" content="{SITE}/og.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:image" content="{SITE}/og.png">
  <style>
    /* Self-contained: the embed kit deliberately does NOT pull the site's
       style.css (its topbar/sidebar/grid rules reflow this single-column page). */
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#fff;color:#111;-webkit-font-smoothing:antialiased;
      font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif}}
    .ek{{max-width:880px;margin:0 auto;padding:32px 20px 80px}}
    .ek .lead{{color:#444;font-size:17px;line-height:1.6;margin:10px 0 8px}}
    .ek h1{{font-family:Georgia,serif;font-weight:600;font-size:32px;letter-spacing:-0.01em}}
    .ek .brand{{font-weight:700;font-size:16px}}
    .ek .brand span{{color:#0e7c86}}
    .ek h2{{font-family:Georgia,serif;font-weight:600;font-size:22px;margin:0 0 4px}}
    .ek .story{{border-top:1px solid #e6e6e6;padding-top:26px;margin-top:30px}}
    .ek .tagline{{color:#666;font-size:14px;line-height:1.5;margin-bottom:14px}}
    .ek .preview{{border:1px solid #e6e6e6;border-radius:10px;overflow:hidden;margin-bottom:14px;background:#fff}}
    .ek .preview iframe{{display:block;width:100%;height:460px;border:0}}
    .ek label{{display:block;font-size:12px;text-transform:uppercase;letter-spacing:0.04em;color:#888;margin-bottom:6px}}
    .ek .codewrap{{position:relative;display:flex;gap:8px;align-items:flex-start}}
    .ek pre{{flex:1;background:#0f172a;color:#e2e8f0;border-radius:8px;padding:14px 16px;overflow:auto;
      font-family:"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.5;margin:0}}
    .ek .copy{{flex:0 0 auto;background:#0e7c86;color:#fff;border:0;border-radius:8px;padding:10px 16px;
      font-size:14px;font-weight:600;cursor:pointer;min-height:44px}}
    .ek .copy:hover{{background:#0b6670}}
    .ek .copy.done{{background:#2f855a}}
    .ek .meta{{margin-top:40px;border-top:1px solid #e6e6e6;padding-top:22px;color:#555;font-size:14px;line-height:1.7}}
    .ek .meta a{{color:#0e7c86}}
    .ek .home{{display:inline-block;margin-top:8px;color:#0e7c86;font-weight:600;text-decoration:none}}
  </style>
</head>
<body>
  <main class="ek">
    <div class="brand">dataviz.<span>ph</span></div>
    <h1>Embed kit for journalists & researchers</h1>
    <p class="lead">Every chart on dataviz.ph embeds in one line of HTML. The embed is the
      live, animated chart — readers can hit play and scrub the years inside your article.
      Pick a story below, copy the snippet, paste it into your CMS.</p>
    <p class="lead">All figures are computed from public data (PSA OpenStat, PhilGEPS, PSA
      Census). Correlation, not causation; spend is contract awards, not verified disbursement.</p>
    {"".join(cards)}
    <div class="meta">
      <strong>Sizing.</strong> The snippet is responsive (it scales to its container, capped at 800px).
      For a fixed size, set <code>width</code> and <code>height</code> directly.<br>
      <strong>Attribution.</strong> Please credit &ldquo;dataviz.ph&rdquo; with a link to
      <a href="https://dataviz.ph">https://dataviz.ph</a>. The data and code are open
      (MIT). Method notes: <a href="/methodology">dataviz.ph/methodology</a>.<br>
      <strong>Custom views.</strong> Any view you build in the explorer has its own URL —
      add <code>&amp;embed=1</code> to that link to embed exactly what you see.
      <a class="home" href="/">&larr; Back to the explorer</a>
    </div>
  </main>
  <script src="/embed-kit.js" defer></script>
</body>
</html>
"""


EMBED_KIT_JS = """\
// Copy-to-clipboard for the embed-kit snippets. External file because the site
// CSP is script-src 'self' (no inline script). Progressive: the <pre> snippets
// are selectable by hand if JS is off.
document.querySelectorAll(".copy").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const code = btn.getAttribute("data-copy") || "";
    try {
      await navigator.clipboard.writeText(code);
      const prev = btn.textContent;
      btn.textContent = "Copied";
      btn.classList.add("done");
      setTimeout(() => {
        btn.textContent = prev;
        btn.classList.remove("done");
      }, 1600);
    } catch (e) {
      btn.textContent = "Press Ctrl+C";
    }
  });
});
"""


def write_share_pages(stories: list[dict], public_dir: Path) -> list[str]:
    """Write /s/<id>/index.html per story + /embed-kit + /embed-kit.js.

    Returns the list of project-relative paths written (for logging/tests).
    """
    written: list[str] = []

    for s in stories:
        sid = s["id"]
        out = public_dir / "s" / sid / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_share_page(s), encoding="utf-8")
        written.append(f"s/{sid}/index.html")

    kit = public_dir / "embed-kit" / "index.html"
    kit.parent.mkdir(parents=True, exist_ok=True)
    kit.write_text(_embed_kit(stories), encoding="utf-8")
    written.append("embed-kit/index.html")

    (public_dir / "embed-kit.js").write_text(EMBED_KIT_JS, encoding="utf-8")
    written.append("embed-kit.js")

    return written


def main() -> None:
    public_dir = Path(__file__).resolve().parent.parent / "public"
    stories = json.loads((public_dir / "data" / "stories.json").read_text())
    written = write_share_pages(stories, public_dir)
    print(f">> wrote {len(written)} share/embed files:")
    for w in written:
        print(f"   public/{w}")


if __name__ == "__main__":
    main()
