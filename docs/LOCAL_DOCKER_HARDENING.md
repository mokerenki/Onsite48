# Onsite48 Local Docker Hardening Guide

## 1. Apply the Update Safely

Extract this update over the existing Onsite48 project directory, but preserve the root `.env` file containing your secrets. Stop and rebuild the containers without removing their volumes:

```powershell
docker compose down
docker compose up --build -d
docker compose ps