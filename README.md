Clipvive (server monorepo)

Structure:
 - backend/    # FastAPI app, Dockerfile, requirements.txt
 - bot/        # bot service
 - worker/     # rq worker entrypoint
 - cleaner/    # periodic cleaner script
 - nginx/      # nginx site files
 - docker-compose.yml
 - migrations/
 - scripts/

Important: Do NOT commit .env, storage/ or logs/
Use .env.sample as template.
