FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Sessions directory — will be bind-mounted as a volume in compose
RUN mkdir -p /app/sessions

# Run as non-root for security
RUN useradd -m -u 1000 yukhub && chown -R yukhub:yukhub /app
USER yukhub

EXPOSE 8000

CMD ["python", "-m", "app.main"]
