import pytest
from app import create_app
from app.extensions import db
from app.models import User, Series, Subject, Content, Activity, ContentHistory, ActivityHistory

@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portaljm.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalJM@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor JM')
    return create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "phase54.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads')})

@pytest.fixture()
def client(app):
    return app.test_client()

def login_teacher(client):
    client.post('/auth/login', data={'email':'professor@portaljm.com','password':'PortalJM@2026'})

def seed_content(app, title='Material 5.4'):
    with app.app_context():
        s=Series.query.filter_by(name='1º ano').first(); sub=Subject.query.filter_by(series_id=s.id).first()
        c=Content(title=title, description='Teste', kind='explanation', body='<p>Olá</p>', series_id=s.id, subject_id=sub.id)
        db.session.add(c); db.session.commit(); return c.id

def seed_activity(app, title='Atividade 5.4'):
    with app.app_context():
        s=Series.query.filter_by(name='1º ano').first(); sub=Subject.query.filter_by(series_id=s.id).first()
        a=Activity(title=title, description='Teste', series_id=s.id, subject_id=sub.id); a.set_questions([{'question':'1+1?','options':['1','2'],'correct':'2'}])
        db.session.add(a); db.session.commit(); return a.id

def test_content_duplicate_archive_restore_and_history(client, app):
    login_teacher(client); cid=seed_content(app)
    assert client.post(f'/admin/contents/{cid}/duplicate').status_code == 302
    with app.app_context():
        copies=Content.query.filter(Content.title.like('Material 5.4 (cópia)%')).all(); assert len(copies)==1
        copy_id=copies[0].id
        assert ContentHistory.query.filter_by(content_id=copy_id, action='duplicado').count()==1
    assert client.post(f'/admin/contents/{cid}/archive').status_code == 302
    with app.app_context(): assert db.session.get(Content,cid).archived_at is not None
    assert client.post(f'/admin/contents/{cid}/restore').status_code == 302
    with app.app_context():
        c=db.session.get(Content,cid); assert c.archived_at is None
        actions=[h.action for h in ContentHistory.query.filter_by(content_id=cid).order_by(ContentHistory.id).all()]
        assert 'arquivado' in actions and 'restaurado' in actions

def test_archived_content_hidden_from_student(client, app):
    cid=seed_content(app); login_teacher(client); client.post(f'/admin/contents/{cid}/archive'); client.post('/auth/logout')
    client.post('/auth/register', data={'name':'Aluno','email':'aluno@test.local','password':'Senha1234!','confirm_password':'Senha1234!'})
    client.post('/auth/login', data={'email':'aluno@test.local','password':'Senha1234!'})
    assert client.get('/aluno/materiais').status_code == 200
    assert 'Material 5.4' not in client.get('/aluno/materiais').text
    assert client.get(f'/aluno/conteudo/{cid}').status_code == 404

def test_activity_duplicate_archive_restore_and_history(client, app):
    login_teacher(client); aid=seed_activity(app)
    assert client.post(f'/admin/activities/{aid}/duplicate').status_code == 302
    with app.app_context():
        copies=Activity.query.filter(Activity.title.like('Atividade 5.4 (cópia)%')).all(); assert len(copies)==1
        copy_id=copies[0].id
        assert ActivityHistory.query.filter_by(activity_id=copy_id, action='duplicada').count()==1
    assert client.post(f'/admin/activities/{aid}/archive').status_code == 302
    with app.app_context(): assert db.session.get(Activity,aid).archived_at is not None
    assert client.post(f'/admin/activities/{aid}/restore').status_code == 302
    with app.app_context():
        a=db.session.get(Activity,aid); assert a.archived_at is None
        actions=[h.action for h in ActivityHistory.query.filter_by(activity_id=aid).order_by(ActivityHistory.id).all()]
        assert 'arquivada' in actions and 'restaurada' in actions
