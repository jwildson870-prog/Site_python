from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_production_secret_key_is_required():
    source = (ROOT / 'app' / '__init__.py').read_text()
    assert "SECRET_KEY deve ser configurada" in source
    assert "dev-change-me" not in source


def test_security_headers_are_configured():
    source = (ROOT / 'app' / '__init__.py').read_text()
    for header in ('X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy', 'Content-Security-Policy'):
        assert header in source
    assert 'Strict-Transport-Security' in source


def test_session_security_and_csrf_are_enabled():
    source = (ROOT / 'app' / '__init__.py').read_text()
    assert 'SESSION_COOKIE_HTTPONLY=True' in source
    assert "SESSION_COOKIE_SAMESITE='Lax'" in source
    assert 'CSRFProtect(app)' in source
    assert "login.session_protection='strong'" in source


def test_student_and_admin_have_server_side_role_guards():
    admin = (ROOT / 'app' / 'admin' / 'routes.py').read_text()
    student = (ROOT / 'app' / 'student' / 'routes.py').read_text()
    assert 'if not current_user.is_authenticated' in admin
    assert 'if not current_user.is_admin: abort(403)' in admin
    assert 'if not current_user.is_authenticated' in student
    assert 'if current_user.is_admin:abort(403)' in student


def test_uploads_use_allowlist_and_content_signature():
    source = (ROOT / 'app' / 'admin' / 'routes.py').read_text()
    assert 'ALLOWED_EXTENSIONS' in source
    assert 'file_signature_ok' in source
    assert 'mime_ok' in source
    assert 'secure_filename' in source
    assert 'MAX_UPLOAD = 25 * 1024 * 1024' in source


def test_explanation_html_is_sanitized_before_rendering():
    source = (ROOT / 'app' / 'templates' / 'student' / 'content.html').read_text()
    init = (ROOT / 'app' / '__init__.py').read_text()
    assert 'sanitize_html' in source
    assert 'bleach.clean' in init


def test_admin_file_route_requires_database_association():
    source = (ROOT / 'app' / 'admin' / 'routes.py').read_text()
    assert "Content.query.filter_by(file_name=filename).first()" in source
    assert 'abort(404)' in source
