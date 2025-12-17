FROM tobyxdd/hysteria:v2 AS hysteria-image
FROM jklolixxs/sing-box:latest AS sing-box-image

FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1

# Copy binaries from other images (cached)
COPY --from=hysteria-image /usr/local/bin/hysteria /usr/local/bin/hysteria
COPY --from=sing-box-image /usr/local/bin/sing-box /usr/local/bin/sing-box

# Create necessary directories
RUN mkdir -p /etc/init.d/ /usr/local/bin /usr/local/lib/xray

# Install Xray (cached layer - rarely changes)
RUN apk add --no-cache curl unzip && \
    XRAY_VERSION=$(curl -s https://api.github.com/repos/XTLS/Xray-core/releases/latest | grep '"tag_name":' | sed -E 's/.*"v([^"]+)".*/\1/') && \
    curl -L "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip" -o /tmp/xray.zip && \
    unzip -o /tmp/xray.zip -d /tmp/xray && \
    mv /tmp/xray/xray /usr/local/bin/xray && \
    chmod +x /usr/local/bin/xray && \
    mkdir -p /usr/local/lib/xray && \
    mv /tmp/xray/geoip.dat /tmp/xray/geosite.dat /usr/local/lib/xray/ 2>/dev/null || true && \
    rm -rf /tmp/xray /tmp/xray.zip && \
    apk del curl unzip

WORKDIR /app

# Copy only requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies (cached layer - changes only when requirements.txt changes)
RUN apk add --no-cache alpine-sdk libffi-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del -r alpine-sdk libffi-dev

# Copy application code (this layer changes often, but previous layers are cached)
COPY . .

CMD ["python3", "marznode.py"]