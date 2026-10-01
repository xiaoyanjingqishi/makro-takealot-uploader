from fastapi import APIRouter
from .products import router as products_router
from .cleaner import router as cleaner_router
from .makro import router as makro_router
from .settings import router as settings_router
from .tasks import router as tasks_router
from .stores import router as stores_router
from .auth import router as auth_router
from .users import router as users_router
from .store_products import router as store_products_router
from .store_orders import router as store_orders_router
from .store_audits import router as store_audits_router
from .piggyback import router as piggyback_router
from .reprice import router as reprice_router
from .management import router as management_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(management_router)

api_router.include_router(products_router)
api_router.include_router(piggyback_router)
api_router.include_router(reprice_router)
api_router.include_router(store_products_router)
api_router.include_router(store_audits_router)
api_router.include_router(store_orders_router)
api_router.include_router(cleaner_router)
api_router.include_router(makro_router)
api_router.include_router(settings_router)
api_router.include_router(tasks_router)
api_router.include_router(stores_router)

