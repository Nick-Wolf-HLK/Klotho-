"""Generiert klotho/art.py (LOGO + FACE als String-Konstanten).

Das FACE-Porträt stammt aus einer Public-Domain-Quelle (Wikimedia Commons,
'KPM-Bildplatte Die Parze Clotho', Maler J. Mai), auf Kopf/Haar zugeschnitten
und zu ASCII konvertiert. Quelle wird in art.py als Kommentar dokumentiert.
"""
from PIL import Image, ImageOps

SRC = "tools/clotho_source_pd.jpg"
CROP = (160, 90, 520, 470)
RAMP = " .:-=+*#%@"
BG_CUTOFF = 62


def render(width):
    base = Image.open(SRC).convert("L")
    img = ImageOps.autocontrast(base.crop(CROP), cutoff=2)
    aspect = img.height / img.width
    h = max(1, int(width * aspect * 0.5))
    img = img.resize((width, h))
    px = list(img.getdata())
    n = len(RAMP)
    grid = []
    for r in range(h):
        row = []
        for c in range(width):
            p = 255 - px[r * width + c]
            row.append(" " if p < BG_CUTOFF else RAMP[min(n - 1, p * n // 256)])
        grid.append(row)

    # Connected-Component-Filter: kleine isolierte Cluster (Rand-Rauschen)
    # entfernen, nur das große zusammenhängende Gesicht behalten.
    from collections import deque
    MIN_CLUSTER = 10
    seen = [[False] * width for _ in range(h)]

    def component(sr, sc):
        q = deque([(sr, sc)])
        seen[sr][sc] = True
        cells = [(sr, sc)]
        while q:
            y, x = q.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if (0 <= ny < h and 0 <= nx < width
                            and not seen[ny][nx] and grid[ny][nx] != " "):
                        seen[ny][nx] = True
                        q.append((ny, nx))
                        cells.append((ny, nx))
        return cells

    for r in range(h):
        for c in range(width):
            if grid[r][c] != " " and not seen[r][c]:
                cells = component(r, c)
                if len(cells) < MIN_CLUSTER:
                    for y, x in cells:
                        grid[y][x] = " "

    rows = ["".join(grid[r]).rstrip() for r in range(h)]

    while rows and not rows[0].strip():
        rows.pop(0)
    while rows and not rows[-1].strip():
        rows.pop()
    indents = [len(l) - len(l.lstrip()) for l in rows if l.strip()]
    cut = min(indents) if indents else 0
    return [l[cut:] for l in rows]


LOGO = [
    "██╗  ██╗██╗      ██████╗ ████████╗██╗  ██╗ ██████╗",
    "██║ ██╔╝██║     ██╔═══██╗╚══██╔══╝██║  ██║██╔═══██╗",
    "█████╔╝ ██║     ██║   ██║   ██║   ███████║██║   ██║",
    "██╔═██╗ ██║     ██║   ██║   ██║   ██╔══██║██║   ██║",
    "██║  ██╗███████╗╚██████╔╝   ██║   ██║  ██║╚██████╔╝",
    "╚═╝  ╚═╝╚══════╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝ ╚═════╝",
]

face = render(76)

header = '''"""ASCII-Art-Assets für Klotho (Logo + Göttinnen-Porträt).

Dieses Modul wird von tools generiert (.klotho_assets/gen_art.py) und enthält nur
statische Strings — keine Laufzeit-Abhängigkeit zu Pillow.

FACE-Quelle: Wikimedia Commons, "KPM-Bildplatte Die Parze Clotho" (Maler J. Mai),
Public Domain. Auf Kopf/Haar zugeschnitten und zu ASCII konvertiert.
"""
'''

with open("klotho/art.py", "w") as f:
    f.write(header)
    f.write("\nLOGO = r\"\"\"\n")
    f.write("\n".join(LOGO))
    f.write("\n\"\"\"\n")
    f.write("\nTAGLINE = \"Multi-LLM Orchestrator · viele Fäden, ein Plan\"\n")
    f.write("\nFACE = \"\"\"\n")
    f.write("\n".join(face))
    f.write("\n\"\"\"\n")

print("klotho/art.py geschrieben:", len(face), "Gesicht-Zeilen,", len(LOGO), "Logo-Zeilen")
