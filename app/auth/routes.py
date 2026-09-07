import os,re
from urllib.parse import urlparse
from flask import Blueprint,render_template,request,redirect,url_for,flash,session,current_app
from flask_login import login_user,logout_user,current_user,login_required
from authlib.integrations.flask_client import OAuth
from ..extensions import db
from ..models import User

auth_bp=Blueprint('auth',__name__,url_prefix='/auth'); oauth=OAuth()
def email_ok(v): return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',v))
def register_google():
    oauth.init_app(current_app); oauth.register(name='google',client_id=os.getenv('GOOGLE_CLIENT_ID'),client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',client_kwargs={'scope':'openid email profile'})
    return oauth.create_client('google')
@auth_bp.get('/')
def index(): return redirect(url_for('student.dashboard' if current_user.is_authenticated and not current_user.is_admin else 'admin.dashboard' if current_user.is_authenticated else 'home'))
@auth_bp.route('/register',methods=['GET','POST'])
def register():
    if current_user.is_authenticated:return redirect(url_for('auth.index'))
    if request.method=='POST':
        name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); p=request.form.get('password',''); c=request.form.get('confirm_password','')
        if not name or not email_ok(email) or not p: flash('Preencha os campos corretamente.','error')
        elif len(p)<8: flash('A senha deve ter pelo menos 8 caracteres.','error')
        elif p!=c: flash('As senhas não coincidem.','error')
        elif User.query.filter_by(email=email).first(): flash('Este e-mail já está cadastrado.','error')
        else:
            u=User(name=name,email=email,role='student');u.set_password(p);db.session.add(u);db.session.commit();flash('Cadastro realizado. Faça login.','success');return redirect(url_for('auth.login'))
    return render_template('auth/register.html')
@auth_bp.route('/login',methods=['GET','POST'])
def login():
    # Abrir a tela de login nunca deve colocar o usuário automaticamente
    # em uma conta já autenticada. Isso é especialmente importante no
    # desenvolvimento, quando uma sessão anterior do professor pode ter
    # ficado salva no navegador. Ao entrar novamente pela tela de login,
    # encerramos a sessão anterior e exigimos e-mail + senha.
    if request.method == 'GET':
        if current_user.is_authenticated:
            logout_user()
            session.clear()
        return render_template('auth/login.html')

    email = request.form.get('email','').strip().lower()
    password = request.form.get('password','')
    u = User.query.filter_by(email=email).first()

    if not u or not u.check_password(password):
        flash('E-mail ou senha inválidos.','error')
        return render_template('auth/login.html')

    login_user(u)
    return redirect(url_for('admin.dashboard' if u.is_admin else 'student.dashboard'))
@auth_bp.post('/logout')
@login_required
def logout():logout_user();session.clear();flash('Você saiu da conta.','success');return redirect(url_for('home'))
@auth_bp.get('/google')
def google_login():
    if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):flash('Google OAuth não configurado no .env.','error');return redirect(url_for('auth.login'))
    return register_google().authorize_redirect(os.getenv('GOOGLE_REDIRECT_URI',url_for('auth.google_callback',_external=True)))
@auth_bp.get('/google/callback')
def google_callback():
    try: token=register_google().authorize_access_token(); info=token.get('userinfo') or register_google().userinfo()
    except Exception: flash('Falha na autenticação Google.','error');return redirect(url_for('auth.login'))
    sub=info.get('sub');email=(info.get('email') or '').lower().strip()
    if not sub or not email:flash('O Google não retornou dados suficientes.','error');return redirect(url_for('auth.login'))
    u=User.query.filter((User.google_sub==sub)|(User.email==email)).first()
    if not u:u=User(name=info.get('name') or email.split('@')[0],email=email,role='student',google_sub=sub);db.session.add(u)
    else:u.google_sub=sub
    db.session.commit();login_user(u);return redirect(url_for('admin.dashboard' if u.is_admin else 'student.dashboard'))
