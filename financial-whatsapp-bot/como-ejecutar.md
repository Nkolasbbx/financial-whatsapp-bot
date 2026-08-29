# Cómo ejecutar

## 1. Levantar ngrok apuntando al puerto 8000

```bash
ngrok http 8000
```

## 2. Levantar el worker (Redis/arq)

```bash
make run-worker
```

Windows (sin `make`):

```bash
arq worker.WorkerSettings
```

## 3. Levantar el servidor (uvicorn)

```bash
make run-server
```

Windows (sin `make`):

```bash
uvicorn main:app --reload --port 8000
```
