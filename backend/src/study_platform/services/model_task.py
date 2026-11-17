"""Pin connection choices for synchronous learning operations."""

from functools import wraps

from study_platform.services import model_connections


def learning_task(function):
    @wraps(function)
    def wrapped(db, *, owner_user_id, **kwargs):
        kb_id = kwargs.get("knowledge_base_id")
        with model_connections.task(owner_user_id, kb_id=kb_id):
            return function(db, owner_user_id=owner_user_id, **kwargs)

    return wrapped
