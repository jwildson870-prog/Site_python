from app.models import LearningPath, LearningPathItem, Series, Subject, Content, Activity

def test_phase58_learning_path_creation_and_prerequisite(client, admin_user, app):
    with app.app_context():
        s=Series(name='1ª Série'); db=__import__('app.extensions',fromlist=['db']).db; db.session.add(s); db.session.flush()
        sub=Subject(name='Química',series_id=s.id); c1=Content(title='C1',kind='explanation',series_id=s.id,subject_id=sub.id); c2=Content(title='C2',kind='explanation',series_id=s.id,subject_id=sub.id); db.session.add_all([sub,c1,c2]); db.session.commit()
        path=LearningPath(title='Trilha teste',series_id=s.id,subject_id=sub.id); db.session.add(path); db.session.flush()
        a=LearningPathItem(path_id=path.id,title='Primeira',item_type='content',target_id=c1.id,position=0); db.session.add(a); db.session.flush()
        b=LearningPathItem(path_id=path.id,title='Segunda',item_type='content',target_id=c2.id,position=1,prerequisite_id=a.id); db.session.add(b); db.session.commit()
        assert len(path.items)==2 and path.items[1].prerequisite_id==path.items[0].id
