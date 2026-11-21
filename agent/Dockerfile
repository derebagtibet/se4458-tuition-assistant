FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY tuition_agent ./tuition_agent
ENTRYPOINT ["python", "-m", "tuition_agent.cli"]
