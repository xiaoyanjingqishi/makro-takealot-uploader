from fastapi import APIRouter
from .products import router as products_router
from .cleaner import router as cleaner_router
from .makro import router as makro_router
from .settings import router as settings_router
from .tasks import router as tasks_router
from .stores import router as stores_router

api_router = APIRouter()
api_router.include_router(products_router)
api_router.include_router(cleaner_router)
api_router.include_router(makro_router)
api_router.include_router(settings_router)
api_router.include_router(tasks_router)
api_router.include_router(stores_router)
