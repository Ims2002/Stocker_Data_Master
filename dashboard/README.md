# dashboard/

App Streamlit de solo lectura sobre `data/stocker.db` y el último modelo
en `models/`. Nunca escribe nada — todas las escrituras las hacen los
módulos de `src/` (ver [`../src/README.md`](../src/README.md)). Si algo
sale desactualizado aquí, el problema está en si corriste el pipeline
(`download.py` → `load.py` → `gold.py` → `model.py` → `predict.py`), no en
el dashboard.

## Lanzar

```bash
python -m streamlit run dashboard/Inicio.py
```

(`streamlit run ...` a secas puede fallar en Git Bash en Windows si el
directorio de scripts de pip no está en el PATH — usar `python -m
streamlit run ...` lo evita siempre.)

## Estructura

- **`Inicio.py`** — entrypoint y enrutador (`st.navigation`, navegación
  arriba en vez de sidebar) + tema visual compartido (`ui.py`). No pinta
  contenido propio, solo enruta a `views/`.
- **`ui.py`** — CSS del tema minimalista blanco/azul/negro (aprobado por
  el usuario a partir de mockups, 2026-08-06) y el wordmark "Stocker" de
  la cabecera. Los colores base (`.streamlit/config.toml`) y este CSS
  deben cambiarse juntos.
- **`data_access.py`** — toda la lógica de acceso a datos vive aquí, no
  en las páginas. Reutiliza `src/db.py`, `src/model.py`
  (`build_feature_matrix`, `FEATURE_NAMES`) y `src/predict.py`
  (`latest_model_path`) en vez de duplicar esa lógica.
- **`views/inicio.py`** — KPIs generales (nº tickers, última fecha
  cargada, nº predicciones, nº artículos de noticias) y cómo leer el
  resto del dashboard.
- **`views/predicciones.py`** — selector de ticker + horizonte (solo
  "día" activo, ver CONTEXTO.md) + gráfico histórico con el backtest del
  modelo superpuesto sobre ese ticker, marcando dónde empieza el test
  real (nunca visto en entrenamiento) para no confundir precisión
  "informativa" con la métrica oficial.
- **`views/importancia_de_features.py`** — importancia global de
  features (una sola, compartida por todos los tickers — el modelo no
  usa el ticker como feature).
- **`views/rendimiento_del_modelo.py`** — modelo vs. baselines
  obligatorios sobre el test temporal oficial. Si el `.joblib` cargado
  no tiene métricas guardadas (modelos entrenados antes de que
  `model.save_model()` empezara a persistirlas), lo avisa y pide
  reentrenar en vez de mostrar datos inventados o vacíos sin explicación.

**¿Por qué `views/` y no la clásica `pages/` de Streamlit?** Con
`st.navigation()`, tener además una carpeta llamada `pages/` junto al
entrypoint hace que Streamlit intente registrar la navegación "clásica"
automática A LA VEZ que la nuestra, y las dos colisionan (el propio
`Inicio.py` se autorregistra como página con la misma ruta que
`pages/0_Inicio.py`, y Streamlit lo rechaza). Usar `views/` evita ese
comportamiento heredado por completo — ver el comentario al principio de
`views/inicio.py`.

## Por qué Streamlit

Decisión del desarrollador (no hubo conflicto con CONTEXTO.md que
resolver): velocidad de construcción dentro del mes de desarrollo
disponible, soporta multi-página y multi-usuario de forma nativa
(`streamlit run` sirve a varios navegadores a la vez), y SQLite tolera
bien lecturas concurrentes (el dashboard nunca escribe). Dash habría dado
más control de layout a cambio de más tiempo de desarrollo — no
justificado para el alcance actual.

## Interfaz visual

Minimalista, base blanca con acentos azul (`#1D4ED8`) y negro
(`#0B0F19`) — decisión del usuario a partir de un par de mockups
comparados antes de escribir código (2026-08-06). Se implementa con tema
nativo de Streamlit (`.streamlit/config.toml`) + CSS inyectado
(`ui.py`), sin salir de Streamlit — se descartó migrar a Dash por el
mismo motivo de tiempo que en "Por qué Streamlit" más abajo.
`streamlit-shadcn-ui` queda como posible mejora futura si hace falta más
control de componentes.

## Probado con

`streamlit.testing.v1.AppTest` (ejecuta cada página server-side sin
necesitar navegador) contra una copia de la base de datos real de 208
tickers — las cuatro páginas cargan sin excepciones, incluyendo cambiar
de ticker y seleccionar un horizonte deshabilitado. Navegación entre
páginas probada con `AppTest.switch_page("views/<archivo>.py")`.
