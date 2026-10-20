"""match_score 0-100: 50 points for title overlap with the CV designation, 50 for skill overlap."""
import re

from .types import JobData

_STOP = {
    "and", "or", "the", "of", "for", "a", "an", "in", "to", "with", "at", "remote", "senior", "junior",
    "jr", "sr", "lead", "staff", "principal", "mid", "level", "ii", "iii", "iv", "intern", "internship",
}
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
# Role nouns shared by thousands of unrelated jobs: a match on "engineer" alone says little.
_GENERIC = {"engineer", "developer", "engineering", "programmer", "specialist", "analyst", "manager",
            "consultant", "architect", "designer", "software", "associate", "officer", "expert", "professional"}
GENERIC_WEIGHT = 0.3
TITLE_WEIGHT, SKILL_WEIGHT = 50, 50
ALT_TITLE_FACTOR = 0.85
SKILL_DENOMINATOR_CAP = 8   # matching 8+ of the CV's skills earns the full skill score


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN_RE.findall((text or "").lower()):
        t = t.strip(".")
        if t and t not in _STOP and (len(t) >= 2 or t in ("c", "r")):
            out.append(t)
    return out


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def title_score(job_title: str, designation: str, alternates: list[str]) -> float:
    """0..1 — best match between the job title and any of the CV titles."""
    job_tokens = tokens(job_title)
    job_set = set(job_tokens)
    best = 0.0
    for i, title in enumerate([designation] + list(alternates)):
        t_tokens = tokens(title)
        if not t_tokens:
            continue
        factor = 1.0 if i == 0 else ALT_TITLE_FACTOR
        if _contains_sequence(job_tokens, t_tokens):
            value = 1.0
        else:
            weight = lambda t: GENERIC_WEIGHT if t in _GENERIC else 1.0
            wanted = set(t_tokens)
            value = 0.8 * sum(weight(t) for t in wanted & job_set) / sum(weight(t) for t in wanted)
        best = max(best, value * factor)
    return best


def skill_in_text(skill: str, text_lower: str) -> bool:
    s = skill.strip().lower()
    if not s:
        return False
    return re.search(r"(?<![\w+#])" + re.escape(s) + r"(?![\w+#])", text_lower) is not None


def skills_score(job: JobData, skills: list[str]) -> float:
    skills = [s for s in skills if s.strip()]
    if not skills:
        return 0.0
    haystack = " ".join([job.title, " ".join(job.tags), job.description]).lower()
    matched = sum(1 for s in skills if skill_in_text(s, haystack))
    return min(1.0, matched / min(len(skills), SKILL_DENOMINATOR_CAP))


def match_score(job: JobData, designation: str, alternates: list[str], skills: list[str]) -> int:
    has_title = bool(tokens(designation) or any(tokens(a) for a in alternates))
    has_skills = any(s.strip() for s in skills)
    t = title_score(job.title, designation, alternates) if has_title else 0.0
    s = skills_score(job, skills) if has_skills else 0.0
    if has_title and has_skills:
        score = t * TITLE_WEIGHT + s * SKILL_WEIGHT
    elif has_title:
        score = t * 100
    elif has_skills:
        score = s * 100
    else:
        score = 0
    return max(0, min(100, round(score)))


MIN_TITLE_RELEVANCE = 0.25    # some real overlap with a CV title...
STRONG_SCORE = 60             # ...or a score that needs both title overlap and strong skills


def is_relevant(job: JobData, designation: str, alternates: list[str], skills: list[str], min_score: int) -> bool:
    """Keep a job only if it matches the designation, not merely a couple of shared skills."""
    score = job.match_score
    if score < min_score:
        return False
    return score >= STRONG_SCORE or title_score(job.title, designation, alternates) >= MIN_TITLE_RELEVANCE
