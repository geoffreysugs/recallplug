import unittest

from app.redact import redact
from app.titles import ActivityLog


class RedactTests(unittest.TestCase):
    def test_email(self):
        self.assertEqual(redact("Mail from amy.lin+work@example.com.tw - Outlook"),
                         "Mail from [EMAIL] - Outlook")

    def test_taiwan_mobile(self):
        for raw in ["0912-345-678", "0912 345 678", "0912345678"]:
            self.assertEqual(redact(f"Call {raw} today"), "Call [PHONE] today", raw)

    def test_plus_886(self):
        for raw in ["+886 912 345 678", "+886-912-345-678", "+886912345678",
                    "+886-2-2345-6789", "+886 2 2345 6789", "+886(0)912345678"]:
            self.assertEqual(redact(f"Tel {raw}"), "Tel [PHONE]", raw)

    def test_card_numbers(self):
        for raw in ["4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111",
                    "3782 822463 10005", "6011000990139424123"]:
            self.assertEqual(redact(f"Card {raw} ok"), "Card [CARD] ok", raw)

    def test_taiwan_id(self):
        self.assertEqual(redact("Form A123456789 - Word"), "Form [ID] - Word")
        self.assertEqual(redact("f287654321.pdf"), "[ID].pdf")

    def test_several_in_one_title(self):
        self.assertEqual(redact("A123456789 / 0912-345-678 / bob@x.org"),
                         "[ID] / [PHONE] / [EMAIL]")

    def test_negative_cases_are_untouched(self):
        for text in ["Q3 2026 Forecast.xlsx", "Meeting 2026-10-25 14:30", "2026/10/25",
                     "Python 3.12.10", "v2.10.3 release notes", "Invoice 12345678",
                     "Room 0912", "Order 123-456-789-012", "Contract Draft v2.txt",
                     "AB12345678", "Page 1 of 20", "1920x1080 wallpaper"]:
            self.assertEqual(redact(text), text, text)

    def test_activity_log_stores_redacted_titles(self):
        log = ActivityLog(5)
        self.assertTrue(log.add("Reply to amy@example.com", "Outlook"))
        self.assertEqual(log.recent(1)[0].title, "Reply to [EMAIL]")
        # a different address redacts to the same title, so it is a duplicate
        self.assertFalse(log.add("Reply to bob@example.com", "Outlook"))


if __name__ == "__main__":
    unittest.main()
