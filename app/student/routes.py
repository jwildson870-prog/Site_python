from flask import Blueprint, render_template, abort, send_from_directory, current_app, redirect, url_for, request, flash
from flask_login import current_user
from urllib.parse import urlparse, parse_qs
from ..models import Series, Subject, Content, StudyProgress
from ..extensions import db

student_bp = Blueprint('student', __name__, url_prefix='/aluno')

@student_bp.before_request
def guard():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login', next=request.path))
    if current_user.is_admin:
        abort(403)

def video_embed(url):
    if not url: return None
    p=urlparse(url); host=p.netloc.lower(); path=p.path.strip('/')
    if 'youtube.com' in host:
        vid=parse_qs(p.query).get('v',[None])[0]
        if not vid and path.startswith('embed/'): vid=path.split('/')[1]
        if not vid and path.startswith('shorts/'): vid=path.split('/')[1]
        return f'https://www.youtube.com/embed/{vid}' if vid else None
    if 'youtu.be' in host and path: return f"https://www.youtube.com/embed/{path.split('/')[0]}"
    if 'vimeo.com' in host and path.isdigit(): return f'https://player.vimeo.com/video/{path}'
    if 'player.vimeo.com' in host: return url
    return None

@student_bp.get('/')
def dashboard():
    series = Series.query.order_by(Series.id).all()
    contents = Content.query.order_by(Content.created_at.desc()).all()
    completed_ids = {p.content_id for p in StudyProgress.query.filter_by(user_id=current_user.id).all()}
    total = len(contents)
    completed = len(completed_ids.intersection({c.id for c in contents}))
    progress = round((completed / total) * 100) if total else 0
    recent = contents[:6]
    return render_template('student/dashboard.html', series=series, contents=contents, recent=recent, completed_ids=completed_ids, total_contents=total, completed_contents=completed, progress=progress)

@student_bp.post('/conteudo/<int:id>/concluir')
def toggle_complete(id):
    content = Content.query.get_or_404(id)
    progress = StudyProgress.query.filter_by(user_id=current_user.id, content_id=content.id).first()
    if progress:
        db.session.delete(progress); flash('Conteúdo marcado como pendente.', 'success')
    else:
        db.session.add(StudyProgress(user_id=current_user.id, content_id=content.id)); flash('Conteúdo marcado como concluído. 🎉', 'success')
    db.session.commit()
    return redirect(request.referrer or url_for('student.dashboard'))

@student_bp.get('/buscar')
def search():
    q = request.args.get('q', '').strip()
    query = Content.query
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
    contents = query.order_by(Content.created_at.desc()).all()
    completed_ids = {p.content_id for p in StudyProgress.query.filter_by(user_id=current_user.id).all()}
    return render_template('student/search.html', q=q, contents=contents, completed_ids=completed_ids)

@student_bp.get('/serie/<int:id>')
def series(id):
    s = Series.query.get_or_404(id)
    return render_template('student/series.html', series=s)

@student_bp.get('/materia/<int:id>')
def subject(id):
    s = Subject.query.get_or_404(id)
    completed_ids = {p.content_id for p in StudyProgress.query.filter_by(user_id=current_user.id).all()}
    return render_template('student/subject.html', subject=s, completed_ids=completed_ids)

@student_bp.get('/conteudo/<int:id>')
def content(id):
    c = Content.query.get_or_404(id)
    completed = StudyProgress.query.filter_by(user_id=current_user.id, content_id=c.id).first() is not None
    return render_template('student/content.html', content=c, video_embed=video_embed(c.external_url), completed=completed)

@student_bp.get('/arquivo/<int:id>')
def arquivo(id):
    c = Content.query.get_or_404(id)
    if c.kind not in ('file','pdf') or not c.file_name: abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], c.file_name, as_attachment=False)

@student_bp.get('/download/<int:id>')
def download(id):
    c = Content.query.get_or_404(id)
    if c.kind not in ('file','pdf') or not c.file_name: abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], c.file_name, as_attachment=True, download_name=(c.title + '.pdf' if c.kind=='pdf' else c.file_name))

@student_bp.get('/pdf/<int:id>')
def pdf(id):
    c = Content.query.get_or_404(id)
    if c.kind!='pdf' or not c.file_name: abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], c.file_name, mimetype='application/pdf')
