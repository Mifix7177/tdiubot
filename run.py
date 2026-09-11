import asyncio
import logging
import uvicorn
from backend.app import app
from backend.bot import start_bot_polling
from backend.config import SERVER_HOST, SERVER_PORT, WEBHOOK_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tdiu_runner")

async def main():
    logger.info(f"Starting TDIU Bot Web Server on {SERVER_HOST}:{SERVER_PORT}")
    logger.info("Health / Uptime ping endpoint: GET / or GET /health")
    logger.info("Admin Portal available at: /admin")
    logger.info("Telegram Web Client available at: /")
    
    config = uvicorn.Config(
        app, 
        host=SERVER_HOST, 
        port=SERVER_PORT, 
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)
    
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Terminated by user.")
