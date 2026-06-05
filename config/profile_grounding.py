"""
Central profile grounding for Chat, application Q&A, and form fill.

All LLM answers must use FACT SHEET + PROFILE + allowed experience anchors only.
"""

from __future__ import annotations

import re
from typing import Optional

# Allowed experience anchors (only these may appear in behavioral / essay answers)
ANCHOR_TOPICS: list[dict] = [
    {
        "id": "adic",
        "keywords": (
            "portfolio", "attribution", "manager", "equity", "investment",
            "analytics", "variance", "active investment", "adic", "fund",
        ),
        "title": "ADIC Active Investments & Equity Intern",
        "summary": (
            "Python multi-factor return attribution, portfolio-tracking software, "
            "manager analysis across 20+ managers; reconciled messy reporting and "
            "explained return drivers (~70% variance explained where verified)."
        ),
    },
    {
        "id": "adia",
        "keywords": (
            "private equity", "pe ", "case study", "due diligence", "adia",
            "deal", "memo", "screening", "apac",
        ),
        "title": "ADIA Private Equity Intern",
        "summary": (
            "APAC-focused private equity research: company screening, financial modeling, "
            "bull/base/bear scenarios, investment memo-style analysis (12+ case studies where verified)."
        ),
    },
    {
        "id": "diba",
        "keywords": (
            "trading", "quant", "systematic", "backtest", "signal", "volatility",
            "risk model", "diba", "derivative", "market risk",
        ),
        "title": "DIBA — Derivative Influenced Buy Analysis (research prototype)",
        "summary": (
            "Quantitative trading research framework: signals, scoring, probability-based "
            "decisions, risk controls, backtesting concepts — research prototype, NOT live trading."
        ),
    },
    {
        "id": "mit",
        "keywords": (
            "robot", "robotics", "space", "optimization", "path", "dijkstra",
            "simulation", "mit", "mbrsc", "astrobee", "iss",
        ),
        "title": "MIT / MBRSC Space Robotics",
        "summary": (
            "Graph-based path optimization in MATLAB/Python for space-robotics-style simulation; "
            "traversal-time improvement vs baseline (~30% where verified)."
        ),
    },
    {
        "id": "nyuad",
        "keywords": (
            "quantum", "research assistant", "parameter sweep", "xxz", "nyuad",
            "scientific computing", "physics",
        ),
        "title": "NYUAD Quantum Computation Research Assistant",
        "summary": (
            "3-qubit XXZ model research; automated parameter sweeps in MATLAB; "
            "compute-time reduction (~40% where verified). NYUAD = research only, not degree campus."
        ),
    },
    {
        "id": "polygon",
        "keywords": (
            "founder", "startup", "cyber", "security", "software", "devops",
            "product", "client", "polygon", "technical infrastructure",
        ),
        "title": "CEO & Co-Founder, Polygon Technical Infrastructures",
        "summary": (
            "Cybersecurity/software services founder: full-stack delivery, DevOps, "
            "client-facing technical work, security-first execution under real constraints."
        ),
    },
    {
        "id": "rectify",
        "keywords": (
            "climate", "carbon", "rec", "emissions", "sustainability", "net zero",
            "rectify", "green",
        ),
        "title": "Co-Founder, RECtify Brokers",
        "summary": (
            "Climate-tech: emissions baselining, REC/I-REC, audit-ready reporting, "
            "UAE Net Zero alignment, marketplace design."
        ),
    },
]

_ROLE_ANCHOR_DEFAULTS = {
    "quant": "diba",
    "invest": "adic",
    "analyst": "adic",
    "risk": "diba",
    "robot": "mit",
    "space": "mit",
    "research": "nyuad",
    "data": "adic",
    "engineer": "polygon",
    "founder": "polygon",
    "climate": "rectify",
    "carbon": "rectify",
    "cyber": "polygon",
}


def format_applicant_facts(qa: dict) -> str:
    """Structured fact sheet from APPLICATION_QA / live qa dict."""
    qa = qa or {}
    fact_keys = [
        ("Full name", qa.get("full_name", "Rashed Ahmed Alneyadi")),
        ("First name", qa.get("first_name", "Rashed")),
        ("Last name", qa.get("last_name", "Alneyadi")),
        ("Phone", qa.get("phone", "")),
        ("Phone (local UAE)", qa.get("phone_local", "")),
        ("Email", qa.get("email", "")),
        ("LinkedIn", qa.get("linkedin", "")),
        ("Website", qa.get("website", "")),
        ("GitHub", qa.get("github", "")),
        ("Location", qa.get("location") or "Abu Dhabi, UAE"),
        ("Country", "United Arab Emirates"),
        ("Nationality", qa.get("nationality", "Emirati (UAE National)")),
        ("Languages", qa.get("languages", "Arabic (fluent), English (fluent)")),
        ("Authorized to work in UAE/GCC", "Yes"),
        ("Visa sponsorship required", "No"),
        ("Years of experience", qa.get("years_experience", "1–2 years")),
        ("Education level", qa.get("education_level", "Bachelor's Degree")),
        ("Degree / field", qa.get("degree_field", "Mathematics with Computer Science Minor")),
        ("University", qa.get("university", "New York University, New York (NYU New York)")),
        ("Graduation", qa.get("graduation_year", "December 2024")),
        ("Current role", "Founder & CEO, Polygon Technical Infrastructures"),
        ("Start date", qa.get("start_date", "Immediately")),
        ("Salary expectation", qa.get("salary_expectation", "Competitive / open to discussion")),
        ("Willing to relocate (UAE/GCC)", qa.get("willing_to_relocate", "Yes")),
        ("Excel", qa.get("excel", "Advanced")),
    ]
    lines = [f"- {k}: {v}" for k, v in fact_keys if v]
    return "\n".join(lines)


def anchors_reference_block() -> str:
    """Compact list of allowed experience anchors for prompts."""
    lines = ["ALLOWED EXPERIENCE ANCHORS (use only these — never invent others):"]
    for a in ANCHOR_TOPICS:
        lines.append(f"- {a['title']}: {a['summary']}")
    return "\n".join(lines)


def pick_anchor_for_question(question: str, role: str = "", company: str = "") -> dict:
    """Choose the best profile anchor for a behavioral / essay question."""
    text = f"{question} {role} {company}".lower()
    best_id = ""
    best_score = 0
    for a in ANCHOR_TOPICS:
        score = sum(1 for kw in a["keywords"] if kw in text)
        if score > best_score:
            best_score = score
            best_id = a["id"]
    if best_score == 0:
        for hint, anchor_id in _ROLE_ANCHOR_DEFAULTS.items():
            if hint in text:
                best_id = anchor_id
                break
    if not best_id:
        # Behavioral / difficulty questions default to strongest finance anchor
        behavioral = (
            "project", "difficult", "challenge", "critical thinking", "problem solving",
            "tell me about", "describe a time", "situation", "accomplishment",
        )
        if any(b in text for b in behavioral):
            best_id = "adic"
        else:
            best_id = "adic"
    for a in ANCHOR_TOPICS:
        if a["id"] == best_id:
            return a
    return ANCHOR_TOPICS[0]


def get_profile_excerpt(max_chars: int = 4500) -> str:
    try:
        from config.md_loader import get_candidate_profile_for_prompt
        return (get_candidate_profile_for_prompt() or "")[:max_chars]
    except Exception:
        return ""


def try_rule_based_answer(question: str, qa: dict) -> Optional[str]:
    """
    Answer from fact sheet / rules without LLM.
    Returns None if the question needs profile narrative or LLM.
    """
    q = (question or "").lower().strip()
    if not q:
        return None

    # Yes / No (common ATS) — before generic patterns
    yes_no = _match_yes_no(q, qa)
    if yes_no is not None:
        return yes_no

    return None


def _match_yes_no(q: str, qa: dict) -> Optional[str]:
    """Return Yes, No, or None if not a clear yes/no question."""
    if not re.search(
        r"\b(are you|do you|have you|will you|can you|is your|did you|would you)\b", q
    ) and "?" not in q:
        return None

    def yn(yes: bool) -> str:
        return "Yes" if yes else "No"

    if any(x in q for x in ("visa", "sponsorship", "sponsor")):
        return yn(False)
    if any(x in q for x in ("authorized", "authorised", "eligible to work", "right to work",
                             "legally", "work permit required")):
        return yn(True)
    if "uae national" in q or "emirati" in q:
        return yn(True)
    if "criminal" in q or "convicted" in q or "felony" in q:
        return yn(False)
    if "relative" in q and "employ" in q:
        return yn(False)
    if "non-compete" in q or "noncompete" in q:
        return yn(False)
    if any(x in q for x in ("willing to relocate", "open to relocate", "relocate")):
        return yn(True)
    if any(x in q for x in ("willing to travel", "travel requirement")):
        return yn(True)
    if any(x in q for x in ("remote", "hybrid", "on-site", "onsite", "office")):
        return yn(True)
    if "arabic" in q and any(x in q for x in ("speak", "fluent", "proficient")):
        return yn(True)
    if "english" in q and any(x in q for x in ("speak", "fluent", "proficient")):
        return yn(True)
    if any(x in q for x in ("bachelor", "degree", "university", "graduated", "completed your education")):
        if any(x in q for x in ("do you have", "have you completed", "hold a")):
            return yn(True)
    if any(x in q for x in ("programming", "python", "matlab", "coding", "software")):
        if "experience" in q or "proficien" in q or "skilled" in q:
            return yn(True)
    if "previously employed" in q or "worked for us before" in q or "former employee" in q:
        return yn(False)
    if any(x in q for x in ("privacy", "terms", "consent", "agree", "acknowledge")):
        return yn(True)
    if "smoke" in q or "tobacco" in q:
        return yn(False)
    if "disability" in q and "have" in q:
        return yn(False)
    if "veteran" in q:
        return yn(False)
    return None


def build_short_answer_prompt(
    *,
    agent_rules: str,
    facts: str,
    profile: str,
    anchors: str,
    company: str,
    role: str,
    question: str,
    selected_anchor: dict,
) -> str:
    return f"""\
You are the applicant (Emirati, NYU Math/CS 2024, NYU New York degree) filling YOUR OWN job application.

{agent_rules}

APPLICANT FACT SHEET — use EXACT values when the question maps here (no extra words):
{facts}

{anchors}

SELECTED ANCHOR for open-ended / behavioral answers (if needed, use ONLY this one):
{selected_anchor["title"]}: {selected_anchor["summary"]}

PROFILE EXCERPT (ground truth — do not contradict):
{profile}

Company: {company}
Role: {role}
Question: {question}

Rules:
1. If the question maps to the FACT SHEET (phone, email, name, location, visa, salary, etc.):
   return ONLY that exact value — one line, no preamble.
2. Yes/No: one word only (Yes or No). Work authorization in UAE: Yes. Visa sponsorship: No.
3. Do not invent employers, projects, dates, or metrics not in FACT SHEET, ANCHORS, or PROFILE.
4. For short open questions under ~3 sentences: first person, use SELECTED ANCHOR only if needed.
5. If you cannot answer from these materials, respond with exactly: UNKNOWN
6. Never refuse. Never say "I cannot provide". Never third person.

Answer:"""


def build_essay_answer_prompt(
    *,
    agent_rules: str,
    facts: str,
    profile: str,
    anchors: str,
    company: str,
    role: str,
    question: str,
    angle: str,
    selected_anchor: dict,
) -> str:
    return f"""\
You are the applicant writing YOUR OWN job application answer.
Identity: Emirati / UAE National; BA Mathematics + CS minor; NYU New York, Dec 2024.

{agent_rules}

APPLICANT FACT SHEET:
{facts}

{anchors}

REQUIRED TOPIC FOR THIS ANSWER — build the entire response around ONLY this experience:
**{selected_anchor["title"]}**
{selected_anchor["summary"]}

Do not switch to a different employer or invent a generic startup. Tie critical thinking /
problem-solving to specific steps you took in this anchor.

PROFILE EXCERPT (ground truth for extra detail — do not contradict):
{profile}

Company: {company}
Role: {role}
Positioning angle: {angle}
Question: {question}

Write a high-quality first-person answer:
- 200–350 words unless the question asks for less.
- Open with a concrete hook tied to {company} (sector/mission — no fake employee names).
- Include at least two specific details from the REQUIRED TOPIC and/or PROFILE (tools, outcome, constraint).
- Voice: confident "I" / "my". No "Rashed", no third person. No "I'm thrilled/excited" openers.
- End with one sentence on contribution in this role.
- Plain prose only — no headings, bullets, or markdown.

Return ONLY the answer text.

Answer:"""
