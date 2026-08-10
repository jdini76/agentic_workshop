# Agentic Workshop — Status Log

## 2026-08-06 (latest) — Claude Code session — SSL fixed, site rebuild approved, website auto-publish implemented

**Two things happened this session, in order: a real production outage got found and fixed, then a
second major feature (website auto-publish, Phase 2 of autonomous publishing) got built on top of
that discovery.**

### The SSL certificate had been expired for about a year

While investigating the site for the website-publishing work below, `jordanandthefosters.fun`
turned out to be completely unreachable in a real browser — its SSL certificate expired
2025-08-12, roughly a year ago. Every real visitor was hitting a hard "connection not private"
interstitial, not a soft warning. This was **found and fixed this session**, independent of
anything else: a new Sectigo multi-domain SAN certificate now covers `jordanandthefosters.fun`,
`rehearselines.com`, and `joeschmoepublishing.com` (confirmed via direct TLS handshake, not just
"cPanel says installed"). One gap remains open: the cert doesn't include `www.` as a SAN, so
`https://www.jordanandthefosters.fun` still fails — you have a Namecheap support ticket open for
this; the apex `https://jordanandthefosters.fun` (no `www`) works correctly in the meantime.

### The site is getting rebuilt as a plain, git-managed static site

Investigating the site also surfaced that it's built with a proprietary drag-and-drop "Website
Builder" bundled with the host, not hand-editable files — meaning autonomous publishing can't just
write into `public_html`. You reviewed this tradeoff and explicitly approved converting it: backed
up the full cPanel account first, accepted the risk given low traffic. This session's Phase 2 work
below is the mechanism for that conversion and its ongoing autonomous updates.

### Website auto-publish (Phase 2) — implemented, same governance rules as Facebook

Approving Casey's content package now attempts to publish to **both** Facebook and the website,
independently — one destination's failure or missing configuration never blocks the other, and
each gets its own `PublicationRecord` and its own "Retry publish" action. Same rules you set for
Facebook apply here: approval is the publish trigger (no separate confirmation), and each week's
approved website draft **replaces** the homepage's current pitch copy rather than accumulating as a
feed — you confirmed both of these explicitly this session.

**How it publishes, mechanically:** `WebsitePublisher` renders the homepage locally, commits it to
a git working copy, and pushes to a GitHub repository — the site's new real source of truth — over
HTTPS with a token (no SSH needed). It then calls cPanel's own **Git™ Version Control** feature via
its UAPI (also no SSH needed — a plain HTTPS API surface) to pull and deploy that commit. Non-weekly
content (author bio, reviews, contact links) is carried over from what's live today, verbatim,
stored in a new resource file — not regenerated or newly sourced by Casey.

**Quality gate:** full suite green (all tests pass, `ruff check .` clean, `mypy src --strict` clean
on 64 source files, up from 61). 33 new tests across `test_website_publisher.py` (18, including
every normalized error path and a check that the GitHub token never leaks into an error message),
extended `test_publish_content_package.py` (independent per-destination success/fail/skip and
idempotency), and a new `test_publish_retry_workspace.py` (3, covering the retry route's
platform-scoped confirmation-nonce replay protection). **Not committed yet — pending your review**,
same as the still-uncommitted Facebook work from earlier this session.

### Setup required from you before any real website deploy can happen

1. Create a GitHub repository for the site's source (public is the simpler default — sidesteps
   cPanel's private-repo auth being untested; say if you'd rather go private).
2. Generate a GitHub fine-grained PAT scoped to just that repo (`contents: write`) → `GITHUB_TOKEN`.
3. In cPanel → **Git™ Version Control**, create a repo pointing at that GitHub clone URL. Point its
   Pull Directory at a **throwaway staging path first** — not `public_html` — until the rendered
   output is verified; the live site should never be touched unreviewed.
4. Generate a cPanel API Token (Security → Manage API Tokens) → `CPANEL_API_TOKEN`.
5. Check Security → SSH Access and tell me if it's enabled — a simpler direct-push topology exists
   as a fallback if so, currently unused since GitHub+UAPI works either way.
6. Do one manual "Deploy HEAD Commit" click in the cPanel UI first, to confirm this cPanel version
   actually honors `.cpanel.yml` deployment tasks, before relying on the automated path.
7. Add the remaining values to the repo-root `.env`: `GITHUB_REPO`, `CPANEL_USERNAME`,
   `CPANEL_HOST`, `CPANEL_GIT_REPO_NAME` (full list and format in `docs/model-adapters.md`).
8. Run `live-smoke-website-publish` once against the **staging** Pull Directory before trusting the
   automated path.
9. Add `"website_auto_publish"` to the cover asset's `approved_uses` in
   `resources/client-assets/jordan-and-the-fosters.v1.json` when ready for image-inclusive publishes.
10. Once staging looks right, repoint the Pull Directory at `public_html` yourself.
11. **Honest caveat carried over from the plan, not resolved this session:** the exact cPanel UAPI
    function names used (`VersionControlDeployment::create`/`::retrieve`) are the standard surface
    for this feature but weren't confirmed against this account's live API — check that account's
    self-documented `/execute/` endpoints before the first real automated deploy.

## 2026-08-06 (earlier) — Claude Code session — Facebook Page auto-publish implemented

**This is a deliberate scope change you asked for, not autonomous drift.** After the workflow above
was fully click-through-verified, you said *"I don't want to do anything manually"* and, when asked
how automated publishing should be, chose fully autonomous — then immediately clarified: *"I am not
saying no approvals at all. Just after I agree to publish. It's pretty clear I am ok to publish."*
Net effect: **approving Casey's content package is now itself the publish trigger for Facebook.**
There is no second "click to publish" step. This matches a full plan you approved via plan mode
(saved at the time as `bubbly-sauteeing-pebble.md`); implementation is complete, not partial.

**What's new:** clicking "Approve Casey's package" in the workspace now also attempts one Facebook
Page post — a photo with caption if an eligible, approved cover asset exists, otherwise a text-only
post. This is the direct-publishing tier (3) that `docs/marketing-workflow-status.md`'s publishing
backlog entry described as "gated on repeated manual use first" — that gate is the one you just
explicitly lifted, for Facebook only.

**Explicitly out of scope, not silently dropped** (confirmed via live investigation, not assumed):
- **Instagram** — Meta's Graph API requires the image already hosted at a public HTTPS URL; this
  app only ever binds to `127.0.0.1`. Blocked until a public image host exists.
- **TikTok** — unaudited API clients are restricted to private (`SELF_ONLY`) posts until TikTok
  runs its own audit, on a timeline nobody here controls. Not meaningfully buildable as "autonomous
  public posting" yet regardless of code.
- **Website** — no CMS, updated by hand via cPanel/FTP; no content API exists to automate against.
  Needs its own separate investigation into the site's actual file layout first.

**Safety net (your explicit request, default on):** publish failures never auto-retry — Facebook's
Graph API has no idempotency key, so retrying a timed-out request risks a double-post, which is
worse than a delayed one. A failed attempt is recorded and surfaced in "What needs your attention"
with a plain-language reason; a **"Retry Facebook publish"** button lets you retry it deliberately
once you've checked Facebook yourself. Re-approving identical content, or a server restart mid-post,
can never double-post the same package to the same destination — publication records are keyed by
package ID + content checksum + destination.

**Graceful degradation if you haven't set up credentials yet:** the workspace does not fail to
start. Every approval instead records a clear "Facebook credentials are not configured yet" skip
until you complete the setup below.

**To disable auto-publish entirely** (approvals go back to not publishing anything): set
`AUTO_PUBLISH_ENABLED=false` in the repo-root `.env`. Default is enabled.

### Setup required from you before any real post can happen

Code is done and tested; these are the manual steps only you can do:

1. Create a Meta for Developers **Business app** with the **Pages API** product added.
2. Get the numeric **Page ID** for the Jordan and the Fosters Facebook Page.
3. Generate a **Page Access Token** — a System User token via Business Manager is recommended over
   one derived from your personal login, since the latter can be silently invalidated by your own
   password changes.
4. Confirm your account is admin/developer/tester on **both** the App and the Page — this avoids
   Meta's App Review process entirely for self-managed posting (no external audit needed, unlike
   TikTok).
5. Add to the repo-root `.env` (never commit this file):
   ```
   FACEBOOK_PAGE_ID=<the page id>
   FACEBOOK_PAGE_ACCESS_TOKEN=<the token>
   ```
6. Run the live smoke test once, with real credentials, before trusting the automated path:
   ```bash
   agentic-workshop live-smoke-facebook-publish "Testing the new publishing pipeline." \
     --confirm-live-post --i-understand-this-posts-publicly
   ```
   This posts for real and visibly on the live Page — confirm the post actually appears before
   relying on auto-publish for a real campaign.
7. When you're ready for posts to include the cover image, add `"facebook_page_auto_publish"` to
   the front cover derivative's `approved_uses` array in
   `resources/client-assets/jordan-and-the-fosters.v1.json`. Until you do this, approved packages
   still publish as text-only posts (not blocked, just no image).
8. No automated token renewal exists yet — periodically confirm the token hasn't been silently
   invalidated by a Meta security event.

### Quality gate

7 new files, 9 modified files. Full suite green: 154 tests pass (11 new: `test_facebook_publisher.py`,
`test_publish_content_package.py`), `ruff check .` clean, `mypy src` (strict) clean on all 61 source
files. One stale real artifact fixed along the way: `artifacts/content-packages/jordan-and-the-fosters-2026-08-03-content.json`
(and its `.md` rendering) still carried the old "will not be published automatically" wording from
before this feature existed — hand-edited in place rather than regenerated, since regenerating would
have reset its real `approved` state. **Not committed yet — pending your review.**

## 2026-08-06 (later) — Claude Code session — "start next campaign" implemented

Built the last remaining Phase 1 gap: `GET`/`POST /campaign/new` in the workspace, backed by a new
`StartNextCampaign` application service. Accepts any day within a week, normalizes to that week's
Monday, rejects duplicate weeks (409 + link to the existing campaign). Verified live against the
real repo: week `2026-08-21` correctly normalized to and created `2026-08-17`; resubmitting
`2026-08-03` correctly rejected as a duplicate. Verification artifacts cleaned up afterward — only
the real Aug 3 / Aug 10 campaigns remain.

**Phase 1 (finish the weekly campaign workflow) is now fully complete** — the entire loop (start →
Sarah approve/revise → Casey generate/approve/revise → preview generate) is operable from the
browser with zero CLI commands needed.

8 new tests added (`tests/test_next_campaign_workspace.py`). Full suite, ruff, and mypy --strict
all clean. **Not committed yet** — changes are local only, pending review.

**Scope decision, not a bug**: "give Sarah an optional direction" from the original request was
deliberately left out. The deterministic brief generator has no hook for freeform steering — it
alternates between two fully hardcoded campaign directions by week parity. Wiring in real direction
support is a content-generation change, not workflow plumbing; didn't want to add an input field
that would silently do nothing. Full detail in `docs/marketing-workflow-status.md`.

**Suggested next step**: manual publication records (Phase 3, item 9) — the smallest piece needed
before running real campaigns produces anything learnable.

### Second real-browser bug found and fixed the same day: preview images 404

User continued reviewing the workspace and reported the Aug 10 preview's cover image missing when
opened via "View campaign preview." Root cause: the preview page's own HTML uses a relative image
path (`assets/cover.png`) written for the case where the file sits on disk next to its own
`assets/` folder (the standalone `campaign-preview` CLI output). The workspace's "View campaign
preview" link pointed at `/campaign/<week>/preview` with **no trailing slash**, so the browser
resolved that relative path one directory level too shallow
(`/campaign/<week>/assets/...` — 404) instead of the actually-registered
`/campaign/<week>/preview/assets/...` (200). Same root-cause *category* as the Referrer-Policy bug
earlier today: something only a real browser doing real relative-URL resolution would surface — not
curl (which was given exact absolute paths in every check I ran) and not caught by any existing
test (which also only ever checked exact absolute paths).

**Fix**: added the trailing slash to the one link that pointed at the preview
(`presentation/workspace.py`, "View campaign preview"). No change to routing or asset-serving
logic — `/campaign/<week>/preview` and `/campaign/<week>/preview/` already routed identically
server-side; only the outgoing link text was wrong. Verified end-to-end against the real Aug 10
preview via curl (simulating exactly what the browser would request) before and after. Added a
regression assertion checking the href includes the trailing slash. Aug 3's preview is still
`unverified` (never regenerated) so this specific bug wasn't independently visible there yet, but
the fix is generic and will apply once it is.

**Pattern worth remembering**: this is the second bug in one day that only a real human clicking in
a real browser found, after this feature had gone through my own automated testing, curl
verification, and a full test suite — all passing the whole time. Automated checks proved the
*server-side* logic was correct; they couldn't prove the *browser* would resolve URLs the way the
HTML assumed. Real manual click-through remains worth doing even when everything else is green.

### Real-browser bug found and fixed the same day

The user manually clicked "Generate Sarah's draft" in real Chrome and hit `403 Invalid request
origin` — reproducible, not a fluke. Root cause, confirmed with a temporary diagnostic log: Chrome
sends the literal `Origin: null` on same-origin form POSTs when the page's `Referrer-Policy` is
`no-referrer`, which every page in this workspace was setting. The Origin equality check then
(correctly) rejected it as indistinguishable from a forged cross-origin request.

**Fix**: changed `Referrer-Policy` from `no-referrer` to `same-origin` in `local_workspace.py`'s
shared `_headers()` — same privacy guarantee (referrer still never leaves the origin, including to
the one external Amazon link), but Chrome computes a real same-origin `Origin` header correctly.
Did **not** loosen the Origin check itself to accept `"null"` — that would reopen the exact
cross-origin/sandboxed-iframe CSRF hole the check exists to close, since forged requests send the
same literal value. Added a regression test proving a forged `Origin: null` POST is still rejected
after the fix. User re-tested in real Chrome after the fix and confirmed it works.

**This likely affected every write action in the workspace, not just the new one** — approve,
revision, Casey generation, preview generation all share the same `_post()` Origin check and the
same global headers. It's plausible this was silently blocking real end-user clicks the whole time
and was only ever exercised successfully via curl or automated browser tooling that doesn't
replicate this specific Chrome/Referrer-Policy interaction. Worth keeping in mind: **prior "visually
verified in browser" claims in this project's history were mostly Codex's own automated in-app
browser tool, not a human clicking in a real desktop browser** — this is the first confirmed real
end-user click-through of a workspace mutation, and it found something the automation missed.



## 2026-08-06 — Claude Code session — full ChatGPT/Codex history received

The complete raw ChatGPT/Codex conversation for this project (repo review → vertical slice →
client onboarding → OpenAI integration → asset pipeline → interactive workspace → preview
lifecycle) was pasted in and reconciled. Nothing it described conflicted with what's already
verified in this log — it's the origin story for everything above. Two operationally important
facts:

- **ChatGPT/Codex is usage-limited until 2026-08-08 5:10 PM** — that's why Claude is doing this
  work right now, not a permanent switch. Keep updating this file either way.
- The acceptance test ChatGPT's last message asked for (regenerate the Aug 10 preview, verify it
  goes `unverified → current`, Aug 3 stays `unverified`) was already completed by Claude earlier
  the same day — see the "2026-08-05 (later)" entry below. No need to repeat it.

New reference docs added: [`docs/roi-framework.md`](docs/roi-framework.md) (Morgan's future
measurement methodology) and an expanded backlog section in
[`docs/marketing-workflow-status.md`](docs/marketing-workflow-status.md) covering community/feedback
loop roles, the Demo Producer concept, three-tier publishing safeguards, and the explicit
product-agnosticism acceptance test (onboard Theater Rehearsal Web App as client #2 without
touching the core engine — deliberately not attempted until Phase 1 here is complete).

No code changed this entry — documentation and reconciliation only.

## North star (from ChatGPT, reconciled 2026-08-06 — consistent with the phase roadmap below, not a change of direction)

1. Finish the local Sarah → Casey → preview workflow.
2. Add manual publication records.
3. Use that process for several real campaigns.
4. Identify which destinations you repeatedly use.
5. Build the first assisted or direct adapter for the most repetitive destination.
6. Keep manual publishing available as a fallback.

Steps 1–2 = Phase 1 + Phase 3 item 9 below. Steps 3–6 = Phase 5's "don't automate distribution
until repeated manual use tells you which destination is worth it" made concrete. Don't build a
publishing adapter speculatively — wait for the evidence from step 3–4.

**Read this file first in any new session (Claude or ChatGPT) before doing project work.**
Keep entries short. Newest first. Update at the end of any session that changes status or makes a
decision — whichever assistant you're using that day.

---

## 2026-08-05 (later) — Claude Code session — acceptance test executed

Ran the exact acceptance test ChatGPT specified (regenerate the legacy Aug 10 campaign preview
through the workspace UI) — actually executed it against a live local server, not just described
it. **All criteria passed:**

- Aug 10 preview status: `unverified` → `current`.
- "View campaign preview" became available; preview HTML confirmed served (`/campaign/2026-08-10/preview`, HTTP 200).
- Preview page title: "Local campaign preview — not published"; body explicitly states "This local
  artifact has no publish, upload, post, send, or external-delivery action." Nothing was published
  or transmitted anywhere.
- Website/social copy in the preview matches Casey's approved package ("When Trust Takes Time" /
  "Trust can grow through patience, kindness, and time").
- Approved cover image (`jordan-and-the-fosters-front-cover-marketing-1600h.v1.png`) is referenced
  correctly in both the website and social sections of the preview.
- Aug 3 campaign untouched, still `unverified` — regeneration correctly scoped to Aug 10 only.
- Attention queue updated from "Regenerate the legacy campaign preview…" to "Review the current
  local campaign preview; nothing has been published."

**Tooling note, not a product bug:** the first two attempts, driven through the automated Browser
pane (a click-through UI test), failed with "The preview request could not be verified" (HTTP 403).
Root cause was **not** the app — replicating the identical request with `curl` (explicit `Origin`
header, session cookie, and all hidden form fields) succeeded immediately (303 → success). The
embedded browser tool isn't sending a matching `Origin` header on this same-origin POST, which the
app correctly rejects per its CSRF/origin-check design (`local_workspace.py` line ~470). Worth
knowing if browser-driven UI testing of this app is attempted again in this environment — a raw
HTTP client (curl/requests) is currently the reliable way to exercise write routes end to end here.

**Fixed (commit `618cf35`):** `.pytest_tmp/` (228 files, accidentally committed in `9caefef`) is now
untracked and gitignored. Local temp files left in place, only git tracking changed — no history
rewrite, no force-push needed. Tests re-run clean afterward.

## 2026-08-05 — Claude Code session

**What's actually done (verified against source, not just commit messages):**
- Full Sarah → CEO-approve → Casey → CEO-approve → preview chain works end to end via the local
  workspace (`agentic-workshop workspace`), not just the CLI.
- Casey review controls, deterministic Casey generation, and campaign-preview generation are all
  implemented (commits `b941b03`, `141a797`, `ef3103f` — `ef3103f` is current HEAD).
- Preview freshness is checksum-bound (`combined_generation_checksum`), not folder-existence-based —
  already ahead of where the last ChatGPT status update believed things were.
- Approved client asset pipeline (checksum-verified manifest, metadata-clean derivative for
  *Jordan and the Fosters*' cover) is in place.
- Optional OpenAI-backed generation exists behind `--generator openai --confirm-paid-call`;
  deterministic generation remains the default. One live baseline evaluated and approved
  (not published) — see `docs/evaluations/model-backed-baseline-2026-08-03.md`.

**Confirmed gap:** no "start next campaign" route in the workspace UI. New campaign weeks can only
be started from the CLI (`agentic-workshop brief <client> --week-of <date>`), never from the
browser. This is the one piece separating "operate mostly from the browser" from "operate fully
from the browser."

**Recommended next step:** build the "start next campaign" workspace route (choose client, choose
week, optional direction for Sarah, generate the draft, prevent duplicate weeks). See
`docs/marketing-workflow-status.md` for the full phase roadmap and reasoning.

**Not yet touched this session:** Phase 2 (attention queue, campaign history polish), Phase 3
(manual publication/analytics records, "Morgan" the performance analyst), Phase 4 (model-assisted
revision, "Riley" research support), Phase 5 (distribution).

**Scope note:** `household-brain-mcp` (a separate Gmail/Calendar household-automation MCP server,
at `C:\Users\joe\Claude\Projects\Household Assistant`) is **not** part of Agentic Workshop — an
earlier ChatGPT handoff conflated the two projects; corrected 2026-08-05.

---

*(Add new entries above this line, newest first. Full detail lives in `docs/marketing-workflow-status.md` and `docs/roadmap.md` — this file is the fast-scan summary for picking the project back up.)*
