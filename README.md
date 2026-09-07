# Portal Python 🐍

Plataforma educacional em **Python + Flask** com áreas separadas para professor/administrador e aluno.

## Funcionalidades

### Conta e acesso
- Cadastro de aluno.
- Login e logout.
- Conta de administrador/professor configurada por variáveis de ambiente.
- Interfaces e permissões separadas: aluno não acessa o painel do professor e professor não entra na área do aluno.
- Google OAuth opcional.
- Proteção CSRF e controle de acesso no servidor.

### Área do professor
- Dashboard com estatísticas.
- Criar, editar e excluir níveis.
- Criar, editar e excluir módulos.
- Criar, editar e excluir materiais.
- Publicar explicações diretamente no portal.
- Enviar arquivos pelo computador/celular.
- Publicar PDFs, slides e arquivos.
- Colocar links externos, inclusive Google Drive/OneDrive/Dropbox.
- Colocar links de videoaulas do YouTube/Vimeo.
- Trocar arquivo ao editar um material.
- Biblioteca de materiais com ações de editar, abrir e excluir.
- Gerenciamento de usuários e permissões.

### Área do aluno
- Trilha organizada por níveis e módulos.
- Página individual de cada material.
- Leitura de explicações dentro do portal.
- Visualização de PDF.
- **Botão Baixar material** para arquivos/PDFs.
- Abertura de links e slides.
- **Videoaulas incorporadas** quando o link for compatível com YouTube/Vimeo, além do botão para abrir a página original.

### Aplicativo (PWA)
- Manifesto de aplicativo instalado.
- Service Worker.
- Botão **＋ Instalar app** quando o navegador disponibilizar a instalação.
- Ícone e atalhos para Área do aluno e Área do professor.
- Layout responsivo para celular e computador.

## Rodar localmente

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Copie `.env.example` para `.env` e execute:

```bash
python app.py
```

Abra `http://127.0.0.1:5000`.

## Professor / administrador

Credenciais padrão:

- E-mail: `professor@portalpython.com`
- Senha: `Python@2026`
- Nome: `Professor Python`

Altere essas variáveis no `.env` antes de publicar o projeto.

## Publicação

Para Render:

```bash
pip install -r requirements.txt
gunicorn --bind 0.0.0.0:$PORT app:app
```

Use PostgreSQL e armazenamento persistente para materiais em produção. Para o PWA, a aplicação deve ser servida em HTTPS em produção.
