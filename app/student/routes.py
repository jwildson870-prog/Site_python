from flask import Blueprint,render_template,abort,send_from_directory,current_app,redirect,url_for,request,Response,flash
from flask_login import login_required,current_user
from sqlalchemy import or_, and_
from ..extensions import db
from ..timeutils import utcnow
from ..models import User,Series,Subject,Content,Activity,ActivityAttempt,Experiment,Favorite,Progress,Notification,WeeklyGoal,ProjectSubmission,ProjectAttachment,ProjectComment,LearningPath,LearningPathItem
from ..storage import get_file, b2_enabled, StorageError
import json
import random
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from werkzeug.utils import secure_filename
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

def _week_start(value=None):
    d = value or utcnow().date()
    return d - timedelta(days=d.weekday())

def _weekly_engagement(user_id):
    start = _week_start()
    goal = WeeklyGoal.query.filter_by(user_id=user_id, week_start=start).first()
    if goal is None:
        goal = WeeklyGoal(user_id=user_id, week_start=start, target=3)
        db.session.add(goal)
        db.session.flush()
    material_count = Progress.query.filter(Progress.user_id == user_id, Progress.completed_at >= datetime.combine(start, datetime.min.time())).count()
    attempt_count = ActivityAttempt.query.filter(ActivityAttempt.user_id == user_id, ActivityAttempt.created_at >= datetime.combine(start, datetime.min.time())).count()
    completed = material_count + attempt_count
    target = max(1, min(int(goal.target or 3), 20))
    percent = min(100, round(completed / target * 100))
    return goal, completed, target, percent

def _create_engagement_reminder(user_id):
    now = utcnow()
    week_start = _week_start()
    since = datetime.combine(week_start, datetime.min.time())
    existing = Notification.query.filter(
        Notification.user_id == user_id,
        Notification.created_at >= since,
        Notification.message.like('Lembrete de estudo:%')
    ).first()
    if existing:
        return
    activities = Activity.query.filter(Activity.archived_at.is_(None), Activity.due_at.isnot(None), Activity.due_at >= now, Activity.due_at <= now + timedelta(hours=72)).order_by(Activity.due_at.asc()).all()
    attempted = {a.activity_id for a in ActivityAttempt.query.filter_by(user_id=user_id).all()}
    pending = next((a for a in activities if a.id not in attempted), None)
    if pending:
        msg = f'Lembrete de estudo: a atividade “{pending.title}” tem prazo próximo.'
        db.session.add(Notification(user_id=user_id, message=msg, link=url_for('student.activity', id=pending.id)))
    else:
        goal, completed, target, _ = _weekly_engagement(user_id)
        if completed < target:
            db.session.add(Notification(user_id=user_id, message=f'Lembrete de estudo: sua meta semanal está em {completed}/{target}.', link=url_for('student.engagement')))
    db.session.commit()

def _preview_files(content):
    try:
        data = json.loads(content.preview_manifest or '[]')
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []

def _published_content_filter():
    now = utcnow()
    return or_(
        Content.status == 'published',
        and_(Content.status == 'scheduled', Content.scheduled_at.isnot(None), Content.scheduled_at <= now),
    )

def _visible_contents_query():
    return Content.query.filter(Content.archived_at.is_(None), _published_content_filter())

@student_bp.before_request
def guard():
    if not current_user.is_authenticated:return redirect(url_for('auth.login',next='/aluno/'))
    if current_user.is_admin:abort(403)
def _path_item_target(item):
    if item.item_type == 'content':
        return Content.query.get(item.target_id)
    if item.item_type == 'activity':
        return Activity.query.get(item.target_id)
    return Experiment.query.get(item.target_id)

def _path_item_visible(item):
    target = _path_item_target(item)
    if target is None:
        return False
    if item.item_type == 'content':
        return Content.query.filter(Content.id == target.id, Content.archived_at.is_(None), _published_content_filter()).first() is not None
    if item.item_type == 'activity':
        return target.archived_at is None
    return True

def _path_item_completed(item, user_id):
    """Calcula a conclusão da etapa respeitando a regra configurada na trilha.

    ``access`` usa o primeiro registro de interação que o sistema já persiste
    para aquele tipo de item. ``complete`` exige o estado final disponível
    no próprio fluxo do portal. Para conteúdos, o portal só registra
    conclusão explícita (Progress), então as duas regras convergem nesse tipo.
    """
    if not _path_item_visible(item):
        return False

    rule = (item.completion_rule or 'access').strip().lower()

    if item.item_type == 'content':
        # Não existe evento de simples visualização persistido para conteúdo.
        # O único estado confiável é a marcação explícita de conclusão.
        return Progress.query.filter_by(user_id=user_id, content_id=item.target_id).first() is not None

    if item.item_type == 'activity':
        attempts = ActivityAttempt.query.filter_by(
            user_id=user_id, activity_id=item.target_id
        ).order_by(ActivityAttempt.id.desc()).all()
        if not attempts:
            return False
        if rule == 'access':
            return True
        # Conclusão: todas as questões apresentadas foram respondidas.
        # Isso não inventa uma nota mínima; a nota continua sendo tratada
        # separadamente pelo fluxo de atividades.
        latest = attempts[0]
        try:
            answers = latest.get_answers()
        except AttributeError:
            answers = {}
        return latest.total > 0 and len(answers) >= latest.total

    latest = ProjectSubmission.query.filter_by(
        user_id=user_id, experiment_id=item.target_id
    ).order_by(ProjectSubmission.id.desc()).first()
    if latest is None:
        return False
    if rule == 'access':
        return True
    # Conclusão: a entrega mais recente precisa ter sido revisada pelo professor.
    # Se o aluno fizer uma nova entrega depois de uma revisão, a trilha volta a
    # aguardar a revisão dessa nova entrega.
    return (latest.status or '').strip().lower() == 'reviewed'

def _path_status(path, user_id):
    completed = [_path_item_completed(item, user_id) for item in path.items]
    by_id = {item.id: done for item, done in zip(path.items, completed)}
    unlocked = [
        item.prerequisite_id is None or by_id.get(item.prerequisite_id, False)
        for item in path.items
    ]
    total = len(path.items)
    done_count = sum(completed)
    return completed, unlocked, done_count, (round(done_count / total * 100) if total else 0)

@student_bp.get('/trilhas')
def learning_paths():
    paths=LearningPath.query.filter_by(active=True).order_by(LearningPath.id.desc()).all()
    data=[(path,*_path_status(path,current_user.id)) for path in paths]
    return render_template('student/learning_paths.html', paths=data)

@student_bp.get('/trilha/<int:id>')
def learning_path(id):
    path=LearningPath.query.filter_by(id=id,active=True).first_or_404()
    completed,unlocked,done_count,percent=_path_status(path,current_user.id)
    visible_items = {item.id for item in path.items if _path_item_visible(item)}
    return render_template('student/learning_path.html', path=path, completed=completed, unlocked=unlocked, done_count=done_count, percent=percent, visible_items=visible_items)

@student_bp.route('/', methods=['GET'])
def dashboard():
    series = Series.query.order_by(Series.id).all()
    total_contents = _visible_contents_query().count()
    completed = Progress.query.join(Content).filter(Progress.user_id == current_user.id, Content.archived_at.is_(None), _published_content_filter()).count()
    percent = round(completed / total_contents * 100) if total_contents else 0
    favorite_count = Favorite.query.filter_by(user_id=current_user.id).count()
    unread_count = Notification.query.filter_by(user_id=current_user.id, read=False).count()
    activities = Activity.query.filter(Activity.archived_at.is_(None)).order_by(Activity.id.desc()).all()
    attempts = ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    attempted_ids = {a.activity_id for a in attempts}
    pending_activities = sum(1 for a in activities if a.id not in attempted_ids and not (a.due_at and utcnow() > a.due_at))
    average = round(sum(a.score for a in attempts) / len(attempts), 1) if attempts else None
    recent_contents = _visible_contents_query().order_by(Content.id.desc()).limit(5).all()
    recent_activities = activities[:5]
    recent_attempts = attempts[:5]
    recent_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(4).all()
    completed_ids = {p.content_id for p in Progress.query.filter_by(user_id=current_user.id).all()}
    continue_content = next((c for c in recent_contents if c.id not in completed_ids), None)
    if continue_content is None:
        continue_content = _visible_contents_query().filter(~Content.id.in_(completed_ids)).order_by(Content.id.desc()).first() if total_contents else None
    upcoming = [a for a in activities if a.due_at and a.due_at >= utcnow() and a.id not in attempted_ids]
    upcoming = sorted(upcoming, key=lambda a: a.due_at)[:4]
    achievement_count = sum([
        completed >= 1, completed >= 5, len(attempts) >= 1, len(attempts) >= 5,
        any(round(a.score, 1) >= 10 for a in attempts), percent >= 100
    ])
    goal, weekly_completed, weekly_target, weekly_percent = _weekly_engagement(current_user.id)
    _create_engagement_reminder(current_user.id)
    return render_template(
        'student/dashboard.html', series=series, favorites=favorite_count, completed=completed,
        notifications=unread_count, total_contents=total_contents, percent=percent,
        activities_count=len(activities), pending_activities=pending_activities, average=average,
        recent_contents=recent_contents, recent_activities=recent_activities,
        recent_attempts=recent_attempts, attempted_ids=attempted_ids, recent_notifications=recent_notifications,
        continue_content=continue_content, upcoming=upcoming, achievement_count=achievement_count, weekly_completed=weekly_completed, weekly_target=weekly_target, weekly_percent=weekly_percent
    )

@student_bp.get('/materiais')
def materials():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    series_id = request.args.get('series_id', '').strip()
    subject_id = request.args.get('subject_id', '').strip()
    query = _visible_contents_query()
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
    activities = Activity.query.filter(Activity.archived_at.is_(None)).order_by(Activity.id.desc()).all()
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
    activities = Activity.query.filter(Activity.archived_at.is_(None)).order_by(Activity.id.desc()).all()
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
def series(id):
    series_obj = Series.query.get_or_404(id)
    visible_counts = {subject.id: _visible_contents_query().filter(Content.subject_id == subject.id).count() for subject in series_obj.subjects}
    return render_template('student/series.html', series=series_obj, visible_counts=visible_counts)
@student_bp.get('/materia/<int:id>')
def subject(id):
    subject_obj = Subject.query.get_or_404(id)
    contents = _visible_contents_query().filter(Content.subject_id == id).order_by(Content.id.desc()).all()
    return render_template('student/subject.html', subject=subject_obj, contents=contents, activities=Activity.query.filter(Activity.subject_id == id, Activity.archived_at.is_(None)).order_by(Activity.id.desc()).all(), experiments=Experiment.query.filter_by(subject_id=id).order_by(Experiment.id.desc()).all())
@student_bp.get('/conteudo/<int:id>')
def content(id):
    c=_visible_contents_query().filter(Content.id == id).first_or_404(); fav=Favorite.query.filter_by(user_id=current_user.id,content_id=c.id).first(); done=Progress.query.filter_by(user_id=current_user.id,content_id=c.id).first(); return render_template('student/content.html',content=c,favorite=bool(fav),completed=bool(done),pptx_preview=bool(_preview_files(c)))
@student_bp.post('/conteudo/<int:id>/favoritar')
def toggle_favorite(id):
    c=_visible_contents_query().filter(Content.id == id).first_or_404(); f=Favorite.query.filter_by(user_id=current_user.id,content_id=c.id).first()
    if f: db.session.delete(f); flash('Removido dos favoritos.','success')
    else: db.session.add(Favorite(user_id=current_user.id,content_id=c.id)); flash('Adicionado aos favoritos.','success')
    db.session.commit(); return redirect(url_for('student.content',id=id))
@student_bp.post('/conteudo/<int:id>/concluir')
def complete(id):
    c=_visible_contents_query().filter(Content.id == id).first_or_404()
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
    query = Activity.query.filter(Activity.archived_at.is_(None))
    if q:
        like = f'%{q}%'; query = query.filter(or_(Activity.title.ilike(like), Activity.description.ilike(like)))
    if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
    if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
    items = query.order_by(Activity.id.desc()).all()
    attempted_ids = {a.activity_id for a in ActivityAttempt.query.filter_by(user_id=current_user.id).all()}
    now = utcnow()
    if status == 'pending': items = [a for a in items if a.id not in attempted_ids and not (a.due_at and now > a.due_at)]
    elif status == 'done': items = [a for a in items if a.id in attempted_ids]
    elif status == 'expired': items = [a for a in items if a.due_at and now > a.due_at]
    return render_template('student/activities.html', activities=items, attempted_ids=attempted_ids, now=now, q=q, series_id=series_id, subject_id=subject_id, status=status, series=Series.query.order_by(Series.id).all(), subjects=Subject.query.order_by(Subject.name).all())

def _prepare_activity_questions(activity, attempt_number):
    questions = deepcopy(activity.get_questions())
    if activity.shuffle_questions:
        random.Random(f"questions:{activity.id}:{current_user.id}:{attempt_number}").shuffle(questions)
    if activity.shuffle_options:
        for index, question in enumerate(questions):
            if question.get('kind', 'objective') == 'essay':
                continue
            options = list(question.get('options') or [])
            random.Random(f"options:{activity.id}:{current_user.id}:{attempt_number}:{index}").shuffle(options)
            question['options'] = options
    return questions

@student_bp.route('/atividade/<int:id>',methods=['GET','POST'])
def activity(id):
    a=Activity.query.filter(Activity.id == id, Activity.archived_at.is_(None)).first_or_404()
    now=utcnow()
    expired = bool(a.due_at and now > a.due_at)
    attempt_count = ActivityAttempt.query.filter_by(user_id=current_user.id, activity_id=a.id).count()
    if request.method=='POST':
        if expired:
            flash('O prazo desta atividade já terminou.', 'error')
            return redirect(url_for('student.activity', id=a.id))
        if a.max_attempts and attempt_count >= a.max_attempts:
            flash('Você atingiu o limite de tentativas desta atividade.', 'error')
            return redirect(url_for('student.activity', id=a.id))
        questions = _prepare_activity_questions(a, attempt_count + 1)
        answers = {}
        correct = 0
        objective_total = 0
        essay_total = 0
        for i, q in enumerate(questions):
            kind = q.get('kind', 'objective')
            value = request.form.get(f'q{i}', '').strip()
            if kind == 'essay':
                answers[str(i)] = value[:5000]
                essay_total += 1
            else:
                valid = set(q.get('options') or [])
                if value in valid:
                    answers[str(i)] = value
                objective_total += 1
                if answers.get(str(i)) == q.get('correct'):
                    correct += 1
        total = len(questions)
        score = (correct/objective_total*10) if objective_total else 0
        attempt=ActivityAttempt(user_id=current_user.id,activity_id=a.id,answers_json=json.dumps(answers,ensure_ascii=False),score=score,total=total,presented_questions_json=json.dumps(questions,ensure_ascii=False))
        db.session.add(attempt); db.session.commit()
        return render_template('student/activity_result.html',activity=a,score=score,correct=correct,total=total,objective_total=objective_total,essay_total=essay_total,review_allowed=a.allow_review,answers=answers,questions=questions,attempt_number=attempt_count+1)
    if a.max_attempts and attempt_count >= a.max_attempts:
        return render_template('student/activity.html',activity=a,questions=[],expired=expired,now=now,attempt_count=attempt_count,attempts_remaining=0,attempts_blocked=True)
    questions = _prepare_activity_questions(a, attempt_count + 1)
    attempts_remaining = (a.max_attempts - attempt_count) if a.max_attempts else None
    return render_template('student/activity.html',activity=a,questions=questions,expired=expired,now=now,attempt_count=attempt_count,attempts_remaining=attempts_remaining,attempts_blocked=False)
@student_bp.get('/experimentos')
def experiments(): return render_template('student/experiments.html',experiments=Experiment.query.order_by(Experiment.id.desc()).all())
@student_bp.route('/experimento/<int:id>', methods=['GET', 'POST'])
def experiment(id):
    experiment = Experiment.query.get_or_404(id)
    submission = ProjectSubmission.query.filter_by(experiment_id=experiment.id, user_id=current_user.id).first()
    if request.method == 'POST':
        action = request.form.get('action', 'submit')
        if action == 'submit':
            content = request.form.get('content', '').strip()
            if not content and not request.files.get('attachment'):
                flash('Escreva sua resposta ou envie um arquivo.', 'error')
                return redirect(url_for('student.experiment', id=id))
            if submission is None:
                submission = ProjectSubmission(experiment_id=experiment.id, user_id=current_user.id, content=content)
                db.session.add(submission); db.session.flush()
            else:
                submission.content = content
                submission.status = 'submitted'
                submission.teacher_feedback = None
                submission.score = None
            attachment = request.files.get('attachment')
            if attachment and attachment.filename:
                original = secure_filename(attachment.filename)
                ext = Path(original).suffix.lower().lstrip('.')
                allowed = {'pdf','png','jpg','jpeg','webp','gif','doc','docx','ppt','pptx','txt','zip'}
                if not original or ext not in allowed or len(original) > 180:
                    flash('Tipo de arquivo não permitido.', 'error')
                    db.session.rollback()
                    return redirect(url_for('student.experiment', id=id))
                try:
                    key = storage_upload(attachment, original, attachment.mimetype)
                except StorageError as exc:
                    db.session.rollback()
                    return render_template('error.html', message=exc.message, error_title='Não foi possível enviar o anexo', back_url=url_for('student.experiment', id=id)), 502
                db.session.add(ProjectAttachment(submission_id=submission.id, filename=original, storage_key=key, content_type=attachment.mimetype))
            db.session.commit()
            flash('Entrega enviada ao professor.', 'success')
            return redirect(url_for('student.experiment', id=id))
        if action == 'comment':
            body = request.form.get('body', '').strip()
            if body:
                if submission is None:
                    submission = ProjectSubmission(experiment_id=experiment.id, user_id=current_user.id, content='')
                    db.session.add(submission); db.session.flush()
                db.session.add(ProjectComment(submission_id=submission.id, user_id=current_user.id, body=body, status='visible'))
                db.session.commit()
                flash('Comentário enviado.', 'success')
            return redirect(url_for('student.experiment', id=id))
    comments = ProjectComment.query.filter_by(submission_id=submission.id, status='visible').order_by(ProjectComment.created_at.asc()).all() if submission else []
    return render_template('student/experiment.html', experiment=experiment, submission=submission, comments=comments, rubric=experiment.rubric)

@student_bp.get('/projeto/anexo/<int:id>')
def project_attachment(id):
    attachment = ProjectAttachment.query.get_or_404(id)
    submission = attachment.submission
    if submission.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if b2_enabled():
        try:
            obj = get_file(attachment.storage_key)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir anexo do projeto %s: %s | %s', attachment.id, exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o anexo', back_url=url_for('student.experiment', id=submission.experiment_id)), 502
        return safe_file_response(obj, attachment.filename, attachment.content_type or 'application/octet-stream')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], attachment.storage_key, as_attachment=True, download_name=attachment.filename, mimetype=attachment.content_type or None)
@student_bp.get('/progresso')
def progress():
    total=_visible_contents_query().count(); completed=Progress.query.filter_by(user_id=current_user.id).count(); percent=round(completed/total*100) if total else 0
    attempts=ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    return render_template('student/progress.html',total=total,completed=completed,percent=percent,attempts=attempts)
@student_bp.get('/calendario')
def calendar_view():
    today = utcnow().date()
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
    for activity in Activity.query.filter(Activity.archived_at.is_(None), Activity.due_at.isnot(None)).all():
        d = activity.due_at.date()
        if d.year == year and d.month == month:
            events.setdefault(d, []).append(activity)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return render_template('student/calendar.html', year=year, month=month, month_name=pycalendar.month_name[month],
                           cells=cells, events=events, today=today, prev_year=prev_year, prev_month=prev_month,
                           next_year=next_year, next_month=next_month)

@student_bp.route('/metas', methods=['GET', 'POST'])
def engagement():
    goal, completed, target, percent = _weekly_engagement(current_user.id)
    if request.method == 'POST':
        try:
            value = int(request.form.get('target', '3'))
        except (TypeError, ValueError):
            value = 0
        if value < 1 or value > 20:
            flash('Escolha uma meta entre 1 e 20 ações por semana.', 'error')
        else:
            goal.target = value
            db.session.commit()
            flash('Meta semanal atualizada.', 'success')
            return redirect(url_for('student.engagement'))
    now = utcnow()
    pending = []
    attempted_ids = {a.activity_id for a in ActivityAttempt.query.filter_by(user_id=current_user.id).all()}
    for activity in Activity.query.filter(Activity.archived_at.is_(None), Activity.due_at.isnot(None), Activity.due_at >= now).order_by(Activity.due_at.asc()).all():
        if activity.id not in attempted_ids:
            pending.append(activity)
    upcoming = pending[:8]
    return render_template('student/engagement.html', goal=goal, completed=completed, target=target, percent=percent, pending=pending, upcoming=upcoming, now=now)

def _attempt_correct_answers(attempt):
    """Conta apenas respostas objetivas corretas registradas na tentativa."""
    answers = attempt.get_answers()
    questions = attempt.get_presented_questions()
    return sum(
        1 for index, question in enumerate(questions)
        if question.get('kind', 'objective') != 'essay'
        and answers.get(str(index)) == question.get('correct')
    )

@student_bp.get('/ranking')
def ranking():
    """Ranking semanal por acertos nas questões das atividades.

    Cada questão objetiva acertada vale 10 pontos. Questões discursivas
    não entram automaticamente na pontuação.
    """
    start = datetime.combine(_week_start(), datetime.min.time())
    students = User.query.filter(User.role == 'student').order_by(User.name.asc()).all()
    rows = []
    for student in students:
        attempts = ActivityAttempt.query.filter(
            ActivityAttempt.user_id == student.id,
            ActivityAttempt.created_at >= start
        ).all()
        correct_answers = sum(_attempt_correct_answers(attempt) for attempt in attempts)
        activities = len(attempts)
        points = correct_answers * 10
        rows.append({
            'student': student,
            'points': points,
            'correct_answers': correct_answers,
            'activities': activities,
        })
    rows.sort(key=lambda row: (-row['points'], -row['correct_answers'], row['student'].name.casefold()))
    for position, row in enumerate(rows, start=1):
        row['position'] = position
    current = next((row for row in rows if row['student'].id == current_user.id), None)
    return render_template('student/ranking.html', rows=rows, current=current, week_start=start.date())


@student_bp.get('/conquistas')
def achievements():
    completed = Progress.query.join(Content).filter(Progress.user_id == current_user.id, Content.archived_at.is_(None), _published_content_filter()).count()
    total = _visible_contents_query().count()
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
            query = Content.query.filter(Content.archived_at.is_(None), _published_content_filter(), or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
            if series_id.isdigit(): query = query.filter_by(series_id=int(series_id))
            if subject_id.isdigit(): query = query.filter_by(subject_id=int(subject_id))
            contents = query.order_by(Content.id.desc()).all()
        if category in {'', 'activity'}:
            query = Activity.query.filter(Activity.archived_at.is_(None), or_(Activity.title.ilike(like), Activity.description.ilike(like)))
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
    c = _visible_contents_query().filter(Content.id == id).first_or_404()
    slides = _preview_files(c)
    if c.kind != 'file' or not c.file_name or not slides:
        abort(404)
    return render_template('student/pptx_viewer.html', content=c, slides=list(range(len(slides))))

@student_bp.get('/pptx/<int:id>/slide/<int:slide>')
def pptx_slide(id, slide):
    c = _visible_contents_query().filter(Content.id == id).first_or_404()
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
    c=_visible_contents_query().filter(Content.id == id).first_or_404()
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
    c=_visible_contents_query().filter(Content.id == id).first_or_404()
    if c.kind!='pdf' or not c.file_name: abort(404)
    if b2_enabled():
        try:
            obj = get_file(c.file_name)
        except StorageError as exc:
            current_app.logger.warning('Falha ao abrir PDF %s: %s | %s', c.id, exc.message, exc.technical)
            return render_template('error.html', message=exc.message, error_title='Não foi possível abrir o PDF', back_url=url_for('student.content', id=c.id)), 502
        return safe_file_response(obj, c.file_name, 'application/pdf')
    return send_from_directory(current_app.config['UPLOAD_FOLDER'],c.file_name,mimetype='application/pdf')
