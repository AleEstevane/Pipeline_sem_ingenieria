# UACJ · Semana de Ingeniería (sitio web)

Sitio hecho 100 % en Python (Flask). **Todo el código vive en `app.py`** (datos, rutas, plantillas Jinja y CSS).
Lee los CSV del ETL tal cual están en `PROYECTO_PIPELINE/Entregable_ETL_DB/` — esa carpeta y el resto de
`PROYECTO_PIPELINE/` no se modificaron.

## Secciones
Inicio · Explorar (tarjetas/agenda + filtros) · Ponentes (panel lateral) · **Agenda semanal** (tema oscuro,
barras con nombre corto → clic = detalle con ✕ que devuelve los filtros) · **Salones** · Mi agenda (+ exportar .ics).

## Correr local
```bash
pip install -r requirements.txt
python app.py          # http://localhost:5000
```

## Desplegar en Render
1. Sube esta carpeta a un repositorio de GitHub.
2. En Render: **New → Blueprint** (usa `render.yaml`) — o **New → Web Service** con:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
3. Opcional: define la variable `SECRET_KEY` (el blueprint ya la genera).

Notas: "Mi agenda" y "Registrarme" se guardan en la cookie de sesión del navegador (no hay base de datos de usuarios).
