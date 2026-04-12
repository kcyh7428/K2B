# K2B Architecture Exploration Report

## Executive Summary

K2B is a **personal AI operating system** with 19+ specialized skills managing a content pipeline from daily work capture → vault organization → content publication. It's moderately complex (~1400 lines of TypeScript in k2b-remote, ~2500 lines across 19 skill files, 200+ scripted bash tools), with several fragility vectors that will break under load or change.

**Overall Health**: Functional but fragile. Tightly coupled skill dependencies, hardcoded paths, and manual deployment workflows create brittleness. Strong: clear CLAUDE.md documentation, consistent templates, git-based version control. Weak: distributed shell scripts, environment-dependent operations, lack of integration tests.

---

## Part 1: Architecture Overview

### 1.1 Core Design

K2B replaces KIRA (AirTable + Supabase + n8n) with a simpler local-first architecture:

```
Telegram (Remote UI)
    ↓
Claude Code (Local Agent)
    ├─→ Skills (19 K2B-specific tools)
    ├─→ Obsidian Vault (Markdown knowledge base)
    ├─→ MCP Servers (Gmail, GCal, Airtable, MiniMax, YouTube, Perplexity)
    ├─→ Bash Scripts (YouTube, LinkedIn, MiniMax, email, scheduling)
    └─→ Database & Persistence (SQLite on Mac Mini)
    
k2b-remote (Telegram Polling Bot)
    ├─→ Runs 24/7 on Mac Mini via pm2
    └─→ Calls Claude Code sessions for actual work
```

**Sync Model**:
- MacBook code (K2B/.claude/skills, scripts): Manual rsync + rebuild required
- MacBook vault: Auto-synced via Syncthing to Mac Mini
- Mac Mini code: Deployed via rsync, built locally, pm2 restarted

### 1.2 Current Skill Stack (19 total)

**Core Capture (6 skills)**:
- `k2b-daily-capture` (57 lines) — Generate daily notes from calendar
- `k2b-meeting-processor` (74 lines) — Extract from Fireflies transcripts
- `k2b-tldr` (81 lines) — Summarize conversations
- `k2b-error` (45 lines) — Log failures with root cause (now consolidated into `k2b-feedback`)
- `k2b-learn` (49 lines) — Capture learnings with deduplication (now consolidated into `k2b-feedback`)
- `k2b-request` (44 lines) — Log feature requests (now consolidated into `k2b-feedback`)

**Vault & Organization (3 skills)**:
- `k2b-vault-writer` (223 lines) — Create/update notes with frontmatter & wikilinks
- `k2b-review` (97 lines) — Process review queue items (promote/archive/delete/revise)
- `k2b-insight-extractor` (87 lines) — Search vault, surface patterns, synthesize insights

**Content Pipeline (2 skills)**:
- `k2b-linkedin` (313 lines) — Draft/publish posts with MiniMax image generation
- `k2b-youtube-capture` (249 lines) — Poll playlists, process videos, recommend content

**Intelligence & Media (3 skills)**:
- `k2b-autoresearch` (230 lines) — Karpathy self-improvement loop on skills
- `k2b-research` (205 lines) — On-demand research agent (internal audit + external scanning)
- `k2b-media-generator` (171 lines) — `/media` command for images, speech, transcription, video, music

**Automation & Utility (4 skills)**:
- `k2b-scheduler` (132 lines) — Persistent scheduled tasks via MCP
- `k2b-usage-tracker` (109 lines) — Track skill invocations, fire usage-based triggers
- `k2b-email` (212 lines) — Gmail operations (read, draft, search, triage)
- `k2b-improve` (39 lines) — Review self-improvement logs
- `obsidian-markdown` (196 lines) — Wikilinks, embeds, callouts, properties (now merged into `k2b-vault-writer` as a reference file)

**Total**: ~2300 lines of skill documentation (non-code). Each skill is a markdown file with frontmatter, workflow sections, and inline examples.

### 1.3 Persistence & Remote Operation

**Scheduled Tasks** (Persistent):
- `weekly-vault-health` — Audit vault structure, orphaned notes, MOC alignment
- `weekly-external-research` — Perplexity + YouTube + web scanning
- `daily-review-check` — Process review-action flagged items
- `friday-self-improvement` — Run `/improve review`, synthesize learnings
- Plus usage-based triggers (e.g., after 10 meeting transcripts, auto-run insight extraction)

**Remote Architecture** (k2b-remote):
- Telegram bot polling via `@TelegramClient`
- Creates fresh Claude Code sessions for each message
- SQLite store for session memory + health state
- pm2 process management + launchd auto-start + daily health heartbeat
- Configured via `.env` on Mac Mini

---

## Part 2: Complexity Analysis

### 2.1 Lines of Code

| Component | Files | Lines | Notes |
|-----------|-------|-------|-------|
| k2b-remote (TypeScript) | 15 | 1406 | Bot, agent, scheduler, memory, health, media, voice, logger, db config |
| K2B Skills (Markdown) | 19 | ~2300 | Skills only; excludes vault, scripts, config |
| Bash Scripts | 12 | ~4500+ | YouTube, LinkedIn, MiniMax, email, scheduling, auth, polling |
| Settings & Config | 2 | 200+ | `.claude/settings.local.json` (204 permissions!), `.mcp.json` (1 server) |
| Vault Structure | — | ~20 files | Templates, MOCs, context docs, processed logs |
| **Total** | — | ~8500+ | Distributed across code, scripts, skills, vault |

### 2.2 Key Fragility Points

#### A. Distributed Shell Scripts (Highest Risk)

**12 shell scripts** manage critical workflows:
- `yt-playlist-poll.sh` — Polls YouTube playlists, detects new videos
- `yt-playlist-add.sh`, `yt-playlist-remove.sh` — Modifies playlists
- `yt-search.sh` — YouTube search + recommendation
- `yt-auth.sh` — OAuth flow, token refresh
- `linkedin-publish.sh`, `linkedin-status.sh` — LinkedIn API integration
- `minimax-*.sh` (4 files) — Image, speech, transcription, common utilities
- `check-usage-triggers.sh` — Fires scheduled tasks at session start

**Risks**:
1. **Hidden dependencies**: Scripts call `yt-dlp`, `jq`, Python, `gws`, `curl`, external APIs
2. **Hardcoded paths**: `/Users/keithmbpm2`, `~/.config/k2b/`, `~/Projects/K2B-Vault/`
3. **Token management**: OAuth tokens stored as files (`~/.linkedin_token`, `~/.config/k2b/youtube-token.json`)
4. **Error handling**: Limited validation, silent failures (redirects to `/dev/null`)
5. **Version drift**: No way to know if a script broke without running it
6. **No tests**: Zero integration tests, validation only via manual runs

**Example fragility**:
```bash
# From yt-playlist-poll.sh
PROCESSED_FILE="${2:-.}"
# If $2 is missing, silently falls back to ".", creates "." file, corrupts vault
```

#### B. Obsidian Vault Path Dependencies

**Multiple hardcoded paths** throughout skills & scripts:
- `/Users/keithmbpm2/Projects/K2B-Vault/Inbox/` — new captures land here
- `/Users/keithmbpm2/Projects/K2B-Vault/Notes/Content-Ideas/` — adopted ideas
- `/Users/keithmbpm2/Projects/K2B-Vault/Assets/images/`, `audio/`, `video/` — generated media

**Risks**:
1. **Moving vault breaks everything**: Renaming vault folder = all skills + scripts fail silently
2. **Path assumptions in Glob**: `k2b-vault-writer` globs `Notes/People/person_*.md` — fails if naming convention changes
3. **No environment vars**: Paths are inline, hard to parameterize per environment (MacBook vs Mac Mini differ: `/Users/keithmbpm2/` vs `/Users/fastshower/`)
4. **Note lifecycle coupling**: `k2b-review` assumes specific file naming (e.g., `content_*.md` for ideas) — inconsistencies cause promotion failures

#### C. MCP Server & API Dependency Chain

**7+ external integrations**:
- Gmail MCP — read, search, draft (no send, no batch delete)
- Google Calendar MCP
- Airtable MCP (keith, talentsignals bases)
- YouTube Data API v3 (OAuth, playlist operations)
- LinkedIn API (OAuth, post publishing)
- MiniMax API (image, speech, video, music generation)
- Perplexity API (research agent)
- Fireflies (meeting transcripts, manual upload)

**Risks**:
1. **Cascading failures**: If MiniMax API fails, image gen breaks; if YouTube API quota hit, playlist polling stalls
2. **Rate limits**: YouTube Data API (quota units), LinkedIn API (throttling), MiniMax (daily limits)
3. **Token rotation**: Multiple OAuth flows with manual refresh (YouTube token at `~/.config/k2b/youtube-token.json`, LinkedIn at `~/.linkedin_token`)
4. **No fallbacks**: If YouTube caption fetch fails, autoresearch tries OpenAI Whisper; if that fails, metadata-only (silent degradation)
5. **Environment variables**: `MINIMAX_API_KEY`, `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`, LinkedIn secrets — all must be set correctly on Mac Mini

#### D. Skill Interdependencies

**Complex call chains**:
- `k2b-linkedin` → calls `k2b-media-generator` for image gen → calls MiniMax API
- `k2b-youtube-capture` → calls YouTube API → calls `k2b-vault-writer` to save notes
- `k2b-research` → calls `k2b-insight-extractor` → globs vault → calls Perplexity for synthesis
- `k2b-autoresearch` → modifies `k2b-vault-writer` SKILL.md → runs eval.json → commits to git

**Risks**:
1. **Silent failures in chains**: If `k2b-media-generator` fails mid-skill, k2b-linkedin still commits draft (image missing)
2. **Circular reasoning**: `k2b-autoresearch` improves skills, but skills call each other; change in one cascades
3. **Memory overhead**: Each skill loads context (vault structure, templates, examples); 3+ skills in a session = context bloat
4. **Skill versioning**: No way to pin skill versions; old saved SKILL.md evals may not apply if skill rewrites happen

#### E. Deployment & Sync Complexity

**3-system setup**: MacBook dev, Mac Mini always-on, K2B-Vault synced

**Current manual workflow**:
```
1. Edit /Users/keithmbpm2/Projects/K2B/.claude/skills/k2b-*.md locally
2. rsync -av ~/Projects/K2B/k2b-remote/ macmini:~/Projects/K2B/k2b-remote/
3. ssh macmini "cd ~/Projects/K2B/k2b-remote && npm run build && pm2 restart k2b-remote"
4. rsync -av ~/Projects/K2B/.claude/skills/ macmini:~/Projects/K2B/.claude/skills/
5. Vault syncs automatically via Syncthing (eventually consistent)
```

**Risks**:
1. **Out-of-sync code**: If sync fails, Mac Mini runs stale skills; operator doesn't know until a skill fails
2. **Build failures silently ignored**: `npm run build` fails but pm2 restart runs old dist/
3. **Partial deployments**: Skill updated, scripts not; or vice versa
4. **No deployment tracking**: No way to know what version is running on Mac Mini
5. **Eventual consistency**: Vault changes lag by seconds/minutes on Mac Mini; scheduled tasks may see stale data

#### F. Permission Sprawl (Settings Config)

**204 explicit allow permissions** in `.claude/settings.local.json` — signs of:
- Iterative feature additions without cleanup
- Per-session hacks that became permanent (e.g., `Bash(mv Notes/Content-Ideas/idea_*.md ...)`)
- Lack of general permission patterns (e.g., `Bash(gws *)` instead of specific commands)

**Risks**:
1. **Hard to audit**: Can't quickly see what K2B is actually allowed to do
2. **Hidden assumptions**: Old one-off commands (like moving old ideas) still in allow list
3. **Security drift**: If permission is too broad, no protection; if too narrow, skill breaks silently
4. **No cleanup**: Old tasks removed from code but permissions remain

### 2.3 Complexity Layers

```
Layer 1 (Closest to User):
  CLAUDE.md (200 lines) — Rules, slash commands, vault structure

Layer 2 (Skills):
  19 skill files (~2300 lines) — Workflows, paths, API calls

Layer 3 (Integrations):
  12 shell scripts — YouTube, LinkedIn, MiniMax, scheduling

Layer 4 (Runtime):
  k2b-remote (1406 lines TS) — Bot, scheduler, memory, health

Layer 5 (External):
  7+ MCP/API services — Gmail, YouTube, LinkedIn, MiniMax, etc.
```

**Problem**: Each layer can fail independently. Layer 2 (skills) assumes Layer 3 (scripts) works. Layer 3 assumes Layer 4 (services) are available. No circuit breakers or graceful degradation.

---

## Part 3: What Works Well

### 3.1 Clear Intent & Design

- **CLAUDE.md** (200 lines) is a single source of truth for K2B's purpose, rules, and slash commands
- **K2B_ARCHITECTURE.md** documents phases, decision rationale, and build instructions
- **Skill frontmatter** includes name, description, when to trigger (helps operator know what to call)

### 3.2 Consistent Templating

- **Vault uses YAML frontmatter**: `tags`, `date`, `type`, `origin`, `up:` (parent MOC)
- **File naming conventions** are explicit (e.g., `person_Firstname-Lastname.md`, `content_slug.md`)
- **MOCs (Maps of Content)** link related notes; every note has `up:` pointing to parent
- **Templates in `Templates/`**: Consistent structure for person, project, meeting, insight notes

### 3.3 Git-Based Version Control

- K2B project is a git repo
- Skills tracked in git, changes committable
- `k2b-autoresearch` uses "commit before test" pattern for skill iterations
- `eval/eval.json` tracks test cases per skill
- Can roll back changes to specific skills or entire codebase

### 3.4 Emphasis on Self-Improvement

- **Explicit learning capture**: `/learn` command for corrections, preferences
- **Autoresearch loop**: Karpathy pattern for iterative skill enhancement
- **eval/eval.json**: Binary assertions per skill for measurable quality
- **Usage tracking**: `/usage` command logs skill invocations, fires automation triggers
- **Self-reflection**: Weekly `/improve review` synthesizes patterns

### 3.5 Progressive Disclosure of Tools

- **Skill-based access**: 19 specialized skills, not one monolithic agent
- **MCP wrapping**: Skills abstract MCP complexity (Gmail, YouTube API, etc.)
- **Staged capability**: Phase 1 (vault) → Phase 2 (remote) → Phase 3+ (content, video, music)

---

## Part 4: Pain Points That Will Emerge at Scale

### 4.1 When Keith's Work Volume Increases

- **Vault orphan growth**: New notes created faster than MOCs updated → graph degrades
- **Skill context saturation**: Each skill reads full vault search results → context window filled by 3-4 concurrent tasks
- **Scheduled task collisions**: If vault-health and review-check run simultaneously, both glob the same vault → contention

### 4.2 When Scripts Accumulate

- **YouTube script brittleness**: If `yt-dlp` is missing on Mac Mini, silent failures; `check-usage-triggers.sh` calls it without validation
- **Path bugs**: If vault is moved, all scripts fail; if scripts are rsync'd to Mac Mini without updating `.env`, they run against wrong vault
- **Token expiry**: LinkedIn & YouTube tokens expire; when renewal fails, skills hang

### 4.3 When External APIs Change

- **YouTube Data API v3 → v4 migration**: All YouTube scripts break; LinkedIn API updates endpoint structure
- **MiniMax quota overflow**: If Keith publishes more posts, image gen hits daily limit
- **Fireflies discontinuation**: No fallback for transcript source

### 4.4 When Deployment Accelerates

- **Manual rsync not scalable**: 12 scripts + 19 skills + config = too many files to manually sync
- **Build failures undetected**: `npm run build` failures on Mac Mini not visible to operator
- **Circular sync**: Changes to Mac Mini vault (from Telegram bot output) not reflected on MacBook

---

## Part 5: Summary Table

| Dimension | Status | Risk | Notes |
|-----------|--------|------|-------|
| **Documentation** | Excellent | Low | CLAUDE.md, architecture doc, skill descriptions clear |
| **Vault Structure** | Healthy | Low | Flat structure, MOCs, consistent templates |
| **Skill Design** | Good | Medium | 19 skills, clear separation, but interdependent |
| **Shell Scripts** | Fragile | High | 12 distributed scripts, limited error handling, hidden deps |
| **Deployment** | Manual | High | rsync + build + restart; no deployment tracking |
| **Path Hardcoding** | Problematic | Medium | Multiple hardcoded paths, no env var abstraction |
| **API Integration** | Functional | Medium | 7+ services, rate limits, token management ad-hoc |
| **Testing** | Minimal | High | 6 eval.json files (partial coverage), no integration tests |
| **Permission Config** | Overgrown | Low | 204 allow rules, hard to audit, but functional |
| **Skill Interdependencies** | Complex | Medium | Chains (linkedin→media→minimax), no circuit breakers |

---

## Part 6: Fragility Assessment by Scenario

### Scenario A: Move Vault to Different Path
**Current state**: Skills + scripts hardcode paths → **BREAKS EVERYTHING**
- All Glob operations fail
- All script calls fail
- No operator feedback until skill is invoked

### Scenario B: YouTube API Quota Exceeded
**Current state**: `yt-playlist-poll.sh` fails silently, `/youtube` command hangs
- No fallback, no circuit breaker
- k2b-youtube-capture skill hangs waiting for script output
- Recommendation engine stops working

### Scenario C: Mac Mini Network Connectivity Lost
**Current state**: Telegram bot disconnects, but k2b-remote process keeps running
- Eventual Syncthing sync lag means MacBook has stale vault state
- Operator doesn't know vault changes didn't sync

### Scenario D: Deploy New Skill to Mac Mini
**Current state**: Manual rsync, no verification that deployment succeeded
- If rsync fails mid-transfer, Mac Mini has partial skill
- If `npm run build` fails, old dist/ still runs
- Operator may not notice for hours

### Scenario E: Fireflies API Deprecates (2027?)
**Current state**: `k2b-meeting-processor` assumes Fireflies is available
- No fallback transcript source
- Meeting notes stop working
- Requires code change, redeployment

---

## Part 7: Recommended Improvements (Ordered by Impact)

### High Priority (Address Fragility)

1. **Centralize Path Configuration** (1 hour)
   - Create `.env` with `VAULT_PATH`, `SCRIPTS_PATH`, `ASSETS_PATH`
   - Update all skills + scripts to source `.env` or read from config file
   - Test on both MacBook and Mac Mini

2. **Script Validation Suite** (3 hours)
   - Create `scripts/validate-all.sh` that checks:
     - All required binaries exist (`yt-dlp`, `jq`, `python3`, `gws`)
     - All hardcoded paths are writable
     - All API tokens are valid (curl --head to service)
     - All vault paths are accessible
   - Run on session start, report failures to operator

3. **Deployment Verification** (2 hours)
   - After `npm run build`, run `npm test` (add basic tests)
   - After rsync, checksum key files on both machines, report mismatches
   - Add `deployment-log.txt` with timestamps + hash + operator
   - Can then query `git log` to know what version is deployed

4. **Skill Eval Coverage** (4 hours)
   - 19 skills, only 6 have eval.json (k2b-daily-capture, k2b-linkedin, k2b-vault-writer, k2b-tldr, k2b-meeting-processor, k2b-insight-extractor)
   - Add eval.json to remaining 13 skills (minimal: 3-5 binary assertions per skill)
   - Run all evals in CI/CD check before deployment

### Medium Priority (Reduce Brittleness)

5. **Vault Sync Monitoring** (2 hours)
   - Track Syncthing last-sync timestamp
   - Alert if Mac Mini vault is >5 minutes stale
   - Before running scheduled tasks, check sync recency

6. **API Circuit Breakers** (3 hours)
   - Wrap YouTube, LinkedIn, MiniMax calls in try-catch + fallback
   - If quota hit, notify operator + skip task (don't hang)
   - Add retry logic with exponential backoff (3 attempts max)

7. **Permission Audit** (1 hour)
   - Review 204 allow rules, remove old one-off commands
   - Group related permissions (e.g., `Bash(gws gmail:*)` instead of 10 specific rules)
   - Document why each broad permission is needed
   - Reduce to ~100 rules

### Low Priority (Nice to Have)

8. **Integration Tests** (6 hours)
   - Test full workflows: create daily note → extract insight → draft LinkedIn post
   - Mock external APIs, validate file creation + wikilinks
   - Add to CI/CD

9. **Deployment Automation** (4 hours)
   - Create `deploy.sh` that does: git commit + rsync + build + pm2 restart
   - Run pre-deployment validation (see #2)
   - Rollback on failure

10. **Skill Versioning** (3 hours)
    - Add `version: 1.2.3` to skill frontmatter
    - Track eval.json results by version
    - Allow pinning autoresearch to specific version

---

## Conclusion

K2B is a **well-designed system with poor operational hardening**. The architecture is sound (local-first, skill-based, persistent scheduling), the documentation is excellent (CLAUDE.md, architecture docs), but the implementation is fragile (distributed scripts, hardcoded paths, manual deployment, limited testing).

**For current light use** (~1-2 skill invocations per session), it's fine. **At 10+ daily tasks, scheduled runs, or scale-up** (more content, more playlists, more API calls), failures will emerge that cascade:
- A broken script blocks an entire pipeline
- A path mismatch silently corrupts output
- A deployment lag causes stale vault reads

**Next steps**: Start with #1 (centralize paths) and #2 (validation suite) to reduce operational risk. Then add skill eval coverage (#4) to catch regressions early.
