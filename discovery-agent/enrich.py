"""
NBP Booster Club Enrichment Agent (Claude-based)
=================================================
Replaces the old DDG-scraping discovery agent that hit rate-limit walls.

Runs weekly on Railway cron (Sunday 2am ET recommended).
Pulls dashboard-data.json from GitHub, uses Claude + web_search to fill
gaps in club data (FB, Instagram, email, AD, activity status), then
commits the updated JSON back to GitHub.

Required env vars:
- ANTHROPIC_API_KEY  : Anthropic API key
- GITHUB_TOKEN       : GitHub PAT with repo write access
- GITHUB_REPO        : default "nathangbingle/nbp-school-proposals"
- DATA_PATH          : default "dashboard-data.json"
- MODEL              : default "claude-haiku-4-5-20251001"
- DRY_RUN            : "1" to skip the commit/push step (for testing)
- COOLDOWN_DAYS      : skip clubs attempted within this many days (default 14)
- MAX_CLUBS          : cap clubs processed per run (default 30, set lower for safety)
"""
import os
import json
import time
import logging
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import urllib.request
import urllib.error

import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "nathangbingle/nbp-school-proposals").strip()
DATA_PATH = os.environ.get("DATA_PATH", "dashboard-data.json").strip()
MODEL = os.environ.get("MODEL", "claude-haiku-4-5-20251001").strip()
DRY_RUN = os.environ.get("DRY_RUN", "0").strip() == "1"
COOLDOWN_DAYS = int(os.environ.get("COOLDOWN_DAYS", "14"))
MAX_CLUBS = int(os.environ.get("MAX_CLUBS", "30"))

WORKDIR = Path("/tmp/nbp-enrich")
RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{DATA_PATH}"
GIT_URL = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("enrich")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def has_gaps(club: dict) -> list:
    """Return a list of fields that are missing/null on a club."""
    if club.get("excluded"):
        return []
    gaps = []
    for field in ("fb", "ig", "email", "ad", "website", "activity"):
        v = club.get(field)
        if not v or (isinstance(v, str) and "PENDING" in v.upper()):
            gaps.append(field)
    return gaps


def in_cooldown(club: dict) -> bool:
    """True if we tried to enrich this club too recently."""
    last = club.get("last_enrichment_attempted")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - last_dt
        return age < timedelta(days=COOLDOWN_DAYS)
    except Exception:
        return False


def too_many_misses(club: dict) -> bool:
    """If we've struck out 3+ times, only retry quarterly."""
    misses = club.get("enrichment_misses", 0)
    if misses < 3:
        return False
    last = club.get("last_enrichment_attempted")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - last_dt
        return age < timedelta(days=90)
    except Exception:
        return False


def fetch_data() -> dict:
    """Pull the live dashboard-data.json from GitHub raw."""
    log.info("Fetching live data from %s", RAW_URL)
    try:
        with urllib.request.urlopen(RAW_URL, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        log.error("Failed to fetch data: %s", e)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Claude enrichment
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a data enrichment agent for a school photography business.
Your job: find missing public contact info for school athletic booster clubs in the Carolinas.

You have access to a web_search tool. Use it to find:
- Facebook page URL (search "[School Name] Athletic Booster Club Facebook")
- Instagram handle/URL (search "[School Name] booster instagram" or athletic dept IG)
- Public email (booster club gmail/yahoo, or athletic dept email)
- Athletic Director name + email (usually on the school's athletic site or CMS/UCPS/FMSD athletic zone)
- Website (booster club site, school athletic site, or athletic zone page)
- Activity status (FB followers count, recent post recency, fundraising signs)

CRITICAL RULES:
1. Only return URLs/emails you have actually seen in search results. NEVER invent.
2. If a field genuinely doesn't exist publicly, return null for that field.
3. For Instagram, prefer the booster club account. If only the school athletic dept has IG, that's acceptable — note it in activity.
4. Distinguish the correct school from same-named schools elsewhere (e.g. there are multiple "Bailey Middle Schools" — confirm by city/state).
5. Output ONLY a valid JSON object with this exact shape, no markdown fences, no commentary:

{
  "fb": "https://..." or null,
  "ig": "https://www.instagram.com/handle/" or null,
  "email": "name@domain.com" or null,
  "ad": "Name — email@domain.com" or null,
  "website": "https://..." or null,
  "activity": "short status string under 80 chars" or null,
  "notes": "any caveats, like 'no dedicated booster FB found, school uses PTSO'" or null
}

If you cannot find anything new for a club, return all nulls. That's acceptable — better than fabricating."""


def enrich_club(client: anthropic.Anthropic, club: dict) -> dict | None:
    """Call Claude with web_search to find missing data for one club.
    Returns parsed JSON dict on success, or None on failure."""
    gaps = has_gaps(club)
    if not gaps:
        return None

    current = {k: club.get(k) for k in ("name", "school", "district", "zip", "tier", "school_type", "fb", "ig", "email", "ad", "website", "activity")}

    user_msg = (
        f"Find missing booster club info for this school.\n\n"
        f"Current data:\n```json\n{json.dumps(current, indent=2)}\n```\n\n"
        f"Missing fields: {', '.join(gaps)}\n\n"
        f"Search the web and return the JSON object as specified in your instructions. "
        f"Only fill fields you found new info for; leave others as null."
    )

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.APIError as e:
        log.warning("[%s] API error: %s", club["id"], e)
        return None
    except Exception as e:
        log.warning("[%s] Unexpected error: %s", club["id"], e)
        return None

    # Extract the final text block (after any tool calls)
    text_blocks = [b.text for b in resp.content if hasattr(b, "text") and b.text]
    full_text = "\n".join(text_blocks).strip()

    if not full_text:
        log.warning("[%s] Empty response", club["id"])
        return None

    # Strip ```json fences if Claude added them despite instructions
    cleaned = full_text
    if "```" in cleaned:
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Find the first { ... } block as fallback
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                result = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                log.warning("[%s] Could not parse JSON: %s", club["id"], cleaned[:200])
                return None
        else:
            log.warning("[%s] No JSON found in response: %s", club["id"], cleaned[:200])
            return None

    return result


def apply_enrichment(club: dict, found: dict) -> list:
    """Apply found fields to club, return list of fields that were updated."""
    updated = []
    for field in ("fb", "ig", "email", "ad", "website", "activity"):
        new_val = found.get(field)
        if not new_val:
            continue
        # Don't overwrite an existing real value with the same/lesser value
        old_val = club.get(field)
        if old_val and isinstance(old_val, str) and old_val.strip() and "PENDING" not in old_val.upper():
            # Only overwrite if old was empty or pending
            continue
        club[field] = new_val
        updated.append(field)

    notes = found.get("notes")
    if notes:
        existing = club.get("note") or ""
        if notes not in existing:
            club["note"] = (existing + " | " if existing else "") + f"[agent] {notes}"

    # Track attempt
    club["last_enrichment_attempted"] = datetime.now(timezone.utc).isoformat()
    if updated:
        club["enrichment_misses"] = 0
    else:
        club["enrichment_misses"] = club.get("enrichment_misses", 0) + 1

    return updated


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------
def run(cmd: list, cwd: Path = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, log it, raise on failure."""
    log.info("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout.strip():
        log.info(result.stdout.strip())
    if result.stderr.strip():
        log.warning(result.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def commit_and_push(data: dict, summary: str) -> None:
    """Clone repo to /tmp, write updated data, commit, push."""
    if WORKDIR.exists():
        run(["rm", "-rf", str(WORKDIR)])
    WORKDIR.mkdir(parents=True, exist_ok=True)

    run(["git", "clone", "--depth=1", GIT_URL, str(WORKDIR)])
    run(["git", "config", "user.email", "agent@nbp-photography.local"], cwd=WORKDIR)
    run(["git", "config", "user.name", "NBP Discovery Agent"], cwd=WORKDIR)

    target = WORKDIR / DATA_PATH
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Check if anything actually changed
    diff = run(["git", "diff", "--stat", DATA_PATH], cwd=WORKDIR, check=False)
    if not diff.stdout.strip():
        log.info("No changes to commit (data already up-to-date).")
        return

    run(["git", "add", DATA_PATH], cwd=WORKDIR)
    run(["git", "commit", "-m", f"Weekly enrichment: {summary}"], cwd=WORKDIR)
    run(["git", "push", "origin", "main"], cwd=WORKDIR)
    log.info("✓ Pushed to %s", GITHUB_REPO)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set"); sys.exit(1)
    if not GITHUB_TOKEN and not DRY_RUN:
        log.error("GITHUB_TOKEN not set (use DRY_RUN=1 for local testing)"); sys.exit(1)

    started = datetime.now(timezone.utc)
    log.info("=== NBP Booster Enrichment Agent ===")
    log.info("Model: %s | Repo: %s | Cooldown: %dd | Max: %d | Dry: %s",
             MODEL, GITHUB_REPO, COOLDOWN_DAYS, MAX_CLUBS, DRY_RUN)

    data = fetch_data()
    clubs = data["clubs"]
    log.info("Loaded %d clubs (data version %s)", len(clubs), data["meta"].get("version"))

    # Filter to clubs that need enrichment
    candidates = []
    for c in clubs:
        if c.get("excluded"):
            continue
        gaps = has_gaps(c)
        if not gaps:
            continue
        if in_cooldown(c):
            log.debug("[%s] in cooldown, skipping", c["id"])
            continue
        if too_many_misses(c):
            log.debug("[%s] too many misses, quarterly retry", c["id"])
            continue
        candidates.append((c, gaps))

    candidates = candidates[:MAX_CLUBS]
    log.info("Processing %d candidates with gaps", len(candidates))

    if not candidates:
        log.info("Nothing to enrich. Exiting cleanly.")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    total_updates = 0
    clubs_changed = 0
    summary_lines = []

    for club, gaps in candidates:
        log.info("[%s] %s — gaps: %s", club["id"], club["name"][:50], gaps)
        found = enrich_club(client, club)
        if found is None:
            log.info("[%s] No result", club["id"])
            club["last_enrichment_attempted"] = datetime.now(timezone.utc).isoformat()
            club["enrichment_misses"] = club.get("enrichment_misses", 0) + 1
            continue

        updated = apply_enrichment(club, found)
        if updated:
            clubs_changed += 1
            total_updates += len(updated)
            log.info("[%s] ✓ Updated: %s", club["id"], updated)
            summary_lines.append(f"{club['id']}: +{','.join(updated)}")
        else:
            log.info("[%s] No new data found", club["id"])

        # Rate-limit politeness between API calls
        time.sleep(2)

    # Update meta
    data["meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["last_agent_run"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["last_agent_clubs_changed"] = clubs_changed
    data["meta"]["last_agent_fields_added"] = total_updates
    data["meta"]["version"] = data["meta"].get("version", 0) + 1

    summary = f"{clubs_changed} clubs / {total_updates} fields"
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("=== DONE: %s in %.1fs ===", summary, elapsed)

    if clubs_changed == 0:
        log.info("Nothing changed; skipping commit.")
        return

    if DRY_RUN:
        log.info("DRY_RUN — would commit:")
        for line in summary_lines:
            log.info("  %s", line)
        return

    full_summary = summary + "\n\n" + "\n".join(summary_lines[:20])
    commit_and_push(data, full_summary)


if __name__ == "__main__":
    main()
