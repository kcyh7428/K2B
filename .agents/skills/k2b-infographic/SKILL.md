---
name: k2b-infographic
description: "Build controlled, on-brand infographics by compositing text-free AI illustrations under a crisp HTML/CSS frame, then headless-rendering to a PNG/PDF. Use when Keith wants a single visual aid (concept map, multi-panel diagram, executive infographic) with EXACT wording, EXACT brand colours, and a clean layout - situations where letting gpt-image-2 render text would garble the labels. Trigger phrases: \"visual aid\", \"infographic\", \"concept image\", \"diagram for the deck\", \"stunning visual\", \"build me a one-pager visual\", or any request to revise the wording on an AI-generated image."
---

# K2B Infographic Builder

## Live K2B Authority

- `AGENTS.md` is the instruction authority and `.agents/skills` is the only live skill root.
- Codex is the interactive commander; Kimi K2.7 is the background text worker.
- OpenAI-built diffs use Kimi review with no fallback; Kimi-built diffs use Codex review.
- Scheduled work must be a registered host job with an observable receipt; failures go to the Operations Console attention queue.
- Capture enters through the dashboard or a vault drop, never Telegram.
- Canonical memory is `K2B-Vault/System/memory`; read Codex sessions only when explicitly required and never read Claude state.

The controllable alternative to fully-AI-painted images.

Maintenance: `.agents/skills/k2b-infographic/SKILL.md` is the sole live copy.
Run `scripts/verify-codex-authority.sh` before review to catch live
authority or retired-routing drift.

## The pattern

`gpt-image-2` is brilliant at illustrations and terrible at typography. It paraphrases prompts, hallucinates words, and re-renders the same label differently every time. So the canonical method is:

1. **Generate text-free illustration panels** with `gpt-image-2` (one per content block, prompted explicitly with `No text, no words, no letters, no labels, no signage anywhere.`).
2. **Write a single HTML file** with the exact title, stage headers, captions, chips, flags, badges, brand colours and SJM logo laid out in CSS.
3. **Headless-render** the HTML to PNG (or PDF) via Chrome `--screenshot` (single image) or `--print-to-pdf` (multi-page).

The illustrations carry the warmth and the SJM premium feel; the HTML carries the words and the layout. Keith's words ship verbatim, never re-rolled by the model.

## When to use this vs [[k2b-media-generator]]

- `k2b-media-generator` is correct for: a standalone image with no critical text, a TTS clip, a transcription, or a multi-slide deck.
- **`k2b-infographic` is correct for:** a single visual where wording matters — title, labels, captions, badges, callouts. The closed-loop service-quality model for Rachel/the MD (2026-06-16) is the canonical example.

If Keith asks to "tweak the wording" on an already-generated image, that is the bright-line signal to switch to this skill rather than re-rolling the same gpt-image-2 prompt.

## Canonical brand palette (SJM Holdings)

Captured 2026-06-15 from sjmholdings.com header. Reuse on every SJM-facing visual.

```css
:root{
  --paper:#FBFAF7;        /* background */
  --ink:#16181A;          /* primary text */
  --green:#178B47;        /* SJM brand green (nav bar) */
  --green-ink:#0B3A1F;    /* deep green for dark panels */
  --green2:#2C7A4C;       /* mid green for hovers / arrows */
  --gold:#9E7F38;         /* SJM brand gold (logo + accent) */
  --gold-soft:#C2A869;    /* light gold for hero stats */
  --slate:#5C6168;        /* secondary text */
  --hair:#E5E1D8;         /* hairline borders */
  --paper2:#F4F1EA;       /* alt panel background */
  --serif:'Charter','Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  --sans:-apple-system,'Helvetica Neue','Inter',Arial,sans-serif;
}
```

The real SJM logo lives at `~/Projects/K2B-Vault/Assets/images/sjm_logo.png` (downloaded from `sjmholdings.com/resources/images/sjm_logo3.png`, 436×100 transparent PNG). On dark backgrounds, knock it out white with `filter:brightness(0) invert(1);`.

## Workflow

### 1. Plan the panels and the wording first

Before generating anything, agree the exact title, stage names, captions and any structured elements (badges, chips, KPIs, flags). The illustration step is cheap; the wording is the load-bearing part.

### 2. Generate text-free panels on the Mac Mini

The Mini holds the GPTsAPI key in its shell profile. Run the remote generation through the Mini's existing `zsh` login context; do not parse `~/.zshrc` by hand.

```bash
set -euo pipefail
mkdir -p /tmp/deckbuild/<slug>
file ~/Projects/K2B-Vault/Assets/images/sjm_logo.png | grep -q 'PNG image data' \
  || { echo "sjm_logo.png is missing or not a valid PNG" >&2; exit 1; }
cp -f ~/Projects/K2B-Vault/Assets/images/sjm_logo.png /tmp/deckbuild/<slug>/  # refresh the local logo on every build

PANEL_PATH="$(ssh macmini 'zsh -l' <<'REMOTE'
set -euo pipefail
: "${GPTSAPI_KEY:?GPTSAPI_KEY not loaded by Mini shell profile}"
cd "$HOME/Projects/K2B"
remote_dir="/tmp/deckbuild/<slug>"
mkdir -p "$remote_dir"
panel_path="$remote_dir/my-panel1.png"  # reruns reuse the same fetch path; version the slug if you want to keep variants

N="No text, no words, no letters, no labels, no signage anywhere."
P1="A warm clean editorial illustration. $N <scene description>. Emerald green and gold on a clean cream background. Simple flat hand-illustrated style."

./scripts/gptsapi-image.sh --prompt "$P1" --aspect-ratio 4:3 \
  --output "$panel_path" \
  --timeout 170 >/dev/null
printf '%s\n' "$panel_path"
REMOTE
)"
[ -n "${PANEL_PATH}" ] || { echo "remote panel path was empty" >&2; exit 1; }

scp "macmini:${PANEL_PATH}" /tmp/deckbuild/<slug>/
```

**Prompt template that consistently works** for SJM panels:

> "A warm clean editorial illustration. **No text, no words, no letters, no labels, no signage anywhere.** *[scene]*. Emerald green and gold on a clean light background. Simple flat hand-illustrated style."

The text-suppression line MUST be near the top of the prompt and worded redundantly — once is not enough. The model will still try to draw chart axes or signage if invited.

**Failure handling:** the old bash 3.2 `bad substitution` error path in `gptsapi-image.sh` was fixed in commit `c624393`, and the current script supports `--output`. If the script fails now, read the JSON diagnostic it prints on stderr and fix the named cause (missing key, rejected prompt, rate limit, API error, or network timeout). Retry once only for a clearly transient API or network failure; otherwise stop and report the diagnostic.

### 3. Write a single HTML file with the frame

One self-contained file under `/tmp/deckbuild/<slug>/`. Reference the downloaded panel PNGs by relative path (they sit beside the HTML), and reference `sjm_logo.png` likewise.

Use these conventions:
- 16:9 frame: `width:1600px; height:900px;` (renders crisp at native size).
- `<img class="cimg" src="..." style="object-fit:cover; object-position:center 32%;">` for cropping the panel cleanly inside a card.
- Card pattern: dark-green header bar with a gold-circle number + uppercase title, illustration, body copy, then an italic-serif caption at the foot.
- Arrows between cards: a small gold uppercase **label** (`Teaches the AI` / `Hand-off to Ops` / `Closes the loop`) above a green `→` glyph. Cheap, legible, on-brand.
- Footer: real SJM logo bottom-left, a single neutral line of takeaway bottom-right.

Avoid: empty decorative banners (the AI default), generic skyline strips, "salesy" italic titles (the 2026-06-16 take-1 said "Staff take pride…" which over-claimed). Keep the title and captions in the discussion-paper voice — neutral, observational.

### 4. Render to PNG

For a single 16:9 image:

```bash
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || { echo "Chrome not found at $CHROME; set CHROME to the browser binary" >&2; exit 1; }
"$CHROME" --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1600,900 \
  --screenshot="$PWD/infographic.png" "file://$PWD/infographic.html"
test -s "$PWD/infographic.png" || { echo "Chrome render failed: infographic.png missing/empty" >&2; exit 1; }
```

The HTML's own `.frame { background: var(--paper); }` rule paints the page, so no `--default-background-color` flag is needed (it varies across Chrome versions and recent builds reject the 8-digit hex form).

For a multi-slide PDF, use `--print-to-pdf` instead (covered in `k2b-media-generator` Presentation Decks section).

### 5. Deliver and preserve

```bash
cp infographic.png ~/Downloads/SJM_<slug>.png
cp infographic.png ~/Projects/K2B-Vault/Assets/images/$(date +%F)_image_sjm-<slug>.png
# preserve the editable source so future tweaks don't start from scratch
mkdir -p ~/Projects/K2B-Vault/Assets/decks/src
cp infographic.html ~/Projects/K2B-Vault/Assets/decks/src/$(date +%F)_image_sjm-<slug>.html
```

## Lessons captured from the closed-loop service-quality build (2026-06-16)

Working notes from the build with Keith — keep these in mind on every infographic:

- **The wording is the artefact.** Generate-the-image / regenerate-the-image cycles are wasted iteration when the issue is copy. Switch to HTML frame the moment Keith asks for any copy change.
- **Neutral over emotional.** Keith rejected "Staff take pride in the standard" as exaggerating; use observable language ("Staff self-correct"). Same for "Good service / Poor service" — became **Meets standard / Falls short** because we observe behaviour and timing, not service quality in the abstract.
- **Privacy boundary up top.** For surveillance-AI visuals, say what we DO and DON'T capture in the subtitle, not buried in body copy: "Observing behaviour and timing — never conversation".
- **Specific observable signals beat abstract categories.** "Table 42 — drink serve > 3 min after order" is honest and demonstrable; "Table 42 — drink delay" is vague. Always cite the measurement.
- **Closed-loop motif, not a giant return arrow.** A small ↻ glyph next to the final box plus the word "Closes the loop" in the copy is enough. A big curved arrow sweeping back across the image was rejected as clutter.
- **3-panel benchmark.** When the message is a journey/loop, the 3-card row (with stage 3 split into 3 small inner boxes) is the proven layout — clean, executive, no empty space.
- **Macau setting cues are subtle.** A glimpse of a Macau skyline in a panel-2 background is fine; a literal labelled "MACAU" skyline strip across the page is not.

## Canonical reference

The working example, source preserved for re-use as a template:

- Output: `K2B-Vault/Assets/images/2026-06-16_image_sjm-closed-loop-service-quality.png`
- Source: `K2B-Vault/Assets/decks/src/2026-06-16_image_sjm-closed-loop-service-quality.html`
- Panels: `K2B-Vault/Assets/images/2026-06-16_image_loop-panel{1-hr,2-floor,3-ops}.png`

Open the HTML source first when starting any new infographic — copy it as the skeleton, swap panels and copy, re-render. Do not start from a blank file.

The build directory at `/tmp/deckbuild/<slug>/` is left in place between iterations on purpose — Keith typically tweaks copy and re-renders multiple times. Vault + Downloads copies are the durable record, so a stale `/tmp` build is harmless and macOS clears `/tmp` on reboot. Only `rm -rf /tmp/deckbuild/<slug>` deliberately when starting a fully unrelated infographic and you want a clean state.

## Usage logging

Append one line to `~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv` using the same four-column TSV pattern used across K2B skills and referenced by `k2b-usage-tracker`: `date<TAB>skill<TAB>run_id<TAB>summary`.
