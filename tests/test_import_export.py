import io
from app import create_app
from app.extensions import db
from app.models import User, Progress, Content, Series, Subject


def make_app():
    return create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'WTF_CSRF_ENABLED': False, 'SECRET_KEY': 'test'})


def login(client, email='admin@test.local', password='admin'):
    return client.post('/login', data={'email': email, 'password': password}, follow_redirects=True)


def test_import_export_routes_require_admin():
    app = make_app()
    with app.app_context():
        u = User(name='Admin', email='admin@test.local', role='admin')
        u.set_password('admin')
        db.session.add(u); db.session.commit()
    client = app.test_client()
    response = login(client)
    assert response.status_code == 200
    assert client.get('/admin/importacao-exportacao').status_code == 200
    assert client.get('/admin/exportar/alunos.csv').status_code == 200
    assert client.get('/admin/exportar/alunos.xlsx').status_code == 200
    assert client.get('/admin/exportar/progresso.csv').status_code == 200
    assert client.get('/admin/exportar/progresso.xlsx').status_code == 200
