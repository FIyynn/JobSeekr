"""
Shared instructions for apply-time LLM prompts (form fill, Q&A, chat).

Loaded by form_filler, application_qa, and GUI chat.
"""

from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
DEFAULT_RESUME_PATH = r"C:/Users/Lordy/Downloads/4.5cx_Found.pdf"

RESUME_BY_ANGLE = {
    "quant": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "investments": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "pe": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "finance": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "trading": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "commodities": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "energy": str(_BASE / "resumes" / "Resume_Rashed_Quant_Investment.pdf"),
    "ai": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "data": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "ml": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "research": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "space": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "defense": str(_BASE / "resumes" / "Resume_Rashed_AI_DataScience.pdf"),
    "cyber": str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "fintech": str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "startup": str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "strategy": str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
    "climate": str(_BASE / "resumes" / "Resume_Rashed_Tech_Startup.pdf"),
}
_DEFAULT_ANGLE_RESUME = str(_BASE / "Rashed_Alneyadi_Resume.pdf")

APPLY_AGENT_RULES = """
APPLICATION AGENT RULES (always follow):

Resume file:
- Use the candidate's resume PDF from settings (default: 4.5cx_Found.pdf).
- On any form with a resume/CV upload or autofill option, attach that file first.
- Workday: click "Autofill with resume" (never "Apply Manually" when autofill is shown).
- Greenhouse: click "Autofill with Resume" / resume autofill before manual fields.
- Other ATS sites: upload resume to file inputs first, then use any autofill/fill-with-resume button.
- Do NOT create or substitute a different resume PDF unless the user explicitly asked and approved tailoring for that job.

Voice:
- Always answer in first person as the applicant: "I am skilled", "I have experience" — never "Rashed is skilled" or third person.

Tailoring:
- Never tailor, rewrite, or generate a job-specific resume variant without explicit user approval for that job.

Profile grounding (Chat + application answers):
- Use ONLY employers, projects, schools, and metrics listed in the applicant profile / fact sheet.
- NEVER invent companies, internships, startups, or projects (e.g. generic "fintech startup market risk framework").
- Education: BA Mathematics + CS minor, **New York University (New York campus)**, graduated December 2024.
- **NYU Abu Dhabi (NYUAD)** = Quantum Computation Research Assistant only — NOT the degree-granting campus.
- For behavioral questions ("project that showed difficulty / critical thinking"), pick ONE real item below:
  • ADIC Active Investments & Equity Intern — Python attribution, portfolio tracking, manager analysis
  • ADIA Private Equity Intern — APAC case studies, modeling, investment memos
  • DIBA — Derivative Influenced Buy Analysis (research prototype; not live trading)
  • MIT/MBRSC space robotics — path optimization, MATLAB/Python simulation
  • NYUAD quantum computation — 3-qubit XXZ model, parameter sweeps
  • Polygon Technical Infrastructures — founder, cybersecurity/software delivery
  • RECtify Brokers — climate-tech, emissions/REC reporting
- Use verified metrics only when they appear in the profile (e.g. ~70% variance explained, ~30% traversal time).
- If nothing in the profile fits, say so and ask which real project to use — do not fabricate.
""".strip()


def pick_resume_by_angle(angle: str) -> str:
    """Angle-based resume fallback when profile_settings path is unset."""
    key = (angle or "").lower().split("/")[0].strip()
    path = RESUME_BY_ANGLE.get(key, _DEFAULT_ANGLE_RESUME)
    return path if Path(path).exists() else _DEFAULT_ANGLE_RESUME


def get_resume_path() -> str:
    """Resume path: profile_settings → DEFAULT_RESUME_PATH → project default."""
    try:
        from agents.profile_manager import get_default_resume_path
        p = (get_default_resume_path() or "").strip()
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    if Path(DEFAULT_RESUME_PATH).exists():
        return DEFAULT_RESUME_PATH
    return DEFAULT_RESUME_PATH


def rules_block() -> str:
    """Paragraph to append to LLM system/answer prompts."""
    parts = [APPLY_AGENT_RULES]
    try:
        from agents.chat_saved_prompts import prompts_block_for_agent
        extra = prompts_block_for_agent()
        if extra:
            parts.append(extra)
    except Exception:
        pass
    return "\n\n".join(parts)
