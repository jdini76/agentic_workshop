# Marketing workflow status — 2026-08-05

Agentic Workshop's practical purpose: a partially autonomous marketing tool promoting the owner's
own products — starting with the children's book *Jordan and the Fosters*, with a Theater Rehearsal
Web App and other future products to follow. Sarah (strategist), Casey (content creator), and the
department/company model are the implementation vehicle for that goal, not an end in themselves.

## ChatGPT status update (verbatim, received 2026-08-05)

> Waiting to push is fine. The local commits preserve the work; GitHub backup can happen whenever
> you're comfortable.
>
> [Full phase roadmap below — Phase 1: finish the weekly campaign workflow (Casey review controls,
> Casey deterministic generation, campaign-preview controls, "start next campaign" workflow);
> Phase 2: make the workflow pleasant (attention queue, preview freshness, campaign history,
> friendlier presentation); Phase 3: close the marketing feedback loop (manual publication records,
> manual analytics entry, hire Morgan the performance analyst); Phase 4: add real AI where it adds
> value (model generation from the interface, model-assisted revision, Sarah research support via
> Riley); Phase 5: distribution only when ready (website handoff, social handoff, email campaigns).
> v0.1 definition: start a campaign week, review/approve Sarah, generate/revise/approve Casey,
> generate and open a preview, see campaign history, record manual publication, enter simple
> results. Recommended order: finish Casey review controls → deterministic Casey generation →
> preview generation/freshness → "start next campaign" → attention queue → publication records →
> manual results → hire Morgan → optional model generation → external publishing integrations only
> after repeated real use.]

*(Full original text preserved in the session transcript this file was generated from; condensed
here to avoid duplicating a large verbatim block — ask if the complete text should be pasted in
directly instead.)*

## Claude verification against actual source (2026-08-05)

Checked directly against `src/agentic_workshop/adapters/local_workspace.py` and `git log`, not
inferred from the ChatGPT summary above.

**Already done — further along than the ChatGPT update states**, confirmed by both commit history
and live route code:

- Casey review controls (Phase 1, item 1): commit `b941b03` "add local Casey review workflow."
  Routes exist for `package` (view), `package/approve/confirm`, `package/revision/confirm`.
- Casey deterministic generation (item 2): commit `141a797` "add deterministic Casey generation to
  workspace." Route `package/generate/confirm` exists and is gated on Sarah's brief being approved
  (`"Sarah's brief must be approved before Casey can generate content."`).
- Campaign-preview controls (item 3): commit `ef3103f` "add verified campaign previews to
  workspace" — the current HEAD commit. Routes `preview` (view) and `preview/generate/confirm`
  exist. Freshness is **checksum-bound, not folder-existence-based**: `combined_generation_checksum`
  binds the brief checksum and package checksum together, and preview generation checks
  `expected_checksum` against the current package before proceeding — this already satisfies the
  Phase 2 item 6 "preview freshness" requirement (stale-because-Casey-changed is structurally
  prevented, not just planned), ahead of where the ChatGPT update placed it.

**Resolved 2026-08-06:** "Start next campaign" workflow (item 4) is now implemented — see the
update below. Phase 1 is complete: the full weekly workflow (start → Sarah approve → Casey
generate/approve → preview generate) is operable entirely from the browser workspace.

**Not yet checked this session** (no claim either way): Phase 2 items 5/7/8, all of Phase 3
(publication records, manual analytics, Morgan), Phase 4, Phase 5.

## Update — 2026-08-06: "start next campaign" implemented

New `StartNextCampaign` application service (`src/agentic_workshop/application/next_campaign.py`)
plus `GET`/`POST /campaign/new` routes in the workspace. Accepts any day within the target week,
normalizes to that week's Monday (matching the CLI's existing behavior), and rejects duplicate
weeks with HTTP 409 and a link to the existing campaign. Uses a dedicated nonce type
(`new_campaign_nonce`) since — unlike every other mutation in this workspace — there's no
pre-existing artifact to bind a checksum to when creating something for the first time.

Verified against the real repository (not just test fixtures): submitted week `2026-08-21`
(a Friday), confirmed it normalized to `2026-08-17` and redirected there with a fresh `draft`
brief; confirmed resubmitting an existing week (`2026-08-03`) returns 409 with a working link back
to that campaign. 8 new tests added, full suite green (ruff, mypy --strict, pytest), verification
artifacts cleaned up afterward. Not committed yet — pending your review.

**Scope note:** the original request also mentioned "give Sarah an optional direction." The
deterministic brief generator (`GenerateWeeklyMarketingBrief`) has no hook for freeform steering —
it alternates between exactly two hardcoded campaign directions by ISO week parity, with "August 3"
literally hardcoded into one of them. Adding real direction support means extending that generator,
which is a content-generation change, not workflow plumbing — left out of this slice rather than
adding an input field that would silently do nothing.

## Recommendation (superseded — see "Update — 2026-08-06" above)

~~Build the "start next campaign" workspace route next.~~ Done. Phase 1 of the phased roadmap is
now complete. Next candidates, in priority order per the "recommended progression" north star in
`STATUS.md`: (1) manual publication records — the smallest remaining piece before real campaigns
can be run and learned from; (2) Phase 2 polish (attention queue, campaign history richness,
friendlier presentation) — lower priority since it's UX polish, not new capability.

## Update — 2026-08-06: full ChatGPT/Codex history received, acceptance test closed out

The complete raw ChatGPT/Codex conversation behind this project (from initial repo review through
every implementation slice) was pasted into the Claude session. Key facts this added:

- **ChatGPT/Codex is usage-limited until 2026-08-08 5:10 PM.** That's the operational reason
  Claude is now the working tool — not a permanent switch. `STATUS.md` at the repo root exists
  specifically so either tool can resume from it.
- The August-10 preview acceptance test that ChatGPT's last message asked the user to run
  end-to-end was **already executed directly by Claude** in this same session (see the 2026-08-05
  "later" entry in `STATUS.md`) — all criteria passed, and the unrelated `.pytest_tmp` git-tracking
  defect found along the way was fixed in commit `618cf35`. That loop is closed; no need to repeat it.
- Confirmed provenance: the repo was originally scaffolded by Codex in a temporary checkout
  (`review_checkout`) before being correctly reimplemented directly in this permanent repository —
  explains why early commit history reads as a full rebuild rather than a migration.
- An OpenAI API key was briefly pasted into the ChatGPT chat during setup and was correctly flagged
  as compromised and revoked immediately; no further action needed, noted for the record only.

### Product-agnosticism — now an explicit, testable goal

Confirmed as a core architectural requirement, not just a nice-to-have: the workflow engine
(company/employee/client/task/approval/asset/preview/history abstractions) must stay generic;
book-specific facts (age range, editions, Amazon URL, review quote) belong in the *Jordan and the
Fosters* client profile, not in shared application code.

**Acceptance test for this goal**: onboard a second, deliberately different client — *Theater
Rehearsal Web App* is the designated candidate — using only new client profiles, campaign
configuration, assets, and validation policy. If that requires changing the core workflow engine,
the abstraction isn't done yet. Explicitly **not** to be attempted until the Jordan workflow is
fully repeatable (i.e., after Phase 1 of this doc is complete) — premature generalization risks
weakening the working slice.

### New enhancement backlog items (not yet started, not yet prioritized against Phase 1–5 above)

- **ROI/performance measurement framework** — a full methodology for Morgan (cost/ROI/ROAS/time-ROI
  formulas, attribution-confidence levels, baseline/incremental sales, campaign scorecard format) is
  now specified in [`docs/roi-framework.md`](roi-framework.md). This substantially deepens Phase 3
  item 11 ("hire Morgan") — Morgan's first deliverable should follow that framework directly rather
  than being designed from scratch when the time comes.
- **Community engagement loop** (new roles, all deferred): a *Community Manager* to classify and
  draft responses to comments (question/compliment/complaint/spam/etc.), with sensitive categories
  always requiring human review; a *Voice of Customer* function to convert comments into structured,
  deduplicated feedback with frequency tracking; feeding into existing *Product Manager* /
  engineering / QA roles for an evidence-based feature pipeline. Hard rule carried over: **a single
  comment must never automatically trigger a code change, public promise, roadmap commitment, price
  change, or published response** — agents recommend, the human decides.
- **Demo Producer** (new role, explicitly deferred until after the second-client agnosticism test):
  given structured instructions plus a controlled demo environment, automates a browser through a
  workflow, captures video/screenshots, and assembles a narrated demo/tutorial video — kept in draft
  until approved. Requires its own safety controls (dedicated demo account, masked secrets, fixed
  viewport, no uploads/destructive actions without confirmation, fails loudly if an expected UI
  element doesn't appear rather than silently recording the wrong thing). Flagged as useful beyond
  marketing (QA evidence, release walkthroughs, sales demos) but scoped as one product-agnostic
  capability, not a marketing-only feature.
- **Publishing ("promote to live")** — clarified as three deliberate tiers, to be built in order,
  each usable as a permanent fallback for the next: (1) **manual handoff** — show approved copy/asset,
  provide a copy button, let the user publish by hand and record the resulting URL; (2) **assisted
  publishing** — the system opens/pre-fills the platform's own composer, human clicks the final
  publish action; (3) **direct publishing** — dedicated per-channel adapters (`WebsitePublisher`,
  `FacebookPublisher`, etc.) act on an immutable, checksum-bound "release package," never on raw
  LLM output. Required safeguards for tier 3: exact-destination/account binding, checksum-bound
  content and asset identity, per-channel permission, explicit human publish confirmation, no silent
  retries (duplicate-post risk), idempotency keys where supported, a publication log, and no agent
  ability to expand its own approved audience or destination list.

  **Superseded for Facebook and the website, 2026-08-06**: the owner explicitly asked to skip ahead
  of the "gated on repeated manual use" default — see the "Facebook Page auto-publish" and "SSL
  fixed, site rebuild approved, website auto-publish implemented" entries in `STATUS.md` for the
  full decision trail. `FacebookPagePublisher` and `WebsitePublisher` (tier 3, both) are
  implemented, approval-triggered, and include every required safeguard above (checksum-bound
  idempotent `PublicationRecord` per destination, no auto-retry, explicit human publish
  confirmation via the package-approval click itself, per-destination asset opt-in). The website's
  "CMS/upload-path discovery" blocker resolved to a git-based rebuild once the CEO approved
  converting the site off its proprietary drag-and-drop builder. Instagram and TikTok adapters
  remain unbuilt and individually blocked (public image hosting and platform audit respectively) —
  Phase 5's original priority and manual-use gate still stands for those.
- **GitHub Pages hosting — answered, not a backlog item**: Pages can serve static exports only
  (read-only dashboard, campaign previews, approved public pages) with an explicit human-approved
  export step; it cannot run the interactive Python workspace (approvals, generation, checksummed
  writes). If a public static presence is wanted later, add an explicit export/release step rather
  than trying to deploy the workspace itself.
