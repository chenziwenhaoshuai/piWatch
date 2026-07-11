from __future__ import annotations
import smtplib, ssl
from email.message import EmailMessage
from typing import Any
class EmailNotifier:
    def __init__(self, settings: dict[str, Any]): self.settings = settings
    def enabled(self): return bool(self.settings.get("enabled") and self.settings.get("smtp_host") and self.settings.get("recipient"))
    def send(self, subject: str, body: str, image: bytes | None = None, image_name: str = "event.jpg"):
        if not self.enabled(): raise RuntimeError("email_not_configured")
        msg = EmailMessage(); sender = self.settings.get("sender") or self.settings.get("username")
        msg["From"], msg["To"], msg["Subject"] = sender, self.settings["recipient"], subject; msg.set_content(body)
        if image:
            msg.add_attachment(image, maintype="image", subtype="jpeg", filename=image_name)
        host, port = self.settings["smtp_host"], int(self.settings.get("smtp_port", 465)); username = self.settings.get("username"); password = self.settings.get("password")
        security = self.settings.get("security", "ssl")
        if security == "ssl":
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
                if username: smtp.login(username, password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                if security == "starttls": smtp.starttls(context=ssl.create_default_context()); smtp.ehlo()
                if username: smtp.login(username, password or "")
                smtp.send_message(msg)
