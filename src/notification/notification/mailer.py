import smtplib
from email.message import EmailMessage
from html import escape

from .config import Config


def build_message(config: Config, to: str, job_id: str, output_name: str) -> EmailMessage:
    link = f"{config.public_url}/#job-{job_id}"
    msg = EmailMessage()
    msg["Subject"] = f"Tu audio esta listo: {output_name}"
    msg["From"] = config.mail_from
    msg["To"] = to
    msg.set_content(
        f"Hola,\n\nTerminamos de convertir tu archivo. Ya puedes descargar "
        f"{output_name} desde:\n\n{link}\n\nID del trabajo: {job_id}\n"
    )
    msg.add_alternative(
        f"""<p>Hola,</p>
<p>Terminamos de convertir tu archivo. Ya puedes descargar
<strong>{escape(output_name)}</strong>.</p>
<p><a href="{escape(link)}">Abrir el convertidor</a></p>
<p style="color:#888;font-size:12px">ID del trabajo: {escape(job_id)}</p>""",
        subtype="html",
    )
    return msg


def send(config: Config, msg: EmailMessage) -> None:
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as smtp:
        if config.smtp_starttls:
            smtp.starttls()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password or "")
        smtp.send_message(msg)
