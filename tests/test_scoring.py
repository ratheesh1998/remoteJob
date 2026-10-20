from django.test import SimpleTestCase

from jobs.scoring import match_score, tokens
from jobs.types import JobData


def job(title, description="", tags=None):
    return JobData(title=title, company="Acme", url="https://acme.com/1", source="x",
                   description=description, tags=tags or [])


SKILLS = ["Python", "Django", "PostgreSQL", "Docker"]


class ScoringTests(SimpleTestCase):
    def test_perfect_match_high(self):
        j = job("Senior Python Developer", "We use Python, Django, PostgreSQL and Docker daily.")
        self.assertGreaterEqual(match_score(j, "Python Developer", [], SKILLS), 95)

    def test_unrelated_low(self):
        j = job("Sales Manager", "Grow our enterprise accounts.")
        self.assertLess(match_score(j, "Python Developer", [], SKILLS), 15)

    def test_title_only_match_is_mid(self):
        j = job("Python Developer", "Nice team.")
        # Title matches fully; none of these skills appear in the posting.
        score = match_score(j, "Python Developer", [], ["Django", "PostgreSQL", "Docker"])
        self.assertEqual(score, 50)

    def test_skills_only_match_is_partial(self):
        j = job("Backend Engineer", "Python and Django and PostgreSQL experience.")
        score = match_score(j, "Python Developer", [], SKILLS)
        self.assertTrue(20 <= score <= 80, score)

    def test_alternate_title_counts_slightly_less(self):
        j = job("Backend Engineer", "")
        alt = match_score(j, "Python Developer", ["Backend Engineer"], [])
        exact = match_score(job("Python Developer"), "Python Developer", ["Backend Engineer"], [])
        self.assertGreater(exact, alt)
        self.assertGreater(alt, 50)

    def test_partial_title_overlap(self):
        j = job("Python Engineer")
        self.assertTrue(0 < match_score(j, "Python Developer", [], []) < 100)

    def test_bounded_and_int(self):
        s = match_score(job("x"), "", [], [])
        self.assertEqual(s, 0)
        self.assertIsInstance(match_score(job("Python Developer"), "Python Developer", [], SKILLS), int)

    def test_skill_word_boundaries(self):
        # "Go" must not match inside "going"; "C++" must match literally.
        self.assertEqual(match_score(job("Engineer", "We are going far."), "", [], ["Go"]), 0)
        self.assertGreater(match_score(job("Engineer", "Strong C++ skills"), "", [], ["C++"]), 0)

    def test_sorted_by_score(self):
        jobs = [job("Sales Manager"), job("Python Developer", "Python Django"), job("Python Engineer")]
        scored = sorted(jobs, key=lambda j: -match_score(j, "Python Developer", [], ["Python", "Django"]))
        self.assertEqual(scored[0].title, "Python Developer")
        self.assertEqual(scored[-1].title, "Sales Manager")

    def test_generic_role_noun_alone_is_weak(self):
        s = match_score(job("Operations Engineer"), "Python Developer", ["Python Engineer"], [])
        self.assertLess(s, 25)

    def test_relevance_rule(self):
        from jobs.scoring import is_relevant
        des, alts, skills = "Python Developer", ["Backend Developer"], ["Docker", "AWS", "Redis", "REST"]
        rails = job("Tech Lead Rails Engineer", "We use Docker, AWS, Redis and REST.")
        rails.match_score = match_score(rails, des, alts, skills)
        self.assertFalse(is_relevant(rails, des, alts, skills, 20))     # shared tools only, wrong role
        py = job("Python Developer", "Docker")
        py.match_score = match_score(py, des, alts, skills)
        self.assertTrue(is_relevant(py, des, alts, skills, 20))

    def test_tokens_drop_seniority(self):
        self.assertEqual(tokens("Senior Python Developer"), ["python", "developer"])
