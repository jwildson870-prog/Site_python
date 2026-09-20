import io

from app import create_app
from app.extensions import db
from app.models import User, PortalSetting


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@test.local')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalTest@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor')
    return create_app({
        'TESTING': True,
        'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'settings.db'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
    })


def _login(client):
    return client.post('/auth/login', data={'email': 'professor@test.local', 'password': 'PortalTest@2026'})


def test_settings_page_and_save(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = app.test_client()
    assert _login(client).status_code == 302
    page = client.get('/admin/settings')
    assert page.status_code == 200
    assert 'Cadastro público' in page.text
    assert 'Limite de upload' in page.text
    response = client.post('/admin/settings', data={
        'institution_name': 'IFRN Teste',
        'upload_limit_mb': '40',
        'google_oauth_enabled': 'on',
        'notifications_enabled': 'on',
        'notification_activities': 'on',
        'notification_materials': 'on',
        'alert_low_performance': 'on',
    })
    assert response.status_code == 302
    with app.app_context():
        assert PortalSetting.query.filter_by(key='institution_name', value='IFRN Teste').first()
        assert PortalSetting.query.filter_by(key='upload_limit_mb', value='40').first()
        assert app.config['MAX_CONTENT_LENGTH'] == 40 * 1024 * 1024
        db.session.remove()
        db.drop_all()


def test_public_registration_can_be_disabled(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    client.post('/admin/settings', data={'institution_name': 'Portal', 'upload_limit_mb': '25'})
    client.post('/auth/logout')
    assert client.get('/auth/register').status_code == 302
    response = client.post('/auth/register', data={
        'name': 'Aluno', 'email': 'aluno@test.local',
        'password': 'Senha1234!', 'confirm_password': 'Senha1234!'
    })
    assert response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email='aluno@test.local').first() is None
        db.session.remove()
        db.drop_all()
