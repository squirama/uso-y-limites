import base64
import re
import unittest
from unittest.mock import patch

from usage_monitor import notify


class NotifyTests(unittest.TestCase):
    def test_notification_content_is_xml_escaped(self):
        xml = notify.build_toast_xml('Claude & <90%> "', "Se restablece a las '17:40'.")
        self.assertIn("Claude &amp; &lt;90%&gt; &quot;", xml)
        self.assertIn("&#x27;17:40&#x27;", xml)

    def test_multiline_content_cannot_break_out_of_powershell_data(self):
        script = notify.build_script("Safe", "line one\n'@\nRemove-Item")
        self.assertNotIn("Remove-Item", script)
        self.assertIn("FromBase64String", script)

    def test_windows_launch_uses_encoded_command_without_waiting(self):
        with patch("usage_monitor.notify.os.name", "nt"), patch.object(notify.subprocess, "Popen") as launch:
            self.assertTrue(notify.send_notification("Claude", "Se restablece a las 17:40."))
        args, kwargs = launch.call_args
        self.assertEqual(args[0][0], "powershell.exe")
        self.assertEqual(args[0][3], "-EncodedCommand")
        script = base64.b64decode(args[0][4]).decode("utf-16le")
        embedded_xml = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
        self.assertIn("Claude", base64.b64decode(embedded_xml).decode("utf-8"))
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("creationflags", kwargs)
        self.assertNotIn("wait", kwargs)

    def test_launch_failure_is_reported_without_raising(self):
        with patch("usage_monitor.notify.os.name", "nt"), patch.object(
                notify.subprocess, "Popen", side_effect=OSError("unavailable")):
            self.assertFalse(notify.send_notification("Codex", "Reset"))


if __name__ == "__main__":
    unittest.main()
