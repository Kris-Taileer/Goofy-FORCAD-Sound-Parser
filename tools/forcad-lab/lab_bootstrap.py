"""Use Valkey for the broker and JSON for local Celery messages."""
from kombu.utils.json import register_type

from lib import config, models
from lib.config import getters


def broker_url():
    redis = config.get_redis_config()
    redis.db = 3
    return redis.url


original_celery_config = getters.get_celery_config


def celery_config():
    result = original_celery_config()
    result.accept_content = ["json"]
    result.task_serializer = "json"
    result.result_serializer = "json"
    return result


for model in (models.Team, models.Task, models.CheckerVerdict):
    register_type(model, f"forcad:{model.__name__}", model.to_dict, model.from_dict)
for enum in (models.TaskStatus, models.Action):
    register_type(enum, f"forcad:{enum.__name__}", lambda value: value.value, enum)

config.get_broker_url = getters.get_broker_url = broker_url
config.get_celery_config = getters.get_celery_config = celery_config
