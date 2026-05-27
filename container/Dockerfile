FROM python:3.14-alpine
WORKDIR /app
RUN apk add --no-cache tzdata
ENV TZ=America/Chihuahua
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "python src/main.py --serve & while true; do python src/main.py; sleep 60; done"]
