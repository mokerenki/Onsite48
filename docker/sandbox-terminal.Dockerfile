# Sandbox terminal image for Onsite48 agent workspace (Phase 2)
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    zip \
    unzip \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    poppler-utils \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    python-docx==1.1.2 \
    python-pptx==1.0.2 \
    openpyxl==3.1.5 \
    matplotlib==3.9.2 \
    pandas==2.2.3 \
    reportlab==4.2.5 \
    tabulate==0.9.0 \
    pypdf==5.1.0

WORKDIR /workspace
RUN useradd -m -u 10001 agent && chown -R agent:agent /workspace
USER agent
CMD ["sleep", "infinity"]
