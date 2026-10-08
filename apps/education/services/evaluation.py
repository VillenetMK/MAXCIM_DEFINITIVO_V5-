from __future__ import annotations

import json
from statistics import mean

from google.genai import types


ALLOWED_CRITERIA = {
    "comunicacion_oral": "Comunicación oral",
    "escucha_activa": "Escucha activa",
    "respeto_turnos": "Respeto de turnos",
    "coherencia": "Coherencia de las respuestas",
}


def calculate_base_metrics(turns) -> dict:
    maxcim_questions = [
        turn
        for turn in turns
        if turn.emisor == "MAXCIM"
        and (turn.id_pregunta is not None or turn.texto_transcrito.rstrip().endswith("?"))
    ]
    student_answers = [turn for turn in turns if turn.emisor == "ALUMNO"]
    graded_answers = [
        turn for turn in student_answers if turn.respuesta_correcta is not None
    ]
    correct_answers = [turn for turn in graded_answers if turn.respuesta_correcta]
    response_times = [
        turn.tiempo_respuesta_ms
        for turn in student_answers
        if turn.tiempo_respuesta_ms is not None and turn.tiempo_respuesta_ms >= 0
    ]

    question_count = len(maxcim_questions)
    answer_count = len(student_answers)
    participation = round(min(answer_count / question_count, 1) * 100, 2) if question_count else 0
    comprehension = (
        round(len(correct_answers) / len(graded_answers) * 100, 2)
        if graded_answers
        else 0
    )

    return {
        "preguntas_realizadas": question_count,
        "respuestas_registradas": answer_count,
        "respuestas_calificadas": len(graded_answers),
        "respuestas_correctas": len(correct_answers),
        "promedio_respuesta_ms": round(mean(response_times)) if response_times else None,
        "porcentaje_participacion": participation,
        "porcentaje_comprension": comprehension,
    }


def _transcript_for_evaluation(turns, max_chars: int = 18000) -> str:
    lines = []
    for turn in turns:
        speaker = "MAXCIM" if turn.emisor == "MAXCIM" else "ALUMNO"
        help_marker = " [necesitó ayuda]" if turn.necesito_ayuda else ""
        lines.append(f"{speaker}: {turn.texto_transcrito.strip()}{help_marker}")
    return "\n".join(lines)[:max_chars]


def generate_ai_assessment(client, model: str, turns, base_metrics: dict) -> dict | None:
    """Return evidence-based scores. The teacher remains the final reviewer."""
    if client is None or not turns:
        return None

    transcript = _transcript_for_evaluation(turns)
    prompt = f"""
Evalúa únicamente la interacción oral contenida en la transcripción. No diagnostiques,
no infieras condiciones personales y no penalices posibles errores de transcripción.
Asigna de 0 a 100 a estos criterios: comunicacion_oral, escucha_activa,
respeto_turnos y coherencia. Para cada criterio devuelve puntuacion y una evidencia
breve tomada de la conducta observable en la sesión. Si no hay evidencia suficiente,
usa null como puntuacion y explica por qué. Devuelve además un resumen breve y una
recomendacion_docente. La evaluación es una propuesta para revisión de la docente.

Métricas objetivas:
{json.dumps(base_metrics, ensure_ascii=False)}

Transcripción:
{transcript}

Responde solamente con JSON usando esta estructura:
{{
  "criterios": {{
    "comunicacion_oral": {{"puntuacion": 0, "evidencia": "..."}},
    "escucha_activa": {{"puntuacion": 0, "evidencia": "..."}},
    "respeto_turnos": {{"puntuacion": 0, "evidencia": "..."}},
    "coherencia": {{"puntuacion": 0, "evidencia": "..."}}
  }},
  "resumen": "...",
  "recomendacion_docente": "..."
}}
""".strip()

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    data = json.loads(response.text)
    raw_criteria = data.get("criterios") if isinstance(data, dict) else None
    if not isinstance(raw_criteria, dict):
        raise ValueError("Gemini no devolvió criterios de evaluación válidos.")

    criteria = {}
    valid_scores = []
    for key, label in ALLOWED_CRITERIA.items():
        raw = raw_criteria.get(key) or {}
        score = raw.get("puntuacion")
        if score is not None:
            score = max(0.0, min(float(score), 100.0))
            valid_scores.append(score)
        criteria[key] = {
            "nombre": label,
            "puntuacion": score,
            "evidencia": str(raw.get("evidencia") or "").strip(),
        }

    return {
        "criterios": criteria,
        "porcentaje_interaccion_oral": round(mean(valid_scores), 2) if valid_scores else None,
        "resumen": str(data.get("resumen") or "").strip(),
        "recomendacion_docente": str(data.get("recomendacion_docente") or "").strip(),
    }
