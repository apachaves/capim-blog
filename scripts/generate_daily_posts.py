#!/usr/bin/env python3
"""
Capim — Daily Post Generator
==========================================
Generates one new blog post per category, injects it into blogData.ts,
generates a contextual capybara illustration, and uploads it to CDN.

Usage:
    python3 scripts/generate_daily_posts.py

Environment variables required:
    OPENAI_API_KEY   — OpenAI API key (or compatible endpoint key)
    OPENAI_BASE_URL  — Optional: custom base URL

The script reads existing posts from blogData.ts to avoid topic repetition.
"""

import os
import re
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
BLOG_DATA_FILE = PROJECT_ROOT / "client" / "src" / "lib" / "blogData.ts"
ASSETS_DIR = Path("/home/ubuntu/webdev-static-assets")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
TODAY = date.today().isoformat()

CATEGORIES = ["philosophy", "tech", "nature", "ml", "games"]

API_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or os.environ.get("BUILT_IN_FORGE_API_KEY")
    or ""
)
BASE_URL = (
    os.environ.get("OPENAI_BASE_URL")
    or os.environ.get("BUILT_IN_FORGE_API_URL")
    or "https://api.openai.com/v1"
).rstrip("/")

COMPLETIONS_URL = f"{BASE_URL}/chat/completions"
IMAGES_URL = f"{BASE_URL}/images/generations"
MODEL = "gpt-4.1-mini"
IMAGE_MODEL = "dall-e-3"

# ── Blog identity ──────────────────────────────────────────────────────────────

BLOG_IDENTITY = """
Capim is a personal journal blog exploring a deep tension that runs through all of 
human thought and culture:

One worldview sees the world as a living, organic, interconnected whole — where meaning emerges 
from relationships, where complexity cannot be reduced to its parts, where nature has its own 
wisdom that precedes human understanding. It values emergence over design, process over product, 
the wisdom of evolved systems over engineered ones.

The opposing worldview sees the world as a system to be understood, modeled, and improved — where 
knowledge is power, where engineering is the highest form of respect for nature, where intelligence 
can and should be applied to solve problems that evolution left unsolved. It values precision, 
control, optimization, and the ability to transcend biological constraints.

These are not good vs. evil. Both have profound insights. The blog explores where they conflict, 
where they complement each other, and what current events reveal about this ancient tension.

CRITICAL: Do NOT reference Magic: The Gathering, card games, or any game's color philosophy 
explicitly. These ideas stand on their own without needing a game as a frame.

The author is Brazilian, which occasionally colors their perspective — but this is never the 
main subject unless directly relevant.

Tone: calm, cozy, intellectual. Like a warm conversation in a well-worn library. 
Curious, not preachy. Willing to sit with contradiction.
"""

CATEGORY_PROMPTS = {
    "philosophy": """
Write a philosophy post exploring a specific philosophical problem at the intersection of 
nature, mind, technology, and ethics. Engage with actual philosophers and their arguments.
Possible angles: consciousness and panpsychism, the ethics of enhancement, what it means 
to be "natural", process philosophy vs substance metaphysics, emergence and reduction,
the hard problem of consciousness, Spinoza's monism, Deleuze's becoming, Buddhist impermanence.
""",
    "tech": """
Write a tech post connecting a programming or systems concept to broader themes of ecology, 
philosophy, or nature. The technology should be a lens, not the subject.
Possible angles: Rust's ownership model as ecological thinking, distributed systems as ecosystems,
type systems as ontologies, functional programming and immutability, version control as memory,
the philosophy of open source, formal verification and trust, the aesthetics of code.
Include at least one code snippet if relevant.
""",
    "nature": """
Write a nature post about a biological or ecological phenomenon that challenges our assumptions 
about individuality, intelligence, or design. Ground it in actual research.
Possible angles: mycorrhizal networks and individuality, epigenetics and nature/nurture,
convergent evolution, extremophiles and the definition of life, CRISPR ethics, rewilding,
the microbiome as identity, phenotypic plasticity, the evolution of cooperation, endosymbiosis.
""",
    "ml": """
Write an ML/AI post that is technically grounded but philosophically engaged. Be honest about 
where analogies break down. Reference real papers and researchers.
Possible angles: the alignment problem as evolutionary mismatch, emergent capabilities,
attention mechanisms and consciousness, optimization vs understanding, the bitter lesson,
neural scaling laws, mechanistic interpretability, compression and intelligence, embodied cognition.
""",
    "games": """
Write a post using games, science fiction, or speculative fiction as a lens for real 
philosophical or ethical questions. Connect to current debates.
Possible angles: game design as systems thinking, roguelikes and emergence, procedural generation,
simulated ecology in games, the ethics of player choice, speedrunning as optimization,
science fiction's treatment of uplift/enhancement, worldbuilding as thought experiment.
""",
}


# ── Step 1: Extract existing posts ────────────────────────────────────────────

def extract_existing_posts(content: str) -> list[dict]:
    """Parse blogData.ts and extract post metadata."""
    posts = []
    id_pattern = re.compile(r'id:\s*["\']([^"\']+)["\']')
    title_pattern = re.compile(r'title:\s*["\']([^"\']+)["\']')
    category_pattern = re.compile(r'category:\s*["\']([^"\']+)["\']')
    date_pattern = re.compile(r'date:\s*["\']([^"\']+)["\']')
    tags_pattern = re.compile(r'tags:\s*\[([^\]]+)\]')

    blocks = re.split(r'\n\s*\{', content)
    for block in blocks[1:]:
        id_m = id_pattern.search(block)
        title_m = title_pattern.search(block)
        cat_m = category_pattern.search(block)
        date_m = date_pattern.search(block)
        tags_m = tags_pattern.search(block)

        if id_m and title_m and cat_m and date_m:
            tags = []
            if tags_m:
                tags = [t.strip().strip('"\'') for t in tags_m.group(1).split(',') if t.strip()]
            posts.append({
                "id": id_m.group(1),
                "title": title_m.group(1),
                "category": cat_m.group(1),
                "date": date_m.group(1),
                "tags": tags,
            })
    return posts


def get_next_id(category: str, existing_posts: list[dict]) -> str:
    """Generate the next sequential ID for a category."""
    prefix_map = {
        "philosophy": "phil",
        "tech": "tech",
        "nature": "nature",
        "ml": "ml",
        "games": "games",
    }
    prefix = prefix_map.get(category, category[:4])
    existing_ids = [p["id"] for p in existing_posts if p["id"].startswith(prefix + "-")]
    nums = []
    for eid in existing_ids:
        m = re.search(r"-(\d+)$", eid)
        if m:
            nums.append(int(m.group(1)))
    next_num = (max(nums) + 1) if nums else 1
    return f"{prefix}-{next_num:03d}"


# ── Step 2: Generate a post via AI ────────────────────────────────────────────

def generate_post(category: str, existing_posts: list[dict]) -> dict | None:
    """Call the AI API to generate a new blog post for the given category."""
    category_posts = [p for p in existing_posts if p["category"] == category]
    existing_summary = ""
    if category_posts:
        existing_summary = "EXISTING POSTS IN THIS CATEGORY (do NOT repeat these topics):\n"
        for p in category_posts:
            existing_summary += f"- [{p['date']}] {p['title']} (tags: {', '.join(p['tags'])})\n"

    system_prompt = f"""You are the author of Capim, a personal journal blog.
{BLOG_IDENTITY}

Your task is to write ONE new blog post for the category: {category.upper()}

{CATEGORY_PROMPTS[category]}

{existing_summary}

CITATION REQUIREMENTS (mandatory):
- Include at least 2-3 real citations using markdown links: [text](url)
- For books or films without direct URLs, use: (Book: "Title" by Author, Year) or (Film: "Title", Year)
- At least one citation must be to a recent source (2022-2026)
- Include at least one current event or recent media discussion that illustrates the post's tension

OUTPUT FORMAT: Respond with ONLY a valid JSON object (no markdown wrapper, no explanation):
{{
  "title": "The post title",
  "excerpt": "A 1-2 sentence hook that captures the central tension (max 200 chars)",
  "content": "Full markdown content (600-900 words). Use ## for section headers. Write in first person. Include real citations as markdown links.",
  "tags": ["tag1", "tag2", "tag3", "tag4"],
  "readTime": 7,
  "capybaraImagePrompt": "A detailed prompt for a cozy capybara illustration that metaphorically represents this post's topic. The capybara should be doing something that relates to the theme. Warm flat illustration style, round glasses, chubby body, content expression. Landscape format."
}}
"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Write a new {category} post for today ({TODAY})."},
        ],
        "temperature": 0.85,
        "max_tokens": 2200,
    }

    print(f"  → Calling API for category: {category}...")
    try:
        resp = requests.post(COMPLETIONS_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if present
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())

        post_data = json.loads(raw)
        for field in ["title", "excerpt", "content", "tags", "readTime"]:
            if field not in post_data:
                raise ValueError(f"Missing field: {field}")

        post_data["category"] = category
        post_data["date"] = TODAY
        print(f"  ✓ Generated: {post_data['title'][:65]}")
        return post_data

    except requests.RequestException as e:
        print(f"  ✗ API error: {e}")
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  ✗ Parse error: {e}")
        if 'raw' in locals():
            print(f"    Raw (first 400 chars): {raw[:400]}")
    return None


# ── Step 3: Generate capybara image ───────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY", "apachaves/capim-blog")


def upload_to_github_releases(img_path: Path, category: str) -> str | None:
    """
    Upload an image to GitHub Releases as a binary asset.
    Returns the CDN URL (github.com releases download URL) or None.
    Requires GITHUB_TOKEN env var.
    """
    if not GITHUB_TOKEN:
        return None

    tag = f"capybara-images-{TODAY}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    # Check if release already exists for today
    releases_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{tag}"
    r = requests.get(releases_url, headers=headers, timeout=30)
    if r.status_code == 200:
        release_id = r.json()["id"]
        upload_url = r.json()["upload_url"].split("{?")[0]
    else:
        # Create a new release
        create_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
        payload = {
            "tag_name": tag,
            "name": f"Capybara Images — {TODAY}",
            "body": f"Auto-generated capybara illustrations for {TODAY}",
            "draft": False,
            "prerelease": False,
        }
        r = requests.post(create_url, headers=headers, json=payload, timeout=30)
        if r.status_code not in (200, 201):
            print(f"  ✗ Could not create GitHub release: {r.status_code} {r.text[:200]}")
            return None
        release_id = r.json()["id"]
        upload_url = r.json()["upload_url"].split("{?")[0]

    # Upload the image asset
    asset_name = img_path.name
    upload_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "image/png",
    }
    with open(img_path, "rb") as f:
        r = requests.post(
            f"{upload_url}?name={asset_name}",
            headers=upload_headers,
            data=f,
            timeout=120,
        )
    if r.status_code in (200, 201):
        cdn_url = r.json()["browser_download_url"]
        print(f"  ✓ GitHub Releases CDN URL: {cdn_url}")
        return cdn_url
    else:
        print(f"  ✗ GitHub asset upload failed: {r.status_code} {r.text[:200]}")
        return None


def generate_capybara_image(prompt: str, category: str) -> str | None:
    """Generate a capybara illustration and upload to CDN. Returns CDN URL or None."""
    full_prompt = (
        f"{prompt} "
        "The capybara has round reading glasses, warm tawny brown fur, chubby round body, "
        "gentle half-closed content eyes. Warm flat illustration style inspired by Studio Ghibli. "
        "Muted warm palette: tawny browns, soft cream, sage greens. Cozy, calm, intellectual mood. "
        "Landscape format, wide composition."
    )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_MODEL,
        "prompt": full_prompt[:1000],  # DALL-E 3 limit
        "n": 1,
        "size": "1792x1024",
        "quality": "standard",
    }

    print(f"  → Generating capybara illustration...")
    try:
        resp = requests.post(IMAGES_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        image_url = resp.json()["data"][0]["url"]

        # Download image
        img_resp = requests.get(image_url, timeout=60)
        img_resp.raise_for_status()

        img_path = ASSETS_DIR / f"capybara-{category}-{TODAY}.png"
        img_path.write_bytes(img_resp.content)
        print(f"  ✓ Image saved: {img_path.name}")

        # Upload to CDN — try manus-upload-file first (Manus sandbox),
        # fall back to GitHub Releases (CI / GitHub Actions)
        cdn_url = None
        manus_tool = subprocess.run(["which", "manus-upload-file"], capture_output=True)
        if manus_tool.returncode == 0:
            result = subprocess.run(
                ["manus-upload-file", str(img_path)],
                capture_output=True, text=True, timeout=300,
            )
            output = result.stdout + result.stderr
            match = re.search(r"CDN URL: (https://\S+)", output)
            if match:
                cdn_url = match.group(1)
                print(f"  ✓ Manus CDN URL: {cdn_url}")
            else:
                print(f"  ✗ manus-upload-file failed: {output[:300]}")
        else:
            print("  → manus-upload-file not available, trying GitHub Releases...")
            cdn_url = upload_to_github_releases(img_path, category)

        if cdn_url:
            return cdn_url
    except Exception as e:
        print(f"  ✗ Image generation/upload failed: {e}")
    return None


# ── Step 4: Convert post dict to TypeScript object literal ────────────────────

def post_to_ts(post: dict, post_id: str, cdn_url: str | None) -> str:
    """Convert a post dict to a TypeScript object literal string."""
    title = post["title"].replace('"', '\\"')
    excerpt = post["excerpt"].replace('"', '\\"')
    content = post["content"]
    content = content.replace("\\", "\\\\")
    content = content.replace("`", "\\`")
    content = content.replace("${", "\\${")
    tags_str = ", ".join(f'"{t}"' for t in post["tags"])
    capybara_line = f'\n    capybaraImage: "{cdn_url}",' if cdn_url else ""

    return f'''  {{
    id: "{post_id}",
    title: "{title}",
    category: "{post["category"]}",
    date: "{post["date"]}",
    excerpt: "{excerpt}",
    content: `{content}`,
    tags: [{tags_str}],
    readTime: {post["readTime"]},{capybara_line}
  }}'''


# ── Step 5: Inject posts into blogData.ts ─────────────────────────────────────

INJECTION_MARKER = "// ── AUTO-GENERATED POSTS (do not edit this marker) ──────────────────────────"


def inject_posts(new_posts_ts: list[str]) -> bool:
    """Inject new posts into blogData.ts before the closing ];"""
    content = BLOG_DATA_FILE.read_text(encoding="utf-8")

    # Find the closing ]; of the blogPosts array
    closing_idx = content.rfind("];")
    if closing_idx == -1:
        print("  ✗ Could not find closing ]; in blogData.ts")
        return False

    new_block = ",\n".join(new_posts_ts)
    new_content = content[:closing_idx] + f"\n{new_block},\n" + content[closing_idx:]

    BLOG_DATA_FILE.write_text(new_content, encoding="utf-8")
    print(f"  ✓ Injected {len(new_posts_ts)} posts into {BLOG_DATA_FILE.name}")
    return True


# ── Step 6: Run pnpm build ────────────────────────────────────────────────────

def run_build() -> bool:
    """Run pnpm build in the project root."""
    print("\n── Running pnpm build ──────────────────────────────────────────────────")
    result = subprocess.run(["pnpm", "build"], cwd=PROJECT_ROOT)
    if result.returncode == 0:
        print("✓ Build succeeded")
        return True
    print(f"✗ Build failed (exit code {result.returncode})")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Capim — Daily Post Generator")
    print(f"Date: {TODAY} | Model: {MODEL}")
    print(f"API: {'SET' if API_KEY else 'NOT SET'}")
    print("=" * 60)

    if not API_KEY:
        print("✗ No API key. Set OPENAI_API_KEY.")
        sys.exit(1)

    if not BLOG_DATA_FILE.exists():
        print(f"✗ blogData.ts not found at {BLOG_DATA_FILE}")
        sys.exit(1)

    # Read existing posts
    original_content = BLOG_DATA_FILE.read_text(encoding="utf-8")
    existing_posts = extract_existing_posts(original_content)
    print(f"\n✓ Found {len(existing_posts)} existing posts")

    # Generate posts
    print("\n── Generating posts ────────────────────────────────────────────────────")
    new_posts_ts = []
    failed = []

    for category in CATEGORIES:
        print(f"\n[{category.upper()}]")
        post_data = generate_post(category, existing_posts)
        if not post_data:
            failed.append(category)
            continue

        # Generate capybara image
        cdn_url = None
        capybara_prompt = post_data.get("capybaraImagePrompt", "")
        if capybara_prompt:
            cdn_url = generate_capybara_image(capybara_prompt, category)

        post_id = get_next_id(category, existing_posts)
        ts = post_to_ts(post_data, post_id, cdn_url)
        new_posts_ts.append(ts)

        # Update in-memory list so next category gets correct IDs
        existing_posts.append({
            "id": post_id,
            "title": post_data["title"],
            "category": category,
            "date": TODAY,
            "tags": post_data.get("tags", []),
        })

        time.sleep(1)  # be polite to the API

    if not new_posts_ts:
        print("\n✗ All categories failed. Aborting.")
        sys.exit(1)

    if failed:
        print(f"\n⚠ Failed categories: {', '.join(failed)}")

    # Inject posts
    print("\n── Injecting posts ─────────────────────────────────────────────────────")
    if not inject_posts(new_posts_ts):
        print("✗ Injection failed.")
        sys.exit(1)

    # Build
    if not run_build():
        print("\n✗ Build failed. Restoring original blogData.ts...")
        BLOG_DATA_FILE.write_text(original_content, encoding="utf-8")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(f"✓ Done! Generated {len(new_posts_ts)}/{len(CATEGORIES)} posts for {TODAY}")
    print("=" * 60)


if __name__ == "__main__":
    main()
