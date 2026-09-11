"""离线回归测试：不登录学校系统，不发送真实邮件或播放提示音。"""

import io
import json
import os
import smtplib
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main


def course(course_id="test-1", **changes):
    data = {
        "BGBM": course_id,
        "BGTMZW": "测试报告",
        "JZSJ": "2099-01-01 12:00:00",
        "YXRS": 2,
        "KXRS": 10,
    }
    return data | changes


def response(rows):
    return {
        "ok": True,
        "status": 200,
        "text": json.dumps({"code": "0", "datas": {"wxbgbgdz": {"rows": rows}}}),
    }


class AvailabilityTests(unittest.TestCase):
    def test_deadline_uses_china_timezone(self):
        now = datetime.fromisoformat("2026-09-11T03:59:59+00:00")
        self.assertTrue(main.is_available(course(JZSJ="2026-09-11 12:00:00"), now))
        now = datetime.fromisoformat("2026-09-11T04:00:01+00:00")
        self.assertFalse(main.is_available(course(JZSJ="2026-09-11 12:00:00"), now))

    def test_full_expired_and_malformed_courses_are_not_available(self):
        for changes in (
            {"YXRS": 10}, {"YXRS": -1}, {"KXRS": 0}, {"YXRS": None},
            {"JZSJ": "2000-01-01 00:00:00"}, {"JZSJ": 123}, {"JZSJ": "bad"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(main.is_available(course(**changes)))


class NotificationTests(unittest.TestCase):
    @patch("main.subprocess.run")
    def test_windows_uses_winsound_and_prints_course(self, run):
        beep = Mock()
        fake_winsound = SimpleNamespace(MessageBeep=beep, MB_ICONEXCLAMATION=48)
        with patch("main.sys.platform", "win32"), patch.dict(
            "sys.modules", {"winsound": fake_winsound}
        ), redirect_stdout(io.StringIO()) as output:
            main.notify(course())
        beep.assert_called_once_with(48)
        run.assert_not_called()
        self.assertIn("测试报告", output.getvalue())

    @patch("main.subprocess.run")
    def test_macos_passes_external_title_as_data(self, run):
        title = '引号"与反斜杠\\\n do shell script "anything"'
        with patch("main.sys.platform", "darwin"), redirect_stdout(io.StringIO()):
            main.notify(course(BGTMZW=title))
        args = run.call_args.args[0]
        self.assertNotIn(title, args[2])
        self.assertIn(title, args[4:])
        self.assertIn("sound name", args[2])

    def test_system_notification_failure_preserves_terminal_notice(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("osascript", 10)):
            with patch("main.sys.platform", "darwin"), patch(
                "main.subprocess.run", side_effect=error
            ), redirect_stdout(io.StringIO()) as output:
                main.notify(course())
            self.assertIn("测试报告", output.getvalue())
            self.assertIn("系统提示不可用", output.getvalue())

    def test_email_failure_retries_then_persists_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notified.json"
            known = set()
            with patch("main.notify"), patch("main.send_email") as send, redirect_stdout(io.StringIO()):
                send.side_effect = smtplib.SMTPAuthenticationError(535, b"private response")
                main.process_courses([course()], known, path, object())
                self.assertFalse(known)
                self.assertFalse(path.exists())
                send.side_effect = None
                main.process_courses([course()], known, path, object())
                main.process_courses([course()], known, path, object())
                self.assertEqual(send.call_count, 2)
            self.assertEqual(main.load_notified(path), {"test-1"})

    def test_local_only_mode_never_calls_email(self):
        with tempfile.TemporaryDirectory() as directory, patch("main.notify"), patch(
            "main.send_email"
        ) as send, redirect_stdout(io.StringIO()):
            known = set()
            main.process_courses([course()], known, Path(directory) / "state.json", None)
            send.assert_not_called()
            self.assertEqual(known, {"test-1"})

    def test_save_failure_does_not_mark_as_notified(self):
        known = set()
        with patch("main.notify"), patch("main.save_notified", side_effect=OSError), redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                main.process_courses([course()], known, Path("unused.json"), None)
        self.assertFalse(known)


class ConfigAndStateTests(unittest.TestCase):
    def test_no_email_is_optional_but_partial_config_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(main.read_email_config())
            os.environ["COURSE_EMAIL"] = "test@example.com"
            with self.assertRaises(ValueError):
                main.read_email_config()

    def test_custom_smtp_and_receiver_default(self):
        with patch.dict(os.environ, {
            "COURSE_EMAIL": "test@example.com",
            "COURSE_EMAIL_AUTH_CODE": "test-placeholder",
            "COURSE_SMTP_HOST": "smtp.example.com",
            "COURSE_SMTP_PORT": "2465",
        }, clear=True):
            config = main.read_email_config()
        self.assertEqual(config.receiver, config.sender)
        self.assertEqual((config.host, config.port), ("smtp.example.com", 2465))

    def test_legacy_ids_normalized_and_corrupt_state_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('[1, "2"]', encoding="utf-8")
            self.assertEqual(main.load_notified(path), {"1", "2"})
            for invalid in ('{"id": 1}', '[{}]', 'not json'):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    main.load_notified(path)
                self.assertEqual(path.read_text(encoding="utf-8"), invalid)

    @patch("main.smtplib.SMTP_SSL")
    def test_email_uses_tls_context_and_single_line_subject(self, smtp):
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        config = main.EmailConfig("test@example.com", "placeholder", "test@example.com")
        with redirect_stdout(io.StringIO()):
            main.send_email(course(BGTMZW="标题\n续行"), config)
        self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)
        message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertNotIn("\n", message["Subject"])


class FetchTests(unittest.TestCase):
    @patch("main.time.sleep")
    def test_pagination_includes_course_after_first_50(self, sleep):
        first = [course(str(i)) for i in range(50)]
        with patch("main.fetch_courses", side_effect=[response(first), response([course("51")])]) as fetch:
            rows = main.fetch_all_courses(object())
        self.assertEqual(len(rows), 51)
        self.assertEqual(fetch.call_args_list[1].args[1], 2)

    @patch("main.time.sleep")
    def test_repeated_page_does_not_loop_forever(self, sleep):
        first = response([course(str(i)) for i in range(50)])
        with patch("main.fetch_courses", return_value=first) as fetch:
            with self.assertRaises(main.MonitorError):
                main.fetch_all_courses(object())
        self.assertEqual(fetch.call_count, 2)

    def test_later_pages_do_not_reset_to_first_page(self):
        frame = Mock()
        main.fetch_courses(frame, 2)
        payload = frame.evaluate.call_args.args[1]["payload"]
        query = json.loads(payload["querySetting"])
        self.assertEqual(payload["pageNumber"], "2")
        self.assertNotIn("_gotoFirstPage", [item["name"] for item in query])

    def test_auth_and_rate_limits_stop_without_echoing_body(self):
        for status in (401, 403, 429):
            with self.subTest(status=status), self.assertRaises(main.StopMonitoring) as error:
                main.parse_response({"ok": False, "status": status, "text": "private body"})
            self.assertNotIn("private body", str(error.exception))

    def test_redirects_stop(self):
        with self.assertRaises(main.StopMonitoring):
            main.parse_response(response([]) | {"redirected": True})

    def test_bad_schema_and_non_json_are_retryable(self):
        for body in ('<html>private login page</html>', '[]', '{"code":"0"}', '{"code":"1","private":"data"}'):
            with self.subTest(body=body), self.assertRaises(main.MonitorError) as error:
                main.parse_response(response([]) | {"text": body})
            self.assertNotIn("private", str(error.exception))

    @patch("main.fetch_all_courses", side_effect=main.MonitorError("bad schema"))
    def test_five_consecutive_errors_stop(self, fetch):
        page = Mock()
        page.is_closed.return_value = False
        with patch("main.CHECK_INTERVAL", 0), redirect_stdout(io.StringIO()):
            main.monitor(page, set(), None)
        self.assertEqual(fetch.call_count, 5)


if __name__ == "__main__":
    unittest.main()
