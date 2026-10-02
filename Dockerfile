FROM python:3.12

WORKDIR /app/src/backend

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY src/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src/backend/ .
RUN chmod +x /app/src/backend/entrypoint.sh

ARG RELEASE_SHA
LABEL org.opencontainers.image.revision=$RELEASE_SHA
LABEL ru.findme-photo.historical-adaface-contract="vector-only-v1"

ENTRYPOINT ["/app/src/backend/entrypoint.sh"]
