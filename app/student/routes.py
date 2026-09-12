from flask import Blueprint,render_template,abort,send_from_directory,current_app,redirect,url_for,request,Response,flash
from flask_login import login_required,current_user
from sqlalchemy import or_
from ..extensions import db
from ..models import Series,Subject,Content,Activity,ActivityAttempt,Experiment,Favorite,Progress,Notification
from ..storage import get_file, b2_enabled, StorageError
import json
from datetime import datetime, date, timedelta
import calendar as pycalendar
student_bp=Blueprint('student',__name__,url_prefix='/aluno')

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
    content_type = SAFE_TYPES.get(ext, fallback_type)
    disposition = 'inline' if ext in SAFE_INLINE_TYPES else 'attachment'
    return Response(obj['Body'].iter_chunks(chunk_size=64 * 1024), content_type=content_type, headers={
        'Content-Length': str(obj['ContentLength']),
        'Content-Disposition': disposition,
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
    })
def _preview_files(content):
    try:
        data = json.loads(content.preview_manifest or '[]')
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []

@student_bp.before_request
def guard():
    if not current_user.is_authenticated:return redirect(url_for('auth.login',next='/aluno/'))
    if current_user.is_admin:abort(403)
@student_bp.route('/', methods=['GET'])
def dashboard():
    series = Series.query.order_by(Series.id).all()
    total_contents = Content.query.count()
    completed = Progress.query.filter_by(user_id=current_user.id).count()
    percent = round(completed / total_contents * 100) if total_contents else 0
    favorite_count = Favorite.query.filter_by(user_id=current_user.id).count()
    unread_count = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    activities = Activity.query.order_by(Activity.id.desc()).all()
    attempts = ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    attempted_ids = {a.activity_id for a in attempts}
    pending_activities = sum(1 for a in activities if a.id not in attempted_ids and not (a.due_at and datetime.utcnow() > a.due_at))
    average = round(sum(a.score for a in attempts) / len(attempts), 1) if attempts else None
    recent_contents = Content.query.order_by(Content.id.desc()).limit(5).all()
    recent_activities = activities[:5]
    recent_attempts = attempts[:5]
    recent_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(4).all()
    completed_ids = {p.content_id for p in Progress.query.filter_by(user_id=current_user.id).all()}
    continue_content = next((c for c in recent_contents if c.id not in completed_ids), None)
    if continue_content is None:
        continue_content = Content.query.filter(~Content.id.in_(completed_ids)).order_by(Content.id.desc()).first() if total_contents else None
    upcoming = [a for a in activities if a.due_at and a.due_at >= datetime.utcnow() and a.id not in attempted_ids]
    upcoming = sorted(upcoming, key=lambda a: a.due_at)[:4]
    achievement_count = sum([
        completed >= 1, completed >= 5, len(attempts) >= 1, len(attempts) >= 5,
        any(round(a.score, 1) >= 10 for a in attempts), percent >= 100
    ])
    return render_template(
        'student/dashboard.html', series=series, favorites=favorite_count, completed=completed,
        notifications=unread_count, total_contents=total_contents, percent=percent,
        activities_count=len(activities), pending_activities=pending_activities, average=average,
        recent_contents=recent_contents, recent_activities=recent_activities,
        recent_attempts=recent_attempts, attempted_ids=attempted_ids, recent_notifications=recent_notifications,
        continue_content=continue_content, upcoming=upcoming, achievement_count=achievement_count
    )

@student_bp.get('/materiais')
def materials():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    query = Content.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
    if kind in {'file','pdf','slide','video','link','explanation'}: query = query.filter_by(kind=kind)
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    contents = query.order_by(Content.id.desc()).all()
    return render_template('student/materials.html', contents=contents, q=q, kind=kind, series_id=series_id, subject_id=subject_id, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all())

@student_bp.route('/perfil', methods=['GET', 'POST'])
def profile():
    if request.method == 'POST':
        name = ' '.join(request.form.get('name', '').split())
        if len(name) < 2 or len(name) > 120:
            flash('Informe um nome entre 2 e 120 caracteres.', 'error')
        else:
            current_user.name = name
            db.session.commit()
            flash('Perfil atualizado com sucesso.', 'success')
            return redirect(url_for('student.profile'))
    return render_template('student/profile.html')

@student_bp.get('/desempenho')
def performance():
    activities = Activity.query.order_by(Activity.id.desc()).all()
    attempts = ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
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
    recent_attempts = attempts[:8]
    return render_template('student/performance.html', activities=activities, by_activity=by_activity, attempts=attempts, recent_attempts=recent_attempts, average=average, completed=completed, pending=pending, completion=completion)

@student_bp.get('/notas')
def grades():
    activities = Activity.query.order_by(Activity.id.desc()).all()
    attempts = ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    by_activity = {}
    for attempt in attempts:
        item = by_activity.setdefault(attempt.activity_id, {'latest': attempt, 'best': attempt})
        if attempt.id > item['latest'].id:
            item['latest'] = attempt
        if attempt.score > item['best'].score:
            item['best'] = attempt
    graded = [item['latest'].score for item in by_activity.values()]
    average = round(sum(graded) / len(graded), 1) if graded else None
    return render_template('student/grades.html', activities=activities, by_activity=by_activity, average=average, graded_count=len(by_activity))
@student_bp.get('/serie/<int:id>')
def series(id): return render_template('student/series.html',series=Series.query.get_or_404(id))
@student_bp.get('/materia/<int:id>')
def subject(id): return render_template('student/subject.html',subject=Subject.query.get_or_404(id),activities=Activity.query.filter_by(subject_id=id).order_by(Activity.id.desc()).all(),experiments=Experiment.query.filter_by(subject_id=id).order_by(Experiment.id.desc()).all())
@student_bp.get('/conteudo/<int:id>')
def content(id):
    c=Content.query.get_or_404(id); fav=Favorite.query.filter_by(user_id=current_user.id,content_id=c.id).first(); done=Progress.query.filter_by(user_id=current_user.id,content_id=c.id).first(); return render_template('student/content.html',content=c,favorite=bool(fav),completed=bool(done),pptx_preview=bool(_preview_files(c)))
@student_bp.post('/conteudo/<int:id>/favoritar')
def toggle_favorite(id):
    c=Content.query.get_or_404(id); f=Favorite.query.filter_by(user_id=current_user.id,content_id=c.id).first()
    if f: db.session.delete(f); flash('Removido dos favoritos.','success')
    else: db.session.add(Favorite(user_id=current_user.id,content_id=c.id)); flash('Adicionado aos favoritos.','success')
    db.session.commit(); return redirect(url_for('student.content',id=id))
@student_bp.post('/conteudo/<int:id>/concluir')
def complete(id):
    c=Content.query.get_or_404(id)
    if not Progress.query.filter_by(user_id=current_user.id,content_id=c.id).first(): db.session.add(Progress(user_id=current_user.id,content_id=c.id)); db.session.commit()
    flash('Conteúdo marcado como concluído.','success'); return redirect(url_for('student.content',id=id))
@student_bp.get('/favoritos')
def favorites(): return render_template('student/favorites.html',favorites=Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.id.desc()).all())
@student_bp.get('/atividades')
def activities():
    q = request.args.get('q', '').strip()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    status = request.args.get('status', '').strip().lower()
    query = Activity.query
    if q:
        like = f'%{q}%'; query = query.filter(or_(Activity.title.ilike(like), Activity.description.ilike(like)))
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    items = query.order_by(Activity.id.desc()).all()
    attempted_ids = {a.activity_id for a in ActivityAttempt.query.filter_by(user_id=current_user.id).all()}
    now = datetime.utcnow()
    if status == 'pending': items = [a for a in items if a.id not in attempted_ids and not (a.due_at and now > a.due_at)]
    elif status == 'done': items = [a for a in items if a.id in attempted_ids]
    elif status == 'expired': items = [a for a in items if a.due_at and now > a.due_at]
    return render_template('student/activities.html', activities=items, attempted_ids=attempted_ids, now=now, q=q, series_id=series_id, subject_id=subject_id, status=status, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all())

@student_bp.route('/atividade/<int:id>',methods=['GET','POST'])
def activity(id):
    a=Activity.query.get_or_404(id); questions=a.get_questions(); now=datetime.utcnow()
    expired = bool(a.due_at and now > a.due_at)
    if request.method=='POST':
        if expired:
            flash('O prazo desta atividade já terminou.', 'error')
            return redirect(url_for('student.activity', id=a.id))
        answers={str(i):request.form.get(f'q{i}','') for i in range(len(questions))}; valid_answers={str(i): set(q.get('options') or []) for i,q in enumerate(questions)}; answers={k:v for k,v in answers.items() if v in valid_answers.get(k,set())}; correct=sum(1 for i,q in enumerate(questions) if answers.get(str(i))==q.get('correct')); total=len(questions); score=(correct/total*10) if total else 0
        attempt=ActivityAttempt(user_id=current_user.id,activity_id=a.id,answers_json=json.dumps(answers,ensure_ascii=False),score=score,total=total); db.session.add(attempt); db.session.commit(); return render_template('student/activity_result.html',activity=a,score=score,correct=correct,total=total)
    return render_template('student/activity.html',activity=a,questions=questions,expired=expired,now=now)
@student_bp.get('/experimentos')
def experiments(): return render_template('student/experiments.html',experiments=Experiment.query.order_by(Experiment.id.desc()).all())
@student_bp.get('/experimento/<int:id>')
def experiment(id): return render_template('student/experiment.html',experiment=Experiment.query.get_or_404(id))
@student_bp.get('/progresso')
def progress():
    total=Content.query.count(); completed=Progress.query.filter_by(user_id=current_user.id).count(); percent=round(completed/total*100) if total else 0
    attempts=ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    return render_template('student/progress.html',total=total,completed=completed,percent=percent,attempts=attempts)
@student_bp.get('/calendario')
def calendar_view():
    today = datetime.utcnow().date()
    try:
        year = int(request.args.get('year', today.year))
        month = int(request.args.get('month', today.month))
        if month < 1 or month > 12: raise ValueError
    except (TypeError, ValueError):
        year, month = today.year, today.month
    first_weekday, days_in_month = pycalendar.monthrange(year, month)
    # Monday=0 -> grid starts Monday and keeps six full weeks when needed.
    leading = first_weekday
    cells = []
    for i in range(leading): cells.append(None)
    for day in range(1, days_in_month + 1): cells.append(date(year, month, day))
    while len(cells) % 7: cells.append(None)
    while len(cells) < 35: cells.append(None)
    events = {}
    for activity in Activity.query.filter(Activity.due_at.isnot(None)).all():
        d = activity.due_at.date()
        if d.year == year and d.month == month:
            events.setdefault(d, []).append(activity)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return render_template('student/calendar.html', year=year, month=month, month_name=pycalendar.month_name[month],
                           cells=cells, events=events, today=today, prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month)

@student_bp.get('/conquistas')
def achievements():
    completed = Progress.query.filter_by(user_id=current_user.id).count()
    total = Content.query.count()
    attempts = ActivityAttempt.query.filter_by(user_id=current_user.id).all()
    definitions = [
        ('Primeiro passo', 'Conclua seu primeiro material.', completed >= 1, '1 material concluído'),
        ('Ritmo de estudo', 'Conclua 5 materiais.', completed >= 5, '5 materiais concluídos'),
        ('Primeira atividade', 'Responda sua primeira atividade.', len(attempts) >= 1, '1 atividade respondida'),
        ('Constância', 'Responda 5 atividades.', len(attempts) >= 5, '5 atividades respondidas'),
        ('Nota máxima', 'Alcance 10 em uma atividade.', any(round(a.score, 1) >= 10 for a in attempts), 'Nota 10'),
        ('Curso completo', 'Conclua todos os materiais disponíveis.', bool(total) and completed >= total, f'{total} materiais concluídos'),
    ]
    unlocked = sum(1 for _, _, ok, _ in definitions if ok)
    return render_template('student/achievements.html', achievements=definitions, unlocked=unlocked, total=len(definitions))

@student_bp.route('/notificacoes', methods=['GET', 'POST'])
def notifications():
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'read_all':
            Notification.query.filter_by(user_id=current_user.id, read=False).update({'read': True})
            flash('Todas as notificações foram marcadas como lidas.', 'success')
        elif action == 'read_one':
            nid = request.form.get('notification_id', '').strip()
            if nid.isdigit():
                item = Notification.query.filter_by(id=int(nid), user_id=current_user.id).first_or_404()
                item.read = True
        db.session.commit()
        return redirect(url_for('student.notifications'))
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).all()
    unread = sum(1 for item in items if not item.read)
    return render_template('student/notifications.html', notifications=items, unread=unread)
@student_bp.get('/busca')
def search():
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip().lower()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    contents, activities, experiments = [], [], []
    if q:
        like = f'%{q}%'
        if category in {'', 'content'}:
            query = Content.query.filter(or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
            if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
            if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
            contents = query.order_by(Content.id.desc()).all()
        if category in {'', 'activity'}:
            query = Activity.query.filter(or_(Activity.title.ilike(like), Activity.description.ilike(like)))
            if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
            if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
            activities = query.order_by(Activity.id.desc()).all()
        if category in {'', 'experiment'}:
            query = Experiment.query.filter(or_(Experiment.title.ilike(like), Experiment.description.ilike(like)))
            if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
            if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
            experiments = query.order_by(Experiment.id.desc()).all()
    return render_template('student/search.html', q=q, category=category, series_id=series_id, subject_id=subject_id,
                           series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all(),
                           contents=contents, activities=activities, experiments=experiments)
@student_bp.get('/pptx/<int:id>')
def pptx_view(id):
    c = Content.query.get_or_404(id)
    slides = _preview_files(c)
    if c.kind != 'file' or not c.file_name or not slides:
        abort(404)
    return render_template('student/pptx_viewer.html', content=c, slides=list(range(len(slides))))

@student_bp.get('/pptx/<int:id>/slide/<int:slide>')
def pptx_slide(id, slide):
    c = Content.query.get_or_404(id)
    slides = _preview_files(c)
    if c.kind != 'file' or slide < 0 or slide >= len(slides):
        abort(404)
    key = slides[slide]
    if not isinstance(key, str):
        abort(404)
    if b2_enabled():
        try:
            obj = get_file(key)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir slide %s do material %s: %s | %s', slide, c.id, exc.message, exc.technical)
            abort(404)
        return safe_file_response(obj, key, 'image/png')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], key, mimetype='image/png')

@student_bp.get('/arquivo/<int:id>')
def arquivo(id):
    c=Content.query.get_or_404(id)
    if c.kind not in ('file','pdf') or not c.file_name: abort(404)
    if b2_enabled():
        try:
            obj = get_file(c.file_name)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir material %s: %s | %s', c.id, exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o material', back_url=url_for('student.content', id=c.id)), 502
        return safe_file_response(obj, c.file_name, obj.get('ContentType') or 'application/octet-stream')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'],c.file_name,as_attachment=False)
@student_bp.get('/pdf/<int:id>')
def pdf(id):
    c=Content.query.get_or_404(id)
    if c.kind!='pdf' or not c.file_name: abort(404)
    if b2_enabled():
        try:
            obj = get_file(c.file_name)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir PDF %s: %s | %s', c.id, exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o PDF', back_url=url_for('student.content', id=c.id)), 502
        return safe_file_response(obj, c.file_name, 'application/pdf')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'],c.file_name,mimetype='application/pdf')
