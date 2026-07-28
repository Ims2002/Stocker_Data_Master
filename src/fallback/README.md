# src/fallback/

Módulos de reserva, **no activos en el pipeline**. El pipeline real vive
directamente en `src/` (`download.py` usa `yfinance`, según el diseño
cerrado en `CONTEXTO.md`).

## `yahooquery_download.py`

Descarga alternativa de histórico diario OHLCV vía `yahooquery`, por si
`yfinance` deja de funcionar de forma fiable (riesgo ya señalado en
CONTEXTO.md: no es una API oficial). No se instala por defecto — no está
en `requirements.txt` — ni se importa desde ningún módulo del pipeline
activo.

Es una versión recortada de un script de referencia más amplio que también
cubría market movers, sector/industry, streaming en vivo, búsqueda y
screener de acciones — todo eso queda fuera del alcance del MVP y se
eliminó al integrarlo aquí. Solo se conserva la parte relevante: histórico
diario OHLCV de uno o varios tickers, volcado a `data/raw/` con el mismo
formato que `src/download.py`.

Activarlo manualmente si hace falta:

```bash
pip install yahooquery
python src/fallback/yahooquery_download.py ticker AAPL --period 10y
```
