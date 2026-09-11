# Portal JM – Química

Sistema educacional em Flask com cadastro, login, sessões, separação real entre PROFESSOR e ALUNO, quatro turmas oficiais (1º, 2º, 3º e 4º ano), CRUD de séries/matérias/conteúdos, uploads, explicações, PDFs, slides, vídeo-aulas, links externos e Google OAuth opcional.

## Rodar localmente

Python 3.11+ recomendado:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Copie `.env.example` para `.env`, defina uma `SECRET_KEY`, `ADMIN_EMAIL` e `ADMIN_PASSWORD`, e rode:

```bash
python app.py
```

Acesse `http://127.0.0.1:5000`.

## Professor / administrador

Por padrão, a conta pré-definida é:

- E-mail: `professor@portaljm.com`
- Senha: `PortalJM@2026`
- Nome: `Professor JM`

As variáveis `ADMIN_EMAIL`, `ADMIN_PASSWORD` e `ADMIN_NAME` podem ser configuradas no Render para trocar as credenciais sem alterar o código. O cadastro público nunca cria administrador: contas cadastradas pela tela pública são sempre ALUNO.

O login do professor redireciona diretamente para `/admin/`. No servidor, todas as rotas `/admin/*` exigem `role=admin`, enquanto `/aluno/*` bloqueia administradores. Portanto, esconder botões no frontend não é a única proteção. O cadastro público sempre cria ALUNO.

Também existe o comando:

```bash
flask --app app.py create-admin
```

## Google OAuth

Crie um cliente OAuth no Google Cloud Console e configure a URI de callback exatamente como `GOOGLE_REDIRECT_URI`. Depois preencha `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` no `.env`. Em produção use HTTPS e a URL pública.

## Banco

Local: SQLite. Produção: PostgreSQL. Exemplo:

```env
DATABASE_URL=postgresql+psycopg2://usuario:senha@host:5432/banco
```

## PDFs

No desenvolvimento os PDFs ficam em `uploads/`. Em hospedagem com filesystem efêmero, use armazenamento persistente (disco persistente ou bucket externo). O banco guarda a referência do arquivo, permitindo trocar a camada de armazenamento depois.

## Render

Build: `pip install -r requirements.txt`  
Start: `gunicorn --bind 0.0.0.0:$PORT app:app`

O `Procfile` já está configurado com esse comando.

Configure no painel do Render `SECRET_KEY`, `DATABASE_URL`, `ADMIN_NAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` e, se usar Google, as três variáveis OAuth. Para PostgreSQL, use a URL do banco do Render.

## Segurança

Senhas são armazenadas somente como hash; CSRF é aplicado aos POST; rotas administrativas verificam o papel no servidor; cadastro não permite promoção a administrador; segredos ficam no `.env`; arquivos não executáveis são aceitos como PDF apenas.

## Publicação de materiais — painel do professor

O painel do professor mantém a interface do Portal JM e oferece uma área de publicação rápida com quatro fontes:

- **Meu dispositivo:** abre o seletor de arquivos do computador/celular e aceita PDF, imagens, PowerPoint, Word e TXT, até 25 MB.
- **Google Drive:** abre o Drive em uma nova aba para o professor escolher o arquivo e colar o link de compartilhamento no Portal JM.
- **Outro lugar:** aceita links HTTP/HTTPS de OneDrive, Dropbox, sites e outros serviços.
- **Escrever aqui:** permite publicar uma explicação diretamente no portal.

A integração do seletor oficial do Google Drive (Picker dentro do próprio Portal JM) exige credenciais/API do Google Cloud e pode ser adicionada em uma etapa posterior. O fluxo por link já funciona sem expor credenciais do Drive.

Cada fonte também tem um campo **Tipo de material**:
- Meu dispositivo → Arquivo ou PDF (PDF exige que o arquivo enviado seja realmente `.pdf`).
- Google Drive / Outro lugar → Link externo, Slides ou Vídeo.
- Escrever aqui → sempre Explicação.

A remoção de materiais permanece disponível no backend, mas foi retirada da interface desta etapa para ser trabalhada depois.


## Acesso de demonstração
Por padrão, o sistema usa `professor@portaljm.com` / `PortalJM@2026` como administrador. Em produção, recomenda-se alterar essas variáveis no Render.


## Quatro anos e compatibilidade com dados existentes

O banco continua usando a tabela `series`; 1º, 2º, 3º e 4º ano são registros reais relacionados a `subjects` e `contents`. Na inicialização, nomes legados como `1ª Série`/`4ª Série` são migrados para `1º ano`/`4º ano` sem apagar os IDs, matérias ou materiais existentes. Nenhuma tabela é recriada ou apagada.

## Testes

A suíte em `tests/test_app.py` cobre isolamento professor/aluno, redirecionamento do login, quatro séries, criação de material no 4º ano, link externo e validação/upload de PDF.

## Recursos adicionados nesta versão
- Atividades interativas de múltipla escolha com correção automática e nota de 0 a 10.
- Experimentos de Química com objetivo, materiais, passo a passo, segurança e conclusão.
- Favoritos por aluno.
- Marcação de conteúdos concluídos.
- Notificações para alunos quando materiais, atividades e experimentos são publicados.
- Busca de conteúdos, atividades e experimentos.
- Gerenciamento de alunos pelo professor (exclusão de contas de aluno).
- Visualização de PDF diretamente na página de conteúdo.
- Botão de exclusão de materiais corrigido.
- Dashboard do professor e do aluno ampliados.

### Google Drive
O Portal JM mantém o fluxo seguro de compartilhar links do Google Drive. Um Google Drive Picker totalmente integrado exige credenciais OAuth/Picker configuradas no Google Cloud e não deve usar uma chave pública embutida no código.


## Uploads persistentes no Render

O Portal JM salva os arquivos enviados em `UPLOAD_FOLDER`. Em desenvolvimento, o padrão continua sendo `uploads/`. No Render, configure `UPLOAD_FOLDER` para o mesmo caminho usado como **Mount Path** de um Persistent Disk, por exemplo `/var/data/uploads`.

O Render informa que o filesystem normal do serviço é efêmero; somente os arquivos dentro do Mount Path do Persistent Disk são preservados entre reinícios e deploys. Persistent Disk exige serviço pago e mantém o serviço em uma única instância.


## Arquitetura simplificada

Esta versão não usa JavaScript no site. A interface é feita com HTML e CSS e as regras, formulários, autenticação, uploads, atividades e banco de dados são processados no Flask/Python. O menu lateral no celular usa apenas HTML + CSS. O mecanismo de instalação PWA/service worker foi removido porque ele depende de JavaScript.

## Backblaze B2

Em produção, os uploads podem ser armazenados no Backblaze B2. Configure no Render as variáveis `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`, `B2_BUCKET_NAME`, `B2_ENDPOINT` e opcionalmente `B2_REGION`.

Quando as quatro primeiras estiverem preenchidas, novos arquivos enviados pelo painel do professor são gravados no B2. O banco continua guardando a referência do arquivo e o Portal JM gera URLs temporárias assinadas para abrir os arquivos com segurança, sem expor a Application Key ao navegador.

Se as variáveis B2 não estiverem configuradas, o sistema continua usando `UPLOAD_FOLDER` como armazenamento local, mantendo compatibilidade com desenvolvimento e com instalações que usam Persistent Disk.
