# notebooks/exploratory/

Carpeta para notebooks exploratorios — **no de producción**. El código real
del pipeline vive en `src/`.

## Pendiente: notebook prototipo

El encargo original menciona un notebook prototipo a mover aquí, pero no se
encontró ningún `.ipynb` entre los ficheros disponibles en esta sesión (solo
llegaron los `.md`/`.pdf` de contexto y de entregas). Cuando el notebook esté
disponible, debe colocarse en esta carpeta con una celda o nota inicial que
dejen explícito:

1. Que es un notebook **exploratorio**, no productivo, y que no representa
   el pipeline final.
2. Los problemas ya detectados en él (según `CONTEXTO.md`), a corregir al
   implementar el pipeline real en `src/`:
   - **Data leakage**: `X` incluía la propia columna del target (`stonks?`)
     como feature.
   - **Agregación OHLC incorrecta**: al agregar datos intradía a diario se
     usó `.mean()` para Open/High/Low/Close, en vez de `Open`=primer valor,
     `High`=máximo, `Low`=mínimo, `Close`=último valor, `Volume`=suma.
   - **Split train/test invertido y aleatorio**: se usó `train_size=0.3`
     (30% train / 70% test), y además mediante un split aleatorio en vez de
     uno cronológico.

Este README puede sustituirse por esa celda/nota en cuanto el notebook se
añada al repo.
