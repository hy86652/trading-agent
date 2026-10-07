# Container for the FastAPI agent (Task 9, deploy).
# Not built or run yet: Docker is not installed on the development machine.
FROM python:3.12-slim

WORKDIR /app

# requirements.txt is a Windows pip freeze: drop the BOM and pywin32, which only installs on Windows.
COPY requirements.txt ./
RUN sed -e '1s/^\xEF\xBB\xBF//' -e '/^pywin32==/d' requirements.txt > requirements.linux.txt \
    && pip install --no-cache-dir -r requirements.linux.txt \
    && rm requirements.linux.txt

COPY . .

# Secrets (.env) are not copied in; set GOOGLE_API_KEY and the LANGFUSE_* variables in the hosting platform.
# The checkpoint database lives in data/; on Render and HF Spaces it may not survive a restart (brief section 10).
EXPOSE 7860
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
