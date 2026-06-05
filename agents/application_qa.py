"""
Application Q&A resolution: profile → saved answers → LLM estimate → ask user → save.

Saved answers are stored in applicant_profile.md under ## Saved application answers.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("application_qa")

SECTION_HEADER = "## Saved application answers"
UNKNOWN_TOKEN = "UNKNOWN"

_ESSAY_HINTS = (
    "why", "describe", "tell us", "explain", "cover letter", "statement",
    "motivation", "what interests", "what makes you", "additional information",
    "anything else", "elaborate", "in your own words", "essay", "hardest",
    "technical problem", "challenging", "challenge", "project", "worked on",
    "achievement", "accomplishment",
)

def _apply_rules() -> str:
    from config.apply_agent_rules import rules_block
    return rules_block()


def _normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").lower().strip())[:200]


def load_saved_application_answers() -> list[tuple[str, str]]:
    """Load Q/A pairs from ## Saved application answers in profile."""
    try:
        from agents.profile_manager import load_profile_body
        body = load_profile_body()
    except Exception:
        return []
    m = re.search(
        r"## Saved application answers\s*(.*?)(?=\n## |\Z)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return []
    block = m.group(1)
    pairs: list[tuple[str, str]] = []
    for chunk in re.split(r"\n(?=\*\*Q:\*\*)", block):
        chunk = chunk.strip()
        if not chunk.startswith("**Q:"):
            continue
        qm = re.match(r"\*\*Q:\*\*\s*(.+?)\s*\n+\*\*A:\*\*\s*(.+)", chunk, re.DOTALL)
        if qm:
            pairs.append((qm.group(1).strip(), qm.group(2).strip()))
    return pairs


def find_saved_answer(question: str) -> Optional[str]:
    """Match question against saved answers (substring / normalized)."""
    nq = _normalize_question(question)
    if len(nq) < 4:
        return None
    for sq, ans in load_saved_application_answers():
        nsq = _normalize_question(sq)
        if nq == nsq or nq in nsq or nsq in nq:
            return ans
        # Token overlap for long questions
        q_tokens = set(nq.split()) - {"a", "an", "the", "do", "you", "your", "is", "are"}
        s_tokens = set(nsq.split()) - {"a", "an", "the", "do", "you", "your", "is", "are"}
        if len(q_tokens) >= 4 and len(q_tokens & s_tokens) / max(len(q_tokens), 1) >= 0.6:
            return ans
    return None


def save_application_answer(question: str, answer: str) -> None:
    """Append Q/A to profile ## Saved application answers."""
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return
    try:
        from agents.profile_manager import load_profile_body, save_profile_body, load_links
        body = load_profile_body()
        entry = f"\n**Q:** {question}\n\n**A:** {answer}\n"
        if SECTION_HEADER.lower() in body.lower():
            idx = body.lower().index(SECTION_HEADER.lower())
            rest = body[idx + len(SECTION_HEADER):]
            next_sec = re.search(r"\n## ", rest)
            if next_sec:
                insert_at = idx + len(SECTION_HEADER) + next_sec.start()
                body = body[:insert_at] + entry + body[insert_at:]
            else:
                body = body.rstrip() + entry
        else:
            body += (
                f"\n\n{SECTION_HEADER}\n\n"
                "> Answers you provided during job applications (reused automatically).\n"
                f"{entry}"
            )
        save_profile_body(body, load_links())
        logger.info("  Saved application answer to profile: %s", question[:60])
        try:
            from config.config import reload_candidate_profile
            reload_candidate_profile()
        except Exception:
            pass
    except Exception as e:
        logger.warning("  Could not save application answer: %s", e)


def is_essay_question(question: str) -> bool:
    q = (question or "").lower()
    if len(q) > 120:
        return True
    return any(h in q for h in _ESSAY_HINTS)


def is_factual_short_question(question: str) -> bool:
    """True for yes/no and single-fact questions that need no profile narrative."""
    q = (question or "").lower().strip()
    if is_essay_question(question):
        return False
    if len(q) > 80:
        return False
    factual_hints = (
        "phone", "email", "name", "linkedin", "website", "github", "salary",
        "years of experience", "degree", "university", "graduation", "location",
        "nationality", "visa", "sponsor", "authorize", "authoris", "relocat",
        "notice", "start date", "employment type", "currently employed",
        "worked for us", "criminal", "felony", "disability", "veteran",
        "race", "ethnicity", "gender", "pronoun", "hear about", "referral",
        "travel", "remote", "full-time", "part-time", "salary type",
        "how many years", "how long", "are you", "do you", "have you",
        "will you", "can you", "would you",
    )
    return any(h in q for h in factual_hints)


def is_unknown_response(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t.upper() == UNKNOWN_TOKEN:
        return True
    low = t.lower()
    return any(
        p in low
        for p in (
            "i cannot provide",
            "i'm not able to provide",
            "not publicly available",
            "do not have access",
            "cannot determine",
            "unable to answer",
        )
    )


_NAME_TO_I = [
    (re.compile(r"\bRashed Ahmed Alneyadi\b", re.I), "I"),
    (re.compile(r"\bRashed Alneyadi\b", re.I), "I"),
    (re.compile(r"\bMr\.?\s+Alneyadi\b", re.I), "I"),
    (re.compile(r"\bAlneyadi\b"), "I"),
    (re.compile(r"\bRashed\b"), "I"),
]

_HE_TO_I = [
    (re.compile(r"\bhe\s+is\b", re.I), "I am"),
    (re.compile(r"\bhe\s+was\b", re.I), "I was"),
    (re.compile(r"\bhe\s+has\b", re.I), "I have"),
    (re.compile(r"\bhe\s+had\b", re.I), "I had"),
    (re.compile(r"\bhe\s+will\b", re.I), "I will"),
    (re.compile(r"\bhe\s+would\b", re.I), "I would"),
    (re.compile(r"\bhe\s+can\b", re.I), "I can"),
    (re.compile(r"\bhe\s+brings\b", re.I), "I bring"),
    (re.compile(r"\bhe\s+holds\b", re.I), "I hold"),
    (re.compile(r"\bhe\s+works\b", re.I), "I work"),
    (re.compile(r"\bhe\s+developed\b", re.I), "I developed"),
    (re.compile(r"\bhe\s+built\b", re.I), "I built"),
    (re.compile(r"\bhe\s+led\b", re.I), "I led"),
    (re.compile(r"\bHis\b"), "My"),
    (re.compile(r"\bhis\b"), "my"),
    (re.compile(r"\bHim\b"), "Me"),
    (re.compile(r"\bhim\b"), "me"),
    (re.compile(r"\bhimself\b", re.I), "myself"),
]

_REMOVE_PREFIXES = (
    "answer:",
    "here is my answer:",
    "here's my answer:",
    "here is a response:",
    "response:",
)


_VERB_AGREEMENT_FIXES = (
    (re.compile(r"\bI\s+is\b"), "I am"),
    (re.compile(r"\bI\s+has\b"), "I have"),
    (re.compile(r"\bI\s+does\b"), "I do"),
    (re.compile(r"\bI\s+brings\b"), "I bring"),
    (re.compile(r"\bI\s+holds\b"), "I hold"),
    (re.compile(r"\bI\s+works\b"), "I work"),
)


def _polish_essay(text: str) -> str:
    """Strip preface, enforce first person, trim runaway whitespace."""
    if not text:
        return ""
    t = text.strip().strip("`").strip()
    lower = t.lower()
    for pre in _REMOVE_PREFIXES:
        if lower.startswith(pre):
            t = t[len(pre):].strip()
            lower = t.lower()
    for pat, sub in _NAME_TO_I:
        t = pat.sub(sub, t)
    for pat, sub in _HE_TO_I:
        t = pat.sub(sub, t)
    for pat, sub in _VERB_AGREEMENT_FIXES:
        t = pat.sub(sub, t)
    # Capitalize lowercase "i" / "my" at sentence start
    t = re.sub(r"(^|[.!?]\s+)i\b", lambda m: m.group(1) + "I", t)
    t = re.sub(r"(^|[.!?]\s+)my\b", lambda m: m.group(1) + "My", t)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def _grounded_essay_fallback(question: str, anchor: dict) -> str:
    """Return a conservative profile-backed answer when the local LLM is unavailable."""
    title = (anchor.get("title") or "a relevant project").strip()
    summary = (anchor.get("summary") or "").strip()
    scope = summary.split(":", 1)[1].strip() if ":" in summary else summary
    q = (question or "").lower()
    if any(hint in q for hint in ("hardest", "technical problem", "challenge", "challenging")):
        lead = f"One of the hardest technical problems I worked on came from my experience with {title}."
    else:
        lead = f"A relevant example is my work on {title}."
    return (
        f"{lead} The work involved {scope.rstrip('.')}. "
        "I had to break the problem into practical steps, validate assumptions, "
        "and balance technical quality with real constraints. That experience "
        "strengthened my ability to deliver structured solutions under pressure."
    ).strip()


def _essay_is_grounded(answer: str, anchor: dict) -> bool:
    """Reject common LLM embellishments not supported by the selected anchor."""
    allowed = f"{anchor.get('title', '')} {anchor.get('summary', '')}".lower()
    text = (answer or "").lower()

    # Metrics are high-risk: only permit numbers explicitly present in the anchor.
    for number in re.findall(r"\b\d+(?:\.\d+)?%?\b", text):
        if number not in allowed:
            logger.warning("  Rejecting ungrounded essay metric: %s", number)
            return False

    # Tool names and strong claims are only allowed when the selected anchor says so.
    high_risk_terms = (
        "gitlab", "terraform", "kubernetes", "docker", "jenkins", "github actions",
        "aws", "azure", "gcp", "iam", "network segmentation", "soc 2", "iso 27001",
        "zero incidents", "zero security incidents", "regulatory compliance",
        "enterprise-grade compliance",
    )
    for term in high_risk_terms:
        if term in text and term not in allowed:
            logger.warning("  Rejecting ungrounded essay claim: %s", term)
            return False
    return True


def _ollama_generate(
    prompt: str,
    model: str,
    base_url: str,
    num_predict: int = 350,
    temperature: float = 0.35,
) -> str:
    import requests
    # Disable chain-of-thought thinking for qwen3 models (hugely speeds up responses)
    if "qwen3" in model.lower():
        prompt = prompt.rstrip() + "\n/no_think"
    r = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "top_p": 0.9,
                "repeat_penalty": 1.15,
            },
        },
        timeout=180 if num_predict > 400 else 75,
    )
    r.raise_for_status()
    return r.json().get("response", "").strip()


def estimate_from_profile(
    question: str,
    company: str,
    role: str,
    profile: str,
    facts: str,
    model: str,
    base_url: str,
    essay: bool = False,
    angle: str = "investments",
) -> tuple[str, bool]:
    """Returns (answer, is_known). is_known=False means ask user."""
    from config.profile_grounding import (
        anchors_reference_block,
        build_essay_answer_prompt,
        build_short_answer_prompt,
        pick_anchor_for_question,
    )

    anchor = pick_anchor_for_question(question, role, company)
    anchors = anchors_reference_block()
    profile_slice = profile[:4500] if essay else profile[:2500]

    # Factual short questions: minimal prompt — no profile excerpt, no anchors
    if not essay and is_factual_short_question(question):
        minimal_prompt = (
            f"You are filling out a job application. Applicant fact sheet:\n{facts}\n\n"
            f"Question: {question}\n\n"
            f"Rules:\n"
            f"- Return ONLY the answer value (1 word or 1 short phrase).\n"
            f"- Yes/No: one word only.\n"
            f"- Never refuse. Never say you cannot provide.\n"
            f"- If the answer is in the fact sheet, return it exactly.\n"
            f"- If unknown, return: UNKNOWN\n\nAnswer:"
        )
        try:
            ans = _ollama_generate(
                minimal_prompt, model, base_url,
                num_predict=60, temperature=0.0,
            )
            if not is_unknown_response(ans):
                return ans.strip().splitlines()[0].strip(), True
        except Exception as e:
            logger.debug("  Factual short prompt failed (%s), falling through", e)

    if essay:
        prompt = build_essay_answer_prompt(
            agent_rules=_apply_rules(),
            facts=facts,
            profile=profile_slice,
            anchors=anchors,
            company=company,
            role=role,
            question=question,
            angle=angle,
            selected_anchor=anchor,
        )
        try:
            ans = _ollama_generate(
                prompt, model, base_url, num_predict=1100, temperature=0.32
            )
            ans = _polish_essay(ans)
            if ans and not is_unknown_response(ans) and _essay_is_grounded(ans, anchor):
                logger.info("  Essay anchor: %s", anchor["title"][:50])
                return ans, True
        except Exception as e:
            logger.error("  Essay answer LLM error: %s", e)
        fallback = _grounded_essay_fallback(question, anchor)
        if fallback:
            logger.warning("  Using grounded essay fallback: %s", anchor["title"][:50])
            return fallback, True
        return "", False

    prompt = build_short_answer_prompt(
        agent_rules=_apply_rules(),
        facts=facts,
        profile=profile_slice,
        anchors=anchors,
        company=company,
        role=role,
        question=question,
        selected_anchor=anchor,
    )
    try:
        ans = _ollama_generate(prompt, model, base_url, num_predict=220, temperature=0.2)
        if is_unknown_response(ans):
            return "", False
        # Short factual answers should stay one line
        if len(ans) < 120 and "\n\n" not in ans:
            return ans.strip(), True
        return _polish_essay(ans), True
    except Exception as e:
        logger.error("  Estimate LLM error: %s", e)
        return "", False


def resolve_application_answer(
    question: str,
    company: str,
    role: str,
    angle: str,
    profile: str,
    model: str,
    base_url: str,
    qa: dict,
    qa_value_fn=None,
) -> str:
    """
    Resolve an application question:
    1. Structured QA map (phone, email, etc.)
    2. Saved application answers in profile
    3. LLM estimate from profile (essay vs short)
    4. Ask user if unknown → save to profile
    """
    if qa_value_fn:
        direct = qa_value_fn(question, qa)
        if direct is not None and str(direct).strip():
            return direct

    from config.profile_grounding import try_rule_based_answer
    ruled = try_rule_based_answer(question, qa)
    if ruled is not None:
        return ruled

    saved = find_saved_answer(question)
    if saved:
        logger.debug("  Using saved application answer")
        return saved

    essay = is_essay_question(question)
    try:
        # Use session cache from form_filler if available
        from agents import form_filler as _ff
        _sess = getattr(_ff, "_THREAD_SESSION", None)
        if _sess is not None:
            facts = _sess.facts
            if not (profile or "").strip():
                profile = _sess.profile
        else:
            from config.profile_grounding import format_applicant_facts, get_profile_excerpt
            facts = format_applicant_facts(qa)
            if not (profile or "").strip():
                profile = get_profile_excerpt(4500)
    except Exception:
        try:
            from config.profile_grounding import format_applicant_facts, get_profile_excerpt
            facts = format_applicant_facts(qa)
            if not (profile or "").strip():
                profile = get_profile_excerpt(4500)
        except Exception:
            facts = ""

    ans, known = estimate_from_profile(
        question, company, role, profile, facts, model, base_url,
        essay=essay, angle=angle,
    )
    if known and ans:
        return ans

    logger.warning("  Unknown application question - using unattended fallback")
    return ans or "N/A"


def _validation_errors_js(root_expr: str) -> str:
    """Browser script: scrape validation errors within ``root`` (element or document)."""
    return f"""(root) => {{
            const scope = {root_expr};
            const out = [];
            const seen = new Set();
            const add = (message, hint, sel) => {{
                const m = (message || '').trim().slice(0, 200);
                if (!m || seen.has(m)) return;
                seen.add(m);
                out.push({{ message: m, hint: (hint || '').trim().slice(0, 120), sel: sel || '' }});
            }};
            const visible = el => el && el.offsetParent !== null;
            scope.querySelectorAll(
                '[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid'
            ).forEach(el => {{
                if (!visible(el)) return;
                let hint = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '';
                const labelled = el.id && scope.querySelector('label[for="' + el.id + '"]');
                if (labelled) hint = labelled.textContent.trim() || hint;
                let msg = el.validationMessage || '';
                const parent = el.closest('[class*="form-element"], fieldset, [data-test-form-element]');
                if (parent) {{
                    parent.querySelectorAll(
                        '[class*="error"], [class*="feedback--error"], [role="alert"]'
                    ).forEach(err => {{ if (err.textContent) msg = err.textContent.trim(); }});
                }}
                const sel = el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : '');
                add(msg || 'Invalid value', hint, sel);
            }});
            scope.querySelectorAll(
                '.artdeco-inline-feedback--error, [data-test-form-element-error-message], ' +
                '[class*="error-message"], [class*="form-error"], div[role="alert"]'
            ).forEach(err => {{
                if (!visible(err)) return;
                const msg = err.textContent.trim();
                let hint = '';
                const wrap = err.closest('[class*="form-element"], fieldset');
                if (wrap) {{
                    const lab = wrap.querySelector('label, legend, [class*="label"]');
                    if (lab) hint = lab.textContent.trim();
                }}
                add(msg, hint, '');
            }});
            return out.slice(0, 15);
        }}"""


def scrape_form_validation_errors(page, root=None) -> list[dict]:
    """Collect visible validation errors; optional ``root`` locator scopes to a modal."""
    try:
        if root is not None:
            return root.evaluate(_validation_errors_js("root")) or []
        return page.evaluate(_validation_errors_js("document")) or []
    except Exception:
        return []


def coerce_value_for_error(value: str, error_message: str, input_type: str = "text") -> str:
    """Adjust value to satisfy common validation messages (e.g. numbers only)."""
    val = (value or "").strip()
    msg = (error_message or "").lower()
    itype = (input_type or "").lower()

    needs_number = (
        itype == "number"
        or "number" in msg
        or "numeric" in msg
        or "digit" in msg
        or "integer" in msg
        or "decimal" in msg
        or ("year" in msg and ("only" in msg or "valid" in msg or "enter" in msg))
    )
    if needs_number:
        m = re.search(r"-?\d+(?:\.\d+)?", val)
        if m:
            num = m.group(0)
            if "." in num and "decimal" not in msg:
                num = num.split(".")[0]
            return num
        if "year" in msg:
            return "2"
        return "0"

    if "email" in msg:
        m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", val)
        if m:
            return m.group(0)

    if "too long" in msg or "maximum" in msg and "character" in msg:
        m = re.search(r"(\d+)\s*character", msg)
        limit = int(m.group(1)) if m else 200
        return val[: max(1, limit - 5)]

    if "required" in msg and not val:
        return "Yes"

    return val


def fix_validation_errors_on_page(
    page,
    qa: dict,
    job: dict,
    profile: str,
    model: str,
    base_url: str,
    qa_value_fn=None,
    root=None,
) -> int:
    """Read validation errors, fix fields, return count fixed."""
    errors = scrape_form_validation_errors(page, root=root)
    if not errors:
        return 0
    fixed = 0
    for err in errors:
        msg = err.get("message", "")
        hint = err.get("hint", "")
        sel = err.get("sel", "")
        logger.info(f"  Validation error: '{msg[:80]}' (field: {hint[:50] or sel})")

        new_val = None
        if qa_value_fn and hint:
            new_val = qa_value_fn(hint, qa)
        if new_val is None and hint:
            new_val = resolve_application_answer(
                hint,
                job.get("company", ""),
                job.get("title", ""),
                job.get("positioning_angle", "investments"),
                profile,
                model,
                base_url,
                qa,
                qa_value_fn=qa_value_fn,
            )

        input_type = "text"
        el = None
        field_root = root if root is not None else page
        if sel:
            try:
                loc = field_root.locator(sel).first
                if loc.count() > 0:
                    el = loc
                    input_type = loc.get_attribute("type") or "text"
            except Exception:
                el = None

        if el and new_val is not None:
            new_val = coerce_value_for_error(str(new_val), msg, input_type)
            try:
                el.fill("")
                el.fill(new_val)
                fixed += 1
                logger.info(f"  Fixed field -> '{new_val[:60]}'")
                continue
            except Exception:
                pass

        # Last resort: coerce whatever is in the field now
        if el:
            try:
                current = el.input_value(timeout=500)
                coerced = coerce_value_for_error(current, msg, input_type)
                if coerced != current:
                    el.fill(coerced)
                    fixed += 1
                    logger.info(f"  Coerced field value -> '{coerced}'")
            except Exception:
                pass
    return fixed
