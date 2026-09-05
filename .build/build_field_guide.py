import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from taxonomy import CATEGORIES

# Derive the project root from this file so the build works from any checkout
# location (and inside the Cowork VM, where the folder is mounted elsewhere).
ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).parent / "field-guide-template.html"
OUT = ROOT / "field-guide.html"

IMG_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
EXT_MIME = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "svg": "svg+xml", "webp": "webp"}


def embed_local_images(md_text, base_dir):
    """Rewrite ![alt](relative/path.png) into an inlined base64 data URI,
    resolved relative to base_dir. Leaves http(s)/data: URLs untouched.
    Silently leaves the reference as-is if the file doesn't exist."""

    def repl(m):
        alt, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://", "data:")):
            return m.group(0)
        img_path = (base_dir / path).resolve()
        if not img_path.exists():
            return m.group(0)
        ext = img_path.suffix.lstrip(".").lower()
        mime = EXT_MIME.get(ext, "png")
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f"![{alt}](data:image/{mime};base64,{b64})"

    return IMG_RE.sub(repl, md_text)


LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)\s]+)\)')


def resolve_internal_links(md_text, base_dir, path_to_pageid):
    """Rewrite [label](relative/path.md) into [label](#pageId) when the
    target resolves to a page this build knows about (tried relative to
    the source file's own directory, then relative to the project root).
    Leaves the link as-is (still a dead link until that topic is written)
    if it doesn't match a known page yet."""

    def repl(m):
        label, path = m.group(1), m.group(2)
        if path.startswith(("http://", "https://", "data:", "#")):
            return m.group(0)
        for candidate in ((base_dir / path).resolve(), (ROOT / path.lstrip("/")).resolve()):
            pid = path_to_pageid.get(candidate)
            if pid:
                return f"[{label}](#{pid})"
        return m.group(0)

    return LINK_RE.sub(repl, md_text)


PAGES = {}
PAGE_DIAGRAMS = {}  # legacy mechanism: pageId -> [data URI, ...], matched in order
                     # against claude.ai-artifact links still present in a page's markdown.
PAGE_BASEDIR = {}
PATH_TO_PAGEID = {}


def add_single_page(page_id, md_path):
    if not md_path.exists():
        return False
    raw = md_path.read_text(encoding="utf-8")
    PAGES[page_id] = {"md": embed_local_images(raw, md_path.parent)}
    PAGE_BASEDIR[page_id] = md_path.parent
    PATH_TO_PAGEID[md_path.resolve()] = page_id
    return True


def add_legacy_diagram(page_id, png_path):
    if not png_path.exists():
        return
    b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    PAGE_DIAGRAMS.setdefault(page_id, []).append(f"data:image/png;base64,{b64}")


nav_sections = []
nav_topics = {}
page_owner = {}

MODULE6_TABS = [
    ("00-overview.md", "Overview", "diagrams/00-overview.png"),
    ("01-architecture-hld.md", "Architecture & HLD", "diagrams/01-architecture.png"),
    ("02-lld.md", "LLD", "diagrams/02-sequence.png"),
    ("03-db-design.md", "DB Design", "diagrams/03-er.png"),
    ("04-interviewer-qna.md", "Interviewer Q&A", None),
    ("README.md", "Module Guide", None),
]

URLSHORTENER_TABS = [
    ("01-hld-fundamentals.md", "High-Level Design (HLD)"),
    ("02-lld-fundamentals.md", "Low-Level Design (LLD)"),
    ("03-db-design-fundamentals.md", "Database Design"),
    ("04-practice-problems.md", "Practice Problems"),
]

for cat in CATEGORIES:
    cat_id = cat["id"]
    folder_style = cat.get("folder_style", False)
    written_items = []

    for item in cat["items"]:
        slug = item["slug"]
        label = item["label"]
        kind = item.get("kind", "single")
        page_id = f"{cat_id}--{slug}"

        if kind == "single":
            if folder_style:
                md_path = ROOT / "content" / cat_id / slug / "README.md"
            else:
                md_path = ROOT / "content" / cat_id / f"{slug}.md"
            if add_single_page(page_id, md_path):
                written_items.append({"kind": "page", "pageId": page_id, "label": label, "star": item.get("star", False)})
                page_owner[page_id] = {"kind": "page", "sectionLabel": cat["label"], "itemLabel": label}

        elif kind == "topic-urlshortener":
            tabs = []
            any_written = False
            for fname, tab_label in URLSHORTENER_TABS:
                tpid = f"{page_id}--{fname.split('.')[0]}"
                if add_single_page(tpid, ROOT / fname):
                    any_written = True
                tabs.append({"pageId": tpid, "label": tab_label})
                page_owner[tpid] = {"kind": "topic", "topicId": page_id}
            if any_written:
                nav_topics[page_id] = {
                    "label": label,
                    "lede": "The one running example carried through HLD, LLD, and a schema, three times over.",
                    "sectionLabel": cat["label"],
                    "tabs": tabs,
                }
                written_items.append({"kind": "topic", "topicId": page_id, "label": label, "star": item.get("star", False)})

        elif kind == "topic-module6":
            topic_dir = ROOT / item["dir"]
            tabs = []
            any_written = False
            for fname, tab_label, diagram_rel in MODULE6_TABS:
                tpid = f"{page_id}--{fname.split('.')[0]}"
                if add_single_page(tpid, topic_dir / fname):
                    any_written = True
                    if diagram_rel:
                        add_legacy_diagram(tpid, topic_dir / diagram_rel)
                tabs.append({"pageId": tpid, "label": tab_label})
                page_owner[tpid] = {"kind": "topic", "topicId": page_id}
            if any_written:
                nav_topics[page_id] = {
                    "label": label,
                    "lede": item.get("lede", ""),
                    "sectionLabel": cat["label"],
                    "tabs": tabs,
                }
                written_items.append({"kind": "topic", "topicId": page_id, "label": label, "star": item.get("star", False)})

    if written_items:
        nav_sections.append({
            "id": cat_id,
            "label": cat["label"],
            "color": cat["color"],
            "blurb": cat.get("blurb", ""),
            "items": written_items,
        })

for pid, page in PAGES.items():
    page["md"] = resolve_internal_links(page["md"], PAGE_BASEDIR[pid], PATH_TO_PAGEID)

total_written = sum(len(s["items"]) for s in nav_sections)
total_planned = sum(len(c["items"]) for c in CATEGORIES)

NAV = {
    "landing": {
        "title": "System Design, Worked End to End",
        "lede": (
            f"{total_written} of {total_planned} planned topics are live so far, across "
            f"{len(nav_sections)} categories — building blocks, database design, low-level design, "
            "worked case studies, and narrow production-shaped deep dives, each with a diagram for "
            "every decision and the load/concurrency questions an interviewer will actually ask."
        ),
    },
    "sections": nav_sections,
    "topics": nav_topics,
    "pageOwner": page_owner,
}

template = TEMPLATE.read_text(encoding="utf-8")
out = (
    template.replace("__PAGES_JSON__", json.dumps(PAGES))
    .replace("__NAV_JSON__", json.dumps(NAV))
    .replace("__PAGE_DIAGRAMS_JSON__", json.dumps(PAGE_DIAGRAMS))
)
OUT.write_text(out, encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size, "bytes")
print(f"topics written: {total_written} / {total_planned}")
for s in nav_sections:
    print(f"  {s['label']}: {len(s['items'])}")
