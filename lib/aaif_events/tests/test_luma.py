import datetime as dt
import email.message
import io
import unittest
import urllib.error
import urllib.request
import urllib.response
from unittest import mock

from aaif_events import luma


def view(**details):
    return {"title": details.get("EVENT TITLE", ""), "details": details,
            "phases": [], "date": None}


IRL = {"EVENT TITLE": "Eval Night · Builder Series",
       "DATE & TIME": "Wed · August 12, 2026 · 18:00 — late",
       "VENUE": "Github HQ", "LOCATION / CITY": "San Francisco",
       "CAPACITY / RSVPS": "120 RSVPs", "LUMA URL": "luma.com/aaif-sanfrancisco"}


class TestAvailable(unittest.TestCase):
    def test_env_key_means_connected(self):
        with mock.patch.dict("os.environ", {"LUMA_API_KEY": "secret"}):
            self.assertTrue(luma.available())

    def test_no_key_anywhere_means_not_connected_not_raising(self):
        with mock.patch.dict("os.environ", {"LUMA_API_KEY": ""}), \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=44, stdout="", stderr=(
                               "security: SecKeychainSearchCopyNext: The specified item "
                               "could not be found in the keychain."))):
            self.assertFalse(luma.available())

    def test_keychain_failure_is_distinguished_from_no_key(self):
        # a locked keychain / denied ACL must not masquerade as "no key stored"
        with mock.patch.dict("os.environ", {"LUMA_API_KEY": ""}), \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=1, stdout="",
                                                  stderr="User interaction is not allowed.")):
            with self.assertRaises(luma.LumaError) as cm:
                luma.api_key()
            self.assertIn("Keychain lookup failed", str(cm.exception))
            self.assertFalse(luma.available())   # still degrades, never raises

    def test_the_keychain_hint_prompts_instead_of_taking_the_key_inline(self):
        with mock.patch.dict("os.environ", {"LUMA_API_KEY": ""}), \
                mock.patch("subprocess.run", side_effect=OSError):
            with self.assertRaises(luma.LumaError) as cm:
                luma.api_key()
        text = str(cm.exception)
        self.assertIn("-a aaif -w\n", text)
        self.assertNotIn("THE_KEY", text)
        self.assertIn("never paste", text)

    def test_missing_security_binary_means_not_connected_not_raising(self):
        # non-macOS: the `security` CLI doesn't exist at all
        with mock.patch.dict("os.environ", {"LUMA_API_KEY": ""}), \
                mock.patch("subprocess.run", side_effect=FileNotFoundError("security")):
            self.assertFalse(luma.available())
            with self.assertRaises(luma.LumaError):
                luma.api_key()


class TestCall(unittest.TestCase):
    """The retry contract: GETs retry transient errors, writes never blind-retry
    (a timed-out create may have gone through) except 429."""

    def setUp(self):
        for p in (mock.patch.dict("os.environ", {"LUMA_API_KEY": "k"}),
                  mock.patch("time.sleep")):
            p.start()
            self.addCleanup(p.stop)

    def _http_error(self, code):
        return urllib.error.HTTPError("https://x", code, "err", None, io.BytesIO(b"boom"))

    def _ok(self):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = b'{"ok": true}'
        return r

    def test_get_retries_transient_then_succeeds(self):
        with mock.patch("aaif_events.luma._urlopen",
                        side_effect=[self._http_error(503), self._ok()]) as uo:
            self.assertEqual(luma.call("GET", "/v1/x"), {"ok": True})
            self.assertEqual(uo.call_count, 2)

    def test_post_transient_error_is_not_retried(self):
        with mock.patch("aaif_events.luma._urlopen",
                        side_effect=self._http_error(503)) as uo:
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("POST", "/v1/events/create", body={})
            self.assertEqual(uo.call_count, 1)
            self.assertIn("may still have gone through", str(cm.exception))
            self.assertEqual(cm.exception.status, 503)

    def test_post_429_is_retried(self):
        with mock.patch("aaif_events.luma._urlopen",
                        side_effect=[self._http_error(429), self._ok()]) as uo:
            self.assertEqual(luma.call("POST", "/v1/events/create", body={}), {"ok": True})
            self.assertEqual(uo.call_count, 2)

    def test_get_4xx_fails_immediately_with_status(self):
        with mock.patch("aaif_events.luma._urlopen",
                        side_effect=self._http_error(404)) as uo:
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x")
            self.assertEqual(uo.call_count, 1)
            self.assertEqual(cm.exception.status, 404)

    def test_a_200_html_body_is_a_lumaerror_not_a_bare_jsondecodeerror(self):
        r = mock.MagicMock()
        r.__enter__.return_value = r
        r.read.return_value = b"<html>maintenance</html>"
        with mock.patch("aaif_events.luma._urlopen", return_value=r):
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x")
            self.assertIn("not JSON", str(cm.exception))

    def test_an_error_body_is_not_embedded_only_its_message_field(self):
        body = io.BytesIO(b'{"message": "calendar not found", "request_headers": {"x-luma-api-key": "k"}}')
        err = urllib.error.HTTPError("https://x", 404, "err", None, body)
        with mock.patch("aaif_events.luma._urlopen", side_effect=err):
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x")
        text = str(cm.exception)
        self.assertIn("HTTP 404: calendar not found", text)
        self.assertNotIn("request_headers", text)
        self.assertNotIn('"k"', text)

    def test_a_non_json_error_body_contributes_nothing(self):
        with mock.patch("aaif_events.luma._urlopen", side_effect=self._http_error(404)):
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x")
        self.assertEqual(str(cm.exception), "GET /v1/x -> HTTP 404")

    def test_a_redirect_is_refused_and_never_followed(self):
        """A 302 raises through the real opener chain; the request that would
        carry x-luma-api-key to the new host is never made."""
        class Answer(urllib.request.BaseHandler):
            handler_order = 100
            urls = []

            def https_open(self, req):
                self.urls.append(req.full_url)
                msg = email.message.Message()
                msg["Location"] = "https://elsewhere.example/v1/x"
                resp = urllib.response.addinfourl(io.BytesIO(b""), msg, req.full_url, 302)
                resp.msg = "Found"
                return resp

        opener = urllib.request.build_opener(luma._NoRedirect(), Answer())
        with mock.patch("aaif_events.luma._OPENER", opener):
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x")
        self.assertEqual(cm.exception.status, 302)
        self.assertIn("redirect refused", str(cm.exception))
        self.assertEqual(Answer.urls, [luma.BASE + "/v1/x"])

    def test_get_network_error_retries_then_raises_without_status(self):
        with mock.patch("aaif_events.luma._urlopen",
                        side_effect=urllib.error.URLError("down")) as uo:
            with self.assertRaises(luma.LumaError) as cm:
                luma.call("GET", "/v1/x", retries=2)
            self.assertEqual(uo.call_count, 2)
            self.assertIsNone(cm.exception.status)


class TestResolveEventId(unittest.TestCase):
    def test_calendar_entity_raises_not_an_event(self):
        with mock.patch.object(luma, "lookup_slug",
                               return_value={"entity": {"type": "calendar"}}):
            with self.assertRaises(luma.NotAnEventUrl):
                luma.resolve_event_id("luma.com/aaif-sanfrancisco")

    def test_api_id_fallback(self):
        with mock.patch.object(luma, "lookup_slug",
                               return_value={"entity": {"type": "event",
                                                        "event": {"api_id": "evt-1"}}}):
            self.assertEqual(luma.resolve_event_id("https://luma.com/x"), "evt-1")

    def test_event_with_no_id_raises(self):
        with mock.patch.object(luma, "lookup_slug",
                               return_value={"entity": {"type": "event", "event": {}}}):
            with self.assertRaises(luma.LumaError):
                luma.resolve_event_id("https://luma.com/x")


class TestUploadImage(unittest.TestCase):
    def test_unsupported_extension_rejected_before_any_network(self):
        with self.assertRaises(luma.LumaError):
            luma.upload_image("banner.pdf")


class TestSlugOfUrl(unittest.TestCase):
    def test_forms(self):
        for given in ("https://luma.com/ia70fwmm", "https://lu.ma/ia70fwmm/",
                      "luma.com/ia70fwmm?utm=x", "ia70fwmm"):
            self.assertEqual(luma.slug_of_url(given), "ia70fwmm", given)


class TestEventTimes(unittest.TestCase):
    def test_start_plus_duration(self):
        start, end = luma.event_times(IRL["DATE & TIME"], "America/Los_Angeles", 3)
        self.assertEqual((start.year, start.month, start.day, start.hour, start.minute),
                         (2026, 8, 12, 18, 0))
        self.assertEqual(end - start, dt.timedelta(hours=3))

    def test_explicit_end_time(self):
        start, end = luma.event_times("July 8, 2026 · 17:30 — 20:30", "Europe/Berlin", 3)
        self.assertEqual((start.hour, end.hour, end.minute), (17, 20, 30))

    def test_end_past_midnight_rolls_to_next_day(self):
        start, end = luma.event_times("July 8, 2026 · 21:00 — 00:30", "Europe/Berlin", 3)
        self.assertEqual(end.day, start.day + 1)

    def test_12_hour_times_honor_am_pm(self):
        # "6:00 PM" must be 18:00, never a silent 6 AM event
        start, end = luma.event_times("July 8, 2026 · 6:00 PM — 9:30 pm", "UTC", 3)
        self.assertEqual((start.hour, end.hour, end.minute), (18, 21, 30))

    def test_noon_and_midnight_edge_cases(self):
        start, _ = luma.event_times("July 8, 2026 · 12:00 PM", "UTC", 1)
        self.assertEqual(start.hour, 12)
        start, _ = luma.event_times("July 8, 2026 · 12:30 AM", "UTC", 1)
        self.assertEqual(start.hour, 0)

    def test_missing_time_raises(self):
        with self.assertRaises(ValueError):
            luma.event_times("Wed · August 12, 2026 · evening", "UTC", 3)

    def test_iso_utc_converts_zone(self):
        start, _ = luma.event_times(IRL["DATE & TIME"], "America/Los_Angeles", 3)
        self.assertEqual(luma.iso_utc(start), "2026-08-13T01:00:00.000Z")  # PDT = UTC-7


class TestEventPayload(unittest.TestCase):
    def test_in_person(self):
        p = luma.event_payload(view(**IRL), "America/Los_Angeles",
                               description_md="# Agenda", slug="aaif-sf-evalnight")
        self.assertEqual(p["name"], "Eval Night · Builder Series")
        self.assertEqual(p["timezone"], "America/Los_Angeles")
        self.assertEqual(p["geo_address_json"],
                         {"type": "manual", "address": "Github HQ, San Francisco"})
        self.assertEqual(p["max_capacity"], 120)
        self.assertEqual(p["visibility"], "private")   # reviewed before going public
        self.assertEqual(p["description_md"], "# Agenda")
        self.assertEqual(p["slug"], "aaif-sf-evalnight")
        self.assertNotIn("meeting_url", p)

    def test_online_series(self):
        p = luma.event_payload(view(**{
            "EVENT TITLE": "Reading Group: Loop Engineering",
            "DATE & TIME": "July 20, 2026 · 9:00",
            "STREAM / JOIN LINK": "join at lu.ma/ia70fwmm"}), "UTC", 1.0)
        self.assertEqual(p["meeting_url"], "https://lu.ma/ia70fwmm")
        self.assertNotIn("geo_address_json", p)
        self.assertNotIn("max_capacity", p)

    def test_visibility_defaults_private_and_is_overridable(self):
        self.assertEqual(luma.event_payload(view(**IRL), "UTC")["visibility"], "private")
        self.assertEqual(luma.event_payload(view(**IRL), "UTC", visibility="public")["visibility"],
                         "public")

    def test_placeholder_capacity_and_no_title(self):
        self.assertIsNone(luma._capacity_of("TBD"))
        with self.assertRaises(ValueError):
            luma.event_payload(view(**{"DATE & TIME": "July 8, 2026 · 18:00"}), "UTC")


class TestDiffPayload(unittest.TestCase):
    LIVE = {"name": "Eval Night · Builder Series",
            "start_at": "2026-08-13T01:00:00.673Z",
            "timezone": "America/Los_Angeles",
            "geo_address_json": {"type": "manual", "address": "Github HQ, San Francisco",
                                 "description": "extra provider field"},
            "max_capacity": 120}

    def test_no_change_is_empty(self):
        desired = {"name": "Eval Night · Builder Series",
                   "start_at": "2026-08-13T01:00:00.000Z",   # same instant, different ms
                   "timezone": "America/Los_Angeles",
                   "geo_address_json": {"type": "manual",
                                        "address": "Github HQ, San Francisco"},
                   "max_capacity": 120}
        self.assertEqual(luma.diff_payload(self.LIVE, desired), {})

    def test_changes_reported_pairwise(self):
        d = luma.diff_payload(self.LIVE, {"name": "Eval Night v2", "max_capacity": 150})
        self.assertEqual(d, {"name": ("Eval Night · Builder Series", "Eval Night v2"),
                             "max_capacity": (120, 150)})

    def test_new_field_counts_as_change(self):
        d = luma.diff_payload(self.LIVE, {"description_md": "# New copy"})
        self.assertEqual(d, {"description_md": (None, "# New copy")})

    def test_genuine_time_change_is_reported(self):
        d = luma.diff_payload(self.LIVE, {"start_at": "2026-08-13T02:00:00.000Z"})
        self.assertIn("start_at", d)


if __name__ == "__main__":
    unittest.main()
