FROM python:3.14-alpine

ARG VERSION=dev
ARG IMAGE_NAME=ghcr.io/jeancarlo13/fivesecondshack

LABEL org.opencontainers.image.title="Five Seconds Hack"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.url="https://github.com/jeancarlo13/FiveSecondsHack"
LABEL org.opencontainers.image.source="https://github.com/jeancarlo13/FiveSecondsHack"
LABEL org.opencontainers.image.ref.name="${IMAGE_NAME}:${VERSION}"

WORKDIR /app
RUN apk add --no-cache tzdata
ENV TZ=America/Chihuahua
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "python src/main.py --serve & while true; do python src/main.py; sleep 60; done"]
