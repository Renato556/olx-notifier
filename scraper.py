#!/usr/bin/env python3
"""
OLX Notifier
Busca anúncios na OLX conforme queries configuradas e notifica via ntfy.sh.

Cada query pode ser configurada com:
  - search_query    : termo de busca (ex: "macbook")
  - enabled         : se a busca está ativa
  - scope           : "bh_only" ou "bh_and_brazil"
  - ntfy_topic_bh   : tópico ntfy para anúncios de BH
  - ntfy_topic_br   : tópico ntfy para anúncios nacionais (apenas quando scope=bh_and_brazil)
  - check_interval_minutes: intervalo de verificação para essa query
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configurações globais
# ---------------------------------------------------------------------------

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
NTFY_MAX_BYTES = 4096  # limite do ntfy antes de virar attachment

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelo de query
# ---------------------------------------------------------------------------

def build_urls(query: dict) -> "tuple[str, str | None]":
    """
    Constrói as URLs de busca para uma query.
    Retorna (url_bh, url_br). url_br é None quando scope == "bh_only".
    """
    q = quote_plus(query["search_query"])
    ps = query.get("price_min", "")
    pe = query.get("price_max", "")

    price_params = ""
    if ps:
        price_params += f"&ps={ps}"
    if pe:
        price_params += f"&pe={pe}"

    url_bh = (
        f"https://www.olx.com.br/informatica/notebooks/estado-mg/belo-horizonte-e-regiao"
        f"?q={q}{price_params}&o=1"
    )

    url_br = None
    if query.get("scope", "bh_only") == "bh_and_brazil":
        url_br = (
            f"https://www.olx.com.br/brasil"
            f"?q={q}{price_params}&delivery=1&o=1"
        )

    return url_bh, url_br


# ---------------------------------------------------------------------------
# Persistência (por query, usando slug do search_query como chave)
# ---------------------------------------------------------------------------

def _seen_file(search_query: str) -> Path:
    slug = re.sub(r"[^\w]+", "_", search_query.lower()).strip("_")
    return DATA_DIR / f"seen_{slug}.json"


def load_seen(search_query: str) -> set:
    """Carrega IDs de anúncios já vistos para uma query."""
    f = _seen_file(search_query)
    if f.exists():
        try:
            data = json.loads(f.read_text())
            return set(data)
        except (json.JSONDecodeError, OSError):
            log.warning("Arquivo %s corrompido, reiniciando.", f.name)
    return set()


def save_seen(search_query: str, seen: set) -> None:
    """Salva IDs de anúncios já vistos para uma query."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _seen_file(search_query).write_text(json.dumps(sorted(seen), indent=2))


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def make_scraper() -> cffi_requests.Session:
    """Cria sessão com impersonação de Chrome real (TLS fingerprint genuíno)."""
    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.olx.com.br/",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return session


def parse_price(text: str) -> "int | None":
    """Extrai valor numérico de uma string de preço brasileira."""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def fetch_ads(scraper: cffi_requests.Session, url: str) -> "list[dict]":
    """
    Busca anúncios em uma URL da OLX.
    Retorna lista de dicts com: id, title, price, url, location, delivery.
    """
    ads = []
    try:
        resp = scraper.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        log.error("Erro ao buscar %s: %s", url, exc)
        return ads

    soup = BeautifulSoup(resp.text, "html.parser")

    # A OLX injeta os dados dos anúncios em um bloco __NEXT_DATA__ (JSON)
    next_data_tag = soup.find("script", id="__NEXT_DATA__")
    if next_data_tag:
        ads = _parse_next_data(next_data_tag.string or "")
        if ads:
            log.info("Extraídos %d anúncios via __NEXT_DATA__ de %s", len(ads), url)
            return ads

    # Fallback: parsing HTML direto dos cards
    ads = _parse_html_cards(soup, url)
    log.info("Extraídos %d anúncios via HTML de %s", len(ads), url)
    return ads


def _parse_next_data(raw_json: str) -> list[dict]:
    """Extrai anúncios do bloco __NEXT_DATA__ do Next.js."""
    ads = []
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return ads

    # Caminho típico na OLX Brasil
    try:
        listings = data["props"]["pageProps"]["ads"]
    except (KeyError, TypeError):
        try:
            listings = data["props"]["pageProps"]["listing"]["ads"]
        except (KeyError, TypeError):
            listings = []

    for item in listings:
        try:
            ad_id = str(item.get("listId") or item.get("id") or "")
            title = item.get("subject") or item.get("title") or ""
            price_raw = item.get("priceValue") or item.get("price") or ""
            price_val = parse_price(str(price_raw))
            ad_url = str(item.get("friendlyUrl") or item.get("url") or item.get("link") or "")
            location = item.get("location") or item.get("municipality") or ""
            delivery = bool(item.get("olxDelivery") or item.get("olxDeliveryBadgeEnabled")
                            or item.get("delivery") or item.get("hasDelivery"))

            if ad_id and title:
                ads.append(
                    {
                        "id": ad_id,
                        "title": title,
                        "price": price_val,
                        "url": ad_url,
                        "location": location,
                        "delivery": delivery,
                    }
                )
        except Exception:
            continue
    return ads


def _parse_html_cards(soup: BeautifulSoup, source_url: str) -> list[dict]:
    """Fallback: extrai anúncios diretamente dos cards HTML."""
    ads = []

    card_selectors = [
        "li[data-lurker-detail='list_id']",
        "li.sc-1fcmfeb-2",
        "[data-testid='listing-card']",
        "section[data-ds-component='DS-AdCard']",
    ]

    cards = []
    for sel in card_selectors:
        cards = soup.select(sel)
        if cards:
            break

    if not cards:
        cards = [
            li for li in soup.find_all("li")
            if li.find("a", href=re.compile(r"/d/[^/]+/[^/]+-\d+"))
        ]

    for card in cards:
        try:
            link_tag = card.find("a", href=True)
            if not link_tag:
                continue

            ad_url = str(link_tag["href"])
            if not ad_url.startswith("http"):
                ad_url = "https://www.olx.com.br" + ad_url

            id_match = re.search(r"-(\d{7,})(?:\.html)?$", ad_url)
            ad_id = id_match.group(1) if id_match else ad_url

            title_tag = card.find(["h2", "h3", "span"], class_=re.compile(r"title|subject", re.I))
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)

            price_tag = card.find(string=re.compile(r"R\$\s*[\d\.,]+"))
            price_val = parse_price(price_tag) if price_tag else None

            location_tag = card.find(
                ["span", "p"], class_=re.compile(r"locat|city|munic", re.I)
            )
            location = location_tag.get_text(strip=True) if location_tag else ""

            delivery = bool(card.find(string=re.compile(r"entrega|delivery", re.I)))

            if ad_id and title:
                ads.append(
                    {
                        "id": ad_id,
                        "title": title,
                        "price": price_val,
                        "url": ad_url,
                        "location": location,
                        "delivery": delivery,
                    }
                )
        except Exception:
            continue

    return ads


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def is_bh_region(location: str) -> bool:
    """Verifica se o anúncio é de Belo Horizonte e região."""
    if not location:
        return False
    loc_lower = location.lower()
    bh_keywords = [
        "belo horizonte", "betim", "contagem", "nova lima", "santa luzia",
        "ribeirão das neves", "ibirité", "vespasiano", "sabará", "lagoa santa",
        "pedro leopoldo", "brumadinho", "esmeraldas", "caeté", "itaguara",
        "mg", "minas gerais",
    ]
    return any(kw in loc_lower for kw in bh_keywords)


def passes_filter(ad: dict, query: dict) -> bool:
    """
    Retorna True se o anúncio passou em todos os filtros da query.
    Os filtros de bloqueio de título são opcionais e configurados pela query.
    """
    title = ad.get("title", "")
    price = ad.get("price")

    # Filtros de título configurados na query
    blocked_keywords = query.get("blocked_keywords", [])
    if blocked_keywords:
        t = title.lower()
        for kw in blocked_keywords:
            if re.search(kw.lower(), t):
                return False

    # Filtro de preço
    price_min = query.get("price_min")
    price_max = query.get("price_max")
    if price is not None:
        if price_min is not None and price < price_min:
            return False
        if price_max is not None and price > price_max:
            return False

    # Localização OU entrega nacional
    scope = query.get("scope", "bh_only")
    if scope == "bh_only":
        return is_bh_region(ad.get("location", ""))

    # bh_and_brazil: qualquer anúncio de BH ou com entrega passa
    if is_bh_region(ad.get("location", "")):
        return True
    if ad.get("delivery"):
        return True

    return False


# ---------------------------------------------------------------------------
# Notificação
# ---------------------------------------------------------------------------

def send_notification(new_ads: list[dict], topic: str, search_query: str) -> None:
    """Envia notificação para o ntfy.sh com os novos anúncios."""
    if not new_ads:
        return

    import urllib.request

    count = len(new_ads)
    title = f"OLX {search_query}: {count} novo{'s' if count > 1 else ''} anuncio{'s' if count > 1 else ''}"

    blocks = []
    for ad in new_ads:
        price_str = f"R$ {ad['price']:,.0f}".replace(",", ".") if ad["price"] else "Preço não informado"
        delivery_str = " 📦 Entrega" if ad["delivery"] else ""
        loc = ad.get("location") or "—"
        ad_url = ad.get("url") or ""
        blocks.append(
            f"**{ad['title'][:60]}**\n"
            f"{price_str} · {loc}{delivery_str}\n"
            f"[Ver anúncio]({ad_url})"
        )

    separator = "\n\n---\n\n"
    body = separator.join(blocks)

    body_bytes = body.encode("utf-8")
    if len(body_bytes) > NTFY_MAX_BYTES:
        truncated = body_bytes[: NTFY_MAX_BYTES - 100]
        body = truncated.decode("utf-8", errors="ignore") + "\n\n…(truncado)"

    req = urllib.request.Request(
        f"{NTFY_SERVER}/{topic}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            "Priority": "default",
            "Tags": "shopping_cart",
            "Content-Type": "text/plain; charset=utf-8",
            "Markdown": "yes",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log.info("Notificação enviada para %s: HTTP %s", topic, resp.status)
    except Exception as exc:
        log.error("Falha ao enviar notificação para %s: %s", topic, exc)


# ---------------------------------------------------------------------------
# Execução de uma query individual
# ---------------------------------------------------------------------------

def run_query(scraper: cffi_requests.Session, query: dict) -> None:
    """Executa uma query: busca, filtra e notifica."""
    search_query = query["search_query"]
    scope = query.get("scope", "bh_only")
    topic_bh = query.get("ntfy_topic_bh", "")
    topic_br = query.get("ntfy_topic_br", "")

    log.info("--- Iniciando query: '%s' (scope: %s) ---", search_query, scope)

    seen = load_seen(search_query)
    url_bh, url_br = build_urls(query)

    # Busca BH
    time.sleep(1)
    bh_ads = fetch_ads(scraper, url_bh)
    for ad in bh_ads:
        ad["_source"] = "bh"

    # Busca Brasil (apenas se scope == bh_and_brazil)
    br_ads = []
    if url_br:
        time.sleep(3)
        br_ads = fetch_ads(scraper, url_br)
        for ad in br_ads:
            ad["_source"] = "br"

    all_ads = bh_ads + br_ads
    log.info("Total coletados (antes de deduplicar): %d", len(all_ads))

    # Deduplica por ID — BH tem prioridade
    unique: dict[str, dict] = {}
    for ad in all_ads:
        if ad["id"] not in unique:
            unique[ad["id"]] = ad

    log.info("Total após deduplicação: %d", len(unique))

    # Filtra e separa novos por tópico
    new_bh: list[dict] = []
    new_br: list[dict] = []
    for ad_id, ad in unique.items():
        if ad_id in seen:
            continue
        if passes_filter(ad, query):
            if ad["_source"] == "bh":
                new_bh.append(ad)
            else:
                new_br.append(ad)

    total_new = len(new_bh) + len(new_br)
    log.info("Novos que passaram nos filtros: %d (BH: %d, Brasil: %d)",
             total_new, len(new_bh), len(new_br))

    # Notifica
    if new_bh and topic_bh:
        send_notification(new_bh, topic_bh, search_query)
    if new_br and topic_br:
        send_notification(new_br, topic_br, search_query)

    if not total_new:
        log.info("Nenhum novo anúncio para '%s'.", search_query)

    # Persiste todos os IDs vistos (inclusive filtrados)
    for ad_id in unique:
        seen.add(ad_id)
    save_seen(search_query, seen)

    log.info("--- Query '%s' concluída. ---", search_query)


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def run() -> None:
    """
    Carrega queries do arquivo de configuração passado como argumento
    (ou de QUERIES_JSON env var) e executa apenas as habilitadas.
    """
    # Aceita caminho do arquivo de queries como argumento ou variável de ambiente
    queries_path = sys.argv[1] if len(sys.argv) > 1 else os.getenv("QUERIES_FILE", "")
    queries_json = os.getenv("QUERIES_JSON", "")

    queries: list[dict] = []

    if queries_path and Path(queries_path).exists():
        try:
            queries = json.loads(Path(queries_path).read_text())
            log.info("Carregadas %d queries de %s", len(queries), queries_path)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Erro ao ler arquivo de queries %s: %s", queries_path, exc)
            sys.exit(1)
    elif queries_json:
        try:
            queries = json.loads(queries_json)
            log.info("Carregadas %d queries da variável QUERIES_JSON", len(queries))
        except json.JSONDecodeError as exc:
            log.error("Erro ao decodificar QUERIES_JSON: %s", exc)
            sys.exit(1)
    else:
        log.error("Nenhuma query configurada. Passe o caminho do arquivo como argumento "
                  "ou defina QUERIES_JSON.")
        sys.exit(1)

    # Filtra apenas queries habilitadas
    active = [q for q in queries if q.get("enabled", True)]
    log.info("Queries ativas: %d / %d", len(active), len(queries))

    if not active:
        log.info("Nenhuma query ativa. Encerrando.")
        return

    scraper = make_scraper()

    for i, query in enumerate(active):
        if i > 0:
            # Pequeno intervalo entre queries para não sobrecarregar a OLX
            time.sleep(5)
        try:
            run_query(scraper, query)
        except Exception as exc:
            log.error("Erro inesperado na query '%s': %s", query.get("search_query", "?"), exc)


if __name__ == "__main__":
    run()
