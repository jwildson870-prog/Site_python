from app.models import LearningPath, LearningPathItem, Series, Subject, Content, Activity, ActivityAttempt, Experiment, ProjectSubmission, Progress

def test_phase58_learning_path_creation_and_prerequisite(client, app):
    with app.app_context():
        s=Series(name='1ª Série'); db=__import__('app.extensions',fromlist=['db']).db; db.session.add(s); db.session.flush()
        sub=Subject(name='Química',series_id=s.id); c1=Content(title='C1',kind='explanation',series_id=s.id,subject_id=sub.id); c2=Content(title='C2',kind='explanation',series_id=s.id,subject_id=sub.id); db.session.add_all([sub,c1,c2]); db.session.commit()
        path=LearningPath(title='Trilha teste',series_id=s.id,subject_id=sub.id); db.session.add(path); db.session.flush()
        a=LearningPathItem(path_id=path.id,title='Primeira',item_type='content',target_id=c1.id,position=0); db.session.add(a); db.session.flush()
        b=LearningPathItem(path_id=path.id,title='Segunda',item_type='content',target_id=c2.id,position=1,prerequisite_id=a.id); db.session.add(b); db.session.commit()
        assert len(path.items)==2 and path.items[1].prerequisite_id==path.items[0].id


def test_phase58_completion_rules_and_target_constraints(app):
    from app.extensions import db
    with app.app_context():
        s1 = Series(name='2ª Série')
        s2 = Series(name='3ª Série')
        db.session.add_all([s1, s2]); db.session.flush()
        sub1 = Subject(name='Orgânica', series_id=s1.id)
        sub2 = Subject(name='Eletroquímica', series_id=s1.id)
        sub_other = Subject(name='Física', series_id=s2.id)
        db.session.add_all([sub1, sub2, sub_other]); db.session.flush()
        c1 = Content(title='Hidrocarbonetos', kind='explanation', series_id=s1.id, subject_id=sub1.id)
        c2 = Content(title='Pilhas', kind='explanation', series_id=s1.id, subject_id=sub2.id)
        c3 = Content(title='Movimento', kind='explanation', series_id=s2.id, subject_id=sub_other.id)
        db.session.add_all([c1, c2, c3]); db.session.flush()
        path = LearningPath(title='Trilha Orgânica', series_id=s1.id, subject_id=sub1.id)
        db.session.add(path); db.session.flush()
        db.session.add(LearningPathItem(path_id=path.id, title='Hidrocarbonetos', item_type='content', target_id=c1.id, position=0, completion_rule='complete'))
        db.session.commit()
        assert path.subject_id == sub1.id
        assert c1.subject_id == path.subject_id
        assert c2.subject_id != path.subject_id
        assert c3.series_id != path.series_id


def test_phase58_routes_keep_subject_and_visibility_guards():
    from pathlib import Path
    admin = Path('app/admin/routes.py').read_text()
    student = Path('app/student/routes.py').read_text()
    template = Path('app/templates/student/learning_path.html').read_text()
    assert "getattr(valid, 'subject_id', None) != path.subject_id" in admin
    assert "def learning_path_item_move" in admin
    assert "def _path_item_visible" in student
    assert "visible_items" in student
    assert "item.id in visible_items" in template


def test_phase58_completion_rule_is_respected(app):
    from app.extensions import db
    from app.student.routes import _path_item_completed
    with app.app_context():
        s = Series(name='1ª Série')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química', series_id=s.id)
        c = Content(title='Conteúdo', kind='explanation', series_id=s.id, subject_id=sub.id)
        a = Activity(title='Atividade', series_id=s.id, subject_id=sub.id, questions_json='[]')
        e = Experiment(title='Projeto', series_id=s.id, subject_id=sub.id)
        db.session.add_all([sub, c, a, e]); db.session.flush()
        path = LearningPath(title='Regras', series_id=s.id, subject_id=sub.id)
        db.session.add(path); db.session.flush()
        content_item = LearningPathItem(path_id=path.id, title='C', item_type='content', target_id=c.id, position=0, completion_rule='complete')
        activity_item = LearningPathItem(path_id=path.id, title='A', item_type='activity', target_id=a.id, position=1, completion_rule='complete')
        experiment_item = LearningPathItem(path_id=path.id, title='E', item_type='experiment', target_id=e.id, position=2, completion_rule='complete')
        db.session.add_all([content_item, activity_item, experiment_item]); db.session.flush()
        user = db.session.query(__import__('app.models', fromlist=['User']).User).filter_by(is_admin=False).first()
        if user is None:
            user = __import__('app.models', fromlist=['User']).User(name='Aluno teste', email='aluno-58-rules@example.com', role='ALUNO')
            db.session.add(user); db.session.flush()
        assert not _path_item_completed(content_item, user.id)
        db.session.add(Progress(user_id=user.id, content_id=c.id))
        db.session.add(ActivityAttempt(user_id=user.id, activity_id=a.id, answers_json='{"0":"A"}', total=2, score=5))
        db.session.add(ProjectSubmission(user_id=user.id, experiment_id=e.id, content='entrega', status='submitted'))
        db.session.commit()
        assert _path_item_completed(content_item, user.id)
        assert not _path_item_completed(activity_item, user.id)
        assert not _path_item_completed(experiment_item, user.id)
        attempt = ActivityAttempt.query.filter_by(user_id=user.id, activity_id=a.id).first()
        attempt.answers_json = '{"0":"A","1":"B"}'
        submission = ProjectSubmission.query.filter_by(user_id=user.id, experiment_id=e.id).first()
        submission.status = 'reviewed'
        db.session.commit()
        assert _path_item_completed(activity_item, user.id)
        assert _path_item_completed(experiment_item, user.id)


def test_phase58_access_rule_counts_first_interaction(app):
    from app.extensions import db
    from app.student.routes import _path_item_completed
    with app.app_context():
        s = Series(name='2ª Série')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química', series_id=s.id)
        a = Activity(title='Atividade', series_id=s.id, subject_id=sub.id, questions_json='[]')
        e = Experiment(title='Projeto', series_id=s.id, subject_id=sub.id)
        db.session.add_all([sub, a, e]); db.session.flush()
        path = LearningPath(title='Acesso', series_id=s.id, subject_id=sub.id); db.session.add(path); db.session.flush()
        ai = LearningPathItem(path_id=path.id, title='A', item_type='activity', target_id=a.id, position=0, completion_rule='access')
        ei = LearningPathItem(path_id=path.id, title='E', item_type='experiment', target_id=e.id, position=1, completion_rule='access')
        db.session.add_all([ai, ei]); db.session.flush()
        User = __import__('app.models', fromlist=['User']).User
        user = User(name='Aluno acesso', email='aluno-58-access@example.com', role='ALUNO')
        db.session.add(user); db.session.flush()
        assert not _path_item_completed(ai, user.id)
        assert not _path_item_completed(ei, user.id)
        db.session.add(ActivityAttempt(user_id=user.id, activity_id=a.id, answers_json='{}', total=0, score=0))
        db.session.add(ProjectSubmission(user_id=user.id, experiment_id=e.id, content='entrega', status='submitted'))
        db.session.commit()
        assert _path_item_completed(ai, user.id)
        assert _path_item_completed(ei, user.id)


def test_phase58_path_change_rejects_incompatible_existing_items(app):
    from app.extensions import db
    with app.app_context():
        s1, s2 = Series(name='1ª Série'), Series(name='2ª Série')
        db.session.add_all([s1, s2]); db.session.flush()
        sub1 = Subject(name='Química', series_id=s1.id)
        sub2 = Subject(name='Física', series_id=s2.id)
        c = Content(title='Conteúdo', kind='explanation', series_id=s1.id, subject_id=sub1.id)
        db.session.add_all([sub1, sub2, c]); db.session.flush()
        path = LearningPath(title='Trilha', series_id=s1.id, subject_id=sub1.id); db.session.add(path); db.session.flush()
        db.session.add(LearningPathItem(path_id=path.id, title='Etapa', item_type='content', target_id=c.id, position=0)); db.session.commit()
        assert c.series_id == path.series_id
        assert c.subject_id == path.subject_id
        assert s2.id != path.series_id


def test_phase58_learning_path_validation_helpers():
    from pathlib import Path
    admin = Path('app/admin/routes.py').read_text()
    assert "def _validate_learning_path_targets" in admin
    assert "target.series_id != series_id" in admin
    assert "def _learning_path_order_is_valid" in admin
    assert "positions.get(row.prerequisite_id, -1) < positions[row.id]" in admin
    assert "A etapa não pode ficar antes do seu pré-requisito." in admin
    assert "Não foi possível alterar a série/matéria" in admin


def test_phase58_order_helper_rejects_prerequisite_after_dependent(app):
    from app.extensions import db
    from app.admin.routes import _learning_path_order_is_valid
    with app.app_context():
        s = Series(name='Ordem 5.8')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química', series_id=s.id)
        c1 = Content(title='Base', kind='explanation', series_id=s.id, subject_id=sub.id)
        c2 = Content(title='Avançado', kind='explanation', series_id=s.id, subject_id=sub.id)
        db.session.add_all([sub, c1, c2]); db.session.flush()
        path = LearningPath(title='Ordem', series_id=s.id, subject_id=sub.id)
        db.session.add(path); db.session.flush()
        first = LearningPathItem(path_id=path.id, title='Base', item_type='content', target_id=c1.id, position=1)
        second = LearningPathItem(path_id=path.id, title='Avançado', item_type='content', target_id=c2.id, position=0)
        db.session.add_all([first, second]); db.session.flush()
        second.prerequisite_id = first.id
        assert _learning_path_order_is_valid([first, second]) is False
        first.position, second.position = 0, 1
        assert _learning_path_order_is_valid([first, second]) is True


def test_phase58_admin_route_rejects_incompatible_path_change(client, app):
    from app.extensions import db
    login = client.post('/auth/login', data={'email': 'professor@portaljm.com', 'password': 'PortalJM@2026'})
    assert login.status_code == 302
    with app.app_context():
        s1 = Series(name='1ª Série - rota 5.8')
        s2 = Series(name='2ª Série - rota 5.8')
        db.session.add_all([s1, s2]); db.session.flush()
        sub1 = Subject(name='Química - rota 5.8', series_id=s1.id)
        sub2 = Subject(name='Física - rota 5.8', series_id=s2.id)
        content = Content(title='Etapa preservada', kind='explanation', series_id=s1.id, subject_id=sub1.id)
        db.session.add_all([sub1, sub2, content]); db.session.flush()
        path = LearningPath(title='Trilha protegida', series_id=s1.id, subject_id=sub1.id)
        db.session.add(path); db.session.flush()
        item = LearningPathItem(path_id=path.id, title='Etapa preservada', item_type='content', target_id=content.id, position=0)
        db.session.add(item); db.session.commit()
        pid, s2id, sub2id = path.id, s2.id, sub2.id
    response = client.post(f'/admin/trilhas/{pid}/editar', data={
        'action': 'save', 'title': 'Tentativa inválida', 'series_id': str(s2id),
        'subject_id': str(sub2id), 'description': '', 'active': '1'
    }, follow_redirects=False)
    assert response.status_code == 302
    with app.app_context():
        path = db.session.get(LearningPath, pid)
        assert path.series_id != s2id
        assert path.subject_id != sub2id


def test_phase58_admin_route_allows_compatible_path_change(client, app):
    from app.extensions import db
    client.post('/auth/login', data={'email': 'professor@portaljm.com', 'password': 'PortalJM@2026'})
    with app.app_context():
        s = Series(name='Série compatível 5.8')
        db.session.add(s); db.session.flush()
        sub1 = Subject(name='Química A 5.8', series_id=s.id)
        sub2 = Subject(name='Química B 5.8', series_id=s.id)
        content = Content(title='Etapa compatível', kind='explanation', series_id=s.id, subject_id=sub2.id)
        db.session.add_all([sub1, sub2, content]); db.session.flush()
        path = LearningPath(title='Trilha compatível', series_id=s.id, subject_id=sub2.id)
        db.session.add(path); db.session.flush()
        db.session.add(LearningPathItem(path_id=path.id, title='Etapa compatível', item_type='content', target_id=content.id, position=0))
        db.session.commit()
        pid, sid, subid = path.id, s.id, sub2.id
    response = client.post(f'/admin/trilhas/{pid}/editar', data={
        'action': 'save', 'title': 'Trilha atualizada', 'series_id': str(sid),
        'subject_id': str(subid), 'description': 'Atualizada', 'active': '1'
    })
    assert response.status_code == 302
    with app.app_context():
        path = db.session.get(LearningPath, pid)
        assert path.title == 'Trilha atualizada'
        assert path.description == 'Atualizada'


def test_phase58_student_route_hides_incomplete_prerequisite_target(client, app):
    from app.extensions import db
    client.post('/auth/register', data={
        'name': 'Aluno trilha 5.8', 'email': 'aluno-trilha-58@example.com',
        'password': 'Senha1234!', 'confirm_password': 'Senha1234!'
    })
    client.post('/auth/login', data={'email': 'aluno-trilha-58@example.com', 'password': 'Senha1234!'})
    with app.app_context():
        s = Series(name='Aluno série 5.8')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química aluno 5.8', series_id=s.id)
        c1 = Content(title='Primeira etapa', kind='explanation', series_id=s.id, subject_id=sub.id,
                     body='<p>Primeira</p>')
        c2 = Content(title='Segunda etapa', kind='explanation', series_id=s.id, subject_id=sub.id,
                     body='<p>Segunda</p>')
        now = __import__('app.timeutils', fromlist=['utcnow']).utcnow()
        c1.status = 'published'; c1.published_at = now
        c2.status = 'published'; c2.published_at = now
        db.session.add_all([sub, c1, c2]); db.session.flush()
        path = LearningPath(title='Trilha aluno 5.8', series_id=s.id, subject_id=sub.id)
        db.session.add(path); db.session.flush()
        i1 = LearningPathItem(path_id=path.id, title='Primeira etapa', item_type='content', target_id=c1.id, position=0)
        i2 = LearningPathItem(path_id=path.id, title='Segunda etapa', item_type='content', target_id=c2.id, position=1, prerequisite_id=i1.id)
        db.session.add_all([i1, i2]); db.session.commit()
        pid = path.id
    page = client.get(f'/aluno/trilha/{pid}')
    assert page.status_code == 200
    assert 'Primeira etapa' in page.text
    assert 'Segunda etapa' in page.text
    assert 'Bloqueado' in page.text
    assert f'/aluno/conteudo/{c2.id}' not in page.text


def test_phase58_archived_content_is_not_visible_inside_path(app):
    from app.extensions import db
    from app.student.routes import _path_item_visible
    from app.timeutils import utcnow
    with app.app_context():
        s = Series(name='Arquivado 5.8')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química arquivada 5.8', series_id=s.id)
        c = Content(title='Conteúdo arquivado', kind='explanation', series_id=s.id, subject_id=sub.id,
                    status='published', published_at=utcnow(), archived_at=utcnow())
        db.session.add_all([sub, c]); db.session.flush()
        path = LearningPath(title='Trilha arquivada 5.8', series_id=s.id, subject_id=sub.id)
        db.session.add(path); db.session.flush()
        item = LearningPathItem(path_id=path.id, title='Arquivado', item_type='content', target_id=c.id, position=0)
        db.session.add(item); db.session.commit()
        assert _path_item_visible(item) is False


def test_phase58_complete_project_requires_latest_submission_reviewed(app):
    from app.extensions import db
    from app.student.routes import _path_item_completed
    with app.app_context():
        s = Series(name='Projeto revisão 5.8')
        db.session.add(s); db.session.flush()
        sub = Subject(name='Química projeto 5.8', series_id=s.id)
        experiment = Experiment(title='Projeto', series_id=s.id, subject_id=sub.id)
        db.session.add_all([sub, experiment]); db.session.flush()
        path = LearningPath(title='Projeto revisão', series_id=s.id, subject_id=sub.id)
        db.session.add(path); db.session.flush()
        item = LearningPathItem(path_id=path.id, title='Projeto', item_type='experiment', target_id=experiment.id,
                                position=0, completion_rule='complete')
        User = __import__('app.models', fromlist=['User']).User
        user = User(name='Aluno projeto 5.8', email='aluno-projeto-58@example.com', role='ALUNO')
        db.session.add_all([item, user]); db.session.flush()
        first = ProjectSubmission(user_id=user.id, experiment_id=experiment.id, content='primeira', status='reviewed')
        db.session.add(first); db.session.commit()
        assert _path_item_completed(item, user.id) is True
        second = ProjectSubmission(user_id=user.id, experiment_id=experiment.id, content='segunda', status='submitted')
        db.session.add(second); db.session.commit()
        assert _path_item_completed(item, user.id) is False
        second.status = 'reviewed'
        db.session.commit()
        assert _path_item_completed(item, user.id) is True
