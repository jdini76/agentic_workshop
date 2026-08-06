# ROI and performance-measurement framework

Design captured from ChatGPT/Codex planning on 2026-08-06, not yet implemented. This is the
intended methodology for "Morgan," the future performance analyst (Phase 3 of
`marketing-workflow-status.md`), and for ROI tracking generally. Recorded here so it survives
independently of chat history.

## Core principle

Every campaign should answer three questions:

1. What result are we trying to create?
2. How will we know whether it happened?
3. Was the result worth the money and time invested?

If repeated activity isn't producing meaningful attention, leads, sales, income, or learning,
Morgan should recommend changing it or stopping it — not just report numbers.

## Per-campaign measurement plan

Every campaign should declare, before or alongside publication:

- Business objective (primary + secondary)
- Expected result
- Measurement window
- Baseline (normal performance absent this campaign)
- Target
- Distribution cost (advertising, API/tool cost, contractor cost)
- Human time spent
- Attribution method

## Four metric levels

1. **Activity** — did the workshop complete the work? (campaigns created, drafts approved, posts
   published, time from brief to publication, review time, API/tool cost). Measures operational
   efficiency, not success.
2. **Attention** — did people notice? (visits, impressions, views, clicks, opens, new visitors).
   Leading indicator, not proof of revenue.
3. **Engagement** — did the right people care? (comments, shares, saves, replies, CTR, time on
   page, meaningful questions, signups). Distinguish meaningful engagement from superficial likes.
4. **Business results** — did it create value? (Amazon visits, orders, royalties, conversions,
   inquiries, leads, repeat buyers). Client-specific — a software client tracks downloads/upgrades
   instead.

## Cost and ROI formulas

```text
Campaign cost = advertising + API/tool costs + contractor costs + (optional) value of human time

Campaign contribution = attributable royalties - variable campaign costs

ROI = (attributable royalties - campaign cost) / campaign cost × 100

ROAS = attributable revenue / advertising spend   (advertising efficiency, distinct from ROI)

Time saved = estimated manual effort - actual review effort

Income per human hour = incremental income / active human time
```

When campaign cost is near zero, percentage ROI is misleading — report net income, royalties per
campaign, royalties per hour of human effort, cost per approved campaign, and cost per sale
instead.

## Attribution honesty

A sale after a post doesn't prove the post caused it. Every result needs an attribution level:

- **Direct** — a tracked action clearly connects to the campaign.
- **Strongly associated** — purchase followed a campaign-specific visit or code.
- **Correlated** — sales increased during the campaign window.
- **Unattributed** — sale occurred, source unknown.

Morgan must never present correlation as certainty. Improve attribution over time with:
campaign-specific/tagged links, redirect endpoints, landing pages, promo codes, "how did you hear
about this" responses, manual royalty-report imports, baselines, publication timestamps.

## Baselines and incremental results

Record normal performance (visits, clicks, sales, engagement, seasonal patterns) before judging a
campaign. Then:

```text
Incremental sales = campaign-period sales - expected baseline sales
```

Label this an estimate, not a fact, until attribution improves.

## Campaign scorecard (target shape for Morgan's weekly report)

```text
Campaign: <name>          Status: Published        Window: 7 days

Cost
- Advertising: $X | API: $X | Human review: N minutes

Results
- Visits: N | Clicks: N | Reported sales: N
- Estimated baseline sales: N | Estimated incremental sales: N
- Incremental royalties: $X

Engagement
- Comments: N | Meaningful comments: N | Shares: N

Assessment
- Attribution confidence: low/medium/high
- What worked, what changed vs. last campaign
- Recommendation: repeat / revise / stop
```

## Eventual ROI dashboard (Today's Work extension)

Revenue this month · campaign costs · estimated net contribution · human time invested · income
per human hour · best-performing campaign/channel · sales by campaign · attribution confidence ·
trend vs. baseline · cumulative Agentic Workshop operating cost. Should explicitly show "not
enough data yet" rather than manufacture a confident conclusion.

## Backlog items this implies

- **Measurement planning**: required campaign objective, baseline, target, measurement window,
  success event, attribution method — captured at brief time.
- **Cost tracking**: model-token costs, ad spend, tool subscriptions, contractor costs, human
  review time.
- **Results collection**: manual metric entry first (this is the near-term Phase 3 item); website
  analytics, platform metrics, royalty/sales imports, lead tracking, qualitative notes later.
- **Morgan**: compare results to goals, compute ROI/time-ROI, assess attribution confidence,
  identify winning themes/channels, recommend continue/revise/stop, feed evidence into Sarah's
  next brief.
