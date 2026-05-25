FROM python:3.11-alpine
WORKDIR /app
RUN apk add --no-cache tzdata
ENV TZ=America/Chihuahua
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "python main.py --serve & while true; do python main.py; sleep 60; done"]