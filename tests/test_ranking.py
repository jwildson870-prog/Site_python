def test_ranking_route_requires_login(client):
    response = client.get('/aluno/ranking', follow_redirects=False)
    assert response.status_code in (302, 303)


def test_ranking_template_exists():
    from pathlib import Path
    assert Path('app/templates/student/ranking.html').exists()
