import unittest

from scripts import render_cron_call


class FakeResponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class RenderCronCallTests(unittest.TestCase):
    def test_build_url_uses_daily_run_action_path(self):
        url = render_cron_call.build_url("daily-run", "https://us-market-pulse.onrender.com/")
        self.assertEqual(
            url,
            "https://us-market-pulse.onrender.com/api/actions/daily-run",
        )

    def test_call_action_posts_to_configured_url(self):
        calls = []

        def fake_post(url, headers, timeout):
            calls.append((url, headers, timeout))
            return FakeResponse(200, '{"ok":true,"run_date":"2026-06-09"}')

        code, body = render_cron_call.call_action(
            "daily-run",
            base_url="https://example.test",
            post=fake_post,
        )

        self.assertEqual(code, 0)
        self.assertIn("2026-06-09", body)
        self.assertEqual(calls[0][0], "https://example.test/api/actions/daily-run")

    def test_call_action_fails_on_http_error(self):
        def fake_post(url, headers, timeout):
            return FakeResponse(500, "server error")

        code, body = render_cron_call.call_action(
            "daily-run",
            base_url="https://example.test",
            post=fake_post,
        )

        self.assertEqual(code, 1)
        self.assertIn("server error", body)


if __name__ == "__main__":
    unittest.main()
