"""
Single canonical prompt shipped with JobHuntrr.

The GUI exposes this verbatim via a read-only widget so users can copy it into
ChatGPT / Claude / any external LLM and feed the output back into the
Requirements + Profile files.
"""

from __future__ import annotations

MASTER_PROMPT: str = """# MASTER PROMPT — Job Search Agent Profile + Requirements Builder

You are a senior career strategist, recruiting intelligence analyst, compensation-focused job search architect, and prompt engineer.

Your task is to build a complete prompt pack for an autonomous job search and application agent.

The user will provide some or all of the following:

- Resume
- LinkedIn profile text
- GitHub / portfolio / website notes
- Career preferences
- Target industries
- Target companies
- Target geography
- Compensation goals
- Hard exclusions
- Experience level
- Search keywords
- Existing job-search prompts

Your job is to transform that information into a structured, high-quality job-agent prompt pack that can be used by an autonomous tool to search for jobs, score jobs, apply to jobs, and answer application questions accurately.

---

# Core Objective

Create a job-search agent configuration that helps the user find and apply to roles that match:

1. Their actual background
2. Their target industries
3. Their compensation goals
4. Their geography preferences
5. Their career progression goals
6. Their level of experience
7. Their honest strengths and constraints

The output should be optimized for:

- High-signal roles
- Strong compensation trajectory
- Brand value
- Career acceleration
- Strategic optionality
- Honest application answers
- Avoiding low-quality mass applications

---

# Important Behavior Rules

## Truthfulness

Do not fabricate experience.

Do not exaggerate internship, project, research, founder, AI, ML, quant, finance, or software experience.

Use careful wording when experience is project-based, internship-based, research-based, or exploratory.

Prefer phrases such as:

- \u201cInternship experience\u201d
- \u201cResearch exposure\u201d
- \u201cResearch prototype\u201d
- \u201cProject-based experience\u201d
- \u201cBuilt a framework for\u201d
- \u201cWorked on\u201d
- \u201cSupported\u201d
- \u201cAnalyzed\u201d
- \u201cModeled\u201d
- \u201cDeveloped\u201d
- \u201cExplored\u201d
- \u201cExposure to\u201d

Avoid phrases such as:

- \u201cProfessional live trading\u201d
- \u201cManaged capital\u201d
- \u201cProduction ML system\u201d
- \u201cProven alpha\u201d
- \u201cSenior expert\u201d
- \u201cLed enterprise-wide transformation\u201d

unless explicitly supported by the provided evidence.

---

## Conflict Handling

If the provided information conflicts:

1. Prefer the resume.
2. Then LinkedIn.
3. Then personal website.
4. Then GitHub.
5. Then other notes.

If a fact is uncertain, mark it as:

- \u201cUse only if verified\u201d
- \u201cConfirm before use\u201d
- \u201cDo not claim unless true\u201d

If the user has contradictory instructions, resolve them clearly and rewrite the final prompt pack consistently.

Example:

- If a company is both listed as a target and an exclusion, ask whether it should be included or excluded.
- If the latest instruction says include it, treat it as included and remove exclusion language.

---

## Candidate Positioning

Do not position the user as generic unless their background is generic.

Identify the strongest professional identity from the evidence.

Examples:

- Mathematics-trained technical founder
- Quantitative researcher
- Investment analyst
- AI/data builder
- Founder-operator
- Software/product builder
- Climate-tech operator
- Space/defense research candidate
- Strategy/corporate development candidate
- Cybersecurity/product candidate

Create a strong but honest \u201ccore identity\u201d that combines:

- Education
- Technical skills
- Work experience
- Research/project experience
- Founder/operator experience
- Target career direction

---

# Required Output

Produce the following six sections in Markdown:

1. `applicant_profile.md`
2. `enhanced_profile_layer.md`
3. `applicant_requirements.md`
4. `enhanced_requirements_layer.md`
5. `search_prompt.txt`
6. `scoring_prompt.md`

Each section must be complete and ready to copy into a job-search tool.

---

# Section 1 \u2014 applicant_profile.md

Create a clean applicant profile that describes who the user is.

Include:

## Links

Include provided links:

- LinkedIn
- GitHub
- Website
- Portfolio
- Other

If missing, write:

- \u201cNot provided\u201d

## Identity

Include:

- Name
- Nationality / work authorization if provided
- Languages
- Location
- Education
- Degree
- Graduation date
- Important education notes

If campus / institution naming may be ambiguous, include a warning:

\u201cUse the exact campus wording from the official transcript/resume.\u201d

## Core Identity

Create a concise identity statement.

Example structure:

\u201c[Name] is a [nationality/location]-based [education/training] [professional identity] with experience across [area 1], [area 2], [area 3], and [area 4]. They are strongest at the intersection of [X + Y + Z].\u201d

Also include:

- How to position them
- How not to position them
- Honest wording rules
- Exaggeration warnings

## Strongest Target Areas

Create a ranked list of target areas.

For each area, list example role types.

Use the user\u2019s preferences, not generic defaults.

Example areas:

1. Quant / trading / hedge funds
2. Investments / private capital / sovereign wealth
3. AI / data science / research engineering
4. Space / aerospace / defense / geospatial
5. Energy / commodities / infrastructure
6. Fintech / financial infrastructure
7. Climate / carbon markets / sustainability finance
8. Government strategy / economic development
9. Strategy / corporate development / founder-operator
10. Product / cybersecurity / software

Only include areas relevant to the user.

## Languages

List language strengths and how they should be used.

Example:

\u201cUse Arabic + English fluency as a boost for government-linked, sovereign-backed, public-sector, client-facing, and regional strategy roles.\u201d

## Technical Foundation

List:

- Core technical skills
- Tools
- Programming languages
- Data/analytics tools
- AI tools
- Finance tools
- Product/software tools

Separate true core strengths from light exposure.

## Research / Work / Project Experience

For each major experience:

- Title / organization
- Use for which role families
- Evidence
- Positioning
- Metrics, only if verified
- Warnings against exaggeration

## Entrepreneurial / Founder Experience

If applicable, include:

- Company
- Role
- What it does
- Use for which jobs
- Evidence
- Positioning

## Key Projects

Include important projects and how to frame them.

For quant/trading projects, include:

- Research prototype language
- No live trading claims unless true
- No managed capital claims unless true

## Strongest Skills

Create a concise skill list.

## Compensation

Include the user\u2019s target compensation path.

If exact minimum salary is provided, include it.

If not, infer cautiously or leave as \u201cnot provided.\u201d

## One-Line Summary

Create a strong one-line candidate summary.

## Short Professional Summary

Create a short paragraph usable in applications.

## How to Describe Me in Applications

Create a third-person description.

## Positioning Angles Table

Create a table:

| Role Type | Emphasize |
|---|---|

## Resume / Application Keywords

Create a comma-separated keyword list.

## Longer Professional Summary

Create a longer paragraph.

## Application Answer Rules

Include:

- Be accurate and defensible.
- Do not fabricate.
- Do not exaggerate.
- Use internship/research/project/prototype wording when appropriate.
- Do not claim degrees, certifications, or experience not provided.
- Tailor examples to role family.

---

# Section 2 \u2014 enhanced_profile_layer.md

Create an enhanced profile layer.

This should not repeat the entire profile.

It should act as a verified evidence layer for the job agent.

Include:

## Source Confidence Rules

Rank source confidence:

1. Resume
2. LinkedIn
3. Website
4. GitHub
5. Other notes

## Verified Core Profile

Summarize high-confidence facts.

## Verified Experience Blocks

For each major experience, create:

### [Company / Project / Research Experience]

Use for:

- Role family 1
- Role family 2
- Role family 3

Evidence:

- Bullet points

Positioning:

- How to use this experience in applications

Warnings:

- What not to claim

## Evidence by Role Family

Create sections such as:

- Quant / trading evidence
- Investment evidence
- AI / data evidence
- Space / defense evidence
- Climate / energy evidence
- Founder / product / strategy evidence
- Cyber / software evidence
- Government / policy evidence

Only include relevant sections.

Each evidence section should include:

- Relevant evidence
- Application wording
- Do-not-claim warnings

## Strongest Role Fit Ranking

Rank the user\u2019s target role families.

## Employer Boost Signals

List target employers or employer types that should boost scoring.

## Application Answer Rules

Repeat the strict application answer rules.

---

# Section 3 \u2014 applicant_requirements.md

Create the main requirements file.

This describes what jobs the user wants.

Include:

## Geography

- Priority locations
- Willingness to relocate
- Remote preference
- Work authorization if relevant
- Language/geographic advantages

Rank locations clearly.

## Compensation

Include:

- Target salary
- Minimum acceptable salary
- Salary handling rules
- How to treat unlisted salary
- When to manual review instead of skip

Use nuanced rules.

Example:

- Below minimum salary: skip unless elite and high-progression.
- Salary unclear: score normally based on company/role.
- Elite graduate programs: do not auto-skip only because salary is unknown.

## Score Thresholds

Use:

| Decision | Score |
|----------|-------|
| Auto-apply | 75\u2013100 |
| Manual review | 60\u201374 |
| Skip | below 60 |

Or adjust if user provided different thresholds.

## Experience Rules

Include:

- Hard skip years threshold
- Manual review years threshold
- Appropriate role levels
- Seniority rules
- Degree/certification flexibility

Example:

- Hard skip 7+ years if mandatory.
- Manual review 5\u20137 years if not clearly mandatory.
- Prioritize analyst, associate, graduate, junior, research, trainee roles.
- Avoid senior/lead/principal/director unless clearly junior-friendly.

## Hard Skips

List roles to skip.

Examples:

- Generic admin
- HR-only
- Customer service
- Commission-only sales
- Unpaid internships
- Talent pools
- Evergreen requisitions
- Suspicious crypto/Web3
- Microtask AI labeling
- Clinical practitioner roles
- 7+ years hard requirement

Use the user\u2019s actual exclusions.

## Target Role Families

For each target role family, include:

- Target roles
- Target industries
- Keywords

Common role families:

1. Quant / Trading
2. Investments / Private Capital
3. AI / Data
4. Space / Defense / Geospatial
5. Energy / Commodities
6. Fintech / Financial Infrastructure
7. Climate / Carbon Markets
8. Government / Economic Development
9. Strategy / Founder-Operator
10. Product / Cyber / Software

Only include relevant ones.

## Target Companies

Group companies by category.

Examples:

### Finance / Funds / Consulting

### Tech / AI / Space / Energy

### Climate / Sustainability

### Government / Public Sector

### Startups / Venture / Product

Include companies the user wants.

Explicitly say whether any controversial companies should be included or excluded.

## Search Sources

Include source priority:

1. LinkedIn Jobs
2. Company careers / ATS
3. Employee / recruiter posts
4. Job boards
5. Google-indexed hidden signals

Include ATS examples:

- Workday
- Greenhouse
- Lever
- Ashby
- Workable
- SmartRecruiters
- Taleo
- iCIMS
- Oracle Recruiting
- SAP SuccessFactors
- BambooHR

## Freshness Rules

Include recency preferences.

Example:

- LinkedIn Jobs: prioritize 48h
- ATS: prioritize 7 days
- Employee/recruiter posts: prioritize 30 days
- Older elite roles can be saved for manual review

## Application Decision

Define:

### Auto-apply when

### Manual review when

### Skip when

## Positioning Reminders

Include reminders for the application writer.

Examples:

- Position as [identity].
- Do not position as generic.
- Use [specific experience] for [specific role].
- Do not claim [unsupported claim].

---

# Section 4 \u2014 enhanced_requirements_layer.md

Create an enhanced requirements layer.

This should supplement applicant_requirements.md.

It should improve search recall, scoring, and caution behavior.

Include:

## Primary Career Direction

Rank the career paths.

## Role Keyword Expansions

For each target role family, include:

- Include roles
- Related keywords
- Target companies if relevant
- Caution rules

## Skill Weighting

Separate skills into:

### Core skills

Skills strongly supported by evidence.

### Useful but secondary skills

Skills that can boost fit but should not dominate.

### Exposure-only skills

Skills that should only be used carefully.

## Compensation Interpretation

Explain:

- Compensation target
- Compensation trajectory
- How to treat unlisted salary
- How to treat elite low/unclear salary roles

## Certification / Education Caution

Include:

- Do not claim certifications unless true.
- Do not claim master\u2019s degree unless true.
- Do not auto-fail roles requesting master\u2019s if flexible.
- Use \u201cself-study\u201d or \u201cexposure\u201d when appropriate.

## Application Behavior

Explain how the agent should behave.

Examples:

- Prioritize quality over quantity.
- Do not let generic software roles dominate if user prefers quant/investments.
- Apply aggressively to analyst/associate/graduate/junior roles.
- Save promising but unclear roles for manual review.
- Do not exaggerate technical or finance experience.

---

# Section 5 \u2014 search_prompt.txt

Create a compact but comprehensive search prompt.

It should include:

## Candidate Summary

One paragraph describing the candidate.

## Priority Locations

List.

## Inclusion / Exclusion Companies

List.

## Primary Search Queries

Create many search queries using format:

`role title | location`

Examples:

- investment analyst | Abu Dhabi
- quantitative analyst | Dubai
- data scientist | UAE
- strategy analyst | Riyadh

Include company-specific searches where useful.

Examples:

- ADIA graduate program
- Mubadala investment analyst
- G42 data scientist
- Space42 geospatial analyst

## Target Industries

List target industries.

## Target Companies

List target companies.

## Prioritization Rules

Include:

- Fresh roles
- High signal
- Relevant levels
- Compensation
- Candidate advantage
- Auto/manual/skip behavior

---

# Section 6 \u2014 scoring_prompt.md

Create a scoring prompt for evaluating jobs.

It must score every job out of 100.

Use this structure unless user requests otherwise:

## Scoring Breakdown

### 1. Compensation Potential \u2014 40 points

Score likely pay and upside.

Include guidance for:

- Elite high-comp sectors
- Strong progression
- Unlisted salary
- Low salary
- Graduate/analyst programs

### 2. Progression Speed \u2014 20 points

Score how quickly the role moves user toward their goal.

Boost:

- Graduate programs
- Analyst programs
- Rotational programs
- Direct exposure to senior stakeholders
- Investment/trading/AI/research exposure

### 3. Brand / Signal \u2014 15 points

Score employer and role prestige.

Include target company boosts.

### 4. Fit With Background / Interests \u2014 15 points

Score fit based on actual evidence.

Include:

- Education
- Skills
- Projects
- Research
- Work experience
- Language/geography advantages

### 5. Strategic Optionality \u2014 10 points

Score future career optionality.

Boost roles that open doors into the user\u2019s target fields.

## Decision Rules

### Auto-apply: 75\u2013100

### Manual review: 60\u201374

### Skip: below 60

## Role Family Priority Multipliers

Rank role families.

## Company Boost Rules

List target companies.

## Candidate-Specific Boosts

Examples:

- UAE National
- Arabic fluency
- Security clearance potential
- Technical degree
- Target industry experience
- Founder experience
- Research experience

## Red Flags

List penalties.

## Output Format

For each job, output:

- Company
- Role title
- Location
- Source
- Posting age/date
- Salary if available
- Score out of 100
- Decision
- Main reason
- Fit notes
- Red flags
- Application status

Require specific reasoning, not generic phrases.

---

# Quality Bar

The final prompt pack should be:

- Clear
- Non-repetitive
- Internally consistent
- Honest
- Aggressive but not reckless
- Optimized for high-signal jobs
- Strong enough for an autonomous job application tool
- Specific to the user, not generic
- Able to guide job search, scoring, and application answers

---

# Inputs

Use the information below to generate the full prompt pack.

If something is missing, infer cautiously and mark it as \u201cnot provided\u201d or \u201cconfirm before use.\u201d

Do not ask follow-up questions unless the missing information makes the entire output impossible.

## User Resume / Profile / LinkedIn / Notes

[PASTE USER RESUME, LINKEDIN, WEBSITE NOTES, GITHUB NOTES, OR EXISTING PROFILE TEXT HERE]

## Career Preferences

[PASTE TARGET INDUSTRIES, ROLES, COMPANIES, GEOGRAPHY, COMPENSATION, EXCLUSIONS, OR EXISTING REQUIREMENTS HERE]

## Existing Search / Scoring Prompts

[PASTE ANY EXISTING PROMPTS HERE]

---

# Final Instruction

Now produce the full six-part prompt pack:

1. applicant_profile.md
2. enhanced_profile_layer.md
3. applicant_requirements.md
4. enhanced_requirements_layer.md
5. search_prompt.txt
6. scoring_prompt.md

Use clean Markdown. Make each section ready to copy into files.
"""


def get_master_prompt() -> str:
    """Return the read-only master prompt verbatim."""
    return MASTER_PROMPT
