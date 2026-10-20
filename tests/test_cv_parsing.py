import io
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from cv import extraction, parsing
from cv.forms import CVConfirmForm

from .helpers import CV_LINES, make_docx, make_pdf, make_scanned_pdf

CV_TEXT = "\n".join(CV_LINES)


def upload(name, data):
    return SimpleUploadedFile(name, data)


class ParsingTests(SimpleTestCase):
    def test_pdf_text_extracted(self):
        up = upload("cv.pdf", make_pdf())
        kind = parsing.validate_upload(up)
        text = parsing.extract_text(up, kind)
        self.assertEqual(kind, "pdf")
        self.assertIn("jane.doe@example.com", text)
        self.assertIn("Django", text)

    def test_docx_text_extracted(self):
        up = upload("cv.docx", make_docx())
        kind = parsing.validate_upload(up)
        self.assertEqual(kind, "docx")
        self.assertIn("Senior Python Developer", parsing.extract_text(up, kind))

    def test_scanned_pdf_gives_clear_message(self):
        up = upload("scan.pdf", make_scanned_pdf())
        kind = parsing.validate_upload(up)
        with self.assertRaises(parsing.ScannedDocumentError) as ctx:
            parsing.extract_text(up, kind)
        self.assertIn("text-based", str(ctx.exception.message))

    def test_rejects_wrong_extension(self):
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.txt", b"hello"))
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.exe", b"MZ..."))

    def test_rejects_pdf_extension_with_other_content(self):
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.pdf", b"<html>not a pdf</html>"))

    def test_rejects_docx_extension_with_pdf_content(self):
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.docx", make_pdf()))

    def test_rejects_zip_that_is_not_docx(self):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("evil.txt", "x")
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.docx", buf.getvalue()))

    @override_settings(CV_MAX_BYTES=1000)
    def test_rejects_oversize(self):
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.pdf", make_pdf()))

    def test_rejects_empty(self):
        with self.assertRaises(ValidationError):
            parsing.validate_upload(upload("cv.pdf", b""))

    def test_corrupt_pdf(self):
        up = upload("cv.pdf", b"%PDF-1.4 garbage")
        parsing.validate_upload(up)
        with self.assertRaises(ValidationError):
            parsing.extract_text(up, "pdf")


class ExtractionTests(SimpleTestCase):
    def test_heuristic_fallback(self):
        data = extraction.heuristic_extract(CV_TEXT)
        self.assertEqual(data["full_name"], "Jane Doe")
        self.assertEqual(data["email"], "jane.doe@example.com")
        self.assertEqual(data["designation"], "Senior Python Developer")
        self.assertIn("Python", data["skills"])
        self.assertEqual(data["years_experience"], 8.0)

    def test_clean_drops_invented_values(self):
        raw = {
            "full_name": "Jane Doe", "email": "invented@nowhere.com", "phone": "+44 7700 900123",
            "designation": "Senior Python Developer", "alternate_titles": ["Backend Engineer"],
            "skills": ["Python", "Kubernetes", "Django"], "years_experience": 8,
            "location": None, "summary": "Two sentences. Here.",
        }
        out = extraction.clean_extraction(raw, CV_TEXT)
        self.assertEqual(out["email"], "jane.doe@example.com")   # invented one replaced by the real one in the text
        self.assertIsNone(out["phone"])                           # not in the CV
        self.assertEqual(out["skills"], ["Python", "Django"])      # Kubernetes is not in the CV
        self.assertIsNone(out["location"])

    def test_clean_keeps_real_phone(self):
        out = extraction.clean_extraction({"phone": "+1 415 555 0134"}, CV_TEXT)
        self.assertEqual(out["phone"], "+1 415 555 0134")

    def test_clean_handles_bad_types(self):
        out = extraction.clean_extraction(
            {"skills": "Python", "years_experience": "eight", "alternate_titles": None, "full_name": 5}, CV_TEXT)
        self.assertEqual(out["skills"], [])
        self.assertIsNone(out["years_experience"])
        self.assertEqual(out["alternate_titles"], [])
        self.assertIsNone(out["full_name"])

    def test_clean_rejects_absurd_years(self):
        self.assertIsNone(extraction.clean_extraction({"years_experience": 400}, CV_TEXT)["years_experience"])

    @override_settings(ANTHROPIC_API_KEY="")
    def test_no_key_uses_heuristics_with_note(self):
        result = extraction.extract_profile(CV_TEXT)
        self.assertEqual(result.method, "heuristic")
        self.assertIn("ANTHROPIC_API_KEY", result.note)

    @override_settings(ANTHROPIC_API_KEY="sk-test")
    def test_llm_failure_falls_back(self):
        from core import llm
        with mock.patch.object(llm, "complete_json", side_effect=llm.LLMError("boom")):
            result = extraction.extract_profile(CV_TEXT)
        self.assertEqual(result.method, "heuristic")
        self.assertIn("boom", result.note)

    @override_settings(ANTHROPIC_API_KEY="sk-test")
    def test_llm_success_is_validated(self):
        from core import llm
        fake = {"full_name": "Jane Doe", "email": None, "phone": None, "designation": "Senior Python Developer",
                "alternate_titles": ["Backend Engineer"], "skills": ["Python", "Rust"],
                "years_experience": 8, "location": "Lisbon, Portugal", "summary": "S1. S2."}
        with mock.patch.object(llm, "complete_json", return_value=fake):
            result = extraction.extract_profile(CV_TEXT)
        self.assertEqual(result.method, "llm")
        self.assertEqual(result.data["skills"], ["Python"])
        self.assertEqual(result.data["email"], "jane.doe@example.com")


class ConfirmFormTests(SimpleTestCase):
    def test_lists_split_and_deduped(self):
        form = CVConfirmForm({"designation": " Python  Dev ", "alternate_titles": "A, B\nA", "skills": "x;y, z",
                              "full_name": "Jane", "location": "", "email": "", "phone": ""})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["designation"], "Python Dev")
        self.assertEqual(form.cleaned_data["alternate_titles"], ["A", "B"])
        self.assertEqual(form.cleaned_data["skills"], ["x", "y", "z"])

    def test_requires_designation_and_name(self):
        form = CVConfirmForm({"designation": "", "full_name": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("designation", form.errors)
        self.assertIn("full_name", form.errors)

    def test_rejects_bad_email_and_phone(self):
        form = CVConfirmForm({"designation": "d", "full_name": "n", "email": "nope", "phone": "abc"})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertIn("phone", form.errors)
