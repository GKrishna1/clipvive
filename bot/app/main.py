from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok", "service": "clipvive-bot"}

@app.get("/")
def index():
    return {"message": "Clipvive Bot is running"}

