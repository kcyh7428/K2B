---
tags: [test, fixture, calibration, washing-machine]
date: 2026-04-21
type: test-fixture
up: "[[../../plans/2026-04-21_washing-machine-ship-1]]"
---

# Washing Machine Classifier Calibration Corpus (v1.0)

25 rows covering the behavioral surface of the Commit 3 classifier. Each row is a user message + expected classifier output. The classifier is considered correct when ALL 25 rows pass simultaneously. Tuning the classifier means tuning the PROMPT, not the expected outputs.

Pairs with `calibration-expected.json` (machine-readable form consumed by `classify.test.sh`) and `calibration-expected.schema.json` (JSON Schema validator).

Anchor date for relative-date resolution in expected timestamps: **2026-04-01 (Wednesday)**. All "next Friday" / "tomorrow" / "yesterday" resolutions use this anchor.

## Categories

Rows are numbered and grouped by intent:
- **001-005**: clean contact / person / org captures (`keep=true`)
- **006-008**: decision captures (`keep=true, category=decision`)
- **009-011**: preference captures (`keep=true, category=preference`)
- **012-014**: forward-relative dates (`keep=true`, resolved timestamps)
- **015-016**: date contradictions (`needs_confirmation_reason=["date_mismatch"]`)
- **017-020**: question rejections (`keep=false, discard_reason="question"`)
- **021**: command rejection (`keep=false, discard_reason="command"`)
- **022-023**: tool-output / assistant-echo rejections (`keep=false, discard_reason="tool_echo"`)
- **024**: too-short rejection (`keep=false, discard_reason="too_short"`)
- **025**: multi-entity capture (`keep=true`, 2+ entities extracted from one message)

---

### Row 001 -- Clean contact (English + phone + org)

**Input**: `Andrew's new number is 9876 5432, works at Apex Capital.`

**Expected**: keep=true, category=fact, shelf=semantic. Entities: one `contact` with name="Andrew", phone="9876 5432", org="Apex Capital". `date_confidence` not required (no date in message).

### Row 002 -- Clean person with role + relationship

**Input**: `Just met Dr. Lisa Chen, pediatrician at HK Sanatorium. Very thorough.`

**Expected**: keep=true, category=fact, shelf=semantic. Entity: `person` name="Dr. Lisa Chen", role="pediatrician", org="HK Sanatorium".

### Row 003 -- Bilingual contact (Chinese + English)

**Input**: `My physiotherapist is 陳醫生 (Dr. Chan), tel 2567 1234, Central branch.`

**Expected**: keep=true. Entity: `contact` name="Dr. Chan" (EN) + "陳醫生" (ZH), role="physiotherapist", phone="2567 1234", location="Central branch".

### Row 004 -- Email-only contact

**Input**: `Add Jamie Liu to the vendor list: jamie@linearleads.com, sourcing contractor.`

**Expected**: keep=true. Entity: `contact` name="Jamie Liu", email="jamie@linearleads.com", role="sourcing contractor".

### Row 005 -- Org with context (not a person)

**Input**: `We're using Peak Talent for the VP Sales search. Retainer signed last week.`

**Expected**: keep=true, category=fact, shelf=semantic. Entity: `org` name="Peak Talent", relationship="recruiting vendor", context="VP Sales search".

---

### Row 006 -- Decision with reason

**Input**: `Decided to park the multi-agent hive mind spec. Too complex for current team; revisit Q3.`

**Expected**: keep=true, category=decision. Entity: `decision` subject="multi-agent hive mind", choice="parked", reason="too complex for current team; revisit Q3".

### Row 007 -- Short-form decision

**Input**: `Going with MiniMax-VL over Opus for image OCR. Cost + same quota bucket wins.`

**Expected**: keep=true, category=decision. Entity: `decision` subject="image OCR provider", choice="MiniMax-VL", reason="cost + same quota bucket".

### Row 008 -- Hiring decision

**Input**: `Offering Fiona the Head of People Ops role at HK$85k base + 12% target bonus.`

**Expected**: keep=true, category=decision. Entity: `decision` subject="Fiona hiring", choice="offer HoPO role at 85k + 12%".

---

### Row 009 -- Positive preference

**Input**: `I prefer Codex over MiniMax for adversarial review when it's available.`

**Expected**: keep=true, category=preference. Entity: `preference` trigger="adversarial review", rule="use Codex when available", reason=null.

### Row 010 -- Negative preference with reason

**Input**: `Don't send me emails after 10pm HK time -- I don't check them until morning anyway.`

**Expected**: keep=true, category=preference. Entity: `preference` trigger="outbound email", rule="not after 22:00 HKT", reason="not read until morning".

### Row 011 -- Always-rule preference

**Input**: `Always use the Telegram bot path for reminders, not the dashboard.`

**Expected**: keep=true, category=preference. Entity: `preference` trigger="reminder channel", rule="use Telegram bot, not dashboard".

---

### Row 012 -- Tomorrow (relative date)

**Input**: `Reminder: annual physical tomorrow at 9am with Dr. Wong.`

**Expected**: keep=true. Entity: `appointment` subject="annual physical", with="Dr. Wong", timestamp_iso="2026-04-02T09:00:00+08:00", date_confidence=0.95 (anchor 2026-04-01).

### Row 013 -- Next Friday (relative date)

**Input**: `Next Friday I'm in Macau for the SJM board meeting. Block the day.`

**Expected**: keep=true. Entity: `appointment` subject="SJM board meeting", location="Macau", timestamp_iso="2026-04-03T00:00:00+08:00" (next Friday from Wed 2026-04-01), date_confidence=0.9.

### Row 014 -- Explicit date (high confidence)

**Input**: `Schedule SJM quarterly review for 2026-05-15, 2-4pm, Conference Room A.`

**Expected**: keep=true. Entity: `appointment` subject="SJM quarterly review", timestamp_iso="2026-05-15T14:00:00+08:00", date_confidence=1.0.

---

### Row 015 -- OCR date contradiction (6-month rule)

**Input**: `[Image: business card for Andrew Lam, Tel 2345 6789, dated 2025-09-01]` (anchor: message arrived 2026-04-01)

**Expected**: keep=true BUT needs_confirmation_reason=["date_mismatch"], date_confidence=0.4. Entity candidate captured, pending-confirmation file written. Gate posts numbered-options reply to Telegram.

### Row 016 -- Ambiguous relative date

**Input**: `Met with her earlier about the hiring freeze.`

**Expected**: keep=true (contains relationship signal) BUT needs_confirmation_reason=["date_ambiguous"], date_confidence=0.5. Classifier asks for clarification before finalizing.

---

### Row 017 -- Question: who

**Input**: `What's my doctor's phone number?`

**Expected**: keep=false, discard_reason="question". No entity extraction. This is the 2026-04-21 bug case itself -- the classifier must NEVER store this as a fact.

### Row 018 -- Question: what

**Input**: `What time is the SJM board meeting?`

**Expected**: keep=false, discard_reason="question".

### Row 019 -- Question: how

**Input**: `How do I reach Andrew by WhatsApp again?`

**Expected**: keep=false, discard_reason="question".

### Row 020 -- Question without question mark (starts with "any chance")

**Input**: `Any chance you remember my physiotherapist's name`

**Expected**: keep=false, discard_reason="question". Classifier prompt must recognize "any chance you remember" / "do you know" / "can you tell me" as question markers even without a trailing "?".

---

### Row 021 -- Slash command

**Input**: `/tldr`

**Expected**: keep=false, discard_reason="command". Starts with "/", is a K2B slash command.

---

### Row 022 -- Tool output echo

**Input**: `[Tool: obsidian_simple_search] Found 3 results for "Dr Lo": wiki/people/person_Dr-Lo-Hak-Keung.md, Daily/2025-04-11.md, wiki/concepts/feature_washing-machine-memory.md`

**Expected**: keep=false, discard_reason="tool_echo". Starts with `[Tool:`.

### Row 023 -- Assistant turn echo

**Input**: `Here's a summary of your meeting with Andrew today: (1) Decided on retainer model, (2) Keith to send the MSA draft by Friday, (3) Andrew to introduce Peak Talent to the finance team.`

**Expected**: keep=false, discard_reason="assistant_turn" (the facts inside ARE legitimate but come from K2B's own output -- they should be captured at source during the original meeting, not re-ingested from the summary K2B wrote). Classifier prompt must distinguish this from `tool_echo` (rows 022): tool_echo = bracketed tool-output pattern; assistant_turn = natural-prose summary that looks like something K2B would say.

---

### Row 024 -- Too short

**Input**: `ok`

**Expected**: keep=false, discard_reason="too_short". Under 20 chars + no structured content.

---

### Row 025 -- Multi-entity (1 message, 2 facts)

**Input**: `Saw Lisa Chen today (pediatrician at HK Sanatorium, tel 2522 9876) and she recommended Dr. Mike Ho for orthopedics at St. Paul's.`

**Expected**: keep=true, category=fact, shelf=semantic. TWO entities:
1. `contact` name="Lisa Chen", role="pediatrician", org="HK Sanatorium", phone="2522 9876".
2. `contact` name="Dr. Mike Ho", role="orthopedics specialist", org="St. Paul's", referral_source="Lisa Chen".

---

## Usage

Commit 3's `tests/washing-machine/classify.test.sh` consumes this corpus via `calibration-expected.json`. Test runs: for each row, pipe input into `classify.sh`, compare output JSON to expected JSON, fail individually per row (CI shows which rows fail).

**Checkpoint 3 gate**: all 25 rows pass. Tuning policy: tune the prompt, not the expected outputs. If a row is genuinely wrong (the expected output doesn't match what a human would actually want), update BOTH this corpus file and `calibration-expected.json` in the same commit, with a one-line changelog entry below.

## Changelog

- **v1.0 (2026-04-21)**: initial 25-row corpus. Anchor date 2026-04-01 (Wednesday). Covers contacts / decisions / preferences / dates / questions / commands / tool-echoes / too-short / multi-entity.
