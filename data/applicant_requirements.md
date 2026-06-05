---
auto_apply: 75
manual_review: 60
max_years_hard_skip: 7
min_requirements_match_pct: 50
linkedin_hours_fresh: 48
ats_days_fresh: 7
min_salary_aed_monthly: 12000
sps_immediate_action: 85
sps_apply_network: 70
sps_network_only: 50
ips_inmail_threshold: 75
easy_apply_max_hours: 24
easy_apply_max_applicants: 50
warm_lead_referral_threshold: 70
---
# Applicant Requirements

> Edit this file to describe what jobs I want. The YAML block above drives score thresholds in code.
> Copy `applicant_requirements.template.md` for a new user.

---

## Geography

- **Priority locations:** Abu Dhabi, Dubai, DIFC, ADGM, UAE, Qatar, GCC, Riyadh, Saudi Arabia, Bahrain, Kuwait
- **Willing to relocate:** Within UAE and GCC — yes
- **Workplace languages:** Comfortable in Arabic and English
- **Candidate positioning:** Emirati, bilingual, UAE/GCC-native profile

Location priority:

1. Abu Dhabi
2. Dubai
3. UAE overall
4. Qatar / Doha
5. Saudi / Riyadh
6. GCC selectively
7. International only if elite and highly relevant

---

## Compensation

- **Target:** 35,000+ AED/month by Year 2
- **Ideal path:** 35k–45k AED/month within 2–4 years
- **Minimum acceptable target:** 27,000 AED/month

Salary handling:

- If parsed salary is **below 20,000 AED/month**, skip unless the company is elite and the role has exceptional progression.
- If parsed salary is **20,000–27,000 AED/month**, manual review unless elite graduate/analyst/associate program.
- If parsed salary is **27,000+ AED/month**, score normally.
- If salary is unlisted, do not auto-skip. Score based on company, role, level, and likely compensation trajectory.
- Do not reject elite roles only because salary is not shown.

Important:

Elite graduate programs, analyst programs, investment roles, quant roles, AI/data roles, sovereign-backed roles, and hedge fund roles may be worth applying to even if salary is unclear.

---

## Score Thresholds

| Decision | Score |
|----------|-------|
| Auto-apply | 75–100 |
| Manual review | 60–74 |
| Skip | below 60 |

---

## Unified Engine (SPS / IPS)

The unified engine runs after scoring on every job. It does **not** replace the LLM score — it adds interview/referral intelligence.

### Success Probability Score (SPS)

Weighted blend of role fit, connection strength, ATS match, timing, hiring urgency, company priority, and outreach quality.

| SPS band | Action |
|----------|--------|
| 85–100 | Immediate action — referral-first when warm leads exist |
| 70–84 | Apply + network simultaneously |
| 50–69 | Network only |
| Below 50 | Monitor or ignore |

Thresholds: `sps_immediate_action`, `sps_apply_network`, `sps_network_only` in YAML frontmatter.

### InMail Priority Score (IPS)

Use paid InMail only when `IPS ≥ ips_inmail_threshold` (default 75) and free outreach levels 1–3 are exhausted.

### Easy Apply gate (LinkedIn only)

Apply via Easy Apply **only** when:

- Job age `< easy_apply_max_hours` (default 24), **and**
- Applicants `< easy_apply_max_applicants` (default 50)

Otherwise: **networking-only mode** (stakeholder mapping + outreach waterfall).

### Bespoke portals (networking-first)

No unattended apply for McKinsey, Goldman, Bain, BCG, BlackRock, sovereign funds, and similar — engine notifies and routes to outreach.

### Human gate

The engine researches, scores, drafts, and recommends. **You** send every message and click every submit.

---

Be aggressive on high-upside roles when I meet **50–70%** of stated requirements, especially in:

- Quant
- Hedge funds
- Trading
- Investments
- Sovereign wealth funds
- Private equity
- Venture capital
- AI/data
- Space/defense
- Energy/commodities
- Fintech
- Climate/carbon markets
- Government strategy

Do not self-reject too early on ambitious analyst, associate, graduate, junior, and research roles.

---

## Experience Rules

Hard skip:

- Job explicitly requires **7+ years** as a hard requirement.
- Senior/Lead/Principal/VP/Director/C-suite roles unless the posting is clearly mislabeled or unusually junior-friendly.

Manual review:

- **5–7 years** required but not clearly mandatory.
- Manager title with junior-friendly description.
- Strong company but imperfect title.
- Role asks for a master’s degree but does not strictly require one.
- Role requires finance/technical experience that can plausibly be covered by math, research, programming, DIBA, or entrepreneurship.

Appropriate levels:

- Analyst
- Associate
- Intern
- Graduate
- Junior
- Researcher
- Trainee
- Fellow
- Rotational program
- Graduate development program
- Early careers
- Emiratization / UAE national program

---

## Hard Skips

Skip roles with:

- Crowdsourced AI labeling / microtask platforms
- AI-agent-only roles where the post is not actually hiring a human employee
- Commission-only sales
- Pure sales with no strategic/finance/tech upside
- Customer service
- HR-only
- Admin assistant
- Low-level coordinator roles
- Talent pools
- Evergreen requisitions
- Always-open generic applications
- Vague crypto/Web3 roles with poor credibility
- Roles with no compensation, brand, progression, technical, or strategic upside
- Clinical practitioner roles: nursing, physician, medical technician, etc.
- Roles requiring 7+ years as a hard requirement

Do **not** skip ADIA or ADIC. They are target companies.

---

## Target Role Families

### 1. Quant / Trading

Target roles:

- Quantitative Researcher
- Quantitative Analyst
- Quantitative Trader
- Systematic Trading Analyst
- Algorithmic Trading Analyst
- Junior Trader
- Graduate Trader
- Trading Analyst
- Execution Trader
- Portfolio Analytics Analyst
- Market Risk Analyst
- Trading Risk Analyst
- Derivatives Analyst
- Research Engineer — Quant / Finance
- Data Scientist — Markets / Trading

Target industries:

- Hedge funds
- Prop trading firms
- Systematic funds
- Market makers
- Commodities trading firms
- Multi-manager funds
- FX/equities/derivatives desks
- Macro trading desks
- Energy trading desks

---

### 2. Investments / Private Capital

Target roles:

- Investment Analyst
- Graduate Investment Analyst
- Investment Associate
- Portfolio Analyst
- Asset Management Analyst
- Private Equity Analyst
- Venture Capital Analyst
- M&A Analyst
- Corporate Development Analyst
- Fund Analyst
- Alternatives Analyst
- Infrastructure Investment Analyst
- Real Estate Investment Analyst
- Family Office Analyst
- Sovereign Wealth Fund Analyst
- Research Analyst — Investments

Target industries:

- Sovereign wealth funds
- Sovereign-backed investment companies
- Private equity
- Venture capital
- Asset management
- Family offices
- Infrastructure funds
- Real estate investment platforms
- Corporate development teams
- M&A teams
- Alternative investments platforms

---

### 3. AI / Data

Target roles:

- Data Scientist
- Machine Learning Engineer
- AI Engineer
- Applied Scientist
- Research Engineer
- Decision Scientist
- Quantitative Data Analyst
- Statistical Modeling Analyst
- AI Product Analyst
- Computer Vision Engineer
- NLP Engineer
- ML Research Assistant / Associate

Target industries:

- AI companies
- Data science teams
- Applied research labs
- AI infrastructure
- Fintech AI
- Defense AI
- Space/geospatial AI
- Government AI units
- Predictive analytics platforms

---

### 4. Space / Defense / Geospatial

Target roles:

- Space Systems Analyst
- Mission Analyst
- Satellite Operations Analyst
- Remote Sensing Analyst
- Geospatial Data Scientist
- Aerospace Strategy Analyst
- Defense Strategy Analyst
- Robotics Engineer
- Research Engineer
- GNC Analyst
- Advanced Technology Analyst
- Junior Engineer, only if space/defense/AI/robotics-related

Target industries:

- Space
- Satellites
- Aerospace
- Defense technology
- Geospatial intelligence
- Remote sensing
- Robotics
- Advanced R&D

---

### 5. Energy / Commodities

Target roles:

- Energy Trading Analyst
- Commodities Analyst
- LNG Analyst
- Oil & Gas Market Analyst
- Power Markets Analyst
- Energy Strategy Analyst
- Energy Risk Analyst
- Clean Energy Analyst
- Energy Transition Analyst
- Carbon Trading Analyst
- Infrastructure Investment Analyst

Target industries:

- Energy trading
- ADNOC-related trading and strategy
- LNG
- Oil and gas markets
- Power markets
- Commodities
- Energy transition
- Clean energy
- Infrastructure investing
- Carbon trading

---

### 6. Fintech / Financial Infrastructure

Target roles:

- Fintech Analyst
- Fintech Strategy Analyst
- Product Analyst — Fintech
- Risk Analyst — Fintech
- Payments Analyst
- Trading Technology Analyst
- Financial Infrastructure Analyst
- Wealthtech Analyst
- Regtech Analyst
- Market Infrastructure Analyst
- Digital Assets Analyst, only if regulated and reputable

Target industries:

- Payments
- Wealthtech
- Regtech
- Trading technology
- Financial data
- Risk analytics
- Market infrastructure
- Regulated digital assets
- ADGM/DIFC fintechs

---

### 7. Climate / Carbon Markets

Target roles:

- Climate Analyst
- Carbon Markets Analyst
- Carbon Trading Analyst
- Sustainability Analyst
- ESG Data Analyst
- Green Finance Analyst
- Energy Transition Analyst
- Climate Fintech Analyst
- Emissions Reporting Analyst
- REC / I-REC Market Analyst

Target industries:

- Carbon markets
- REC / I-REC markets
- Sustainability technology
- Climate fintech
- ESG data
- Green finance
- Energy transition funds
- Emissions reporting platforms

---

### 8. Government / Economic Development

Target roles:

- Strategy Analyst
- Government Strategy Analyst
- Economic Development Analyst
- Innovation Analyst
- Transformation Analyst
- Strategic Initiatives Analyst
- Public Sector Strategy Analyst
- Policy Strategy Analyst
- National Development Program Analyst

Target industries:

- Government-linked entities
- Sovereign-backed companies
- Economic development offices
- Public-sector transformation
- Innovation offices
- UAE national development programs
- National champion companies

---

### 9. Strategy / Founder-Operator

Target roles:

- Chief of Staff
- Founder Associate
- Venture Builder
- Strategy & Operations Associate
- Corporate Strategy Analyst
- Corporate Development Analyst
- Business Operations Analyst
- Product Strategy Analyst
- Growth Strategy Analyst
- Innovation Analyst

Target industries:

- High-growth startups
- Venture studios
- Sovereign-backed portfolio companies
- Family office portfolio companies
- AI startups
- Fintechs
- Cybersecurity companies
- Climate/green-tech companies
- Space/defense companies

---

### 10. Product / Cyber / Software

Target roles:

- Technical Product Manager
- Product Manager
- AI Product Manager
- Product Analyst
- Cybersecurity Consultant
- Security Analyst
- GRC Analyst, only if pay/path is strong
- Solutions Consultant
- Software Engineer, only if requirements accept math/founder/non-CS profile
- Security Engineer, only if requirements are not too rigid

Target industries:

- Cybersecurity
- SaaS
- Cloud/security
- Enterprise software
- AI product companies
- GovTech
- Strategic technology startups

---

## Target Companies

### Finance / Funds / Consulting

Include:

- ADIA
- ADIC
- Mubadala
- Mubadala Capital
- ADQ
- Emirates Investment Authority
- EIA
- Lunate
- Chimera
- Invest AD
- ICD
- Brevan Howard
- Millennium
- Point72
- Squarepoint
- Schonfeld
- ExodusPoint
- Qube Research & Technologies
- BAM / Balyasny
- Verition
- Dymon Asia
- TCI Fund Management
- KKR
- Partners Group
- McKinsey
- Oliver Wyman
- PIF
- QIA
- Hub71
- Abu Dhabi Catalyst Partners
- DIFC funds
- ADGM funds
- UAE family offices

### Tech / AI / Space / Energy

Include:

- G42
- Core42
- M42
- AI71
- Space42
- Presight
- TII
- EDGE Group
- Bayanat
- Yahsat
- Thuraya
- MBRSC
- UAE Space Agency
- ADNOC
- ADNOC Trading
- ADNOC Global Trading
- Masdar
- MBZUAI-linked labs
- Huawei UAE

---

## Search Sources

Search in this order:

1. LinkedIn Jobs
2. Company careers / ATS
3. Employee / recruiter posts
4. High-quality UAE job boards only
5. Google-indexed hidden hiring signals

ATS systems to check:

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

Freshness priority:

- LinkedIn Jobs: prioritize roles posted within 48h
- ATS/careers pages: prioritize roles posted within 7 days
- Employee/recruiter posts: prioritize posts from past 30 days

Older roles can still be saved for manual review if the company and role are elite.

---

## Application Decision

Auto-apply when:

- UAE/GCC role
- Strong company signal
- Strong compensation or likely progression
- Candidate meets 50–70% of requirements
- Role matches target families
- Role is analyst/associate/graduate/junior/research level
- Role has strategic optionality

Manual review when:

- Borderline years
- Unclear salary
- Strong company but imperfect title
- Outside UAE/GCC but elite
- Needs DM/referral
- Role is interesting but title is unusual
- Requirements include master’s degree but not strictly mandatory
- Salary is 20k–27k but progression is strong

Skip when:

- Low-signal
- Unrelated
- 7+ years hard requirement
- Admin/customer service/HR-only
- Pure sales or commission-only
- Crowdsourced AI labeling
- Evergreen/talent pool
- No compensation/progression upside

---

## Positioning Reminders

- I am an Emirati with fluent Arabic and English.
- Boost UAE/GCC government, sovereign-backed, and national development roles where Emirati profile is an advantage.
- Position as: mathematics + software + investments + research + founder/operator.
- Do not position me as a generic business graduate only.
- Master's degree not held — do not auto-fail unless mandatory.
- DIBA/backtesting should be described as research prototype experience, not live trading claims.
- MIT Media Lab / space robotics exposure should be used strongly for space, AI, robotics, defense, and research roles.
- Entrepreneurship should be used for product, strategy, chief of staff, founder associate, cyber/software, and venture roles.

Reminder:
If a job has a good career progression plan, a good pay, and everything checks out. It passes even if it is not in the industries mentioned

## Custom scoring prompt

# Job Scoring Prompt

You are scoring job opportunities for an Emirati candidate with:

- NYU New York BA Mathematics + CS minor (NYUAD = quantum research assistant only)
- MIT Media Lab / space robotics exposure
- Quantitative and analytical background
- Programming, AI, and data science interest
- Founder/operator experience in software, cybersecurity, sustainability, and product building
- Interest in quant trading, hedge funds, investments, sovereign wealth funds, AI, space, defense, energy, fintech, climate/carbon markets, and strategy

The goal is to identify roles that can realistically lead to:

- 35,000+ AED/month by Year 2
- Or 35k–45k AED/month within 2–4 years
- Or strong long-term optionality through brand, technical depth, or strategic exposure

Score every job out of 100.

---

## Scoring Breakdown

### 1. Compensation Potential — 40 points

Score based on likely pay and upside, even if salary is not listed.

- 36–40: Hedge fund, quant, sovereign wealth, PE/VC, elite asset management, elite AI, elite energy trading, top strategy consulting
- 30–35: Strong investment, AI/data, energy, fintech, space/defense, corporate development, government strategy role with strong progression
- 20–29: Decent analyst/associate role with moderate compensation and progression
- 10–19: Low-to-mid compensation or unclear path
- 0–9: Clearly low-pay with no upside

Salary rules:

- Salary unlisted: do not penalize heavily if company and role are strong.
- Salary below 20k AED/month: score low unless elite brand/progression.
- Salary 20k–27k AED/month: manual review unless strong progression.
- Salary 27k+ AED/month: score normally.
- Elite graduate/analyst programs can still score high even if salary is unclear.

---

### 2. Progression Speed — 20 points

Score based on how quickly the role can move the candidate toward high compensation, responsibility, or elite exits.

- 18–20: Fast promotion path, elite training, graduate program, analyst-to-associate path, direct investment/trading/AI exposure
- 14–17: Good progression within 2–4 years
- 8–13: Moderate progression but useful experience
- 1–7: Slow progression or unclear pathway
- 0: Dead-end role

Boost:

- Graduate development programs
- Rotational investment programs
- Analyst programs
- Early-career quant/trading programs
- UAE national development programs
- Roles with direct exposure to senior stakeholders or investment committees

---

### 3. Brand / Signal — 15 points

Score company and role prestige.

- 14–15: ADIA, ADIC, Mubadala, ADQ, EIA, Lunate, top hedge funds, top PE/VC, G42/Core42/Presight, Space42, EDGE, ADNOC Trading, elite consulting, PIF/QIA
- 11–13: Strong sovereign-backed company, ADGM/DIFC fund, strong AI/fintech/space/energy company, strong family office
- 7–10: Good regional company or credible startup
- 3–6: Unknown company with some relevance
- 0–2: Weak signal or questionable employer

Boost:

- UAE government-linked entities
- Sovereign-backed companies
- ADGM/DIFC finance firms
- High-growth AI/fintech/space/defense companies
- Elite international funds or trading firms

---

### 4. Fit With Candidate Background / Interests — 15 points

Score how well the role fits the candidate’s actual profile.

Strong fit signals:

- Mathematics / statistics / quantitative analysis
- Programming / Python / data
- AI / ML / research
- Investments / financial modeling / markets
- Space robotics / satellite / geospatial / defense
- Founder/operator / product / strategy
- Arabic + English / Emirati / UAE national advantage

Scoring:

- 14–15: Direct fit with math + research + AI/investments/space/founder background
- 11–13: Strong fit with some gaps
- 7–10: Partial fit
- 3–6: Weak but plausible fit
- 0–2: Not relevant

Do not over-penalize for missing master’s degree unless mandatory.
Do not over-penalize for not having years of formal finance experience if the role is analyst/graduate level.

---

### 5. Strategic Optionality — 10 points

Score how much the role improves future career options.

- 9–10: Opens doors to elite finance, quant, AI, sovereign funds, space/defense, government strategy, or founder/investor path
- 7–8: Strong optionality into several target industries
- 4–6: Some optionality but narrower
- 1–3: Limited optionality
- 0: No meaningful optionality

Boost roles that create access to:

- Sovereign funds
- Hedge funds
- Investment committees
- AI/data teams
- Space/defense ecosystem
- Energy/commodities markets
- Government strategy
- High-growth startups
- Family offices
- Corporate development
- Venture capital

---

## Decision Rules

### Auto-apply: 75–100

Auto-apply if:

- Role is in UAE/GCC or elite international
- Candidate meets 50–70% of requirements
- Role is analyst/associate/graduate/junior/research level
- Strong company or strong compensation path
- Role aligns with target industries

### Manual Review: 60–74

Save for manual review if:

- Strong company but imperfect title
- Role is slightly senior
- Salary is unclear or borderline
- Role requires 5–7 years but not clearly hard
- Role requires DM/referral/email
- Role is outside UAE/GCC but potentially elite
- Role is interesting but not obviously aligned
- Role requires master’s degree but it may be flexible

### Skip: Below 60

Skip if:

- Low-signal employer
- Unrelated function
- Generic admin/HR/customer service
- Pure sales or commission-only
- Crowdsourced AI labeling / microtask work
- Evergreen/talent pool
- 7+ years hard requirement
- No compensation/progression/brand/optionality upside

---

## Role Family Priority Multipliers

When uncertain, boost roles in this order:

1. Quant / hedge funds / trading
2. Investments / sovereign wealth / private capital
3. AI / data science / ML
4. Space / defense / geospatial
5. Energy / commodities / infrastructure
6. Fintech / financial infrastructure
7. Climate / carbon markets / sustainability finance
8. Government strategy / economic development
9. Strategy / corporate development / founder-operator
10. Product / cyber / software

---

## Company Boost Rules

Give extra weight to roles at:

- ADIA
- ADIC
- Mubadala
- Mubadala Capital
- ADQ
- EIA
- Lunate
- Chimera
- Invest AD
- ICD
- Brevan Howard
- Millennium
- Point72
- Squarepoint
- Schonfeld
- ExodusPoint
- Qube
- BAM / Balyasny
- Verition
- KKR
- Partners Group
- McKinsey
- Oliver Wyman
- PIF
- QIA
- G42
- Core42
- M42
- AI71
- Presight
- Space42
- TII
- EDGE
- Bayanat
- Yahsat
- MBRSC
- UAE Space Agency
- ADNOC
- ADNOC Trading
- ADNOC Global Trading
- Masdar
- Hub71
- MBZUAI-linked labs
- DIFC funds
- ADGM funds
- UAE family offices

---

## Emirati / UAE National Boost

Boost score when the role mentions:

- UAE National
- Emirati
- Emiratization
- National development program
- Graduate development program
- Government-linked entity
- Arabic required
- Arabic preferred
- UAE nationals encouraged
- Public sector
- Sovereign-backed entity

Reason:

The candidate is Emirati, bilingual in Arabic and English, and likely has an advantage for UAE/GCC government-linked, sovereign-backed, and national development roles.

---

## Red Flags

Penalize heavily if the role is:

- Generic business development with no strategic upside
- Admin / coordinator / assistant
- Customer service
- HR-only
- Pure sales
- Commission-only
- Microtask AI labeling
- Unpaid
- Vague crypto/Web3
- Requires 7+ years
- Requires very specific license/certification candidate does not have
- Healthcare practitioner role
- No clear employer identity
- Suspicious or scam-like

---

## Output Format

For each job, output:

- Company
- Role title
- Location
- Source
- Posting age/date
- Salary if available
- Score out of 100
- Decision: auto-apply / manual review / skip
- Main reason
- Fit notes
- Red flags, if any
- Application status

Use concise but specific reasoning. Do not only say “good fit.” Explain why.
If a job has a good career progression plan, a good pay, and everything checks out. It passes even if it is not in the industries mentioned

## Search queries

Search for high-signal UAE/GCC roles for an Emirati candidate with mathematics, software, AI, investments, research, space robotics, and founder/operator experience.

Priority locations:
Abu Dhabi, ADGM, Dubai, DIFC, UAE, Qatar, Doha, Riyadh, Saudi Arabia, GCC.

Include ADIA and ADIC in searches. Do not exclude them.

Primary search queries:

quantitative researcher | Abu Dhabi
quantitative analyst | Dubai
quantitative trader | DIFC
quantitative analyst | ADGM
systematic trading analyst | UAE
algorithmic trading analyst | UAE
graduate trader | UAE
junior trader | Dubai
trading analyst | Dubai
market risk analyst | UAE
derivatives analyst | UAE
portfolio analytics analyst | UAE

investment analyst | Abu Dhabi
investment analyst | ADIA
graduate investment analyst | ADIA
ADIA graduate development program
ADIA analyst | Abu Dhabi
ADIA investment analyst | Abu Dhabi
ADIC investment analyst | Abu Dhabi
ADIC analyst | Abu Dhabi
Abu Dhabi Investment Council analyst
graduate investment analyst | UAE
sovereign wealth analyst | UAE
private equity analyst | UAE
venture capital analyst | UAE
portfolio analyst | UAE
asset management analyst | UAE
family office analyst | Dubai
family office analyst | Abu Dhabi
infrastructure investment analyst | UAE
real estate investment analyst | UAE
corporate development analyst | UAE
M&A analyst | UAE

data scientist | Abu Dhabi
machine learning engineer | UAE
AI engineer | UAE
research engineer | Abu Dhabi
applied scientist | UAE
decision scientist | UAE
statistical modeling analyst | UAE
computer vision engineer | Abu Dhabi
NLP engineer | UAE

space systems analyst | UAE
space strategy analyst | UAE
satellite operations analyst | UAE
geospatial data scientist | UAE
remote sensing analyst | UAE
robotics engineer | Abu Dhabi
mission analyst | UAE
defense strategy analyst | UAE
advanced technology analyst | UAE

energy trading analyst | UAE
commodities analyst | UAE
LNG analyst | UAE
power markets analyst | UAE
energy risk analyst | UAE
energy strategy analyst | UAE
infrastructure investment analyst | UAE
clean energy analyst | UAE
energy transition analyst | UAE

carbon markets analyst | UAE
carbon trading analyst | UAE
sustainability analyst | UAE
ESG data analyst | UAE
green finance analyst | UAE
REC analyst | UAE
I-REC analyst | UAE
climate fintech analyst | UAE

fintech analyst | DIFC
fintech analyst | ADGM
trading technology analyst | UAE
payments analyst | UAE
financial infrastructure analyst | UAE
wealthtech analyst | UAE
regtech analyst | UAE
market infrastructure analyst | UAE
risk analytics analyst | UAE

strategy analyst | Abu Dhabi
government strategy analyst | UAE
economic development analyst | UAE
innovation analyst | UAE
transformation analyst | UAE
strategic initiatives analyst | UAE
public sector strategy analyst | UAE
chief of staff | UAE
founder associate | UAE
venture builder | UAE
strategy operations associate | UAE
product strategy analyst | UAE

technical product manager | UAE
product analyst | Dubai
AI product analyst | UAE
cybersecurity analyst | UAE
cybersecurity consultant | UAE
security analyst | UAE
GRC analyst | UAE
solutions consultant | UAE

Target industries:
quant, hedge funds, systematic trading, prop trading, investment analysis, sovereign wealth funds, ADIA, ADIC, Mubadala, ADQ, EIA, Lunate, private equity, venture capital, family offices, asset management, AI/data, machine learning, space, defense, geospatial, energy trading, commodities, fintech, carbon markets, climate, government strategy, corporate development, product, cybersecurity.

Target companies:
ADIA, ADIC, Mubadala, Mubadala Capital, ADQ, EIA, Lunate, Chimera, Invest AD, ICD, Brevan Howard, Millennium, Point72, Squarepoint, Schonfeld, ExodusPoint, Qube, BAM/Balyasny, Verition, KKR, Partners Group, McKinsey, Oliver Wyman, PIF, QIA, G42, Core42, M42, AI71, Presight, Space42, TII, EDGE, Bayanat, Yahsat, Thuraya, MBRSC, UAE Space Agency, ADNOC, ADNOC Trading, Masdar, Hub71, DIFC funds, ADGM funds, UAE family offices, MBZUAI-linked labs.

Prioritize:
- Fresh LinkedIn postings within 48h
- ATS/company roles within 7 days
- Employee/recruiter posts within 30 days
- UAE/GCC-based roles
- Analyst, associate, graduate, junior, researcher, trainee, and early-career roles
- High compensation or elite brand signal
- Emirati/UAE national programs
- Roles where Arabic + English + UAE national profile is an advantage

Auto-apply to high-signal roles when candidate meets 50–70% of requirements.
Save borderline or unusual roles for manual review.
Skip low-signal admin, HR-only, customer service, pure sales, commission-only, crowdsourced AI labeling, evergreen/talent pools, and 7+ years hard-requirement roles.
If a job has a good career progression plan, a good pay, and everything checks out. It passes even if it is not in the industries mentioned

## Custom search prompt

Search for high-signal UAE/GCC roles for an Emirati candidate with mathematics, software, AI, investments, research, space robotics, and founder/operator experience.

Priority locations:
Abu Dhabi, ADGM, Dubai, DIFC, UAE, Qatar, Doha, Riyadh, Saudi Arabia, GCC.

Include ADIA and ADIC in searches. Do not exclude them.

Primary search queries:

quantitative researcher | Abu Dhabi
quantitative analyst | Dubai
quantitative trader | DIFC
quantitative analyst | ADGM
systematic trading analyst | UAE
algorithmic trading analyst | UAE
graduate trader | UAE
junior trader | Dubai
trading analyst | Dubai
market risk analyst | UAE
derivatives analyst | UAE
portfolio analytics analyst | UAE

investment analyst | Abu Dhabi
investment analyst | ADIA
graduate investment analyst | ADIA
ADIA graduate development program
ADIA analyst | Abu Dhabi
ADIA investment analyst | Abu Dhabi
ADIC investment analyst | Abu Dhabi
ADIC analyst | Abu Dhabi
Abu Dhabi Investment Council analyst
graduate investment analyst | UAE
sovereign wealth analyst | UAE
private equity analyst | UAE
venture capital analyst | UAE
portfolio analyst | UAE
asset management analyst | UAE
family office analyst | Dubai
family office analyst | Abu Dhabi
infrastructure investment analyst | UAE
real estate investment analyst | UAE
corporate development analyst | UAE
M&A analyst | UAE

data scientist | Abu Dhabi
machine learning engineer | UAE
AI engineer | UAE
research engineer | Abu Dhabi
applied scientist | UAE
decision scientist | UAE
statistical modeling analyst | UAE
computer vision engineer | Abu Dhabi
NLP engineer | UAE

space systems analyst | UAE
space strategy analyst | UAE
satellite operations analyst | UAE
geospatial data scientist | UAE
remote sensing analyst | UAE
robotics engineer | Abu Dhabi
mission analyst | UAE
defense strategy analyst | UAE
advanced technology analyst | UAE

energy trading analyst | UAE
commodities analyst | UAE
LNG analyst | UAE
power markets analyst | UAE
energy risk analyst | UAE
energy strategy analyst | UAE
infrastructure investment analyst | UAE
clean energy analyst | UAE
energy transition analyst | UAE

carbon markets analyst | UAE
carbon trading analyst | UAE
sustainability analyst | UAE
ESG data analyst | UAE
green finance analyst | UAE
REC analyst | UAE
I-REC analyst | UAE
climate fintech analyst | UAE

fintech analyst | DIFC
fintech analyst | ADGM
trading technology analyst | UAE
payments analyst | UAE
financial infrastructure analyst | UAE
wealthtech analyst | UAE
regtech analyst | UAE
market infrastructure analyst | UAE
risk analytics analyst | UAE

strategy analyst | Abu Dhabi
government strategy analyst | UAE
economic development analyst | UAE
innovation analyst | UAE
transformation analyst | UAE
strategic initiatives analyst | UAE
public sector strategy analyst | UAE
chief of staff | UAE
founder associate | UAE
venture builder | UAE
strategy operations associate | UAE
product strategy analyst | UAE

technical product manager | UAE
product analyst | Dubai
AI product analyst | UAE
cybersecurity analyst | UAE
cybersecurity consultant | UAE
security analyst | UAE
GRC analyst | UAE
solutions consultant | UAE

Target industries:
quant, hedge funds, systematic trading, prop trading, investment analysis, sovereign wealth funds, ADIA, ADIC, Mubadala, ADQ, EIA, Lunate, private equity, venture capital, family offices, asset management, AI/data, machine learning, space, defense, geospatial, energy trading, commodities, fintech, carbon markets, climate, government strategy, corporate development, product, cybersecurity.

Target companies:
ADIA, ADIC, Mubadala, Mubadala Capital, ADQ, EIA, Lunate, Chimera, Invest AD, ICD, Brevan Howard, Millennium, Point72, Squarepoint, Schonfeld, ExodusPoint, Qube, BAM/Balyasny, Verition, KKR, Partners Group, McKinsey, Oliver Wyman, PIF, QIA, G42, Core42, M42, AI71, Presight, Space42, TII, EDGE, Bayanat, Yahsat, Thuraya, MBRSC, UAE Space Agency, ADNOC, ADNOC Trading, Masdar, Hub71, DIFC funds, ADGM funds, UAE family offices, MBZUAI-linked labs.

Prioritize:
- Fresh LinkedIn postings within 48h
- ATS/company roles within 7 days
- Employee/recruiter posts within 30 days
- UAE/GCC-based roles
- Analyst, associate, graduate, junior, researcher, trainee, and early-career roles
- High compensation or elite brand signal
- Emirati/UAE national programs
- Roles where Arabic + English + UAE national profile is an advantage

Auto-apply to high-signal roles when candidate meets 50–70% of requirements.
Save borderline or unusual roles for manual review.
Skip low-signal admin, HR-only, customer service, pure sales, commission-only, crowdsourced AI labeling, evergreen/talent pools, and 7+ years hard-requirement roles.
