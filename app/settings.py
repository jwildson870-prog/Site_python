from .extensions import db
from .models import PortalSetting

DEFAULT_SETTINGS = {
    "public_registration": "true",
    "upload_limit_mb": "25",
    "google_oauth_enabled": "true",
    "institution_name": "Portal Python",
    "notifications_enabled": "true",
    "notification_activities": "true",
    "notification_materials": "true",
    "notification_deadlines": "true",
    "notification_announcements": "true",
    "alerts_enabled": "true",
    "alert_inactive_students": "true",
    "alert_pending_activities": "true",
    "alert_low_performance": "true",
    "alert_performance_drop": "true",
    "alert_deadlines": "true",
    "ai_tutor_enabled": "false",
    "ai_feedback_enabled": "false",
    "ai_question_gen_enabled": "false",
    "ai_class_summary_enabled": "false",
    "ai_project_precorrect_enabled": "false",
    "ai_code_review_enabled": "false",
    "ai_tutor_rate_limit": "10",
}

def get_setting(key, default=None):
    row = PortalSetting.query.filter_by(key=key).first()
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key, default)

def get_bool(key, default=False):
    value = str(get_setting(key, str(default).lower())).strip().lower()
    return value in {"1", "true", "yes", "on", "sim"}

def get_int(key, default=0):
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default

def set_setting(key, value):
    row = PortalSetting.query.filter_by(key=key).first()
    if row is None:
        row = PortalSetting(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)
    return row

def ensure_default_settings():
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        if PortalSetting.query.filter_by(key=key).first() is None:
            db.session.add(PortalSetting(key=key, value=value))
            changed = True
    if changed:
        db.session.commit()
