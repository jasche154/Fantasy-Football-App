from fastapi import FastAPI #pydantic schemas natural fit for separating the data in from the data out
app = FastAPI()

@app.get("/")
def read_root():
    return{"message": "API is running"}