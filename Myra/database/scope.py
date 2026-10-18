"""
Per-bot identity + the global ("main") admin view.

The primary bot and every **cloned bot** share one MongoDB and one process. Each
document is tagged with a *scope* string:

    "main"        → the primary bot (``config.BOT_TOKEN``)
    "c<bot_id>"   → a clone, where ``<bot_id>`` is the clone's own Telegram id

The scope string is still the clone's identity (it keys the running-client
registry and the ``BotDB.clones`` documents), and a clone's own chat serving
stays isolated to its scope.

**The admin surface is global.** ``"main"`` is the *shared view*: queries made
with scope ``main`` match every document, whichever bot wrote it. That is what
makes ``/admin`` show identical data no matter which bot opened it — add two IDs
in one bot's panel and they appear in all of them. Only the main owner
(``config.OWNER_ID``) and the admins the owner added can use it.

Legacy rows written before scopes existed have no ``scope`` field at all; they
count as ``"main"`` (see :func:`scope_match` and :func:`owns_scope`).
"""

import contextvars
import functools

MAIN_SCOPE = "main"

_scope: contextvars.ContextVar = contextvars.ContextVar("myra_scope", default=MAIN_SCOPE)


def current_scope() -> str:
    """Scope of the update currently being handled (defaults to ``main``)."""
    try:
        return _scope.get() or MAIN_SCOPE
    except Exception:  # noqa: BLE001
        return MAIN_SCOPE


def set_scope(scope: str):
    """Set the scope for the current task; returns a token for :func:`reset_scope`."""
    return _scope.set(scope or MAIN_SCOPE)


def reset_scope(token) -> None:
    try:
        _scope.reset(token)
    except Exception:  # noqa: BLE001
        pass


def global_scope(func):
    """
    Decorator that runs a handler against the ONE shared database.

    Applied to every admin-surface handler so that a command or button press
    arriving through a clone still reads and writes the global (``main``) data:
    the panel therefore looks identical in every bot.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        token = set_scope(MAIN_SCOPE)
        try:
            return await func(*args, **kwargs)
        finally:
            reset_scope(token)

    return wrapper


def scope_match(scope: str = None) -> dict:
    """
    Query fragment selecting the documents that belong to a scope.

    ``main`` is the **global view** and matches every document (plus legacy
    rows without a ``scope`` field), so an admin panel opened in any bot sees
    the same shared data.
    """
    value = scope or current_scope()
    if value == MAIN_SCOPE:
        return {}
    return {"scope": value}


def scope_field(scope: str = None) -> dict:
    """Fields to store on a document so it belongs to the scope."""
    return {"scope": scope or current_scope()}


def owns_scope(doc_scope, scope: str = None) -> bool:
    """True when a document carrying ``doc_scope`` is visible to ``scope``."""
    value = scope or current_scope()
    if value == MAIN_SCOPE:
        return True
    return (doc_scope or MAIN_SCOPE) == value


def is_main(scope: str = None) -> bool:
    return (scope or current_scope()) == MAIN_SCOPE


def clone_scope(bot_id) -> str:
    """Scope string of a clone bot, from its Telegram id."""
    return f"c{bot_id}"


def bot_id_from_scope(scope: str):
    """Telegram id of a clone scope (``None`` for ``main``)."""
    scope = scope or current_scope()
    if scope == MAIN_SCOPE or not scope.startswith("c"):
        return None
    try:
        return int(scope[1:])
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "MAIN_SCOPE",
    "current_scope",
    "set_scope",
    "reset_scope",
    "global_scope",
    "scope_match",
    "scope_field",
    "owns_scope",
    "is_main",
    "clone_scope",
    "bot_id_from_scope",
]
