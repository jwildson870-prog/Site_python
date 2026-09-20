# Validação 5.12 — Revisão final do Portal

## Escopo

A fase 5.12 é uma revisão final, sem introduzir uma nova funcionalidade de negócio. O objetivo é verificar regressões das fases anteriores e os pontos transversais do Portal.

## Checklist

- [x] Sintaxe Python de todo o `app/` validada por AST/compilação.
- [x] Endpoints usados pelos templates conferidos estaticamente.
- [x] Permissões servidor-side de professor/aluno conferidas.
- [x] Limite de upload usa `MAX_CONTENT_LENGTH` em runtime, mantendo 25 MB como padrão.
- [x] Upload mantém allowlist, assinatura e MIME.
- [x] Erros do Backblaze são convertidos para mensagens do Portal sem exibir XML/HTML bruto ao usuário.
- [x] Manifest PWA, Service Worker, página offline e botão de instalação conferidos.
- [x] Todos os arquivos estáticos declarados no cache do Service Worker existem no pacote.
- [x] Loader de página possui caminho de encerramento por `load` e `pageshow`.
- [x] Menu lateral possui comportamento responsivo para desktop/mobile.
- [x] Banco é inicializado sem comandos destrutivos de `DROP TABLE`/`DROP DATABASE` no bootstrap.
- [x] Testes estáticos específicos da 5.12 executados.

## Execução local

Com as dependências do `requirements.txt` instaladas:

```bash
pytest -q
```

No ambiente usado para esta entrega, Flask e algumas dependências de runtime não estavam instalados; por isso, a suíte completa não pôde ser executada aqui. Os testes estáticos da 5.12 e de segurança, que não dependem do Flask, foram executados com sucesso.

## Resultado desta revisão

A revisão encontrou e corrigiu um problema de teste que ainda verificava um limite de upload fixo (`MAX_UPLOAD`) em vez da configuração real do Flask (`MAX_CONTENT_LENGTH`). A aplicação já utilizava o limite configurável; o teste foi alinhado ao comportamento atual.

Também foram ampliadas as verificações da 5.12 para PWA, arquivos do Service Worker, loader, menu lateral, permissões, armazenamento e bootstrap do banco.
