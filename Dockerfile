FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[kafka]"
ENV PYTHONUNBUFFERED=1
CMD ["coldchain", "api", "--host", "0.0.0.0", "--port", "8000"]
