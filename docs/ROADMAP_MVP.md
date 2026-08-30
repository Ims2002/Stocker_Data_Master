# Roadmap v1 (MVP para publicar)

Decisiones tomadas el 2026-08-27 (detalle y porqué en `CONTEXTO.md`,
"Roadmap v1 (MVP para publicar)"):

- **"Publicar" = demo desplegada con datos congelados.** Una foto de la
  base de datos en el momento de publicar, sin pipeline corriendo en la
  nube ni actualización automática. No hace falta resolver scheduling en
  la nube ni exponer `ALPHA_VANTAGE_API_KEY` — el dashboard nunca llama a
  ninguna API en tiempo real, es de solo lectura sobre `stocker.db` /
  `models/`.
- **Navegación del v1 (5 páginas)**: Dashboard (entrada) → Predicciones →
  En qué se fija → ¿Funciona de verdad? → Día a día. "Resumen",
  "Noticias de la acción" y "Sentimiento del mercado" se quitaron de la
  navegación (archivos intactos, se recuperan añadiendo una línea en
  `dashboard/Inicio.py`).
- **Universo de la demo: 50 tickers**, reutilizando
  `config.NEWS_PRIORITY_TICKERS` (los más relevantes, ya definidos para
  la rotación de noticias) — así la base de datos de la demo pesa ~60MB
  en vez de 254MB y cabe en un repo normal de GitHub (límite 100MB por
  archivo), sin Git LFS ni hosting externo. Los modelos NO se recortan
  ni reentrenan: no usan el ticker como feature, sirven igual sobre el
  subconjunto.
- **Plazo: esta semana.** Roadmap deliberadamente ajustado — lo que no
  es imprescindible para tener ALGO desplegado y funcionando pasa a la
  "Fase 3 (fuera del v1)" de abajo, no se descarta, solo se pospone.

Cada tarea indica quién la hace: **[yo]** = la puedo dejar lista en este
entorno (código, scripts, documentación); **[tú]** = requiere tus
cuentas/navegador/decisión y no puedo hacerla por ti.

## Fase 0 — Ya hecho

- [x] Recortar la navegación a las 5 páginas del v1, "Dashboard" como
      página de entrada. **[yo]**
- [x] Arreglar el enlace de pie de página de "Dashboard" que apuntaba a
      una página ya no registrada. **[yo]**
- [x] Verificar con `AppTest` contra la base de datos real que las 5
      páginas cargan sin excepción. **[yo]**
- [x] Confirmar que el dashboard no depende de `ALPHA_VANTAGE_API_KEY` en
      tiempo de ejecución (revisado `data_access.py`: no importa
      `requests` ni nada de `news.py`). **[yo]**
- [x] Modelos de horizonte semana/mes reentrenados y con predicciones
      frescas (26/08/2026). **[tú, ya hecho]**

## Fase 1 — Bloqueante para tener algo desplegado esta semana

- [x] **Script de exportación de la base de datos reducida.**
      `src/export_demo_db.py` lee `data/stocker.db` (real, sin tocarla) y
      escribe `data/stocker_demo.db` con el mismo esquema (incluida la
      vista `news_sentiment_daily` — bug real encontrado y corregido en
      la primera versión del script, ver CONTEXTO.md) pero solo las
      filas de los 50 tickers de `NEWS_PRIORITY_TICKERS`. Ejecutado
      contra la base real: **61.0MB**, muy por debajo del límite de
      100MB de GitHub. **[yo, hecho]**
- [x] **Override de base de datos y modelos por variable de entorno.**
      `config.DB_PATH`/`config.MODELS_DIR` respetan `STOCKER_DB_PATH` /
      `STOCKER_MODELS_DIR` si están definidas; si no, se comportan
      exactamente igual que siempre. Los 3 `.joblib` elegidos
      (día/semana/mes, los más recientes) se copiaron a `models_demo/`
      sin reentrenar (el modelo no usa el ticker como feature).
      Verificado con `AppTest` apuntando a `stocker_demo.db` +
      `models_demo/`: las 5 páginas cargan, el selector de ticker
      muestra 50 opciones, los tres horizontes funcionan. **[yo, hecho]**
- [x] **`.gitignore` permite versionar `data/stocker_demo.db` y
      `models_demo/*.joblib`** sin afectar a la base de datos real ni a
      `/models/` (verificado con `git add -n`: los 4 ficheros se
      añadirían, `data/stocker.db` y `models/` se siguen ignorando).
      **[yo, hecho]**
- [x] **Aviso de "datos actualizados hasta" en el Dashboard** — usa la
      fecha máxima real de `daily_prices` (`get_latest_data_date`), sin
      necesitar detectar "modo demo" en ningún sitio: si esa fecha deja
      de avanzar, es la señal honesta de que es una foto fija. **[yo,
      hecho — este ítem estaba en la Fase 2, se adelantó]**
- [ ] **Decidir la plataforma de despliegue.** Se ha asumido Streamlit
      Community Cloud (share.streamlit.io) por ser gratis, nativo para
      Streamlit y el despliegue más rápido posible desde un repo de
      GitHub — dímelo si prefieres otra cosa (Render, Railway, un VPS
      propio...), cambia los pasos siguientes. **[tú — confírmalo]**
- [ ] **Confirmar público/privado del repo de GitHub** y que la
      plataforma elegida puede desplegar desde él tal cual está
      configurado. **[tú]**
- [ ] **Subir `stocker_demo.db` + `models_demo/` al repo** — el
      `.gitignore` ya los permite (hecho), solo falta:
      `git add data/stocker_demo.db models_demo/ && git commit -m "..." && git push`.
      No lo hago yo automáticamente: es una acción que publica contenido
      en tu GitHub, te toca confirmarla y ejecutarla tú. **[tú]**
- [ ] **Crear la cuenta/proyecto en Streamlit Community Cloud, conectar
      el repo y desplegar** (`dashboard/Inicio.py` como archivo de
      entrada, apuntando `STOCKER_DB_PATH` a `data/stocker_demo.db` en la
      configuración del despliegue). **[tú — necesita tu cuenta]**
- [ ] **Revisar visualmente en el navegador ya desplegado** que la barra
      lateral de filtros de "Dashboard" se ve bien —
      `.streamlit/config.toml` tiene `showSidebarNavigation = false`
      (pensado para ocultar el menú automático de páginas, no debería
      afectar a los widgets propios en `st.sidebar`, pero no se ha
      comprobado en un navegador real todavía, solo con `AppTest`).
      **[tú]**

## Fase 2 — Pulido recomendado antes de compartir el enlace

- [x] **Refrescar `README.md`** — ya menciona "Dashboard" como entrada,
      las 5 páginas del v1, los tres horizontes entrenados, y una
      sección nueva "Demo publicada" con las instrucciones de
      `STOCKER_DB_PATH`/`STOCKER_MODELS_DIR`. **[yo, hecho]**
- [x] **Aviso de "datos congelados" visible en el Dashboard** — caption
      con la fecha real más reciente de `daily_prices`
      (`get_latest_data_date`, nueva en `data_access.py`). **[yo, hecho
      — se adelantó desde la Fase 2 original]**
- [ ] **`docs/entregas/` en un repo público, ¿sí o no?** Son entregas
      del máster (PDFs/.md) — no es información sensible, pero es tu
      decisión si quieres que se vean en el repo público o prefieres
      excluirlas del repo desplegado. **[tú]**
- [ ] **LICENSE** — opcional pero recomendable en un repo público (p. ej.
      MIT). **[tú decides la licencia, yo la añado]**

## Fase 3 — Explícitamente fuera del v1 (backlog, no de esta semana)

- Pipeline en la nube con actualización diaria automática (implica
  resolver hosting de una base de datos ESCRIBIBLE + scheduler en la
  nube — mucho más trabajo que una foto fija; era la opción "producto
  vivo" descartada al decidir qué significa "publicar").
- Reincorporar "Resumen", "Noticias de la acción" y "Sentimiento del
  mercado" a la navegación — archivos intactos, listos para cuando se
  decida ampliar a una v2.
- Universo completo de 208 tickers en la versión pública (de momento
  son 50 en la demo; tu base de datos local sigue teniendo los 208).
- Limpieza de baja prioridad ya identificada antes de esta sesión: filas
  huérfanas en `stocks`, tablas legacy `gold_aapl_train`/
  `gold_aapl_inference`, `docs/entregas/03_modelo_datos_stocker.md`
  duplicado, entregas 1/2 sin versión `.md`.
- Sentimiento de noticias como feature real del modelo (investigado,
  sin señal encontrada — ver CONTEXTO.md).
- Mercado español (bloqueado por falta de calendario de mercado BME en
  las librerías usadas).
- Tests automatizados repetibles (por ahora la verificación es
  `AppTest` ejecutado a mano en cada cambio, no un suite en CI).

## Siguiente paso inmediato

Todo lo marcado **[yo]** de las Fases 1 y 2 ya está hecho y verificado
con `AppTest` (contra la base real y contra la demo reducida). Lo que
queda es todo lo marcado **[tú]**: confirmar la plataforma de despliegue,
público/privado del repo, subir `stocker_demo.db` + `models_demo/` con
git, crear la cuenta en el hosting elegido y desplegar. Avísame en cuanto
quieras que revise algo de eso contigo o si algún paso da un error
inesperado.
