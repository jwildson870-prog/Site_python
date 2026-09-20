from datetime import datetime, timedelta
from pathlib import Path

from app.extensions import db
from app.models import User, Activity, ActivityAttempt, Series, Subject


def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})


def make_student(client, email, name='Aluno Ranking'):
    client.post('/auth/register', data={
        'name': name, 'email': email,
        'password': 'Senha1234!', 'confirm_password': 'Senha1234!'
    })
    login(client, email, 'Senha1234!')


def test_student_ranking_requires_login(client):
    response = client.get('/aluno/ranking', follow_redirects=False)
    assert response.status_code in (302, 303)


def test_student_ranking_scores_only_objective_correct_answers(app, client):
    make_student(client, 'ranking59@test.local')
    with app.app_context():
        user = User.query.filter_by(email='ranking59@test.local').first()
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        activity = Activity(
            title='Ranking', description='', series_id=series.id, subject_id=subject.id,
            questions_json='[]'
        )
        activity.set_questions([
            {'kind': 'objective', 'question': '1', 'options': ['A'], 'correct': 'A'},
            {'kind': 'essay', 'question': '2', 'options': [], 'correct': ''},
            {'kind': 'objective', 'question': '3', 'options': ['B'], 'correct': 'B'},
        ])
        db.session.add(activity)
        db.session.commit()
        attempt = ActivityAttempt(
            user_id=user.id, activity_id=activity.id,
            answers_json='{"0":"A","1":"qualquer texto","2":"errado"}',
            presented_questions_json=activity.questions_json,
            created_at=datetime.utcnow(), total=3, score=3.3
        )
        db.session.add(attempt)
        db.session.commit()
    response = client.get('/aluno/ranking')
    assert response.status_code == 200
    assert '10 ponto(s)' in response.text or '10 pts' in response.text
    assert 'Ranking' in response.text


def test_admin_ranking_route_and_search(client):
    client.post('/auth/register', data={'name':'Aluno Ranking Admin','email':'ranking59admin@test.local','password':'Senha1234!','confirm_password':'Senha1234!'})
    client.get('/auth/logout')
    login(client, 'professor@portaljm.com', 'PortalJM@2026')
    response = client.get('/admin/ranking?q=Aluno%20Ranking%20Admin')
    assert response.status_code == 200
    assert 'Aluno Ranking Admin' in response.text


def test_ranking_templates_and_navigation_exist():
    base = Path('app/templates/base.html').read_text()
    dashboard = Path('app/templates/admin/dashboard.html').read_text()
    assert Path('app/templates/student/ranking.html').exists()
    assert Path('app/templates/admin/ranking.html').exists()
    assert "student.ranking" in base
    assert "admin.ranking" in dashboard
