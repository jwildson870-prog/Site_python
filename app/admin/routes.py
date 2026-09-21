import os, uuid, io, json, mimetypes
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_from_directory, Response, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
from ..storage import upload as storage_upload, delete as storage_delete, get_file, b2_enabled, StorageError
from ..extensions import db
from ..timeutils import utcnow
from ..models import Series, Subject, Content, ContentHistory, User, Activity, ActivityHistory, ActivityAttempt, Experiment, Notification, Alert, QuestionBank, Progress, LearningPath, LearningPathItem
from ..pptx_preview import convert_pptx_to_images
from ..activity_library import PREBUILT_ACTIVITIES, BY_SLUG
from ..settings import DEFAULT_SETTINGS, get_bool, get_int, set_setting

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
ALLOWED_KINDS = {'explanation','file','pdf','slide','video','link'}
DIFFICULTIES = {'facil', 'medio', 'dificil'}
DEFAULT_DIFFICULTY = 'medio'

def normalize_difficulty(value):
    """Normaliza a dificuldade aceita pelo Portal, sem confiar no formulário."""
    value = (value or DEFAULT_DIFFICULTY).strip().lower()
    return value if value in DIFFICULTIES else DEFAULT_DIFFICULTY

ALLOWED_EXTENSIONS = {'pdf','png','jpg','jpeg','webp','gif','ppt','pptx','doc','docx','txt'}
DEFAULT_MAX_UPLOAD = 25 * 1024 * 1024
SAFE_INLINE_TYPES = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
SAFE_TYPES = {
    'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp',
    'doc': 'application/msword', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'ppt': 'application/vnd.ms-powerpoint', 'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'txt': 'text/plain; charset=utf-8',
}

def safe_file_response(obj, filename, fallback_type='application/octet-stream'):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return Response(obj['Body'].iter_chunks(chunk_size=64 * 1024), content_type=SAFE_TYPES.get(ext, fallback_type), headers={
        'Content-Length': str(obj['ContentLength']),
        'Content-Disposition': 'inline' if ext in SAFE_INLINE_TYPES else 'attachment',
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
    })

# Assinaturas mínimas para os formatos mais comuns. A extensão sozinha não é
# suficiente, pois pode ser alterada pelo usuário antes do upload.
MAGIC_SIGNATURES = {
    'pdf': (b'%PDF-',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'gif': (b'GIF87a', b'GIF89a'),
    'webp': (b'RIFF',),
    'doc': (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',),
    'ppt': (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',),
    'docx': (b'PK\x03\x04',),
    'pptx': (b'PK\x03\x04',),
    'xlsx': (b'PK\x03\x04',),
}

def file_signature_ok(file_storage, extension):
    signatures = MAGIC_SIGNATURES.get(extension)
    if not signatures:
        return True
    stream = file_storage.stream
    try:
        position = stream.tell()
        head = stream.read(16)
        stream.seek(position)
    except (AttributeError, OSError):
        return False
    return any(head.startswith(signature) for signature in signatures)

def valid_url(value):
    parsed = urlparse(value or '')
    return parsed.scheme in ('http','https') and bool(parsed.netloc) and not parsed.username and not parsed.password and len(value) <= 1000


def mime_ok(extension, mimetype):
    expected = {
        'pdf': {'application/pdf'},
        'png': {'image/png'},
        'jpg': {'image/jpeg'}, 'jpeg': {'image/jpeg'},
        'gif': {'image/gif'}, 'webp': {'image/webp'},
        'doc': {'application/msword'}, 'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
        'ppt': {'application/vnd.ms-powerpoint'}, 'pptx': {'application/vnd.openxmlformats-officedocument.presentationml.presentation'},
        'txt': {'text/plain', 'application/octet-stream'},
    }
    allowed = expected.get(extension)
    return not allowed or not mimetype or mimetype.lower() in allowed

def save_uploaded_file(file, required_extension=None):
    if not file or not file.filename: return None, None
    original = secure_filename(file.filename); extension = Path(original).suffix.lower().lstrip('.')
    if not extension or extension not in ALLOWED_EXTENSIONS or (required_extension and extension != required_extension): return False, None
    if len(original) > 180 or any(ord(ch) < 32 for ch in original): return False, None
    max_upload = int(current_app.config.get('MAX_CONTENT_LENGTH', DEFAULT_MAX_UPLOAD) or DEFAULT_MAX_UPLOAD)
    if request.content_length and request.content_length > max_upload: return 'too_large', None
    if not file_signature_ok(file, extension): return 'invalid_signature', None
    if not mime_ok(extension, file.mimetype): return 'invalid_mime', None
    try:
        filename = storage_upload(file, original, file.mimetype)
    except StorageError as exc:
        current_app.logger.warning('Falha no upload do material: %s | %s', exc.message, exc.technical)
        return exc, None
    except Exception as exc:
        current_app.logger.exception('Falha inesperada no upload do material')
        # Nunca esconda um erro de runtime atrás da mensagem genérica: transforme-o
        # em StorageError para que o administrador saiba qual componente falhou.
        detail = str(exc).strip() or exc.__class__.__name__
        return StorageError(
            f'Falha inesperada ao enviar o arquivo ({exc.__class__.__name__}). Detalhes: {detail}',
            code='storage_unexpected',
            technical=detail,
        ), None
    return filename, original

def notify_students(message, link=None, category='announcements'):
    """Cria notificações respeitando as configurações do administrador."""
    if not get_bool('notifications_enabled', True):
        return 0
    setting_by_category = {
        'activities': 'notification_activities',
        'materials': 'notification_materials',
        'deadlines': 'notification_deadlines',
        'announcements': 'notification_announcements',
    }
    setting = setting_by_category.get(category)
    if setting and not get_bool(setting, True):
        return 0
    students = User.query.filter_by(role='student').all()
    for student in students:
        db.session.add(Notification(user_id=student.id, message=message, link=link))
    return len(students)

@admin_bp.before_request
def admin_guard():
    if not current_user.is_authenticated: return redirect(url_for('auth.login', next=request.path))
    if not current_user.is_admin: abort(403)

def _alert_kind_enabled(kind):
    if not get_bool('alerts_enabled', True):
        return False
    setting_by_kind = {
        'inactive': 'alert_inactive_students',
        'pending': 'alert_pending_activities',
        'low_performance': 'alert_low_performance',
        'performance_drop': 'alert_performance_drop',
        'deadline': 'alert_deadlines',
        'deadline_soon': 'alert_deadlines',
    }
    setting = setting_by_kind.get(kind)
    return get_bool(setting, True) if setting else True


def _visible_active_alerts():
    return [
        alert for alert in Alert.query.filter_by(resolved=False).all()
        if _alert_kind_enabled(alert.kind)
    ]


def _sync_smart_alerts(students, activities, attempts, now):
    """Cria alertas acionáveis sem duplicar alertas ativos."""
    latest_by_pair = {}
    attempts_by_student = {}
    for attempt in attempts:
        latest_by_pair.setdefault((attempt.user_id, attempt.activity_id), attempt)
        attempts_by_student.setdefault(attempt.user_id, []).append(attempt)
    existing = {(a.kind, a.user_id, a.activity_id) for a in Alert.query.filter_by(resolved=False).all()}
    created = 0

    def add(kind, user_id=None, activity_id=None, message='', link=None, priority='medium'):
        nonlocal created
        if not _alert_kind_enabled(kind):
            return
        key = (kind, user_id, activity_id)
        if key in existing:
            return
        db.session.add(Alert(kind=kind, user_id=user_id, activity_id=activity_id, message=message, link=link, priority=priority))
        existing.add(key); created += 1

    for student in students:
        latest = [a for (uid, _), a in latest_by_pair.items() if uid == student.id]
        scores = [a.score for a in latest]
        avg = sum(scores) / len(scores) if scores else None
        pending = max(len(activities) - len(latest), 0)
        if avg is not None and avg < 6:
            add('low_performance', student.id, None, f'{student.name} está com média abaixo de 6 ({avg:.1f}).', url_for('admin.user_performance', id=student.id), 'high')
        if pending >= 2:
            add('pending', student.id, None, f'{student.name} tem {pending} atividade(s) pendente(s).', url_for('admin.user_performance', id=student.id), 'medium')
        student_attempts = sorted(attempts_by_student.get(student.id, []), key=lambda a: a.created_at or datetime.min, reverse=True)
        if student_attempts and student_attempts[0].created_at and (now - student_attempts[0].created_at).days >= 14 and activities:
            add('inactive', student.id, None, f'{student.name} não entrega atividades há pelo menos 14 dias.', url_for('admin.user_performance', id=student.id), 'medium')
        if len(student_attempts) >= 2:
            recent = sum(a.score for a in student_attempts[:2]) / 2
            previous = sum(a.score for a in student_attempts[2:4]) / len(student_attempts[2:4]) if len(student_attempts) >= 4 else None
            if previous is not None and recent <= previous - 2:
                add('performance_drop', student.id, None, f'O desempenho recente de {student.name} caiu {previous-recent:.1f} ponto(s).', url_for('admin.user_performance', id=student.id), 'high')

    for activity in activities:
        if not activity.due_at:
            continue
        hours = (activity.due_at - now).total_seconds() / 3600
        if 0 <= hours <= 24:
            pending_count = max(len(students) - len({a.user_id for a in attempts if a.activity_id == activity.id}), 0)
            if pending_count:
                add('deadline', None, activity.id, f'{activity.title} vence em até 24h e ainda tem {pending_count} aluno(s) pendente(s).', url_for('admin.activity_results', id=activity.id), 'high')
        elif 24 < hours <= 72:
            pending_count = max(len(students) - len({a.user_id for a in attempts if a.activity_id == activity.id}), 0)
            if pending_count:
                add('deadline_soon', None, activity.id, f'{activity.title} vence em até 3 dias e tem {pending_count} aluno(s) pendente(s).', url_for('admin.activity_results', id=activity.id), 'medium')
    if created:
        db.session.commit()


@admin_bp.get('/')
def dashboard():
    recent_contents = Content.query.order_by(Content.id.desc()).limit(5).all()
    students = User.query.filter_by(role='student').order_by(User.id.desc()).all()
    unread_notifications = Notification.query.filter_by(read=False).count()
    total_attempts = ActivityAttempt.query.count()
    average_score = db.session.query(db.func.avg(ActivityAttempt.score)).scalar()
    average_score = round(float(average_score), 1) if average_score is not None else 0

    # Visão executiva do professor: agregações pequenas e prontas para o dashboard.
    now = utcnow()
    all_activities = Activity.query.order_by(Activity.id.desc()).all()
    all_attempts = ActivityAttempt.query.order_by(ActivityAttempt.id.desc()).all()
    latest_by_pair = {}
    for attempt in all_attempts:
        latest_by_pair.setdefault((attempt.user_id, attempt.activity_id), attempt)

    student_rows = []
    for student in students:
        latest = [a for (uid, _), a in latest_by_pair.items() if uid == student.id]
        scores = [a.score for a in latest]
        avg = round(sum(scores) / len(scores), 1) if scores else None
        pending = max(len(all_activities) - len(latest), 0)
        completion = round(len(latest) / len(all_activities) * 100) if all_activities else 0
        if avg is not None and avg < 6:
            status = 'Atenção'
        elif pending == 0 and all_activities:
            status = 'Completo'
        elif not latest:
            status = 'Sem atividade'
        else:
            status = 'Em andamento'
        student_rows.append({'student': student, 'average': avg, 'pending': pending, 'completion': completion, 'status': status})

    risk_students = sorted(
        [r for r in student_rows if r['status'] in {'Atenção', 'Sem atividade'} or r['pending'] >= 2],
        key=lambda r: (r['average'] is None, r['average'] if r['average'] is not None else 0, -r['pending'])
    )[:6]

    activity_stats = []
    student_count = len(students)
    for activity in all_activities[:6]:
        attempts = [a for a in all_attempts if a.activity_id == activity.id]
        unique_students = {a.user_id for a in attempts}
        avg = round(sum(a.score for a in attempts) / len(attempts), 1) if attempts else None
        completion = round(len(unique_students) / student_count * 100) if student_count else 0
        activity_stats.append({'activity': activity, 'attempts': len(attempts), 'average': avg, 'completion': completion})

    deadline_activities = sorted(
        [a for a in all_activities if a.due_at and a.due_at >= now],
        key=lambda a: a.due_at
    )[:5]
    recent_attempts = all_attempts[:8]
    latest_attempt_dates = [a.created_at for a in all_attempts[:8]]
    recent_activity_count = sum(1 for a in all_activities if a.created_at and (now - a.created_at).days <= 7)
    overall_completion = round(sum(r['completion'] for r in student_rows) / len(student_rows)) if student_rows else 0

    # Fase 5.10 — indicadores agregados por série. O projeto atual não possui
    # vínculo aluno→turma; por isso não inventamos esse relacionamento.
    # A comparação usa as séries já existentes como agrupamento acadêmico.
    series_indicators = []
    for serie in Series.query.order_by(Series.id).all():
        serie_activities = [a for a in all_activities if a.series_id == serie.id]
        serie_activity_ids = {a.id for a in serie_activities}
        serie_attempts = [a for a in all_attempts if a.activity_id in serie_activity_ids]
        unique_students = {a.user_id for a in serie_attempts}
        latest_scores = {}
        for attempt in sorted(serie_attempts, key=lambda a: a.id):
            latest_scores[(attempt.user_id, attempt.activity_id)] = attempt.score
        scores = list(latest_scores.values())
        average = round(sum(scores) / len(scores), 1) if scores else None
        possible = len(students) * len(serie_activities)
        completed = len(latest_scores)
        completion = round(completed / possible * 100) if possible else 0
        series_indicators.append({
            'series': serie, 'activities': len(serie_activities),
            'attempts': len(serie_attempts), 'students_active': len(unique_students),
            'average': average, 'completion': min(completion, 100),
        })

    # Alunos sem tentativa nos últimos 14 dias (ou sem nenhuma tentativa).
    last_attempt_by_student = {}
    for attempt in all_attempts:
        if attempt.created_at:
            last_attempt_by_student[attempt.user_id] = max(
                last_attempt_by_student.get(attempt.user_id, datetime.min), attempt.created_at
            )
    inactive_students = []
    for student in students:
        last = last_attempt_by_student.get(student.id)
        days = (now - last).days if last else None
        if last is None or days >= 14:
            inactive_students.append({'student': student, 'last_attempt': last, 'days': days})
    inactive_students.sort(key=lambda row: (row['last_attempt'] is not None, row['last_attempt'] or datetime.min))
    inactive_students = inactive_students[:8]

    # Atividades pendentes: quantidade estimada de alunos que ainda não
    # registraram tentativa. Mantém a mesma regra usada no monitoramento.
    pending_activity_stats = []
    for activity in all_activities:
        attempted = {a.user_id for a in all_attempts if a.activity_id == activity.id}
        pending_count = max(len(students) - len(attempted), 0)
        if pending_count:
            pending_activity_stats.append({'activity': activity, 'pending': pending_count})
    pending_activity_stats.sort(key=lambda row: (-row['pending'], row['activity'].due_at or datetime.max))
    pending_activity_stats = pending_activity_stats[:8]

    _sync_smart_alerts(students, all_activities, all_attempts, now)
    visible_alerts = _visible_active_alerts()
    visible_alerts.sort(key=lambda alert: (0 if alert.priority == 'high' else 1, -(alert.created_at.timestamp() if alert.created_at else 0)))
    smart_alerts = visible_alerts[:8]
    alert_counts = {
        'high': sum(1 for alert in visible_alerts if alert.priority == 'high'),
        'medium': sum(1 for alert in visible_alerts if alert.priority == 'medium'),
    }
    return render_template('admin/dashboard.html',
        series=Series.query.count(), series_list=Series.query.order_by(Series.id).all(),
        subjects=Subject.query.count(), contents=Content.query.count(), users=User.query.count(),
        students_count=len(students), activities=len(all_activities), experiments=Experiment.query.count(),
        recent_contents=recent_contents, recent_attempts=recent_attempts,
        total_attempts=total_attempts, average_score=average_score, unread_notifications=unread_notifications,
        risk_students=risk_students, activity_stats=activity_stats, deadline_activities=deadline_activities,
        overall_completion=overall_completion, recent_activity_count=recent_activity_count, now=now, smart_alerts=smart_alerts, alert_counts=alert_counts,
        series_indicators=series_indicators, inactive_students=inactive_students, pending_activity_stats=pending_activity_stats)



@admin_bp.route('/alertas', methods=['GET', 'POST'])
def alerts():
    if request.method == 'POST':
        alert_id = request.form.get('alert_id', type=int)
        alert = db.session.get(Alert, alert_id) if alert_id else None
        if not alert or alert.resolved:
            abort(404)
        alert.resolved = True
        alert.resolved_at = utcnow()
        db.session.commit()
        flash('Alerta marcado como resolvido.', 'success')
        return redirect(url_for('admin.alerts'))
    students = User.query.filter_by(role='student').order_by(User.name).all()
    activities = Activity.query.order_by(Activity.due_at.asc().nullslast(), Activity.id.desc()).all()
    attempts = ActivityAttempt.query.order_by(ActivityAttempt.created_at.desc()).all()
    now = utcnow()
    _sync_smart_alerts(students, activities, attempts, now)
    all_alerts = _visible_active_alerts()
    all_alerts.sort(key=lambda alert: (0 if alert.priority == 'high' else 1, -(alert.created_at.timestamp() if alert.created_at else 0)))
    return render_template('admin/alerts.html', alerts=all_alerts, now=now)


@admin_bp.route('/series', methods=['GET','POST'])
def series_list():
    if request.method == 'POST':
        name=request.form.get('name','').strip()
        if not name: flash('Informe o nome da série.','error')
        elif Series.query.filter_by(name=name).first(): flash('Série já existe.','error')
        else: db.session.add(Series(name=name)); db.session.commit(); flash('Série criada.','success')
    return render_template('admin/series.html', series=Series.query.order_by(Series.id).all())

@admin_bp.route('/series/<int:id>/editar', methods=['GET','POST'])
def series_edit(id):
    s=Series.query.get_or_404(id)
    if request.method=='POST':
        n=request.form.get('name','').strip(); other=Series.query.filter(Series.name==n,Series.id!=id).first()
        if not n or other: flash('Nome inválido ou já utilizado.','error')
        else: s.name=n; db.session.commit(); flash('Série atualizada.','success'); return redirect(url_for('admin.series_list'))
    return render_template('admin/edit_simple.html',title='Editar série',value=s.name,action=url_for('admin.series_edit',id=id))

@admin_bp.post('/series/<int:id>/excluir')
def series_delete(id):
    s=Series.query.get_or_404(id); db.session.delete(s); db.session.commit(); flash('Série excluída.','success'); return redirect(url_for('admin.series_list'))

@admin_bp.route('/subjects', methods=['GET','POST'])
def subjects_list():
    if request.method=='POST':
        name=request.form.get('name','').strip(); sid=request.form.get('series_id',''); s=db.session.get(Series, int(sid)) if sid.isdigit() else None
        if not name or not s: flash('Informe matéria e série.','error')
        elif Subject.query.filter_by(name=name,series_id=s.id).first(): flash('Matéria já existe nessa série.','error')
        else: db.session.add(Subject(name=name,series_id=s.id)); db.session.commit(); flash('Matéria criada.','success')
    return render_template('admin/subjects.html',subjects=Subject.query.order_by(Subject.id).all(),series=Series.query.order_by(Series.id).all())

@admin_bp.route('/subjects/<int:id>/editar', methods=['GET','POST'])
def subject_edit(id):
    s=Subject.query.get_or_404(id)
    if request.method=='POST':
        n=request.form.get('name','').strip(); sid=request.form.get('series_id',''); series=db.session.get(Series, int(sid)) if sid.isdigit() else None
        dup=Subject.query.filter(Subject.name==n,Subject.series_id==series.id,Subject.id!=id).first() if series else None
        if not n or not series or dup: flash('Dados inválidos ou duplicados.','error')
        else: s.name=n; s.series_id=series.id; db.session.commit(); flash('Matéria atualizada.','success'); return redirect(url_for('admin.subjects_list'))
    return render_template('admin/subject_edit.html',subject=s,series=Series.query.all())

@admin_bp.post('/subjects/<int:id>/excluir')
def subject_delete(id):
    s=Subject.query.get_or_404(id); db.session.delete(s); db.session.commit(); flash('Matéria excluída.','success'); return redirect(url_for('admin.subjects_list'))

def _record_content_history(content, action):
    db.session.add(ContentHistory(content_id=content.id, action=action, actor_id=current_user.id))


def _record_activity_history(activity, action):
    db.session.add(ActivityHistory(activity_id=activity.id, action=action, actor_id=current_user.id))


def _copy_storage_key(key):
    """Copia um arquivo armazenado sem compartilhar a mesma chave entre materiais."""
    if not key:
        return None
    if b2_enabled():
        obj = get_file(key)
        if not obj:
            raise RuntimeError('Não foi possível ler o arquivo original para duplicação.')
        data = obj['Body'].read()
    else:
        path = Path(current_app.config['UPLOAD_FOLDER']) / key
        if not path.exists():
            raise RuntimeError('O arquivo original não foi encontrado para duplicação.')
        data = path.read_bytes()
    suffix = Path(key).suffix or '.bin'
    filename = f'copia-{uuid.uuid4().hex}{suffix}'
    fs = FileStorage(stream=io.BytesIO(data), filename=filename, content_type=mimetypes.guess_type(filename)[0])
    return storage_upload(fs, filename, fs.content_type)


def _clone_content_files(content):
    new_file = None
    new_preview = []
    try:
        if content.file_name:
            new_file = _copy_storage_key(content.file_name)
        for key in _preview_files(content):
            copied = _copy_storage_key(key)
            if copied:
                new_preview.append(copied)
        return new_file, new_preview
    except Exception:
        if new_file:
            storage_delete(new_file)
        for key in new_preview:
            storage_delete(key)
        raise


@admin_bp.get('/contents')
def contents():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    status = request.args.get('status', 'active').strip().lower()
    query = Content.query
    if status == 'archived':
        query = query.filter(Content.archived_at.isnot(None))
    elif status != 'all':
        status = 'active'
        query = query.filter(Content.archived_at.is_(None))
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
    if kind in ALLOWED_KINDS:
        query = query.filter_by(kind=kind)
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    contents = query.order_by(Content.id.desc()).all()
    return render_template('admin/contents.html', contents=contents, q=q, kind=kind, series_id=series_id, subject_id=subject_id,
                           series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all(), status=status)
@admin_bp.get('/series/<int:id>')
def series_detail(id): return render_template('admin/series_detail.html',series=Series.query.get_or_404(id))

def _preview_files(content):
    try:
        data = json.loads(content.preview_manifest or '[]')
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []

def _load_stored_bytes(key):
    if b2_enabled():
        obj = get_file(key)
        if not obj:
            raise RuntimeError('Não foi possível ler o arquivo enviado.')
        return obj['Body'].read()
    path = Path(current_app.config['UPLOAD_FOLDER']) / key
    if not path.exists():
        raise RuntimeError('O arquivo enviado não foi encontrado no armazenamento.')
    return path.read_bytes()

def _build_pptx_preview(key):
    images = convert_pptx_to_images(_load_stored_bytes(key))
    keys = []
    try:
        for slide_name, data in images:
            upload_name = f'{Path(key).stem}-{slide_name}'
            slide_key = storage_upload(io.BytesIO(data), upload_name, 'image/png')
            keys.append(slide_key)
        return keys
    except Exception:
        for slide_key in keys:
            storage_delete(slide_key)
        raise

def content_form(content=None):
    series = Series.query.order_by(Series.id).all()
    subjects = Subject.query.order_by(Subject.id).all()
    if request.method != 'POST':
        return content, series, subjects

    sid = request.form.get('series_id', '').strip()
    subid = request.form.get('subject_id', '').strip()
    kind = request.form.get('kind', '').strip()
    title = request.form.get('title', '').strip()
    desc = request.form.get('description', '').strip()
    body = request.form.get('body', '').strip()
    external_url = request.form.get('external_url', '').strip()
    publication_status = request.form.get('status', 'published').strip().lower()
    if publication_status not in {'draft', 'published', 'scheduled'}:
        publication_status = 'published'
    scheduled_at = None
    if publication_status == 'scheduled':
        raw_scheduled = request.form.get('scheduled_at', '').strip()
        if not raw_scheduled:
            flash('Informe a data e hora da publicação programada.', 'error')
            return None, series, subjects
        try:
            scheduled_at = datetime.fromisoformat(raw_scheduled)
        except ValueError:
            flash('A data da publicação programada é inválida.', 'error')
            return None, series, subjects
        if scheduled_at <= utcnow():
            flash('A publicação programada deve ocorrer no futuro.', 'error')
            return None, series, subjects

    s = db.session.get(Series, int(sid)) if sid.isdigit() else None
    sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
    if not title or len(title) > 200 or len(desc) > 5000 or not s or not sub or sub.series_id != s.id or kind not in ALLOWED_KINDS:
        flash('Preencha os dados obrigatórios corretamente.', 'error')
        return None, series, subjects

    if kind in {'slide', 'video', 'link'} and not valid_url(external_url):
        flash('Informe um link HTTP/HTTPS válido.', 'error')
        return None, series, subjects
    if kind == 'explanation' and not body:
        flash('Escreva o conteúdo da explicação.', 'error')
        return None, series, subjects

    old_file = content.file_name if content else None
    old_preview = _preview_files(content) if content else []
    new_file = old_file
    new_preview = old_preview
    if kind in {'file', 'pdf'}:
        uploaded, original = save_uploaded_file(
            request.files.get('file') or request.files.get('pdf'),
            'pdf' if kind == 'pdf' else None
        )
        if uploaded is False:
            flash('Para PDF envie .pdf.' if kind == 'pdf' else 'Esse tipo de arquivo não é permitido.', 'error')
            return None, series, subjects
        if uploaded == 'invalid_mime':
            flash('O tipo MIME do arquivo não corresponde à extensão informada.', 'error')
            return None, series, subjects
        if uploaded == 'invalid_signature':
            flash('O conteúdo do arquivo não corresponde ao tipo informado. Escolha um arquivo válido.', 'error')
            return None, series, subjects
        if uploaded == 'too_large':
            flash('Arquivo muito grande. Limite: 25 MB.', 'error')
            return None, series, subjects
        if isinstance(uploaded, StorageError):
            flash(uploaded.message, 'error')
            return None, series, subjects
        if uploaded == 'storage_error':
            flash('Não foi possível enviar o arquivo para o armazenamento. Verifique a configuração do Backblaze B2 no servidor.', 'error')
            return None, series, subjects
        if uploaded:
            new_file = uploaded
            desc = desc or f'Material enviado: {original}'
            if Path(original).suffix.lower() == '.pptx':
                try:
                    new_preview = _build_pptx_preview(new_file)
                except Exception as exc:
                    storage_delete(new_file)
                    current_app.logger.exception('Falha ao gerar pré-visualização do PPTX')
                    flash(f'Não foi possível preparar a visualização do PowerPoint: {exc}', 'error')
                    return None, series, subjects
            else:
                new_preview = []
        elif not old_file:
            flash('Escolha um arquivo do seu dispositivo.', 'error')
            return None, series, subjects
    else:
        new_file = None
        new_preview = []

    if content is None:
        content = Content()

    content.title = title
    content.description = desc
    content.kind = kind
    content.body = body if kind == 'explanation' else None
    content.external_url = external_url if kind in {'slide', 'video', 'link'} else None
    content.file_name = new_file
    content.preview_manifest = json.dumps(new_preview, ensure_ascii=False) if new_preview else None
    content.series_id = s.id
    content.subject_id = sub.id
    content.status = publication_status
    content.scheduled_at = scheduled_at
    content.published_at = utcnow() if publication_status == 'published' else None
    db.session.add(content)
    db.session.commit()

    if old_file and old_file != new_file:
        storage_delete(old_file)
    if old_preview and old_preview != new_preview:
        for key in old_preview:
            if key not in new_preview:
                storage_delete(key)
    return content, None, None

@admin_bp.route('/contents/new', methods=['GET', 'POST'])
def content_new():
    content, series, subjects = content_form()
    if request.method == 'POST' and content:
        if content.status == 'published':
            notify_students(f'Novo material: {content.title}', url_for('student.content', id=content.id), category='materials')
        _record_content_history(content, 'criado')
        db.session.commit()
        message = 'Material publicado com sucesso.' if content.status == 'published' else ('Material programado com sucesso.' if content.status == 'scheduled' else 'Material salvo como rascunho.')
        flash(message, 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=None, series=series, subjects=subjects)

@admin_bp.route('/contents/<int:id>/edit', methods=['GET', 'POST'])
def content_edit(id):
    content = Content.query.get_or_404(id)
    previous_status = content.status or 'published'
    result, series, subjects = content_form(content)
    if request.method == 'POST' and result:
        action = 'publicada' if content.status == 'published' and previous_status != 'published' else ('programada' if content.status == 'scheduled' else ('rascunho_salvo' if content.status == 'draft' else 'editado'))
        _record_content_history(content, action)
        if content.status == 'published' and previous_status != 'published':
            notify_students(f'Novo material publicado: {content.title}', url_for('student.content', id=content.id), category='materials')
        db.session.commit()
        message = 'Material publicado.' if content.status == 'published' and previous_status != 'published' else ('Material programado.' if content.status == 'scheduled' else ('Rascunho salvo.' if content.status == 'draft' else 'Material atualizado.'))
        flash(message, 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=content, series=series, subjects=subjects)

@admin_bp.post('/contents/<int:id>/duplicate')
def content_duplicate(id):
    source = Content.query.get_or_404(id)
    try:
        new_file, new_preview = _clone_content_files(source)
    except Exception as exc:
        current_app.logger.exception('Falha ao duplicar material')
        flash(f'Não foi possível duplicar o material: {exc}', 'error')
        return redirect(url_for('admin.contents'))
    duplicate = Content(
        title=f'{source.title} (cópia)', description=source.description, kind=source.kind,
        body=source.body, external_url=source.external_url, file_name=new_file,
        preview_manifest=json.dumps(new_preview, ensure_ascii=False) if new_preview else None,
        series_id=source.series_id, subject_id=source.subject_id, archived_at=None,
        status='draft', published_at=None, scheduled_at=None,
    )
    db.session.add(duplicate)
    db.session.flush()
    _record_content_history(duplicate, 'duplicado')
    db.session.commit()
    flash('Material duplicado. A cópia foi criada como conteúdo ativo.', 'success')
    return redirect(url_for('admin.contents'))


@admin_bp.post('/contents/<int:id>/archive')
def content_archive(id):
    content = Content.query.get_or_404(id)
    if content.archived_at is None:
        content.archived_at = utcnow()
        _record_content_history(content, 'arquivado')
        db.session.commit()
    flash('Material arquivado. Ele não aparece mais para os alunos.', 'success')
    return redirect(url_for('admin.contents'))


@admin_bp.post('/contents/<int:id>/restore')
def content_restore(id):
    content = Content.query.get_or_404(id)
    if content.archived_at is not None:
        content.archived_at = None
        _record_content_history(content, 'restaurado')
        db.session.commit()
    flash('Material restaurado e novamente disponível para os alunos.', 'success')
    return redirect(url_for('admin.contents', status='archived'))


@admin_bp.get('/contents/<int:id>/preview')
def content_preview(id):
    content = Content.query.get_or_404(id)
    slides = _preview_files(content) if content.kind == 'file' else []
    return render_template('admin/content_preview.html', content=content, slides=slides)


@admin_bp.get('/contents/<int:id>/preview/slide/<int:slide>')
def content_preview_slide(id, slide):
    content = Content.query.get_or_404(id)
    slides = _preview_files(content)
    if content.kind != 'file' or slide < 0 or slide >= len(slides):
        abort(404)
    key = slides[slide]
    if not isinstance(key, str) or not key:
        abort(404)
    try:
        data = _load_stored_bytes(key)
    except Exception as exc:
        current_app.logger.warning('Falha ao abrir slide de pré-visualização: %s | %s', key, exc)
        return render_template('error.html', message='Não foi possível abrir a pré-visualização deste slide.', error_title='Pré-visualização indisponível', back_url=url_for('admin.content_preview', id=content.id)), 502
    return Response(data, mimetype='image/png', headers={
        'Cache-Control': 'private, max-age=3600',
        'X-Content-Type-Options': 'nosniff',
    })


@admin_bp.get('/contents/<int:id>/history')
def content_history(id):
    content = Content.query.get_or_404(id)
    history = ContentHistory.query.filter_by(content_id=content.id).order_by(ContentHistory.created_at.desc(), ContentHistory.id.desc()).all()
    return render_template('admin/content_history.html', content=content, history=history)


@admin_bp.post('/contents/<int:id>/delete')
def content_delete(id):
    content = Content.query.get_or_404(id)
    filename = content.file_name
    preview_files = _preview_files(content)
    _record_content_history(content, 'excluído')
    db.session.delete(content)
    db.session.commit()
    if filename:
        storage_delete(filename)
    for key in preview_files:
        if isinstance(key, str):
            storage_delete(key)
    flash('Conteúdo excluído.', 'success')
    return redirect(url_for('admin.contents'))

@admin_bp.get('/file/<path:filename>')
def file(filename):
    # Nunca permita que a URL seja usada como um navegador arbitrário do bucket.
    # O arquivo precisa estar associado a um material existente no banco.
    if not Content.query.filter_by(file_name=filename).first():
        abort(404)
    if b2_enabled():
        try:
            obj = get_file(filename)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir arquivo administrativo: %s | %s', exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o arquivo', back_url=url_for('admin.contents')), 502
        return safe_file_response(obj, filename, obj.get('ContentType') or 'application/octet-stream')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@admin_bp.get('/desempenho')
def performance():
    q = request.args.get('q', '').strip()
    student_query = User.query.filter_by(role='student')
    if q:
        like = f'%{q}%'
        student_query = student_query.filter(or_(User.name.ilike(like), User.email.ilike(like)))
    students = student_query.order_by(User.name.asc()).all()
    activities = Activity.query.order_by(Activity.id.desc()).all()
    activity_total = len(activities)
    rows = []
    all_latest_scores = []
    for student in students:
        attempts = ActivityAttempt.query.filter_by(user_id=student.id).order_by(ActivityAttempt.id.desc()).all()
        latest = {}
        best = {}
        for attempt in attempts:
            latest.setdefault(attempt.activity_id, attempt)
            if attempt.activity_id not in best or attempt.score > best[attempt.activity_id].score:
                best[attempt.activity_id] = attempt
        scores = [a.score for a in latest.values()]
        average = round(sum(scores) / len(scores), 1) if scores else None
        all_latest_scores.extend(scores)
        completed = len(latest)
        pending = max(activity_total - completed, 0)
        completion = round(completed / activity_total * 100) if activity_total else 0
        low_performance = average is not None and average < 6
        rows.append({
            'student': student, 'average': average, 'completed': completed,
            'pending': pending, 'completion': completion, 'attempts': len(attempts),
            'best': max(best.values(), key=lambda a: a.score, default=None),
            'low_performance': low_performance,
        })
    overall_average = round(sum(all_latest_scores) / len(all_latest_scores), 1) if all_latest_scores else None
    at_risk = sum(1 for row in rows if row['low_performance'] or (row['pending'] > 0 and activity_total > 0))
    return render_template('admin/performance.html', rows=rows, q=q, activity_total=activity_total, overall_average=overall_average, at_risk=at_risk)

@admin_bp.get('/users/<int:id>/desempenho')
def user_performance(id):
    student = User.query.filter_by(id=id, role='student').first_or_404()
    activities = Activity.query.order_by(Activity.id.desc()).all()
    attempts = ActivityAttempt.query.filter_by(user_id=student.id).order_by(ActivityAttempt.id.desc()).all()
    by_activity = {}
    for attempt in attempts:
        item = by_activity.setdefault(attempt.activity_id, {'latest': attempt, 'best': attempt, 'attempts': 0})
        item['attempts'] += 1
        if attempt.id > item['latest'].id:
            item['latest'] = attempt
        if attempt.score > item['best'].score:
            item['best'] = attempt
    latest_scores = [item['latest'].score for item in by_activity.values()]
    average = round(sum(latest_scores) / len(latest_scores), 1) if latest_scores else None
    completed = len(by_activity)
    pending = max(len(activities) - completed, 0)
    completion = round(completed / len(activities) * 100) if activities else 0
    return render_template('admin/user_performance.html', student=student, activities=activities, by_activity=by_activity, attempts=attempts, average=average, completed=completed, pending=pending, completion=completion, now=utcnow())

@admin_bp.get('/users')
def users():
    q = request.args.get('q', '').strip()
    role = request.args.get('role', '').strip().lower()
    sort = request.args.get('sort', 'recent').strip().lower()
    query = User.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like)))
    if role in {'student', 'admin'}:
        query = query.filter_by(role=role)
    ordering = User.name.asc() if sort == 'name' else User.id.desc()
    users_list = query.order_by(ordering).all()
    return render_template('admin/users.html', users=users_list, q=q, role=role, sort=sort)

@admin_bp.post('/users/<int:id>/delete')
def user_delete(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('Você não pode excluir sua própria conta.', 'error')
    elif user.is_admin:
        flash('A conta de administrador não pode ser excluída por este painel.', 'error')
    else:
        db.session.delete(user)
        db.session.commit()
        flash('Aluno excluído.', 'success')
    return redirect(url_for('admin.users'))

@admin_bp.route('/question-bank', methods=['GET', 'POST'])
def question_bank():
    series = Series.query.order_by(Series.id).all()
    subjects = Subject.query.order_by(Subject.name).all()
    categories = [row[0] for row in db.session.query(QuestionBank.category).filter(QuestionBank.category.isnot(None), QuestionBank.category != '').distinct().order_by(QuestionBank.category.asc()).all()]

    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        options = [request.form.get(f'option_{letter}', '').strip() for letter in ('a','b','c','d')]
        code = request.form.get('code', '').strip()
        correct_index = request.form.get('correct', '').strip()
        difficulty = normalize_difficulty(request.form.get('difficulty'))
        category = ' '.join(request.form.get('category', '').strip().split())[:100] or 'Geral'
        tags_raw = request.form.get('tags', '').strip()
        tags = ', '.join(dict.fromkeys(tag.strip().lower() for tag in tags_raw.split(',') if tag.strip()))[:1000]
        sid = request.form.get('series_id', '').strip(); subid = request.form.get('subject_id', '').strip()
        ser = db.session.get(Series, int(sid)) if sid.isdigit() else None
        sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
        if not question or len(question) > 1000 or sum(bool(x) for x in options) < 2 or correct_index not in {'0','1','2','3'}:
            flash('Preencha a questão, pelo menos duas alternativas e a resposta correta.', 'error')
        elif not ser or not sub or sub.series_id != ser.id:
            flash('Selecione uma série e matéria válidas.', 'error')
        elif not options[int(correct_index)]:
            flash('A resposta correta precisa estar preenchida.', 'error')
        elif any(options[i] and not options[i-1] for i in range(1,4)):
            flash('Preencha as alternativas em sequência.', 'error')
        else:
            item = QuestionBank(question=question, correct=options[int(correct_index)], code=code[:8000] or None,
                                difficulty=difficulty, category=category, tags=tags or None,
                                series_id=ser.id, subject_id=sub.id)
            item.set_options([x for x in options if x])
            db.session.add(item); db.session.commit()
            flash('Questão adicionada ao banco.', 'success')
            return redirect(url_for('admin.question_bank'))

    q = request.args.get('q', '').strip()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    difficulty = request.args.get('difficulty', '').strip().lower()
    category = request.args.get('category', '').strip()
    tag = request.args.get('tag', '').strip().lower()
    sort = request.args.get('sort', 'recent').strip().lower()

    query = QuestionBank.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(QuestionBank.question.ilike(like), QuestionBank.category.ilike(like), QuestionBank.tags.ilike(like), QuestionBank.code.ilike(like)))
    if series_id.isdigit():
        query = query.filter(QuestionBank.series_id == int(series_id))
    if subject_id.isdigit():
        query = query.filter(QuestionBank.subject_id == int(subject_id))
    if difficulty in DIFFICULTIES:
        query = query.filter(QuestionBank.difficulty == difficulty)
    if category:
        query = query.filter(QuestionBank.category == category)
    if tag:
        query = query.filter(QuestionBank.tags.ilike(f'%{tag}%'))

    if sort == 'oldest':
        query = query.order_by(QuestionBank.id.asc())
    elif sort == 'question':
        query = query.order_by(QuestionBank.question.asc(), QuestionBank.id.desc())
    elif sort == 'difficulty':
        query = query.order_by(QuestionBank.difficulty.asc(), QuestionBank.id.desc())
    elif sort == 'category':
        query = query.order_by(QuestionBank.category.asc(), QuestionBank.id.desc())
    else:
        sort = 'recent'
        query = query.order_by(QuestionBank.id.desc())

    items = query.all()
    from ..settings import get_bool
    return render_template('admin/question_bank.html', items=items, series=series, subjects=subjects,
                           categories=categories, q=q, series_id=series_id, subject_id=subject_id,
                           ai_question_gen_enabled=get_bool('ai_question_gen_enabled', False),
                           difficulty=difficulty, category=category, tag=tag, sort=sort)

@admin_bp.post('/question-bank/<int:id>/delete')
def question_bank_delete(id):
    item = QuestionBank.query.get_or_404(id)
    db.session.delete(item); db.session.commit()
    flash('Questão removida do banco.', 'success')
    return redirect(url_for('admin.question_bank'))

@admin_bp.post('/activities/<int:id>/import-questions')
def activity_import_questions(id):
    activity = Activity.query.get_or_404(id)
    ids = [int(x) for x in request.form.getlist('question_ids') if x.isdigit()]
    current = activity.get_questions()
    added = 0
    for qid in ids:
        if len(current) >= 20: break
        item = db.session.get(QuestionBank, qid)
        if not item or item.series_id != activity.series_id or item.subject_id != activity.subject_id: continue
        opts = item.get_options()
        current.append({'question': item.question, 'options': opts, 'correct': item.correct, 'kind': 'objective', **({'code': item.code} if item.code else {})})
        added += 1
    activity.set_questions(current); db.session.commit()
    if added:
        _record_activity_history(activity, 'questões adicionadas')
        db.session.commit()
    flash(f'{added} questão(ões) importada(s) para a atividade.', 'success' if added else 'error')
    return redirect(url_for('admin.activity_edit', id=id))

@admin_bp.route('/activities', methods=['GET', 'POST'])
def activities():
    q = request.args.get('q', '').strip()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    status = request.args.get('status', '').strip().lower()
    difficulty = request.args.get('difficulty', '').strip().lower()
    archive_status = request.args.get('archive_status', 'active').strip().lower()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        difficulty_value = normalize_difficulty(request.form.get('difficulty'))
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        due_raw = request.form.get('due_at', '').strip()
        s = db.session.get(Series, int(sid)) if sid.isdigit() else None
        sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
        due_at, due_error = parse_due_at(due_raw)
        if not title or len(title) > 200 or not s or not sub or sub.series_id != s.id:
            flash('Preencha título, série e matéria corretamente.', 'error')
        elif due_error:
            flash(due_error, 'error')
        else:
            a = Activity(title=title, description=description[:5000], series_id=s.id, subject_id=sub.id, due_at=due_at, difficulty=difficulty_value)
            a.set_questions([])
            db.session.add(a)
            db.session.commit()
            _record_activity_history(a, 'criada')
            db.session.commit()
            flash('Atividade criada. Agora adicione as questões.', 'success')
            return redirect(url_for('admin.activity_edit', id=a.id))
    query = Activity.query
    if archive_status == 'archived':
        query = query.filter(Activity.archived_at.isnot(None))
    elif archive_status != 'all':
        archive_status = 'active'
        query = query.filter(Activity.archived_at.is_(None))
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Activity.title.ilike(like), Activity.description.ilike(like)))
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    if difficulty in DIFFICULTIES: query = query.filter_by(difficulty=difficulty)
    now = utcnow()
    items = query.order_by(Activity.id.desc()).all()
    if status == 'pending': items = [a for a in items if not a.due_at or a.due_at >= now]
    elif status == 'expired': items = [a for a in items if a.due_at and a.due_at < now]
    return render_template('admin/activities.html', activities=items, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all(), q=q, series_id=series_id, subject_id=subject_id, status=status, difficulty=difficulty, archive_status=archive_status, now=now)


@admin_bp.route('/activities/prontas', methods=['GET', 'POST'])
def ready_activities():
    """Catálogo de atividades prontas, organizado por conteúdo."""
    difficulty = request.args.get('difficulty', '').strip().lower()
    topic = request.args.get('topic', '').strip().lower()
    selected = [item for item in PREBUILT_ACTIVITIES if not difficulty or item['difficulty'] == difficulty]

    def activity_topic(item):
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        if 'análise de código' in text or 'analise de codigo' in text or 'análise de código' in text:
            return 'Análise de Código'
        if 'funç' in text or 'funcoes' in text:
            return 'Funções'
        if 'laço' in text or 'laços' in text or 'repetição' in text or 'repeticao' in text:
            return 'Estruturas de Repetição'
        if 'condicional' in text or 'condicionais' in text:
            return 'Estruturas Condicionais'
        return 'Outros'

    topic_map = {
        'condicionais': 'Estruturas Condicionais',
        'repeticao': 'Estruturas de Repetição',
        'funcoes': 'Funções',
        'codigo': 'Análise de Código',
        'outros': 'Outros',
    }
    selected = [item for item in selected if not topic or activity_topic(item) == topic_map.get(topic, topic)]
    grouped = {}
    for item in selected:
        grouped.setdefault(activity_topic(item), []).append(item)
    order = ['Estruturas Condicionais', 'Estruturas de Repetição', 'Funções', 'Análise de Código', 'Outros']
    grouped = {name: grouped[name] for name in order if name in grouped}
    code_questions = sum(1 for item in selected for q in item.get('questions', []) if q.get('code'))
    total_questions = sum(len(item.get('questions', [])) for item in selected)
    series = Series.query.order_by(Series.id).all()
    subjects = Subject.query.order_by(Subject.name).all()
    if request.method == 'POST':
        slug = request.form.get('template_slug', '').strip()
        item = BY_SLUG.get(slug)
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        due_raw = request.form.get('due_at', '').strip()
        ser = db.session.get(Series, int(sid)) if sid.isdigit() else None
        sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
        due_at, due_error = parse_due_at(due_raw)
        if not item:
            flash('Selecione uma atividade pronta válida.', 'error')
        elif not ser or not sub or sub.series_id != ser.id:
            flash('Selecione uma série e matéria válidas.', 'error')
        elif due_error:
            flash(due_error, 'error')
        else:
            activity = Activity(
                title=item['title'], description=item['description'],
                series_id=ser.id, subject_id=sub.id, due_at=due_at,
                difficulty=item['difficulty'],
            )
            activity.set_questions([dict(q) for q in item['questions']])
            db.session.add(activity); db.session.commit()
            _record_activity_history(activity, 'criada')
            db.session.commit()
            notify_students(f'Nova atividade: {activity.title}', url_for('student.activity', id=activity.id), category='activities')
            db.session.commit()
            flash(f'Atividade pronta "{activity.title}" adicionada com {len(item["questions"])} questões.', 'success')
            return redirect(url_for('admin.activity_edit', id=activity.id))
    return render_template('admin/ready_activities.html', templates=selected, grouped_templates=grouped, all_templates=PREBUILT_ACTIVITIES, series=series, subjects=subjects, difficulty=difficulty, topic=topic, code_questions=code_questions, total_questions=total_questions)


def parse_due_at(value):
    if not value:
        return None, None
    try:
        due = datetime.fromisoformat(value)
    except ValueError:
        return None, 'O prazo informado é inválido.'
    if due <= utcnow():
        return None, 'O prazo precisa ser uma data e hora futuras.'
    return due, None


def read_activity_questions_from_form():
    questions = []
    for i in range(20):
        kind = request.form.get(f'kind_{i}', 'objective').strip().lower()
        kind = 'essay' if kind == 'essay' else 'objective'
        question = request.form.get(f'question_{i}', '').strip()
        options = [request.form.get(f'option_{i}_{letter}', '').strip() for letter in ('a', 'b', 'c', 'd')]
        correct_index = request.form.get(f'correct_{i}', '').strip()
        if not question and not any(options) and not correct_index:
            continue
        if len(question) > 1000:
            return None, 'Cada questão pode ter no máximo 1000 caracteres.'
        if kind == 'essay':
            if not question:
                return None, 'Cada questão discursiva precisa de enunciado.'
            questions.append({'question': question, 'options': [], 'correct': '', 'kind': 'essay'})
            continue
        if not question or len(question) > 1000 or sum(bool(option) for option in options) < 2 or correct_index not in {'0', '1', '2', '3'}:
            return None, 'Cada questão objetiva preenchida precisa de enunciado, pelo menos duas alternativas e uma resposta correta.'
        if any(len(option) > 500 for option in options):
            return None, 'Cada alternativa pode ter no máximo 500 caracteres.'
        filled = [bool(option) for option in options]
        if any(filled[i] and not filled[i - 1] for i in range(1, 4)):
            return None, 'Preencha as alternativas em sequência, sem deixar espaços vazios entre elas.'
        index = int(correct_index)
        if not options[index]:
            return None, 'A resposta correta precisa apontar para uma alternativa preenchida.'
        questions.append({'question': question, 'options': options, 'correct': options[index], 'kind': 'objective'})
    if not questions:
        return None, 'Informe pelo menos uma questão.'
    return questions, None

@admin_bp.route('/activities/<int:id>/editar', methods=['GET', 'POST'])
def activity_edit(id):
    a = Activity.query.get_or_404(id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        desc = request.form.get('description', '').strip()
        due_raw = request.form.get('due_at', '').strip()
        difficulty_value = request.form.get('difficulty', a.difficulty or 'medio').strip().lower()
        max_attempts_raw = request.form.get('max_attempts', '0').strip()
        try:
            max_attempts = max(0, min(int(max_attempts_raw or 0), 20))
        except ValueError:
            max_attempts = 0
        allow_review = request.form.get('allow_review') == 'on'
        shuffle_questions = request.form.get('shuffle_questions') == 'on'
        shuffle_options = request.form.get('shuffle_options') == 'on'
        due_at, due_error = parse_due_at(due_raw)
        questions, error = read_activity_questions_from_form()
        if not title or len(title) > 200:
            flash('Informe um título entre 1 e 200 caracteres.', 'error')
        elif due_error:
            flash(due_error, 'error')
        elif error:
            flash(error, 'error')
        else:
            was_empty = len(a.get_questions()) == 0
            a.title = title
            a.description = desc[:5000]
            a.due_at = due_at
            a.difficulty = difficulty_value if difficulty_value in {'facil','medio','dificil'} else 'medio'
            a.max_attempts = max_attempts
            a.allow_review = allow_review
            a.shuffle_questions = shuffle_questions
            a.shuffle_options = shuffle_options
            a.set_questions(questions)
            db.session.commit()
            _record_activity_history(a, 'editada')
            db.session.commit()
            if was_empty:
                notify_students(f'Nova atividade: {a.title}', url_for('student.activity', id=a.id), category='activities')
                db.session.commit()
            flash('Atividade salva.', 'success')
            return redirect(url_for('admin.activities'))
        questions = []
        for i in range(20):
            q = request.form.get(f'question_{i}', '').strip()
            opts = [request.form.get(f'option_{i}_{letter}', '').strip() for letter in ('a', 'b', 'c', 'd')]
            correct = request.form.get(f'correct_{i}', '').strip()
            kind = 'essay' if request.form.get(f'kind_{i}', 'objective').strip().lower() == 'essay' else 'objective'
            questions.append({'question': q, 'options': [] if kind == 'essay' else opts, 'correct': '' if kind == 'essay' else (opts[int(correct)] if correct.isdigit() and int(correct) < len(opts) else ''), 'kind': kind})
        while questions and not questions[-1].get('question') and not any(questions[-1].get('options', [])):
            questions.pop()
        return render_template('admin/activity_form.html', activity=a, questions=questions, due_raw=due_raw, bank_items=QuestionBank.query.filter_by(series_id=a.series_id, subject_id=a.subject_id).order_by(QuestionBank.id.desc()).all())
    due_raw = a.due_at.strftime('%Y-%m-%dT%H:%M') if a.due_at else ''
    return render_template('admin/activity_form.html', activity=a, questions=a.get_questions(), due_raw=due_raw, bank_items=QuestionBank.query.filter_by(series_id=a.series_id, subject_id=a.subject_id).order_by(QuestionBank.id.desc()).all())

@admin_bp.post('/activities/<int:id>/duplicate')
def activity_duplicate(id):
    source = Activity.query.get_or_404(id)
    duplicate = Activity(
        title=f'{source.title} (cópia)', description=source.description,
        series_id=source.series_id, subject_id=source.subject_id,
        questions_json=source.questions_json, due_at=None,
        difficulty=source.difficulty, max_attempts=source.max_attempts,
        allow_review=source.allow_review, shuffle_questions=source.shuffle_questions,
        shuffle_options=source.shuffle_options, archived_at=None,
    )
    db.session.add(duplicate)
    db.session.flush()
    _record_activity_history(duplicate, 'duplicada')
    db.session.commit()
    flash('Atividade duplicada. A cópia foi criada sem prazo de entrega.', 'success')
    return redirect(url_for('admin.activities'))


@admin_bp.post('/activities/<int:id>/archive')
def activity_archive(id):
    activity = Activity.query.get_or_404(id)
    if activity.archived_at is None:
        activity.archived_at = utcnow()
        _record_activity_history(activity, 'arquivada')
        db.session.commit()
    flash('Atividade arquivada. Ela não aparece mais para os alunos.', 'success')
    return redirect(url_for('admin.activities'))


@admin_bp.post('/activities/<int:id>/restore')
def activity_restore(id):
    activity = Activity.query.get_or_404(id)
    if activity.archived_at is not None:
        activity.archived_at = None
        _record_activity_history(activity, 'restaurada')
        db.session.commit()
    flash('Atividade restaurada e novamente disponível para os alunos.', 'success')
    return redirect(url_for('admin.activities', archive_status='archived'))


@admin_bp.get('/activities/<int:id>/history')
def activity_history(id):
    activity = Activity.query.get_or_404(id)
    history = ActivityHistory.query.filter_by(activity_id=activity.id).order_by(ActivityHistory.created_at.desc(), ActivityHistory.id.desc()).all()
    return render_template('admin/activity_history.html', activity=activity, history=history)


@admin_bp.post('/activities/<int:id>/delete')
def activity_delete(id):
    a = Activity.query.get_or_404(id)
    _record_activity_history(a, 'excluída')
    db.session.delete(a)
    db.session.commit()
    flash('Atividade excluída.', 'success')
    return redirect(url_for('admin.activities'))

def _learning_path_item_target(item):
    if item.item_type == 'content':
        return Content.query.get(item.target_id)
    if item.item_type == 'activity':
        return Activity.query.get(item.target_id)
    return Experiment.query.get(item.target_id)


def _validate_learning_path_targets(path, series_id, subject_id):
    """Retorna os títulos das etapas incompatíveis com a nova série/matéria."""
    incompatible = []
    for existing in path.items:
        target = _learning_path_item_target(existing)
        if target is None or target.series_id != series_id or (
            subject_id is not None and getattr(target, 'subject_id', None) != subject_id
        ):
            incompatible.append(existing.title)
    return incompatible


def _learning_path_order_is_valid(items):
    ordered = sorted(items, key=lambda row: row.position)
    positions = {row.id: index for index, row in enumerate(ordered)}
    return all(
        not row.prerequisite_id or positions.get(row.prerequisite_id, -1) < positions[row.id]
        for row in ordered
    )


@admin_bp.route('/trilhas', methods=['GET', 'POST'])
def learning_paths():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        sid = request.form.get('series_id', '').strip()
        subject_id = request.form.get('subject_id', '').strip()
        series = db.session.get(Series, int(sid)) if sid.isdigit() else None
        subject = db.session.get(Subject, int(subject_id)) if subject_id.isdigit() else None
        if not title or not series or (subject and subject.series_id != series.id):
            flash('Informe um título e uma série válidos. A matéria deve pertencer à série.', 'error')
        else:
            path = LearningPath(title=title, description=request.form.get('description', '').strip(), series_id=series.id, subject_id=subject.id if subject else None, active=request.form.get('active') == '1')
            db.session.add(path); db.session.commit()
            flash('Trilha criada.', 'success')
            return redirect(url_for('admin.learning_path_edit', id=path.id))
    return render_template('admin/learning_paths.html', paths=LearningPath.query.order_by(LearningPath.id.desc()).all(), series=Series.query.order_by(Series.id).all())

@admin_bp.route('/trilhas/<int:id>/editar', methods=['GET', 'POST'])
def learning_path_edit(id):
    path = LearningPath.query.get_or_404(id)
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'save':
            title = request.form.get('title', '').strip()
            sid = request.form.get('series_id', '').strip()
            subject_id = request.form.get('subject_id', '').strip()
            series = db.session.get(Series, int(sid)) if sid.isdigit() else None
            subject = db.session.get(Subject, int(subject_id)) if subject_id.isdigit() else None
            if not title or not series or (subject and subject.series_id != series.id):
                flash('Dados inválidos.', 'error')
            else:
                new_series_id = series.id
                new_subject_id = subject.id if subject else None
                incompatible = _validate_learning_path_targets(path, new_series_id, new_subject_id)
                if incompatible:
                    flash(
                        'Não foi possível alterar a série/matéria porque estas etapas ficariam incompatíveis: '
                        + ', '.join(incompatible) + '.',
                        'error'
                    )
                else:
                    path.title=title; path.description=request.form.get('description','').strip(); path.series_id=new_series_id; path.subject_id=new_subject_id; path.active=request.form.get('active') == '1'
                    db.session.commit(); flash('Trilha atualizada.', 'success')
        elif action == 'item':
            item_type=request.form.get('item_type','content').strip().lower()
            target=request.form.get('target_id','').strip()
            title=request.form.get('item_title','').strip()
            rule=request.form.get('completion_rule','access').strip().lower()
            prerequisite=request.form.get('prerequisite_id','').strip()
            if item_type not in {'content','activity','experiment'} or not target.isdigit() or not title:
                flash('Informe item, título e tipo válidos.', 'error')
            else:
                target_id=int(target)
                valid = (Content.query.filter_by(id=target_id, series_id=path.series_id).first() if item_type=='content' else Activity.query.filter_by(id=target_id, series_id=path.series_id).first() if item_type=='activity' else Experiment.query.filter_by(id=target_id, series_id=path.series_id).first())
                if valid and path.subject_id and getattr(valid, 'subject_id', None) != path.subject_id:
                    valid = None
                if not valid:
                    flash('O item escolhido não pertence à série/matéria da trilha.', 'error')
                else:
                    pre=LearningPathItem.query.filter_by(id=int(prerequisite)).first() if prerequisite.isdigit() else None
                    position=(db.session.query(db.func.max(LearningPathItem.position)).filter_by(path_id=path.id).scalar() or -1)+1
                    db.session.add(LearningPathItem(path_id=path.id,title=title,item_type=item_type,target_id=target_id,position=position,prerequisite_id=pre.id if pre and pre.path_id == path.id else None,completion_rule=rule if rule in {'access','complete'} else 'access'))
                    db.session.commit(); flash('Item adicionado à trilha.', 'success')
        elif action == 'delete_item':
            item=LearningPathItem.query.filter_by(id=int(request.form.get('item_id','0') or 0),path_id=path.id).first_or_404(); db.session.delete(item); db.session.commit(); flash('Item removido.', 'success')
        return redirect(url_for('admin.learning_path_edit', id=path.id))
    content_query = Content.query.filter_by(series_id=path.series_id)
    activity_query = Activity.query.filter_by(series_id=path.series_id)
    experiment_query = Experiment.query.filter_by(series_id=path.series_id)
    if path.subject_id:
        content_query = content_query.filter_by(subject_id=path.subject_id)
        activity_query = activity_query.filter_by(subject_id=path.subject_id)
        experiment_query = experiment_query.filter_by(subject_id=path.subject_id)
    return render_template('admin/learning_path_form.html', path=path, series=Series.query.order_by(Series.id).all(), contents=content_query.order_by(Content.title).all(), activities=activity_query.order_by(Activity.title).all(), experiments=experiment_query.order_by(Experiment.title).all())

@admin_bp.post('/trilhas/<int:id>/itens/<int:item_id>/mover')
def learning_path_item_move(id, item_id):
    path = LearningPath.query.get_or_404(id)
    item = LearningPathItem.query.filter_by(id=item_id, path_id=path.id).first_or_404()
    direction = request.form.get('direction', '').strip()
    if direction not in {'up', 'down'}:
        flash('Movimento inválido.', 'error')
        return redirect(url_for('admin.learning_path_edit', id=path.id))
    items = list(path.items)
    index = next((i for i, row in enumerate(items) if row.id == item.id), None)
    target_index = index - 1 if direction == 'up' else index + 1
    if index is None or target_index < 0 or target_index >= len(items):
        return redirect(url_for('admin.learning_path_edit', id=path.id))
    other = items[target_index]
    item.position, other.position = other.position, item.position
    if not _learning_path_order_is_valid(items):
        db.session.rollback()
        flash('A etapa não pode ficar antes do seu pré-requisito.', 'error')
        return redirect(url_for('admin.learning_path_edit', id=path.id))
    db.session.commit()
    flash('Sequência da trilha atualizada.', 'success')
    return redirect(url_for('admin.learning_path_edit', id=path.id))

@admin_bp.post('/trilhas/<int:id>/excluir')
def learning_path_delete(id):
    path=LearningPath.query.get_or_404(id); db.session.delete(path); db.session.commit(); flash('Trilha excluída.', 'success'); return redirect(url_for('admin.learning_paths'))

@admin_bp.route('/experiments', methods=['GET', 'POST'])
def experiments():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        s = db.session.get(Series, int(sid)) if sid.isdigit() else None
        sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
        if not title or not s or not sub or sub.series_id != s.id:
            flash('Informe título, série e matéria.', 'error')
        else:
            e = Experiment(title=title, description=request.form.get('description', '').strip(), objective=request.form.get('objective', '').strip(), materials=request.form.get('materials', '').strip(), steps=request.form.get('steps', '').strip(), safety=request.form.get('safety', '').strip(), conclusion=request.form.get('conclusion', '').strip(), series_id=s.id, subject_id=sub.id)
            db.session.add(e)
            db.session.commit()
            notify_students(f'Novo experimento: {e.title}', url_for('student.experiment', id=e.id), category='materials')
            db.session.commit()
            flash('Experimento publicado.', 'success')
            return redirect(url_for('admin.experiments'))
    return render_template('admin/experiments.html', experiments=Experiment.query.order_by(Experiment.id.desc()).all(), series=Series.query.all())

@admin_bp.route('/experiments/<int:id>/editar', methods=['GET', 'POST'])
def experiment_edit(id):
    e = Experiment.query.get_or_404(id)
    if request.method == 'POST':
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        s = db.session.get(Series, int(sid)) if sid.isdigit() else None
        sub = db.session.get(Subject, int(subid)) if subid.isdigit() else None
        if not request.form.get('title', '').strip() or not s or not sub or sub.series_id != s.id:
            flash('Dados inválidos.', 'error')
        else:
            for f in ['title', 'description', 'objective', 'materials', 'steps', 'safety', 'conclusion']:
                setattr(e, f, request.form.get(f, '').strip())
            e.series_id = s.id
            e.subject_id = sub.id
            db.session.commit()
            flash('Experimento atualizado.', 'success')
            return redirect(url_for('admin.experiments'))
    return render_template('admin/experiment_form.html', experiment=e, series=Series.query.all(), subjects=Subject.query.all())

@admin_bp.post('/experiments/<int:id>/delete')
def experiment_delete(id):
    e=Experiment.query.get_or_404(id); db.session.delete(e); db.session.commit(); flash('Experimento excluído.','success'); return redirect(url_for('admin.experiments'))

@admin_bp.get('/experiments/<int:id>/entregas')
def project_submissions(id):
    experiment = Experiment.query.get_or_404(id)
    rubric = ProjectRubric.query.filter_by(experiment_id=experiment.id).first()
    submissions = ProjectSubmission.query.filter_by(experiment_id=experiment.id).order_by(ProjectSubmission.submitted_at.desc()).all()
    return render_template('admin/project_submissions.html', experiment=experiment, rubric=rubric, submissions=submissions)

@admin_bp.post('/experiments/<int:id>/entregas')
def project_submissions_save(id):
    experiment = Experiment.query.get_or_404(id)
    action = request.form.get('action', '').strip()
    if action == 'rubric':
        rubric = ProjectRubric.query.filter_by(experiment_id=experiment.id).first()
        if not rubric:
            rubric = ProjectRubric(experiment_id=experiment.id)
            db.session.add(rubric)
            db.session.flush()
        rubric.title = request.form.get('rubric_title', '').strip() or 'Rubrica do projeto'
        names = request.form.getlist('criterion_name')
        points = request.form.getlist('criterion_points')
        descriptions = request.form.getlist('criterion_description')
        # Rebuild the small rubric atomically so removed criteria disappear too.
        for criterion in list(rubric.criteria):
            db.session.delete(criterion)
        for pos, name in enumerate(names):
            name = name.strip()
            if not name:
                continue
            try:
                maximum = float(points[pos]) if pos < len(points) else 10
            except (TypeError, ValueError):
                maximum = 10
            maximum = max(0.5, min(maximum, 100))
            db.session.add(ProjectRubricCriterion(rubric=rubric, name=name, description=(descriptions[pos].strip() if pos < len(descriptions) else ''), max_points=maximum, position=pos))
        db.session.commit()
        flash('Rubrica salva.', 'success')
    elif action == 'feedback':
        sid = request.form.get('submission_id', '').strip()
        submission = ProjectSubmission.query.filter_by(id=int(sid) if sid.isdigit() else -1, experiment_id=experiment.id).first_or_404()
        submission.teacher_feedback = request.form.get('teacher_feedback', '').strip()
        raw_score = request.form.get('score', '').strip()
        try:
            submission.score = max(0, min(float(raw_score), 100)) if raw_score else None
        except ValueError:
            submission.score = None
        submission.status = 'reviewed'
        db.session.commit()
        flash('Feedback salvo.', 'success')
    elif action == 'rubric_score':
        sid = request.form.get('submission_id', '').strip()
        submission = ProjectSubmission.query.filter_by(id=int(sid) if sid.isdigit() else -1, experiment_id=experiment.id).first_or_404()
        rubric = ProjectRubric.query.filter_by(experiment_id=experiment.id).first()
        if rubric:
            existing = {row.criterion_id: row for row in submission.rubric_scores}
            for criterion in rubric.criteria:
                raw = request.form.get(f'criterion_{criterion.id}', '').strip()
                try: points = max(0, min(float(raw), criterion.max_points)) if raw else 0
                except ValueError: points = 0
                row = existing.get(criterion.id) or ProjectRubricScore(submission_id=submission.id, criterion_id=criterion.id)
                row.points = points
                row.feedback = request.form.get(f'criterion_feedback_{criterion.id}', '').strip()
                db.session.add(row)
            db.session.commit()
            flash('Avaliação por rubrica salva.', 'success')
    elif action == 'moderate_comment':
        cid = request.form.get('comment_id', '').strip()
        comment = ProjectComment.query.get_or_404(int(cid) if cid.isdigit() else -1)
        if comment.submission.experiment_id != experiment.id:
            abort(404)
        comment.status = 'hidden' if request.form.get('status') == 'hidden' else 'visible'
        db.session.commit()
        flash('Comentário moderado.', 'success')
    return redirect(url_for('admin.project_submissions', id=experiment.id))


@admin_bp.get('/activities/<int:id>/resultados')
def activity_results(id):
    activity = Activity.query.get_or_404(id)
    questions = activity.get_questions()
    students = User.query.filter_by(role='student').order_by(User.name).all()
    rows = []
    for student in students:
        attempt = ActivityAttempt.query.filter_by(activity_id=id, user_id=student.id).order_by(ActivityAttempt.id.desc()).first()
        correct = None
        if attempt:
            answers = attempt.get_answers()
            correct = sum(1 for i, question in enumerate(questions) if answers.get(str(i)) == question.get('correct'))
        rows.append({'student': student, 'attempt': attempt, 'correct': correct, 'total': len(questions)})
    submitted = sum(1 for row in rows if row['attempt'])
    return render_template('admin/activity_results.html', activity=activity, attempts=rows, questions=questions, submitted=submitted, pending=len(rows)-submitted)

@admin_bp.get('/activities/<int:activity_id>/tentativa/<int:attempt_id>')
def activity_attempt_detail(activity_id, attempt_id):
    activity = Activity.query.get_or_404(activity_id)
    attempt = ActivityAttempt.query.filter_by(id=attempt_id, activity_id=activity.id).first_or_404()
    questions = activity.get_questions()
    answers = attempt.get_answers()
    details = []
    for i, question in enumerate(questions):
        answer = answers.get(str(i))
        correct = question.get('correct')
        details.append({'number': i + 1, 'question': question.get('question', ''), 'answer': answer, 'correct': correct, 'is_correct': answer == correct})
    return render_template('admin/activity_attempt_detail.html', activity=activity, attempt=attempt, details=details)

@admin_bp.route('/avisos', methods=['GET', 'POST'])
def announcements():
    students = User.query.filter_by(role='student').order_by(User.name).all()
    if request.method == 'POST':
        message = request.form.get('message', '').strip()
        link = request.form.get('link', '').strip()
        if not message or len(message) > 500:
            flash('O aviso precisa ter entre 1 e 500 caracteres.', 'error')
        elif link and not valid_url(link):
            flash('O link do aviso precisa ser uma URL http(s) válida.', 'error')
        else:
            notify_students(message, link or None, category='announcements')
            db.session.commit()
            flash(f'Aviso enviado para {len(students)} aluno(s).', 'success')
            return redirect(url_for('admin.announcements'))
    recent = Notification.query.order_by(Notification.id.desc()).limit(30).all()
    return render_template('admin/announcements.html', students=students, recent=recent)

def _report_dataset():
    q = request.args.get('q', '').strip()
    activity_id = request.args.get('activity_id', '').strip()
    student_id = request.args.get('student_id', '').strip()
    period = request.args.get('period', '30').strip()
    try:
        days = int(period)
    except ValueError:
        days = 30
    days = days if days in {7, 30, 90, 365} else 30
    since = utcnow() - timedelta(days=days)

    students_q = User.query.filter_by(role='student')
    if q:
        like = f'%{q}%'
        students_q = students_q.filter(or_(User.name.ilike(like), User.email.ilike(like)))
    if student_id.isdigit():
        students_q = students_q.filter(User.id == int(student_id))
    students = students_q.order_by(User.name.asc()).all()

    activities_q = Activity.query.order_by(Activity.title.asc())
    if activity_id.isdigit():
        activities_q = activities_q.filter(Activity.id == int(activity_id))
    activities = activities_q.all()
    activity_ids = {a.id for a in activities}
    student_ids = {u.id for u in students}
    attempts = ActivityAttempt.query.filter(ActivityAttempt.created_at >= since)
    if activity_id.isdigit():
        attempts = attempts.filter(ActivityAttempt.activity_id.in_(activity_ids or {-1}))
    if student_ids:
        attempts = attempts.filter(ActivityAttempt.user_id.in_(student_ids))
    else:
        attempts = attempts.filter(db.false())
    attempts = attempts.order_by(ActivityAttempt.created_at.asc()).all()

    scores = [float(a.score) for a in attempts]
    average = round(sum(scores) / len(scores), 1) if scores else 0
    completion_pairs = {(a.user_id, a.activity_id) for a in attempts}
    total_possible = len(students) * len(activities)
    completion = round(len(completion_pairs) / total_possible * 100) if total_possible else 0
    below = sum(1 for score in scores if score < 6)

    activity_rows = []
    for activity in activities:
        aa = [a for a in attempts if a.activity_id == activity.id]
        vals = [float(a.score) for a in aa]
        unique = len({a.user_id for a in aa})
        activity_rows.append({
            'activity': activity, 'attempts': len(aa), 'students': unique,
            'average': round(sum(vals)/len(vals), 1) if vals else None,
            'completion': round(unique/len(students)*100) if students else 0,
            'best': max(vals) if vals else None,
        })
    activity_rows.sort(key=lambda r: (r['average'] is None, -(r['average'] or 0)))

    student_rows = []
    for student in students:
        aa = [a for a in attempts if a.user_id == student.id]
        vals = [float(a.score) for a in aa]
        unique = len({a.activity_id for a in aa})
        student_rows.append({
            'student': student, 'attempts': len(aa), 'completed': unique,
            'completion': round(unique/len(activities)*100) if activities else 0,
            'average': round(sum(vals)/len(vals), 1) if vals else None,
            'best': max(vals) if vals else None,
        })
    student_rows.sort(key=lambda r: (r['average'] is None, r['average'] if r['average'] is not None else 0, r['student'].name.lower()))

    buckets = []
    bucket_count = 7
    step = max(days // bucket_count, 1)
    for i in range(bucket_count - 1, -1, -1):
        start = utcnow() - timedelta(days=(i+1)*step)
        end = utcnow() - timedelta(days=i*step)
        vals = [float(a.score) for a in attempts if start <= a.created_at < end]
        buckets.append({'label': start.strftime('%d/%m'), 'average': round(sum(vals)/len(vals), 1) if vals else None, 'count': len(vals)})
    max_chart = max([b['average'] or 0 for b in buckets] + [10])
    return locals()

@admin_bp.get('/relatorios')
def reports():
    data = _report_dataset()
    return render_template('admin/reports.html', **data, activity_options=Activity.query.order_by(Activity.title.asc()).all(), student_options=User.query.filter_by(role='student').order_by(User.name.asc()).all())

@admin_bp.get('/relatorios/export.csv')
def reports_csv():
    import csv, io
    data = _report_dataset()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Aluno', 'E-mail', 'Atividades concluídas', 'Conclusão %', 'Tentativas', 'Média', 'Melhor nota'])
    for row in data['student_rows']:
        writer.writerow([row['student'].name, row['student'].email, row['completed'], row['completion'], row['attempts'], row['average'] if row['average'] is not None else '', row['best'] if row['best'] is not None else ''])
    resp = make_response('\ufeff' + out.getvalue())
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=portal-python-relatorio.csv'
    return resp

@admin_bp.get('/relatorios/export.xlsx')
def reports_xlsx():
    try:
        from openpyxl import Workbook
    except ImportError:
        flash('A exportação Excel requer a dependência openpyxl.', 'error')
        return redirect(url_for('admin.reports'))
    data = _report_dataset()
    wb = Workbook()
    ws = wb.active
    ws.title = 'Alunos'
    ws.append(['Aluno', 'E-mail', 'Concluídas', 'Conclusão %', 'Tentativas', 'Média', 'Melhor nota'])
    for row in data['student_rows']:
        ws.append([row['student'].name, row['student'].email, row['completed'], row['completion'], row['attempts'], row['average'], row['best']])
    wa = wb.create_sheet('Atividades')
    wa.append(['Atividade', 'Tentativas', 'Alunos', 'Conclusão %', 'Média', 'Melhor nota'])
    for row in data['activity_rows']:
        wa.append([row['activity'].title, row['attempts'], row['students'], row['completion'], row['average'], row['best']])
    for sheet in wb.worksheets:
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for col in sheet.columns:
            width = min(max(len(str(cell.value or '')) for cell in col) + 2, 42)
            sheet.column_dimensions[col[0].column_letter].width = width
    import io
    out = io.BytesIO()
    wb.save(out)
    resp = make_response(out.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = 'attachment; filename=portal-python-relatorio.xlsx'
    return resp

@admin_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    """Exibe e persiste exclusivamente as configurações da fase 5.1."""
    if request.method == 'POST':
        institution_name = request.form.get('institution_name', '').strip()
        upload_limit_raw = request.form.get('upload_limit_mb', '').strip()
        if not institution_name or len(institution_name) > 160:
            flash('Informe um nome de instituição válido (1 a 160 caracteres).', 'error')
            return redirect(url_for('admin.settings'))
        try:
            upload_limit = int(upload_limit_raw)
        except (TypeError, ValueError):
            upload_limit = 0
        if not 1 <= upload_limit <= 100:
            flash('O limite de upload deve estar entre 1 e 100 MB.', 'error')
            return redirect(url_for('admin.settings'))

        checkbox_keys = (
            'public_registration', 'google_oauth_enabled',
            'notifications_enabled', 'notification_activities',
            'notification_materials', 'notification_deadlines',
            'notification_announcements', 'alerts_enabled',
            'alert_inactive_students', 'alert_pending_activities',
            'alert_low_performance', 'alert_performance_drop',
            'alert_deadlines',
            'ai_tutor_enabled', 'ai_feedback_enabled', 'ai_question_gen_enabled',
            'ai_class_summary_enabled', 'ai_project_precorrect_enabled', 'ai_code_review_enabled',
        )
        set_setting('institution_name', institution_name)
        set_setting('upload_limit_mb', upload_limit)
        try:
            ai_rate_limit = max(1, min(int(request.form.get('ai_tutor_rate_limit', '10')), 100))
        except (TypeError, ValueError):
            ai_rate_limit = 10
        set_setting('ai_tutor_rate_limit', ai_rate_limit)
        try:
            ai_question_gen_rate_limit = max(1, min(int(request.form.get('ai_question_gen_rate_limit', '20')), 100))
        except (TypeError, ValueError):
            ai_question_gen_rate_limit = 20
        set_setting('ai_question_gen_rate_limit', ai_question_gen_rate_limit)
        for key in checkbox_keys:
            set_setting(key, 'true' if request.form.get(key) == 'on' else 'false')
        db.session.commit()
        current_app.config['MAX_CONTENT_LENGTH'] = upload_limit * 1024 * 1024
        flash('Configurações salvas com sucesso.', 'success')
        return redirect(url_for('admin.settings'))

    from ..settings import get_setting
    values = {key: get_setting(key, default) for key, default in DEFAULT_SETTINGS.items()}
    return render_template(
        'admin/settings.html',
        settings=values,
        google_credentials_configured=bool(
            os.getenv('GOOGLE_CLIENT_ID', '').strip()
            and os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
        ),
    )

# ---------------------------------------------------------------------------
# FASE 5.11 — Importação e exportação de alunos/progresso
# ---------------------------------------------------------------------------
def _parse_student_rows(upload):
    """Lê CSV/XLSX e devolve (linhas, erros). Cabeçalhos aceitos: nome/name,
    email/e-mail e senha/password (opcional). Não lê nem exporta senhas."""
    import csv
    filename = secure_filename(upload.filename or '')
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    rows, errors = [], []
    if ext == 'csv':
        raw = upload.read()
        text_data = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                text_data = raw.decode(enc); break
            except UnicodeDecodeError:
                pass
        if text_data is None:
            return [], ['CSV não pôde ser lido como texto.']
        reader = csv.DictReader(io.StringIO(text_data))
        if not reader.fieldnames:
            return [], ['CSV sem cabeçalho.']
        for n, row in enumerate(reader, 2):
            rows.append((n, {str(k or '').strip().casefold(): (v or '').strip() for k, v in row.items()}))
    elif ext == 'xlsx':
        try:
            from openpyxl import load_workbook
            wb = load_workbook(upload, read_only=True, data_only=True)
            ws = wb.active
            values = list(ws.iter_rows(values_only=True))
            if not values:
                return [], ['Planilha vazia.']
            headers = [str(v or '').strip().casefold() for v in values[0]]
            for n, values_row in enumerate(values[1:], 2):
                rows.append((n, {headers[i]: str(values_row[i] or '').strip() for i in range(len(headers))}))
        except Exception as exc:
            current_app.logger.warning('Falha ao ler XLSX: %s', exc)
            return [], ['Planilha XLSX inválida ou corrompida.']
    else:
        return [], ['Formato não suportado. Use CSV ou XLSX.']
    return rows, errors


def _student_field(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None:
            return str(value).strip()
    return ''


@admin_bp.route('/importacao-exportacao', methods=['GET', 'POST'])
def import_export():
    result = None
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'import_students':
            upload = request.files.get('file')
            if not upload or not upload.filename:
                flash('Selecione um arquivo CSV ou XLSX.', 'error')
            else:
                rows, parse_errors = _parse_student_rows(upload)
                imported, duplicates, errors = [], [], list(parse_errors)
                seen = set()
                temporary_passwords = []
                from secrets import token_urlsafe
                for line, row in rows:
                    name = _student_field(row, 'nome', 'name')
                    email = _student_field(row, 'email', 'e-mail', 'e_mail').lower()
                    password = _student_field(row, 'senha', 'password')
                    if not name or not email or '@' not in email:
                        errors.append(f'Linha {line}: nome e e-mail válido são obrigatórios.')
                        continue
                    if email in seen:
                        duplicates.append({'line': line, 'email': email, 'reason': 'duplicado no arquivo'})
                        continue
                    seen.add(email)
                    if User.query.filter_by(email=email).first():
                        duplicates.append({'line': line, 'email': email, 'reason': 'já cadastrado'})
                        continue
                    user = User(name=name[:120], email=email, role='student')
                    temporary_password = '' if password else token_urlsafe(9)
                    user.set_password(password if password else temporary_password)
                    db.session.add(user)
                    imported.append({'line': line, 'email': email, 'name': name})
                    if temporary_password:
                        temporary_passwords.append({'email': email, 'password': temporary_password})
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    current_app.logger.exception('Falha na importação de alunos')
                    errors.append('Não foi possível salvar os alunos. Nenhuma alteração desta importação foi aplicada.')
                    imported = []
                flash(f'Importação concluída: {len(imported)} aluno(s) criado(s), {len(duplicates)} duplicado(s), {len(errors)} erro(s).', 'success' if not errors else 'error')
                result = {'imported': imported, 'duplicates': duplicates, 'errors': errors, 'temporary_passwords': temporary_passwords}
        else:
            flash('Ação de importação inválida.', 'error')
    return render_template('admin/import_export.html', result=result)


@admin_bp.get('/exportar/alunos.csv')
def export_students_csv():
    import csv
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['id', 'nome', 'email', 'perfil', 'criado_em'])
    for user in User.query.filter_by(role='student').order_by(User.name.asc()).all():
        writer.writerow([user.id, user.name, user.email, user.role, user.created_at.strftime('%Y-%m-%d %H:%M:%S')])
    response = make_response('\ufeff' + out.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=portal-python-alunos.csv'
    return response


@admin_bp.get('/exportar/alunos.xlsx')
def export_students_xlsx():
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = 'Alunos'
    ws.append(['id', 'nome', 'email', 'perfil', 'criado_em'])
    for user in User.query.filter_by(role='student').order_by(User.name.asc()).all():
        ws.append([user.id, user.name, user.email, user.role, user.created_at.strftime('%Y-%m-%d %H:%M:%S')])
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return Response(output.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=portal-python-alunos.xlsx'})


@admin_bp.get('/exportar/progresso.csv')
def export_progress_csv():
    import csv
    out = io.StringIO(); writer = csv.writer(out)
    writer.writerow(['aluno_id', 'aluno', 'email', 'material_id', 'material', 'concluido_em'])
    query = Progress.query.join(User).join(Content).order_by(User.name.asc(), Progress.completed_at.asc())
    for p in query.all():
        writer.writerow([p.user_id, p.user.name, p.user.email, p.content_id, p.content.title, p.completed_at.strftime('%Y-%m-%d %H:%M:%S')])
    response = make_response('\ufeff' + out.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=portal-python-progresso.csv'
    return response


@admin_bp.get('/exportar/progresso.xlsx')
def export_progress_xlsx():
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = 'Progresso'
    ws.append(['aluno_id', 'aluno', 'email', 'material_id', 'material', 'concluido_em'])
    for p in Progress.query.join(User).join(Content).order_by(User.name.asc(), Progress.completed_at.asc()).all():
        ws.append([p.user_id, p.user.name, p.user.email, p.content_id, p.content.title, p.completed_at.strftime('%Y-%m-%d %H:%M:%S')])
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return Response(output.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=portal-python-progresso.xlsx'})
