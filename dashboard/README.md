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
  contenido propio, solo enruta a `views/`. Desde el recorte a v1
  (2026-08-27, ver CONTEXTO.md "Roadmap v1 (MVP para publicar)") registra
  solo 5 páginas de las 8 que llegó a haber — las 3 que se quitaron
  siguen existiendo como archivo, solo dejaron de registrarse aquí (se
  recuperan añadiendo la línea de vuelta).
- **`ui.py`** — CSS del tema minimalista blanco/azul/negro (aprobado por
  el usuario a partir de mockups, 2026-08-06) y el logo real de la
  cabecera (`render_logo()`, actualizado 2026-08-28 — ver `assets/` más
  abajo). Los colores base (`.streamlit/config.toml`) y este CSS deben
  cambiarse juntos.
- **`assets/`** (nuevo, 2026-08-28) — identidad de marca real, a partir de
  un artifact de claude.ai que trajo el usuario (icono "K" con forma de
  gráfico de velas alcista/bajista + wordmark "STOCKER"). Contiene los 4
  SVG originales del usuario (`stocker_k_icon.svg`,
  `stocker_app_icon_dark.svg`, `stocker_app_icon_light.svg`, más el HTML
  del wordmark que ya no se usa tal cual) y `stocker_wordmark.svg`
  (generado, no a mano — ver `generate_wordmark.py` en ese mismo
  directorio para el porqué: el HTML original dependía de la fuente
  Space Grotesk vía Google Fonts, que `st.logo()` no puede cargar porque
  pinta una imagen, no ejecuta HTML/CSS; el texto se convirtió a
  contornos vectoriales con `fonttools`, usando Poppins Bold como
  sustituta ya instalada localmente, para que se vea igual en cualquier
  navegador sin depender de ninguna fuente). `ui.render_logo()` usa el
  wordmark completo cuando la barra lateral está abierta y el icono "K"
  suelto cuando está colapsada; `Inicio.py` usa
  `stocker_app_icon_dark.svg` como favicon (`page_icon`). Detalle
  completo en CONTEXTO.md, "Identidad de marca: logos del artifact de
  claude.ai".
- **`data_access.py`** — toda la lógica de acceso a datos vive aquí, no
  en las páginas. Reutiliza `src/db.py`, `src/model.py`
  (`build_feature_matrix`, `FEATURE_NAMES`) y `src/predict.py`
  (`latest_model_path`) en vez de duplicar esa lógica.
- **`views/dashboard.py`** (nuevo, 2026-08-25; recortado el mismo día
  tras el primer feedback; página de ENTRADA del v1 desde el 2026-08-27)
  — "Dashboard": implementación del mockup tipo SaaS aprobado por el
  usuario (ver CONTEXTO.md, "Dashboard unificado (mockup)"). Barra
  lateral con filtros (sector, ticker, horizonte, meses de histórico) que
  recentra TODO el contenido a la acción elegida — 3 KPIs de cabecera
  (predicción, probabilidad, último cierre), gráfico de precio con
  aciertos/fallos del backtest, tabla de noticias de una línea con color
  de sentimiento, y un párrafo de "Lectura rápida" con texto dinámico. Ya
  NO incluye el medidor de sentimiento general del mercado ni el
  KPI/enlace de "¿Funciona de verdad?" (quitados el 2026-08-25) ni el
  enlace a "Noticias de la acción" (quitado el 2026-08-27, esa página ya
  no está registrada en el v1). Solo enlaza a "Predicciones". Cabecera
  rehecha el 2026-08-28: ya no hay `st.title("Dashboard")` ni un
  `st.title("STOCKER")` (se probó y se quitó el mismo día, redundante con
  el wordmark "Stocker" que ya está en la barra de navegación superior).
  La cabecera es solo el subtítulo dinámico: logo del ticker + nombre de
  la empresa + `#TICKER`, en gris claro `#9CA3AF`, ver CONTEXTO.md
  "Cabecera de marca en Dashboard".
- **`views/inicio.py`** (página "Resumen" — YA NO registrada en el v1,
  ver Inicio.py) — primera versión de un dashboard unificado
  (2026-08-13, ampliado 2026-08-18, ver CONTEXTO.md): KPIs generales +
  resumen agregado de las predicciones más recientes + amplitud de
  mercado histórica + highlights de backtest + comparativa entre
  horizontes + acierto real en producción + importancia de features +
  acierto por régimen de volatilidad/confianza + sentimiento de mercado.
  Se quitó de la navegación del v1 por redundante con "Dashboard" (dos
  páginas contando una historia parecida); se queda el archivo por si se
  quiere recuperar en una v2 más completa.
- **`views/predicciones.py`** — selector de sector (`stocks.sector`,
  2026-08-18) + ticker (filtrado por el sector elegido) + horizonte (día
  activo desde el principio; semana/mes activos desde 2026-08-13 EN
  CUANTO haya un modelo entrenado para ellos — `model.py --horizon 5/20`,
  ver CONTEXTO.md "Horizontes de predicción: semana y mes" — si no,
  avisa en vez de fingir un resultado; los tres horizontes ya tienen
  modelo entrenado desde 2026-08-18) + gráfico histórico con el
  backtest del modelo superpuesto sobre ese ticker, marcando dónde
  empieza el test real (nunca visto en entrenamiento) para no confundir
  precisión "informativa" con la métrica oficial.
- **`views/importancia_de_features.py`** — importancia global de
  features (una sola, compartida por todos los tickers — el modelo no
  usa el ticker como feature).
- **`views/rendimiento_del_modelo.py`** — modelo vs. baselines
  obligatorios sobre el test temporal oficial. Si el `.joblib` cargado
  no tiene métricas guardadas (modelos entrenados antes de que
  `model.save_model()` empezara a persistirlas), lo avisa y pide
  reentrenar en vez de mostrar datos inventados o vacíos sin explicación.
- **`views/seguimiento_real.py`** (nuevo, 2026-08-13) — acierto REAL en
  producción (`predictions.actual_target_up_down`, rellenado por
  `src/track_predictions.py`), no backtest. Regla de oro: nunca mezcla
  `model_version` distintos en el mismo cálculo de acierto — tabla
  resumen por versión + gráfico de evolución día a día de la versión
  elegida, con aviso explícito de que las ~208 predicciones de un mismo
  día NO son observaciones independientes (ver CONTEXTO.md, "Seguimiento
  real de predicciones").
- **`views/sentimiento_por_accion.py`** / **`views/sentimiento_del_mercado.py`**
  (nuevas, 2026-08-13; YA NO registradas en el v1, ver Inicio.py) —
  cuadros de mando de noticias/sentimiento, por ticker (con el mismo
  selector de sector que "Predicciones", añadido 2026-08-18) y agregado
  de mercado. Puramente informativas: la investigación del mismo día
  (CONTEXTO.md, "¿Ayuda el sentimiento de noticias a acertar más?") no
  encontró correlación real con la dirección del precio, así que ninguna
  de las dos insinúa que el sentimiento predice nada. Se quitaron de la
  navegación del v1 porque dependen del backfill de Alpha Vantage (~9
  días desde cero) y no sostienen el valor central del proyecto
  (predicción + honestidad de resultado) — quedan los archivos para una
  v2. Manejan el caso de tickers sin cobertura todavía mostrando un aviso
  en vez de gráficos vacíos.

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
necesitar navegador) contra la base de datos real de 208 tickers — las
ocho páginas cargan sin excepciones, incluyendo cambiar de ticker,
cambiar entre los tres horizontes en "Predicciones" (los tres con modelo
real entrenado desde 2026-08-18), filtrar por sector en "Predicciones" y
"Noticias de la acción" (verificado que el selector de ticker se
actualiza a solo los tickers de ese sector), mover el slider de umbral de
confianza en "Resumen", cambiar de `model_version` en "Día a día", y ver
un ticker sin cobertura de noticias todavía en "Noticias de la acción".
Navegación entre páginas probada con
`AppTest.switch_page("views/<archivo>.py")` — necesario también para
probar "Dashboard": `st.page_link` solo resuelve rutas dentro del
contexto de navegación completo (`Inicio.py` + `switch_page`), falla con
`KeyError: 'url_pathname'` si se prueba el archivo de la vista aislado
con `AppTest.from_file()` directo (error del arnés de pruebas, no un bug
real). En "Dashboard" se probó además el recentrado completo al cambiar
de ticker/sector/horizonte y los extremos del slider de meses.
