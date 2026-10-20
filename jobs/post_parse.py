"""Best-effort title / company guesses from post text. Returns None when unsure; never invents data."""
import re

from cv.extraction import TITLE_RE

_TOK = r"[A-Z][\w+#/&'-]*(?:\.\w+)*"          # internal dots only (Node.js), never a sentence end
_TITLE_AFTER = re.compile(
    r"(?:hiring|looking for|seeking|opening for|openings? for|vacanc(?:y|ies) for|position of|role of|"
    r"we need|join us as)[ \t]*[:\-\u2013]?[ \t]*(?:an?[ \t]+|the[ \t]+)?"
    rf"({_TOK}(?:[ \t]+{_TOK}){{0,5}})")
_COMPANY_IS_HIRING = re.compile(r"(?:^|[\s:|\-–])([A-Z][\w&.'-]*(?:\s[A-Z][\w&.'-]*){0,3})\s+is\s+(?:now\s+)?hiring\b")
_COMPANY_AT = re.compile(r"\b(?:hiring|openings?|roles?|positions?)\b[^.\n]{0,60}?\bat\s+([A-Z][\w&.'-]*(?:\s[A-Z][\w&.'-]*){0,3})")
_TITLE_TAIL = re.compile(r"\s+(?:Remote|Hybrid|Fulltime|Full-time|Part-time|Urgent|Immediate|Opening|Role|Position|Job)\s*$", re.I)
_BAD_COMPANY = {"we", "i", "our", "hiring", "remote", "linkedin", "team", "company", "they", "who", "it", "this"}
_RESULT_PREFIX = re.compile(r"^.{1,80}?\s+on\s+LinkedIn:\s*", re.I)


def clean_result_title(title: str) -> str:
    """Brave/Google titles look like 'Jane Doe on LinkedIn: We are hiring ...'; keep the post text."""
    t = _RESULT_PREFIX.sub("", title or "")
    t = re.sub(r"\s*[|\-–]\s*LinkedIn\s*$", "", t, flags=re.I)
    return " ".join(t.split())


def guess_title(text: str) -> str | None:
    for m in _TITLE_AFTER.finditer(text or ""):
        cand = _TITLE_TAIL.sub("", " ".join(m.group(1).split())).strip(" -:,.")
        if cand and TITLE_RE.search(cand) and len(cand) <= 70:
            return cand
    return None


def guess_company(text: str) -> str | None:
    for rx in (_COMPANY_IS_HIRING, _COMPANY_AT):
        for m in rx.finditer(text or ""):
            cand = m.group(1).strip(" .,-")
            if cand and cand.lower() not in _BAD_COMPANY and len(cand) >= 2 and "@" not in cand:
                return cand
    return None
