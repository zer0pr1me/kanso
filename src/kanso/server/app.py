from fastapi import FastAPI

app = FastAPI(title="kanso")


def main():
    import uvicorn

    uvicorn.run(app, port=8000)
