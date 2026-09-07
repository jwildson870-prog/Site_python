import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Series, Subject, Content, User

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALLOWED_KINDS = {'explanation', 'file', 'pdf', 'slide', 'video', 'link'}
ALLOWED_EXTENSIONS = {
    'pdf', 'png', 'jpg', 'jpeg', 'webp', 'gif',
    'ppt', 'pptx', 'doc', 'docx', 'txt'
}
MAX_UPLOAD = 25 * 1024 * 1024


def valid_url(value):
    parsed = urlparse(value or '')
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def save_uploaded_file(file, required_extension=None):
    if not file or not file.filename:
        return None, None

    original = secure_filename(file.filename)
    extension = Path(original).suffix.lower().lstrip('.')

    if not extension or extension not in ALLOWED_EXTENSIONS:
        return False, None

    if required_extension and extension != required_extension:
        return False, None

    if request.content_length and request.content_length > MAX_UPLOAD:
        return 'too_large', None

    filename = f'{uuid.uuid4().hex}.{extension}'
    upload_folder = Path(current_app.config['UPLOAD_FOLDER'])
    upload_folder.mkdir(parents=True, exist_ok=True)
    file.save(upload_folder / filename)
    return filename, original


@admin_bp.before_request
def admin_guard():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.path))
    if not current_user.is_admin:
        abort(403)


@admin_bp.get('/')
def dashboard():
    return render_template(
        'admin/dashboard.html',
        series=Series.query.count(),
        series_list=Series.query.order_by(Series.id).all(),
        subjects=Subject.query.count(),
        contents=Content.query.count(),
        users=User.query.count(),
    )


@admin_bp.route('/series', methods=['GET', 'POST'])
def series_list():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Informe o nome da série.', 'error')
        elif Series.query.filter_by(name=name).first():
            flash('Série já existe.', 'error')
        else:
            db.session.add(Series(name=name))
            db.session.commit()
            flash('Série criada.', 'success')
    return render_template('admin/series.html', series=Series.query.order_by(Series.id).all())


@admin_bp.route('/series/<int:id>/editar', methods=['GET', 'POST'])
def series_edit(id):
    s = Series.query.get_or_404(id)
    if request.method == 'POST':
        n = request.form.get('name', '').strip()
        other = Series.query.filter(Series.name == n, Series.id != id).first()
        if not n or other:
            flash('Nome inválido ou já utilizado.', 'error')
        else:
            s.name = n
            db.session.commit()
            flash('Série atualizada.', 'success')
            return redirect(url_for('admin.series_list'))
    return render_template('admin/edit_simple.html', title='Editar série', value=s.name, action=url_for('admin.series_edit', id=id))


@admin_bp.post('/series/<int:id>/excluir')
def series_delete(id):
    s = Series.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    flash('Série excluída.', 'success')
    return redirect(url_for('admin.series_list'))


@admin_bp.route('/subjects', methods=['GET', 'POST'])
def subjects_list():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        sid = request.form.get('series_id', '')
        s = Series.query.get(int(sid)) if sid.isdigit() else None
        if not name or not s:
            flash('Informe matéria e série.', 'error')
        elif Subject.query.filter_by(name=name, series_id=s.id).first():
            flash('Matéria já existe nessa série.', 'error')
        else:
            db.session.add(Subject(name=name, series_id=s.id))
            db.session.commit()
            flash('Matéria criada.', 'success')
    return render_template('admin/subjects.html', subjects=Subject.query.order_by(Subject.id).all(), series=Series.query.order_by(Series.id).all())


@admin_bp.route('/subjects/<int:id>/editar', methods=['GET', 'POST'])
def subject_edit(id):
    s = Subject.query.get_or_404(id)
    if request.method == 'POST':
        n = request.form.get('name', '').strip()
        sid = request.form.get('series_id', '')
        series = Series.query.get(int(sid)) if sid.isdigit() else None
        dup = Subject.query.filter(
            Subject.name == n,
            Subject.series_id == series.id if series else False,
            Subject.id != id,
        ).first() if series else None
        if not n or not series or dup:
            flash('Dados inválidos ou duplicados.', 'error')
        else:
            s.name = n
            s.series_id = series.id
            db.session.commit()
            flash('Matéria atualizada.', 'success')
            return redirect(url_for('admin.subjects_list'))
    return render_template('admin/subject_edit.html', subject=s, series=Series.query.all())


@admin_bp.post('/subjects/<int:id>/excluir')
def subject_delete(id):
    s = Subject.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    flash('Matéria excluída.', 'success')
    return redirect(url_for('admin.subjects_list'))


@admin_bp.get('/contents')
def contents():
    return render_template('admin/contents.html', contents=Content.query.order_by(Content.id.desc()).all())


@admin_bp.get('/series/<int:id>')
def series_detail(id):
    s = Series.query.get_or_404(id)
    return render_template('admin/series_detail.html', series=s)


def content_form(content=None):
    series = Series.query.order_by(Series.id).all()
    subjects = Subject.query.order_by(Subject.id).all()

    if request.method != 'POST':
        return content, series, subjects

    sid = request.form.get('series_id', '')
    subid = request.form.get('subject_id', '')
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
        required_extension = 'pdf' if kind == 'pdf' else None
        uploaded, original_name = save_uploaded_file(request.files.get('file') or request.files.get('pdf'), required_extension)
        if uploaded is False:
            msg = 'Para o tipo "PDF", envie um arquivo .pdf.' if kind == 'pdf' else 'Esse tipo de arquivo não é permitido.'
            flash(msg, 'error')
            return None, series, subjects
        if uploaded == 'too_large':
            flash('Arquivo muito grande. Limite: 25 MB.', 'error')
            return None, series, subjects
        if uploaded:
            new_file = uploaded
            desc = desc or f'Material enviado: {original_name}'
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
        try:
            os.remove(Path(current_app.config['UPLOAD_FOLDER']) / old_file)
        except FileNotFoundError:
            pass

    return content, None, None


@admin_bp.route('/contents/new', methods=['GET', 'POST'])
def content_new():
    content, series, subjects = content_form()
    if content:
        flash('Material publicado com sucesso.', 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=None, series=series, subjects=subjects)


@admin_bp.route('/contents/<int:id>/edit', methods=['GET', 'POST'])
def content_edit(id):
    content = Content.query.get_or_404(id)
    result, series, subjects = content_form(content)
    if result and result.id:
        flash('Material atualizado.', 'success')
        return redirect(url_for('admin.contents'))
    return render_template('admin/content_form.html', content=content, series=series, subjects=subjects)


# A remoção continua disponível no backend para uma etapa futura,
# mas não é exibida no novo painel por enquanto.
@admin_bp.post('/contents/<int:id>/delete')
def content_delete(id):
    content = Content.query.get_or_404(id)
    filename = content.file_name
    db.session.delete(content)
    db.session.commit()
    if filename:
        try:
            os.remove(Path(current_app.config['UPLOAD_FOLDER']) / filename)
        except FileNotFoundError:
            pass
    flash('Conteúdo excluído.', 'success')
    return redirect(url_for('admin.contents'))


@admin_bp.get('/file/<filename>')
def file(filename):
    from flask import send_from_directory
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@admin_bp.get('/users')
def users():
    return render_template('admin/users.html', users=User.query.order_by(User.id).all())


@admin_bp.get('/settings')
def settings():
    return render_template('admin/settings.html')
