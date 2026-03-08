#!/bin/sh
set -e

OPTIONS_FILE="/data/options.json"
QUERIES_FILE="/data/queries.json"

# ---------------------------------------------------------------------------
# Extrai configurações do options.json gerado pelo HAOS
# ---------------------------------------------------------------------------
NTFY_SERVER=$(python3 -c "
import json, sys
try:
    d = json.load(open('${OPTIONS_FILE}'))
    print(d.get('ntfy_server', 'https://ntfy.sh'))
except Exception:
    print('https://ntfy.sh')
" 2>/dev/null || echo "https://ntfy.sh")

# ---------------------------------------------------------------------------
# Grava queries.json no /data a partir das opções configuradas na UI
# Cada query tem seu próprio check_interval_minutes.
# O entrypoint usa o menor intervalo como tick do loop e controla
# individualmente quando cada query deve rodar.
# ---------------------------------------------------------------------------
python3 - <<'PYEOF'
import json, os, sys
from pathlib import Path

options_file = "/data/options.json"
queries_file = "/data/queries.json"

try:
    opts = json.loads(Path(options_file).read_text())
except Exception as e:
    print(f"[entrypoint] Erro ao ler {options_file}: {e}", file=sys.stderr)
    sys.exit(1)

queries = opts.get("queries", [])
if not queries:
    print("[entrypoint] Nenhuma query encontrada em options.json.", file=sys.stderr)
    sys.exit(1)

Path(queries_file).write_text(json.dumps(queries, indent=2, ensure_ascii=False))
print(f"[entrypoint] {len(queries)} queries gravadas em {queries_file}")
PYEOF

# ---------------------------------------------------------------------------
# Calcula o menor intervalo entre todas as queries ativas (tick do loop)
# ---------------------------------------------------------------------------
MIN_INTERVAL=$(python3 -c "
import json
from pathlib import Path
queries = json.loads(Path('/data/queries.json').read_text())
active = [q for q in queries if q.get('enabled', True)]
if not active:
    print(15)
else:
    print(min(q.get('check_interval_minutes', 15) for q in active))
" 2>/dev/null || echo "15")

echo "========================================"
echo "  OLX Notifier v2"
echo "========================================"
echo "  Servidor ntfy : ${NTFY_SERVER}"
echo "  Tick do loop  : ${MIN_INTERVAL} minutos"
echo "  Queries       : $(python3 -c "
import json
from pathlib import Path
qs = json.loads(Path('/data/queries.json').read_text())
active = [q for q in qs if q.get('enabled', True)]
names = ', '.join(q['search_query'] for q in active)
print(f'{len(active)} ativas: {names}')
" 2>/dev/null || echo "?")"
echo "========================================"

# ---------------------------------------------------------------------------
# Função que decide quais queries devem rodar agora com base nos timestamps
# ---------------------------------------------------------------------------
run_due_queries() {
    python3 - <<'PYEOF'
import json, os, time
from pathlib import Path

queries_file = "/data/queries.json"
timestamps_file = "/data/last_run.json"
ntfy_server = os.getenv("NTFY_SERVER", "https://ntfy.sh")

queries = json.loads(Path(queries_file).read_text())
active = [q for q in queries if q.get("enabled", True)]

# Carrega timestamps da última execução de cada query
try:
    timestamps = json.loads(Path(timestamps_file).read_text())
except Exception:
    timestamps = {}

now = time.time()
ran_any = False

for query in active:
    name = query["search_query"]
    interval_min = query.get("check_interval_minutes", 15)
    interval_sec = interval_min * 60
    last = timestamps.get(name, 0)

    if now - last >= interval_sec:
        print(f"[scheduler] Rodando query '{name}' (intervalo: {interval_min}min)")
        timestamps[name] = now
        ran_any = True

        # Executa o scraper apenas para essa query (passa via stdin)
        import subprocess, sys, json as _json
        single_query = json.dumps([query])
        env = os.environ.copy()
        env["NTFY_SERVER"] = ntfy_server
        env["DATA_DIR"] = "/data"
        env["QUERIES_JSON"] = single_query
        result = subprocess.run(
            ["python3", "/app/scraper.py"],
            env=env,
        )
        if result.returncode != 0:
            print(f"[scheduler] Query '{name}' terminou com erro (código {result.returncode})",
                  file=sys.stderr)

        # Salva o timestamp atualizado após cada execução
        Path(timestamps_file).write_text(_json.dumps(timestamps, indent=2))
    else:
        remaining = int((interval_sec - (now - last)) / 60)
        print(f"[scheduler] Query '{name}': próxima em ~{remaining} min")

if not ran_any:
    print("[scheduler] Nenhuma query a executar neste tick.")
PYEOF
}

# Executa imediatamente na inicialização (zera timestamps para forçar execução)
echo "Executando verificação inicial..."
python3 -c "
from pathlib import Path
import json
Path('/data/last_run.json').write_text('{}')
" 2>/dev/null || true

NTFY_SERVER="${NTFY_SERVER}" run_due_queries

# Loop principal com tick = menor intervalo configurado
echo "Verificação inicial concluída. Próximo tick em ${MIN_INTERVAL} minutos."
while true; do
    sleep "${MIN_INTERVAL}m"
    echo "--- Tick do scheduler ---"
    NTFY_SERVER="${NTFY_SERVER}" run_due_queries
done
