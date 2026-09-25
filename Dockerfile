FROM python:3.13-slim

# Run the simulator as an unprivileged user; the code itself stays root-owned.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --chown=root:root simulate_prod.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
CMD ["python3", "simulate_prod.py"]
