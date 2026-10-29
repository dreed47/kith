"""Outgoing email over SMTP with STARTTLS (Gmail with an App Password by default). Messages are fixed text from
this app; nothing a visitor typed goes into them, so the site can't be used to send someone else's words. The one
exception is an admin's own announcement (Admin > Email everyone), and that only ever goes to account holders."""
import logging
import smtplib
from email.message import EmailMessage

from flask import current_app


def enabled():
    """A mail account and the site's own address: links in email are built from KITH_SITE_URL, never from the
    request, whose Host header a visitor chooses (a reset link pointing at their server would hand them the token)."""
    return bool(current_app.config["SMTP_USER"] and current_app.config["SITE_URL"])


def send(to, subject, body):
    """True once handed to the mail server. False when email isn't set up or the server refused (logged)."""
    cfg = current_app.config
    if not enabled():
        return False
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"] or cfg["SMTP_USER"]
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as smtp:
            smtp.starttls()
            smtp.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        logging.getLogger(__name__).exception("sending %r to %s failed", subject, to)
        return False
    return True
