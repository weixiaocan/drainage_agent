FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

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

COPY . .

RUN mkdir -p var/outputs var/workspace var/logs

EXPOSE 8000

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
