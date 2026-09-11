import os, uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_from_directory, Response
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename
from ..storage import upload as storage_upload, delete as storage_delete, get_file, b2_enabled, StorageError
from ..extensions import db
from ..models import Series, Subject, Content, User, Activity, ActivityAttempt, Experiment, Notification

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

@admin_bp.get('/')
def dashboard():
    recent_contents = Content.query.order_by(Content.id.desc()).limit(5).all()
    students = User.query.filter_by(role='student').order_by(User.id.desc()).all()
    recent_attempts = ActivityAttempt.query.order_by(ActivityAttempt.id.desc()).limit(8).all()
    unread_notifications = Notification.query.filter_by(read=False).count()
    total_attempts = ActivityAttempt.query.count()
    average_score = db.session.query(db.func.avg(ActivityAttempt.score)).scalar()
    average_score = round(float(average_score), 1) if average_score is not None else 0
    return render_template('admin/dashboard.html',
        series=Series.query.count(), series_list=Series.query.order_by(Series.id).all(),
        subjects=Subject.query.count(), contents=Content.query.count(), users=User.query.count(),
        students_count=len(students), activities=Activity.query.count(), experiments=Experiment.query.count(),
        recent_contents=recent_contents, recent_attempts=recent_attempts,
        total_attempts=total_attempts, average_score=average_score,
        unread_notifications=unread_notifications)

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
    new_file = old_file
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
        elif not old_file:
            flash('Escolha um arquivo do seu dispositivo.', 'error')
            return None, series, subjects
    else:
        new_file = None

    if content is None:
        content = Content()

    content.title = title
    content.description = desc
    content.kind = kind
    content.body = body if kind == 'explanation' else None
    content.external_url = external_url if kind in {'slide', 'video', 'link'} else None
    content.file_name = new_file
    content.series_id = s.id
    content.subject_id = sub.id
    db.session.add(content)
    db.session.commit()

    if old_file and old_file != new_file:
        storage_delete(old_file)
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
    db.session.delete(content)
    db.session.commit()
    if filename:
        storage_delete(filename)
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

@admin_bp.route('/activities', methods=['GET', 'POST'])
def activities():
    q = request.args.get('q', '').strip()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    status = request.args.get('status', '').strip().lower()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
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
            a = Activity(title=title, description=description[:5000], series_id=s.id, subject_id=sub.id, due_at=due_at)
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
    now = datetime.utcnow()
    items = query.order_by(Activity.id.desc()).all()
    if status == 'pending': items = [a for a in items if not a.due_at or a.due_at >= now]
    elif status == 'expired': items = [a for a in items if a.due_at and a.due_at < now]
    return render_template('admin/activities.html', activities=items, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all(), q=q, series_id=series_id, subject_id=subject_id, status=status, now=now)


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
        return render_template('admin/activity_form.html', activity=a, questions=questions, due_raw=due_raw)
    due_raw = a.due_at.strftime('%Y-%m-%dT%H:%M') if a.due_at else ''
    return render_template('admin/activity_form.html', activity=a, questions=a.get_questions(), due_raw=due_raw)

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

@admin_bp.get('/relatorios')
def reports():
    students = User.query.filter_by(role='student').count()
    attempts = ActivityAttempt.query.count()
    average = db.session.query(db.func.avg(ActivityAttempt.score)).scalar()
    best = ActivityAttempt.query.order_by(ActivityAttempt.score.desc()).first()
    return render_template('admin/reports.html', students=students, attempts=attempts, average=round(float(average),1) if average is not None else 0, best=best)

@admin_bp.get('/settings')
def settings(): return render_template('admin/settings.html')
