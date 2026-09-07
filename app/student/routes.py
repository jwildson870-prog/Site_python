from flask import Blueprint,render_template,abort,send_from_directory,current_app,redirect,url_for
from flask_login import login_required,current_user
from ..models import Series,Subject,Content
student_bp=Blueprint('student',__name__,url_prefix='/aluno')
@student_bp.before_request
def guard():
    if not current_user.is_authenticated:return redirect_login()
    if current_user.is_admin:abort(403)
def redirect_login():
    from flask import redirect,url_for
    return redirect(url_for('auth.login',next='/aluno/'))
@student_bp.get('/')
def dashboard():return render_template('student/dashboard.html',series=Series.query.order_by(Series.id).all())
@student_bp.get('/serie/<int:id>')
def series(id):return render_template('student/series.html',series=Series.query.get_or_404(id))
@student_bp.get('/materia/<int:id>')
def subject(id):return render_template('student/subject.html',subject=Subject.query.get_or_404(id))
@student_bp.get('/conteudo/<int:id>')
def content(id):return render_template('student/content.html',content=Content.query.get_or_404(id))
@student_bp.get('/arquivo/<int:id>')
def arquivo(id):
    c=Content.query.get_or_404(id)
    if c.kind not in ('file','pdf') or not c.file_name: abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], c.file_name, as_attachment=False)

@student_bp.get('/pdf/<int:id>')
def pdf(id):
    c=Content.query.get_or_404(id)
    if c.kind!='pdf' or not c.file_name:abort(404)
    return send_from_directory(current_app.config['UPLOAD_FOLDER'],c.file_name,mimetype='application/pdf')
