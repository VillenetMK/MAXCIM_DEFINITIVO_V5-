import json
import os
from datetime import date, timedelta

from app import material_progress_rows
from extensions import db
from models import Interaccion, Material, Periodo, Tema, TIPO_CUENTO

TEST_TEACHER_ID = "DOC-TEST-1"


def _periodo_con_tema(nombre="Bimestre activo", *, anio=None, start_offset=-1, end_offset=1):
    """Crea un periodo y un tema suyo; devuelve (periodo_id, tema_id).
    Todo material guardado vía API necesita ambos."""
    periodo = Periodo(
        nombre=nombre,
        anio=anio or date.today().year,
        fecha_inicio=date.today() + timedelta(days=start_offset),
        fecha_fin=date.today() + timedelta(days=end_offset),
    )
    db.session.add(periodo)
    db.session.commit()
    tema = Tema(nombre=f"Tema de {nombre}", fk_user=TEST_TEACHER_ID, id_periodo=periodo.id)
    db.session.add(tema)
    db.session.commit()
    return periodo.id, tema.id


def add_material(owner, name):
    material = Material(
        nombre_material=name,
        tipo_material=TIPO_CUENTO,
        path_audio="fixtures/audio.wav",
        path_texto="fixtures/texto.txt",
        path_texto_resumen="fixtures/resumen.txt",
        path_preguntas="fixtures/preguntas.json",
        fk_user=owner,
    )
    db.session.add(material)
    db.session.flush()
    return material


def add_interaction(material, student_id, *, correct, question, answer, appraisal, id_periodo=None):
    interaction = Interaccion(
        material=material,
        fk_alumno=student_id,
        pregunta=question,
        respuesta=answer,
        path_audio_rpta="fixtures/respuesta.wav",
        apreciacion_robot=appraisal,
        rpta_correcta=correct,
        # Igual que registrar_interaccion en producción: la interacción copia
        # el periodo del material al crearse (no se resuelve al vuelo).
        id_periodo=id_periodo if id_periodo is not None else (material.id_periodo if material else None),
    )
    db.session.add(interaction)
    return interaction


def test_classroom_student_list_renders_adapter_students(client, urls):
    response = client.get(urls.classroom("AULA-REAL-1"))
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Pérez Flores" in html
    assert "Ana Lucía" in html
    assert "Quispe Rojas" in html
    # El enlace al alumno usa el identificador opaco firmado, no los IDs crudos.
    assert urls.student("AULA-REAL-1", "ALU-TEST-1") in html
    assert "AULA-REAL-1" not in html
    assert "ALU-TEST-1" not in html


def test_teacher_cannot_open_a_classroom_that_is_not_theirs(client, urls):
    response = client.get(urls.classroom("AULA-AJENA"))

    assert response.status_code == 404
    assert "no pertenece a la docente autenticada" in response.get_data(as_text=True)


def test_a_tampered_or_foreign_ref_is_rejected(client):
    assert client.get("/aulas/no-es-un-token-firmado").status_code == 404
    assert client.get("/aulas/alumno/basura").status_code == 404


def test_a_ref_of_the_wrong_kind_is_rejected(client, urls):
    # Un token de alumno no debe abrir una ruta de aula, ni al revés.
    student_token = urls.student("AULA-REAL-1", "ALU-TEST-1").rsplit("/", 1)[-1]
    classroom_token = urls.classroom("AULA-REAL-1").rsplit("/", 1)[-1]
    # token de alumno en rutas que esperan uno de aula
    assert client.get(f"/aulas/{student_token}").status_code == 404
    assert client.get(f"/aulas/{student_token}/avance").status_code == 404
    # token de aula en la ruta que espera uno de alumno
    assert client.get(f"/aulas/alumno/{classroom_token}").status_code == 404


def test_media_token_is_bound_to_the_teacher(app, client):
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="maxcim-media")
    mine = s.dumps({"p": "x/y.txt", "t": "DOC-TEST-1"})
    other = s.dumps({"p": "x/y.txt", "t": "OTRA-DOCENTE"})
    no_owner = s.dumps({"p": "x/y.txt"})
    # El mío pasa la verificación de dueño (404 solo porque el archivo no existe).
    assert client.get(f"/media/{mine}").status_code == 404
    assert client.get(f"/media/{other}").status_code == 403
    assert client.get(f"/media/{no_owner}").status_code == 403


def test_media_token_rejects_path_escape(app, client):
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="maxcim-media")
    for bad in ("../../secret.txt", "C:/Windows/win.ini", "/etc/passwd", "//host/share/x"):
        tok = s.dumps({"p": bad, "t": "DOC-TEST-1"})
        assert client.get(f"/media/{tok}").status_code == 404


def test_material_progress_rows_groups_by_material_and_grades_severity():
    """Función pura (sin BD): agrupa por material -o "Conversación" si no hay
    uno- y clasifica cada grupo por su propio % de aciertos, no por el del
    resto. El orden de salida es el de la primera aparición de cada uno."""
    class FakeMaterial:
        def __init__(self, name):
            self.nombre_material = name

    class FakeInteraction:
        def __init__(self, id_material, material, correct):
            self.id_material = id_material
            self.material = material
            self.rpta_correcta = correct

    material_bueno = FakeMaterial("Material bueno")
    material_regular = FakeMaterial("Material regular")
    material_bajo = FakeMaterial("Material bajo")

    interactions = [
        FakeInteraction(1, material_bueno, True),
        FakeInteraction(1, material_bueno, True),
        FakeInteraction(1, material_bueno, True),
        FakeInteraction(1, material_bueno, False),  # 3/4 = 75% -> good
        FakeInteraction(2, material_regular, True),
        FakeInteraction(2, material_regular, False),  # 1/2 = 50% -> warning
        FakeInteraction(3, material_bajo, False),
        FakeInteraction(3, material_bajo, False),
        FakeInteraction(3, material_bajo, False),
        FakeInteraction(3, material_bajo, True),  # 1/4 = 25% -> danger
        FakeInteraction(None, None, True),  # Conversación: 1/1 = 100% -> good
    ]

    rows = material_progress_rows(interactions)
    by_name = {row["name"]: row for row in rows}

    assert by_name["Material bueno"] == {
        "name": "Material bueno", "is_conversation": False,
        "correct": 3, "total": 4, "percent": 75, "severity": "good",
    }
    assert by_name["Material regular"]["percent"] == 50
    assert by_name["Material regular"]["severity"] == "warning"
    assert by_name["Material bajo"]["percent"] == 25
    assert by_name["Material bajo"]["severity"] == "danger"
    assert by_name["Conversación"]["is_conversation"] is True
    assert by_name["Conversación"]["severity"] == "good"

    # Orden de aparición: el de la primera interacción con cada material.
    assert [row["name"] for row in rows] == [
        "Material bueno", "Material regular", "Material bajo", "Conversación",
    ]


def test_progress_shows_results_and_ignores_other_teacher_materials(app, client, urls):
    with app.app_context():
        own_material = add_material("DOC-TEST-1", "Cuento autorizado")
        other_material = add_material("DOC-OTRA", "Material ajeno")
        add_interaction(
            own_material,
            "ALU-TEST-1",
            correct=True,
            question="Pregunta correcta",
            answer="Respuesta correcta",
            appraisal="Muy bien.",
        )
        add_interaction(
            own_material,
            "ALU-TEST-1",
            correct=False,
            question="Pregunta incorrecta",
            answer="Respuesta incorrecta",
            appraisal="Debe intentarlo otra vez.",
        )
        add_interaction(
            other_material,
            "ALU-TEST-1",
            correct=True,
            question="Pregunta privada",
            answer="Respuesta privada",
            appraisal="No debe mostrarse.",
        )
        db.session.commit()

    response = client.get(urls.progress("AULA-REAL-1"))
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    # "Resultados por material": una barra de aciertos/total por material, no
    # una lista de cada interacción.
    assert "Cuento autorizado" in html
    assert "1/2" in html
    assert "(50%)" in html
    assert "Material ajeno" not in html


def test_progress_and_detail_label_a_materialless_turn_as_conversation(app, client, urls):
    with app.app_context():
        own_material = add_material("DOC-TEST-1", "Cuento del búho")
        add_interaction(
            own_material,
            "ALU-TEST-1",
            correct=True,
            question="¿Quién ayudó al búho?",
            answer="Sus amigos del bosque.",
            appraisal="Identificó el dato del texto.",
        )
        # Two free conversations (id_material NULL): one right, one wrong, so the
        # tally has to fold them in next to the material-backed turn.
        add_interaction(
            None,
            "ALU-TEST-1",
            correct=True,
            question="¿De qué quieres hablar hoy?",
            answer="De mi mascota nueva.",
            appraisal="Conversación fluida y respetuosa.",
        )
        add_interaction(
            None,
            "ALU-TEST-1",
            correct=False,
            question="¿Cómo te sentiste en el recreo?",
            answer="No sé.",
            appraisal="Respuestas muy cortas.",
        )
        db.session.commit()

    progress = client.get(urls.progress("AULA-REAL-1"))
    progress_html = progress.get_data(as_text=True)
    assert progress.status_code == 200
    # Material-less turns are labelled "Conversación"; the material one keeps its name.
    assert "Conversación" in progress_html
    assert "Cuento del búho" in progress_html
    # The "Aciertos" tally counts the two conversation rows alongside the
    # material row: 2 correct out of 3.
    assert "2/3" in progress_html
    # Conversation bucket: 1 correct of 2 (the material bucket is 1/1, so
    # "1/2" is unambiguous here). Never the literal "None" as a label.
    assert "1/2" in progress_html
    assert ">None<" not in progress_html
    assert 'title="None' not in progress_html
    assert "Conversación: None" not in progress_html

    detail = client.get(urls.student("AULA-REAL-1", "ALU-TEST-1"))
    detail_html = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "Conversación" in detail_html
    assert "De mi mascota nueva." in detail_html
    assert "¿De qué quieres hablar hoy?" in detail_html
    # The material-backed turn still shows its material name and question.
    assert "Cuento del búho" in detail_html
    assert "Sus amigos del bosque." in detail_html
    assert "<strong>None</strong>" not in detail_html


def test_student_detail_shows_question_answer_and_robot_appraisal(app, client, urls):
    with app.app_context():
        material = add_material("DOC-TEST-1", "El bosque que escucha")
        add_interaction(
            material,
            "ALU-TEST-1",
            correct=True,
            question="¿Qué hizo Luna para ayudar?",
            answer="Escuchó a sus amigos.",
            appraisal="Respuesta clara y completa.",
        )
        db.session.commit()

    response = client.get(urls.student("AULA-REAL-1", "ALU-TEST-1"))
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "¿Qué hizo Luna para ayudar?" in html
    assert "Escuchó a sus amigos." in html
    assert "Respuesta clara y completa." in html
    # El audio de la respuesta se muestra debajo del texto, servido por una
    # URL firmada (/media/<token>), nunca por /static/.
    assert '<audio class="interaction-answer__audio"' in html
    assert 'src="/media/' in html
    assert "/static/fixtures/respuesta.wav" not in html
    assert "El bosque que escucha" in html


def test_student_detail_shows_local_time_not_utc(app, client, urls):
    # `fecha_hora` se guarda en UTC (ver utc_now en app.py); la consola debe
    # mostrarla en hora de Perú (UTC-5), no el valor crudo almacenado. Se
    # elige una hora que cruza medianoche en UTC para que una conversión
    # ausente o incorrecta también se note en la fecha, no solo en la hora.
    from datetime import datetime

    with app.app_context():
        material = add_material("DOC-TEST-1", "Cuento de prueba")
        interaction = add_interaction(
            material,
            "ALU-TEST-1",
            correct=True,
            question="¿Pregunta?",
            answer="Respuesta.",
            appraisal="Apreciación.",
        )
        interaction.fecha_hora = datetime(2026, 8, 20, 3, 0, 0)  # 20/08 03:00 UTC
        db.session.commit()

    html = client.get(urls.student("AULA-REAL-1", "ALU-TEST-1")).get_data(as_text=True)

    # 20/08 03:00 UTC == 19/08 22:00 hora de Perú (UTC-5). El texto visible
    # debe mostrar la hora local; el atributo datetime del <time>, la UTC
    # cruda (marcada explícitamente con "Z"), no confundir una con la otra.
    assert "19/08/2026" in html
    assert "<span>22:00</span>" in html
    assert "20/08/2026" not in html
    assert "<span>03:00</span>" not in html
    assert '2026-08-20T03:00:00Z' in html


def _seed_periodos_y_temas_para_filtro(anio_pasado=None):
    """Un bimestre pasado con un tema, y el bimestre vigente hoy con dos
    temas (A y B, en ese orden alfabético). Cada tema tiene su propio
    material con una interacción, para poder distinguir en las pruebas cuál
    quedó dentro/fuera de cada filtro por defecto."""
    periodo_pasado, tema_pasado = _periodo_con_tema(
        "Bimestre pasado", anio=anio_pasado or (date.today().year - 1),
        start_offset=-200, end_offset=-100,
    )
    periodo_actual, _ = _periodo_con_tema("Bimestre vigente")
    tema_a = Tema(nombre="A - Tema vigente", fk_user=TEST_TEACHER_ID, id_periodo=periodo_actual)
    tema_b = Tema(nombre="B - Tema vigente", fk_user=TEST_TEACHER_ID, id_periodo=periodo_actual)
    db.session.add_all([tema_a, tema_b])
    db.session.commit()

    material_pasado = add_material("DOC-TEST-1", "Material del bimestre pasado")
    material_pasado.id_periodo = periodo_pasado
    material_pasado.id_tema = tema_pasado
    material_a = add_material("DOC-TEST-1", "Material del tema A")
    material_a.id_periodo = periodo_actual
    material_a.id_tema = tema_a.id
    material_b = add_material("DOC-TEST-1", "Material del tema B")
    material_b.id_periodo = periodo_actual
    material_b.id_tema = tema_b.id
    db.session.commit()

    add_interaction(material_pasado, "ALU-TEST-1", correct=True,
                     question="Pregunta del bimestre pasado", answer="r", appraisal="ap")
    add_interaction(material_a, "ALU-TEST-1", correct=True,
                     question="Pregunta del tema A", answer="r", appraisal="ap")
    add_interaction(material_b, "ALU-TEST-1", correct=False,
                     question="Pregunta del tema B", answer="r", appraisal="ap")
    db.session.commit()

    return {
        "periodo_pasado": periodo_pasado,
        "periodo_actual": periodo_actual,
        "tema_pasado": tema_pasado,
        "tema_a": tema_a.id,
        "tema_b": tema_b.id,
    }


def test_progress_and_detail_default_to_current_periodo_and_first_tema(app, client, urls):
    """Sin filtros en la URL (primera entrada a la vista) no se debe listar
    todo el historial de una: se acota al periodo vigente y, dentro de este,
    al primer tema de la docente -igual criterio que ya usa /temas."""
    with app.app_context():
        _seed_periodos_y_temas_para_filtro()

    progress_html = client.get(urls.progress("AULA-REAL-1")).get_data(as_text=True)
    # "Resultados por material" agrupa por material, no por pregunta.
    assert "Material del tema A" in progress_html
    assert "Material del tema B" not in progress_html
    assert "Material del bimestre pasado" not in progress_html

    detail_html = client.get(urls.student("AULA-REAL-1", "ALU-TEST-1")).get_data(as_text=True)
    assert "Pregunta del tema A" in detail_html
    assert "Pregunta del tema B" not in detail_html
    assert "Pregunta del bimestre pasado" not in detail_html


def test_progress_and_detail_explicit_todos_shows_full_history(app, client, urls):
    """`?periodo=` y `?tema=` presentes (aunque vacíos) son la elección
    explícita de "Todos" -distinta de no haber elegido nada todavía- y deben
    respetarse mostrando el historial completo."""
    with app.app_context():
        _seed_periodos_y_temas_para_filtro()

    html = client.get(urls.student("AULA-REAL-1", "ALU-TEST-1") + "?periodo=&tema=").get_data(as_text=True)
    assert "Pregunta del tema A" in html
    assert "Pregunta del tema B" in html
    assert "Pregunta del bimestre pasado" in html

    progress_html = client.get(urls.progress("AULA-REAL-1") + "?periodo=&tema=").get_data(as_text=True)
    assert "2/3" in progress_html  # 2 correctas (pasado + tema A) de 3 en total


def test_tema_filter_infers_its_own_periodo(app, client, urls):
    """Un `?tema=<id>` sin `?periodo=` (p.ej. un enlace guardado) debe acotar
    al periodo de ese tema, no al vigente hoy."""
    with app.app_context():
        seeded = _seed_periodos_y_temas_para_filtro()
        tema_pasado_id = seeded["tema_pasado"]

    html = client.get(f"{urls.student('AULA-REAL-1', 'ALU-TEST-1')}?tema={tema_pasado_id}").get_data(as_text=True)
    assert "Pregunta del bimestre pasado" in html
    assert "Pregunta del tema A" not in html
    assert "Pregunta del tema B" not in html


def _material_select_block(html):
    """Recorta el <select> de material del historial de un alumno, para
    revisar sus opciones sin depender de dónde más aparezca el nombre de un
    material en la página (también sale en la tabla de interacciones)."""
    start = html.index('id="materialSelect"')
    return html[start:html.index("</select>", start)]


def test_material_filter_narrows_to_one_material_within_its_tema(app, client, urls):
    """`?material=<id>` acota un nivel más allá del tema elegido: dos
    materiales del mismo tema, cada uno con su propia interacción, deben
    poder verse por separado -y el desplegable debe listar ambos, ya
    acotados a ese tema (ver resolve_material_filter)."""
    with app.app_context():
        periodo_id, tema_id = _periodo_con_tema()
        material_1 = add_material("DOC-TEST-1", "Cuento de la granja")
        material_1.id_periodo = periodo_id
        material_1.id_tema = tema_id
        material_2 = add_material("DOC-TEST-1", "Cuento del bosque")
        material_2.id_periodo = periodo_id
        material_2.id_tema = tema_id
        db.session.commit()
        add_interaction(material_1, "ALU-TEST-1", correct=True,
                         question="Pregunta de la granja", answer="r", appraisal="ap")
        add_interaction(material_2, "ALU-TEST-1", correct=True,
                         question="Pregunta del bosque", answer="r", appraisal="ap")
        db.session.commit()
        material_1_id = material_1.id

    html = client.get(f"{urls.student('AULA-REAL-1', 'ALU-TEST-1')}?tema={tema_id}").get_data(as_text=True)
    select_block = _material_select_block(html)
    assert "disabled" not in select_block
    assert "Cuento de la granja" in select_block
    assert "Cuento del bosque" in select_block
    assert "Pregunta de la granja" in html
    assert "Pregunta del bosque" in html

    filtered = client.get(
        f"{urls.student('AULA-REAL-1', 'ALU-TEST-1')}?tema={tema_id}&material={material_1_id}"
    ).get_data(as_text=True)
    assert "Pregunta de la granja" in filtered
    assert "Pregunta del bosque" not in filtered


def test_material_select_stays_empty_and_disabled_without_a_tema(app, client, urls):
    """Sin tema elegido no hay de dónde listar materiales -el desplegable
    queda deshabilitado y sin más opción que "Todos los materiales", aunque
    la docente sí tenga materiales en otros temas."""
    with app.app_context():
        _seed_periodos_y_temas_para_filtro()

    html = client.get(f"{urls.student('AULA-REAL-1', 'ALU-TEST-1')}?periodo=&tema=").get_data(as_text=True)
    select_block = _material_select_block(html)
    assert "disabled" in select_block
    assert select_block.count('<option value="') == 1  # solo "Todos los materiales"


def _stored_upload(app, stored_path):
    """Ruta en disco de un archivo de material (path guardado con prefijo
    histórico `uploads/`, ahora relativo a UPLOADS_ROOT)."""
    rel = stored_path[len("uploads/"):] if stored_path.startswith("uploads/") else stored_path
    return os.path.join(app.config["UPLOADS_ROOT"], rel)


def test_saving_sentence_material_writes_a_json_list(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_id, tema_id = _periodo_con_tema()

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Oraciones de práctica",
            "sentences_json": json.dumps(["La luna brilla.", "El río canta."]),
            "id_periodo": str(periodo_id),
            "id_tema": str(tema_id),
        },
    )

    assert response.status_code == 200
    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        assert material.tipo_material == "oracion"
        assert material.path_preguntas.startswith("uploads/")
        assert material.path_preguntas.endswith("/oraciones.json")
        assert material.path_texto is None
        assert material.path_texto_resumen is None
        assert material.path_audio is None

        with open(_stored_upload(app, material.path_preguntas), "r", encoding="utf-8") as f:
            assert json.load(f) == ["La luna brilla.", "El río canta."]


def test_saving_material_without_id_periodo_falls_back_to_active_periodo(app, client, tmp_path, monkeypatch):
    """Cliente antiguo (o llamada externa) que no manda el campo: se usa el
    periodo vigente hoy por fecha, igual que antes de que el modal empezara
    a mandarlo explícitamente."""
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        active_id, tema_id = _periodo_con_tema(nombre="Bimestre activo")

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Sin campo id_periodo",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_tema": str(tema_id),
        },
    )

    assert response.status_code == 200
    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        assert material.id_periodo == active_id


def test_saving_material_with_explicit_empty_id_periodo_is_rejected(app, client, tmp_path, monkeypatch):
    """Ningún material puede quedar sin periodo: si el modal manda "" (o no
    hay periodo vigente para el fallback), se rechaza con 400."""
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        _periodo_con_tema(nombre="Bimestre activo")

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Sin periodo a propósito",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": "",
        },
    )

    assert response.status_code == 400
    assert "periodo" in response.get_json()["error"].lower()


def test_saving_material_with_explicit_id_periodo_uses_it_even_if_not_active(app, client, tmp_path, monkeypatch):
    """Preparar material para un periodo futuro (o pasado) con anticipación:
    el id explícito manda por sobre el periodo vigente por fecha."""
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        _periodo_con_tema(nombre="Bimestre activo")
        future_id, future_tema_id = _periodo_con_tema(
            nombre="Bimestre futuro",
            anio=date.today().year + 1,
            start_offset=200,
            end_offset=260,
        )

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Preparado con anticipación",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": str(future_id),
            "id_tema": str(future_tema_id),
        },
    )

    assert response.status_code == 200
    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        assert material.id_periodo == future_id


def test_saving_material_without_a_tema_is_rejected(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_id, _tema_id = _periodo_con_tema()

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Sin tema",
            "sentences_json": json.dumps(["La luna brilla."]),
            "id_periodo": str(periodo_id),
        },
    )

    assert response.status_code == 400
    assert "tema" in response.get_json()["error"].lower()


def test_saving_sentence_material_still_accepts_raw_text(app, client, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "UPLOADS_ROOT", str(tmp_path))
    with app.app_context():
        periodo_id, tema_id = _periodo_con_tema()

    response = client.post(
        "/api/material/save",
        data={
            "tipo_material": "oracion",
            "title": "Oraciones heredadas",
            "sentences_text": "La luna brilla.\nEl río canta.",
            "id_periodo": str(periodo_id),
            "id_tema": str(tema_id),
        },
    )

    assert response.status_code == 200
    with app.app_context():
        material = db.session.get(Material, response.get_json()["material_id"])
        with open(_stored_upload(app, material.path_preguntas), "r", encoding="utf-8") as f:
            assert json.load(f) == ["La luna brilla.", "El río canta."]
