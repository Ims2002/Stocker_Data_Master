# Despliegue en Streamlit Community Cloud

Cierra el punto pendiente de `ROADMAP_MVP.md` ("Fase 1 — decidir la
plataforma / crear la app"). Lo que se publica es la **demo con datos
congelados**: `data/stocker_demo.db` + `models_demo/`, solo lectura. El
pipeline NO corre en la nube; los datos avanzan cuando tú vuelves a
exportar y a hacer push.

## 1. Regenerar la demo con los datos ya corregidos

La `stocker_demo.db` versionada es del 27/08, anterior a la auditoría —
lleva los precios de media sesión y el histórico sin reajustar. Con la
base reparada ya en `data/stocker.db`:

```bat
python src\export_demo_db.py
```

Y los modelos: `models_demo/` todavía tiene los de agosto. Sustitúyelos
por los tres reentrenados después de la auditoría:

```bat
del models_demo\*.joblib
copy models\random_forest_h1_20260916180523.joblib  models_demo\
copy models\random_forest_h5_20260916180645.joblib  models_demo\
copy models\random_forest_h20_20260916181145.joblib models_demo\
```

Esto importa: "En qué se fija" carga el `.joblib` **por el nombre del
`model_version` que está guardado en `predictions`**. Si el `.db` y los
`.joblib` no son de la misma tanda, esa página falla en el despliegue
aunque en local funcione.

Comprobación antes de publicar nada (mismas variables que usará la nube):

```bat
set STOCKER_DB_PATH=data\stocker_demo.db
set STOCKER_MODELS_DIR=models_demo
python -m streamlit run dashboard\Inicio.py
```

Recorre las 8 páginas. Si alguna falla aquí, falla igual desplegada.

## 2. Subir los binarios al repo

`.gitignore` ya hace la excepción para estos dos (el resto de `data/` y
`models/` se sigue ignorando):

```bat
git add data\stocker_demo.db models_demo
git commit -m "demo: base y modelos posteriores a la auditoria"
git push
```

Dos avisos: el límite de GitHub es 100 MB por archivo (la demo pesa ~64
MB, entra), y **cada re-exportación añade otros ~64 MB al historial para
siempre** — no la regeneres por costumbre, solo cuando el salto de datos
lo justifique.

Si el repo va a ser público, recuerda que se publica *todo* lo que está
versionado: `CONTEXTO.md` (200 KB de notas internas) y `docs/entregas/`
incluidos.

## 3. El puente de variables de entorno

`src/config.py` lee `STOCKER_DB_PATH` / `STOCKER_MODELS_DIR` /
`ANTHROPIC_API_KEY` de `os.environ`, y en Streamlit Cloud lo que existe
es el cuadro de **Secrets** (TOML → `st.secrets`). Para que una cosa
alimente a la otra, al principio de `dashboard/Inicio.py`, **antes** de
`import ui` y de que cualquier vista importe `config`:

```python
import os

import streamlit as st

try:
    for _k in ("STOCKER_DB_PATH", "STOCKER_MODELS_DIR", "ANTHROPIC_API_KEY"):
        if not os.environ.get(_k) and _k in st.secrets:
            os.environ[_k] = str(st.secrets[_k])
except FileNotFoundError:  # en local no hay secrets.toml y no hace falta
    pass
```

Sin esto la app desplegada busca `data/stocker.db`, que no está en el
repo, y arranca con error.

## 4. Crear la app

1. [share.streamlit.io](https://share.streamlit.io) → entrar con GitHub y
   autorizar el repo.
2. **Create app** → *Deploy a public app from GitHub*.
3. Repositorio, rama `main`, **Main file path**: `dashboard/Inicio.py`.
4. **Advanced settings** → versión de Python: la misma que uses en local
   (3.12 si no tienes preferencia) → **Secrets**:

```toml
STOCKER_DB_PATH = "data/stocker_demo.db"
STOCKER_MODELS_DIR = "models_demo"
# ANTHROPIC_API_KEY = "sk-ant-..."   # solo si quieres el chat Premium en público (leer abajo)
```

5. **Deploy**. La primera instalación tarda unos minutos (`lightgbm` es
   la dependencia pesada y es opcional: si quieres acelerarla, coméntala
   en `requirements.txt` antes de desplegar; el pipeline por defecto usa
   `random_forest` y no la necesita).

A partir de ahí, cada push a `main` redespliega solo.

## 5. Lo que conviene saber de la plataforma

- **Se duerme sin visitas durante 12 horas** y la despierta cualquiera
  que abra el enlace (tarda unos segundos en arrancar).
- **Recursos**: hasta ~2,7 GB de RAM y 2 núcleos por app; la demo va
  sobrada.
- **El disco es efímero**: cualquier cosa que la app escriba se pierde al
  reiniciarse o al dormirse. El dashboard no escribe en la base de datos,
  así que solo afecta al contador del chat Premium (siguiente punto).
- **`ALPHA_VANTAGE_API_KEY` no hace falta**: el dashboard no llama a
  ninguna API de datos, solo lee la base.

## 6. Chat Premium en un despliegue público

Cada pregunta tiene coste real en tu cuenta de Anthropic. Los topes
(`PREMIUM_CHAT_MAX_PER_DAY_GLOBAL`, `..._PER_IP`) viven en
`dashboard/.premium_chat_usage.json`, que en la nube está en ese disco
efímero: **al reiniciarse la app los contadores diarios vuelven a cero**,
así que el límite de verdad pasa a ser tu saldo.

Recomendación para el enlace público: **no pongas `ANTHROPIC_API_KEY` en
los secrets**. La página lo detecta sola, enseña el aviso de chat no
disponible y el resto del contenido Premium se ve igual. Si lo activas,
hazlo con un límite de gasto puesto en la consola de Anthropic.

## 7. Actualizar la demo más adelante

```bat
python src\run_pipeline.py          REM datos frescos en la base real
python src\export_demo_db.py        REM vuelca los 50 tickers a la demo
git add data\stocker_demo.db && git commit -m "demo: datos hasta AAAA-MM-DD" && git push
```

Si en esa tanda has reentrenado, copia también los `.joblib` nuevos a
`models_demo/` (punto 1). El aviso "Datos actualizados hasta..." del
Dashboard es el que delata al visitante que está viendo una foto fija.

## 8. Tareas programadas mientras tanto

El despliegue es una copia congelada: **lo que corra en tu PC no puede
romper la app publicada**, y al revés. Lo único que comparten las dos
copias del proyecto (original y esta) es la cuota diaria de Alpha Vantage
(25 llamadas en el plan gratuito), así que no conviene programar las dos
a la vez. Detalle y recomendación de horario en `CAMBIOS_AUDITORIA.md` y
en la cabecera de `run_daily_pipeline.bat`: una única tarea diaria a las
22:30 (Madrid), de lunes a sábado, y la de noticias desactivada.
