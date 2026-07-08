"""Run the Gemini OpenAI-compatible API server."""

import uvicorn

from gemini_openai import config

if __name__ == "__main__":
    uvicorn.run("gemini_openai.server:app", host=config.HOST, port=config.PORT, log_level="info")
