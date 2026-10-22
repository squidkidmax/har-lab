"""Network checks for the local demo; no third-party accounts are contacted."""

from http.cookiejar import CookieJar
import json
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from capture_runner.demo import DEMO_ACCOUNTS, demo_server


class DemoServerTests(unittest.TestCase):
    def setUp(self):
        self.server_context = demo_server()
        self.origin = self.server_context.__enter__()
        self.addCleanup(self.server_context.__exit__, None, None, None)

    def client(self):
        jar = CookieJar()
        return build_opener(HTTPCookieProcessor(jar)), jar

    def login(self, client, username="profile_01", password="DemoPass-01!"):
        request = Request(
            self.origin + "/login",
            data=urlencode({"username": username, "password": password}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return client.open(request, timeout=5)

    def test_five_test_accounts_login_and_profile(self):
        seen_sessions = set()
        for username, account in DEMO_ACCOUNTS.items():
            with self.subTest(username=username):
                client, cookies = self.client()
                with self.login(client, username, account["password"]) as response:
                    body = response.read().decode()
                    self.assertTrue(response.url.endswith("/dashboard"))
                    self.assertIn("<h1>Dashboard</h1>", body)
                    self.assertIn("fetch('/api/profile'", body)
                    self.assertIn('id="profile-ready"', body)
                session = next(c for c in cookies if c.name == "demo_session")
                self.assertTrue(session.has_nonstandard_attr("HttpOnly"))
                self.assertNotIn(session.value, seen_sessions)
                seen_sessions.add(session.value)
                with client.open(self.origin + "/api/profile", timeout=5) as response:
                    profile = json.load(response)
                    self.assertEqual(profile["username"], username)
                    self.assertEqual(profile["email"], account["email"])
                    self.assertEqual(profile["access_token"], f"demo-api-token-{username}")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_login_does_not_authenticate_other_browser(self):
        authenticated, _ = self.client()
        anonymous, _ = self.client()
        with self.login(authenticated):
            pass
        with self.assertRaises(HTTPError) as raised:
            anonymous.open(self.origin + "/api/profile", timeout=5)
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()
        with anonymous.open(self.origin + "/dashboard", timeout=5) as response:
            self.assertTrue(response.url.endswith("/login"))

    def test_invalid_credentials_rejected(self):
        client, cookies = self.client()
        for password in ("wrong", "mật-khẩu-khác"):
            with self.subTest(password=password):
                with self.assertRaises(HTTPError) as raised:
                    self.login(client, password=password)
                self.assertEqual(raised.exception.code, 401)
                raised.exception.close()
        self.assertEqual(list(cookies), [])

    def test_search_escapes_input_and_logout_revokes_cookie(self):
        client, cookies = self.client()
        with self.login(client):
            pass
        original_session = next(c.value for c in cookies if c.name == "demo_session")
        with client.open(self.origin + "/search?" + urlencode({"q": "<script>alert(1)</script>"}), timeout=5) as response:
            body = response.read().decode()
            self.assertIn("<h1>Search results</h1>", body)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
            self.assertNotIn("<script>alert(1)</script>", body)
        with client.open(Request(self.origin + "/logout", data=b""), timeout=5) as response:
            self.assertTrue(response.url.endswith("/login"))
        self.assertEqual(list(cookies), [])
        with self.assertRaises(HTTPError) as raised:
            client.open(Request(self.origin + "/api/profile", headers={"Cookie": f"demo_session={original_session}"}), timeout=5)
        self.assertEqual(raised.exception.code, 401)
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()
