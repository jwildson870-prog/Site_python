import os, uuid
from pathlib import Path
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app, send_from_directory, Response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from ..storage import upload as storage_upload, delete as storage_delete, get_file, b2_enabled, StorageError
from ..extensions import db
from ..models import Series, Subject, Content, User, Activity, ActivityAttempt, Experiment, Notification

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')
ALLOWED_KINDS = {'explanation','file','pdf','slide','video','link'}
ALLOWED_EXTENSIONS = {'pdf','png','jpg','jpeg','webp','gif','ppt','pptx','doc','docx','txt'}
MAX_UPLOAD = 25 * 1024 * 1024

def valid_url(value):
    parsed = urlparse(value or '')
    return parsed.scheme in ('http','https') and bool(parsed.netloc)

def save_uploaded_file(file, required_extension=None):
    if not file or not file.filename: return None, None
    original = secure_filename(file.filename); extension = Path(original).suffix.lower().lstrip('.')
    if not extension or extension not in ALLOWED_EXTENSIONS or (required_extension and extension != required_extension): return False, None
    if request.content_length and request.content_length > MAX_UPLOAD: return 'too_large', None
    try:
        filename = storage_upload(file, original, file.mimetype)
    except StorageError as exc:
        current_app.logger.warning('Falha no upload do material: %s | %s', exc.message, exc.technical)
        return exc, None
    except Exception:
        current_app.logger.exception('Falha inesperada no upload do material')
        return 'storage_error', None
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
    return render_template('admin/dashboard.html', series=Series.query.count(), series_list=Series.query.order_by(Series.id).all(), subjects=Subject.query.count(), contents=Content.query.count(), users=User.query.count(), activities=Activity.query.count(), experiments=Experiment.query.count())

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
    if not title or not s or not sub or sub.series_id != s.id or kind not in ALLOWED_KINDS:
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
    if b2_enabled():
        try:
            obj = get_file(filename)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir arquivo administrativo: %s | %s', exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o arquivo', back_url=url_for('admin.contents')), 502
        return Response(obj['Body'].iter_chunks(chunk_size=64 * 1024), content_type=obj.get('ContentType') or 'application/octet-stream', headers={
            'Content-Length': str(obj['ContentLength']),
            'Content-Disposition': 'inline',
            'Cache-Control': 'private, no-store',
        })
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename, as_attachment=False)

@admin_bp.get('/users')
def users():
    return render_template('admin/users.html', users=User.query.order_by(User.id).all())

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

@admin_bp.get('/settings')
def settings(): return render_template('admin/settings.html')
