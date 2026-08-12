FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

RUN groupadd --system --gid 10002 sandbox-jobs \
    && useradd --system --uid 10001 --gid sandbox-jobs --no-create-home --shell /usr/sbin/nologin drainage

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/matplotlib \
    && printf '%s\n' \
        'font.family: sans-serif' \
        'font.sans-serif: Noto Sans CJK SC, Noto Sans CJK JP, DejaVu Sans' \
        'axes.unicode_minus: False' \
        > /tmp/matplotlib/matplotlibrc

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=root:root . .

RUN mkdir -p var/outputs var/workspace var/logs /var/lib/sandbox-jobs \
    && chown -R 10001:10002 var /var/lib/sandbox-jobs \
    && chmod 2770 /var/lib/sandbox-jobs

EXPOSE 8000

USER 10001:10002
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
