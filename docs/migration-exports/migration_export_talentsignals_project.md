# Migration Export: TalentSignals Claude Project

## Project Summary
- **Name**: talentsignals-build
- **Domain**: talentsignals
- **Status**: active
- **Description**: The primary Claude Project used to design, build, debug, and document the TalentSignals recruitment intelligence platform — from initial client builds (Bryan, Sylvia, Chris) through productization. Contains all architectural decisions, AI prompt engineering, workflow debugging, Airtable schema design, and product documentation across Signal Monitoring, R2 (Rank & Reach), and early Reverse Recruiter work.

---

## Key Decisions

### Architecture & System Design

- **Decision**: Adopt a 3-Tier signal-agnostic architecture (Signal Detection → Opportunity Intelligence → Contact Execution)
  - **Date**: Aug–Sep 2025
  - **Rationale**: Needed a scalable framework that could accept unlimited new signal types without disrupting existing pipelines. Chris's dual-automation build proved the concept; the 3-tier abstraction made it productizable.
  - **Alternatives considered**: Single monolithic workflow per client (rejected — couldn't scale); per-signal separate systems (rejected — too much duplication).

- **Decision**: Two-stage AI analysis to prevent hallucination (Clay extracts facts first, n8n/OpenAI makes strategic decisions second)
  - **Date**: Aug 2025 (emerged during Bryan/Chris builds)
  - **Rationale**: Mixing data extraction and strategic reasoning in a single prompt caused AI to override correct mathematical calculations with intuitive reasoning. Separating concerns gave 85%+ accuracy on job change detection.
  - **Alternatives considered**: Single-prompt approach (rejected — AI would "second-guess" its own math, especially on cross-year date calculations).

- **Decision**: Separate Claygent 1 (timeframe gatekeeping + employment pattern analysis) from Claygent 2 (change type classification + business logic)
  - **Date**: Sep 2025
  - **Rationale**: AI was overriding correct date math with intuitive reasoning. Field name "role_change" was ambiguous (did a change occur? vs should we process?). Renamed to `proceed_to_stage2` with supporting `gate_status` field. Claygent 1 became the authoritative timeframe gatekeeper; Claygent 2 could not override its decisions.
  - **Alternatives considered**: Single Claygent doing everything (rejected — date calculation errors persisted); adding validation layers to single prompt (rejected — added complexity without fixing root cause).

- **Decision**: Use n8n Queue Mode for all client instances
  - **Date**: Sep 2025
  - **Rationale**: Better handles high-volume contact monitoring workflows and concurrent processing. Required for production-grade reliability.
  - **Alternatives considered**: Standard n8n mode (rejected — couldn't handle concurrent webhook processing at scale).

### Product Design

- **Decision**: R2 (Rank & Reach) as standalone product, not extension of Signal Monitoring
  - **Date**: Oct 2025
  - **Rationale**: Original "Candidate Matching" design was too complex with 4-stage consultant workflows, deep profiling, and multi-checkpoint HITL. R2 simplified to 3 stages: job intake → candidate matching/scoring → outreach generation. Standalone product serves different buyer need.
  - **Alternatives considered**: 4-stage consultant workflow (rejected — too complicated); bolt-on to Signal Monitoring (rejected — different user persona and workflow).

- **Decision**: Replace Requirements JSON blob with simple Airtable fields in R2 Job Requests table
  - **Date**: Oct 2025
  - **Rationale**: Consultants need human-readable fields, not JSON blobs. Nine weight columns consolidated into one multiline "Weights (template)" field. Consultant-friendly design over technical purity.
  - **Alternatives considered**: Separate Requirements Items table (rejected for MVP — overkill); JSON blob approach (rejected — not consultant-friendly).

- **Decision**: Remove versioning workflow from R2 MVP (eliminated Requirements Versions table)
  - **Date**: Oct 2025
  - **Rationale**: Added complexity without immediate value. Requirements Hash field provides deterministic caching and audit trail without full version management.
  - **Alternatives considered**: Full versioning (deferred — not needed for MVP).

- **Decision**: Single-pass 0-100 scoring in R2 (not multi-stage)
  - **Date**: Oct 2025
  - **Rationale**: Simpler, faster, easier for consultants to understand. 9 scoring dimensions with configurable weights provide sufficient granularity.
  - **Alternatives considered**: Multi-stage progressive scoring (rejected — over-engineered for current needs).

### Data Architecture

- **Decision**: Transition Dynamic Feedback Filtering from JSON blob rules to individual Airtable records (Client_Filter_Rules table)
  - **Date**: Nov 2025
  - **Rationale**: Individual records allow visual management and direct editing by users. JSON blob required technical intervention for any rule change.
  - **Alternatives considered**: Continue with JSON blob (rejected — poor UX for non-technical users).

- **Decision**: Rule consolidation over proliferation (~8-10 rules vs 40+ fragmented rules)
  - **Date**: Nov 2025
  - **Rationale**: Multiple companies flagged as "organization too large" should consolidate into a single Company Size rule with 501+ threshold, not individual company exclusions. Comma-separated values in Match Pattern field for Keyword rules (one rule per category, not per keyword).
  - **Alternatives considered**: One rule per feedback item (rejected — unmaintainable at scale; would create 40+ rules from simple feedback patterns).

- **Decision**: Never invite external clients to internal TalentSignals Airtable workspace — use Base Transfer workspace
  - **Date**: Dec 2025 (Juan onboarding session)
  - **Rationale**: Adding clients via share link triggers additional subscription seat charges. Free Base Transfer workspace avoids this entirely.
  - **Alternatives considered**: Shared workspace with client seats (rejected — cost).

- **Decision**: n8n must be configured first, then Airtable, then Clay (setup sequence)
  - **Date**: Dec 2025
  - **Rationale**: n8n generates webhook endpoints that Airtable and Clay need. Reverse order creates broken references.
  - **Alternatives considered**: Any-order setup (rejected — creates configuration errors).

### AI Prompt Engineering

- **Decision**: Add mandatory "STEP 0" explicit date extraction before any analysis in Claygent 1
  - **Date**: Sep 2025
  - **Rationale**: AI consistently failed cross-year date calculations (e.g., treating July 2024 → Sep 2025 as "2 months" instead of 14 months). Forcing explicit year-month extraction with step-by-step math display eliminated the error class.
  - **Alternatives considered**: Adding more examples (insufficient); structured date validation post-hoc (too late in pipeline).

- **Decision**: Two-stage job posting qualification (Stage 1: hard requirements pass/fail, Stage 2: quality scoring)
  - **Date**: Aug 2025 (Chris job posting monitoring)
  - **Rationale**: Initial single-stage approach let unqualified jobs (e.g., "Project Coordinator" at entry level) pass through to scoring. Stage 1 binary gates on management level, title inclusion, experience level, exclusion keywords, geography, and salary eliminate unqualified jobs before any scoring occurs.
  - **Alternatives considered**: Single-stage scoring with high threshold (rejected — allowed edge cases through).

- **Decision**: Outreach emails for senior candidates should be 150-200 words with conversationally integrated questions (not bulleted)
  - **Date**: Oct 2025
  - **Rationale**: Research showed 36% higher response rates for shorter emails; bullet-listed questions reduce response rates with senior/passive candidates. Subject lines under 60 characters with personalization get 40%+ open rates.
  - **Alternatives considered**: Longer emails with formal question lists (rejected by research data).

- **Decision**: Rename ambiguous field "role_change" to "proceed_to_stage2" with supporting "gate_status" field
  - **Date**: Sep-Oct 2025
  - **Rationale**: "role_change" could mean "did a change occur?" or "should we process this?" — causing downstream AI misinterpretation. Explicit naming eliminated the ambiguity.
  - **Alternatives considered**: Adding documentation to existing field (rejected — AI doesn't reliably read field descriptions during inference).

### Go-to-Market & Operations

- **Decision**: Distribute through BenAI partner network as primary GTM channel
  - **Date**: 2024-2025
  - **Rationale**: Andrew's existing relationships in Ben Van Sprundel's AI automation community provide warm deal flow. Keith holds Chief AI Officer role within BenAI network.
  - **Alternatives considered**: Direct sales (possible but slower); marketplace listing (too early).

- **Decision**: Transition client support from Keith to Juan Arroyave
  - **Date**: Dec 2025
  - **Rationale**: Better timezone alignment with Andrew; frees Keith to focus on product design and Reverse Recruiter development. Escalation path: Juan → Keith (technical) / Andrew (client relationship).
  - **Alternatives considered**: Keith continues support (rejected — blocks product development); hiring external support (premature).

- **Decision**: Per-consultant monthly subscription pricing model
  - **Date**: 2025
  - **Rationale**: Aligns cost with client value; scales with usage.
  - **Alternatives considered**: Per-signal pricing, flat fee (specific alternatives not documented).

- **Decision**: Traffic light customization framework (Green/Yellow/Red) for sales conversations
  - **Date**: Sep 2025
  - **Rationale**: Sales team needs instant responses to customization requests. Green = easy adjustments same-day, Yellow = needs evaluation 2-3 weeks, Red = scope creep / custom pricing. 18 records across 6 customization areas.
  - **Alternatives considered**: Ad-hoc quoting (rejected — inconsistent; slows sales cycle).

---

## People Involved

- **Name**: Keith Cheung
  - **Organization**: recruitsystems.ai / TalentSignals
  - **Role**: CTO, sole technical architect
  - **Relationship**: Owner/builder
  - **Context**: Owns all technical architecture, AI prompt engineering, workflow design, product decisions. Background: RPO delivery leadership at Korn Ferry, Head of Sales at Hays, Head of Talent Acquisition for Sheraton and St. Regis in Macao (pre-opening, 4,000+ placements in 12 months). 15+ years digital transformation. Founded Agency at Scale. Chief AI Officer within BenAI partner network.

- **Name**: Andrew (surname not documented)
  - **Organization**: recruitsystems.ai / TalentSignals
  - **Role**: Business partner / Sales
  - **Relationship**: Co-founder/partner
  - **Context**: Owns sales, client relationships, commercial decisions, Pipedrive CRM pipeline, client onboarding calls (product training). NOT deeply technical. Clean division: Andrew owns client relationship, Keith owns product and technology. No formal title documented in conversations.

- **Name**: Juan Arroyave
  - **Organization**: recruitsystems.ai / TalentSignals
  - **Role**: Client implementations & technical support
  - **Relationship**: Team member (onboarded Dec 2025)
  - **Context**: Taking over client setup and support from Keith. Better timezone alignment with Andrew. Escalation path: self-resolves access/config → Keith for workflow logic → Andrew for client relationships. Onboarded via detailed Fireflies-recorded session Dec 5, 2025.

- **Name**: Chris Wessell
  - **Organization**: Recruitment agency (Albany, NY area)
  - **Role**: First production client / end user
  - **Relationship**: Client
  - **Context**: Active end user of Signal Monitoring since mid-2024. His dual-automation build (job change monitoring + job posting monitoring) became the foundation for productization. Provided critical feedback including concerns about over-filtering in the Dynamic Feedback system. Targets Albany metro executive-level positions (CEO, CFO, COO, CIO, CTO, President, VP, Director, Manager, Controller). Excludes retail, restaurant, hospitality. Uses CATS ATS as output destination. Production Airtable base: `appBoRrPiiimgVFBk`.

- **Name**: Bryan
  - **Organization**: Not documented
  - **Role**: Original client
  - **Relationship**: Client (first build)
  - **Context**: First implementation — single-purpose candidate monitoring system for vacancy detection. 500,000+ profile monitoring pool. His system provided the pattern for job change detection that became Tier 1 of the 3-tier architecture. 10% placement fee model vs industry standard 20-25%.

- **Name**: Sylvia
  - **Organization**: Not documented
  - **Role**: Client
  - **Relationship**: Client
  - **Context**: Job search automation for senior executives (CTO, VP, Director level) in specialized markets (Middle East tech sector). 4-stage pipeline: onboarding → configuration → weekly discovery → report generation. Her system provided the pattern for job board scraping and search configuration that became the Job Posting Monitoring component. Airtable base: `apprLH50n9EDFFgKK`.

- **Name**: Ben Van Sprundel
  - **Organization**: BenAI
  - **Role**: Partner network leader
  - **Relationship**: External partner
  - **Context**: Runs the BenAI AI automation community/partner network that provides deal flow for TalentSignals. Keith holds Chief AI Officer role within this network.

- **Name**: Jerome
  - **Organization**: Client
  - **Role**: Client (setup in progress)
  - **Relationship**: Client
  - **Context**: Mentioned in Juan onboarding session — needs to resend Hostinger invitation. Setup not yet complete.

- **Name**: Eric
  - **Organization**: Client
  - **Role**: Client (setup in progress)
  - **Relationship**: Client
  - **Context**: Mentioned in Juan onboarding session as "appears ready" — suggested as Juan's first practice setup.

---

## Active Action Items

- [ ] Complete R2 outreach generation component — Keith — no deadline set
- [ ] Build R2 export workflow to Clay → Instantly for campaign execution — Keith
- [ ] Branch 3 of Dynamic Feedback Filtering: positive reinforcement score boosting for preferred job types — Keith
- [ ] Reverse Recruiter product design — Keith — early stage
- [ ] Add screenshots to Client Automation Setup SOP document — Keith
- [ ] Content channel development (YouTube/LinkedIn) leveraging practitioner positioning — Keith
- [ ] Jerome: resend Hostinger invitation — Juan/Andrew
- [ ] Eric: complete full setup as Juan's first practice client — Juan
- [ ] Complete R2 job parsing workflow debugging (AI agent node architecture correction) — Keith
- [ ] Migrate Talent Signals v1.1 (appstrC3fpHDPloCU) workflow updates to new base IDs — Keith (partially completed; n8n validation errors encountered)

---

## Completed Milestones

- Bryan's Candidate Monitoring System built and deployed — 2024
- Sylvia's Job Search Automation System built and deployed — 2024
- Chris's dual-automation system designed and built (Automation #1: Job Movement Tracking + Automation #2: Job Posting Monitoring) — Aug-Sep 2025
- Two-stage Claygent architecture designed and proven for job change detection — Aug-Sep 2025
- Chris's Airtable schema redesigned from Bryan's duplicate to support 3 change types (company_change, internal_progression, additional_venture) — Aug 2025
- 3-Tier Recruitment Intelligence System architecture documented — Sep 2025
- Job Posting Monitoring Claygent prompts created (two-stage qualification) — Aug-Sep 2025
- Tier 3 Contact Discovery engine built (employment verification + hiring authority Claygents + Serper query generation) — Sep 2025
- CATS ATS API integration built for contact creation with duplicate checking — Sep 2025
- LinkedIn and Indeed scraper query generation prompts created — Sep 2025
- Outreach email generation prompt designed with research-backed best practices — Sep 2025
- Company intelligence enrichment layer designed (Company Intel, Market Intel, Outreach Strategy) — Sep 2025
- GPT-4.1 company enrichment validated (80-85% factual accuracy confirmed via web search verification) — Sep 2025
- Dashboard intelligence system designed with improved naming conventions — Sep 2025
- Job Discovery Configuration interface designed — Sep 2025
- Opportunity Review interface with human-in-the-loop validation designed — Sep 2025
- Sales Customization Framework created (18 records, 6 areas, traffic light system) — Sep 2025
- Talent Signals Platform Business Reference Blueprint documented — Sep 2025
- Keith's CTO bio written for recruitsystems.ai website — Sep 2025
- Competitive landscape research: 15 signal detection platforms identified and categorized — Sep 2025
- Candidate Matching & Sourcing module context brief created for cross-LLM brainstorming — Sep 2025
- Airtable onboarding video script updated (Team plan, subscription cost transparency) — Oct 2025
- Dynamic Feedback Filtering System redesigned: JSON blob → individual Airtable records — Oct-Nov 2025
- Branch 1 (Rules Compiler) n8n workflow built and tested — Oct 2025
- Branch 2 (Runtime Filter) n8n workflow designed with AI prompt — Nov 2025
- R2 Airtable base created (applI3iLy3pvAOKBK) with 5 core tables — Oct 2025
- R2 Job Requests table redesigned: JSON blob → consultant-friendly fields, 9 weights → 1 template field — Oct 2025
- R2 Candidate Matches table created with mock data (5 candidates with full score components) — Oct 2025
- R2 Question Bank pre-populated with 15 screening questions — Oct 2025
- R2 job scraping workflow (L4qTfAtz2jescnwg) AI agent node prompt rewritten from crawling orchestrator to extraction agent — Oct 2025
- Airtable schema comparison: Profile & Vacancy Monitoring vs Talent Signals v1.1 — created 10 missing CATS integration fields — Sep 2025
- Airtable Job Functions field created (27 categories) and Industries field (650+ options) — Nov 2025
- Juan Arroyave onboarded via 83-minute Fireflies-recorded session — Dec 5, 2025
- Client Automation Setup SOP structure designed (10 sections + appendices) — Dec 2025
- Cross-project context document compiled for SJM project — Mar 2026
- Signal Monitoring comprehensive system analysis completed for redesign project — Mar 2026
- 7+ client setups delivered — as of Dec 2025

---

## Insights & Learnings

- **Insight**: AI overrides correct math with intuitive reasoning when extraction and decision-making are in the same prompt.
  - **Why it matters**: This is the foundational architectural principle of TalentSignals. Separating Clay (fact extraction) from n8n/OpenAI (strategic decisions) across two stages eliminated the entire class of "AI second-guessing its own calculations" errors. Most critical for date calculations across year boundaries.

- **Insight**: Ambiguous field names cause downstream AI misinterpretation.
  - **Why it matters**: "role_change" meaning either "did a change occur?" or "should we process?" caused cascading errors. Explicit naming (`proceed_to_stage2`, `gate_status`) eliminates this. Applies to any system where AI reads field names as semantic cues.

- **Insight**: Rule consolidation vastly outperforms rule proliferation for AI-driven filtering.
  - **Why it matters**: 40+ individual rules from client feedback are unmaintainable and create conflicting matches. Consolidating to ~8-10 rules with patterns (company size thresholds, comma-separated keywords per category) is both more accurate and more maintainable.

- **Insight**: Cross-year date calculations are a consistent AI failure mode.
  - **Why it matters**: AI treats "July 2024 to September 2025" as "2 months" (comparing months only, ignoring years). Fixed by forcing explicit year-month extraction as STEP 0 before any analysis, with mandatory math display.

- **Insight**: Consultant-friendly design beats technical purity every time.
  - **Why it matters**: JSON blobs, complex scoring matrices, and versioning workflows that seem elegant to engineers are unusable by recruitment consultants. Simple multiline text fields with bullet points outperform structured data for human operators.

- **Insight**: Signal-agnostic architecture enables unlimited expansion without disrupting existing pipelines.
  - **Why it matters**: New signal types (funding announcements, expansion news, etc.) can be added by creating a new Tier 1 detector and connecting it to the existing Tier 2 webhook. No changes to Tier 2 or Tier 3 needed.

- **Insight**: GPT-4.1 company intelligence is 80-85% factually accurate but requires validation for production use.
  - **Why it matters**: Verified via web search against Lovevery data — regulatory standards, funding details, and industry context were largely accurate but contained some hallucinated specifics. Good enough for enrichment layer, not good enough as sole source of truth.

- **Insight**: Research-backed email design significantly impacts senior candidate outreach.
  - **Why it matters**: 150-200 word emails get 36% higher response rates. Bulleted questions reduce response rates with senior candidates. Questions must be conversationally integrated, not listed. Subject lines under 60 chars with personalization get 40%+ open rates.

- **Insight**: The "disconnect/reconnect trick" in n8n preserves field mappings when changing Airtable base IDs.
  - **Why it matters**: Critical for client replication. Without this, every Airtable node needs manual field remapping after base ID change.

- **Insight**: Execution summaries in n8n can show "0 items" even when nodes successfully process data.
  - **Why it matters**: Debugging must examine actual data flow, not rely on execution summary counts. Discrepancies between LLM outputs and saved Airtable records often stem from field mapping errors in update nodes, not parsing failures.

- **Insight**: For Airtable table creation via API, the first field must be singleLineText (not multipleRecordLinks).
  - **Why it matters**: API constraint that isn't documented clearly. Affects schema design when building tables programmatically.

- **Insight**: Companies flagged as "organization too large" should become a Company Size threshold rule, not individual company exclusions.
  - **Why it matters**: Pattern recognition insight for the Dynamic Feedback Filtering System. Instead of excluding "Accenture" and "KPMG" individually, set a 501+ employee threshold. Reduces rule count from 40+ to ~8-10.

- **Insight**: n8n workflow node naming should follow "Table | Action" pattern in 2-3 words.
  - **Why it matters**: Consistency across client instances makes debugging and maintenance scalable. Juan and future team members can navigate any client's workflow.

---

## Technical Architecture

### Core Platform Stack

| Component | Purpose | Details |
|-----------|---------|---------|
| **n8n** | Workflow orchestration | Self-hosted at n8n.talentsignals.ai; Queue Mode; each client gets own Hostinger VPS instance |
| **Clay** | AI enrichment, Claygent workflows, LinkedIn monitoring | Shared instance for most clients; some client-owned |
| **Airtable** | Data management across all products and clients | Each client gets own base (duplicated from master via Base Transfer workspace) |
| **Apify** | Job board scraping | LinkedIn (bebity/linkedin-jobs-scraper) and Indeed (borderline/indeed-scraper) |
| **OpenAI** | Strategic analysis and AI agent nodes | GPT-4.1; Tier 4 required for clients |
| **Serper API** | LinkedIn people search for contact discovery | 3-tier query strategy (primary/secondary/fallback) |
| **CATS ATS** | Output destination for contact records | API v3 with duplicate checking |
| **Hostinger** | Server hosting for client n8n instances | KVM2 plan recommended; select closest server location to client |
| **Pipedrive** | CRM (Andrew's domain) | Tracks deal pipeline and client communications |
| **Fillout** | Client intake forms | Collects requirements and credentials |
| **Fireflies** | Meeting transcription | Knowledge capture for onboarding sessions |

### 3-Tier Architecture

```
TIER 1: SIGNAL DETECTION
├── Role Change Monitoring (Clay → LinkedIn re-enrichment → Claygent 1 timeframe gate → Claygent 2 classification)
├── Job Vacancy Discovery (Apify scrapers → Clay qualification → two-stage hard/soft evaluation)
└── [Future: Funding signals, expansion signals, competitive intelligence]

TIER 2: OPPORTUNITY INTELLIGENCE HUB (n8n master workflow x9LeElcb6VC7N3p5)
├── Signal Type Switch → routes by signal type
├── Action Switch → routes by required action (create_target_company, find_supervisor, etc.)
├── AI company enrichment (GPT-4.1 → Company Intel, Market Intel, Outreach Strategy)
├── Human-in-the-loop review checkpoint ("Company Researched" status)
└── Targeted_Companies table population

TIER 3: CONTACT EXECUTION
├── Employment Verification Claygent (confirms contact still at target company)
├── Hiring Authority Claygent (assesses decision-making power, generates contact intelligence)
├── Serper API LinkedIn search (AI-generated queries with Serper-specific constraints)
├── Contact enrichment and email discovery
└── CATS ATS integration (API v3, check_duplicate=true as query parameter)
```

### Key Airtable Bases

| Base | ID | Purpose |
|------|-----|---------|
| Chris Production (Signal Monitoring) | `appBoRrPiiimgVFBk` | Live production system |
| Talent Signals v1.1 (Chris) | `appstrC3fpHDPloCU` | Feedback filtering system |
| R2 Rank & Reach | `applI3iLy3pvAOKBK` | Candidate screening product |
| R2 Job Scraping | `appS5M4HQJhHza2Qq` | Job parsing workflows |
| Sales Customization Framework | `applMfshngBhbXqU3` | Traffic light sales tool |
| Demo base (original Candidate Matching) | `appFvlZHgCWxvhGqU` | Deprecated demo |
| Sylvia's base | `apprLH50n9EDFFgKK` | Job search automation reference |
| Keith's KIRA | `appDe0hmbjYSFpjP0` | Personal knowledge base |
| Target Company enrichment base | `appApdWPkBaKeNoT0` | Company intelligence |

### Key n8n Workflows

| Workflow | ID | Purpose |
|----------|-----|---------|
| Master production workflow | `x9LeElcb6VC7N3p5` | Main Tier 2 processing hub |
| R2 job scraping | `L4qTfAtz2jescnwg` | Job parsing for R2 product |
| Chris production (original) | `2hWvnMsattWCYLdQ` | Do not edit — production |
| Base migration workflow | `GFFayAQvLo5pg6Rp` | Airtable base ID updates |

### Key Airtable Tables (Chris Production - appBoRrPiiimgVFBk)

- **Profiles** (`tblnon2etfnMo7O5C`) — Central monitoring hub for candidate profiles
- **Targeted_Companies** (`tblBzV0R3kzcm9DJi`) — Qualified company opportunities
- **Job_Postings** (`tblz30VW52GcOYhSt`) — Scraped and qualified job listings
- **Job_Config** (`tblSMNh7XjU87OPcR`) — Search configuration for scrapers
- **Contacts** — Decision-maker records for CATS integration

### Key Airtable Tables (R2 - applI3iLy3pvAOKBK)

- **Job Requests** (`tbloiVx7nT6rFkNlM`) — Job specifications with consultant-friendly fields
- **Candidate Pool** (`tblknp72DVxxOXDrl`) — Canonical LinkedIn profiles
- **Candidate Matches** (`tblpExQixQD76zC8w`) — Job × Candidate scoring junction table
- **Question Bank** (`tblYkgC4dLLU94AMI`) — Reusable screening questions
- **Outreach Exports** — Campaign tracking to Clay/Instantly

### Feedback Filtering Tables (appstrC3fpHDPloCU)

- **Client_Filter_Rules** (`tblR0qJ2TVqwEDMYf`) — Individual exclusion rules (Company Name, Domain, Keyword, Company Size types)
- **Opportunities** (`tblBzV0R3kzcm9DJi`) — Review feedback source

### AI Prompt Architecture

**Signal Monitoring — Role Change Detection:**
1. Claygent 1 (Clay): Employment pattern analysis, timeframe validation, role snapshot extraction. Outputs: change_type, months_ago, within_timeframe, proceed_to_stage2, role minis.
2. Claygent 2 (Clay): Change type classification, significance assessment, action determination. Respects Claygent 1's timeframe authority.

**Signal Monitoring — Job Vacancy Qualification:**
1. Stage 1: Hard requirements binary pass/fail (management level, title inclusion, experience, exclusions, geography, salary)
2. Stage 2: Quality scoring (only for jobs passing Stage 1)

**Tier 2 — Company Enrichment:**
- GPT-4.1 prompt generating Signal Context, Company Intel, Market Intel, Outreach Strategy
- Job Vacancy variant generates same fields from job posting data + department/seniority classification

**Tier 3 — Contact Discovery:**
- Employment Verification Claygent (confirms current employment at target)
- Hiring Authority Claygent (contactRoleType, hierarchicalRelationship, authorityLevel, authorityScore)
- Serper Query Generation (3 queries: primary/secondary/fallback with Serper API constraints)

**Tier 3 — Outreach Email Generation:**
- Signal-type-aware (job_vacancy vs profile_change)
- Uses hierarchical relationship and authority level for tone calibration
- 150-200 words, conversationally integrated questions, personalized subject lines

**R2 — Job Parsing:**
- Extraction agent processing Apify-scraped content (not crawling orchestrator)
- 19+ fields extracted per job description
- Corrected from original "Job Content Orchestrator" architecture

**Scraper Query Generation:**
- LinkedIn prompt: generates query objects with position, locationName, experienceLevel, publishedAt, contractType, rows
- Indeed prompt: generates query objects with query, location, country (lowercase), maxItems, radius, fromDays
- Both use 60/30/10 title distribution strategy (primary/secondary/variations)

---

## Content Seeds

- **Idea**: Two-stage AI analysis prevents hallucination — why separating extraction from reasoning matters
  - **Angle**: Practical production lesson. AI overrides its own correct math when you mix data extraction and strategic reasoning. Show the actual failure (cross-year date calculation bug) and the architectural fix.
  - **Source**: Claygent separation decision; multiple debugging sessions (Sep 2025).

- **Idea**: How a TA/HR professional built an AI platform without a traditional engineering team
  - **Angle**: Keith's background in Korn Ferry, Hays, Sheraton/St. Regis TA — not a software engineer. Built production AI system using no-code/low-code tools (n8n, Clay, Airtable). Practical AI for non-engineers.
  - **Source**: CTO bio conversation; overall project narrative.

- **Idea**: From single client to productized platform — lessons from building TalentSignals
  - **Angle**: Bryan → Sylvia → Chris → productized. Each build taught something. Signal-agnostic architecture emerged from client diversity. The "aha moment" when you realize the architecture is the product, not the individual automations.
  - **Source**: Evolution documented across all project knowledge files.

- **Idea**: The field naming problem — when ambiguous names break AI systems
  - **Angle**: "role_change" meaning two different things caused cascading AI errors. Renamed to "proceed_to_stage2." Simple lesson with massive impact. Applies to any AI system design.
  - **Source**: Oct 2025 debugging sessions.

- **Idea**: Rule consolidation vs proliferation — how we reduced 40+ filtering rules to 8
  - **Angle**: Client feedback generated dozens of individual exclusions. Consolidating into patterns (company size threshold, keyword categories) was both more accurate and maintainable. Counter-intuitive: fewer rules = better filtering.
  - **Source**: Dynamic Feedback Filtering System redesign (Nov 2025).

- **Idea**: Why your AI agent node is probably architectured wrong (extraction agent vs orchestrator)
  - **Angle**: R2 job parsing node was configured as a "Job Content Orchestrator" planning crawling strategies when it should have been an extraction agent processing already-scraped content. Common mistake when copying workflow patterns.
  - **Source**: R2 workflow debugging (Oct 2025).

- **Idea**: The outreach email research that changed our approach — data-driven recruitment communication
  - **Angle**: Research showed specific metrics: 150-200 words = 36% higher response, bulleted questions reduce senior candidate response rates. Subject lines under 60 chars. Practical, research-backed content.
  - **Source**: Outreach email research conversation (Oct 2025).

- **Idea**: Building a recruitment intelligence "dashboard" — what metrics actually matter
  - **Angle**: Naming conventions matter (not "Profiles Monitoring" but "Active Monitoring"). What to track: signal accuracy, cost per lead, processing speed, not just vanity metrics.
  - **Source**: Dashboard design conversation (Sep 2025).

- **Idea**: The setup sequence that saves hours of debugging (n8n → Airtable → Clay)
  - **Angle**: Order matters in multi-tool integrations. Webhook endpoints must exist before downstream tools reference them. Simple insight, saves massive debugging time.
  - **Source**: SOP development (Dec 2025).

- **Idea**: Competitive landscape analysis — how TalentSignals compares to 15 signal detection platforms
  - **Angle**: Researched JobsPikr, Specter, Lightcast, Sapiengraph, HR Signal, etc. TalentSignals' differentiation: end-to-end pipeline (detection through contact delivery), not just data.
  - **Source**: Competitive research conversation (Sep 2025).

---

## Raw Context

### Specific Configurations

**Chris's Job Search Criteria:**
- LinkedIn: Albany, NY Metro Area; Past Week; Associate, Mid-Senior Level, Director, Executive; Full time; Onsite and Hybrid; Salary 80,000+ or not specified
- Indeed: Albany 50 miles, within 7 days; CEO, CFO, COO, CIO, CTO, President, VP, Director, Manager (exclude retail, restaurant, hospitality), Controller

**Serper API Constraints (critical for query generation):**
- NO complex nested Boolean: -("term1" OR "term2") returns ZERO results
- NO domain searches: "company.com" returns ZERO results
- NO explicit AND operators
- OR operators in parentheses work: ("VP Sales" OR "CRO")
- Simple negative filters work: -"BadCompany"
- Unquoted company names work better than quoted
- LOCATION must be handled separately, not in query string

**Airtable Field Naming Conventions:**
- "Departed Employee" / "Departed Employee Name" for tracking people who left companies
- "Source Link" for universal URL field (employee profile or job posting)
- "Ready to Activate" instead of "Generate Search Query" for user-facing status
- "Company Researched" as the human review checkpoint status (after automated research, before contact discovery)
- "Pending Enrichment" as the status for new profiles needing initial Clay enrichment

**CATS ATS API:**
- Endpoint: `https://api.catsone.com/v3/contacts?check_duplicate=true`
- check_duplicate is a query parameter, not body parameter
- Method: POST with Authorization: Token header

**Indeed Scraper Query Format:**
- Country codes must be lowercase (e.g., "ae" not "AE", "us" not "US")
- Required fields: query, country, location
- Optional: maxItems, radius, fromDays, jobType, remote, postedBy, enableUniqueJobs, sort

**LinkedIn Scraper Query Format:**
- Required fields: position, locationName
- Uses experienceLevel enum codes: "1" (Internship), "2" (Entry), "3" (Associate), "4" (Mid-Senior), "5" (Director), "6" (Executive)
- publishedAt: "r604800" (past week), contractType: "F" (full-time)

**Dynamic Feedback Filtering Rule Types:**
- Company Name (match against company name substring)
- Domain (match against .gov, .edu etc.)
- Keyword (comma-separated values per category: education, healthcare, recruiting, etc.)
- Company Size (threshold with >= logic, e.g., "501 to 1000")

**R2 Scoring Dimensions (9 weights):**
Skills, Title Proximity, Domain, Seniority, Location Fit, Tenure Stability, Promotion History, Education Licenses, Activity Signals

**Hostinger Setup:**
- KVM2 plan recommended as starting point
- Select server location closest to client
- OS: search for "n8n" and select "n8n (queue mode)"
- After payment, client provides Hostinger account email and password via Fillout form

### URL References

- Company website: recruitsystems.ai
- n8n instance: n8n.talentsignals.ai
- CATS API docs: https://docs.catsone.com/api/v3/#contacts-create-a-contact

### Client Onboarding Flow

Fillout intake form → Pipedrive deal tracking → Technical setup (Juan): n8n first → Airtable second → Clay third → Test automations → Product training call (Andrew)

### Products at Different Stages

1. **Signal Monitoring** — Production. Multiple clients live. 3-tier architecture with two active signal types.
2. **R2 / Rank & Reach** — Active development. Airtable base built, job scraping workflow in progress, outreach generation pending.
3. **Reverse Recruiter** — Early design. Career-coach-focused, job-seeker-centric data model. Client-owned Clay instance (unlike Signal Monitoring's shared Clay).

### Project Knowledge Files in This Project

1. `bryan_candidate_monitoring_system_overview.md` — Original Bryan system architecture
2. `sylvia_job_search_system_overview.md` — Sylvia's job search automation
3. `Chris_Recruitment_Automation_-_System_Design___Architecture.md` — Chris dual-automation design
4. `Chris_Job_Change_Detection_-_Two-Stage_Analysis_Workflow_Documentation.md` — Two-stage Claygent docs
5. `Chris_Recruitment_Automation_-_Scope_of_Work.md` — Chris deliverables and requirements
6. `Automation__2__Job_Posting_Monitoring_System_-_High-Level_Design_Summary` — Job posting monitoring
7. `3-Tier_Recruitment_Intelligence_System_-_Implementation___Scaling_Guide.md` — Implementation and scaling
8. `Talent_Signals_Platform_-_Business_Reference_Blueprint.md` — Business reference doc
9. `Talent_Signals_Platform_-_Technical_Documentation.txt` — Technical documentation
10. `Dynamic_Feedback_Filtering_System` — Feedback filtering architecture

### Conversation Count

This project contains approximately 35-40 conversation threads spanning August 2025 through March 2026, covering the full lifecycle from initial Bryan/Sylvia reference builds through Chris implementation, productization, R2 development, operational handover to Juan, and documentation/migration efforts.
