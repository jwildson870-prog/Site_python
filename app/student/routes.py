from flask import Blueprint,render_template,abort,send_from_directory,current_app,redirect,url_for,request,Response,flash
from flask_login import login_required,current_user
from sqlalchemy import or_
from ..extensions import db
from ..models import Series,Subject,Content,Activity,ActivityAttempt,Experiment,Favorite,Progress,Notification
from ..storage import get_file, b2_enabled, StorageError
import json
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
    pending_activities = sum(1 for a in activities if a.id not in attempted_ids)
    average = round(sum(a.score for a in attempts) / len(attempts), 1) if attempts else None
    recent_contents = Content.query.order_by(Content.id.desc()).limit(5).all()
    recent_activities = activities[:5]
    recent_attempts = attempts[:5]
    recent_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).limit(4).all()
    return render_template(
        'student/dashboard.html', series=series, favorites=favorite_count, completed=completed,
        notifications=unread_count, total_contents=total_contents, percent=percent,
        activities_count=len(activities), pending_activities=pending_activities, average=average,
        recent_contents=recent_contents, recent_activities=recent_activities,
        recent_attempts=recent_attempts, attempted_ids=attempted_ids, recent_notifications=recent_notifications
    )

@student_bp.get('/materiais')
def materials():
    q = request.args.get('q', '').strip()
    kind = request.args.get('kind', '').strip().lower()
    query = Content.query
    if q:
        like = f'%{q}%'
        query = query.filter(or_(Content.title.ilike(like), Content.description.ilike(like), Content.body.ilike(like)))
    if kind in {'file','pdf','slide','video','link','explanation'}:
        query = query.filter_by(kind=kind)
    contents = query.order_by(Content.id.desc()).all()
    return render_template('student/materials.html', contents=contents, q=q, kind=kind)

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
    c=Content.query.get_or_404(id); fav=Favorite.query.filter_by(user_id=current_user.id,content_id=c.id).first(); done=Progress.query.filter_by(user_id=current_user.id,content_id=c.id).first(); return render_template('student/content.html',content=c,favorite=bool(fav),completed=bool(done))
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
def activities(): return render_template('student/activities.html',activities=Activity.query.order_by(Activity.id.desc()).all())
@student_bp.route('/atividade/<int:id>',methods=['GET','POST'])
def activity(id):
    a=Activity.query.get_or_404(id); questions=a.get_questions()
    if request.method=='POST':
        answers={str(i):request.form.get(f'q{i}','') for i in range(len(questions))}; valid_answers={str(i): set(q.get('options') or []) for i,q in enumerate(questions)}; answers={k:v for k,v in answers.items() if v in valid_answers.get(k,set())}; correct=sum(1 for i,q in enumerate(questions) if answers.get(str(i))==q.get('correct')); total=len(questions); score=(correct/total*10) if total else 0
        attempt=ActivityAttempt(user_id=current_user.id,activity_id=a.id,answers_json=json.dumps(answers,ensure_ascii=False),score=score,total=total); db.session.add(attempt); db.session.commit(); return render_template('student/activity_result.html',activity=a,score=score,correct=correct,total=total)
    return render_template('student/activity.html',activity=a,questions=questions)
@student_bp.get('/experimentos')
def experiments(): return render_template('student/experiments.html',experiments=Experiment.query.order_by(Experiment.id.desc()).all())
@student_bp.get('/experimento/<int:id>')
def experiment(id): return render_template('student/experiment.html',experiment=Experiment.query.get_or_404(id))
@student_bp.get('/progresso')
def progress():
    total=Content.query.count(); completed=Progress.query.filter_by(user_id=current_user.id).count(); percent=round(completed/total*100) if total else 0
    attempts=ActivityAttempt.query.filter_by(user_id=current_user.id).order_by(ActivityAttempt.id.desc()).all()
    return render_template('student/progress.html',total=total,completed=completed,percent=percent,attempts=attempts)
@student_bp.get('/notificacoes')
def notifications():
    items=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.id.desc()).all(); Notification.query.filter_by(user_id=current_user.id,read=False).update({'read':True}); db.session.commit(); return render_template('student/notifications.html',notifications=items)
@student_bp.get('/busca')
def search():
    q=request.args.get('q','').strip(); contents=[]; activities=[]; experiments=[]
    if q:
        like=f'%{q}%'; contents=Content.query.filter(or_(Content.title.ilike(like),Content.description.ilike(like),Content.body.ilike(like))).order_by(Content.id.desc()).all(); activities=Activity.query.filter(or_(Activity.title.ilike(like),Activity.description.ilike(like))).order_by(Activity.id.desc()).all(); experiments=Experiment.query.filter(or_(Experiment.title.ilike(like),Experiment.description.ilike(like))).order_by(Experiment.id.desc()).all()
    return render_template('student/search.html',q=q,contents=contents,activities=activities,experiments=experiments)
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
