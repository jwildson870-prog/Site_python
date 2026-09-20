from datetime import datetime, timedelta
from pathlib import Path

from app.extensions import db
from app.models import User, Series, Subject, Content, Activity, ActivityAttempt, Progress


def login(client, email, password):
    return client.post('/auth/login', data={'email': email, 'password': password})


def make_student(client, email):
    client.post('/auth/register', data={
        'name': 'Aluno 5.9', 'email': email,
        'password': 'Senha1234!', 'confirm_password': 'Senha1234!'
    })
    login(client, email, 'Senha1234!')


def test_weekly_goal_counts_content_and_activity(app, client):
    make_student(client, 'student59@test.local')
    with app.app_context():
        user = User.query.filter_by(email='student59@test.local').first()
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        content = Content(title='C', description='', kind='explanation', body='C', series_id=series.id,
                          subject_id=subject.id, status='published')
        activity = Activity(title='A', description='', series_id=series.id, subject_id=subject.id,
                            questions_json='[]')
        db.session.add_all([content, activity])
        db.session.commit()
        db.session.add(Progress(user_id=user.id, content_id=content.id))
        db.session.add(ActivityAttempt(user_id=user.id, activity_id=activity.id,
                                       answers_json='{}', presented_questions_json='[]'))
        db.session.commit()
        from app.student.routes import _weekly_engagement
        goal, completed, target, percent = _weekly_engagement(user.id)
        assert goal.target == 3
        assert completed == 2
        assert percent == 67


def test_student_can_update_weekly_goal(client):
    make_student(client, 'student59b@test.local')
    response = client.post('/aluno/metas', data={'target': '7'}, follow_redirects=True)
    assert response.status_code == 200
    assert 'Meta semanal atualizada.' in response.text
    assert '7 ações' in response.text


def test_engagement_page_shows_pending_and_upcoming_deadline(app, client):
    make_student(client, 'student59c@test.local')
    with app.app_context():
        series = Series.query.first()
        subject = Subject.query.filter_by(series_id=series.id).first()
        activity = Activity(title='Prazo próximo', description='', series_id=series.id, subject_id=subject.id,
                            due_at=datetime.utcnow() + timedelta(hours=24), questions_json='[]')
        db.session.add(activity)
        db.session.commit()
    response = client.get('/aluno/metas')
    assert response.status_code == 200
    assert 'Prazo próximo' in response.text
    assert 'Prazos próximos' in response.text


def test_achievements_are_individual_and_have_six_definitions(client):
    make_student(client, 'student59d@test.local')
    response = client.get('/aluno/conquistas')
    assert response.status_code == 200
    assert 'Primeiro projeto' in response.text
    assert '/ 6 desbloqueadas' in response.text


def test_engagement_reminder_respects_notification_setting(app, client):
    make_student(client, 'student59e@test.local')
    with app.app_context():
        from app.settings import set_setting
        set_setting('notifications_enabled', 'false')
        db.session.commit()
    response = client.get('/aluno/')
    assert response.status_code == 200
    with app.app_context():
        from app.models import Notification
        assert Notification.query.filter_by(user_id=User.query.filter_by(email='student59e@test.local').first().id).count() == 0


def test_deadline_setting_does_not_disable_weekly_goal_reminder(app, client):
    make_student(client, 'student59g@test.local')
    with app.app_context():
        from app.settings import set_setting
        set_setting('notifications_enabled', 'true')
        set_setting('notification_deadlines', 'false')
        db.session.commit()
    response = client.get('/aluno/')
    assert response.status_code == 200
    with app.app_context():
        from app.models import Notification
        user = User.query.filter_by(email='student59g@test.local').first()
        assert Notification.query.filter_by(user_id=user.id).filter(
            Notification.message.like('Lembrete de estudo: sua meta semanal%')
        ).count() == 1
