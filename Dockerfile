FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# System deps: font tooling only (no TeX Live, no Quarto)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    unzip \
    xz-utils \
    ca-certificates \
    fontconfig \
    fonts-texgyre \
    && rm -rf /var/lib/apt/lists/*

# Install Inter font (body text)
RUN wget -q "https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip" \
    && unzip -q Inter-4.0.zip -d /tmp/inter \
    && find /tmp/inter -name "*.ttf" -exec cp {} /usr/local/share/fonts/ \; \
    && fc-cache -f \
    && rm -rf Inter-4.0.zip /tmp/inter

# Install Typst CLI (single static binary, ~30 MB)
ARG TYPST_VERSION=0.13.1
RUN wget -q "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
    && tar -xf "typst-x86_64-unknown-linux-musl.tar.xz" \
    && mv "typst-x86_64-unknown-linux-musl/typst" /usr/local/bin/typst \
    && chmod +x /usr/local/bin/typst \
    && rm -rf "typst-x86_64-unknown-linux-musl.tar.xz" "typst-x86_64-unknown-linux-musl"

# Python app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

WORKDIR /app/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
