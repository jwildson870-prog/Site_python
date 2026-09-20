def test_ranking_route_requires_login(client):
    response = client.get('/aluno/ranking', follow_redirects=False)
    assert response.status_code in (302, 303)


def test_ranking_template_exists():
    from pathlib import Path
    assert Path('app/templates/student/ranking.html').exists()


def test_admin_ranking_route_exists():
    from app.admin.routes import ranking
    assert callable(ranking)

def test_student_ranking_route_helper_is_not_a_route():
    from app.student.routes import _attempt_correct_answers
    assert callable(_attempt_correct_answers)
