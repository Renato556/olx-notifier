#!/bin/sh
# ---------------------------------------------------------------------------
# clear_seen.sh — Remove registros de anúncios já vistos para uma ou todas as queries
#
# Uso (dentro do container):
#   ./clear_seen.sh                     # lista os arquivos existentes
#   ./clear_seen.sh all                 # remove todos os seen_*.json
#   ./clear_seen.sh macbook             # remove seen_macbook.json
#   ./clear_seen.sh "macbook pro"       # remove seen_macbook_pro.json
#
# Uso via API do Supervisor Home Assistant (de fora do container):
#   curl -X POST http://supervisor/addons/olx_notifier/stdin \
#        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
#        -H "Content-Type: application/json" \
#        -d '{"command":"clear","query":"all"}'
#
#   curl -X POST http://supervisor/addons/olx_notifier/stdin \
#        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
#        -H "Content-Type: application/json" \
#        -d '{"command":"list"}'
# ---------------------------------------------------------------------------

DATA_DIR="${DATA_DIR:-/data}"

list_files() {
    python3 - <<PYEOF
import json
from pathlib import Path

data_dir = Path("${DATA_DIR}")
files = sorted(data_dir.glob("seen_*.json"))
if not files:
    print("Nenhum arquivo de registros vistos encontrado.")
else:
    print("Arquivos de registros vistos:")
    for f in files:
        try:
            ids = json.loads(f.read_text())
            count = len(ids)
        except Exception:
            count = "?"
        slug = f.stem.removeprefix("seen_")
        print(f"  [{slug}] {count} registros  →  {f.name}")
PYEOF
}

clear_all() {
    python3 - <<PYEOF
from pathlib import Path
data_dir = Path("${DATA_DIR}")
files = sorted(data_dir.glob("seen_*.json"))
if not files:
    print("Nenhum arquivo encontrado.")
else:
    for f in files:
        f.unlink()
        print(f"Removido: {f.name}")
    print(f"Total: {len(files)} arquivo(s) removido(s).")
PYEOF
}

clear_query() {
    QUERY="$1"
    python3 - <<PYEOF
import re
from pathlib import Path

data_dir = Path("${DATA_DIR}")
query = "${QUERY}"
slug = re.sub(r"[^\w]+", "_", query.lower()).strip("_")
f = data_dir / f"seen_{slug}.json"
if f.exists():
    f.unlink()
    print(f"Removido: {f.name}")
else:
    print(f"Arquivo não encontrado: {f.name}")
    # Mostra arquivos disponíveis
    available = sorted(data_dir.glob("seen_*.json"))
    if available:
        print("Arquivos disponíveis:")
        for a in available:
            print(f"  - {a.stem.removeprefix('seen_')}")
PYEOF
}

case "$1" in
    "")
        list_files
        echo ""
        echo "Uso: $0 [all | <query>]"
        echo "  all     — remove todos os arquivos seen_*.json"
        echo "  <query> — remove o seen_<slug>.json da query especificada"
        ;;
    all)
        echo "Removendo todos os registros vistos..."
        clear_all
        ;;
    *)
        echo "Removendo registros para query: $1"
        clear_query "$1"
        ;;
esac
