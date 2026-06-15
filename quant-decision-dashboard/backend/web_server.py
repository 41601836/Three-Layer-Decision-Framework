from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.decision import router as decision_router

app = FastAPI()

# CORS middleware to allow requests from the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the decision API router
app.include_router(decision_router, prefix="/api/decision", tags=["decision"])

@app.get("/")
async def read_root():
    return {"message": "Welcome to the Quant Decision Dashboard API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)