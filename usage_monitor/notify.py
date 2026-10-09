"""Best-effort Windows toast notifications without exposing text to a shell."""

import base64
import html
import os
import subprocess


APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def build_toast_xml(title, message):
    title_xml = html.escape(str(title), quote=True)
    message_xml = html.escape(str(message), quote=True)
    return (
        '<toast><visual><binding template="ToastGeneric">'
        f'<text>{title_xml}</text><text>{message_xml}</text>'
        '</binding></visual></toast>'
    )


def build_script(title, message):
    xml_encoded = base64.b64encode(build_toast_xml(title, message).encode("utf-8")).decode("ascii")
    return f'''
$ErrorActionPreference = 'Stop'
$appId = '{APP_ID}'
$xml = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{xml_encoded}'))
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$document = New-Object Windows.Data.Xml.Dom.XmlDocument
$document.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($document)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)
$notifier.Show($toast)
'''.strip()


def send_notification(title, message):
    if os.name != "nt":
        return False
    try:
        script = build_script(title, message)
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True,
        )
        return True
    except Exception:
        return False
