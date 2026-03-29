# Migration Export: SJM Day Job Command Center

## Project Summary
- **Name**: sjm-day-job
- **Domain**: sjm
- **Status**: active
- **Description**: Keith Cheung's primary Claude project for his AVP Talent Acquisition & HRIS role at SJM Resorts, Macao. Functions as a command center for drafting leadership communications, managing executive searches, strategic thinking, team restructuring planning, and building personal AI infrastructure (ClaudeClaw). Also serves as the collection point for a YouTube content pipeline about AI transformation in corporate environments. Active since March 10, 2026 with 10 conversations to date.

---

## Key Decisions

### Project Setup & Infrastructure

- **Decision**: Established "observe → describe → draft → re-enter" as the core workflow pattern for working with AI despite SJM's locked-down IT environment
  - **Date**: March 10, 2026
  - **Rationale**: SJM desktop is fully locked down (no app installs, no email forwarding), duty iPhone has MDM restrictions preventing copy-paste between managed/personal apps. This pattern lets Keith use Claude on personal devices without violating any SJM IT policies.
  - **Alternatives considered**: Direct integration with SJM systems (impossible due to IT restrictions); using Claude on duty iPhone Safari (untested, marked for testing)

- **Decision**: Built six knowledge base documents as project foundation files
  - **Date**: March 10, 2026
  - **Rationale**: Ensures every new conversation in the project starts with full context without Keith needing to re-explain his situation
  - **Files created**: 01_Keith_Profile_and_Background.md, 02_SJM_Role_Context.md, 03_TalentSignals_Cross_Reference.md, 04_Content_Channel_Strategy.md, 05_Project_Operating_Manual.md, 06_ClaudeClaw_Infrastructure.md

- **Decision**: Established automatic Insight Log system at end of every substantive session
  - **Date**: March 10, 2026
  - **Rationale**: Feeds YouTube content pipeline passively without Keith needing to remember to capture insights
  - **Format**: Challenge / AI Angle / Content Takeaway

- **Decision**: ClaudeClaw personal AI agent — confirmed architecture
  - **Date**: March 10, 2026
  - **Rationale**: Needed a way to capture quick thoughts and process meeting notes from duty iPhone without SJM IT restrictions
  - **Configuration chosen**: Telegram interface → Node.js bridge → Claude Code CLI (via Agent SDK) → SQLite memory + Obsidian vault. Text-only Phase 1. Cron-based daily briefings. Background service via macOS Launch Agent.
  - **Alternatives rejected**: WhatsApp bridge (skip), voice input Phase 1 (defer to Phase 2 with Groq STT), video analysis (defer, Gemini API later)

- **Decision**: Recommended assistant names — Kenzo or Beacon
  - **Date**: March 10, 2026
  - **Rationale**: Kenzo fits Macao context and sounds natural on camera; Beacon reinforces "signal from noise" theme across TalentSignals and SJM role
  - **Alternatives considered**: Cortex, Atlas, Radar, Pronto, Jarvis, Friday

- **Decision**: Created cross-project extraction prompt for TalentSignals context bridging
  - **Date**: March 10, 2026
  - **Rationale**: Claude memory is project-scoped; can't cross boundaries. Designed a structured prompt to paste into TalentSignals project that produces a single synthesized context document.

### Executive Searches

- **Decision**: Pamela Yeung best fit is Team C (Special Projects) at Senior Manager to Assistant Director level
  - **Date**: ~March 12, 2026
  - **Rationale**: Her background (Korean beauty brands, consignment/incubation leasing models, Hongkong Land, Wharf China Estates Mainland portfolios) aligns with Team C's mandate around Japan/Korea/China new markets, pop-ups, and casual leasing at GLP retail
  - **Alternatives considered**: Higher-level placement (not justified by current title/experience); other teams (less alignment with her specialty)

- **Decision**: AVP/VP Retail Leasing headhunter brief — package in MOP (not HKD), no years-of-experience thresholds, English requirement softened to reading/writing ability
  - **Date**: ~March 12, 2026
  - **Rationale**: MOP is the local operating currency for Macao roles; removing experience thresholds keeps the search flexible; English fluency requirement was too rigid for the target candidate pool
  - **Alternatives considered**: HKD package (rejected — MOP more appropriate); 15+ years experience requirement (rejected — unnecessarily limiting)

- **Decision**: Reference projects for retail leasing brief finalized: Shanghai West Bund, MixC World Shenzhen Bay, SKP-S Beijing, The Shoppes at Marina Bay Sands, Jewel Changi Airport
  - **Date**: ~March 12, 2026
  - **Rationale**: Each project matches GLP's visitor profile (international tourists, luxury consumers, gaming visitors) and demonstrates non-standard (非標準) retail thinking
  - **Alternatives rejected**: K11 Musea (audience mismatch — serves local arts/cultural audience, not GLP's tourist/gaming visitor profile)

- **Decision**: Declined Chef Guo Yuan Feng's profit-sharing proposal on the spot
  - **Date**: ~March 13, 2026 (the Thursday meeting)
  - **Rationale**: Profit-sharing is not SJM's compensation structure for chef roles and is not common hotel industry practice
  - **Asked Guo to resubmit**: Requested a revised salary expectation and CV. As of March 16, no response.

- **Decision**: Chef Guo's ~RMB 116k after-tax figure flagged as potentially misleading if included in written update
  - **Date**: March 16, 2026
  - **Rationale**: The figure represents combined after-tax income across multiple streams (salary + allowances + own company invoicing), not a simple base salary. Could create a misleading anchor for offer discussions.

- **Decision**: Chef Lam assessed as not suitable for The Eight following food tastings
  - **Date**: By March 16, 2026
  - **Rationale**: Gerard's mystery shopper visits found food "ok" but inconsistent for a 1-star Michelin venue, let alone a 2-star destination

### Hiring Freeze & Internal Communications

- **Decision**: Name the hiring freeze explicitly and plainly in email to Gerard Walker
  - **Date**: ~March 19-20, 2026
  - **Rationale**: Keith initially considered soft-pedaling but ultimately decided to state the freeze directly. Vague softening would obscure the message to a CXO-level stakeholder.
  - **Alternatives rejected**: "Internal process considerations" language (too vague); surfacing the pre-existing role positioning overlap issue (would stir unnecessary written discussion — keep offline)

- **Decision**: Do NOT surface the F&B Marketing role positioning overlap issue in writing
  - **Date**: ~March 19-20, 2026
  - **Rationale**: The two roles had a pre-existing, unresolved overlap with the current marketing department that the CHRO hadn't cleared. Gerard was unaware. Surfacing it in email would create unnecessary discussion. Handle verbally.
  - **Principle established**: Politically complex or sensitive matters stay out of written comms and are handled offline

- **Decision**: Keep F&B Marketing candidates warm but make no timeline commitment
  - **Date**: ~March 19-20, 2026
  - **Rationale**: Two separate hurdles exist (hiring freeze + unresolved positioning), not just one. Can't commit to swift action post-freeze since the second hurdle remains.

### Team & HR

- **Decision**: Angel offered HR Employee Relations Manager role at SJM
  - **Date**: ~March 17, 2026
  - **Rationale**: Viewed as someone who can help steer the team and build the right culture — a strategic partner, not just a role-filler
  - **Communication approach**: WhatsApp message framed around "help shape what we're building" — conveying influence and co-ownership

- **Decision**: Catherine Chang to be promoted immediately to Associate Director; Sharon Kuong's 10-person F&B hiring team to be absorbed under Catherine post-promotion
  - **Date**: ~March 20, 2026
  - **Rationale**: Catherine is Keith's strongest leader with an established management style. Sharon's team under MaiLing was a temporary/legacy arrangement from predecessor Cathy Lei.

- **Decision**: Cautious assessment of Alex Leong despite leadership viewing him as succession plan
  - **Date**: ~March 20, 2026
  - **Rationale**: Alex is passive and hands-off; management gaps cascade down through sub-managers (Clovis, Renee, Karen). Most visible in the Reina U situation — placed in AM role without adequate coaching. Key tension: Rachel and Daniel view Alex as capable; Keith's ground-level assessment differs.

### Personal / Dining

- **Decision**: Bamboo Hut at GLP serves Turkish cuisine (not Southeast Asian as originally classified)
  - **Date**: ~March 19, 2026
  - **Rationale**: Keith dined there and confirmed directly

- **Decision**: Recognition email for Kim (胡文傑) at Bamboo Hut to be sent to Gerard Walker (CHO)
  - **Date**: ~March 19, 2026
  - **Rationale**: Peer-level compliment from Keith carries weight; natural relationship-building touchpoint early in tenure. Recommended shorter version given where Keith is in building the Gerard relationship.

- **Decision**: Public restaurant reviews should NOT name Kim and should NOT reveal SJM executive status
  - **Date**: ~March 19, 2026
  - **Rationale**: As an SJM executive, public reviews of SJM outlets could read as internal promotion rather than genuine guest feedback. Keep public reviews food-focused; reserve named recognition for internal email.

---

## People Involved

### SJM Leadership

- **Daniel Shim** | SJM Resorts | Chief Corporate Affairs Officer | Keith's reporting director | HR sits under his remit. Note: NOT Chief HR Officer — corrected March 16.
- **Rachel** (surname: Chan based on email thread context) | SJM Resorts | SVP, Human Resources | Keith's direct reporting line (Keith → Rachel → Daniel)
- **Gerard Walker** | SJM Resorts | Chief Hospitality Officer (CHO) | Cross-functional stakeholder | Leads culinary candidate evaluation and mystery shopper visits for exec chef searches. Key relationship Keith is building. Positive and responsive.
- **Bruno Correa** | SJM Resorts | Executive Chef | Cross-functional stakeholder | Culinary assessor for F&B searches. Works closely with Gerard on chef evaluations.
- **Shirley** | SJM Resorts (Grand Lisboa Palace) | New SVP of Retail | Stakeholder | Recently joined to replace underperforming VP Miranda. Introduced Pamela Yeung as a candidate.
- **Miranda** | SJM Resorts (Grand Lisboa Palace) | VP of Retail (outgoing) | Internal | Still in seat, being managed out. Replaced by Shirley.
- **Ms. Ho** | SJM Resorts | MD (Managing Director) | Internal | Recommended Chef Zhang for Hua Ting role.
- **Cathy Lei** | SJM Resorts | Keith's predecessor in the AVP TA role | Internal | Some legacy team structures (e.g., MaiLing's standalone team) are inherited from her tenure. Also involved in interview notes/supporting processes.

### Keith's Direct Reports (TA Team)

- **Alex Leong** | SJM Resorts | Associate Director | Direct report | Passive/hands-off management style. Leadership views him as succession plan but Keith's assessment is more cautious. Three sub-managers: Clovis Vong, Renee Chan, Karen Kwan.
- **Catherine Chang** | SJM Resorts | Senior Manager → Associate Director (promotion pending) | Direct report | Strongest leader. Established management style. Will absorb Sharon Kuong's F&B hiring team post-promotion.
- **MaiLing Leong** | SJM Resorts | Senior Manager | Direct report | Standalone small team (2 people). Legacy structure from Cathy Lei that needs rethinking.
- **Ryan Cheang** | SJM Resorts | Senior Manager (promoted last year) | Direct report | Recruitment fundamentals are a concern beyond just management development.

### Key Sub-Managers & Team Members

- **Sharon Kuong** | SJM Resorts | Manager | Sub-manager | Leads 10-person F&B hiring team. Currently under MaiLing temporarily; to transition under Catherine.
- **Clovis Vong** | SJM Resorts | Sub-manager under Alex | Internal
- **Renee Chan** | SJM Resorts | Sub-manager under Alex | Internal
- **Karen Kwan** | SJM Resorts | Sub-manager under Alex | Internal
- **Reina U** | SJM Resorts | Assistant Manager (under Karen) | Internal | Placed in AM role without adequate coaching or framework. Case study for Alex's management gaps.
- **Yany Ho** | SJM Resorts | Team member | Internal
- **Santana Lam** | SJM Resorts | Team member | Internal

### Executive Chef Search — The Eight (2-Michelin-star)

- **Chef Tam Kwok Fung** | Wynn Palace | Executive Chef (current) | Candidate — primary target | Package info collected, salary expectation not yet provided.
- **Chef Leo Li** | Four Seasons Guangzhou | Chef | Candidate — alternate | Positive mystery shopper assessment March 7 by Gerard, Jonas, Nelson, Bruno.
- **Chef Lam** | Peninsula Hong Kong | Chef (1-Michelin-star) | Candidate — alternate, likely to be declined | Food assessed as inconsistent across mystery shopper visits. Not suitable for The Eight.
- **Chef Guo Yuan Feng (郭元峰 / Gordon Guo)** | Raffles Shenzhen (Yun Jing 云境) | Chinese Executive Chef & GM | Candidate | Met Keith in person ~March 13. Proposed profit-sharing (declined). Asked to bring 10 team members. Asked about renovation plans. ~RMB 116k/month after-tax total comp across multiple streams. No Michelin star. Has not responded with revised expectations or CV.

### Executive Chef Search — Hua Ting

- **Chef Yan** | SJM Resorts (Hua Ting) | Incumbent Executive Chef | Internal | Work authorization (blue card) expires September 2026 — driving urgency for replacement.
- **Chef Zhang** | Tokyo (current) | Chef | Candidate — recommended | Tasted by Bruno and Nelson in Tokyo. Recommended by MD Ms. Ho. Deemed suitable for Hua Ting (Cantonese and Shanghainese).
- **Chef Chang** | Unknown | Chef | Candidate | Keith in contact, continuing process.

### GLP Retail Leasing

- **Pamela Yeung** | Wharf China Estates (current); previously Hongkong Land (One Central Macau) | Leasing Manager | Candidate | Introduced by Shirley. Background in luxury leasing, Korean beauty brands, non-traditional leasing models (consignment, incubation). Recommended fit: Team C (Special Projects) at Senior Manager to Assistant Director level.

### F&B Marketing Candidates

- **Ms. Elli** | External | F&B Marketing candidate | Candidate | Recommended by Nelson. On hold due to hiring freeze + unresolved role positioning.
- **Ms. Sofia** | External | F&B Marketing candidate | Candidate | Recommended by Nelson. Same status as Ms. Elli.
- **Nelson** | SJM Resorts | Internal (involved in tasting visits and candidate recommendations) | Colleague

### Other SJM People

- **Jonas** | SJM Resorts | Involved in tasting visits | Colleague
- **Angel** | External → SJM Resorts | HR Employee Relations Manager | New hire | Offer extended ~March 17. Viewed as strategic partner who can help shape culture.
- **Kim (胡文傑)** | SJM Resorts (Bamboo Hut, GLP) | Service Attendant | Internal | Exceptional service; recognition email drafted to Gerard.

### TalentSignals (Cross-Reference)

- **Andrew Shwetzer** | TalentSignals | Business/Sales Partner | Partner | Handles all client sales, pricing, onboarding. Clean division: Andrew owns clients, Keith owns product/tech.
- **Juan Arroyave** | TalentSignals | Operations (transitioning in) | Partner | Taking over client implementations from Keith. Freeing Keith's bandwidth for SJM.
- **Silvia Vladimirova** | Dubai | Career Coach | Client | Original Reverse Recruiter client. Part of 100+ coach community.
- **Chris Wessell / PeopleWise LLC** | External | Recruiter | Client | First production Signal Monitoring + R2 client. 127K candidates + 4.4K contacts in CATS ATS.
- **Ben Van Sprundel** | BenAI Partner Network | Founder | Ecosystem | TalentSignals originated within BenAI. Keith is CAO of BenAI Partner Network.
- **David** | External | AI Strategy Partner (earlier phase) | Collaborator | Contributed to architecture decisions and prompt engineering strategy.

---

## Active Action Items

### Executive Searches
- [ ] Follow up with Chef Guo Yuan Feng for revised salary expectation and CV -- Keith -- no deadline (he's gone silent)
- [ ] Await Gerard's guidance on prioritization across The Eight's four candidates -- Gerard/Bruno -- pending their response to Keith's update email
- [ ] Chef Lam — to be formally declined or redirected to another role -- Keith/Gerard -- pending
- [ ] Connect with Chef Zhang and map hiring/onboarding for Hua Ting -- Keith -- before September (Chef Yan's blue card expiry)
- [ ] Continue engagement with Chef Tam Kwok Fung (Wynn Palace) to obtain salary expectation -- Keith -- ongoing
- [ ] Chef Chang — continue process for Hua Ting onboarding -- Keith -- ongoing

### GLP Retail
- [ ] Headhunter brief for AVP/VP Retail Leasing — finalized, needs to be sent to headhunter -- Keith -- immediate
- [ ] Pamela Yeung placement — confirm level and team assignment with Shirley -- Keith -- pending
- [ ] New Yaohan floor repositioning strategy (lease terminates January 2027) -- Shirley/Retail team -- ongoing

### F&B Marketing
- [ ] Keep Ms. Elli and Ms. Sofia warm during hiring freeze -- Keith's team -- ongoing, no timeline
- [ ] Resolve F&B Marketing role positioning overlap with current marketing department -- offline/verbal with CHRO -- no deadline set

### Team Restructuring
- [ ] Complete Catherine Chang's promotion to Associate Director -- Keith -- immediate
- [ ] Transition Sharon Kuong's 10-person F&B hiring team from MaiLing to Catherine -- Keith -- post-promotion
- [ ] Address Alex Leong's management gaps (navigate carefully given leadership's positive perception) -- Keith -- ongoing
- [ ] Rethink MaiLing Leong's standalone 2-person team structure -- Keith -- medium-term
- [ ] Assess Ryan Cheang's recruitment fundamentals -- Keith -- ongoing
- [ ] Onboard Angel as HR Employee Relations Manager -- Keith/HR -- post-offer acceptance
- [ ] Confirm 2 unidentified names from org chart (likely in Alex's tree) -- Keith -- low priority

### Infrastructure
- [ ] ClaudeClaw weekend build session -- Keith -- planned for a weekend in March 2026
- [ ] Install Obsidian on Mac -- Keith -- before ClaudeClaw build
- [ ] Create Telegram bot via BotFather -- Keith -- during ClaudeClaw setup
- [ ] Test Claude.ai on duty iPhone Safari -- Keith -- quick test
- [ ] Verify Node.js and Claude Code CLI versions on Mac -- Keith -- pre-flight for ClaudeClaw
- [ ] Phase 2: Plaud AI → Zapier → ClaudeClaw pipeline -- Keith -- after ClaudeClaw is stable

### Personal
- [ ] Send recognition email for Kim (胡文傑) to Gerard Walker -- Keith -- pending
- [ ] Post public reviews of Bamboo Hut on Google Maps and TripAdvisor (without naming Kim or SJM affiliation) -- Keith -- optional
- [ ] Update Bamboo Hut cuisine classification from "Southeast Asian" to "Turkish" in directory files -- done in markdown, may need xlsx update

---

## Completed Milestones

- Six knowledge base documents created and uploaded to project -- March 10, 2026
- 11 memory edits established for project continuity -- March 10, 2026
- Cross-project extraction prompt built for TalentSignals context bridging -- March 10, 2026
- ClaudeClaw complete setup plan document produced (ClaudeClaw_Complete_Setup_Plan.md) -- March 10, 2026
- 907-line mega prompt (REBUILD_PROMPT.md) uploaded to project -- March 10, 2026
- Confidential position brief for AVP/VP Retail Leasing headhunter search produced (5 iterations) -- ~March 12, 2026
- Pamela Yeung placement assessment completed (Team C, SM to AD level) -- ~March 12, 2026
- The Eight executive chef search — consolidated 4-candidate status update email drafted and refined -- March 16, 2026
- Daniel Shim title correction (Chief Corporate Affairs Officer, not Chief HR Officer) updated in memory -- March 16, 2026
- Angel offer WhatsApp message crafted -- ~March 17, 2026
- DE F&B Discount Directory consolidated from photographed source docs into 4-sheet Excel workbook -- March 18, 2026
- STDM property locations identified and mapped (Grand Lapa, Grand Coloane, Macau Tower, Golf Club) -- March 18, 2026
- DE F&B Discount Directory converted to enriched markdown with cuisine tags and quick-find index -- ~March 19, 2026
- Bamboo Hut recognition email to Gerard drafted (2 variants) -- ~March 19, 2026
- Public review drafts for Bamboo Hut produced (TripAdvisor + Google Maps versions) -- ~March 19, 2026
- Hiring freeze email to Gerard drafted and refined through multiple iterations -- ~March 19-20, 2026
- Follow-up reply to Gerard (post his positive response) refined -- ~March 20, 2026
- Full team structure document produced: 07_SJM_TA_Team_Structure.md — 41 confirmed names, capability assessments, restructuring plans -- March 20, 2026

---

## Insights & Learnings

- **Insight**: Financial figures in writing require care — combined or multi-stream income figures create misleading anchors
  - **Why it matters**: Chef Guo's ~RMB 116k/month was from salary + allowances + company invoicing. Presenting it as a simple salary number would mislead Gerard/Bruno in offer discussions. Rule: flag whether to include financial details in writing or discuss verbally.

- **Insight**: "I understand that" implies the other person informed you — subtle but important in CXO communications
  - **Why it matters**: In the hiring freeze email, "I understand that there is a hiring freeze" implied Gerard told Keith, when actually Keith was informing Gerard. The correct framing was "I wish to advise that." Direction of information flow matters in political communications.

- **Insight**: When two hurdles exist (hiring freeze + unresolved positioning), don't commit to swift action after removing just one
  - **Why it matters**: Claude initially drafted language promising to "move forward promptly" post-freeze. Keith caught that the positioning issue was the second, unwritten blocker. Lesson: always consider what's NOT being said.

- **Insight**: Sensitive matters stay out of written comms — keep verbal
  - **Why it matters**: The F&B Marketing role overlap with the marketing department, Miranda being managed out, and internal capability assessments are examples of information that should never be in email. Written words become permanent records.

- **Insight**: Reference project audience-matching is critical in retail leasing briefs
  - **Why it matters**: K11 Musea looks impressive on paper but serves a local arts/cultural audience — wrong match for GLP's international tourist/gaming visitor profile. Always validate reference projects against the actual target audience.

- **Insight**: Peer-level compliments from new executives carry disproportionate weight early in tenure
  - **Why it matters**: Keith's recognition email to Gerard about Kim is both genuine and strategically smart — it builds the relationship naturally. But it needs to be the shorter/more restrained version given Keith is still new.

- **Insight**: SJM executives publicly reviewing SJM outlets creates a positioning risk
  - **Why it matters**: Reviews could read as internal promotion rather than genuine guest feedback. Keep public reviews anonymous and food-focused; reserve named employee recognition for internal channels.

- **Insight**: Keith rejects generic recruitment language — "development opportunity," "partnership opportunity," "good news," "close the loop" all miss
  - **Why it matters**: His communication instinct is specific, strategic, and brief. When helping with candidate communications, skip the recruiter playbook and lean into what makes the role/person genuinely unique.

- **Insight**: Claude's project-scoped memory creates a real information architecture challenge across multiple workstreams
  - **Why it matters**: TalentSignals context can't cross into the SJM project automatically. The workaround is designing structured extraction prompts as "context transfer protocols" between projects.

- **Insight**: The "observe → describe → draft → re-enter" workflow works — most AI users assume they need data access, but verbal description is sufficient for most executive work
  - **Why it matters**: This is the core content thesis for the YouTube channel. Most people assume AI needs system integrations to be useful in corporate settings.

---

## Technical Architecture

### Claude.ai Project (This Project)
- **Platform**: Claude.ai (web/mobile), Claude Max subscription ($100/month)
- **Access**: Personal laptop (primary), personal phone (secondary)
- **Project files**: 6 knowledge base .md files + 2 DE F&B directory files (.md and .xlsx) + REBUILD_PROMPT.md + ClaudeClaw_Complete_Setup_Plan.md + 07_SJM_TA_Team_Structure.md
- **Memory edits**: 11 items maintained
- **Connected MCPs**: YouTube Transcript Server, Fireflies, Google Calendar, Gmail, n8n, Airtable, filesystem, brave-search, Control Chrome, Claude in Chrome

### ClaudeClaw (Planned)
- **Architecture**: Telegram Bot → Node.js/TypeScript bridge → Anthropic Agent SDK → Claude Code CLI → SQLite (memory) + Obsidian vault (knowledge)
- **Location**: ~/Projects/claudeclaw/ on home Mac
- **Auth**: OAuth via existing Claude Max subscription (no separate API key)
- **Permission mode**: bypassPermissions (unattended operation)
- **Background service**: macOS Launch Agent (.plist in ~/Library/LaunchAgents/)
- **Database**: SQLite with WAL mode in store/ directory
- **Commands**: /start, /chatid, /newchat, /memory, /forget, /voice (future), /schedule
- **Scheduled tasks**: Morning briefing 8:00 AM Mon-Fri; Evening capture 9:00 PM daily

### Obsidian Vault (Planned)
- **Location**: ~/TheVault/
- **Structure**: daily-notes/, sjm/ (team/, stakeholders/, processes/, meetings/), content-pipeline/ (insight-log/, episode-ideas/), projects/, people/, inbox/
- **Format**: YAML frontmatter, [[double brackets]] linking, #tags
- **Interaction**: Claude Code reads/writes; human reads in Obsidian app

### Meeting Capture Pipeline (Planned — Phase 2)
- **Hardware**: Plaud AI recorder
- **Flow**: Plaud records (Cantonese) → Plaud app transcribes → Zapier detects → sends to ClaudeClaw via Telegram → Claude Code processes with Keith's English voice summary → structured note to Obsidian vault
- **Note**: Cantonese transcription accuracy is imperfect; Keith's English voice summaries are the primary narrative source

### SJM IT Constraints
- **Desktop**: Windows/Microsoft 365, fully locked down, no app installs
- **Duty iPhone 17**: MDM-restricted — can't save Outlook files, can't copy-paste between managed/personal apps. Telegram works.
- **Workaround**: All AI interaction happens on personal devices. Data transfer limited to what Keith can observe/describe/dictate.

---

## Content Seeds

- **Idea**: "I built a personal AI that runs around my corporate IT restrictions — in one weekend"
  - **Angle**: The ClaudeClaw setup story. Hook: most people assume AI needs corporate system access. This shows an executive building a personal AI stack using Telegram, Claude Code, and Obsidian that works alongside (not inside) corporate systems.
  - **Source**: March 10 setup session

- **Idea**: "How I use a hardware recorder and AI to turn Cantonese meetings into structured English notes"
  - **Angle**: Plaud AI → Zapier → ClaudeClaw → Obsidian pipeline. The constraint (Cantonese meetings, imperfect transcription, locked IT) makes it relatable.
  - **Source**: March 10 infrastructure planning

- **Idea**: "How I loaded my entire career into AI in 10 minutes"
  - **Angle**: Showing the process of creating cross-project context documents. Six documents gave a new Claude instance full working knowledge of a complex multi-venture career.
  - **Source**: March 10 knowledge base creation

- **Idea**: "How I manage two jobs with AI — and the context problem no one talks about"
  - **Angle**: AI memory is project-scoped and doesn't cross boundaries. The workaround is designing structured extraction prompts as "context transfer protocols." It's prompt engineering as information architecture.
  - **Source**: March 10 TalentSignals extraction prompt

- **Idea**: "How I handle sensitive stakeholder communications using AI — even when I can't copy a single word out of my corporate email"
  - **Angle**: The hiring freeze email to Gerard. Keith photographed the email on his SJM desktop, described the political context verbally, and Claude drafted a formal CXO response in 2 minutes. Pure observe → describe → draft.
  - **Source**: March 19-20 hiring freeze conversation

- **Idea**: "My first 90 days using AI as a secret weapon at a traditional company"
  - **Angle**: Compilation episode covering the full range of SJM work: exec chef search, retail leasing brief, team restructuring, stakeholder comms — all done through the locked-down workaround architecture.
  - **Source**: Aggregate across all conversations

- **Idea**: "The gap between what AI can do and what corporate IT will let you do"
  - **Angle**: The fundamental tension of Keith's situation — deep AI building expertise (TalentSignals) but working inside a company that won't let him install apps or forward emails.
  - **Source**: Ongoing theme

- **Idea**: "How I used AI to redesign my team structure without access to a single HR system"
  - **Angle**: Keith described the 43-person team verbally (supplemented with phone photos of org charts), and Claude mapped it into a full structured document with capability assessments and restructuring recommendations.
  - **Source**: March 20 team structure conversation

---

## Raw Context

### SJM Corporate Facts
- SJM Resorts: one of six gaming concessionaires in Macao
- Properties: Grand Lisboa, Grand Lisboa Palace (GLP, Cotai), Hotel Lisboa, Ponte 16, Jai Alai, plus STDM parent company properties (Grand Lapa, Grand Coloane, Macau Tower)
- GLP retail shopping area significantly underperforms other Cotai operators — low capture ratio, low foot traffic, physical distance from main Cotai Strip
- New Yaohan department store at GLP terminating January 2027 — frees entire floor for repositioning
- Company-wide hiring freeze on new headcount communicated by Daniel Shim to all CXOs (as of ~March 19, 2026)
- Culture: very formal and corporate, hierarchical decision-making, Cantonese workplace language with English for certain business comms

### The Eight Restaurant Context
- Two-Michelin-star Cantonese fine dining at Grand Lisboa
- Market landscape includes 4 three-star and 14 two-star Michelin Chinese restaurants (excluding SJM's)
- The 8 / "8餐廳" — one of Macao's best Chinese fine dining rooms

### Hua Ting Context
- Shanghainese cuisine restaurant at Grand Lisboa Palace
- Current Chef Yan's blue card (work authorization) expires September 2026 — driving urgency
- Replacement chef must handle both Cantonese and Shanghainese cuisine

### GLP Retail Leasing Brief — Key Details
- Role: AVP or VP of Retail Leasing (flexible on level)
- Package: MOP 1.2M–2M
- Profile: Non-standard (非標準) retail thinking — not traditional mall leasing
- English: Reading/writing for internal correspondence (not fluency)
- Source companies: Shanghai West Bund Development Group, Hongkong Land, SKP/Beijing Hualian Group, China Resources Land, Swire Properties, Changi Airport Group/Jewel, Hysan Development, Wharf REIC, Las Vegas Sands/Marina Bay Sands retail, DFS/LVMH Travel Retail, other Cotai operators

### Keith's TA Team — Full Structure (41 of 43 confirmed)
- Keith Cheung (AVP) reports to Rachel (SVP HR) reports to Daniel Shim (CCAO)
- Four direct reports: Alex Leong (AD), Catherine Chang (SM→AD), MaiLing Leong (SM), Ryan Cheang (SM)
- Alex's sub-managers: Clovis Vong, Renee Chan, Karen Kwan
- Sharon Kuong: Manager, 10-person F&B hiring team (under MaiLing temporarily → Catherine)
- Complete name list in 07_SJM_TA_Team_Structure.md

### DE F&B Discount Directory
- ~65 restaurants across SJM and STDM properties
- Discount tiers: 50% (best value, most outlets), 40% (STDM properties), 30% (fine dining), 25% (Tromba Rija only), 20% (Valet Shop only)
- Full 2026 blackout calendar maintained
- Bamboo Hut confirmed as Turkish cuisine (not Southeast Asian)
- Best Thai option: NAAM at Grand Lapa (40% off)
- Top fine dining with discount: Robuchon au Dôme (French, 30%), The 8 (Cantonese, 30%), Mesa by José Avillez (Portuguese, 30%)

### Communication Patterns Learned
- SJM leadership comms: very formal, "Dear X", structured closes, contain only what's necessary
- Keith on WhatsApp: casual, direct, authentic, hates generic recruiter language
- Keith's drafting workflow: Claude produces initial draft → Keith edits directly → Claude reviews for typos, grammar, framing, misleading implications
- Keith values being flagged on things he hasn't explicitly raised (reputational risks, political implications, misleading financial anchors)

### Key URLs Referenced
- Qianwen salary analysis for Chef Guo: https://www.qianwen.com/share/chat/37c553765cf44f55bafb2b7a177b3fb1?biz_id=ai_qwen&env=prod&qwcontainer=qk (JavaScript-rendered, couldn't be fetched directly)

### Files Produced in This Project
- 01_Keith_Profile_and_Background.md
- 02_SJM_Role_Context.md
- 03_TalentSignals_Cross_Reference.md
- 04_Content_Channel_Strategy.md
- 05_Project_Operating_Manual.md
- 06_ClaudeClaw_Infrastructure.md
- 07_SJM_TA_Team_Structure.md
- SJM_DE_FB_Discount_Directory.xlsx (4-sheet workbook)
- SJM_DE_FB_Discount_Directory.md (enriched markdown version)
- ClaudeClaw_Complete_Setup_Plan.md
- REBUILD_PROMPT.md (907-line mega prompt)
- Retail Leasing headhunter position brief (confidential)

---

*Exported: March 22, 2026 | Source: Claude.ai SJM Day Job project | 10 conversations spanning March 10–20, 2026*
