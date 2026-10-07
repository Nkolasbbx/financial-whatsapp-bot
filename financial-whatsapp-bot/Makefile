# Usa los binarios del venv del proyecto si existe, para que los targets
# funcionen sin tener que activar el venv antes.
VENV_BIN := $(if $(wildcard venv/bin/arq),venv/bin/,)

run-server:
	$(VENV_BIN)uvicorn main:app --reload --port 8000

run-worker:
	$(VENV_BIN)arq worker.WorkerSettings

off-railway:
	npx -y @railway/cli@latest scale us-west=0 --service financial-whatsapp-bot
	npx -y @railway/cli@latest scale us-west=0 --service incredible-adventure

on-railway:
	npx -y @railway/cli@latest scale us-west=1 --service financial-whatsapp-bot
	npx -y @railway/cli@latest scale us-west=1 --service incredible-adventure