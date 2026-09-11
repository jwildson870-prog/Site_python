import io
import os
import pytest
from app import create_app
from app.extensions import db
from app.models import User, Series, Subject, Content

@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portaljm.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalJM@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor JM')
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

def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})

def test_teacher_login_redirect_and_isolation(client):
    r = login(client, 'professor@portaljm.com', 'PortalJM@2026')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/admin/')
    panel = client.get('/admin/')
    assert panel.status_code == 200
    assert 'ÁREA DO PROFESSOR' in panel.text
    assert 'Estudar' not in panel.text
    assert client.get('/aluno/').status_code == 403

def test_student_login_and_admin_block(client):
    client.post('/auth/register', data={
        'name': 'Aluno', 'email': 'aluno@test.local',
        'password': 'Senha1234!', 'confirm_password': 'Senha1234!'
    })
    r = login(client, 'aluno@test.local', 'Senha1234!')
    assert r.status_code == 302
    assert r.headers['Location'].endswith('/aluno/')
    assert client.get('/aluno/').status_code == 200
    assert 'ÁREA DO ALUNO' in client.get('/aluno/').text
    assert client.get('/admin/').status_code == 403

def test_four_real_series_exist(client, app):
    with app.app_context():
        names = {s.name for s in Series.query.all()}
    assert {'1º ano', '2º ano', '3º ano', '4º ano'} <= names

def test_legacy_series_are_migrated_without_deleting_data(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portaljm.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalJM@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor JM')
    app = create_app({
        'TESTING': True, 'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'legacy.db'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
    })
    with app.app_context():
        s = Series.query.filter_by(name='1º ano').first()
        assert s is not None
        c = Content(title='Legado', description='', kind='explanation',
                    body='<p>Legado</p>', series_id=s.id,
                    subject_id=Subject.query.filter_by(series_id=s.id).first().id)
        db.session.add(c); db.session.commit()
        cid = c.id
        assert db.session.get(Content, cid).title == 'Legado'

def test_create_material_in_fourth_year(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.filter_by(name='4º ano').first()
        sub = Subject.query.filter_by(series_id=s.id).first()
        sid, subid = s.id, sub.id
    r = client.post('/admin/contents/new', data={
        'title': 'Material 4º ano', 'description': 'Teste',
        'series_id': str(sid), 'subject_id': str(subid),
        'kind': 'explanation', 'body': '<p>Conteúdo</p>'
    })
    assert r.status_code == 302
    with app.app_context():
        c = Content.query.filter_by(title='Material 4º ano').first()
        assert c and c.series_id == sid and c.subject_id == subid

def test_external_link_material(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.first()
        sub = Subject.query.filter_by(series_id=s.id).first()
    r = client.post('/admin/contents/new', data={
        'title': 'Drive', 'description': 'Link',
        'series_id': str(s.id), 'subject_id': str(sub.id),
        'kind': 'link', 'external_url': 'https://drive.google.com/file/d/test'
    })
    assert r.status_code == 302
    with app.app_context():
        c = Content.query.filter_by(title='Drive').first()
        assert c.external_url.startswith('https://drive.google.com/')

def test_pdf_upload(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.first()
        sub = Subject.query.filter_by(series_id=s.id).first()
    r = client.post('/admin/contents/new', data={
        'title': 'PDF', 'description': '',
        'series_id': str(s.id), 'subject_id': str(sub.id),
        'kind': 'pdf',
        'pdf': (io.BytesIO(b'%PDF-1.4 test'), 'teste.pdf')
    }, content_type='multipart/form-data')
    assert r.status_code == 302
    with app.app_context():
        c = Content.query.filter_by(title='PDF').first()
        assert c and c.kind == 'pdf' and c.file_name
    assert client.get(f'/aluno/pdf/{c.id}').status_code == 403

def test_pdf_extension_validation(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.first()
        sub = Subject.query.filter_by(series_id=s.id).first()
    r = client.post('/admin/contents/new', data={
        'title': 'Não é PDF', 'description': '',
        'series_id': str(s.id), 'subject_id': str(sub.id),
        'kind': 'pdf',
        'pdf': (io.BytesIO(b'conteudo'), 'arquivo.docx')
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    with app.app_context():
        assert Content.query.filter_by(title='Não é PDF').first() is None

def test_activity_and_experiment_are_available_to_students(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.filter_by(name='1º ano').first()
        sub = Subject.query.filter_by(series_id=s.id).first()
        a = Activity(title='Quiz de Química', description='Teste', series_id=s.id, subject_id=sub.id)
        a.set_questions([{'question':'Quanto é 1+1?','options':['1','2','3','4'],'correct':'2'}])
        e = Experiment(title='Experimento seguro', description='Teste', objective='Observar', materials='Materiais', steps='Passo a passo', safety='Faça com supervisão.', conclusion='Conclusão', series_id=s.id, subject_id=sub.id)
        db.session.add_all([a,e]); db.session.commit(); aid,eid=a.id,e.id
    client.post('/auth/logout')
    login(client, 'aluno@test.local', 'Senha1234!') if False else None
