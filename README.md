# photo-prjct

## Deployment

- Copy `.env.example` to `.env` on the VM and fill in real values.
- Build and push the Docker image in GitHub Actions.
- Run `docker compose -f docker-compose.prod.yml up -d` on the VM.
- The web container runs migrations and `collectstatic` on startup.
