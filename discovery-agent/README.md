# NBP Booster Enrichment Agent

Weekly Claude-based agent that finds missing public contact info for booster clubs in `dashboard-data.json` and commits updates back to GitHub.

## What it replaces

The old `nbp-booster-discovery` agent scraped DuckDuckGo HTML and got rate-limited into uselessness (last run: 0 found / 12 blocked). This version uses the Anthropic API's built-in `web_search` tool — no scraping, no blocks, structured output.

## How it works

1. Pulls live `dashboard-data.json` from GitHub raw
2. For each club with gaps (missing FB / IG / email / AD / website / activity), calls Claude Haiku 4.5 with the `web_search` tool
3. Claude searches the web 1–5 times per club, returns structured JSON (no fabrication — only fields it actually saw)
4. Applies updates without overwriting existing real values
5. Commits + pushes back to `nathangbingle/nbp-school-proposals`
6. Tracks `last_enrichment_attempted` + `enrichment_misses` per club; clubs that strike out 3+ times move to quarterly retry

## Cost

Roughly **$1.50–2 per run**, so ~$8/month on a weekly cron:
- ~30 clubs × ~3 web searches each = ~90 searches × $10/1000 = $0.90
- ~5K input tokens × 30 + ~1K output × 30 = ~$0.50 on Haiku 4.5
- Free Railway tier covers the compute (job runs <2 min)

## Deploy on Railway

1. Create a new Railway service, source = GitHub
2. Connect repo: `nathangbingle/nbp-school-proposals`
3. **Set root directory** to `/discovery-agent`
4. Add environment variables:
   - `ANTHROPIC_API_KEY` — your Anthropic API key
   - `GITHUB_TOKEN` — `ghp_...` PAT with `repo` scope (you already have one)
5. The `railway.json` in this folder sets the cron schedule to `0 7 * * 0` (Sundays 7am UTC = 3am ET)
6. Disable / delete the old `nbp-booster-discovery` service so it stops burning Railway minutes

## Test it locally first

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export DRY_RUN=1            # don't push, just log what would change
export MAX_CLUBS=2          # only process 2 clubs for a quick test
python enrich.py
```

## Tunable env vars

| Var | Default | What it does |
|---|---|---|
| `MODEL` | `claude-haiku-4-5-20251001` | bump to Sonnet for harder cases |
| `COOLDOWN_DAYS` | 14 | skip clubs attempted within N days |
| `MAX_CLUBS` | 30 | cap clubs per run (cost control) |
| `DRY_RUN` | 0 | set to 1 to skip the commit/push |
| `GITHUB_REPO` | `nathangbingle/nbp-school-proposals` | target repo |
| `DATA_PATH` | `dashboard-data.json` | filename in the repo |

## What the agent will NOT do

- It won't invent URLs or emails. If Claude can't verify it, the field stays null.
- It won't overwrite an existing real value (e.g. won't replace a confirmed FB URL with a worse one).
- It won't discover NEW clubs — only enriches the existing 30 in the data file. Adding new clubs is a separate task.
- It won't run on `excluded` clubs (e.g. PKMS, your existing client).

## Watching it work

After each run, check the latest commit at:
- https://github.com/nathangbingle/nbp-school-proposals/commits/main

The commit message lists every club + field that changed, e.g.:
```
Weekly enrichment: 4 clubs / 7 fields

cuthhs: +fb,activity
crest: +fb
hough: +email
ilms: +fb,ad
```
