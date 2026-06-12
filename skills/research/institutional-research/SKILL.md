---
name: institutional-research
description: Run source-grounded institutional research workflows.
version: 0.1.0
author: Nous Research, Hermes Agent
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [research, due-diligence, finance, citations, evidence, analysis]
    category: research
    related_skills: [osint-investigation, domain-intel, comps-analysis, stocks]
---

# Institutional Research Skill

Use this skill to raise the rigor of business, market, financial, policy, and
due-diligence research. It does not provide investment, legal, tax, accounting,
or compliance advice. It turns open-ended research into a source-grounded,
auditable workflow with explicit evidence, freshness checks, and review gates.

## When to Use

- The user asks for market research, company research, sector mapping, or a
  competitive landscape.
- The user asks for a memo, briefing, thesis, diligence note, earnings summary,
  buyer list, investment screen, or research pack.
- The answer depends on current facts, financial data, public filings,
  management commentary, policy changes, prices, transactions, or news.
- The work product needs citations, source quality, assumptions, or a clear
  audit trail.
- A finance skill such as `comps-analysis`, `dcf-model`, `lbo-model`,
  `3-statement-model`, or `stocks` is being used and the raw inputs need
  defensible sourcing.

Do not use this skill for pure opinion, quick definitions, coding research
that is better handled by `codebase-inspection`, or academic literature review
that is better handled by `arxiv` and `research-paper-writing`.

## Prerequisites

Use the best available Hermes tools and installed skills:

- `web_search` and `web_extract` for current public web sources.
- `terminal` for local scripts, tabular checks, and document generation.
- `read_file` for user-provided filings, decks, spreadsheets, PDFs, notes, and
  local knowledge bases.
- `osint-investigation` for public-records due diligence.
- `stocks` for quick read-only market quotes when installed.
- Finance modeling skills for Excel outputs, with this skill providing the
  sourcing, evidence, and review discipline.

If a premium database, MCP server, terminal, internal data room, or user-uploaded
source is available, prefer it over general web search for the facts it covers.
If access is unavailable, state the limitation and use the best public source.

## How to Run

Start by converting the user's request into a research brief:

```text
Question:
Decision or audience:
Required output:
Time horizon:
Geography:
Entities:
Known sources:
Unavailable sources:
Materiality threshold:
Deadline:
```

Then run the procedure below. For small tasks, compress the workflow but keep
the source hierarchy, freshness check, and citation discipline.

## Quick Reference

Source hierarchy:

1. User-provided primary documents and internal systems.
2. Regulator, exchange, court, government, and statutory records.
3. Company disclosures, investor relations, earnings releases, and transcripts.
4. Licensed or institution-grade databases available through MCP or local tools.
5. Reputable trade, news, academic, and industry sources.
6. General web sources, blogs, and social media.

Every important claim should carry:

```text
Claim | Source | Source type | Date | URL/path | Confidence | Notes
```

Default confidence labels:

- `high`: primary source or verified database, directly supports claim.
- `medium`: reputable secondary source or triangulated public sources.
- `low`: indirect, stale, incomplete, single-source, or inferred evidence.

## Procedure

### 1. Scope the Work

Clarify the decision being supported before collecting data. A market map for
screening, a board memo, and an earnings note need different depth, sources,
and outputs.

Write down:

- The exact research question.
- The intended audience.
- The expected deliverable: brief, memo, table, model input pack, deck outline,
  diligence checklist, timeline, or source dossier.
- The freshness requirement: latest quarter, trailing twelve months, last 30
  days, current as of today, or a fixed historical date.
- Known exclusions and unavailable data.

### 2. Build the Source Plan

Choose sources before searching broadly. Use the hierarchy in Quick Reference.

For finance and company research, prioritize:

- SEC EDGAR, SEDAR+, Companies House, exchange filings, or local equivalents.
- Investor relations pages, earnings releases, presentations, and transcripts.
- Official guidance, annual reports, quarterly reports, and audited statements.
- Available premium databases or MCP sources for market data, consensus,
  ownership, private company data, and transaction data.
- Reputable news and trade publications for context and event chronology.

For each source class, record expected coverage, access limits, date range, and
known reliability issues.

### 3. Verify Freshness

For changing facts, never rely on model memory. Establish the current date and
compare it to the source date.

Check:

- Publication date, filing date, effective date, and period covered.
- Whether a newer filing, release, correction, restatement, or transcript exists.
- Whether values are point-in-time, fiscal-period, calendar-period, or trailing
  period figures.
- Whether market prices, share counts, rates, and FX assumptions are as-of a
  specific date.

If source dates conflict, prefer primary filings and explicitly note the
conflict.

### 4. Collect Evidence

Create an evidence matrix before writing conclusions. Use
`templates/evidence-matrix.md` as the default structure.

Rules:

- Tie each key claim to at least one source.
- Use primary sources for factual claims whenever available.
- Use multiple independent sources for disputed or high-impact claims.
- Separate reported facts from assumptions and analyst interpretation.
- Capture exact dates and source locations, not just domain names.
- For tables and figures, include source lines under the table or figure.

### 5. Analyze With Fit-for-Purpose Frameworks

Pick the framework that matches the decision:

- Competitive landscape: segments, buyer needs, players, differentiation,
  pricing, distribution, barriers, substitutes.
- Company profile: business model, segments, revenue drivers, margins, risks,
  ownership, management, recent events.
- Earnings or event update: expectations, beat/miss, drivers, guidance, estimate
  changes, thesis impact, follow-up questions.
- Market sizing: definition, boundaries, units, top-down, bottom-up, sources,
  assumptions, sensitivity.
- Diligence: red flags, open questions, source gaps, dependency risks, legal or
  compliance constraints for human review.
- Investment or valuation support: thesis, variant view, catalysts, risks,
  comparable metrics, assumptions, scenarios, and source-backed model inputs.

Do not force a financial model when the question is qualitative. Do not force a
long memo when a sourced table answers the decision.

### 6. Red-Team the Draft

Before finalizing, challenge the output:

- What would change the conclusion?
- Which claims are single-source?
- Which numbers are stale, rounded, restated, or non-comparable?
- Where are definitions inconsistent across sources?
- Are there alternative explanations for the observed pattern?
- What is missing because the available sources are weak?
- Are any statements advice, recommendations, or regulated conclusions that
  should be framed for human review instead?

Downgrade confidence or add caveats when evidence is weak.

### 7. Deliver the Work Product

Match the output to the user's requested format. If no format is specified, use:

```text
Executive answer
Key findings
Evidence table
Analysis
Risks and caveats
Open questions
Sources
```

For financial models and spreadsheets:

- Every hardcoded input must have source, date, and unit metadata.
- Every derived metric should be a formula in the workbook, not a pasted result.
- Tables should state the period, currency, units, and as-of date.
- Market data should show quote date and data source.
- Assumptions should be visually and textually separated from historical facts.

For memos and reports:

- Use citations close to the claim they support.
- Put source/date lines under each material figure and table.
- Include a source appendix when the output is longer than a short brief.
- Label unsupported hypotheses as hypotheses.

## Pitfalls

- Starting with general web search when primary sources or databases are
  available.
- Mixing fiscal years, calendar years, LTM, quarterly, and point-in-time values.
- Treating consensus estimates, company guidance, and actual results as the same
  type of evidence.
- Using current market data with stale share counts or debt balances.
- Citing a page that cites another source instead of finding the original.
- Producing a polished narrative before building the evidence matrix.
- Hiding source gaps instead of making them reviewable.
- Presenting investment, legal, tax, accounting, or compliance decisions as if
  the agent can approve them.

## Verification

Before final response or file delivery, confirm:

- The source hierarchy was followed or deviations were disclosed.
- Current facts were checked against today's date.
- All key claims have source, date, and confidence.
- High-impact claims have primary or triangulated support.
- Tables and figures include source/date lines.
- Assumptions are separated from facts.
- Open questions and residual uncertainty are visible.
- The final answer does not overstate unsupported conclusions.
