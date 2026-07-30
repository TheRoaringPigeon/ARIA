import smtplib
from email.message import EmailMessage

from app.config import settings


def send_mail(to: str, subject: str, body_text: str) -> None:
    """One-shot outbound SMTP send. No connection pooling/retry — this is
    called at most a few times per household per day (send_overdue_digest),
    not a hot path. A new connection per call keeps the failure mode local:
    one bad send doesn't leave a stale/broken connection for the next.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from_address
    msg["To"] = to
    msg.set_content(body_text)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
