FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# 书籍项目数据（gennovel.yaml / prompts / book.db）挂载在 /data
VOLUME /data
EXPOSE 13300

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:13300/healthz',timeout=3)"

CMD ["gennovel", "serve", "--project", "/data", "--host", "0.0.0.0", "--port", "13300"]
