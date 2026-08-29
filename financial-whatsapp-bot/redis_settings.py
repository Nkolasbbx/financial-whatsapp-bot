from arq.connections import RedisSettings
from redis.exceptions import ConnectionError, TimeoutError

from config import REDIS_URL


def get_redis_settings() -> RedisSettings:
    """Construye la configuración de Redis usada por arq (worker y cola de jobs).

    RedisSettings.from_dsn() deja los timeouts por defecto (conn_timeout=1s,
    retry_on_timeout=False), pensados para un Redis local. Contra un Redis
    remoto sobre TLS (ej. Upstash) un blip de red normal supera ese margen y
    tumba el proceso entero (ver TimeoutError en _poll_iteration/finish_job).
    Se endurecen acá para que un timeout puntual se reintente en vez de matar
    el worker o el proceso web.
    """
    settings = RedisSettings.from_dsn(REDIS_URL)
    settings.conn_timeout = 5
    settings.retry_on_timeout = True
    settings.retry_on_error = [ConnectionError, TimeoutError]
    return settings
