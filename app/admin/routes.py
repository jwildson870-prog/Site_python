import os, uuid
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
def contents(): return render_template('admin/contents.html',contents=Content.query.order_by(Content.id.desc()).all())
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

@admin_bp.get('/users')
def users():
    q = request.args.get('q', '').strip()
    query = User.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like)))
    return render_template('admin/users.html', users=query.order_by(User.id.desc()).all(), q=q)

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
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        sid = request.form.get('series_id', '').strip()
        subid = request.form.get('subject_id', '').strip()
        s = Series.query.get(int(sid)) if sid.isdigit() else None
        sub = Subject.query.get(int(subid)) if subid.isdigit() else None
        if not title or not s or not sub or sub.series_id != s.id:
            flash('Preencha título, série e matéria.', 'error')
        else:
            a = Activity(title=title, description=description, series_id=s.id, subject_id=sub.id)
            a.set_questions([])
            db.session.add(a)
            db.session.commit()
            flash('Atividade criada. Agora adicione as questões.', 'success')
            return redirect(url_for('admin.activity_edit', id=a.id))
    return render_template('admin/activities.html', activities=Activity.query.order_by(Activity.id.desc()).all(), series=Series.query.all())


def read_activity_questions_from_form():
    questions = []
    for i in range(20):
        question = request.form.get(f'question_{i}', '').strip()
        options = [request.form.get(f'option_{i}_{letter}', '').strip() for letter in ('a', 'b', 'c', 'd')]
        correct_index = request.form.get(f'correct_{i}', '').strip()
        if not question and not any(options) and not correct_index:
            continue
        if not question or sum(bool(option) for option in options) < 2 or correct_index not in {'0', '1', '2', '3'}:
            return None, 'Cada questão preenchida precisa de enunciado, pelo menos duas alternativas e uma resposta correta.'
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
        questions, error = read_activity_questions_from_form()
        if not title:
            flash('Informe o título da atividade.', 'error')
        elif error:
            flash(error, 'error')
        else:
            a.title = title
            a.description = desc
            a.set_questions(questions)
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
        return render_template('admin/activity_form.html', activity=a, questions=questions)
    return render_template('admin/activity_form.html', activity=a, questions=a.get_questions())

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
    attempts = ActivityAttempt.query.filter_by(activity_id=id).order_by(ActivityAttempt.score.desc(), ActivityAttempt.id.desc()).all()
    return render_template('admin/activity_results.html', activity=activity, attempts=attempts)

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
