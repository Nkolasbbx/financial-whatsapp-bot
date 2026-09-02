run-server:
	uvicorn main:app --reload --port 8000

run-worker:
	arq worker.WorkerSettings

off-railway:
	npx -y @railway/cli@latest scale us-west=0 --service financial-whatsapp-bot
	npx -y @railway/cli@latest scale us-west=0 --service incredible-adventure

on-railway:
	npx -y @railway/cli@latest scale us-west=1 --service financial-whatsapp-bot
	npx -y @railway/cli@latest scale us-west=1 --service incredible-adventure