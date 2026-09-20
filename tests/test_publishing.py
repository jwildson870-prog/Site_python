import pytest
from datetime import timedelta
from app.extensions import db
from app.models import User, Series, Subject, Content
from app.timeutils import utcnow

def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})

def seed_base(app, status='published', scheduled_at=None):
    with app.app_context():
        s = Series.query.first()
        sub = Subject.query.filter_by(series_id=s.id).first()
        c = Content(title='Publicação teste', description='Teste', kind='explanation', body='<p>Teste</p>',
                    series_id=s.id, subject_id=sub.id, status=status, scheduled_at=scheduled_at,
                    published_at=utcnow() if status == 'published' else None)
        db.session.add(c); db.session.commit(); return c.id

def test_draft_is_hidden_from_student(client, app):
    cid = seed_base(app, 'draft')
    client.post('/auth/register', data={'name':'Aluno','email':'aluno@test.local','password':'Senha1234!','confirm_password':'Senha1234!'})
    login(client, 'aluno@test.local', 'Senha1234!')
    assert client.get('/aluno/materiais').status_code == 200
    assert 'Publicação teste' not in client.get('/aluno/materiais').text
    assert client.get(f'/aluno/conteudo/{cid}').status_code == 404

def test_published_is_visible_to_student(client, app):
    cid = seed_base(app, 'published')
    client.post('/auth/register', data={'name':'Aluno','email':'aluno@test.local','password':'Senha1234!','confirm_password':'Senha1234!'})
    login(client, 'aluno@test.local', 'Senha1234!')
    assert 'Publicação teste' in client.get('/aluno/materiais').text
    assert client.get(f'/aluno/conteudo/{cid}').status_code == 200

def test_scheduled_future_is_hidden_and_past_is_visible(client, app):
    future_id = seed_base(app, 'scheduled', utcnow() + timedelta(hours=1))
    past_id = seed_base(app, 'scheduled', utcnow() - timedelta(minutes=1))
    client.post('/auth/register', data={'name':'Aluno','email':'aluno@test.local','password':'Senha1234!','confirm_password':'Senha1234!'})
    login(client, 'aluno@test.local', 'Senha1234!')
    page = client.get('/aluno/materiais')
    assert page.status_code == 200
    assert client.get(f'/aluno/conteudo/{future_id}').status_code == 404
    assert client.get(f'/aluno/conteudo/{past_id}').status_code == 200

def test_admin_can_preview_unpublished(client, app):
    cid = seed_base(app, 'draft')
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    assert client.get(f'/admin/contents/{cid}/preview').status_code == 200

def test_new_material_can_be_saved_as_draft(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.first(); sub = Subject.query.filter_by(series_id=s.id).first()
    response = client.post('/admin/contents/new', data={
        'title':'Rascunho novo','description':'Teste','series_id':str(s.id),'subject_id':str(sub.id),
        'kind':'explanation','body':'<p>Rascunho</p>','status':'draft'
    })
    assert response.status_code == 302
    with app.app_context():
        c = Content.query.filter_by(title='Rascunho novo').first()
        assert c.status == 'draft' and c.published_at is None

def test_new_material_can_be_scheduled(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        s = Series.query.first(); sub = Subject.query.filter_by(series_id=s.id).first()
    scheduled = (utcnow() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
    response = client.post('/admin/contents/new', data={
        'title':'Programado','description':'Teste','series_id':str(s.id),'subject_id':str(sub.id),
        'kind':'explanation','body':'<p>Programado</p>','status':'scheduled','scheduled_at':scheduled
    })
    assert response.status_code == 302
    with app.app_context():
        c = Content.query.filter_by(title='Programado').first()
        assert c.status == 'scheduled' and c.scheduled_at is not None
