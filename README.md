# OLX Notifier — Home Assistant Add-on

Monitora múltiplos termos de busca na OLX e envia notificações via [ntfy.sh](https://ntfy.sh) quando novos anúncios aparecem.

## Estrutura do repositório

```
olx-notifier/              # repositório raiz
├── repository.yaml        # identifica o repo como fonte de add-ons para o HAOS
├── README.md
└── olx-notifier/          # pasta do add-on
    ├── config.yaml        # manifesto: nome, schema das opções, arquiteturas
    ├── Dockerfile         # imagem Alpine com Python + curl_cffi
    ├── entrypoint.sh      # scheduler: lê options.json, dispara queries no intervalo certo
    ├── requirements.txt   # dependências Python
    └── scraper.py         # scraping da OLX, filtros e envio de notificações
```

## Instalação

1. No Home Assistant, vá em **Settings → Add-ons → Add-on Store**
2. Clique em ⋮ (canto superior direito) → **Repositories**
3. Cole a URL deste repositório e clique em **Add**
4. O add-on **OLX Notifier** aparecerá na loja — clique em **Install**
5. Configure suas queries na aba **Configuration**
6. Clique em **Start**

## Configuração

Na aba Configuration do add-on você define a lista de `queries`. Cada query aceita:

| Campo | Tipo | Descrição |
|---|---|---|
| `search_query` | `str` | Termo de busca (ex: `macbook`, `ipad pro`) |
| `enabled` | `bool` | Ativa/desativa a busca sem removê-la |
| `scope` | `bh_only` \| `bh_and_brazil` | Busca só em BH ou BH + Brasil inteiro com entrega |
| `ntfy_topic_bh` | `str` | Tópico ntfy para anúncios de BH e região |
| `ntfy_topic_br` | `str` | Tópico ntfy para anúncios nacionais (só quando `bh_and_brazil`) |
| `check_interval_minutes` | `int` | Intervalo de verificação em minutos |
| `price_min` | `int` (opcional) | Preço mínimo em R$ |
| `price_max` | `int` (opcional) | Preço máximo em R$ |
| `blocked_keywords` | `list[str]` (opcional) | Regex aplicados ao título para bloquear anúncios |

---

## Contribuindo

### Pré-requisitos

- Python 3.10+
- `pip install curl-cffi beautifulsoup4 lxml`

### Rodando localmente

O scraper lê as queries da variável de ambiente `QUERIES_JSON`:

```bash
export QUERIES_JSON='[{
  "search_query": "macbook",
  "enabled": true,
  "scope": "bh_and_brazil",
  "ntfy_topic_bh": "olx-mac-bh",
  "ntfy_topic_br": "olx-mac-brasil",
  "check_interval_minutes": 15,
  "price_min": 2000,
  "price_max": 6000,
  "blocked_keywords": ["defeito"]
}]'

DATA_DIR=/tmp/olx-test python3 olx-notifier/scraper.py
```

Os arquivos de estado ficam em `DATA_DIR` (`seen_<query>.json` e `last_run.json`). Delete-os para forçar reprocessamento de todos os anúncios.

### Estrutura do código (`scraper.py`)

| Função | Responsabilidade |
|---|---|
| `build_urls(query)` | Monta as URLs de busca da OLX a partir dos parâmetros da query |
| `fetch_ads(scraper, url)` | Faz o request e tenta `__NEXT_DATA__` JSON; cai no parser HTML como fallback |
| `_parse_next_data(raw)` | Extrai anúncios do blob JSON injetado pelo Next.js |
| `_parse_html_cards(soup, url)` | Fallback: extrai anúncios via seletores CSS dos cards HTML |
| `passes_filter(ad, query)` | Aplica filtros de título (regex), preço e localização/entrega |
| `send_notification(ads, topic, query)` | Formata e envia POST para o ntfy.sh |
| `run_query(scraper, query)` | Orquestra uma query: busca → filtra → notifica → persiste IDs vistos |
| `run()` | Ponto de entrada: carrega queries, filtra as ativas, executa cada uma |

### Fluxo de execução

```
entrypoint.sh
  └── a cada tick (menor intervalo entre queries ativas)
        └── run_due_queries()  ← verifica last_run.json
              └── para cada query cujo intervalo expirou:
                    QUERIES_JSON="[<query>]" python3 scraper.py
                      ├── fetch_ads(url_bh)
                      ├── fetch_ads(url_br)   ← só se scope=bh_and_brazil
                      ├── deduplica por ID
                      ├── passes_filter() para cada anúncio novo
                      ├── send_notification() → ntfy.sh
                      └── save_seen()  → seen_<query>.json
```

### Adicionando suporte a uma nova região

A função `is_bh_region()` em `olx-notifier/scraper.py` lista as cidades reconhecidas como BH e região. Para adicionar outra região basta estender essa lista ou, futuramente, torná-la configurável por query.

### Bypass de bot detection

A OLX usa detecção de bots baseada em TLS fingerprint. O scraper usa `curl_cffi` com `impersonate="chrome120"` para apresentar um handshake TLS idêntico ao Chrome real. Se a OLX passar a bloquear esse perfil, basta atualizar a string de impersonação para um Chrome mais recente (ex: `chrome124`) — veja as versões disponíveis na [documentação do curl_cffi](https://curl-cffi.readthedocs.io).

### Atualizando o add-on no HAOS

Faça push das alterações para o repositório e clique em **Update** (ou **Rebuild**) pelo painel do add-on no HAOS.
