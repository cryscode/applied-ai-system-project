# Structured logging for PawPal+ scheduling decisions.

import logging

LOGGER_NAME = "pawpal.scheduler"

PROPOSAL = "PROPOSAL"
REJECTION = "REJECTION"
ADJUSTMENT = "ADJUSTMENT"


class DecisionFormatter(logging.Formatter):
    """Renders decision log records as human-readable one-liners.

    Non-decision records (anything without a `decision` attribute) fall back
    to the standard formatter so the logger stays usable for ad-hoc messages.
    """

    def format(self, record: logging.LogRecord) -> str:
        decision = getattr(record, "decision", None)
        if decision is None:
            return super().format(record)

        line = f"[{decision}] {getattr(record, 'task', '?')}"
        reason = getattr(record, "reason", "")
        if reason:
            line += f" -- {reason}"
        return line


def get_logger() -> logging.Logger:
    """Return the shared PawPal scheduler logger, configuring it on first use.

    Propagation is left on (the default) so pytest's `caplog` fixture, which
    attaches its capture handler to the root logger, still sees every record.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(DecisionFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def log_decision(logger: logging.Logger, decision: str, task: str, reason: str = "", **fields) -> None:
    """Emit a structured log record for one scheduling decision.

    `decision`, `task`, `reason`, and any extra `fields` are attached to the
    LogRecord (via `extra`) rather than baked into the message string, so
    tests can assert on them directly through `caplog.records` instead of
    parsing text.
    """
    logger.info(
        "%s: %s",
        decision,
        task,
        extra={"decision": decision, "task": task, "reason": reason, **fields},
    )
