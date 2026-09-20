"""Verificações estáticas da fase 5.1."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / 'app' / 'admin' / 'routes.py'
SETTINGS = ROOT / 'app' / 'settings.py'
AUTH = ROOT / 'app' / 'auth' / 'routes.py'


def test_settings_route_supports_get_and_post_and_persists():
    source = ADMIN.read_text(encoding='utf-8')
    assert "@admin_bp.route('/settings', methods=['GET', 'POST'])" in source
    assert 'set_setting(' in source
    assert "current_app.config['MAX_CONTENT_LENGTH']" in source


def test_all_phase51_keys_are_saved():
    source = ADMIN.read_text(encoding='utf-8')
    for key in (
        'public_registration', 'upload_limit_mb', 'google_oauth_enabled',
        'institution_name', 'notifications_enabled',
        'notification_activities', 'notification_materials',
        'notification_deadlines', 'notification_announcements',
        'alerts_enabled', 'alert_inactive_students',
        'alert_pending_activities', 'alert_low_performance',
        'alert_performance_drop', 'alert_deadlines',
    ):
        assert key in source


def test_notifications_and_alerts_honor_settings():
    source = ADMIN.read_text(encoding='utf-8')
    assert "get_bool('notifications_enabled', True)" in source
    assert 'setting_by_category' in source
    assert '_alert_kind_enabled(kind)' in source
    assert "get_bool('alerts_enabled', True)" in source


def test_public_registration_and_google_oauth_use_settings():
    source = AUTH.read_text(encoding='utf-8')
    assert "get_bool('public_registration', True)" in source
    assert "get_bool('google_oauth_enabled', True)" in source


def test_defaults_cover_every_phase51_setting():
    import ast
    tree = ast.parse(SETTINGS.read_text(encoding='utf-8'))
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DEFAULT_SETTINGS' for t in node.targets):
            value = node.value
            if isinstance(value, ast.Dict):
                keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
    expected = {
        'public_registration', 'upload_limit_mb', 'google_oauth_enabled',
        'institution_name', 'notifications_enabled',
        'notification_activities', 'notification_materials',
        'notification_deadlines', 'notification_announcements',
        'alerts_enabled', 'alert_inactive_students',
        'alert_pending_activities', 'alert_low_performance',
        'alert_performance_drop', 'alert_deadlines',
    }
    assert expected <= keys
