"""Limite de tentativas para login, cadastro e recuperação de senha.

Guardado no banco de dados (não em memória do processo) para funcionar de
forma consistente com vários workers do gunicorn em produção. Só tentativas
malsucedidas são gravadas, então a tabela fica pequena.

Item 12/13 do pedido: bloqueio temporário por IP e por conta, sem travar o
uso normal do portal para usuários legítimos. CAPTCHA não foi implementado
(exigiria uma conta em um serviço externo como hCaptcha/Turnstile, o que
está fora do alcance deste ambiente); o ponto de extensão está marcado
abaixo em ``should_show_captcha``.
"""
from datetime import timedelta
from .extensions import db
from .models import LoginAttempt
from .timeutils import utcnow

WINDOW_MINUTES = 15
MAX_ATTEMPTS_PER_IP = 20
MAX_ATTEMPTS_PER_EMAIL = 6
# A partir de quantas tentativas por conta um CAPTCHA passaria a ser exigido,
# se/quando um provedor de CAPTCHA for configurado (ver README).
CAPTCHA_THRESHOLD = 3


def client_ip(request):
    # request.remote_addr é a origem confiável por padrão neste ambiente
    # (não há proxy reverso configurado à frente do Flask). Se o Portal
    # Python passar a rodar atrás de um proxy (Render usa um, por exemplo),
    # adicionar werkzeug.middleware.proxy_fix.ProxyFix em app/__init__.py é
    # necessário para que este valor continue confiável.
    return (request.remote_addr or '')[:64]


def _window_start():
    return utcnow() - timedelta(minutes=WINDOW_MINUTES)


def record_failed_attempt(action, ip=None, email=None):
    db.session.add(LoginAttempt(action=action, ip=ip or None, email=(email or None), created_at=utcnow()))
    db.session.commit()


def is_rate_limited(action, ip=None, email=None):
    since = _window_start()
    base = LoginAttempt.query.filter(LoginAttempt.action == action, LoginAttempt.created_at >= since)
    if ip and base.filter(LoginAttempt.ip == ip).count() >= MAX_ATTEMPTS_PER_IP:
        return True
    if email and base.filter(LoginAttempt.email == email).count() >= MAX_ATTEMPTS_PER_EMAIL:
        return True
    return False


def should_show_captcha(action, ip=None, email=None):
    """Ponto de extensão: quando um provedor de CAPTCHA estiver configurado
    (CAPTCHA_SITE_KEY/CAPTCHA_SECRET_KEY no .env), o template pode consultar
    isto para decidir se mostra o desafio — só depois de comportamento
    suspeito, nunca no primeiro acesso."""
    since = _window_start()
    base = LoginAttempt.query.filter(LoginAttempt.action == action, LoginAttempt.created_at >= since)
    count = base.filter(LoginAttempt.ip == ip).count() if ip else 0
    if email:
        count = max(count, base.filter(LoginAttempt.email == email).count())
    return count >= CAPTCHA_THRESHOLD
