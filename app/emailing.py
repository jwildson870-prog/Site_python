"""Envio de e-mail transacional (recuperação de senha, verificação de conta).

Configurável só por variáveis de ambiente — nenhuma credencial fica no
código (ver .env.example). Se o serviço de e-mail ainda não estiver
configurado, ``send_email`` simplesmente não envia e retorna False; o
Portal Python continua funcionando normalmente (item 7/8 do pedido: não
pode depender de um serviço inexistente para iniciar).

Importante: esta função nunca registra o corpo da mensagem (onde vai o
token/link) em log, nem em caso de erro — só o fato de que o envio falhou.
"""
import os
import smtplib
from email.message import EmailMessage


def mail_configured():
    return bool(os.getenv('MAIL_SERVER', '').strip())


def send_email(to_addr, subject, text_body):
    if not mail_configured():
        return False
    host = os.getenv('MAIL_SERVER', '').strip()
    port = int(os.getenv('MAIL_PORT', '587') or '587')
    username = os.getenv('MAIL_USERNAME', '').strip()
    password = os.getenv('MAIL_PASSWORD', '')
    use_tls = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
    sender = os.getenv('MAIL_DEFAULT_SENDER', username or 'no-reply@portal-python.local')

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to_addr
    msg.set_content(text_body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True
    except Exception:
        # Nunca incluir o corpo (que contém o link/token) no log.
        import logging
        logging.getLogger(__name__).warning('Falha ao enviar e-mail transacional para %s', to_addr[:3] + '***')
        return False
