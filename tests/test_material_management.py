import io
import json
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


def seed_content(app, title='Material de teste'):
    with app.app_context():
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        content = Content(
            title=title,
            description='Descrição',
            kind='explanation',
            body='<p>Conteúdo</p>',
            series_id=series.id,
            subject_id=subject.id,
        )
        db.session.add(content)
        db.session.commit()
        return content.id


def test_material_management_controls_are_visible(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    cid = seed_content(app)
    response = client.get('/admin/contents')
    assert response.status_code == 200
    assert f'/admin/contents/{cid}/preview' in response.text
    assert f'/admin/contents/{cid}/edit' in response.text
    assert f'/admin/contents/{cid}/delete' in response.text
    assert 'data-confirm-message=' in response.text


def test_material_preview_route_is_available_to_admin(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    cid = seed_content(app)
    response = client.get(f'/admin/contents/{cid}/preview')
    assert response.status_code == 200
    assert 'Pré-visualização' in response.text
    assert 'Material de teste' in response.text


def test_student_cannot_access_material_management(client, app):
    cid = seed_content(app)
    client.post('/auth/register', data={
        'name': 'Aluno',
        'email': 'aluno@test.local',
        'password': 'Senha1234!',
        'confirm_password': 'Senha1234!',
    })
    login(client, 'aluno@test.local', 'Senha1234!')
    assert client.get('/admin/contents').status_code == 403
    assert client.get(f'/admin/contents/{cid}/preview').status_code == 403
    assert client.get(f'/admin/contents/{cid}/edit').status_code == 403
    assert client.post(f'/admin/contents/{cid}/delete').status_code == 403


def test_material_can_be_edited(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    cid = seed_content(app)
    with app.app_context():
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        sid, subid = series.id, subject.id
    response = client.post(f'/admin/contents/{cid}/edit', data={
        'title': 'Material atualizado',
        'description': 'Nova descrição',
        'series_id': str(sid),
        'subject_id': str(subid),
        'kind': 'explanation',
        'body': '<p>Atualizado</p>',
    })
    assert response.status_code == 302
    with app.app_context():
        content = db.session.get(Content, cid)
        assert content.title == 'Material atualizado'
        assert content.description == 'Nova descrição'


def test_material_can_be_deleted(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    cid = seed_content(app)
    response = client.post(f'/admin/contents/{cid}/delete')
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(Content, cid) is None
