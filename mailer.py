import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict


logger = logging.getLogger(__name__)


def _build_smtp(config: Dict[str, Any]) -> smtplib.SMTP:
    email_cfg = config["email"]
    smtp = smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"], timeout=30)
    if email_cfg.get("use_tls", True):
        smtp.starttls()
    smtp.login(email_cfg["username"], email_cfg["password"])
    return smtp


def send_direction_email(direction: str, file_path: Path, config: Dict[str, Any]) -> None:
    email_cfg = config["email"]
    recipients = email_cfg["recipients"].get(direction, [])
    if not recipients:
        logger.warning("No recipients for direction %s", direction)
        return

    subject = email_cfg["subject_template"].format(direction=direction)
    body = email_cfg["body_template"].format(direction=direction)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    with open(file_path, "rb") as handle:
        msg.add_attachment(
            handle.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=file_path.name,
        )

    with _build_smtp(config) as smtp:
        smtp.send_message(msg)
    logger.info("Sent email for %s to %s", direction, recipients)


def send_alert_email(error_message: str, config: Dict[str, Any]) -> None:
    email_cfg = config["email"]
    recipients = email_cfg.get("alert_recipients", [])
    if not recipients:
        return

    msg = EmailMessage()
    msg["Subject"] = email_cfg["alert_subject"]
    msg["From"] = email_cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(f"任务失败: {error_message}")

    with _build_smtp(config) as smtp:
        smtp.send_message(msg)
    logger.info("Sent alert email to %s", recipients)
