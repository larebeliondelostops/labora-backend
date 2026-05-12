import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


class EmailService:
    def send_otp(self, *, recipient: str, code: str, purpose: str) -> None:
        subject = "Tu codigo de verificacion de Labora"
        text = (
            "Hola,\n\n"
            f"Tu codigo de verificacion de Labora es: {code}\n\n"
            f"Este codigo vence en {settings.otp_ttl_minutes} minutos.\n"
            "Si no solicitaste este codigo, puedes ignorar este correo.\n"
        )
        self._send_email(recipient=recipient, subject=subject, text=text)

    def send_password_reset(self, *, recipient: str, token: str) -> None:
        reset_url = f"{settings.frontend_url}/reset-password?token={token}"
        subject = "Restablece tu contrasena de Labora"
        text = (
            "Hola,\n\n"
            "Recibimos una solicitud para restablecer tu contrasena.\n"
            f"Abre este enlace para continuar: {reset_url}\n\n"
            "Si no solicitaste este cambio, puedes ignorar este correo.\n"
        )
        self._send_email(recipient=recipient, subject=subject, text=text)

    def _send_email(self, *, recipient: str, subject: str, text: str) -> None:
        provider = settings.email_provider
        if provider == "console":
            logger.info("Email to %s | %s\n%s", recipient, subject, text)
            return
        if provider != "smtp":
            raise EmailDeliveryError(f"Proveedor de correo no soportado: {provider}")

        if not settings.smtp_host:
            raise EmailDeliveryError("SMTP_HOST no esta configurado.")

        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(text)

        try:
            self._send_smtp(message)
        except smtplib.SMTPException as exc:
            raise EmailDeliveryError("No se pudo enviar el correo SMTP.") from exc
        except OSError as exc:
            raise EmailDeliveryError("No se pudo conectar con el servidor SMTP.") from exc

    def _send_smtp(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=20,
                context=context,
            ) as server:
                self._login(server)
                server.send_message(message)
            return

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_use_tls:
                server.starttls(context=context)
            self._login(server)
            server.send_message(message)

    def _login(self, server: smtplib.SMTP) -> None:
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
