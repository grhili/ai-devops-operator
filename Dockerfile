# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM python:3.11-slim

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash operator

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /root/.local /home/operator/.local

# Copy source code
COPY src/ ./src/

# Set ownership
RUN chown -R operator:operator /app

# Switch to non-root user
USER operator

# Add local packages to PATH
ENV PATH=/home/operator/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

# Run operator
CMD ["python", "-u", "src/main.py"]
