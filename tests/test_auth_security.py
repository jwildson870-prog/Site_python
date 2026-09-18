import pytest
from datetime import timedelta

from app import create_app
from app.extensions import db
from app.models import User, PasswordResetToken
from app.timeutils import utcnow


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portalpython.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalPython@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor Python')
    monkeypatch.delenv('GOOGLE_CLIENT_ID', raising=False)
    monkeypatch.delenv('GOOGLE_CLIENT_SECRET', raising=False)
    monkeypatch.delenv('MAIL_SERVER', raising=False)
    app = create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'test.db'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
    })
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email='aluno@test.local', password='Senha1234', confirm=None):
    return client.post('/auth/register', data={
        'name': 'Aluno Teste', 'email': email,
        'password': password, 'confirm_password': confirm if confirm is not None else password,
    })


def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})


# --- 1/2/3: cadastro harmonizado, confirmação de senha, requisitos ---

def test_register_rejects_password_without_digit(client):
    r = register(client, password='SomenteLetras', confirm='SomenteLetras')
    assert r.status_code == 200
    assert 'requisitos mínimos' in r.text
    with client.application.app_context():
        assert User.query.filter_by(email='aluno@test.local').first() is None


def test_register_rejects_short_password(client):
    r = register(client, password='Ab1', confirm='Ab1')
    assert r.status_code == 200
    assert 'requisitos mínimos' in r.text


def test_register_rejects_mismatched_confirmation(client):
    r = register(client, password='Senha1234', confirm='Senha9999')
    assert r.status_code == 200
    assert 'não coincidem' in r.text
    with client.application.app_context():
        assert User.query.filter_by(email='aluno@test.local').first() is None


def test_register_valid_password_succeeds(client):
    r = register(client)
    assert r.status_code == 302
    with client.application.app_context():
        u = User.query.filter_by(email='aluno@test.local').first()
        assert u is not None
        assert u.check_password('Senha1234')


def test_register_duplicate_email_uses_generic_message(client):
    register(client)
    r = register(client, password='OutraSenha1')
    assert r.status_code == 200
    # Não deve dizer explicitamente "este e-mail já está cadastrado"
    assert 'já está cadastrado' not in r.text


# --- 6: mensagens de login não revelam se a conta existe ---

def test_login_error_message_is_identical_for_unknown_email_and_wrong_password(client):
    register(client)
    r_unknown = login(client, 'naoexiste@test.local', 'Senha1234')
    r_wrong = login(client, 'aluno@test.local', 'SenhaErrada1')
    assert 'E-mail ou senha inválidos.' in r_unknown.text
    assert 'E-mail ou senha inválidos.' in r_wrong.text


# --- 12: proteção contra tentativas repetidas ---

def test_login_rate_limit_kicks_in_after_repeated_failures(client):
    register(client)
    for _ in range(6):
        login(client, 'aluno@test.local', 'SenhaErrada1')
    r = login(client, 'aluno@test.local', 'SenhaErrada1')
    assert 'Muitas tentativas' in r.text


def test_login_rate_limit_does_not_block_a_different_account(client):
    register(client, email='aluno1@test.local')
    register(client, email='aluno2@test.local')
    for _ in range(6):
        login(client, 'aluno1@test.local', 'SenhaErrada1')
    # A conta 2 não deve ser afetada pelas tentativas contra a conta 1
    # (o limite por e-mail é isolado; só o limite por IP é compartilhado,
    # e o teste fica bem abaixo do limite por IP).
    r = login(client, 'aluno2@test.local', 'Senha1234')
    assert r.status_code == 302


# --- 7: recuperação de senha ---

def test_forgot_password_gives_same_response_for_existing_and_unknown_email(client):
    register(client)
    r1 = client.post('/auth/forgot-password', data={'email': 'aluno@test.local'}, follow_redirects=True)
    r2 = client.post('/auth/forgot-password', data={'email': 'ninguem@test.local'}, follow_redirects=True)
    assert 'Se este e-mail estiver cadastrado' in r1.text
    assert 'Se este e-mail estiver cadastrado' in r2.text


def test_forgot_password_creates_single_use_token_for_existing_user(client):
    register(client)
    client.post('/auth/forgot-password', data={'email': 'aluno@test.local'})
    with client.application.app_context():
        tokens = PasswordResetToken.query.all()
        assert len(tokens) == 1
        assert tokens[0].used_at is None


def test_forgot_password_does_not_create_token_for_unknown_email(client):
    client.post('/auth/forgot-password', data={'email': 'ninguem@test.local'})
    with client.application.app_context():
        assert PasswordResetToken.query.count() == 0


def _issue_raw_reset_token(app, email):
    """Emite um token de redefinição diretamente (sem passar pelo e-mail,
    que não é enviado em teste) e devolve o valor bruto para simular o
    link que o usuário clicaria."""
    import hashlib, secrets
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        raw = secrets.token_urlsafe(32)
        db.session.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=30),
        ))
        db.session.commit()
    return raw


def test_reset_password_with_valid_token_changes_password(client):
    register(client)
    raw = _issue_raw_reset_token(client.application, 'aluno@test.local')
    r = client.post(f'/auth/reset-password/{raw}', data={
        'password': 'NovaSenha1', 'confirm_password': 'NovaSenha1',
    })
    assert r.status_code == 302
    with client.application.app_context():
        u = User.query.filter_by(email='aluno@test.local').first()
        assert u.check_password('NovaSenha1')
        assert not u.check_password('Senha1234')


def test_reset_password_token_cannot_be_reused(client):
    register(client)
    raw = _issue_raw_reset_token(client.application, 'aluno@test.local')
    client.post(f'/auth/reset-password/{raw}', data={'password': 'NovaSenha1', 'confirm_password': 'NovaSenha1'})
    r2 = client.post(f'/auth/reset-password/{raw}', data={'password': 'OutraSenha2', 'confirm_password': 'OutraSenha2'}, follow_redirects=True)
    assert 'inválido ou já expirou' in r2.text
    with client.application.app_context():
        u = User.query.filter_by(email='aluno@test.local').first()
        assert u.check_password('NovaSenha1')  # não foi trocada pela segunda tentativa


def test_reset_password_rejects_expired_token(client):
    register(client)
    import hashlib, secrets
    with client.application.app_context():
        user = User.query.filter_by(email='aluno@test.local').first()
        raw = secrets.token_urlsafe(32)
        db.session.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() - timedelta(minutes=1),  # já expirado
        ))
        db.session.commit()
    r = client.get(f'/auth/reset-password/{raw}', follow_redirects=True)
    assert 'inválido ou já expirou' in r.text


def test_reset_password_rejects_weak_new_password(client):
    register(client)
    raw = _issue_raw_reset_token(client.application, 'aluno@test.local')
    r = client.post(f'/auth/reset-password/{raw}', data={'password': 'curta', 'confirm_password': 'curta'})
    assert 'requisitos mínimos' in r.text
    with client.application.app_context():
        u = User.query.filter_by(email='aluno@test.local').first()
        assert u.check_password('Senha1234')  # senha original preservada


def test_password_reset_invalidates_previously_logged_in_session(client, app):
    register(client)
    login(client, 'aluno@test.local', 'Senha1234')
    assert client.get('/aluno/').status_code == 200  # sessão válida

    raw = _issue_raw_reset_token(app, 'aluno@test.local')
    client.post(f'/auth/reset-password/{raw}', data={'password': 'NovaSenha1', 'confirm_password': 'NovaSenha1'})

    # A sessão antiga (cookie do mesmo client) deve ter sido derrubada pela
    # troca de senha: a próxima requisição autenticada deve ser recusada.
    r = client.get('/aluno/')
    assert r.status_code in (302, 401, 403)


# --- 11: botão do Google só aparece quando configurado ---

def test_google_button_hidden_when_not_configured(client):
    r = client.get('/auth/login')
    assert 'Continuar com Google' not in r.text
    r2 = client.get('/auth/register')
    assert 'Continuar com Google' not in r2.text


def test_google_login_route_fails_gracefully_when_not_configured(client):
    r = client.get('/auth/google', follow_redirects=True)
    assert 'não está disponível' in r.text


# --- 10: sessões ativas ---

def test_login_creates_a_visible_session_entry(client):
    register(client)
    login(client, 'aluno@test.local', 'Senha1234')
    r = client.get('/auth/sessions')
    assert r.status_code == 200
    assert 'Este dispositivo' in r.text


def test_revoking_current_session_logs_the_user_out(client):
    register(client)
    login(client, 'aluno@test.local', 'Senha1234')
    page = client.get('/auth/sessions')
    with client.application.app_context():
        from app.models import UserSession
        row = UserSession.query.filter_by(revoked_at=None).first()
        session_id = row.id
    r = client.post(f'/auth/sessions/{session_id}/revoke', follow_redirects=True)
    assert 'saiu desta sessão' in r.text
    assert client.get('/aluno/').status_code in (302, 401, 403)


def test_revoke_other_sessions_keeps_current_one_active(client, app):
    register(client)
    login(client, 'aluno@test.local', 'Senha1234')
    # Simula um segundo dispositivo logado na mesma conta, direto no banco.
    import hashlib, secrets as _secrets
    with app.app_context():
        from app.models import User, UserSession
        user = User.query.filter_by(email='aluno@test.local').first()
        db.session.add(UserSession(user_id=user.id, token_hash=hashlib.sha256(_secrets.token_urlsafe(24).encode()).hexdigest(), user_agent='OutroAparelho'))
        db.session.commit()

    r = client.post('/auth/sessions/revoke-others', follow_redirects=True)
    assert 'Todas as outras sessões foram encerradas' in r.text
    # A sessão usada pelo client (a atual) continua válida.
    assert client.get('/aluno/').status_code == 200
    with app.app_context():
        from app.models import UserSession
        active = UserSession.query.filter_by(revoked_at=None).count()
        assert active == 1
