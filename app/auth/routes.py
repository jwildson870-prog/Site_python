import os,re,hashlib,secrets
from datetime import timedelta
from flask import Blueprint,render_template,request,redirect,url_for,flash,session,current_app
from flask_login import login_user,logout_user,current_user,login_required
from authlib.integrations.flask_client import OAuth
from ..extensions import db
from ..models import User,PasswordResetToken,EmailVerificationToken,UserSession
from ..timeutils import utcnow
from ..emailing import send_email,mail_configured
from ..rate_limit import client_ip,is_rate_limited,record_failed_attempt
from ..settings import get_bool

auth_bp=Blueprint('auth',__name__,url_prefix='/auth'); oauth=OAuth()

PASSWORD_MIN_LEN = 10
RESET_TOKEN_TTL_MINUTES = 30
VERIFY_TOKEN_TTL_MINUTES = 60 * 24

# Genérica de propósito: usada sempre que credenciais são inválidas, para
# nunca revelar se o e-mail existe (item 6 do pedido).
GENERIC_LOGIN_ERROR = 'E-mail ou senha inválidos.'
RATE_LIMIT_MESSAGE = 'Muitas tentativas. Aguarde alguns minutos e tente novamente.'


def email_ok(v): return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',v)) and len(v) <= 255


def password_requirements():
    """Lista exibida na tela de cadastro/redefinição — espelha exatamente
    o que password_errors() abaixo valida no servidor, para nunca prometer
    uma regra que o backend não aplica (item 4 do pedido)."""
    return [
        {'id': 'length', 'label': f'Pelo menos {PASSWORD_MIN_LEN} caracteres'},
        {'id': 'letter', 'label': 'Ao menos uma letra'},
        {'id': 'digit', 'label': 'Ao menos um número'},
    ]


def password_errors(p):
    """Validação de senha — única fonte de verdade, usada no cadastro e na
    redefinição de senha. A checagem no cliente (JS) é só uma conveniência
    visual; esta função roda sempre no servidor antes de qualquer senha
    ser salva."""
    errs = []
    if len(p) < PASSWORD_MIN_LEN: errs.append('length')
    if not re.search(r'[A-Za-z]', p): errs.append('letter')
    if not re.search(r'[0-9]', p): errs.append('digit')
    return errs


def google_configured():
    return get_bool('google_oauth_enabled', True) and bool(os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET'))


def register_google():
    oauth.init_app(current_app); oauth.register(name='google',client_id=os.getenv('GOOGLE_CLIENT_ID'),client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',client_kwargs={'scope':'openid email profile'})
    return oauth.create_client('google')


def _issue_token(model, user_id, ttl_minutes):
    """Gera um token de uso único, guarda só o hash SHA-256 no banco e
    devolve o valor bruto (que nunca é registrado em log nem exibido na
    tela — só volta dentro do link enviado por e-mail)."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    db.session.add(model(user_id=user_id, token_hash=token_hash, expires_at=utcnow()+timedelta(minutes=ttl_minutes)))
    return raw


def _start_session(user):
    session.clear()
    session['sv'] = user.session_version
    raw_token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ua = (request.user_agent.string or '')[:255]
    db.session.add(UserSession(user_id=user.id, token_hash=token_hash, user_agent=ua, ip=client_ip(request)))
    db.session.commit()
    session['st'] = raw_token
    login_user(user, remember=bool(request.form.get('remember')), fresh=True)


def _revoke_all_sessions(user, keep_token_hash=None):
    """Encerra todas as sessões ativas do usuário (usado ao redefinir a
    senha). ``keep_token_hash`` permite preservar a sessão atual quando a
    troca é feita pelo próprio usuário logado (não é o caso do fluxo por
    token de e-mail, que é sempre anônimo, mas mantém a função reutilizável)."""
    q = UserSession.query.filter_by(user_id=user.id, revoked_at=None)
    for s in q.all():
        if keep_token_hash and s.token_hash == keep_token_hash:
            continue
        s.revoked_at = utcnow()


def _send_verification_email(user):
    if not mail_configured():
        return
    raw = _issue_token(EmailVerificationToken, user.id, VERIFY_TOKEN_TTL_MINUTES)
    db.session.commit()
    url = url_for('auth.verify_email', token=raw, _external=True)
    send_email(user.email, 'Confirme seu e-mail — Portal Python',
        f'Olá, {user.name}!\n\nConfirme seu e-mail no Portal Python clicando no link abaixo '
        f'(válido por {VERIFY_TOKEN_TTL_MINUTES // 60} horas, uso único):\n\n{url}\n\n'
        'Se você não criou esta conta, ignore este e-mail.')


@auth_bp.get('/')
def index(): return redirect(url_for('student.dashboard' if current_user.is_authenticated and not current_user.is_admin else 'admin.dashboard' if current_user.is_authenticated else 'home'))


@auth_bp.route('/register',methods=['GET','POST'])
def register():
    if not get_bool('public_registration', True):
        flash('O cadastro público está desativado no momento.', 'error')
        return redirect(url_for('auth.login'))
    if current_user.is_authenticated:return redirect(url_for('auth.index'))
    if request.method=='POST':
        ip = client_ip(request)
        if is_rate_limited('register', ip=ip):
            flash(RATE_LIMIT_MESSAGE,'error')
            return render_template('auth/register.html', google_enabled=google_configured(), password_requirements=password_requirements(), password_min_len=PASSWORD_MIN_LEN)
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); p=request.form.get('password',''); c=request.form.get('confirm_password','')
        pw_errors = password_errors(p)
        if not name or len(name) > 120 or not email_ok(email):
            flash('Preencha os campos corretamente.','error')
            record_failed_attempt('register', ip=ip, email=email)
        elif pw_errors:
            flash('A senha não atende aos requisitos mínimos.','error')
            record_failed_attempt('register', ip=ip, email=email)
        elif p!=c:
            flash('As senhas não coincidem.','error')
            record_failed_attempt('register', ip=ip, email=email)
        elif User.query.filter_by(email=email).first():
            # Mensagem genérica: não confirma nem nega a existência do
            # e-mail para quem está tentando descobrir contas cadastradas.
            flash('Não foi possível concluir o cadastro com estes dados. Se você já tem conta, tente entrar.','error')
            record_failed_attempt('register', ip=ip, email=email)
        else:
            u=User(name=name,email=email,role='student');u.set_password(p);db.session.add(u);db.session.flush()
            _send_verification_email(u)
            db.session.commit()
            flash('Cadastro realizado. Faça login.','success');return redirect(url_for('auth.login'))
    return render_template('auth/register.html', google_enabled=google_configured(), password_requirements=password_requirements(), password_min_len=PASSWORD_MIN_LEN)


@auth_bp.route('/login',methods=['GET','POST'])
def login():
    # Abrir a tela de login nunca deve colocar o usuário automaticamente
    # em uma conta já autenticada. Isso é especialmente importante no
    # desenvolvimento, quando uma sessão anterior do professor pode ter
    # ficado salva no navegador. Ao entrar novamente pela tela de login,
    # encerramos a sessão anterior e exigimos e-mail + senha.
    if request.method == 'GET':
        if current_user.is_authenticated:
            logout_user()
            session.clear()
        return render_template('auth/login.html', google_enabled=google_configured(), registration_enabled=get_bool('public_registration', True))

    ip = client_ip(request)
    email = request.form.get('email','').strip().lower()
    password = request.form.get('password','')

    if is_rate_limited('login', ip=ip, email=email):
        flash(RATE_LIMIT_MESSAGE,'error')
        return render_template('auth/login.html', google_enabled=google_configured(), registration_enabled=get_bool('public_registration', True))

    u = User.query.filter_by(email=email).first()

    if not u or not u.check_password(password):
        record_failed_attempt('login', ip=ip, email=email)
        flash(GENERIC_LOGIN_ERROR,'error')
        return render_template('auth/login.html', google_enabled=google_configured(), registration_enabled=get_bool('public_registration', True))

    _start_session(u)
    return redirect(url_for('admin.dashboard' if u.is_admin else 'student.dashboard'))


@auth_bp.post('/logout')
@login_required
def logout():
    raw = session.get('st')
    if raw:
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        row = UserSession.query.filter_by(token_hash=token_hash, user_id=current_user.id).first()
        if row and row.revoked_at is None:
            row.revoked_at = utcnow(); db.session.commit()
    logout_user();session.clear();flash('Você saiu da conta.','success');return redirect(url_for('home'))


@auth_bp.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if current_user.is_authenticated:return redirect(url_for('auth.index'))
    if request.method == 'POST':
        ip = client_ip(request)
        email = request.form.get('email','').strip().lower()
        # Sempre a mesma mensagem e o mesmo caminho, exista ou não a conta
        # (item 7: nunca revelar se um e-mail está cadastrado).
        generic_message = 'Se este e-mail estiver cadastrado, enviamos um link de redefinição de senha. Verifique também a caixa de spam.'
        if not email_ok(email) or is_rate_limited('forgot_password', ip=ip, email=email):
            record_failed_attempt('forgot_password', ip=ip, email=email)
            flash(generic_message,'success')
            return redirect(url_for('auth.login'))
        record_failed_attempt('forgot_password', ip=ip, email=email)  # conta como uma tentativa, sucesso ou não
        user = User.query.filter_by(email=email).first()
        if user and user.password_hash:  # contas só-Google não têm senha para redefinir
            raw = _issue_token(PasswordResetToken, user.id, RESET_TOKEN_TTL_MINUTES)
            db.session.commit()
            if mail_configured():
                url = url_for('auth.reset_password', token=raw, _external=True)
                send_email(user.email, 'Redefinição de senha — Portal Python',
                    f'Olá, {user.name}!\n\nRecebemos um pedido para redefinir sua senha no Portal Python.\n'
                    f'Este link é válido por {RESET_TOKEN_TTL_MINUTES} minutos e só pode ser usado uma vez:\n\n{url}\n\n'
                    'Se você não pediu isso, pode ignorar este e-mail — sua senha continua a mesma.')
        flash(generic_message,'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')


def _find_reset_token(raw_token):
    token_hash = hashlib.sha256((raw_token or '').encode()).hexdigest()
    return PasswordResetToken.query.filter_by(token_hash=token_hash).first()


@auth_bp.route('/reset-password/<token>', methods=['GET','POST'])
def reset_password(token):
    if current_user.is_authenticated:return redirect(url_for('auth.index'))
    record = _find_reset_token(token)
    if not record or not record.is_valid:
        flash('Este link de redefinição é inválido ou já expirou. Solicite um novo.','error')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        p = request.form.get('password','')
        c = request.form.get('confirm_password','')
        pw_errors = password_errors(p)
        if pw_errors:
            flash('A senha não atende aos requisitos mínimos.','error')
        elif p != c:
            flash('As senhas não coincidem.','error')
        else:
            user = record.user
            user.set_password(p)
            user.session_version = (user.session_version or 0) + 1  # derruba outras sessões abertas
            _revoke_all_sessions(user)
            record.used_at = utcnow()
            db.session.commit()
            flash('Senha redefinida com sucesso. Faça login com a nova senha.','success')
            return redirect(url_for('auth.login'))
    return render_template('auth/reset_password.html', token=token, password_requirements=password_requirements(), password_min_len=PASSWORD_MIN_LEN)


@auth_bp.get('/verify-email/<token>')
def verify_email(token):
    token_hash = hashlib.sha256((token or '').encode()).hexdigest()
    record = EmailVerificationToken.query.filter_by(token_hash=token_hash).first()
    if not record or not record.is_valid:
        flash('Este link de confirmação é inválido ou já expirou.','error')
        return redirect(url_for('auth.login'))
    record.used_at = utcnow()
    record.user.email_verified = True
    db.session.commit()
    flash('E-mail confirmado com sucesso!','success')
    return redirect(url_for('auth.login'))


@auth_bp.get('/google')
def google_login():
    if not google_configured():flash('Login com Google não está disponível no momento.','error');return redirect(url_for('auth.login'))
    return register_google().authorize_redirect(os.getenv('GOOGLE_REDIRECT_URI',url_for('auth.google_callback',_external=True)))


@auth_bp.get('/google/callback')
def google_callback():
    if not google_configured():flash('Login com Google não está disponível no momento.','error');return redirect(url_for('auth.login'))
    try: token=register_google().authorize_access_token(); info=token.get('userinfo') or register_google().userinfo()
    except Exception: flash('Não foi possível concluir a autenticação com o Google. Tente novamente.','error');return redirect(url_for('auth.login'))
    sub=info.get('sub');email=(info.get('email') or '').lower().strip()
    if not sub or not email:flash('O Google não retornou os dados necessários para entrar.','error');return redirect(url_for('auth.login'))
    u=User.query.filter((User.google_sub==sub)|(User.email==email)).first()
    if not u:
        if not get_bool('public_registration', True):
            flash('O cadastro público está desativado no momento.', 'error')
            return redirect(url_for('auth.login'))
        u=User(name=info.get('name') or email.split('@')[0],email=email,role='student',google_sub=sub,email_verified=True);db.session.add(u)
    else:u.google_sub=sub;u.email_verified=True
    db.session.commit()
    _start_session(u)
    return redirect(url_for('admin.dashboard' if u.is_admin else 'student.dashboard'))


def _describe_device(user_agent):
    """Resumo simples e legível do user agent, sem depender de bibliotecas
    externas de detecção — o suficiente para o usuário reconhecer o
    dispositivo ('Chrome no Windows', 'Safari no iPhone')."""
    ua = user_agent or ''
    if 'iPhone' in ua: os_name = 'iPhone'
    elif 'iPad' in ua: os_name = 'iPad'
    elif 'Android' in ua: os_name = 'Android'
    elif 'Mac OS X' in ua or 'Macintosh' in ua: os_name = 'Mac'
    elif 'Windows' in ua: os_name = 'Windows'
    elif 'Linux' in ua: os_name = 'Linux'
    else: os_name = 'Dispositivo desconhecido'
    if 'Edg/' in ua: browser = 'Edge'
    elif 'Chrome/' in ua: browser = 'Chrome'
    elif 'CriOS' in ua: browser = 'Chrome'
    elif 'Firefox/' in ua: browser = 'Firefox'
    elif 'Safari/' in ua: browser = 'Safari'
    else: browser = 'Navegador'
    return f'{browser} · {os_name}'


@auth_bp.get('/sessions')
@login_required
def sessions():
    current_hash = hashlib.sha256((session.get('st') or '').encode()).hexdigest()
    rows = (UserSession.query
            .filter_by(user_id=current_user.id, revoked_at=None)
            .order_by(UserSession.last_seen_at.desc()).all())
    items = [{
        'id': s.id,
        'device': _describe_device(s.user_agent),
        'ip': s.ip or '—',
        'created_at': s.created_at,
        'last_seen_at': s.last_seen_at,
        'is_current': s.token_hash == current_hash,
    } for s in rows]
    return render_template('auth/sessions.html', sessions=items)


@auth_bp.post('/sessions/<int:session_id>/revoke')
@login_required
def revoke_session(session_id):
    row = UserSession.query.filter_by(id=session_id, user_id=current_user.id, revoked_at=None).first()
    if not row:
        flash('Sessão não encontrada.','error')
        return redirect(url_for('auth.sessions'))
    current_hash = hashlib.sha256((session.get('st') or '').encode()).hexdigest()
    row.revoked_at = utcnow()
    db.session.commit()
    if row.token_hash == current_hash:
        # Encerrou a própria sessão atual: desloga de verdade.
        logout_user(); session.clear()
        flash('Você saiu desta sessão.','success')
        return redirect(url_for('home'))
    flash('Sessão encerrada.','success')
    return redirect(url_for('auth.sessions'))


@auth_bp.post('/sessions/revoke-others')
@login_required
def revoke_other_sessions():
    current_hash = hashlib.sha256((session.get('st') or '').encode()).hexdigest()
    rows = UserSession.query.filter_by(user_id=current_user.id, revoked_at=None).all()
    for row in rows:
        if row.token_hash != current_hash:
            row.revoked_at = utcnow()
    db.session.commit()
    flash('Todas as outras sessões foram encerradas.','success')
    return redirect(url_for('auth.sessions'))
