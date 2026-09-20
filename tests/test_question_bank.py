import pytest
from app import create_app
from app.extensions import db
from app.models import User, Series, Subject, QuestionBank

@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_EMAIL', 'professor@portaljm.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'PortalJM@2026')
    monkeypatch.setenv('ADMIN_NAME', 'Professor JM')
    return create_app({'TESTING': True, 'WTF_CSRF_ENABLED': False,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "question_bank.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads')})

@pytest.fixture()
def client(app):
    return app.test_client()

def login_teacher(client):
    client.post('/auth/login', data={'email':'professor@portaljm.com','password':'PortalJM@2026'})

def seed_question(app, question='Questão de Python', category='Funções', tags='python, funções', difficulty='medio'):
    with app.app_context():
        s = Series.query.filter_by(name='1º ano').first()
        sub = Subject.query.filter_by(series_id=s.id).first()
        q = QuestionBank(question=question, correct='A', difficulty=difficulty, category=category, tags=tags, series_id=s.id, subject_id=sub.id)
        q.set_options(['A', 'B'])
        db.session.add(q); db.session.commit(); return q.id

def test_question_bank_create_and_normalize_difficulty(client, app):
    login_teacher(client)
    with app.app_context():
        s = Series.query.filter_by(name='1º ano').first(); sub = Subject.query.filter_by(series_id=s.id).first()
    response = client.post('/admin/question-bank', data={
        'question':'Qual é a resposta?', 'option_a':'A', 'option_b':'B', 'correct':'0',
        'series_id':str(s.id), 'subject_id':str(sub.id), 'difficulty':'NAO_EXISTE',
        'category':'  Sintaxe  ', 'tags':'Python, python,  revisão '
    })
    assert response.status_code == 302
    with app.app_context():
        q = QuestionBank.query.filter_by(question='Qual é a resposta?').one()
        assert q.difficulty == 'medio'
        assert q.category == 'Sintaxe'
        assert q.tags == 'python, revisão'

def test_question_bank_search_filters_and_sort(client, app):
    login_teacher(client)
    seed_question(app, 'Questão sobre funções', 'Funções', 'python,funções', 'facil')
    seed_question(app, 'Questão sobre listas', 'Estruturas', 'python,listas', 'dificil')
    r = client.get('/admin/question-bank?q=funções')
    assert r.status_code == 200 and 'Questão sobre funções' in r.text and 'Questão sobre listas' not in r.text
    r = client.get('/admin/question-bank?category=Estruturas&difficulty=dificil')
    assert r.status_code == 200 and 'Questão sobre listas' in r.text and 'Questão sobre funções' not in r.text
    r = client.get('/admin/question-bank?tag=listas')
    assert r.status_code == 200 and 'Questão sobre listas' in r.text
    r = client.get('/admin/question-bank?sort=oldest')
    assert r.status_code == 200
