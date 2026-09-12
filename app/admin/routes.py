import os, uuid, io, json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_from_directory, Response, make_response
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename
from ..storage import upload as storage_upload, delete as storage_delete, get_file, b2_enabled, StorageError
from ..extensions import db
from ..models import Series, Subject, Content, User, Activity, ActivityAttempt, Experiment, Notification, Alert, QuestionBank
from ..pptx_preview import convert_pptx_to_images
from ..activity_library import PREBUILT_ACTIVITIES, BY_SLUG

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
ALLOWED_KINDS = {'explanation','file','pdf','slide','video','link'}
ALLOWED_EXTENSIONS = {'pdf','png','jpg','jpeg','webp','gif','ppt','pptx','doc','docx','txt'}
MAX_UPLOAD = 25 * 1024 * 1024
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
    if request.content_length and request.content_length > MAX_UPLOAD: return 'too_large', None
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

def notify_students(message, link=None):
    for student in User.query.filter_by(role='student').all():
        db.session.add(Notification(user_id=student.id, message=message, link=link))

@admin_bp.before_request
def admin_guard():
    if not current_user.is_authenticated: return redirect(url_for('auth.login', next=request.path))
    if not current_user.is_admin: abort(403)

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
    now = datetime.utcnow()
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
    _sync_smart_alerts(students, all_activities, all_attempts, now)
    smart_alerts = Alert.query.filter_by(resolved=False).order_by(Alert.priority.desc(), Alert.created_at.desc()).limit(8).all()
    alert_counts = {'high': Alert.query.filter_by(resolved=False, priority='high').count(), 'medium': Alert.query.filter_by(resolved=False, priority='medium').count()}
    return render_template('admin/dashboard.html',
        series=Series.query.count(), series_list=Series.query.order_by(Series.id).all(),
        subjects=Subject.query.count(), contents=Content.query.count(), users=User.query.count(),
        students_count=len(students), activities=len(all_activities), experiments=Experiment.query.count(),
        recent_contents=recent_contents, recent_attempts=recent_attempts,
        total_attempts=total_attempts, average_score=average_score, unread_notifications=unread_notifications,
        risk_students=risk_students, activity_stats=activity_stats, deadline_activities=deadline_activities,
        overall_completion=overall_completion, recent_activity_count=recent_activity_count, now=now, smart_alerts=smart_alerts, alert_counts=alert_counts)

@admin_bp.route('/alertas', methods=['GET', 'POST'])
def alerts():
    if request.method == 'POST':
        alert_id = request.form.get('alert_id', type=int)
        alert = db.session.get(Alert, alert_id) if alert_id else None
        if not alert or alert.resolved:
            abort(404)
        alert.resolved = True
        alert.resolved_at = datetime.utcnow()
        db.session.commit()
        flash('Alerta marcado como resolvido.', 'success')
        return redirect(url_for('admin.alerts'))
    students = User.query.filter_by(role='student').order_by(User.name).all()
    activities = Activity.query.order_by(Activity.due_at.asc().nullslast(), Activity.id.desc()).all()
    attempts = ActivityAttempt.query.order_by(ActivityAttempt.created_at.desc()).all()
    now = datetime.utcnow()
    _sync_smart_alerts(students, activities, attempts, now)
    all_alerts = Alert.query.filter_by(resolved=False).order_by(Alert.priority.desc(), Alert.created_at.desc()).all()
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
        name=request.form.get('name','').strip(); sid=request.form.get('series_id',''); s=Series.query.get(int(sid)) if sid.isdigit() else None
        if not name or not s: flash('Informe matéria e série.','error')
        elif Subject.query.filter_by(name=name,series_id=s.id).first(): flash('Matéria já existe nessa série.','error')
        else: db.session.add(Subject(name=name,series_id=s.id)); db.session.commit(); flash('Matéria criada.','success')
    return render_template('admin/subjects.html',subjects=Subject.query.order_by(Subject.id).all(),series=Series.query.order_by(Series.id).all())

@admin_bp.route('/subjects/<int:id>/editar', methods=['GET','POST'])
def subject_edit(id):
    s=Subject.query.get_or_404(id)
    if request.method=='POST':
        n=request.form.get('name','').strip(); sid=request.form.get('series_id',''); series=Series.query.get(int(sid)) if sid.isdigit() else None
        dup=Subject.query.filter(Subject.name==n,Subject.series_id==series.id,Subject.id!=id).first() if series else None
        if not n or not series or dup: flash('Dados inválidos ou duplicados.','error')
        else: s.name=n; s.series_id=series.id; db.session.commit(); flash('Matéria atualizada.','success'); return redirect(url_for('admin.subjects_list'))
    return render_template('admin/subject_edit.html',subject=s,series=Series.query.all())

@admin_bp.post('/subjects/<int:id>/excluir')
def subject_delete(id):
    s=Subject.query.get_or_404(id); db.session.delete(s); db.session.commit(); flash('Matéria excluída.','success'); return redirect(url_for('admin.subjects_list'))

@admin_bp.get('/contents')
def contents():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    query = Content.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
    if kind in ALLOWED_KINDS:
        query = query.filter_by(kind=kind)
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    contents = query.order_by(Content.id.desc()).all()
    return render_template('admin/contents.html', contents=contents, q=q, kind=kind, series_id=series_id, subject_id=subject_id,
                           series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all())
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

    s = Series.query.get(int(sid)) if sid.isdigit() else None
    sub = Subject.query.get(int(subid)) if subid.isdigit() else None
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
        notify_students(f'Novo material: {content.title}', url_for('student.content', id=content.id))
        db.session.commit()
        flash('Material publicado com sucesso.', 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=None, series=series, subjects=subjects)

@admin_bp.route('/contents/<int:id>/edit', methods=['GET', 'POST'])
def content_edit(id):
    content = Content.query.get_or_404(id)
    result, series, subjects = content_form(content)
    if request.method == 'POST' and result:
        flash('Material atualizado.', 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=content, series=series, subjects=subjects)

@admin_bp.post('/contents/<int:id>/delete')
def content_delete(id):
    content = Content.query.get_or_404(id)
    filename = content.file_name
    preview_files = _preview_files(content)
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
    return render_template('admin/user_performance.html', student=student, activities=activities, by_activity=by_activity, attempts=attempts, average=average, completed=completed, pending=pending, completion=completion, now=datetime.utcnow())

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
    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        options = [request.form.get(f'option_{letter}', '').strip() for letter in ('a','b','c','d')]
        code = request.form.get('code', '').strip()
        correct_index = request.form.get('correct', '').strip()
        sid = request.form.get('series_id', '').strip(); subid = request.form.get('subject_id', '').strip()
        ser = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
        if not question or len(question) > 1000 or sum(bool(x) for x in options) < 2 or correct_index not in {'0','1','2','3'}:
            flash('Preencha a questão, pelo menos duas alternativas e a resposta correta.', 'error')
        elif not ser or not sub or sub.series_id != ser.id:
            flash('Selecione uma série e matéria válidas.', 'error')
        elif not options[int(correct_index)]:
            flash('A resposta correta precisa estar preenchida.', 'error')
        elif any(options[i] and not options[i-1] for i in range(1,4)):
            flash('Preencha as alternativas em sequência.', 'error')
        else:
            item = QuestionBank(question=question, correct=options[int(correct_index)], code=code[:8000] or None, difficulty=difficulty if difficulty in {'facil','medio','dificil'} else 'medio', series_id=ser.id, subject_id=sub.id)
            item.set_options([x for x in options if x])
            db.session.add(item); db.session.commit()
            flash('Questão adicionada ao banco.', 'success')
            return redirect(url_for('admin.question_bank'))
    items = QuestionBank.query.order_by(QuestionBank.id.desc()).all()
    return render_template('admin/question_bank.html', items=items, series=series, subjects=subjects)

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
        item = QuestionBank.query.get(qid)
        if not item or item.series_id != activity.series_id or item.subject_id != activity.subject_id: continue
        opts = item.get_options()
        current.append({'question': item.question, 'options': opts, 'correct': item.correct, **({'code': item.code} if item.code else {})})
        added += 1
    activity.set_questions(current); db.session.commit()
    flash(f'{added} questão(ões) importada(s) para a atividade.', 'success' if added else 'error')
    return redirect(url_for('admin.activity_edit', id=id))

@admin_bp.route('/activities', methods=['GET', 'POST'])
def activities():
    q = request.args.get('q', '').strip()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    status = request.args.get('status', '').strip().lower()
    difficulty = request.args.get('difficulty', '').strip().lower()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        difficulty_value = request.form.get('difficulty', 'medio').strip().lower()
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        due_raw = request.form.get('due_at', '').strip()
        s = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
        due_at, due_error = parse_due_at(due_raw)
        if not title or len(title) > 200 or not s or not sub or sub.series_id != s.id:
            flash('Preencha título, série e matéria corretamente.', 'error')
        elif due_error:
            flash(due_error, 'error')
        else:
            a = Activity(title=title, description=description[:5000], series_id=s.id, subject_id=sub.id, due_at=due_at, difficulty=difficulty_value if difficulty_value in {'facil','medio','dificil'} else 'medio')
            a.set_questions([])
            db.session.add(a)
            db.session.commit()
            flash('Atividade criada. Agora adicione as questões.', 'success')
            return redirect(url_for('admin.activity_edit', id=a.id))
    query = Activity.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Activity.title.ilike(like), Activity.description.ilike(like)))
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    if difficulty in {'facil','medio','dificil'}: query = query.filter_by(difficulty=difficulty)
    now = datetime.utcnow()
    items = query.order_by(Activity.id.desc()).all()
    if status == 'pending': items = [a for a in items if not a.due_at or a.due_at >= now]
    elif status == 'expired': items = [a for a in items if a.due_at and a.due_at < now]
    return render_template('admin/activities.html', activities=items, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all(), q=q, series_id=series_id, subject_id=subject_id, status=status, difficulty=difficulty, now=now)


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
        ser = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
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
            notify_students(f'Nova atividade: {activity.title}', url_for('student.activity', id=activity.id))
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
    if due <= datetime.utcnow():
        return None, 'O prazo precisa ser uma data e hora futuras.'
    return due, None


def read_activity_questions_from_form():
    questions = []
    for i in range(20):
        question = request.form.get(f'question_{i}', '').strip()
        options = [request.form.get(f'option_{i}_{letter}', '').strip() for letter in ('a', 'b', 'c', 'd')]
        correct_index = request.form.get(f'correct_{i}', '').strip()
        if not question and not any(options) and not correct_index:
            continue
        if not question or len(question) > 1000 or sum(bool(option) for option in options) < 2 or correct_index not in {'0', '1', '2', '3'}:
            return None, 'Cada questão preenchida precisa de enunciado, pelo menos duas alternativas e uma resposta correta.'
        if any(len(option) > 500 for option in options):
            return None, 'Cada alternativa pode ter no máximo 500 caracteres.'
        filled = [bool(option) for option in options]
        if any(filled[i] and not filled[i - 1] for i in range(1, 4)):
            return None, 'Preencha as alternativas em sequência, sem deixar espaços vazios entre elas.'
        index = int(correct_index)
        if not options[index]:
            return None, 'A resposta correta precisa apontar para uma alternativa preenchida.'
        questions.append({'question': question, 'options': options, 'correct': options[index]})
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
            a.set_questions(questions)
            db.session.commit()
            if was_empty:
                notify_students(f'Nova atividade: {a.title}', url_for('student.activity', id=a.id))
                db.session.commit()
            flash('Atividade salva.', 'success')
            return redirect(url_for('admin.activities'))
        questions = []
        for i in range(20):
            q = request.form.get(f'question_{i}', '').strip()
            opts = [request.form.get(f'option_{i}_{letter}', '').strip() for letter in ('a', 'b', 'c', 'd')]
            correct = request.form.get(f'correct_{i}', '').strip()
            questions.append({'question': q, 'options': opts, 'correct': opts[int(correct)] if correct.isdigit() and int(correct) < len(opts) else ''})
        while questions and not questions[-1].get('question') and not any(questions[-1].get('options', [])):
            questions.pop()
        return render_template('admin/activity_form.html', activity=a, questions=questions, due_raw=due_raw, bank_items=QuestionBank.query.filter_by(series_id=a.series_id, subject_id=a.subject_id).order_by(QuestionBank.id.desc()).all())
    due_raw = a.due_at.strftime('%Y-%m-%dT%H:%M') if a.due_at else ''
    return render_template('admin/activity_form.html', activity=a, questions=a.get_questions(), due_raw=due_raw, bank_items=QuestionBank.query.filter_by(series_id=a.series_id, subject_id=a.subject_id).order_by(QuestionBank.id.desc()).all())

@admin_bp.post('/activities/<int:id>/delete')
def activity_delete(id):
    a = Activity.query.get_or_404(id)
    db.session.delete(a)
    db.session.commit()
    flash('Atividade excluída.', 'success')
    return redirect(url_for('admin.activities'))

@admin_bp.route('/experiments', methods=['GET', 'POST'])
def experiments():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        s = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
        if not title or not s or not sub or sub.series_id != s.id:
            flash('Informe título, série e matéria.', 'error')
        else:
            e = Experiment(title=title, description=request.form.get('description', '').strip(), objective=request.form.get('objective', '').strip(), materials=request.form.get('materials', '').strip(), steps=request.form.get('steps', '').strip(), safety=request.form.get('safety', '').strip(), conclusion=request.form.get('conclusion', '').strip(), series_id=s.id, subject_id=sub.id)
            db.session.add(e)
            db.session.commit()
            notify_students(f'Novo experimento: {e.title}', url_for('student.experiment', id=e.id))
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
        s = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
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
            notify_students(message, link or None)
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
    since = datetime.utcnow() - timedelta(days=days)

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
        start = datetime.utcnow() - timedelta(days=(i+1)*step)
        end = datetime.utcnow() - timedelta(days=i*step)
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

@admin_bp.get('/settings')
def settings(): return render_template('admin/settings.html')
