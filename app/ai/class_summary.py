"""Geração do resumo de turma usando somente métricas já calculadas pela aplicação."""
import json

from . import TUTOR_MODEL, call_gemini


SUMMARY_SYSTEM = """Você é um assistente pedagógico para professores.
Produza um resumo curto, objetivo e factual das métricas fornecidas.
Não invente dados, alunos, causas ou informações que não estejam no conjunto recebido.
Não atribua notas, não recomende punições e não substitua a análise do professor.
Destaque tendências observáveis, pontos de atenção e aspectos positivos somente quando
forem sustentados pelos números. Se os dados forem insuficientes para uma conclusão,
diga isso claramente.
Responda em português do Brasil, com texto simples e no máximo 5 parágrafos curtos.
"""


def _compact_metrics(metrics):
    """Aceita apenas um dicionário JSON simples e limita o tamanho do contexto."""
    if not isinstance(metrics, dict):
        raise ValueError("Métricas inválidas.")

    allowed = {
        "students_analyzed", "attempts", "average", "completion",
        "below_six", "activities_analyzed", "students_with_attempt",
        "period_days", "activity_rows", "student_rows",
        "students_displayed", "activities_available", "overall_average", "at_risk",
    }
    compact = {k: metrics[k] for k in allowed if k in metrics}

    # As linhas são métricas agregadas; não enviar nomes/e-mails ou IDs.
    for key in ("activity_rows", "student_rows"):
        rows = compact.get(key)
        if isinstance(rows, list):
            safe_rows = []
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                item = {}
                for field in ("attempts", "students", "completion", "average", "best",
                              "completed", "pending", "at_risk", "low_performance"):
                    if field in row and isinstance(row[field], (int, float, bool)) or row.get(field) is None:
                        item[field] = row.get(field)
                safe_rows.append(item)
            compact[key] = safe_rows

    raw = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return raw[:16000]


def generate_class_summary(*, user_id, metrics):
    try:
        compact = _compact_metrics(metrics)
    except ValueError as exc:
        return {"ok": False, "text": "", "error": str(exc)}

    return call_gemini(
        user_id=user_id,
        feature="ai_class_summary",
        model=TUTOR_MODEL,
        system=SUMMARY_SYSTEM,
        messages=[{
            "role": "user",
            "content": "Analise somente estas métricas já calculadas pelo Portal:\n" + compact,
        }],
        timeout=20,
        metadata={"source": "precomputed_metrics"},
        max_tokens=700,
    )
