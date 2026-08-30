"""
dashboard/assets/generate_wordmark.py — genera `stocker_wordmark.svg` a
partir del artifact de marca que trajo el usuario (2026-08-28, ver
CONTEXTO.md "Identidad de marca: logos del artifact de claude.ai").

Por qué existe este script en vez de guardar el wordmark tal cual venía
(`stocker-wordmark.html`): ese HTML depende de la fuente Space Grotesk
cargada desde Google Fonts. `st.logo()` de Streamlit no ejecuta HTML/CSS
— pinta una imagen (SVG o raster) — y un `@font-face` externo dentro de
un SVG usado como imagen no es fiable entre navegadores (no se puede
garantizar que Space Grotesk esté instalada o se cargue). La solución
robusta y estándar para logotipos es convertir el texto a CONTORNOS
vectoriales (paths), como haría cualquier editor de logos — así el
resultado se ve exactamente igual en cualquier navegador, sin depender
de ninguna fuente instalada ni de conexión a internet.

Como no había Space Grotesk disponible en este entorno (ni se puede
descargar — la política de esta sesión prohíbe hacer fetch de binarios
externos con curl/requests para saltarse las restricciones de las
herramientas de navegación), se usó Poppins Bold como sustituta: es una
geométrica sans-serif con proporciones similares (Google Fonts, ya
instalada localmente en `/usr/share/fonts/truetype/google-fonts/`). El
icono (el gráfico que sustituye a la "K") es el SVG original del
usuario, sin modificar, solo reescalado/posicionado.

Requiere `fonttools` (`pip install fonttools`). No hace falta ejecutar
esto salvo que se quiera regenerar el wordmark (p. ej. cambiando el
tamaño, el color, o la fuente sustituta).
"""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

FONT_PATH = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
FONT_SIZE = 96  # mismo tamaño de diseño que el mockup original (96px)
LETTER_SPACING = 0.005 * FONT_SIZE  # mismo valor que el CSS original
FILL = "#0f172a"

# Icono original del usuario (stocker-k-icon.svg), sin modificar — ocupa
# el hueco de la "K" en "STOCKER". Proporciones (0.80em × 0.72em) y
# márgenes (0.02em) también tal cual el diseño original.
ICON_SVG_BODY = """\
  <rect x="4" y="0" width="24" height="100" fill="#0f172a"/>
  <polyline points="28,50 40,38 48,44 60,24 68,30 82,12 94,8"
    fill="none" stroke="#16a34a" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="40" cy="38" r="4.5" fill="#16a34a"/>
  <circle cx="48" cy="44" r="4.5" fill="#16a34a"/>
  <circle cx="60" cy="24" r="4.5" fill="#16a34a"/>
  <circle cx="68" cy="30" r="4.5" fill="#16a34a"/>
  <circle cx="94" cy="8" r="7" fill="#16a34a" stroke="#ffffff" stroke-width="2.5"/>
  <polyline points="28,50 40,62 48,56 60,76 68,70 82,88 94,92"
    fill="none" stroke="#dc2626" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="40" cy="62" r="4.5" fill="#dc2626"/>
  <circle cx="48" cy="56" r="4.5" fill="#dc2626"/>
  <circle cx="60" cy="76" r="4.5" fill="#dc2626"/>
  <circle cx="68" cy="70" r="4.5" fill="#dc2626"/>
  <circle cx="94" cy="92" r="7" fill="#dc2626" stroke="#ffffff" stroke-width="2.5"/>
"""


def _word_paths(font: TTFont, glyph_set, cmap, scale: float, word: str, start_x: float):
    """Contornos SVG de cada letra de `word`, ya posicionados en X."""
    pieces = []
    cursor = start_x
    for ch in word:
        glyph = glyph_set[cmap[ord(ch)]]
        pen = SVGPathPen(glyph_set)
        glyph.draw(pen)
        transform = f"translate({cursor:.2f},0) scale({scale:.6f},{-scale:.6f})"
        pieces.append((pen.getCommands(), transform))
        cursor += glyph.width * scale + LETTER_SPACING
    return pieces, cursor - LETTER_SPACING - start_x


def _render_word(pieces, base_x: float, baseline_y: float) -> str:
    out = []
    for d, transform in pieces:
        inner = transform.split(") ", 1)
        out.append(
            f'<path d="{d}" transform="translate({base_x:.2f},{baseline_y:.2f}) '
            f'{inner[0]}) {inner[1]}" fill="{FILL}"/>'
        )
    return "\n".join(out)


def build_wordmark_svg() -> str:
    font = TTFont(FONT_PATH)
    upm = font["head"].unitsPerEm
    scale = FONT_SIZE / upm
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()

    stoc_pieces, stoc_w = _word_paths(font, glyph_set, cmap, scale, "STOC", 0)
    er_pieces, er_w = _word_paths(font, glyph_set, cmap, scale, "ER", 0)

    icon_w = 0.80 * FONT_SIZE
    icon_h = 0.72 * FONT_SIZE
    margin = 0.02 * FONT_SIZE

    pad_left = 3
    x_stoc = pad_left
    x_icon = x_stoc + stoc_w + margin
    x_er = x_icon + icon_w + margin
    total_w = x_er + er_w + pad_left

    baseline_y = 90
    icon_y = baseline_y - icon_h
    height = baseline_y + 16

    icon_scale_x = icon_w / 100
    icon_scale_y = icon_h / 100

    stoc_svg = _render_word(stoc_pieces, x_stoc, baseline_y)
    er_svg = _render_word(er_pieces, x_er, baseline_y)

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w:.0f} {height:.0f}" height="28">
{stoc_svg}
<g transform="translate({x_icon:.2f},{icon_y:.2f}) scale({icon_scale_x:.4f},{icon_scale_y:.4f})">
{ICON_SVG_BODY}</g>
{er_svg}
</svg>
'''


if __name__ == "__main__":
    out_path = Path(__file__).parent / "stocker_wordmark.svg"
    out_path.write_text(build_wordmark_svg())
    print(f"Escrito {out_path} ({out_path.stat().st_size} bytes)")
