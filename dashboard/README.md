# dashboard/

App Streamlit de solo lectura sobre `data/stocker.db` y el último modelo
en `models/`. Nunca escribe nada — todas las escrituras las hacen los
módulos de `src/` (ver [`../src/README.md`](../src/README.md)). Si algo
sale desactualizado aquí, el problema está en si corriste el pipeline
(`download.py` → `load.py` → `gold.py` → `model.py` → `predict.py`), no en
el dashboard. **Única excepción** (2026-08-31, ver `premium_chat.py` más
abajo): el chat de "Contenido Premium" escribe un contador de uso local
para controlar coste — nunca toca `stocker.db` ni `models/`.

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
  recuperan añadiendo la línea de vuelta). Desde el 2026-08-31 registra
  una 6ª página nueva, "Contenido Premium" (`views/premium.py`, no es
  parte del recorte de arriba — ver más abajo). Desde el 2026-09-05
  registra una 7ª, "Comparador" (`views/comparador.py`, tampoco parte
  del recorte — ver más abajo). **2026-09-08**: se quita "¿Funciona de
  verdad?" (`views/rendimiento_del_modelo.py`, mismo criterio de recorte
  — archivo intacto, solo deja de registrarse) y se reincorpora
  "Noticias de la acción" (`views/sentimiento_por_accion.py`, el
  backfill de noticias que motivó quitarla ya está completo) — ver
  CONTEXTO.md, "Quitar '¿Funciona de verdad?' y profundizar en noticias
  desde el Dashboard".
- **`ui.py`** — CSS del tema minimalista blanco/azul/negro (aprobado por
  el usuario a partir de mockups, 2026-08-06) y el logo real de la
  cabecera (`render_logo()`, actualizado 2026-08-28 — ver `assets/` más
  abajo). Los colores base (`.streamlit/config.toml`) y este CSS deben
  cambiarse juntos. **2026-08-31**: la línea inferior de la navbar usa un
  degradado azul marino (`_AZUL_MARINO = "#1E3A8A"`) → blanco, desvanecido
  en el último cuarto del ancho de pantalla. Se probó el mismo degradado
  en los `st.divider()` del aside de filtros, pero el usuario lo quitó
  ese mismo día — junto con los dos `st.divider()` de `views/dashboard.py`
  que los sostenían, para no dejar hueco en blanco donde iba la línea. El
  aside de filtros no tiene separadores por ahora. Ver CONTEXTO.md,
  "Separadores en degradado azul marino: navbar + aside de filtros".
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
  (`build_feature_matrix`, `FEATURE_NAMES`, `calibration_diagnostic`) y
  `src/predict.py` (`latest_model_path`) en vez de duplicar esa lógica.
  `prediction_confidence()` (2026-09-10, ver CONTEXTO.md "Feedback de un
  tutor: comunicación de predicciones poco concluyentes...") consolida
  en un solo sitio la conversión de `predicted_probability` (siempre
  P(sube)) a confianza en la dirección predicha, antes duplicada en
  `dashboard.py`/`predicciones.py`. (La constante
  `CONFIANZA_POCO_CONCLUYENTE_UMBRAL` que acompañaba a esta función se
  quitó el mismo día — ver más abajo, el usuario revirtió el aviso de
  "poco concluyente" que la usaba.)
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
  "Cabecera de marca en Dashboard". **Rediseño 2026-08-30** (ver
  CONTEXTO.md, "Medidor de sentimiento de mercado: reincorporado"): el
  semicírculo de sentimiento de mercado vuelve (como 4ª tarjeta en la
  fila de KPIs, no una sección propia), y el gráfico + noticias/lectura
  rápida pasan a dos columnas lado a lado en vez de apiladas a todo lo
  ancho. Ese mismo día se probó una segunda pasada (cajas de altura
  igualada, líneas de degradado azul marino, menos padding) que el
  usuario pidió revertir tras verla — se deshizo, quedando el layout tal
  como se describe aquí. Lo que sí se mantuvo: el enlace "Profundizar en
  Predicciones →" se quitó del todo (ya está en la navegación superior).
  El semicírculo de sentimiento ahora se dibuja con `streamlit-echarts`
  (Apache ECharts) en vez de `go.Indicator` de Plotly — misma paleta y
  aguja azul que antes, pero con degradado y animación propios de
  ECharts; elegida por el usuario frente a `streamviz` y frente a solo
  pulir el Plotly existente. Ver CONTEXTO.md, "Segunda pasada revertida +
  librería de gráficos para el semicírculo" y "Semicírculo de
  sentimiento: librería nueva (`streamlit-echarts`, 2026-08-30)".
  **2026-09-10** (feedback de un tutor, ver CONTEXTO.md "Feedback de un
  tutor: comunicación de predicciones poco concluyentes..."): el caption
  del medidor de sentimiento de mercado declara explícitamente su
  alcance ("todo el universo, últimos N días") y el de "Lectura rápida"
  el suyo ("solo {ticker}"), para que dos cifras de nº de artículos con
  ventanas distintas no se lean como inconsistentes entre sí; y un pie
  de página nuevo enlaza el pipeline real (`download.py → ... →
  predict.py`) que genera cada cifra de la página. Se probó también
  marcar como "⚖️ Poco concluyente" el KPI de predicción y "Lectura
  rápida" cuando la confianza caía por debajo del 55% (en vez de
  "📈 Sube"/"📉 Baja") — el usuario pidió revertirlo el mismo día, así
  que ambos vuelven a mostrar siempre la dirección predicha sin ese
  aviso (ver CONTEXTO.md para el detalle del revert).
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
  precisión "informativa" con la métrica oficial. **2026-09-10**
  (feedback de un tutor, ver CONTEXTO.md): caption de trazabilidad justo
  debajo del selector con `model_version`/fecha de entrenamiento/ventana
  de test del modelo activo para el horizonte elegido, más un
  `st.page_link` hacia "¿Funciona de verdad?" — antes esa evidencia solo
  existía en otra pestaña sin ningún enlace desde aquí. Mismo pie de
  trazabilidad del pipeline al final de la página que Dashboard (el
  aviso de "⚖️ Poco concluyente" que se probó el mismo día en el KPI de
  predicción se revirtió a petición del usuario — ver CONTEXTO.md).
- **`views/importancia_de_features.py`** — importancia global de
  features (una sola, compartida por todos los tickers — el modelo no
  usa el ticker como feature).
- **`views/rendimiento_del_modelo.py`** ("¿Funciona de verdad?" — quitada
  de la navegación el 2026-09-08, **REINCORPORADA el 2026-09-09** por un
  motivo distinto, ver Inicio.py y CONTEXTO.md "Feedback de un tutor del
  TFM: leakage en horizontes 5/20 y calibración de predicted_probability")
  — modelo vs. baselines obligatorios sobre el test temporal oficial, con
  selector de horizonte (día/semana/mes, nuevo — antes solo cargaba el
  horizonte día). Si el `.joblib` cargado no tiene métricas guardadas
  (modelos entrenados antes de que `model.save_model()` empezara a
  persistirlas), lo avisa y pide reentrenar en vez de mostrar datos
  inventados o vacíos sin explicación. Sección nueva al final,
  "¿Es fiable la probabilidad que muestra el modelo?": Brier score +
  curva de fiabilidad (confianza declarada vs. acierto real, calculados
  en vivo sobre el test oficial actual vía
  `data_access.get_calibration_diagnostic()`) — responde directamente al
  feedback de no vender `predicted_probability` como "confianza" sin
  comprobar calibración.
- **`views/seguimiento_real.py`** (nuevo, 2026-08-13) — acierto REAL en
  producción (`predictions.actual_target_up_down`, rellenado por
  `src/track_predictions.py`), no backtest. Regla de oro: nunca mezcla
  `model_version` distintos en el mismo cálculo de acierto — tabla
  resumen por versión + gráfico de evolución día a día de la versión
  elegida, con aviso explícito de que las ~208 predicciones de un mismo
  día NO son observaciones independientes (ver CONTEXTO.md, "Seguimiento
  real de predicciones").
- **`views/premium.py`** (nuevo, 2026-08-31; registrada en el v1 desde el
  mismo día) — "Contenido Premium": vista previa del futuro apartado de
  pago, con los 4 hyperscalers (AMZN, MSFT, GOOGL, META) en pestañas y
  un apartado "Próximamente" para NVDA y SpaceX. Lee directamente los `.md`
  de `docs/premium/` (fuera del pipeline, no toca la base de datos salvo
  para nombre/logo de cada ticker) — si un archivo todavía no existe
  para un ticker, muestra una tarjeta de "en preparación"/"Próximamente"
  en su lugar. Sin login ni cobro todavía, a propósito — ver
  CONTEXTO.md, "Contenido premium: página en el dashboard". Cada
  análisis lleva el logo de la empresa en una cabecera justo antes del
  contenido (mismo bucle sobre `HYPERSCALERS`, se aplica solo con
  añadir el `.md` — ver CONTEXTO.md, "Logo al inicio de cada análisis").
  Debajo del análisis de cada ticker con contenido, un chat experto
  (`premium_chat.py`) responde preguntas ancladas a esos documentos —
  ver CONTEXTO.md, "Chat experto sobre earnings: viabilidad y diseño".
- **`premium_chat.py`** (nuevo, 2026-08-31) — corpus + llamada a la API
  de Anthropic + límites de uso del chat de "Contenido Premium".
  Context-stuffing, no RAG vectorial (el corpus por ticker cabe entero
  en el contexto del modelo). El corpus es SOLO el análisis redactado +
  los documentos oficiales de `docs/premium/hyperscalers/<TICKER>_fuentes/`
  — nunca la transcripción del vídeo de terceros. **Única excepción
  documentada a la regla de "el dashboard nunca escribe nada"** (ver
  cabecera de este README): escribe un contador de uso diario en
  `dashboard/.premium_chat_usage.json` (fuera de git) para limitar el
  gasto de una función que, a diferencia del resto del dashboard, sí
  cuesta dinero por petición y todavía es pública sin login. Sin
  `ANTHROPIC_API_KEY` configurada, el chat se deshabilita solo con un
  aviso — el resto del dashboard sigue funcionando igual. Si tu clave de
  Anthropic es "identity-linked" (personal/de service account sin
  workspace fijado al crearla), necesitarás además `ANTHROPIC_WORKSPACE_ID`
  en `.env` — ver la nota en `src/config.py` y CONTEXTO.md, "Error del
  chat: `anthropic-workspace-id` requerido". La conversación va dentro
  de un `st.container(height=420, autoscroll=True)` (mensaje más
  reciente siempre visible, historial hacia arriba con scroll) con un
  estado vacío con preguntas sugeridas antes de escribir nada — ver
  CONTEXTO.md, "Estética del chat: caja acotada + estado vacío".
- **`views/comparador.py`** (nuevo, 2026-09-05) — "Comparador": parte del
  apartado free, no de "Contenido Premium" — deja elegir dos tickers
  cualquiera del universo (mismo selector sector→ticker que
  "Predicciones", factorizado para no duplicarlo al usarse dos veces en
  la misma página) y ver su precio en el mismo gráfico,
  **independientemente de cualquier predicción o backtest**. El precio se
  normaliza a base 100 en el inicio de la ventana elegida (no el precio
  bruto) porque comparar acciones con escalas muy distintas en el mismo
  eje Y no es legible — ver CONTEXTO.md, "Comparador de gráficos: dos
  acciones en un mismo gráfico". v1 a propósito: solo el gráfico de
  precio normalizado; el archivo deja dos puntos de extensión sin
  implementar (`_render_technical_panel()`, `_render_correlation_kpi()`)
  para añadir más adelante un panel de indicadores técnicos comparados y
  un KPI de correlación entre las dos series, sin tener que rehacer la
  página.
- **`views/sentimiento_por_accion.py`** ("Noticias de la acción", nueva
  2026-08-13; **REINCORPORADA a la navegación el 2026-09-08**, ver
  Inicio.py y CONTEXTO.md "Quitar '¿Funciona de verdad?' y profundizar en
  noticias desde el Dashboard") — cuadro de mando de noticias/sentimiento
  de UN ticker: KPIs (nº artículos, tono medio, días con cobertura),
  gráfico de tendencia (tono + volumen, eje doble) y titulares con slider
  de relevancia mínima. Se había quitado de la navegación del v1
  (2026-08-27) porque el backfill de Alpha Vantage todavía estaba en
  marcha; se reincorpora porque ya está completo (208/208 tickers,
  verificado con `da.news_coverage_status()`) y porque el Dashboard
  pedía un sitio al que profundizar: `views/dashboard.py` tiene un botón
  **"Ver todas las noticias →"** bajo su caja de 4 titulares que guarda
  el ticker elegido en `st.session_state["noticias_ticker"]` y navega
  aquí con `st.switch_page()`; esta página lee y consume esa clave
  (`.pop()`, de un solo uso) para preseleccionar el mismo ticker en vez
  de caer al ticker por defecto. Puramente informativa: la investigación
  del 2026-08-13 (CONTEXTO.md, "¿Ayuda el sentimiento de noticias a
  acertar más?") no encontró correlación real con la dirección del
  precio, así que no insinúa que el sentimiento predice nada. Maneja el
  caso de un ticker sin cobertura todavía mostrando un aviso en vez de
  gráficos vacíos.
- **`views/sentimiento_del_mercado.py`** (nueva, 2026-08-13; YA NO
  registrada, ver Inicio.py) — mismo espíritu que la anterior pero
  agregado de TODO el universo (KPI de cobertura del backfill, tendencia
  de tono ponderada por volumen, ranking de tickers más positivos/
  negativos, sentimiento medio por sector). Se quitó de la navegación
  del v1 por la misma razón que la anterior (backfill en marcha); a
  diferencia de "Noticias de la acción", el usuario no pidió
  reincorporarla el 2026-09-08 (solo pidió profundizar por ticker desde
  el Dashboard) — se queda como archivo sin registrar, candidata para una
  v2 si se decide más adelante.

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
