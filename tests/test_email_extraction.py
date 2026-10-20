from django.test import SimpleTestCase

from jobs.text import extract_application_email, html_to_text


class ExtractEmailTests(SimpleTestCase):
    def test_simple(self):
        self.assertEqual(extract_application_email("Send your CV to jobs@acme.io today."), "jobs@acme.io")

    def test_trailing_punctuation(self):
        self.assertEqual(extract_application_email("Email careers@acme.co.uk."), "careers@acme.co.uk")
        self.assertEqual(extract_application_email("(apply@acme.com)"), "apply@acme.com")

    def test_none_when_absent(self):
        self.assertIsNone(extract_application_email("Apply through our website."))
        self.assertIsNone(extract_application_email(""))
        self.assertIsNone(extract_application_email(None))

    def test_rejects_noreply_and_placeholders(self):
        self.assertIsNone(extract_application_email("noreply@acme.com no-reply@acme.com user@example.com"))

    def test_rejects_image_filenames(self):
        self.assertIsNone(extract_application_email("logo@2x.png and banner@3x.jpg"))

    def test_prefers_hiring_address(self):
        text = "Questions: bob@acme.com. Applications: careers@acme.com"
        self.assertEqual(extract_application_email(text), "careers@acme.com")

    def test_first_when_no_preference(self):
        self.assertEqual(extract_application_email("a@x.com then b@y.com"), "a@x.com")

    def test_lowercased(self):
        self.assertEqual(extract_application_email("Jobs@Acme.COM"), "jobs@acme.com")

    def test_mailto_link_survives_html_stripping(self):
        text = html_to_text('<p>Apply: <a href="mailto:talent@acme.com">click here</a></p>')
        self.assertEqual(extract_application_email(text), "talent@acme.com")

    def test_rejects_accommodation_and_privacy_addresses(self):
        text = ("If you need reasonable accommodations during the process, email "
                "reasonableaccommodations@airbnb.com. Privacy questions: privacy@airbnb.com.")
        self.assertIsNone(extract_application_email(text))

    def test_rejects_address_whose_context_is_not_applying(self):
        text = "To report a concern about this posting or request disability support write to help@acme.com."
        self.assertIsNone(extract_application_email(text))

    def test_picks_application_address_over_accommodation_address(self):
        text = ("Accommodations: accessibility@acme.com. To apply, send your resume to hiring-team@acme.com "
                "or reach out to someone@acme.com")
        self.assertEqual(extract_application_email(text), "hiring-team@acme.com")

    def test_apply_context_beats_position(self):
        text = "Questions? ask bob@acme.com. To apply send your CV to alice@acme.com"
        self.assertEqual(extract_application_email(text), "alice@acme.com")

    def test_not_fooled_by_url_userinfo_or_partial(self):
        self.assertIsNone(extract_application_email("see https://acme.com/@handle for more"))


class HtmlStripTests(SimpleTestCase):
    def test_strips_tags_scripts_and_entities(self):
        out = html_to_text('<p>Hello &amp; <b>welcome</b></p><script>alert(1)</script><style>p{}</style><p>Next</p>')
        self.assertNotIn("<", out)
        self.assertNotIn("alert", out)
        self.assertIn("Hello & welcome", out)
        self.assertIn("Next", out)

    def test_no_markup_survives(self):
        out = html_to_text('<img src=x onerror=alert(1)><a href="javascript:evil()">x</a>')
        self.assertNotIn("onerror", out)
        self.assertNotIn("javascript", out)

    def test_empty(self):
        self.assertEqual(html_to_text(None), "")
