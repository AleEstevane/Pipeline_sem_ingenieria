# -*- coding: utf-8 -*-
"""
UACJ · Semana de Ingeniería — sitio web (Flask)

Todo el sitio (datos, lógica, plantillas y estilos) vive en este único archivo.
Los datos se leen tal cual de los CSV generados por el pipeline ETL
(PROYECTO_PIPELINE/Entregable_ETL_DB/*.csv); no se modifica ningún archivo original.

Ejecutar local:   python app.py
Producción:       gunicorn app:app
"""
import csv
import hashlib
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, time
from types import SimpleNamespace
from urllib.parse import urlencode

from flask import (Flask, Response, abort, redirect, render_template, request,
                   session, url_for)
from jinja2 import DictLoader
from markupsafe import Markup

# ════════════════════════════════════════════════════════════════════
#  1. CARGA Y MODELADO DE DATOS (CSV del ETL)
# ════════════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.join(BASE_DIR, "PROYECTO_PIPELINE", "Entregable_ETL_DB"))


def leer(nombre):
    with open(os.path.join(DATA_DIR, nombre), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def limpia(t):
    return re.sub(r"\s+", " ", t or "").strip()


def norm(t):
    """minúsculas y sin acentos, para búsquedas."""
    t = unicodedata.normalize("NFD", t or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


DIAS = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
DIAS_LARGO = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

CAMPUS_NOMBRE = {"IIT": "IIT", "CU": "CU", "CUAUH": "Cuauhtémoc"}
CAMPUS_LARGO = {"IIT": "Instituto de Ingeniería y Tecnología", "CU": "Ciudad Universitaria",
                "CUAUH": "Campus Cuauhtémoc"}

# Grupos visuales por tipo de actividad -------------------------------------------------
GRUPO_TIPO = {
    "Conferencia": "dialogo", "Conferencia magistral": "dialogo", "Ponencia": "dialogo",
    "Foro": "dialogo", "Panel": "dialogo", "Mesa de diálogo": "dialogo",
    "Presentación": "dialogo", "Cátedra Patrimonial": "dialogo",
    "Taller": "practica", "Curso": "practica", "Capacitación": "practica",
    "Visita de Campo": "practica",
    "Concurso": "competencia",
    "Exposicion": "expo", "FOSEEI": "expo",
    "Deportiva": "cultural", "Cultural": "cultural", "Otra": "cultural",
}
GRUPOS = {
    "dialogo": dict(nombre="Conferencias y diálogo", var=0,
                    lema="Ideas, ponencias y conversaciones con especialistas."),
    "competencia": dict(nombre="Competencias", var=1,
                        lema="Retos para poner a prueba tu ingenio."),
    "practica": dict(nombre="Formación práctica", var=2,
                     lema="Aprende haciendo: herramientas, software y laboratorio."),
    "expo": dict(nombre="Exposiciones", var=3,
                 lema="Proyectos y trabajos que muestran lo que se construye."),
    "cultural": dict(nombre="Cultural y deportiva", var=4,
                     lema="Actividades para convivir, moverse y conocer el entorno."),
}


def nombre_salon(desc):
    """Limpia nombres de lugar (el ETL dejó textos duplicados/truncados)."""
    d = limpia(desc)
    if d in ("", "—", "-"):
        return None
    m = re.match(r"^([A-ZÁÉÍÓÚÑ/]+(?: [A-ZÁÉÍÓÚÑ/]+)*) ([A-ZÁÉÍÓÚÑ][a-záéíóúñ].*)$", d)
    if m and m.group(1).split()[0].lower() == m.group(2).split()[0].lower():
        pref, resto = m.groups()
        if len(pref.split()) == 1:
            return resto
        peq = {"de", "la", "del", "los", "las", "y"}
        return " ".join(w.lower() if w.lower() in peq else w.capitalize()
                        for w in pref.split())
    return d


def tipo_salon(nombre, ubic):
    n = norm(nombre)
    if "macroaula" in n:
        return "Auditorio"
    if any(k in n for k in ("vestibulo", "plaza", "explanada", "estacionamiento")):
        return "Explanada"
    if any(k in n for k in ("cancha", "estadio", "polideportivo", "pista")):
        return "Deportivo"
    if "biblioteca" in n:
        return "Biblioteca"
    return {"Aula": "Aula", "Audiovisual": "Audiovisual", "Laboratorio": "Laboratorio"}.get(
        ubic, ubic or "Aula")


def plataforma(link):
    l = (link or "").lower()
    if "teams" in l:
        return "Microsoft Teams"
    if "zoom" in l:
        return "Zoom"
    if "meet.google" in l:
        return "Google Meet"
    return "Enlace de reunión" if link else ""


def h12(t):
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}", ("a. m." if t.hour < 12 else "p. m.")


def rango_horas(ini, fin):
    a, sa = h12(ini)
    b, sb = h12(fin)
    return f"{a} – {b} {sb}" if sa == sb else f"{a} {sa} – {b} {sb}"


def mins(t):
    return t.hour * 60 + t.minute


def cargar():
    tipos = {r["Tipo_actividad_id"]: r["Nombre_tipo"] for r in leer("TIPO_ACTIVIDAD.csv")}
    deptos = {r["Departamento_id"]: r["Nombre_departamento"] for r in leer("DEPARTAMENTO.csv")}
    empresas = {r["Empresa_id"]: r["Nombre"] for r in leer("EMPRESA_ORG.csv")}

    # Lugares → salones únicos (el CSV trae una fila de LUGAR por sesión)
    salones = {}
    lugar_a_salon = {}
    for r in leer("LUGAR.csv"):
        nom = nombre_salon(r["Descripcion_lugar"])
        if nom is None:
            lugar_a_salon[r["Lugar_id"]] = None
            continue
        key = (r["Campus"], nom)
        if key not in salones:
            sid = "s" + hashlib.md5(f"{key[0]}|{key[1]}".encode()).hexdigest()[:6]
            salones[key] = SimpleNamespace(
                id=sid, nombre=nom, campus=r["Campus"], campus_nombre=CAMPUS_NOMBRE.get(r["Campus"], r["Campus"]),
                tipo=tipo_salon(nom, r["Ubicacion"]), cupo=int(r["Cupo"] or 0), sesiones=[])
        lugar_a_salon[r["Lugar_id"]] = salones[key]
    salones_id = {s.id: s for s in salones.values()}

    # Personas (el ETL guardó las columnas con nombre/apellido cruzados:
    # el nombre completo original = Título + Primer_nombre + Primer_apellido + Segundo_nombre + Segundo_apellido)
    personas = {}
    for r in leer("PERSONA.csv"):
        partes = [r["Primer_nombre"], r["Primer_apellido"], r["Segundo_nombre"], r["Segundo_apellido"]]
        partes = [p.strip() for p in partes if p.strip()]
        while partes and partes[-1].lower() in ("y", "/", "-", "&", "e"):
            partes.pop()
        partes = [p for p in partes if p not in ("/", "-")]
        if not partes:
            continue
        nombre = limpia(" ".join(([r["Titulo"]] if r["Titulo"] else []) + partes))
        sin_titulo = [p for p in partes if p.lower() not in ("y", "de", "del", "la")]
        ini = (sin_titulo[0][0] + (sin_titulo[-1][0] if len(sin_titulo) > 1 else "")).upper() if sin_titulo else "?"
        correo = r["Correo"].strip()
        personas[r["Persona_id"]] = SimpleNamespace(
            id=r["Persona_id"], nombre=nombre, iniciales=ini,
            correo=None if correo in ("", "N/A") else correo,
            roles=defaultdict(set), eventos=[], deptos=Counter(), tipos=Counter())

    # Eventos
    eventos = {}
    for r in leer("EVENTO.csv"):
        tipo = tipos.get(r["Tipo_actividad_id"], "Otra")
        gid = GRUPO_TIPO.get(tipo, "cultural")
        eventos[r["Evento_id"]] = SimpleNamespace(
            id=r["Evento_id"], titulo=limpia(r["Titulo_evento"]), desc=limpia(r["Descripcion_evento"]),
            tipo=tipo, grupo=gid, g=GRUPOS[gid], depto_id=r["Departamento_id"],
            depto=deptos.get(r["Departamento_id"], ""), orgs=[], ponentes=[], mcs=[], sesiones=[])
    for r in leer("LIDERA.csv"):
        e = eventos.get(r["Evento_id"])
        if e and empresas.get(r["Empresa_id"]):
            e.orgs.append(empresas[r["Empresa_id"]])
    for r in leer("PRESENTA.csv"):
        e, p = eventos.get(r["Evento_id"]), personas.get(r["Persona_id"])
        if not e or not p:
            continue
        (e.ponentes if r["Rol"] == "Ponente" else e.mcs).append(p)
        p.roles[r["Evento_id"]].add(r["Rol"])
    for p in personas.values():
        p.eventos = [eventos[i] for i in p.roles]
        p.es_ponente = any("Ponente" in r for r in p.roles.values())
        for e in p.eventos:
            p.deptos[e.depto] += 1
            p.tipos[e.tipo] += 1

    # Sesiones
    sesiones = []
    for r in leer("SESION.csv"):
        e = eventos.get(r["Evento_id"])
        if not e:
            continue
        y, mo, d = map(int, r["Fecha"].split("-"))
        hi, hf = [time(*map(int, r[k].split(":")[:2])) for k in ("Hora_inicio", "Hora_fin")]
        sal = lugar_a_salon.get(r["Lugar_id"])
        s = SimpleNamespace(
            id=r["Sesion_id"], evento=e, fecha=date(y, mo, d), ini=hi, fin=hf,
            modalidad=r["Modalidad"], link=r["Link_reunion"], plataforma=plataforma(r["Link_reunion"]),
            salon=sal, campus=sal.campus if sal else None, unica=r["Es_unica"] == "True",
            horas=rango_horas(hi, hf), dia=DIAS[date(y, mo, d).weekday()])
        e.sesiones.append(s)
        sesiones.append(s)
        if sal:
            sal.sesiones.append(s)
    for e in eventos.values():
        e.sesiones.sort(key=lambda s: (s.fecha, s.ini))
        e.n = len(e.sesiones)
        e.primera = e.sesiones[0] if e.sesiones else None
        e.campus = sorted({s.campus for s in e.sesiones if s.campus})
        e.modalidades = sorted({s.modalidad for s in e.sesiones})
        e.busqueda = norm(" ".join([e.titulo, e.desc, e.tipo, e.depto] + e.orgs +
                                   [p.nombre for p in e.ponentes + e.mcs]))
    for s in salones.values():
        s.sesiones.sort(key=lambda x: (x.fecha, x.ini, x.evento.titulo))
    eventos = {k: v for k, v in eventos.items() if v.sesiones}
    sesiones.sort(key=lambda s: (s.fecha, s.ini, s.evento.titulo))
    return SimpleNamespace(
        tipos=tipos, deptos=deptos, eventos=eventos, sesiones=sesiones,
        sesiones_id={s.id: s for s in sesiones},
        personas={k: v for k, v in personas.items() if v.eventos},
        salones=sorted(salones.values(), key=lambda s: ("IIT CU CUAUH".split().index(s.campus), norm(s.nombre))),
        salones_id=salones_id)


DB = cargar()
EVENTOS = sorted(DB.eventos.values(), key=lambda e: (e.primera.fecha, e.primera.ini, e.titulo))
PERSONAS = sorted(DB.personas.values(), key=lambda p: norm(p.nombre))

# Días del evento (los que tienen actividad) y rango "oficial" (días con ≥5 sesiones)
_cnt = Counter(s.fecha for s in DB.sesiones)
DIAS_EVENTO = sorted(_cnt)
DIAS_SEMANA = [d for d in DIAS_EVENTO if _cnt[d] >= 5] or DIAS_EVENTO
H0 = min(mins(s.ini) for s in DB.sesiones) // 60
H1 = -(-max(mins(s.fin) for s in DB.sesiones) // 60)


def fecha_larga(d):
    return f"{DIAS_LARGO[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def rango_semana():
    a, b = DIAS_SEMANA[0], DIAS_SEMANA[-1]
    return a, b


# ════════════════════════════════════════════════════════════════════
#  2. FILTROS (se aplican en el servidor con Python)
# ════════════════════════════════════════════════════════════════════
HORARIOS = {
    "manana": ("Mañana · antes de 12:00", lambda t: t.hour < 12),
    "tarde": ("Tarde · 12:00 a 18:00", lambda t: 12 <= t.hour < 18),
    "noche": ("Noche · desde 18:00", lambda t: t.hour >= 18),
}
CLAVES = ("q", "tipo", "dia", "horario", "campus", "modalidad", "depto", "lugar")


def leer_filtros(args):
    return {k: (args.get(k) or "").strip() for k in CLAVES}


def filtrar_sesiones(f):
    q = norm(f["q"])
    out = []
    for s in DB.sesiones:
        e = s.evento
        if f["tipo"] and e.tipo != f["tipo"]:
            continue
        if f["depto"] and e.depto_id != f["depto"]:
            continue
        if f["dia"] and s.fecha.isoformat() != f["dia"]:
            continue
        if f["horario"] in HORARIOS and not HORARIOS[f["horario"]][1](s.ini):
            continue
        if f["campus"] and s.campus != f["campus"]:
            continue
        if f["modalidad"] and s.modalidad != f["modalidad"]:
            continue
        if f["lugar"] and not (s.salon and s.salon.id == f["lugar"]):
            continue
        if q and q not in e.busqueda and not (s.salon and q in norm(s.salon.nombre + " " + s.salon.campus_nombre)):
            continue
        out.append(s)
    return out


def filtrar_eventos(f):
    vistos, out = set(), []
    for s in filtrar_sesiones(f):
        if s.evento.id not in vistos:
            vistos.add(s.evento.id)
            out.append(s.evento)
    out.sort(key=lambda e: (e.primera.fecha, e.primera.ini, e.titulo))
    return out


def opciones_filtros():
    return dict(
        tipos=sorted({e.tipo for e in EVENTOS}, key=norm),
        dias=[(d.isoformat(), f"{DIAS[d.weekday()]} {d.day}") for d in DIAS_EVENTO],
        horarios=[(k, v[0]) for k, v in HORARIOS.items()],
        campus=[(k, CAMPUS_NOMBRE[k]) for k in ("IIT", "CU", "CUAUH") if any(s.campus == k for s in DB.sesiones)],
        modalidades=sorted({s.modalidad for s in DB.sesiones}),
        deptos=sorted(DB.deptos.items(), key=lambda kv: norm(kv[1])),
        lugares=[(s.id, f"{s.nombre} · {s.campus_nombre}") for s in DB.salones if s.sesiones],
    )


def paginar(items, pagina, por):
    total = max(1, -(-len(items) // por))
    pagina = min(max(pagina, 1), total)
    return SimpleNamespace(items=items[(pagina - 1) * por: pagina * por], pagina=pagina,
                           total=total, n=len(items))


def entero(v, d=1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# ════════════════════════════════════════════════════════════════════
#  3. APLICACIÓN FLASK
# ════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "semana-ingenieria-uacj-dev-key")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["TEMPLATES_AUTO_RELOAD"] = False


def agenda_ids():
    return session.get("ag", [])


def registro_ids():
    return session.get("reg", [])


def qs(f=None, **extra):
    """Arma ?a=b&c=d con los filtros activos + extras (vacíos se omiten)."""
    d = {k: v for k, v in (f or {}).items() if v}
    for k, v in extra.items():
        if v in (None, ""):
            d.pop(k, None)
        else:
            d[k] = v
    return ("?" + urlencode(d)) if d else ""


def ruta_actual():
    p = request.full_path
    return p[:-1] if p.endswith("?") else p


def fmt_hora_eje(h):
    t = time(h % 24, 0)
    a, suf = h12(t)
    return f"{a} {suf}"


_ICONOS = {
    "arrow": '<path d="M4 12h15M13 6l6 6-6 6"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "cal": '<rect x="3.5" y="5" width="17" height="15" rx="1.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "user": '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c.8-4 3.5-6 7-6s6.2 2 7 6"/>',
    "bldg": '<path d="M5 21V4h9v17M14 9h5v12M3 21h18M8 8h3M8 12h3M8 16h3"/>',
    "pin": '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0113 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
    "users": '<circle cx="9" cy="8.5" r="3"/><path d="M3 20c.6-3.4 3-5 6-5s5.4 1.6 6 5M16 5.8a3 3 0 010 5.4M18 15.4c1.8.6 2.8 2.2 3 4.6"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M3.5 6.5l8.5 6.5 8.5-6.5"/>',
    "monitor": '<rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16.5V20"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "down": '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    "link": '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
}


def ico(nombre, tam=16):
    return Markup(f'<svg class="ico" width="{tam}" height="{tam}" viewBox="0 0 24 24" fill="none" '
                  f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
                  f'stroke-linejoin="round" aria-hidden="true">{_ICONOS[nombre]}</svg>')


LOGO = Markup('<svg width="34" height="30" viewBox="0 0 34 30" aria-hidden="true"><g fill="currentColor">'
              '<path d="M1 30 7 5h5L6 30z"/><path d="M12 30 17 0h5l-5 30z"/>'
              '<path d="M22 30 25 9h4l-3 21z"/><path d="M29 30l2-14h3l-2 14z"/></g></svg>')

# Ilustraciones geométricas de las tarjetas (una por grupo de actividad)
_ARTES = {
    0: ('#3155ed', '#ffffff',
        '<circle cx="178" cy="190" r="128" fill="#111"/><circle cx="178" cy="190" r="82" fill="none" stroke="#ffffff66"/>'),
    1: ('#ff7247', '#111111',
        '<polygon points="120,0 352,100 306,205 28,205" fill="#e9ff47"/>'
        '<circle cx="178" cy="190" r="82" fill="none" stroke="#11111166"/>'),
    2: ('#c8ea58', '#111111',
        '<path d="M50 205V112Q50 44 118 44H240Q308 44 308 112V205Z" fill="#3155ed"/>'
        '<circle cx="178" cy="190" r="82" fill="none" stroke="#ffffff66"/>'),
    3: ('#a99af8', '#111111',
        '<polygon points="105,0 345,0 300,205 38,205" fill="#111"/>'
        '<circle cx="178" cy="190" r="82" fill="none" stroke="#ffffff55"/>'),
    4: ('#e9ff47', '#111111',
        '<circle cx="230" cy="200" r="120" fill="#ff7247"/><circle cx="230" cy="200" r="76" fill="none" stroke="#11111166"/>'),
}


def arte(var):
    fondo, tinta, formas = _ARTES[var % len(_ARTES)]
    return Markup(f'<svg class="art-svg" viewBox="0 0 420 205" preserveAspectRatio="xMidYMid slice" '
                  f'aria-hidden="true"><rect width="420" height="205" fill="{fondo}"/>{formas}</svg>'), tinta


COLORES_AV = ["#a99af8", "#ff7247", "#3155ed", "#c8ea58"]


def avatar(i):
    fondo = COLORES_AV[i % 4]
    return Markup(f'<svg class="av-svg" viewBox="0 0 312 270" preserveAspectRatio="xMidYMid slice" aria-hidden="true">'
                  f'<rect width="312" height="270" fill="{fondo}"/><circle cx="155" cy="106" r="70" fill="#e9ff47"/>'
                  f'<path d="M40 270v-48c0-52 52-86 115-86s115 34 115 86v48z" fill="#111"/></svg>')


@app.context_processor
def globales():
    return dict(ico=ico, LOGO=LOGO, arte=arte, avatar=avatar, qs=qs, norm=norm,
                n_agenda=len(agenda_ids()), en_agenda=lambda eid: eid in agenda_ids(),
                registrado=lambda eid: eid in registro_ids(), ruta=ruta_actual,
                DIAS=DIAS, MESES=MESES, CAMPUS_NOMBRE=CAMPUS_NOMBRE, fecha_larga=fecha_larga,
                fmt_hora_eje=fmt_hora_eje, version=CSS_VERSION, rango_semana=rango_semana(),
                plataforma=plataforma, DIAS_SEMANA=DIAS_SEMANA)


def pagina(plantilla, **ctx):
    return render_template(plantilla, **ctx)


# ── Inicio ────────────────────────────────────────────────────────────
@app.route("/")
def inicio():
    destacados, usados = [], set()
    for tipo in ("Conferencia magistral", "Concurso", "Taller", "Conferencia", "Curso"):
        for e in EVENTOS:
            if e.tipo == tipo and e.id not in usados and e.ponentes:
                destacados.append(e)
                usados.add(e.id)
                break
        if len(destacados) == 3:
            break
    return pagina("inicio.html", tema="light", activa="inicio", destacados=destacados,
                  total=len(EVENTOS), n_ponentes=len(PERSONAS))


# ── Explorar ──────────────────────────────────────────────────────────
@app.route("/explorar")
def explorar():
    f = leer_filtros(request.args)
    vista = "agenda" if request.args.get("vista") == "agenda" else "tarjetas"
    evs = filtrar_eventos(f)
    pg = paginar(evs, entero(request.args.get("pagina")), 12 if vista == "tarjetas" else 40)
    por_dia = defaultdict(list)
    if vista == "agenda":
        for s in filtrar_sesiones(f):
            por_dia[s.fecha].append(s)
    return pagina("explorar.html", tema="light", activa="explorar", f=f, opc=opciones_filtros(),
                  vista=vista, pg=pg, por_dia=sorted(por_dia.items()))


# ── Detalle de actividad ───────────────────────────────────────────────
@app.route("/actividad/<eid>")
def actividad(eid):
    e = DB.eventos.get(eid)
    if not e:
        abort(404)
    return pagina("actividad.html", tema="light", activa="explorar", e=e)


# ── Ponentes ──────────────────────────────────────────────────────────
@app.route("/ponentes")
def ponentes():
    q = norm(request.args.get("q", ""))
    rol = request.args.get("rol", "")
    lista = [p for p in PERSONAS
             if (not q or q in norm(p.nombre))
             and (not rol or any(rol in r for r in p.roles.values()))]
    rol_etq = {"Ponente": "Ponente", "Maestro de ceremonias": "Maestro de ceremonias"}
    pg = paginar(lista, entero(request.args.get("pagina")), 24)
    abierto = DB.personas.get(request.args.get("persona", ""))
    agrup = defaultdict(list)
    if abierto:
        for e in sorted(abierto.eventos, key=lambda x: (x.primera.fecha, x.primera.ini)):
            agrup[e.primera.fecha].append(e)
    return pagina("ponentes.html", tema="light", activa="ponentes", pg=pg, q=request.args.get("q", ""),
                  rol=rol, abierto=abierto, agrup=sorted(agrup.items()), rol_etq=rol_etq,
                  total_personas=len(lista), offset=(pg.pagina - 1) * 24)


# ── Agenda semanal (tema oscuro) ───────────────────────────────────────
PXH = 118


@app.route("/agenda-semanal")
def agenda_semanal():
    f = leer_filtros(request.args)
    sel = DB.sesiones_id.get(request.args.get("s", ""))
    ses = filtrar_sesiones(f)
    por_dia = defaultdict(list)
    for s in ses:
        por_dia[s.fecha].append(s)
    dias = []
    for d in sorted(por_dia):
        barras = []
        for s in por_dia[d]:
            dur = mins(s.fin) - mins(s.ini)
            barras.append(SimpleNamespace(
                s=s, e=s.evento, left=round((mins(s.ini) - H0 * 60) / 60 * PXH + 2),
                w=max(round(dur / 60 * PXH - 4), 26)))
        dias.append(SimpleNamespace(fecha=d, abbr=DIAS[d.weekday()], barras=barras))
    # destacados: sesiones de actividades "especiales"
    esp = [s for s in DB.sesiones
           if s.evento.tipo in ("Conferencia magistral", "FOSEEI", "Cátedra Patrimonial")
           or re.search(r"inaugura|clausura", norm(s.evento.titulo))]
    vistos, destacados = set(), []
    for s in esp:
        if s.evento.id not in vistos:
            vistos.add(s.evento.id)
            destacados.append(s)
    return pagina("agenda_semanal.html", tema="dark", activa="agenda", f=f, opc=opciones_filtros(),
                  sel=sel, dias=dias, n=len(ses), destacados=destacados[:8], PXH=PXH, H0=H0, H1=H1,
                  horas=list(range(H0, H1 + 1)), ancho=(H1 - H0) * PXH, grupos=GRUPOS)


# ── Salones (tema oscuro) ─────────────────────────────────────────────
@app.route("/salones")
def salones():
    q = norm(request.args.get("q", ""))
    campus = request.args.get("campus", "")
    lista = [s for s in DB.salones if s.sesiones
             and (not campus or s.campus == campus)
             and (not q or q in norm(s.nombre + " " + s.campus_nombre + " " + s.tipo + " " + s.campus))]
    detalle = {}
    for s in lista:
        g = defaultdict(list)
        for x in s.sesiones:
            g[x.fecha].append(x)
        detalle[s.id] = sorted(g.items())
    return pagina("salones.html", tema="dark", activa="salones", lista=lista, detalle=detalle,
                  q=request.args.get("q", ""), campus=campus, abrir=request.args.get("abrir", ""),
                  campus_opc=[(k, CAMPUS_NOMBRE[k]) for k in ("IIT", "CU", "CUAUH")],
                  total_salones=len([s for s in DB.salones if s.sesiones]))


# ── Mi agenda ─────────────────────────────────────────────────────────
def disponer_columnas(items):
    items.sort(key=lambda x: (x.ini, x.fin))
    grupos, cur, fin_cur = [], [], -1
    for it in items:
        if cur and it.ini >= fin_cur:
            grupos.append(cur)
            cur, fin_cur = [], -1
        cur.append(it)
        fin_cur = max(fin_cur, it.fin)
    if cur:
        grupos.append(cur)
    for g in grupos:
        cols = []
        for it in g:
            for i, c in enumerate(cols):
                if c <= it.ini:
                    cols[i], it.col = it.fin, i
                    break
            else:
                cols.append(it.fin)
                it.col = len(cols) - 1
        for it in g:
            it.ncols = len(cols)


@app.route("/mi-agenda")
def mi_agenda():
    ids = agenda_ids()
    mis = [DB.eventos[i] for i in ids if i in DB.eventos]
    ses = sorted((s for e in mis for s in e.sesiones), key=lambda s: (s.fecha, s.ini))
    dias_cols = DIAS_SEMANA[:]
    for s in ses:
        if s.fecha not in dias_cols:
            dias_cols.append(s.fecha)
    dias_cols.sort()
    h0 = min([H0] + [mins(s.ini) // 60 for s in ses]) if ses else 8
    h1 = max([18] + [-(-mins(s.fin) // 60) for s in ses]) if ses else 18
    PX = 64
    cols = []
    for d in dias_cols:
        items = [SimpleNamespace(s=s, e=s.evento, ini=mins(s.ini), fin=mins(s.fin)) for s in ses if s.fecha == d]
        disponer_columnas(items)
        for it in items:
            it.top = (it.ini - h0 * 60) / 60 * PX
            it.alto = max((it.fin - it.ini) / 60 * PX, 30)
        cols.append(SimpleNamespace(fecha=d, items=items))
    return pagina("mi_agenda.html", tema="light", activa="", mis=mis, ses=ses, cols=cols,
                  horas=list(range(h0, h1 + 1)), PX=PX, h0=h0)


def _ics_esc(t):
    return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(linea):
    b = linea.encode("utf-8")
    if len(b) <= 74:
        return linea
    partes, actual = [], ""
    for ch in linea:
        if len((actual + ch).encode("utf-8")) > (74 if not partes else 73):
            partes.append(actual)
            actual = ch
        else:
            actual += ch
    partes.append(actual)
    return "\r\n ".join(partes)


@app.route("/mi-agenda.ics")
def mi_agenda_ics():
    ahora = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//UACJ//Semana de Ingenieria//ES",
         "CALSCALE:GREGORIAN", "X-WR-CALNAME:Mi agenda · Semana de Ingeniería",
         "X-WR-TIMEZONE:America/Ciudad_Juarez"]
    for eid in agenda_ids():
        e = DB.eventos.get(eid)
        if not e:
            continue
        for s in e.sesiones:
            lugar = (f"{s.salon.nombre} · {s.salon.campus_nombre}" if s.salon
                     else f"En línea · {s.plataforma}".strip(" ·"))
            desc = f"{e.tipo} · {e.depto}\n" + (f"Ponentes: {', '.join(p.nombre for p in e.ponentes)}\n" if e.ponentes else "")
            if s.link:
                desc += f"Enlace: {s.link}"
            L += ["BEGIN:VEVENT", f"UID:{s.id}@semana-ingenieria.uacj", f"DTSTAMP:{ahora}",
                  f"DTSTART;TZID=America/Ciudad_Juarez:{s.fecha:%Y%m%d}T{s.ini:%H%M}00",
                  f"DTEND;TZID=America/Ciudad_Juarez:{s.fecha:%Y%m%d}T{s.fin:%H%M}00",
                  f"SUMMARY:{_ics_esc(e.titulo)}", f"LOCATION:{_ics_esc(lugar)}",
                  f"DESCRIPTION:{_ics_esc(desc)}", "END:VEVENT"]
    L.append("END:VCALENDAR")
    cuerpo = "\r\n".join(_ics_fold(x) for x in L) + "\r\n"
    return Response(cuerpo, mimetype="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=mi-agenda-semana-ingenieria.ics"})


# ── Acciones (agregar / registrarse) ───────────────────────────────────
def _siguiente():
    nxt = request.form.get("next") or url_for("inicio")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("inicio")


def _alternar(clave, eid):
    if eid not in DB.eventos:
        abort(404)
    lst = list(session.get(clave, []))
    lst.remove(eid) if eid in lst else lst.append(eid)
    session[clave] = lst
    session.permanent = True


@app.post("/agenda/<eid>")
def alternar_agenda(eid):
    _alternar("ag", eid)
    return redirect(_siguiente())


@app.post("/registro/<eid>")
def alternar_registro(eid):
    _alternar("reg", eid)
    return redirect(_siguiente())


@app.route("/healthz")
def healthz():
    return "ok"


@app.errorhandler(404)
def no_encontrada(_):
    return pagina("404.html", tema="light", activa=""), 404


# ════════════════════════════════════════════════════════════════════
#  4. PLANTILLAS (Jinja)
# ════════════════════════════════════════════════════════════════════
T = {}

T["base.html"] = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block titulo %}Semana de Ingeniería{% endblock %} · UACJ</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Plus+Jakarta+Sans:wght@600;700;800&family=Source+Serif+4:wght@600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{{ url_for('estilos') }}?v={{ version }}">
</head>
<body class="{{ tema }}">
<header class="top"><div class="top-in">
  <a class="brand" href="{{ url_for('inicio') }}">{{ LOGO }}<span><b>UACJ</b><small>Semana de ingeniería</small></span></a>
  <nav aria-label="Principal">
    {% for k, etq, ep in [('inicio','Inicio','inicio'),('explorar','Explorar','explorar'),('ponentes','Ponentes','ponentes'),('agenda','Agenda semanal','agenda_semanal'),('salones','Salones','salones')] %}
    <a href="{{ url_for(ep) }}" class="{{ 'on' if activa == k }}">{{ etq }}</a>
    {% endfor %}
  </nav>
  <a class="btn-agenda" href="{{ url_for('mi_agenda') }}">{{ ico('cal', 15) }}<span>Mi agenda</span><i class="n">{{ n_agenda }}</i></a>
</div></header>
<main>{% block main %}{% endblock %}</main>
<footer class="foot"><div class="wrap">
  <div>{{ LOGO }}<p><b>XXXII Semana de Ingeniería</b><br>Universidad Autónoma de Ciudad Juárez</p></div>
  <p class="foot-n">Programa generado a partir del pipeline ETL del evento · {{ rango_semana[0].day }}–{{ rango_semana[1].day }} de {{ MESES[rango_semana[0].month-1] }} de {{ rango_semana[0].year }}</p>
</div></footer>
<script>
document.querySelectorAll('form[data-auto] select').forEach(function(s){s.addEventListener('change',function(){s.form.submit()})});
</script>
</body></html>"""

T["_m.html"] = r"""
{% macro btn_agregar(e, corto=False) %}
<form method="post" action="{{ url_for('alternar_agenda', eid=e.id) }}" class="f-add">
  <input type="hidden" name="next" value="{{ ruta() }}">
  {% if en_agenda(e.id) %}
  <button class="add on" title="Quitar de mi agenda">{{ ico('check', 14) }} Agregado</button>
  {% else %}
  <button class="add">{{ ico('plus', 14) }} {{ 'Agregar' if corto else 'Agregar a mi agenda' }}</button>
  {% endif %}
</form>
{% endmacro %}

{% macro tarjeta(e) %}
{% set a = arte(e.g.var) %}
<article class="card">
  <a class="art tinta-{{ 'claro' if a[1] == '#ffffff' else 'oscuro' }}" href="{{ url_for('actividad', eid=e.id) }}" tabindex="-1" aria-hidden="true">
    {{ a[0] }}
    <span class="tag">{{ e.tipo }}</span>
    <span class="dnum"><b>{{ e.primera.fecha.day }}</b><small>{{ MESES[e.primera.fecha.month-1][:3] }}</small></span>
  </a>
  <div class="card-b">
    <p class="when">{{ e.primera.dia }} · {{ e.primera.horas }}</p>
    <h3><a href="{{ url_for('actividad', eid=e.id) }}">{{ e.titulo }}</a></h3>
    <p class="meta-s">{{ e.depto }} · {{ 'Sesión única' if e.n == 1 else e.n ~ ' sesiones' }}</p>
    <p class="desc">{{ e.desc }}</p>
    <ul class="meta">
      {% if e.ponentes %}<li>{{ ico('user') }}<span>{% for p in e.ponentes[:2] %}{{ p.nombre }}{{ ', ' if not loop.last }}{% endfor %}{{ ' y ' ~ (e.ponentes|length - 2) ~ ' más' if e.ponentes|length > 2 }}</span></li>{% endif %}
      {% set s = e.primera %}
      <li>{{ ico('monitor' if s.modalidad == 'Virtual' else 'bldg') }}<span>{{ 'En línea' if not s.campus else CAMPUS_NOMBRE[s.campus] }} · {{ s.modalidad }}{{ ' · ' ~ s.plataforma if s.plataforma }}</span></li>
      {% if s.salon %}<li>{{ ico('pin') }}<span>{{ s.salon.nombre }}</span></li>
      <li>{{ ico('users') }}<span>Cupo: {{ s.salon.cupo }} personas</span></li>{% endif %}
    </ul>
    {{ btn_agregar(e) }}
  </div>
</article>
{% endmacro %}

{% macro filtros_claros(f, opc, destino, extra=None) %}
<form class="filtros" method="get" action="{{ destino }}" data-auto>
  {% if extra %}{% for k, v in extra.items() %}<input type="hidden" name="{{ k }}" value="{{ v }}">{% endfor %}{% endif %}
  <label class="busca">{{ ico('search', 20) }}<input type="search" name="q" value="{{ f.q }}" placeholder="Buscar por actividad, persona, empresa o lugar"></label>
  <div class="fgrid">
    <label class="fld"><small>Tipo</small><select name="tipo"><option value="">Todos los tipos</option>{% for t in opc.tipos %}<option value="{{ t }}" {{ 'selected' if f.tipo == t }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Día</small><select name="dia"><option value="">Todos los días</option>{% for v, t in opc.dias %}<option value="{{ v }}" {{ 'selected' if f.dia == v }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Horario</small><select name="horario"><option value="">Cualquier horario</option>{% for v, t in opc.horarios %}<option value="{{ v }}" {{ 'selected' if f.horario == v }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Campus</small><select name="campus"><option value="">Todos los campus</option>{% for v, t in opc.campus %}<option value="{{ v }}" {{ 'selected' if f.campus == v }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Modalidad</small><select name="modalidad"><option value="">Todas las modalidades</option>{% for t in opc.modalidades %}<option value="{{ t }}" {{ 'selected' if f.modalidad == t }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Departamento</small><select name="depto"><option value="">Todos los departamentos</option>{% for v, t in opc.deptos %}<option value="{{ v }}" {{ 'selected' if f.depto == v }}>{{ t }}</option>{% endfor %}</select></label>
    <label class="fld"><small>Lugar</small><select name="lugar"><option value="">Todos los lugares</option>{% for v, t in opc.lugares %}<option value="{{ v }}" {{ 'selected' if f.lugar == v }}>{{ t }}</option>{% endfor %}</select></label>
  </div>
  <noscript><button class="btn-lima">Aplicar filtros</button></noscript>
</form>
{% endmacro %}

{% macro paginador(pg, destino, args) %}
{% if pg.total > 1 %}
<nav class="pager" aria-label="Páginas">
  {% if pg.pagina > 1 %}<a href="{{ destino }}{{ qs(args, pagina=pg.pagina-1) }}">← Anterior</a>{% endif %}
  <span>Página {{ pg.pagina }} de {{ pg.total }}</span>
  {% if pg.pagina < pg.total %}<a href="{{ destino }}{{ qs(args, pagina=pg.pagina+1) }}">Siguiente →</a>{% endif %}
</nav>
{% endif %}
{% endmacro %}
"""

T["inicio.html"] = r"""{% extends "base.html" %}{% import "_m.html" as m with context %}
{% block main %}
<section class="hero">
  <div class="wrap hero-in">
    <p class="eyebrow"><i></i>{{ rango_semana[0].day }}—{{ rango_semana[1].day }} DE {{ MESES[rango_semana[0].month-1]|upper }} DE {{ rango_semana[0].year }}</p>
    <h1>Ideas que<br><em>mueven</em> al<br>mundo.</h1>
    <p class="lead">Cinco días de conferencias, talleres y exposiciones donde la ingeniería construye lo que sigue.</p>
    <div class="ctas">
      <a class="btn-lima" href="{{ url_for('explorar') }}">Explorar actividades {{ ico('arrow') }}</a>
      <a class="lnk-b" href="{{ url_for('agenda_semanal') }}">Ver la semana {{ ico('arrow') }}</a>
    </div>
  </div>
  <div class="disc" aria-hidden="true">
    <div class="core"><b>{{ (total // 10) * 10 }}+</b><small>Actividades</small></div>
  </div>
  <span class="ring r1"></span><span class="ring r2"></span><span class="ring r3"></span>
  <span class="stk s1">Construye</span><span class="stk s2">Pregunta</span><span class="stk s3">Descubre</span>
  <p class="uni">Universidad Autónoma<br>de Ciudad Juárez</p>
</section>
<section class="strip"><div class="strip-in">
  <span class="strip-t">Explorar por</span>
  <a href="{{ url_for('explorar') }}"><small>01</small><b>Todas las actividades</b>{{ ico('arrow', 18) }}</a>
  <a href="{{ url_for('ponentes') }}"><small>02</small><b>Ponentes</b>{{ ico('arrow', 18) }}</a>
  <a href="{{ url_for('explorar') }}#filtros"><small>03</small><b>Tipo de actividad</b>{{ ico('arrow', 18) }}</a>
  <a href="{{ url_for('agenda_semanal') }}"><small>04</small><b>Día y horario</b>{{ ico('arrow', 18) }}</a>
</div></section>
<section class="sec cream"><div class="wrap-s">
  <p class="eyebrow-s">No te lo pierdas</p>
  <div class="sec-h"><h2>Destacados de la semana</h2><a class="lnk-u" href="{{ url_for('explorar') }}">Ver todas las actividades {{ ico('arrow', 14) }}</a></div>
  <div class="cards">{% for e in destacados %}{{ m.tarjeta(e) }}{% endfor %}</div>
</div></section>
<section class="band"><div class="wrap band-in">
  <div>
    <p class="eyebrow-s w">La ingeniería es un trabajo en equipo</p>
    <h2>Conoce a las mentes detrás del impulso.</h2>
    <p class="lead-d">Investigadores, docentes, estudiantes y líderes que comparten su experiencia y se preguntan qué sigue.</p>
    <a class="btn-blanco" href="{{ url_for('ponentes') }}">Conocer a los ponentes {{ ico('arrow') }}</a>
  </div>
  <div class="anillo" aria-hidden="true"><span>{{ n_ponentes }} personas</span></div>
</div></section>
{% endblock %}"""

T["explorar.html"] = r"""{% extends "base.html" %}{% import "_m.html" as m with context %}
{% block titulo %}Explorar{% endblock %}
{% block main %}
<section class="ph"><div class="wrap ph-in">
  <div><p class="eyebrow-l">Encuentra tu próxima idea</p><h1>Explora la semana</h1></div>
  <p class="ph-p">Busca entre conferencias, competencias, talleres y exposiciones. Haz tuya la semana.</p>
</div></section>
<section class="white" id="filtros"><div class="wrap">{{ m.filtros_claros(f, opc, url_for('explorar'), {'vista': vista} if vista == 'agenda' else None) }}</div></section>
<section class="sec cream"><div class="wrap-s">
  <div class="count-row"><b>{{ pg.n }} actividades</b>
    <span class="tabs"><a class="{{ 'on' if vista == 'tarjetas' }}" href="{{ url_for('explorar') }}{{ qs(f) }}">Tarjetas</a><a class="{{ 'on' if vista == 'agenda' }}" href="{{ url_for('explorar') }}{{ qs(f, vista='agenda') }}">Agenda</a></span></div>
  {% if not pg.n %}<p class="vacio">No encontramos actividades con esos filtros. <a href="{{ url_for('explorar') }}">Limpiar filtros</a></p>{% endif %}
  {% if vista == 'tarjetas' %}
  <div class="cards">{% for e in pg.items %}{{ m.tarjeta(e) }}{% endfor %}</div>
  {{ m.paginador(pg, url_for('explorar'), f) }}
  {% else %}
  {% for d, ss in por_dia %}
  <h3 class="dia-t">{{ fecha_larga(d)|capitalize }} <small>{{ ss|length }}</small></h3>
  <div class="lista">
    {% for s in ss %}
    <a class="fila g-{{ s.evento.grupo }}" href="{{ url_for('actividad', eid=s.evento.id) }}">
      <span class="h">{{ s.horas }}</span><b>{{ s.evento.titulo }}</b><span class="t">{{ s.evento.tipo }}</span>
      <span class="l">{{ s.salon.nombre ~ ' · ' ~ s.salon.campus_nombre if s.salon else 'En línea' ~ (' · ' ~ s.plataforma if s.plataforma else '') }}</span>
    </a>
    {% endfor %}
  </div>
  {% endfor %}
  {% endif %}
</div></section>
{% endblock %}"""

T["actividad.html"] = r"""{% extends "base.html" %}{% import "_m.html" as m with context %}
{% block titulo %}{{ e.titulo }}{% endblock %}
{% block main %}
{% set a = arte(e.g.var) %}
<section class="ph det-h"><div class="wrap">
  <a class="back" href="javascript:history.back()">← Volver</a>
  <p class="eyebrow-l">{{ e.tipo }} · {{ e.g.nombre }}</p>
  <h1 class="h1-m">{{ e.titulo }}</h1>
</div></section>
<section class="sec cream"><div class="wrap-s det">
  <div class="det-main">
    <p class="big-p">{{ e.desc }}</p>
    <h3 class="sub">{{ 'Sesión única' if e.n == 1 else e.n ~ ' sesiones' }}</h3>
    <div class="ses-l">
    {% for s in e.sesiones %}
      <div class="ses">
        <b>{{ s.dia }} {{ s.fecha.day }} {{ MESES[s.fecha.month-1][:3] }}</b><span>{{ s.horas }}</span>
        <span>{% if s.salon %}{{ s.salon.nombre }} · {{ s.salon.campus_nombre }}{% else %}En línea{{ ' · ' ~ s.plataforma if s.plataforma }}{% endif %} · {{ s.modalidad }}</span>
        {% if s.link %}<a class="lnk-u" href="{{ s.link }}" target="_blank" rel="noopener">Unirme {{ ico('link', 13) }}</a>{% endif %}
      </div>
    {% endfor %}
    </div>
    <div class="acc-row">
      {{ m.btn_agregar(e) }}
      <form method="post" action="{{ url_for('alternar_registro', eid=e.id) }}"><input type="hidden" name="next" value="{{ ruta() }}">
        <button class="add {{ 'on' if registrado(e.id) }}">{{ 'Registrado · darme de baja' if registrado(e.id) else 'Registrarme' }}</button></form>
    </div>
  </div>
  <aside class="det-side">
    <dl>
      {% if e.ponentes %}<dt>Ponente{{ 's' if e.ponentes|length > 1 }}</dt><dd>{% for p in e.ponentes %}<a class="lnk-u" href="{{ url_for('ponentes', persona=p.id) }}">{{ p.nombre }}</a>{{ ', ' if not loop.last }}{% endfor %}</dd>{% endif %}
      {% if e.mcs %}<dt>Maestro{{ 's' if e.mcs|length > 1 }} de ceremonias</dt><dd>{% for p in e.mcs %}<a class="lnk-u" href="{{ url_for('ponentes', persona=p.id) }}">{{ p.nombre }}</a>{{ ', ' if not loop.last }}{% endfor %}</dd>{% endif %}
      {% if e.orgs %}<dt>Organización</dt><dd>{{ e.orgs|join(', ') }}</dd>{% endif %}
      <dt>Departamento</dt><dd>{{ e.depto }}</dd>
      <dt>Modalidad</dt><dd>{{ e.modalidades|join(' / ') }}</dd>
      {% if e.sesiones[0].salon %}<dt>Cupo</dt><dd>{{ e.sesiones[0].salon.cupo }} personas</dd>{% endif %}
    </dl>
  </aside>
</div></section>
{% endblock %}"""

T["ponentes.html"] = r"""{% extends "base.html" %}{% import "_m.html" as m with context %}
{% block titulo %}Ponentes{% endblock %}
{% block main %}
<section class="ph"><div class="wrap ph-in">
  <div><p class="eyebrow-l">Ponentes de la Semana de Ingeniería</p><h1>Mentes brillantes.<br>Puertas abiertas.</h1></div>
  <p class="ph-p">Conoce a especialistas, docentes y mentores que guían las conversaciones de esta semana.</p>
</div></section>
<section class="white"><div class="wrap">
  <form class="filtros" method="get" action="{{ url_for('ponentes') }}" data-auto>
    <div class="fila-f">
      <label class="busca">{{ ico('search', 20) }}<input type="search" name="q" value="{{ q }}" placeholder="Buscar persona"></label>
      <label class="fld"><small>Participación</small><select name="rol"><option value="">Todas</option><option value="Ponente" {{ 'selected' if rol == 'Ponente' }}>Ponentes</option><option value="Maestro de ceremonias" {{ 'selected' if rol == 'Maestro de ceremonias' }}>Maestros de ceremonias</option></select></label>
    </div>
  </form>
</div></section>
<section class="sec cream"><div class="wrap-s">
  <div class="count-row"><b>{{ total_personas }} personas</b></div>
  {% if not pg.n %}<p class="vacio">Sin resultados. <a href="{{ url_for('ponentes') }}">Limpiar búsqueda</a></p>{% endif %}
  <div class="pgrid">
  {% for p in pg.items %}
    {% set idx = offset + loop.index %}
    <article class="pcard">
      <a class="pav" href="{{ url_for('ponentes') }}{{ qs({'q': q, 'rol': rol}, pagina=pg.pagina if pg.pagina > 1 else None, persona=p.id) }}">{{ avatar(idx) }}<span class="ini">{{ p.iniciales }}</span></a>
      <div class="pb">
        <div class="pt"><small>{{ '%02d' % idx }}</small><span class="chip">{{ 'Ponente' if p.es_ponente else 'Maestro de ceremonias' }}</span></div>
        <h3>{{ p.nombre }}</h3>
        <p class="rol">{{ p.deptos.most_common(1)[0][0] }}</p>
        {% if p.correo %}<p class="mail">{{ ico('mail', 13) }} {{ p.correo }}</p>{% else %}<p class="mail dim">{{ p.eventos|length }} actividad{{ 'es' if p.eventos|length > 1 }}</p>{% endif %}
        <a class="lnk-u peq" href="{{ url_for('ponentes') }}{{ qs({'q': q, 'rol': rol}, pagina=pg.pagina if pg.pagina > 1 else None, persona=p.id) }}">Ver {{ 'su actividad' if p.eventos|length == 1 else 'sus actividades' }} {{ ico('arrow', 13) }}</a>
      </div>
    </article>
  {% endfor %}
  </div>
  {{ m.paginador(pg, url_for('ponentes'), {'q': q, 'rol': rol}) }}
</div></section>
{% if abierto %}
<a class="velo" href="{{ url_for('ponentes') }}{{ qs({'q': q, 'rol': rol}, pagina=pg.pagina if pg.pagina > 1 else None) }}" aria-label="Cerrar"></a>
<aside class="drawer" role="dialog" aria-label="{{ abierto.nombre }}">
  <div class="dr-h">
    <a class="cerrar" href="{{ url_for('ponentes') }}{{ qs({'q': q, 'rol': rol}, pagina=pg.pagina if pg.pagina > 1 else None) }}" aria-label="Cerrar">{{ ico('x', 18) }}</a>
    <div class="dr-av">{{ abierto.iniciales }}</div>
    <p class="eyebrow-l">{{ 'Ponente' if abierto.es_ponente else 'Maestro de ceremonias' }}</p>
    <h2>{{ abierto.nombre }}</h2>
    <p class="dr-s">{{ abierto.deptos.most_common(1)[0][0] }}</p>
    {% if abierto.correo %}<p class="dr-m">{{ ico('mail', 14) }} {{ abierto.correo }}</p>{% endif %}
  </div>
  <div class="dr-b">
    <div class="dr-t"><span>Actividades</span><b>{{ abierto.eventos|length }}</b></div>
    {% for d, evs in agrup %}
      <p class="dr-d">{{ DIAS[d.weekday()] }} {{ d.day }} {{ MESES[d.month-1][:3]|upper }}</p>
      {% for e in evs %}
      <div class="dr-c">
        <span class="tag-s">{{ e.tipo }}</span>
        <h4>{{ e.titulo }}</h4>
        <p>{{ e.primera.horas }} · {{ e.primera.modalidad }} · {{ CAMPUS_NOMBRE[e.primera.campus] if e.primera.campus else 'En línea' }}{{ ' · ' ~ e.n ~ ' sesiones' if e.n > 1 }}</p>
        {% if e.primera.salon %}<p class="dim">{{ e.primera.salon.nombre }}</p>{% endif %}
        <p class="dep">{{ e.depto }} · {{ abierto.roles[e.id]|join(', ') }}</p>
        <div class="dr-a">{{ m.btn_agregar(e, True) }}<a class="lnk-u peq" href="{{ url_for('actividad', eid=e.id) }}">Ver detalle {{ ico('arrow', 13) }}</a></div>
      </div>
      {% endfor %}
    {% endfor %}
  </div>
</aside>
{% endif %}
{% endblock %}"""

T["mi_agenda.html"] = r"""{% extends "base.html" %}{% import "_m.html" as m with context %}
{% block titulo %}Mi agenda{% endblock %}
{% block main %}
<section class="ph"><div class="wrap ph-in ph-ag">
  <div><p class="eyebrow-l">Tu semana de ingeniería</p><h1>Mi agenda</h1>
    <p class="ph-sub">{{ mis|length }} actividad{{ 'es' if mis|length != 1 }} guardada{{ 's' if mis|length != 1 }} · {{ rango_semana[0].day }}–{{ rango_semana[1].day }} de {{ MESES[rango_semana[0].month-1] }} de {{ rango_semana[0].year }}</p></div>
  {% if mis %}<a class="btn-lima" href="{{ url_for('mi_agenda_ics') }}">{{ ico('down', 16) }} Exportar calendario</a>{% endif %}
</div></section>
<section class="sec cream"><div class="wrap-s">
{% if not mis %}
  <p class="vacio grande">Aún no agregas actividades. <a href="{{ url_for('explorar') }}">Explora la semana</a> y arma tu agenda.</p>
{% else %}
  <div class="cal-sc"><div class="cal" style="--px:{{ PX }}px;--cols:{{ cols|length }}">
    <div class="cal-head"><span></span>{% for c in cols %}<div><small>{{ DIAS[c.fecha.weekday()] }}</small><b>{{ c.fecha.day }}</b></div>{% endfor %}</div>
    <div class="cal-body" style="height:{{ (horas|length - 1) * PX }}px">
      <div class="cal-h">{% for h in horas[:-1] %}<span style="top:{{ loop.index0 * PX }}px">{{ fmt_hora_eje(h) }}</span>{% endfor %}</div>
      {% for c in cols %}
      <div class="cal-c" style="grid-column:{{ loop.index + 1 }}">
        {% for it in c.items %}
        <a class="bl v{{ it.e.g.var }}" href="{{ url_for('actividad', eid=it.e.id) }}"
           style="top:{{ it.top }}px;height:{{ it.alto }}px;left:calc({{ it.col }} * 100% / {{ it.ncols }});width:calc(100% / {{ it.ncols }})">
          <b>{{ it.e.titulo }}</b><span>{{ it.s.horas }}</span><em>{{ it.s.salon.nombre if it.s.salon else 'En línea' }}</em>
        </a>
        {% endfor %}
      </div>
      {% endfor %}
    </div>
  </div></div>
  <h3 class="dia-t" style="margin-top:56px">Tus actividades</h3>
  <div class="lista">
  {% for e in mis %}
    <div class="fila g-{{ e.grupo }} fila-ag">
      <span class="h">{{ e.primera.dia }} {{ e.primera.fecha.day }} · {{ e.primera.horas }}</span>
      <a href="{{ url_for('actividad', eid=e.id) }}"><b>{{ e.titulo }}</b></a><span class="t">{{ e.tipo }}</span>
      <span class="l">{{ e.primera.salon.nombre if e.primera.salon else 'En línea' }}{{ ' · +' ~ (e.n - 1) ~ ' sesiones' if e.n > 1 }}</span>
      <span class="q">{{ m.btn_agregar(e, True) }}</span>
    </div>
  {% endfor %}
  </div>
{% endif %}
</div></section>
{% endblock %}"""

T["agenda_semanal.html"] = r"""{% extends "base.html" %}
{% block titulo %}Agenda semanal{% endblock %}
{% block main %}
<section class="dk-top" id="filtros"><div class="wrap-d">
{% if sel %}
  {% set e = sel.evento %}
  <div class="dt" id="detalle">
    <a class="dt-x" href="{{ url_for('agenda_semanal') }}{{ qs(f) }}#filtros" aria-label="Cerrar detalle y volver a los filtros" title="Cerrar">{{ ico('x', 18) }}</a>
    <div class="dt-l">
      <span class="pill g-{{ e.grupo }}">{{ e.g.nombre }}</span>
      <p class="lema">{{ e.g.lema }}</p>
      <h1>{{ e.titulo }}</h1>
      <p class="tp">{{ e.tipo }} · {{ 'Sesión única.' if e.n == 1 else e.n ~ ' sesiones.' }}</p>
      <ul class="ses-d">
        {% for s in e.sesiones %}
        <li class="{{ 'actual' if s.id == sel.id }}">{{ s.dia }} {{ s.fecha.day }} {{ MESES[s.fecha.month-1][:3] }} · {{ s.horas }}
          <span>{% if s.salon %}{{ s.salon.nombre }} · {{ s.salon.campus_nombre }}{% else %}En línea{{ ' · ' ~ s.plataforma if s.plataforma }}{% endif %}</span></li>
        {% endfor %}
      </ul>
      <p class="desc-d">{{ e.desc }}</p>
      <div class="acc-d">
        <form method="post" action="{{ url_for('alternar_registro', eid=e.id) }}"><input type="hidden" name="next" value="{{ ruta() }}#detalle">
          {% if not registrado(e.id) %}<button class="b-reg">Registrarme</button>{% else %}<button class="b-reg ok">{{ ico('check', 15) }} Registrado</button>{% endif %}</form>
        <form method="post" action="{{ url_for('alternar_agenda', eid=e.id) }}"><input type="hidden" name="next" value="{{ ruta() }}#detalle">
          <button class="b-out">{{ 'Quitar de mi agenda' if en_agenda(e.id) else 'Agregar a mi agenda' }}</button></form>
        {% if registrado(e.id) %}<form method="post" action="{{ url_for('alternar_registro', eid=e.id) }}"><input type="hidden" name="next" value="{{ ruta() }}#detalle"><button class="b-baja">Darme de baja</button></form>{% endif %}
        <a class="b-baja" href="{{ url_for('actividad', eid=e.id) }}">Ver página completa</a>
      </div>
    </div>
    <dl class="dt-r">
      {% if e.ponentes %}<div><dt>{{ ico('user', 16) }} Ponente{{ 's' if e.ponentes|length > 1 }}</dt><dd>{% for p in e.ponentes %}<a href="{{ url_for('ponentes', persona=p.id) }}">{{ p.nombre }}</a>{{ ', ' if not loop.last }}{% endfor %}</dd></div>{% endif %}
      {% if e.mcs %}<div><dt>{{ ico('user', 16) }} Maestro{{ 's' if e.mcs|length > 1 }} de ceremonias</dt><dd>{% for p in e.mcs %}<a href="{{ url_for('ponentes', persona=p.id) }}">{{ p.nombre }}</a>{{ ', ' if not loop.last }}{% endfor %}</dd></div>{% endif %}
      <div><dt>{{ ico('bldg', 16) }} Organización</dt><dd><b>{{ e.orgs[0] if e.orgs else 'UACJ' }}</b><small>{{ 'Organización participante' }}{{ ' · ' ~ e.depto if e.depto }}</small></dd></div>
      <div><dt>{{ ico('monitor', 16) }} Modalidad</dt><dd>{{ CAMPUS_NOMBRE[sel.campus] ~ ' · ' if sel.campus }}{{ sel.modalidad }}{{ ' · ' ~ sel.plataforma if sel.plataforma }}</dd></div>
      <div><dt>{{ ico('pin', 16) }} Lugar</dt><dd>{% if sel.salon %}<a href="{{ url_for('salones', abrir=sel.salon.id) }}#{{ sel.salon.id }}">{{ sel.salon.nombre }}</a>{% else %}En línea{% endif %}</dd></div>
      {% if sel.salon %}<div><dt>{{ ico('users', 16) }} Cupo</dt><dd>{{ sel.salon.cupo }} personas</dd></div>{% endif %}
      {% if sel.link %}<div><dt>{{ ico('link', 16) }} Enlace</dt><dd><a href="{{ sel.link }}" target="_blank" rel="noopener">Abrir reunión en {{ sel.plataforma }}</a></dd></div>{% endif %}
    </dl>
  </div>
{% else %}
  <h1 class="dk-h">Agenda semanal</h1>
  <p class="dk-p">Cada barra es una sesión, colocada en su horario. Toca una para ver todos sus datos.</p>
  <form class="dk-f" method="get" action="{{ url_for('agenda_semanal') }}" data-auto>
    <label class="busca">{{ ico('search', 18) }}<input type="search" name="q" value="{{ f.q }}" placeholder="Buscar por actividad, persona, empresa o lugar"></label>
    <div class="dk-grid">
      <label><small>Tipo</small><select name="tipo"><option value="">Todos los tipos</option>{% for t in opc.tipos %}<option value="{{ t }}" {{ 'selected' if f.tipo == t }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Día</small><select name="dia"><option value="">Todos los días</option>{% for v, t in opc.dias %}<option value="{{ v }}" {{ 'selected' if f.dia == v }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Horario</small><select name="horario"><option value="">Cualquier horario</option>{% for v, t in opc.horarios %}<option value="{{ v }}" {{ 'selected' if f.horario == v }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Campus</small><select name="campus"><option value="">Todos los campus</option>{% for v, t in opc.campus %}<option value="{{ v }}" {{ 'selected' if f.campus == v }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Modalidad</small><select name="modalidad"><option value="">Todas las modalidades</option>{% for t in opc.modalidades %}<option value="{{ t }}" {{ 'selected' if f.modalidad == t }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Departamento</small><select name="depto"><option value="">Todos los departamentos</option>{% for v, t in opc.deptos %}<option value="{{ v }}" {{ 'selected' if f.depto == v }}>{{ t }}</option>{% endfor %}</select></label>
      <label><small>Lugar</small><select name="lugar"><option value="">Todos los lugares</option>{% for v, t in opc.lugares %}<option value="{{ v }}" {{ 'selected' if f.lugar == v }}>{{ t }}</option>{% endfor %}</select></label>
    </div>
    <noscript><button class="b-reg">Aplicar</button></noscript>
  </form>
{% endif %}
</div></section>

<section class="dk-main"><div class="wrap-d">
  {% if destacados and not f.q and not f.tipo and not f.dia and not f.horario and not f.campus and not f.modalidad and not f.depto and not f.lugar %}
  <p class="dk-lbl">Destacados</p>
  <div class="chips">{% for s in destacados %}<a class="chip-d" href="{{ url_for('agenda_semanal') }}{{ qs(f, s=s.id) }}#detalle">{{ s.evento.titulo }}</a>{% endfor %}</div>
  {% endif %}
  <div class="dk-count"><b>{{ n }} sesiones</b>
    <span class="leyenda">{% for k, g in grupos.items() %}<span class="lg"><i class="g-{{ k }}"></i>{{ g.nombre }}</span>{% endfor %}</span>
    {% if f.q or f.tipo or f.dia or f.horario or f.campus or f.modalidad or f.depto or f.lugar %}<a href="{{ url_for('agenda_semanal') }}">Limpiar filtros</a>{% endif %}</div>
  {% if not dias %}<p class="vacio-d">No hay sesiones con esos filtros.</p>{% endif %}
  <div class="tl-sc">
    <div class="tl" style="--pxh:{{ PXH }}px;--ancho:{{ ancho }}px">
    {% for d in dias %}
      <div class="tl-day">
        <div class="tl-label"><b>{{ d.abbr }}</b><span>{{ d.fecha.day }}</span><small>{{ d.barras|length }} ses.</small></div>
        <div class="tl-body">
          <div class="tl-ruler">{% for h in horas[:-1] %}<span style="left:{{ loop.index0 * PXH }}px">{{ fmt_hora_eje(h) }}</span>{% endfor %}</div>
          {% for b in d.barras %}
          <div class="tl-row"><a class="bar g-{{ b.e.grupo }}{{ ' sel' if sel and sel.id == b.s.id }}" style="left:{{ b.left }}px;width:{{ b.w }}px"
             href="{{ url_for('agenda_semanal') }}{{ qs(f, s=b.s.id) }}#detalle" title="{{ b.e.titulo }} · {{ b.s.horas }}"><span>{{ b.e.titulo }}</span></a></div>
          {% endfor %}
        </div>
      </div>
    {% endfor %}
    </div>
  </div>
</div></section>
{% endblock %}"""

T["salones.html"] = r"""{% extends "base.html" %}
{% block titulo %}Salones{% endblock %}
{% block main %}
<section class="dk-top"><div class="wrap-d narrow">
  <h1 class="dk-h">Salones</h1>
  <p class="dk-p">Elige un salón para ver los eventos que se realizarán ahí.</p>
  <form class="sal-f" method="get" action="{{ url_for('salones') }}" data-auto>
    <label class="busca"><input type="search" name="q" value="{{ q }}" placeholder="Buscar salón o campus…"></label>
    <select name="campus"><option value="">Todos los campus</option>{% for v, t in campus_opc %}<option value="{{ v }}" {{ 'selected' if campus == v }}>{{ t }}</option>{% endfor %}</select>
    <noscript><button class="b-reg">Buscar</button></noscript>
  </form>
  <p class="dk-lbl sep">{{ lista|length }} salones{{ ' (de ' ~ total_salones ~ ')' if lista|length != total_salones }}</p>
  {% if not lista %}<p class="vacio-d">No encontramos salones con esa búsqueda.</p>{% endif %}
  {% for s in lista %}
  <details class="sal" id="{{ s.id }}" {{ 'open' if s.id == abrir }}>
    <summary><div><b>{{ s.nombre }}</b><small>{{ s.campus_nombre }} · {{ s.tipo }} · {{ s.sesiones|length }} evento{{ 's' if s.sesiones|length != 1 }}</small></div><i></i></summary>
    <div class="sal-b">
      {% for d, ss in detalle[s.id] %}
      <p class="sal-d">{{ DIAS[d.weekday()] }} {{ d.day }} {{ MESES[d.month-1][:3]|upper }}</p>
      {% for x in ss %}
      <a class="sal-e g-{{ x.evento.grupo }}" href="{{ url_for('agenda_semanal', s=x.id) }}#detalle"><span class="h">{{ x.horas }}</span><b>{{ x.evento.titulo }}</b><em>{{ x.evento.tipo }}</em></a>
      {% endfor %}
      {% endfor %}
    </div>
  </details>
  {% endfor %}
</div></section>
{% endblock %}"""

T["404.html"] = r"""{% extends "base.html" %}
{% block main %}<section class="ph"><div class="wrap"><p class="eyebrow-l">404</p><h1>Página no encontrada.</h1><p class="ph-sub"><a class="lnk-lima" href="{{ url_for('inicio') }}">Volver al inicio</a></p></div></section>{% endblock %}"""

app.jinja_loader = DictLoader(T)

# ════════════════════════════════════════════════════════════════════
#  5. ESTILOS (CSS servido desde Python)
# ════════════════════════════════════════════════════════════════════
CSS = r"""
:root{--blue:#3155ed;--lime:#e9ff47;--lime2:#c8ea58;--orange:#ff7247;--purple:#a99af8;--ink:#111;--cream:#f4f3ee;
--line:#dcd9d2;--muted:#6b6a66;--f-t:'Plus Jakarta Sans','DM Sans',system-ui,sans-serif;--f-b:'DM Sans',system-ui,sans-serif;
--f-s:'Source Serif 4',Georgia,serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;font:400 15px/1.5 var(--f-b);color:var(--ink);background:#fff;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}h1,h2,h3,h4{font-family:var(--f-t);margin:0;font-weight:700}
button,select,input{font:inherit;color:inherit}.ico{flex:none}
.wrap{max-width:1380px;margin:0 auto;padding:0 56px}.wrap-s{max-width:1320px;margin:0 auto;padding:0 28px}
/* header */
.top{position:sticky;top:0;z-index:60;background:#fff;border-bottom:1px solid var(--line)}
.top-in{max-width:1900px;margin:0 auto;padding:0 48px;height:72px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px}
.brand{display:flex;align-items:center;gap:12px}.brand span{display:flex;flex-direction:column;line-height:1.25}
.brand b{font-size:13px;letter-spacing:.08em}.brand small{font-size:9px;letter-spacing:.16em;text-transform:uppercase}
.top nav{display:flex;height:72px}.top nav a{display:flex;align-items:center;padding:0 16px;font-weight:500;font-size:14px;position:relative}
.top nav a.on::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:3px;background:var(--ink)}
.btn-agenda{justify-self:end;display:flex;align-items:center;gap:10px;background:var(--lime);border:1px solid var(--ink);
padding:13px 20px;font:700 11px var(--f-b);letter-spacing:.12em;text-transform:uppercase}
.btn-agenda .n{font-style:normal;background:var(--ink);color:#fff;border-radius:50%;width:20px;height:20px;display:grid;place-items:center;font-size:10px;letter-spacing:0}
/* botones */
.btn-lima{display:inline-flex;align-items:center;gap:14px;background:var(--lime);color:var(--ink);border:1px solid var(--ink);padding:17px 22px;font:700 11px var(--f-b);letter-spacing:.14em;text-transform:uppercase;cursor:pointer}
.btn-blanco{display:inline-flex;align-items:center;gap:14px;background:#fff;color:var(--ink);padding:16px 22px;font:700 11px var(--f-b);letter-spacing:.14em;text-transform:uppercase}
.lnk-b{display:inline-flex;align-items:center;gap:12px;font:700 12px var(--f-b);letter-spacing:.14em;text-transform:uppercase;border-bottom:2px solid #fff;padding-bottom:6px}
.lnk-u{border-bottom:1px solid currentColor;font-weight:500}.lnk-u.peq{font:700 10.5px var(--f-b);letter-spacing:.1em;text-transform:uppercase;display:inline-flex;gap:8px;align-items:center;width:max-content}
.lnk-lima{color:var(--lime);border-bottom:1px solid var(--lime)}
.eyebrow{display:flex;align-items:center;gap:14px;font:700 11px var(--f-b);letter-spacing:.2em;text-transform:uppercase}.eyebrow i{width:34px;height:2px;background:var(--lime)}
.eyebrow-s{font:700 10px var(--f-b);letter-spacing:.22em;text-transform:uppercase;margin:0 0 6px}.eyebrow-s.w{color:#fff;margin-bottom:34px}
.eyebrow-l{font:700 10.5px var(--f-b);letter-spacing:.2em;text-transform:uppercase;color:var(--lime);margin:0 0 20px}
/* hero */
.hero{background:var(--blue);color:#fff;position:relative;overflow:hidden;min-height:650px}
.hero-in{position:relative;z-index:3;padding-top:76px;padding-bottom:70px;max-width:1900px;padding-left:max(56px,calc((100vw - 1380px)/2 + 56px))}
.hero h1{font-weight:800;font-size:clamp(54px,7.6vw,116px);line-height:.88;letter-spacing:-.055em;text-transform:uppercase;margin:28px 0 36px}
.hero h1 em{font-style:normal;color:var(--lime)}.lead{font-size:18px;line-height:1.65;max-width:30em;color:#e6ebff;margin:0 0 38px}
.ctas{display:flex;gap:34px;align-items:center;flex-wrap:wrap}
.disc{position:absolute;right:-9%;top:72px;width:min(790px,58vw);aspect-ratio:1;border-radius:50%;background:var(--lime);z-index:1}
.core{position:absolute;left:19%;top:19%;width:57%;aspect-ratio:1;border-radius:50%;background:var(--ink);display:flex;flex-direction:column;align-items:center;justify-content:center;
box-shadow:-24px 24px 0 #d0e03c}.core b{font:800 clamp(64px,8vw,116px)/1 var(--f-t);letter-spacing:-.05em}.core small{color:var(--lime);font:700 10px var(--f-b);letter-spacing:.24em;text-transform:uppercase;margin-top:6px}
.ring{position:absolute;border:1px solid #ffffff55;border-radius:50%;z-index:2;pointer-events:none}
.r1{width:min(780px,58vw);aspect-ratio:1;right:2%;top:90px}.r2{width:min(660px,48vw);aspect-ratio:1;right:11%;top:140px}.r3{width:min(900px,66vw);aspect-ratio:1;right:-14%;top:-40px}
.stk{position:absolute;z-index:4;background:#fff;color:var(--ink);font:700 9.5px var(--f-b);letter-spacing:.14em;text-transform:uppercase;padding:11px 14px}
.s1{left:58%;top:200px;transform:rotate(-4deg)}.s2{left:56%;top:480px;transform:rotate(5deg)}.s3{right:3%;top:560px;background:var(--orange);transform:rotate(-6deg);z-index:5}
.uni{position:absolute;z-index:4;right:5%;bottom:22px;margin:0;text-align:right;font:500 9px var(--f-b);letter-spacing:.2em;text-transform:uppercase;color:#fff9;line-height:1.7}
.strip{background:#111;color:#fff}.strip-in{max-width:1900px;margin:0 auto;display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr 1fr}
.strip-t{padding:40px 56px;font:500 10px var(--f-b);letter-spacing:.22em;text-transform:uppercase;color:#ffffff88;align-self:center}
.strip a{display:grid;grid-template-columns:1fr auto;grid-template-rows:auto auto;padding:30px 32px 32px;border-left:1px solid #ffffff22;align-items:end;transition:background .15s}
.strip a:hover{background:#1c1c1c}.strip small{grid-column:1/-1;color:var(--lime);font:700 9px var(--f-b);letter-spacing:.1em;margin-bottom:6px}.strip b{font:600 14px var(--f-b)}
/* secciones */
.sec{padding:54px 0 90px}.cream{background:var(--cream)}.white{background:#fff}
.sec-h{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;margin-bottom:34px;flex-wrap:wrap}
.sec-h h2,.band h2{font-size:clamp(38px,5vw,66px);letter-spacing:-.045em;line-height:1}
.sec-h .lnk-u{font:700 11px var(--f-b);letter-spacing:.14em;text-transform:uppercase;display:inline-flex;gap:12px;align-items:center;padding-bottom:6px;border-bottom:2px solid var(--ink)}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;align-items:start}
.card{background:#fff;border:1px solid var(--line);display:flex;flex-direction:column;transition:box-shadow .2s,transform .2s}
.card:hover{box-shadow:0 12px 34px #0000001a;transform:translateY(-2px)}
.art{display:block;position:relative;aspect-ratio:420/205;overflow:hidden}.art-svg{display:block;width:100%;height:100%}
.tag{position:absolute;left:18px;top:18px;background:#fff;color:var(--ink);font:700 9px var(--f-b);letter-spacing:.16em;text-transform:uppercase;padding:9px 12px}
.dnum{position:absolute;right:20px;bottom:14px;text-align:center;line-height:1}.dnum b{display:block;font:800 52px/1 var(--f-t);letter-spacing:-.05em}.dnum small{font:700 9px var(--f-b);letter-spacing:.3em;margin-left:.3em;text-transform:uppercase}
.tinta-claro .dnum{color:#fff}.tinta-oscuro .dnum{color:#111}
.card-b{padding:22px 22px 22px}.when{font:700 9.5px var(--f-b);letter-spacing:.12em;text-transform:uppercase;margin:0 0 14px;color:#444}
.card h3{font-size:22px;line-height:1.2;letter-spacing:-.03em;font-weight:600;margin-bottom:22px}.card h3 a:hover{text-decoration:underline}
.meta-s{font:600 8.5px var(--f-b);letter-spacing:.12em;text-transform:uppercase;color:#77756f;margin:0 0 8px}
.desc{font-size:12.5px;color:#6a6862;margin:0 0 18px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.meta{list-style:none;margin:0 0 20px;padding:0;display:grid;gap:7px;font-size:13px;color:#5e5c57}.meta li{display:flex;gap:10px;align-items:flex-start}.meta .ico{margin-top:2px;opacity:.8}
.f-add{margin:0}.add{width:100%;height:42px;display:flex;align-items:center;justify-content:center;gap:9px;border:1px solid var(--ink);background:#fff;font:700 10.5px var(--f-b);letter-spacing:.14em;text-transform:uppercase;cursor:pointer}
.add:hover{background:#f0efe9}.add.on{background:var(--ink);color:#fff}
.band{background:#111;color:#fff;overflow:hidden}.band-in{display:grid;grid-template-columns:1.4fr 1fr;align-items:center;padding-top:110px;padding-bottom:110px;position:relative}
.band h2{max-width:11em;margin-bottom:26px}.lead-d{color:#bdbdb7;font-size:17px;max-width:30em;margin:0 0 34px}
.anillo{justify-self:end;width:min(480px,44vw);aspect-ratio:1;border-radius:50%;border:80px solid var(--blue);margin-right:-130px;position:relative}
.anillo span{position:absolute;left:50%;bottom:-62px;font:700 10px var(--f-b);letter-spacing:.2em;text-transform:uppercase}
/* encabezado de páginas internas */
.ph{background:#111;color:#fff;padding:78px 0 70px}.ph-in{display:flex;justify-content:space-between;align-items:flex-end;gap:40px;flex-wrap:wrap}
.ph h1{font-size:clamp(46px,7vw,88px);letter-spacing:-.055em;line-height:.95;font-weight:700}.h1-m{font-size:clamp(34px,4.6vw,60px)!important;max-width:20em}
.ph-p{max-width:25em;color:#bdbdb7;font-size:15.5px;margin:0 0 10px}.ph-sub{color:#bdbdb7;font-size:17px;margin:20px 0 0}.back{display:inline-block;margin-bottom:28px;font-size:13px;color:#bbb}
.ph-ag{align-items:flex-end}
/* filtros */
.white .wrap{padding-top:26px;padding-bottom:28px}.filtros{display:grid;gap:12px}
.busca{display:flex;align-items:center;gap:14px;border:1px solid #cfcdc6;background:#fff;padding:0 20px;height:62px}.busca input{flex:1;border:0;outline:0;background:transparent;font-size:16px;min-width:0}
.fgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.fld{display:flex;flex-direction:column;justify-content:center;border:1px solid #cfcdc6;background:#fff;padding:0 18px;height:62px;position:relative}
.fld small{font:700 7.5px var(--f-b);letter-spacing:.18em;text-transform:uppercase;color:#555;margin-bottom:3px}
.fld select{appearance:none;border:0;background:transparent;font-size:14.5px;outline:0;padding:0 28px 0 0;cursor:pointer;width:100%}
.fld::after{content:"";position:absolute;right:18px;top:50%;width:7px;height:7px;border-right:2px solid #111;border-bottom:2px solid #111;transform:translateY(-70%) rotate(45deg);pointer-events:none}
.fila-f{display:grid;grid-template-columns:1fr 280px;gap:12px}
.count-row{display:flex;justify-content:space-between;border-bottom:1px solid #111;padding:0 0 12px;margin-bottom:34px;font:700 11px var(--f-b);letter-spacing:.16em;text-transform:uppercase}
.tabs{display:flex;gap:22px}.tabs a{color:#8a8883}.tabs a.on{color:#111}
.vacio{padding:40px 0;color:#555}.vacio a{text-decoration:underline}.vacio.grande{font-size:20px;padding:60px 0}
.pager{display:flex;justify-content:center;gap:28px;align-items:center;margin-top:46px;font:700 11px var(--f-b);letter-spacing:.12em;text-transform:uppercase}.pager a{border-bottom:2px solid #111;padding-bottom:3px}
.dia-t{font-size:22px;letter-spacing:-.03em;margin:34px 0 12px;display:flex;gap:12px;align-items:center}.dia-t small{font:700 10px var(--f-b);background:#111;color:#fff;padding:3px 9px;border-radius:20px}
.lista{border-top:1px solid #111}
.fila{display:grid;grid-template-columns:170px 1fr 150px 280px;gap:18px;padding:14px 14px;border-bottom:1px solid var(--line);background:#fff;align-items:center;font-size:13.5px;border-left:6px solid #111}
.fila .h{font:700 10.5px var(--f-b);letter-spacing:.08em;text-transform:uppercase}.fila b{font-weight:600;font-family:var(--f-t)}.fila .t,.fila .l{color:#6a6862;font-size:12.5px}
.fila:hover{background:#faf9f5}.fila.g-dialogo{border-left-color:var(--blue)}.fila.g-competencia{border-left-color:var(--orange)}.fila.g-practica{border-left-color:var(--lime2)}.fila.g-expo{border-left-color:var(--purple)}.fila.g-cultural{border-left-color:var(--lime)}
.fila-ag{grid-template-columns:170px 1fr 120px 240px 190px}.fila-ag .f-add{margin:0}
/* detalle claro */
.det-h{padding-bottom:56px}.det{display:grid;grid-template-columns:1.6fr 1fr;gap:56px;padding-top:30px}
.big-p{font-size:19px;line-height:1.6;margin:0 0 34px;max-width:36em}.sub{font-size:13px;letter-spacing:.14em;text-transform:uppercase;font-family:var(--f-b);font-weight:700;margin-bottom:12px}
.ses-l{display:grid;gap:10px;margin-bottom:30px}.ses{background:#fff;border:1px solid var(--line);padding:14px 18px;display:flex;gap:18px;flex-wrap:wrap;align-items:center;font-size:14px}.ses b{min-width:90px}
.acc-row{display:flex;gap:12px;max-width:560px}.acc-row form{flex:1}
.det-side dl{margin:0;background:#fff;border:1px solid var(--line);padding:8px 24px}.det-side dt{font:700 9.5px var(--f-b);letter-spacing:.16em;text-transform:uppercase;color:#777;margin-top:18px}.det-side dd{margin:4px 0 18px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:14px}.det-side dd:last-child{border:0}
/* ponentes */
.pgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}.pcard{background:#fff;border:1px solid var(--line);display:flex;flex-direction:column}
.pav{display:block;position:relative;aspect-ratio:312/270;overflow:hidden}.av-svg{width:100%;height:100%;display:block}
.ini{position:absolute;left:0;right:0;top:36%;text-align:center;color:#fff;font:700 clamp(30px,3vw,42px)/1 var(--f-t);letter-spacing:-.02em}
.pb{padding:16px 20px 22px;display:flex;flex-direction:column;gap:10px;flex:1}.pt{display:flex;justify-content:space-between;align-items:center}.pt small{font-size:10px;color:#777}
.chip{background:var(--cream);font:700 7.5px var(--f-b);letter-spacing:.12em;text-transform:uppercase;padding:5px 8px}
.pcard h3{font-size:19px;letter-spacing:-.02em;font-weight:600;line-height:1.2}.rol{margin:0;font-size:12.5px;color:#6a6862;min-height:38px}.mail{margin:6px 0 4px;font-size:12px;color:#555;display:flex;gap:8px;align-items:center}.mail.dim{color:#8a8883}
.velo{position:fixed;inset:0;background:#000000a6;z-index:70}.drawer{position:fixed;top:0;right:0;bottom:0;width:min(540px,100vw);background:var(--cream);z-index:80;overflow:auto;box-shadow:-20px 0 60px #0006;animation:entra .22s ease-out}
@keyframes entra{from{transform:translateX(40px);opacity:0}to{transform:none;opacity:1}}
.dr-h{background:#111;color:#fff;padding:40px 36px 34px;position:relative}.cerrar{position:absolute;right:20px;top:20px;width:42px;height:42px;border:1px solid #ffffff66;display:grid;place-items:center;color:#fff}.cerrar:hover{background:#fff;color:#111}
.dr-av{width:92px;height:92px;border-radius:50%;background:var(--lime);color:#111;display:grid;place-items:center;font:700 32px var(--f-t);margin-bottom:22px}
.dr-h h2{font-size:38px;letter-spacing:-.04em;line-height:1.05;margin-bottom:12px}.dr-s{color:#ccc;font-size:16px;margin:0 0 14px}.dr-m{display:flex;gap:10px;align-items:center;font-size:13px;margin:0}
.dr-b{padding:34px 36px 60px}.dr-t{display:flex;justify-content:space-between;border-bottom:1px solid #111;padding-bottom:10px;margin-bottom:22px;font:700 10px var(--f-b);letter-spacing:.16em;text-transform:uppercase}
.dr-d{font:700 10px var(--f-b);letter-spacing:.16em;text-transform:uppercase;margin:26px 0 12px}.dr-c{background:#fff;border:1px solid var(--line);padding:20px;margin-bottom:14px}.dr-c h4{font-size:19px;letter-spacing:-.02em;font-weight:600;margin:12px 0 6px}
.dr-c p{margin:0 0 5px;font-size:12px;color:#555}.dr-c .dim{color:#777}.dr-c .dep{font:700 8px var(--f-b);letter-spacing:.1em;text-transform:uppercase;margin-top:10px}
.tag-s{border:1px solid #ccc;font:700 8.5px var(--f-b);letter-spacing:.14em;text-transform:uppercase;padding:5px 9px;display:inline-block}
.dr-a{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:center;margin-top:14px}
/* mi agenda (calendario) */
.cal-sc{overflow-x:auto;border:1px solid var(--line);background:#fff}.cal{min-width:860px}
.cal-head{display:grid;grid-template-columns:90px repeat(var(--cols),1fr);border-bottom:1px solid var(--line)}.cal-head div{padding:18px 0;text-align:center;border-left:1px solid var(--line)}
.cal-head small{font:700 10px var(--f-b);letter-spacing:.1em;color:var(--blue);margin-right:6px}.cal-head b{font:700 28px var(--f-t);letter-spacing:-.03em}
.cal-body{display:grid;grid-template-columns:90px repeat(var(--cols),1fr);position:relative;background:repeating-linear-gradient(#fff 0 calc(var(--px) - 1px),#eceae4 calc(var(--px) - 1px) var(--px))}
.cal-h{position:relative;grid-column:1}.cal-h span{position:absolute;right:12px;font-size:11px;color:#777;transform:translateY(-50%);background:#fff;padding:0 2px}.cal-h span:first-child{transform:translateY(2px)}
.cal-c{position:relative;border-left:1px solid var(--line)}
.bl{position:absolute;padding:7px 9px;border:2px solid #111;overflow:hidden;font-size:11px;line-height:1.25;display:flex;flex-direction:column;gap:2px}.bl b{font:700 12px var(--f-t);letter-spacing:-.01em}.bl em{font-style:normal;opacity:.85}
.bl:hover{z-index:5;overflow:visible;box-shadow:0 6px 20px #0004}
.bl.v0{background:var(--blue);color:#fff}.bl.v1{background:var(--orange);color:#111}.bl.v2{background:var(--lime2);color:#111}.bl.v3{background:var(--purple);color:#111}.bl.v4{background:var(--lime);color:#111}
/* footer */
.foot{background:#111;color:#fff;padding:46px 0}.foot .wrap{display:flex;justify-content:space-between;gap:30px;flex-wrap:wrap;align-items:flex-end}.foot p{margin:10px 0 0;font-size:13px;color:#bbb;line-height:1.6}.foot-n{font-size:11px!important;max-width:28em;text-align:right}
/* ═══════════ TEMA OSCURO (Agenda semanal y Salones) ═══════════ */
body.dark{--bg:#12141a;--panel:#1c2029;--panel2:#242936;--dl:#2c3240;--txt:#f2f4f8;--mut:#9ba5b7;--acc:#fbc02d;background:var(--bg);color:var(--txt)}
.dark .top{background:var(--bg);border-color:var(--dl)}.dark .brand{font-family:var(--f-s)}.dark .brand b{font:700 17px var(--f-s);letter-spacing:0}.dark .brand small{color:var(--mut)}
.dark .top nav{align-items:center}.dark .top nav a{height:36px;border-radius:30px;padding:0 15px;margin:0 2px;color:#e8eaf0}.dark .top nav a.on{background:#f4f4f4;color:#111}.dark .top nav a.on::after{display:none}
.dark .btn-agenda{background:var(--acc);border:0;border-radius:30px;color:#111;padding:11px 20px;font:700 13px var(--f-b);letter-spacing:0;text-transform:none;white-space:nowrap;gap:6px}.dark .btn-agenda .n{background:transparent;color:#111;width:auto;height:auto;font-size:13px;display:inline}.dark .btn-agenda .n::before{content:"("}.dark .btn-agenda .n::after{content:")"}
.dark .btn-agenda .ico{display:none}
.wrap-d{max-width:1240px;margin:0 auto;padding:0 28px}.wrap-d.narrow{max-width:900px}
.dk-top{padding:48px 0 22px}.dk-h{font:700 clamp(38px,5vw,56px)/1.05 var(--f-s);margin:0 0 10px}.dk-p{color:var(--mut);margin:0 0 26px;font-size:15.5px}
.dk-f{display:grid;gap:12px}.dark .busca{background:var(--panel);border:1px solid var(--dl);border-radius:12px;height:50px;padding:0 18px;color:var(--mut)}.dark .busca input{font-size:15px;color:var(--txt)}.dark .busca input::placeholder{color:#7d879a}
.dk-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.dk-grid label{display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--dl);border-radius:12px;padding:8px 14px 6px;position:relative}
.dk-grid small{font:700 9px var(--f-b);letter-spacing:.14em;text-transform:uppercase;color:var(--mut)}
.dk-grid select,.sal-f select{appearance:none;background:transparent;border:0;color:var(--txt);outline:0;font-size:14.5px;cursor:pointer;padding:2px 22px 2px 0;width:100%}
.dk-grid select option,.sal-f select option{background:#1c2029;color:#f2f4f8}.dk-grid label::after{content:"";position:absolute;right:14px;top:55%;width:6px;height:6px;border-right:2px solid var(--mut);border-bottom:2px solid var(--mut);transform:translateY(-60%) rotate(45deg);pointer-events:none}
.dk-main{padding:6px 0 90px}.dk-lbl{font:700 10.5px var(--f-b);letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin:0 0 10px}.dk-lbl.sep{border-top:1px solid var(--dl);padding-top:18px;margin-top:22px}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}.chip-d{background:var(--acc);color:#111;font:600 12.5px var(--f-b);padding:8px 16px;border-radius:30px;max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip-d:hover{filter:brightness(1.08)}
.dk-count{display:flex;justify-content:space-between;gap:20px;align-items:center;flex-wrap:wrap;margin:6px 0 14px;font-size:13px;color:var(--mut)}.dk-count b{color:var(--txt);font:700 11px var(--f-b);letter-spacing:.12em;text-transform:uppercase}
.dk-count a{color:var(--acc);border-bottom:1px solid}.leyenda{display:flex;gap:16px;flex-wrap:wrap;font-size:12px}.leyenda i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}.lg{white-space:nowrap}
.g-dialogo{--gc:#1f9d8a}.g-competencia{--gc:#7b4be0}.g-practica{--gc:#2878b5}.g-expo{--gc:#c2477f}.g-cultural{--gc:#b8860b}
.dark .leyenda i,.dark .bar,.dark .pill{background:var(--gc)}
.vacio-d{padding:40px 0;color:var(--mut)}
/* línea de tiempo */
.tl-sc{overflow-x:auto;border:1px solid var(--dl);border-radius:14px;background:var(--panel);padding-bottom:6px}
.tl{width:calc(96px + var(--ancho))}
.tl-day{display:grid;grid-template-columns:96px var(--ancho);border-bottom:1px solid var(--dl);padding-bottom:10px}.tl-day:last-child{border:0}
.tl-label{position:sticky;left:0;z-index:5;background:var(--panel);padding:34px 0 0 18px;display:flex;flex-direction:column;border-right:1px solid var(--dl)}
.tl-label b{font:700 13px var(--f-b);letter-spacing:.06em}.tl-label span{font:700 28px/1.1 var(--f-t);margin:2px 0}.tl-label small{color:var(--mut);font-size:11px}
.tl-body{position:relative;background:repeating-linear-gradient(90deg,transparent 0 calc(var(--pxh) - 1px),#ffffff12 calc(var(--pxh) - 1px) var(--pxh))}
.tl-ruler{height:30px;position:relative;border-bottom:1px solid var(--dl);margin-bottom:6px}.tl-ruler span{position:absolute;top:8px;font-size:11px;color:var(--mut);padding-left:6px;white-space:nowrap}
.tl-row{position:relative;height:30px;overflow:hidden}
.bar{position:absolute;top:2px;height:26px;border-radius:6px;display:flex;align-items:center;padding:0 10px;font:600 12.5px var(--f-b);color:#fff;white-space:nowrap;overflow:visible;z-index:1}
.bar span{pointer-events:none;text-shadow:0 1px 2px #0006}.bar:hover{filter:brightness(1.18);z-index:3}.bar.sel{outline:2px solid #fff;outline-offset:1px;z-index:4}
/* detalle oscuro */
.dt{position:relative;display:grid;grid-template-columns:1.5fr 1fr;gap:48px;background:var(--panel);border:1px solid var(--dl);border-radius:16px;padding:34px 34px 34px;scroll-margin-top:90px}
.dt-x{position:absolute;right:16px;top:16px;width:40px;height:40px;border-radius:50%;border:1px solid var(--dl);display:grid;place-items:center;color:var(--txt);background:var(--panel2);z-index:2}.dt-x:hover{background:var(--acc);color:#111;border-color:var(--acc)}
.pill{display:inline-block;color:#fff;font:700 12px var(--f-b);padding:5px 12px;border-radius:30px}.lema{color:var(--mut);margin:12px 0 10px}
.dt h1{font:700 clamp(26px,3.4vw,40px)/1.2 var(--f-s);margin:0 36px 14px 0}.tp{margin:0 0 12px;font-size:15.5px}
.ses-d{margin:0 0 14px;padding:0 0 0 20px;display:grid;gap:7px;font-size:15px}.ses-d li span{display:block;font-size:12.5px;color:var(--mut)}.ses-d li.actual{color:var(--acc);font-weight:600}
.desc-d{color:var(--mut);font-size:13.5px;max-width:42em;margin:0 0 22px}
.acc-d{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.acc-d form{margin:0}
.b-reg{background:var(--acc);color:#111;border:0;border-radius:30px;padding:12px 22px;font:700 14px var(--f-b);cursor:pointer;display:inline-flex;gap:8px;align-items:center}.b-reg.ok{background:#3cc47c}
.b-out{background:transparent;color:var(--txt);border:1px solid #cfd4de;border-radius:30px;padding:11px 20px;font:600 14px var(--f-b);cursor:pointer}.b-out:hover{background:var(--panel2)}
.b-baja{background:none;border:0;color:var(--txt);text-decoration:underline;font:500 14px var(--f-b);cursor:pointer;padding:0}
.dt-r{margin:34px 0 0;display:grid;align-content:start}.dt-r>div{border-bottom:1px solid var(--dl);padding:14px 0}.dt-r>div:first-child{padding-top:0}
.dt-r dt{display:flex;gap:10px;align-items:center;font:700 10.5px var(--f-b);letter-spacing:.12em;text-transform:uppercase;color:var(--mut)}
.dt-r dd{margin:6px 0 0 26px;font-size:16px}.dt-r dd a{text-decoration:underline;text-underline-offset:3px}.dt-r dd small{display:block;color:var(--mut);font-size:12px;margin-top:2px}
/* salones */
.sal-f{display:grid;grid-template-columns:1fr 200px;gap:10px}.sal-f .busca{border-radius:30px}.sal-f select{background:var(--panel);border:1px solid var(--dl);border-radius:30px;padding:0 16px;height:50px}
.sal{background:var(--panel);border:1px solid var(--dl);border-radius:14px;margin-bottom:10px;scroll-margin-top:90px}
.sal summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:16px 22px}.sal summary::-webkit-details-marker{display:none}
.sal summary b{display:block;font-size:16px}.sal summary small{color:var(--mut);font-size:13px}
.sal summary i{width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;border-top:9px solid var(--txt);transition:transform .2s}.sal[open] summary i{transform:rotate(180deg)}
.sal-b{padding:4px 22px 20px;border-top:1px solid var(--dl)}.sal-d{font:700 11px var(--f-b);letter-spacing:.14em;color:var(--acc);margin:18px 0 8px}
.sal-e{display:grid;grid-template-columns:150px 1fr auto;gap:14px;align-items:center;padding:9px 12px;border-radius:8px;background:var(--panel2);margin-bottom:6px;border-left:4px solid var(--gc,#888);font-size:13.5px}
.sal-e:hover{background:#2d3343}.sal-e .h{color:var(--mut);font-size:12px}.sal-e em{font-style:normal;color:var(--mut);font-size:12px}
.dark .foot{background:#0c0e12}
/* responsive */
@media(max-width:1100px){.cards{grid-template-columns:repeat(2,1fr)}.pgrid{grid-template-columns:repeat(3,1fr)}.fgrid,.dk-grid{grid-template-columns:repeat(2,1fr)}
.strip-in{grid-template-columns:1fr 1fr}.strip-t{grid-column:1/-1;padding:24px 28px 8px}.fila,.fila-ag{grid-template-columns:150px 1fr}.fila .t,.fila .l,.fila-ag .q{grid-column:2}
.det,.dt{grid-template-columns:1fr}.band-in{grid-template-columns:1fr}.anillo{display:none}}
@media(max-width:860px){.top-in{grid-template-columns:1fr auto;height:auto;padding:10px 18px}.top nav{grid-column:1/-1;order:3;height:auto;overflow-x:auto;margin:0 -6px}.top nav a{height:44px;white-space:nowrap}.dark .top nav a{height:34px}
.wrap{padding:0 20px}.hero-in{padding-left:20px}.disc,.r1,.r2,.r3{opacity:.35}.stk{display:none}.pgrid{grid-template-columns:repeat(2,1fr)}.fila-f,.sal-f{grid-template-columns:1fr}.foot-n{text-align:left}}
@media(max-width:620px){.cards,.pgrid{grid-template-columns:1fr}.fgrid,.dk-grid{grid-template-columns:1fr}.fila,.fila-ag{grid-template-columns:1fr}.fila .t,.fila .l,.fila-ag .q{grid-column:1}
.sal-e{grid-template-columns:1fr}.wrap-d,.wrap-s{padding:0 16px}.btn-agenda span{display:none}.hero h1{font-size:54px}.acc-row{flex-direction:column}}
"""
CSS_VERSION = hashlib.md5(CSS.encode()).hexdigest()[:8]


@app.route("/static-css/estilos.css")
def estilos():
    return Response(CSS, mimetype="text/css", headers={"Cache-Control": "public, max-age=3600"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
