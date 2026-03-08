# syntax=docker/dockerfile:1
# Imagem base do Home Assistant OS — suporta aarch64 (Raspberry Pi) e amd64
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM $BUILD_FROM

LABEL maintainer="olx-notifier"
LABEL description="Monitora anúncios na OLX e notifica via ntfy.sh"

# Instala Python, pip e dependências de sistema
RUN apk add --no-cache \
    python3 \
    py3-pip \
    libcurl \
    curl-dev \
    gcc \
    musl-dev \
    && rm -rf /var/cache/apk/*

WORKDIR /app

# Copia e instala dependências Python
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copia o scraper, o entrypoint e o script auxiliar de limpeza
COPY scraper.py .
COPY entrypoint.sh /entrypoint.sh
COPY clear_seen.sh /clear_seen.sh
RUN chmod +x /entrypoint.sh /clear_seen.sh

ENTRYPOINT ["/entrypoint.sh"]
