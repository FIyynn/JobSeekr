"""
Scoring Agent
Uses local Ollama (OLLAMA_MODEL from config / profile_settings.json) to score each job.
Returns a score 0–100 and a decision: auto_apply | manual_review | skip.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import logging
import re
import requests
from typing import Optional

from agents.job_fit import prefilter_job
from agents.target_industry import is_outside_target_industry
from agents.job_profile import build_structured_job_profile, job_profile_summary_for_scorer
from agents.salary_filter import check_salary_floor, get_min_salary_from_config, parse_salary_from_text

logger = logging.getLogger("scorer")

# ── Crowdsourced / AI-labeling platforms — always skip ────────────────────────
CROWDSOURCED_PLATFORMS = [
    "dataannotation", "scale ai", "appen", "outlier ai", "outlier",
    "remotasks", "invisible ai", "surge ai", "labelbox", "toloka",
    "defined.ai", "icomply", "lionbridge", "telus international",
    "taskus", "clickworker", "microworkers", "mturk", "amazon mechanical turk",
    "prolific", "isahit", "smartcrowd",
]

SCORING_SYSTEM_PROMPT = """
You are a job-fit scoring agent for a specific candidate. Your job is to score each role
and return ONLY valid JSON — no markdown, no explanation, just the JSON object.

## CANDIDATE SUMMARY
{candidate_profile}

## CANDIDATE STATUS
- Graduated December 2024 from NYU (New York) — BA Mathematics, CS minor. ~1–2 years experience (internships + founder roles).
- High-agency technical founder — NOT a generic entry-level applicant. NOT a senior hire (no Senior/Lead/Principal/VP).
- Score TWO-SIDED: job requirements vs candidate offerings AND candidate target industries vs job.
- Be aggressive: auto-apply ambitious roles where candidate meets 50–70% of requirements in quant, investments, AI, space, energy, fintech, sovereign-backed firms. Do not self-reject too early.
- Trading gap: candidate has no professional live trading, but DIBA framework = systematic signal generation + risk controls. Do NOT penalise profile_fit for roles that say "trading knowledge not required" or "will train".

Experience calibration:
- 0–3 years stated: full scoring if skills match
- 4–6 years hard requirement: manual_review at best unless exceptional overlap; rarely auto_apply
- 7+ years hard requirement: HARD SKIP (score 0)
- 5–7 years ambiguous: manual_review, not auto_apply
- Senior / Lead / Manager / VP / Director / Principal / Chief: HARD SKIP unless title is clearly junior (e.g. "Junior Trader", "Graduate Analyst", "Graduate Programme")

## SCORING CRITERIA (weights) — must sum to 100
- compensation_potential (40): Does this role offer strong ABSOLUTE earning potential within 2 years —
  whether via UAE/GCC base pay OR high Western comp (quant trading, HFT, top-tier IB, PE, FAANG)?
  Score 35–40 for: HFT firms (Wintermute, Optiver, Jump, DRW, Citadel), bulge-bracket IB analyst,
  top PE/VC, sovereign wealth, FAANG/hyperscaler, or UAE firms with AED 35k+/mo trajectory.
  Score 20–30 for mid-tier but clear upside. Score <20 only if comp ceiling is genuinely low.
  Geography alone (London vs. Abu Dhabi) does NOT reduce this score — it's about absolute earning power.
- progression_speed (20): Growth trajectory from this role?
- brand_signal (15): Top-tier brand signal. UAE: Mubadala, ADQ, G42, Brevan Howard, Millennium, ADNOC,
  McKinsey. Global: Citadel, Optiver, Jump, Jane Street, Wintermute, DRW, Goldman, JPMorgan, Soros,
  Two Sigma, FAANG, top-3 consulting. Mid-tier firms score 8–10; no-name firms score 3–6.
- profile_fit (15): Match to math + software + investments + research + founder background?
- strategic_optionality (10): Opens doors in target areas (quant, investments, AI, space, energy, climate, fintech, strategy)?

## DECISION THRESHOLDS (use these exactly)
- score 75–100 → decision "auto_apply"
- score 60–74 → decision "manual_review"
- score below 60 → decision "skip"

## HARD SKIP RULES — if ANY of these apply, return score=0 and decision="skip"
- ADIA or ADIC roles → always skip, score 0
- Requires 5+ years as a hard requirement → skip
- Pure sales / commission-only → skip
- Admin / HR / customer service → skip
- No compensation upside path → skip
- Vague crypto/Web3 with no reputable backing → skip
- Senior / Lead / Manager / VP / Director / C-suite titles → skip
- Role is for AI agents, bots, or "developer submission" of an AI system — NOT human hires → skip
- G42 "Intelligence Agent" roles (Legal/Marketing/Compliance Intelligence Agent) → skip
- Role is clearly designed for an AI system, automation bot, or crowdsourced worker,
  not a human professional → skip
- Company is a crowdsourced AI-training/data-labeling platform (DataAnnotation, Scale AI,
  Appen, Outlier, Remotasks, Toloka, TaskUs, Surge AI, etc.) → skip, score 0
  (These platforms pay workers to train AI models — not real analyst career paths)

## ATS MAPPING RULES (use when assessing profile_fit)
- "Python / backtesting / performance tracking" → candidate has ADIC attribution model,
  DIBA framework, quant research. Mark as Partial unless clearly production-level required.
- "Math / stats / quantitative modeling" → BA Mathematics from NYU (New York). Mark Yes.
- "Systematic trading" → candidate has DIBA project (signal generation, scoring models,
  risk controls). Mark Yes for exposure.
- "Master's degree" → candidate does NOT have one. Note it but don't auto-fail.
- "Testing / validating models" → MIT robotics + NYUAD quantum sweeps + ADIC attribution.
  Mark Yes.
- "Complex datasets" → ADIC 20+ manager attribution, portfolio analytics. Mark Yes.

## OUTPUT FORMAT (return ONLY this JSON, nothing else)
{
  "score": <integer 0-100>,
  "breakdown": {
    "compensation_potential": <0-40>,
    "progression_speed": <0-20>,
    "brand_signal": <0-15>,
    "profile_fit": <0-15>,
    "strategic_optionality": <0-10>
  },
  "years_required_guess": <integer or null>,
  "human_applyable": <true|false>,
  "decision": "<auto_apply|manual_review|skip>",
  "fit_reason": "<1–2 sentences why this is or isn't a fit>",
  "skip_reason": "<1 sentence if skipping, else empty string>",
  "positioning_angle": "<which profile angle to lead with: quant|investments|AI|space|energy|fintech|climate|strategy|cyber>",
  "outside_target_industry": <true if role is clearly outside quant/investments/AI/space/energy/fintech/climate/strategy families, else false>,
  "matches_stated_targets": <true if role aligns with applicant's stated target role families in requirements>,
  "suggested_alternate": <true if OUTSIDE stated targets but score would be 75+ and candidate meets requirements — recommend this role anyway>,
  "alternate_suggestion_reason": "<1 sentence why apply despite different industry; empty if not suggested_alternate>"
}
"""

def _is_crowdsourced_platform(company: str) -> bool:
    """Return True if company is a crowdsourced AI-labeling / task platform."""
    c = (company or "").lower().strip()
    return any(p in c for p in CROWDSOURCED_PLATFORMS)


def _call_ollama(prompt: str, model: str, base_url: str, timeout: int = 180) -> str:
    """Call Ollama and return response text. Retries once if JSON looks truncated."""
    # Disable chain-of-thought thinking for qwen3 models (hugely speeds up responses)
    if "qwen3" in model.lower():
        prompt = prompt.rstrip() + "\n/no_think"
    def _call(num_predict: int) -> str:
        try:
            resp = requests.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "num_predict": num_predict,
                    }
                },
                timeout=timeout
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Ollama. Make sure Ollama is running: ollama serve"
            )
        except Exception as e:
            raise RuntimeError(f"Ollama call failed: {e}")

    text = _call(1500)
    # Retry with more tokens if response looks truncated (no closing brace)
    if text and text.count("{") > text.count("}"):
        logger.debug("Scorer response truncated, retrying with more tokens")
        text = _call(2500)
    return text


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from model output even if it has surrounding text or thinking preamble."""
    if not text:
        return None
    # Direct parse
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    # Strip qwen3 <think>...</think> blocks before searching
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Find the last (outermost) {...} block — model sometimes emits explanation first
    # Use rfind to get the last { and matching }
    last_open = text.rfind('{')
    if last_open != -1:
        snippet = text[last_open:]
        try:
            return json.loads(snippet)
        except Exception:
            pass
    # Greedy search for any {...} block
    for match in re.finditer(r'\{[\s\S]*?\}', text):
        try:
            obj = json.loads(match.group())
            if "score" in obj:  # only accept if it looks like our schema
                return obj
        except Exception:
            continue
    # Last resort: try largest {...} block
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def _hold_employee_post_for_review(job: dict) -> dict:
    """A public hiring post is useful discovery evidence, but not an apply form."""
    if (
        job.get("source") == "employee_post"
        and not job.get("job_url_direct")
        and job.get("decision") == "auto_apply"
    ):
        job["decision"] = "manual_review"
        note = " Hiring signal found in an employee/recruiter LinkedIn post; no direct application URL was indexed."
        job["fit_reason"] = ((job.get("fit_reason") or "") + note).strip()
    return job


def score_job(
    job: dict,
    candidate_profile: str,
    model: str,
    base_url: str,
    score_thresholds: dict,
) -> dict:
    """
    Score a single job dict using Ollama.
    Mutates and returns the job dict with score, decision, fit_reason, etc.
    Pre-filters crowdsourced platforms before calling the LLM.
    """
    company = job.get("company", "")
    title   = job.get("title", "")

    # ── Pre-filter: crowdsourced platforms ──────────────────────────────────────
    if _is_crowdsourced_platform(company):
        logger.info(f"  [SKIP crowdsourced] {title} @ {company}")
        job["score"]             = 0
        job["decision"]          = "skip"
        job["fit_reason"]        = ""
        job["skip_reason"]       = f"{company} is a crowdsourced AI-labeling platform, not a professional career path."
        job["positioning_angle"] = "investments"
        job["score_breakdown"]   = {}
        job["outside_target_industry"] = False
        job["suggested_alternate"] = False
        return _enrich_with_unified_engine(job)

    blocked, block_reason = prefilter_job(job)
    if blocked:
        logger.info(f"  [SKIP prefilter] {title} @ {company}: {block_reason}")
        job["score"]             = 0
        job["decision"]          = "skip"
        job["fit_reason"]        = ""
        job["skip_reason"]       = block_reason
        job["positioning_angle"] = "investments"
        job["score_breakdown"]   = {}
        job["outside_target_industry"] = False
        job["suggested_alternate"] = False
        return _enrich_with_unified_engine(job)

    # Structured job profile + salary parse
    try:
        build_structured_job_profile(job, model, base_url, use_llm=True)
    except Exception as e:
        logger.debug(f"Job profile build: {e}")
    sal = parse_salary_from_text(job.get("description") or "")
    job.update(sal)

    min_salary = get_min_salary_from_config()
    below, sal_reason = check_salary_floor(job, min_salary)
    if below:
        logger.info(f"  [SKIP low salary] {title} @ {company}: {sal_reason}")
        job["score"] = min(job.get("score") or 0, 25)
        job["decision"] = "skip"
        job["fit_reason"] = ""
        job["skip_reason"] = sal_reason
        job["salary_below_minimum"] = True
        job["outside_target_industry"] = False
        job["suggested_alternate"] = False
        return _enrich_with_unified_engine(job)
    job["salary_below_minimum"] = False

    system = SCORING_SYSTEM_PROMPT.replace("{candidate_profile}", candidate_profile)
    try:
        from config.md_loader import get_requirements_for_scorer
        req_text = get_requirements_for_scorer()
        if req_text:
            system += (
                "\n\n## APPLICANT REQUIREMENTS (what the candidate wants)\n"
                + req_text[:6000]
            )
    except Exception:
        pass

    profile_block = job_profile_summary_for_scorer(job)
    sal_note = ""
    if job.get("salary_snippet"):
        sal_note = f"\nSalary mentioned: {job.get('salary_snippet')}"
        if job.get("min_monthly_aed"):
            sal_note += f" (~{job['min_monthly_aed']:,} AED/mo parsed)"

    job_text = f"""
ROLE TO SCORE:
Title:       {title}
Company:     {company}
Location:    {job.get('location', 'Unknown')}
Posted:      {job.get('date_posted', 'Unknown')}
Source:      {job.get('source', 'Unknown')}
URL:         {job.get('job_url', '')}
{sal_note}

STRUCTURED JOB PROFILE:
{profile_block}
"""

    full_prompt = system + "\n\n" + job_text + "\n\nReturn ONLY the JSON scoring object:"

    logger.debug(f"Scoring: {title} @ {company}")

    try:
        raw = _call_ollama(full_prompt, model, base_url)
        result = _extract_json(raw)

        if not result:
            logger.warning(f"Could not parse JSON for {title}. Raw: {raw[:200]}")
            job["score"] = 50
            job["decision"] = "manual_review"
            job["fit_reason"] = "Could not parse AI scoring — flagged for manual review"
            job["skip_reason"] = ""
            job["positioning_angle"] = "investments"
            return _enrich_with_unified_engine(job)

        score = max(0, min(100, int(result.get("score", 50))))
        job["score"] = score

        # Apply thresholds
        if score >= score_thresholds["auto_apply"]:
            job["decision"] = "auto_apply"
        elif score >= score_thresholds["manual_review"]:
            job["decision"] = "manual_review"
        else:
            job["decision"] = "skip"

        # Override with model's own decision if it's more conservative
        model_decision = result.get("decision", "").lower()
        if model_decision == "skip":
            job["decision"] = "skip"
        if result.get("human_applyable") is False:
            job["decision"] = "skip"
            job["score"] = min(score, 10)
            if not result.get("skip_reason"):
                result["skip_reason"] = "Role is not for human applicants"

        job["fit_reason"]         = result.get("fit_reason", "")
        job["skip_reason"]        = result.get("skip_reason", "")
        job["positioning_angle"]  = result.get("positioning_angle", "investments")
        job["score_breakdown"]    = result.get("breakdown", {})

        outside, outside_reason = is_outside_target_industry(job)
        if result.get("outside_target_industry") is True:
            outside = True
            if not outside_reason:
                outside_reason = "Marked outside target industries by scorer"
        job["outside_target_industry"] = outside
        job["outside_target_reason"] = outside_reason if outside else ""
        job["matches_stated_targets"] = bool(result.get("matches_stated_targets", not outside))
        job["suggested_alternate"] = bool(result.get("suggested_alternate"))
        job["alternate_suggestion_reason"] = (result.get("alternate_suggestion_reason") or "").strip()

        if job["suggested_alternate"] and score >= 75:
            prefix = "[Suggested fit outside your stated targets] "
            job["alternate_suggestion_reason"] = job["alternate_suggestion_reason"] or (
                "Strong match on skills and level despite different industry than you listed."
            )
            job["fit_reason"] = prefix + (job.get("fit_reason") or job["alternate_suggestion_reason"])
            if job["decision"] == "skip" and score >= 75:
                job["decision"] = "manual_review"
        elif outside and job["decision"] == "auto_apply" and not job["suggested_alternate"]:
            # Only downgrade to manual_review if the LLM *itself* flagged the
            # role as outside target.  The keyword heuristic alone is too strict
            # and was blocking every 75+ job that didn't contain "quant" / "investments"
            # in the raw text.  Trust the model's score when the heuristic fires
            # but the LLM did not flag outside_target_industry.
            if result.get("outside_target_industry") is True:
                job["decision"] = "manual_review"
                if outside_reason:
                    job["fit_reason"] = (job.get("fit_reason") or "") + f" [Off-target industry: {outside_reason}]"
            # else: keep auto_apply — heuristic fired but LLM was confident it's a fit

        if job["decision"] == "skip" and not (job.get("skip_reason") or "").strip():
            fit = (job.get("fit_reason") or "").strip()
            job["skip_reason"] = (
                f"Score {job['score']}/100 is below the apply threshold. {fit}".strip()
            )

        logger.info(
            f"  [{score}/100 -> {job['decision'].upper()}] "
            f"{title} @ {company}"
        )

    except Exception as e:
        logger.error(f"Scoring error for {title}: {e}")
        job["score"] = 50
        job["decision"] = "manual_review"
        job["fit_reason"] = f"Scoring error: {e}"
        job["skip_reason"] = ""
        job["positioning_angle"] = "investments"
        job["outside_target_industry"] = False
        job["suggested_alternate"] = False
        job["matches_stated_targets"] = True

    return _enrich_with_unified_engine(_hold_employee_post_for_review(job))


def _enrich_with_unified_engine(job: dict) -> dict:
    try:
        from agents.unified_engine import enrich_job_with_engine
        return enrich_job_with_engine(job)
    except Exception as exc:
        logger.debug("Unified engine enrich skipped: %s", exc)
        return job


def score_jobs_batch(
    jobs: list[dict],
    candidate_profile: str,
    model: str,
    base_url: str,
    score_thresholds: dict,
    progress_callback=None,
) -> list[dict]:
    """Score all jobs and return the list with scores filled in."""
    logger.info(f"Scoring {len(jobs)} jobs with {model}...")
    scored = []
    for i, job in enumerate(jobs, 1):
        try:
            from gui.stop_flag import check_stop
            check_stop(f"Stop requested — scored {i - 1}/{len(jobs)} jobs")
        except ImportError:
            pass
        logger.info(f"Scoring {i}/{len(jobs)}: {job['title']} @ {job['company']}")
        scored_job = score_job(job, candidate_profile, model, base_url, score_thresholds)
        scored.append(scored_job)
        if progress_callback:
            progress_callback(scored_job)
    return scored


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from config.config import (
        OLLAMA_MODEL, OLLAMA_BASE_URL, CANDIDATE_PROFILE, SCORE_THRESHOLDS
    )
    test_job = {
        "title": "Quantitative Researcher",
        "company": "Brevan Howard",
        "location": "Abu Dhabi",
        "date_posted": "2025-05-23",
        "source": "linkedin",
        "job_url": "https://linkedin.com/jobs/view/test",
        "description": (
            "We are looking for a quantitative researcher to join our systematic "
            "strategies team in Abu Dhabi. Strong Python and statistics background "
            "required. Experience with time-series data and signal generation preferred."
        ),
    }
    result = score_job(
        test_job, CANDIDATE_PROFILE, OLLAMA_MODEL, OLLAMA_BASE_URL, SCORE_THRESHOLDS
    )
    print(f"\nScore: {result['score']}/100 → {result['decision'].upper()}")
    print(f"Fit:   {result['fit_reason']}")
    print(f"Angle: {result['positioning_angle']}")
