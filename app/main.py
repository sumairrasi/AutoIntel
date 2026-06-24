from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Base
from typing import AsyncGenerator
from app.routers.file import router as file_router
from app.routers.ocr_doc import router as ocr_router
from app.routers.tokens import router as token_router
from app.routers.chat_doc import router as chat_router
from app.routers.feedback import router as feedback_router
from app.routers.llm_config import router as llm_router
from fastapi.middleware.cors import CORSMiddleware
from app.config.constant import EUREKA_SERVER,SERVICE_NAME,HOST,PORT,INSTANCE_ID
import time
import requests
import logging
import asyncio




app = FastAPI(title="Async Document API")
logger = logging.getLogger("uvicorn")



# Routers
app.include_router(file_router)
app.include_router(ocr_router)
app.include_router(token_router)
app.include_router(chat_router)
app.include_router(feedback_router)
app.include_router(llm_router)



def register_to_eureka():
    instance = {
        "instance": {
            "instanceId": f"{HOST}:{SERVICE_NAME}:{PORT}",
            "hostName": HOST,
            "app": SERVICE_NAME,
            "ipAddr": HOST,
            "status": "UP",
            "port": {"$": PORT, "@enabled": "true"},
            "dataCenterInfo": {
                "@class": "com.netflix.appinfo.InstanceInfo$DefaultDataCenterInfo",
                "name": "MyOwn"
            }
        }
    }

    url = f"{EUREKA_SERVER}/{SERVICE_NAME}"
    res = requests.post(url, json=instance, headers={"Content-Type": "application/json"})
    logger.info("Registered with Eureka: %s %s", res.status_code, res.text)


async def send_heartbeat():
    url = f"{EUREKA_SERVER}/{SERVICE_NAME}/{INSTANCE_ID}"
    while True:
        try:
            res = requests.put(url, headers={"Content-Type": "application/json"})
            logger.info("Heartbeat sent: %s", res.status_code)
        except Exception as e:
            logger.error("Heartbeat failed: %s", e)
        await asyncio.sleep(30)


@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    register_to_eureka()
    asyncio.create_task(send_heartbeat())
        




async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
        
        



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
