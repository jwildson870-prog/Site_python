from datetime import datetime, timezone


def utcnow():
    """Retorna o instante atual em UTC.

    Substitui o uso descontinuado de ``datetime.utcnow()`` (removido em
    versões futuras do Python) por ``datetime.now(timezone.utc)``, mas
    remove o ``tzinfo`` antes de retornar para manter compatibilidade com
    as colunas ``db.DateTime`` existentes, que são "naive" (sem fuso).
    Isso preserva exatamente o comportamento de datas já gravado no banco.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
