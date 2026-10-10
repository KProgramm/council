# server/app.py — stub. Owner: Krish (G4).
# Will expose: POST /reviews, WS /ws/{review_id}, GET /replay, GET / (serves web/).
from fastapi import FastAPI

app = FastAPI(title="PR Council")
