"""Errors whose message can be shown to the tenant in the portal."""


class IngestionError(Exception):
    """A known problem with ONE document (missing file, no text, wrong type...).

    The worker stores str(error) as the document's error_message, which the portal
    shows to the tenant (spec FR-03). So the message must be short, in plain words,
    and contain no internal details (no paths, no stack traces).

    Any OTHER exception is unexpected: the worker logs its details and shows the
    tenant only a generic "Processing failed (<type>)".
    """
