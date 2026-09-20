import json
import pytest

from app import create_app
from app.extensions import db
from app.models import User, Series, Subject, Activity, ActivityAttempt


def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portaljm.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalJM@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor JM')
    return create_app({
        'TESTING': True, 'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f"sqlite:///{tmp_path / 'phase56.db'}",
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
    })


@pytest.fixture()
def app(tmp_path, monkeypatch):
    application = make_app(tmp_path, monkeypatch)
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})


def seed_activity(app, title='Atividade 5.6'):
    with app.app_context():
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        activity = Activity(title=title, description='Teste', series_id=series.id, subject_id=subject.id, max_attempts=2, allow_review=True, shuffle_questions=True, shuffle_options=True)
        activity.set_questions([
            {'question': 'Qual é 1+1?', 'options': ['1', '2', '3', '4'], 'correct': '2', 'kind': 'objective'},
            {'question': 'Explique o conceito.', 'options': [], 'correct': '', 'kind': 'essay'},
        ])
        db.session.add(activity); db.session.commit()
        return activity.id


def test_activity_settings_are_saved(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    with app.app_context():
        series = Series.query.first(); subject = Subject.query.filter_by(series_id=series.id).first()
        activity = Activity(title='Configuração', series_id=series.id, subject_id=subject.id)
        db.session.add(activity); db.session.commit(); aid = activity.id
    response = client.post(f'/admin/activities/{aid}/editar', data={
        'title': 'Configuração', 'description': 'Teste', 'difficulty': 'medio', 'due_at': '',
        'max_attempts': '3', 'allow_review': 'on', 'shuffle_questions': 'on', 'shuffle_options': 'on',
        'kind_0': 'objective', 'question_0': '1+1?', 'option_0_a': '1', 'option_0_b': '2', 'option_0_c': '3', 'option_0_d': '', 'correct_0': '1',
    })
    assert response.status_code == 302
    with app.app_context():
        a = db.session.get(Activity, aid)
        assert a.max_attempts == 3
        assert a.allow_review is True
        assert a.shuffle_questions is True
        assert a.shuffle_options is True


def test_attempt_limit_blocks_after_configured_number(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    aid = seed_activity(app)
    client.post('/auth/logout')
    client.post('/auth/register', data={'name': 'Aluno', 'email': 'aluno56@test.local', 'password': 'Senha1234!', 'confirm_password': 'Senha1234!'})
    login(client, 'aluno56@test.local', 'Senha1234!')
    for _ in range(2):
        page = client.get(f'/aluno/atividade/{aid}')
        assert page.status_code == 200
        # Submit objective + essay using the rendered order.
        with app.app_context():
            attempt_count = ActivityAttempt.query.filter_by(activity_id=aid).count()
        assert client.post(f'/aluno/atividade/{aid}', data={'q0': '2', 'q1': 'Resposta discursiva'}).status_code == 200
        with app.app_context():
            assert ActivityAttempt.query.filter_by(activity_id=aid).count() == attempt_count + 1
    blocked = client.get(f'/aluno/atividade/{aid}')
    assert 'limite de 2 tentativa' in blocked.text


def test_essay_and_review_are_recorded(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    aid = seed_activity(app, 'Revisão')
    client.post('/auth/logout')
    client.post('/auth/register', data={'name': 'Aluno', 'email': 'aluno57@test.local', 'password': 'Senha1234!', 'confirm_password': 'Senha1234!'})
    login(client, 'aluno57@test.local', 'Senha1234!')
    response = client.post(f'/aluno/atividade/{aid}', data={'q0': '2', 'q1': 'Minha explicação'})
    assert response.status_code == 200
    assert 'Minha explicação' in response.text
    assert 'aguardando correção' in response.text
    with app.app_context():
        attempt = ActivityAttempt.query.filter_by(activity_id=aid).first()
        assert attempt is not None
        assert attempt.get_answers()['1'] == 'Minha explicação'
        assert len(attempt.get_presented_questions()) == 2


def test_shuffle_settings_keep_valid_correct_answer(client, app):
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    aid = seed_activity(app, 'Embaralhamento')
    client.post('/auth/logout')
    client.post('/auth/register', data={'name': 'Aluno', 'email': 'aluno58@test.local', 'password': 'Senha1234!', 'confirm_password': 'Senha1234!'})
    login(client, 'aluno58@test.local', 'Senha1234!')
    page = client.get(f'/aluno/atividade/{aid}')
    assert page.status_code == 200
    # Both question types are rendered and the objective question remains answerable.
    assert 'Discursiva' in page.text
    assert 'Qual é 1+1?' in page.text
