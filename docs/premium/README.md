# docs/premium/

Nueva línea de contenido, separada del TFM en sí (`docs/entregas/`):
análisis en detalle por acción, pensado como el contenido de un futuro
apartado de pago de Stocker. Arranca el 2026-08-31 — ver CONTEXTO.md,
"Apartado de pago: análisis en detalle de hyperscalers" para el porqué y
las decisiones de alcance.

**Fase actual: solo contenido, sin cobro.** Desde el 2026-08-31 sí hay
una página en el dashboard (`dashboard/views/premium.py`, "Contenido
Premium" en la navegación superior) que lee y muestra estos archivos tal
cual, pero sigue sin sistema de usuarios ni pasarela de pago — cualquiera
que abra Stocker puede verla. Esa parte (login + cobro real) se diseña
más adelante. `dashboard/data_access.py` no se ha tocado para esto: la
página lee los `.md` directamente del disco, sin pasar por la base de
datos (salvo para nombre/logo de cada ticker, como el resto del
dashboard).

## Alcance de esta primera tanda

Hyperscalers: **AMZN, MSFT, GOOGL, META**. Un archivo por ticker
(`hyperscalers/<TICKER>.md`), a partir de la plantilla
`hyperscalers/_plantilla.md`.

Además, la página del dashboard reserva un apartado "Próximamente" para
**NVDA** y **SPX** (el índice S&P 500) — todavía sin material del
usuario ni archivo `.md`. Cuando llegue contenido para cualquiera de los
dos, va en `otros/<TICKER>.md` (carpeta nueva, sin plantilla propia
todavía — SPX en particular no es una empresa individual, así que
probablemente necesite una plantilla distinta a la de `hyperscalers/`;
se diseña cuando haya material real que analizar).

## Cómo se alimenta

El usuario pega transcripción o notas de un vídeo de YouTube por acción
(no el vídeo en sí, para no reproducir contenido con derechos de autor).
A partir de eso se redacta el análisis siguiendo la plantilla. Si el
vídeo aporta datos concretos (cifras de ingresos, guidance, etc.), se
contrastan contra fuentes públicas cuando es razonable hacerlo, y se deja
constancia de la fuente original (canal, título, fecha) en cada archivo.

## Aviso legal (en cada archivo, no solo aquí)

Contenido informativo/educativo, no asesoramiento financiero
personalizado — mismo principio que ya aplica al resto de Stocker (ver
CONTEXTO.md, "Honestidad de resultado"). Si esto se convierte en un
apartado de pago real, este aviso tiene que ir también en el producto
final, no solo en el borrador.
