import pytest
from app import create_app
from app.extensions import db
from app.models import User, Series, Subject, Experiment, ProjectSubmission, ProjectRubric, ProjectRubricCriterion, ProjectComment

@pytest.fixture()
def app():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'WTF_CSRF_ENABLED': False, 'SECRET_KEY': 'test'})
    with app.app_context():
        db.create_all()
        s=Series(name='1ª Série'); db.session.add(s); db.session.flush()
        sub=Subject(name='Programação', series_id=s.id); db.session.add(sub); db.session.flush()
        admin=User(name='Professor', email='prof@example.com', role='admin'); admin.set_password('123456');
        student=User(name='Aluno', email='aluno@example.com', role='student'); student.set_password('123456')
        db.session.add_all([admin,student]); db.session.flush()
        exp=Experiment(title='Projeto teste', series_id=s.id, subject_id=sub.id); db.session.add(exp); db.session.commit()
    yield app
    with app.app_context(): db.drop_all()

def login(client, email):
    return client.post('/login', data={'email':email,'password':'123456'}, follow_redirects=True)

def test_student_can_submit_and_comment(app):
    client=app.test_client(); login(client,'aluno@example.com')
    r=client.post('/aluno/experimento/1', data={'action':'submit','content':'Minha entrega'})
    assert r.status_code == 302
    r=client.post('/aluno/experimento/1', data={'action':'comment','body':'Professor, tenho uma dúvida.'})
    assert r.status_code == 302
    with app.app_context():
        assert ProjectSubmission.query.count()==1
        assert ProjectComment.query.count()==1

def test_admin_can_manage_rubric_and_feedback(app):
    client=app.test_client(); login(client,'prof@example.com')
    client.post('/aluno/experimento/1', data={'action':'submit','content':'Entrega'}, follow_redirects=True)
    r=client.post('/admin/experiments/1/entregas', data={'action':'rubric','rubric_title':'Rubrica','criterion_name':['Código','Documentação'],'criterion_points':['60','40'],'criterion_description':['Qualidade','Clareza']})
    assert r.status_code == 302
    with app.app_context():
        sub=ProjectSubmission.query.first(); assert ProjectRubric.query.count()==1; assert ProjectRubricCriterion.query.count()==2; sid=sub.id
    r=client.post('/admin/experiments/1/entregas', data={'action':'feedback','submission_id':sid,'teacher_feedback':'Muito bom.','score':'85'})
    assert r.status_code == 302
    with app.app_context():
        sub=ProjectSubmission.query.get(sid); assert sub.score==85 and sub.status=='reviewed'
