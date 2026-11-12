OWNERS = (
    "listener",
    "source_parser",
    "pre_ai",
    "ai",
    "catalog",
    "search",
    "notifications",
    "identity",
    "control",
    "taxonomy",
    "content",
    "analytics",
)
BINDINGS = {o: [o + ".command.execute.v1"] for o in OWNERS}
for key, owners in {
    "raw.message.received": ("source_parser",),
    "raw.message.edited": ("source_parser",),
    "raw.message.deleted": ("source_parser", "catalog"),
    "source.album.changed": ("source_parser",),
    "parser.candidate.parsed": ("pre_ai",),
    "parser.candidate.rejected": ("control", "analytics"),
    "parser.batch.completed": ("catalog",),
    "candidate.prechecked": ("ai", "catalog"),
    "candidate.extracted": ("catalog",),
    "media.unavailable": ("catalog",),
    "media.ready": ("catalog",),
    "media.failed": ("catalog",),
    "catalog.listing.changed": (
        "pre_ai",
        "search",
        "notifications",
        "content",
        "analytics",
        "control",
    ),
    "catalog.listing.retracted": (
        "pre_ai",
        "search",
        "notifications",
        "content",
        "analytics",
        "control",
    ),
    "identity.user.changed": ("notifications",),
    "notifications.filter.activated": ("identity",),
    "notifications.delivery.changed": ("analytics", "control"),
    "content.publication.changed": ("analytics", "control"),
    "operation.completed": ("control",),
    "taxonomy.tag.changed": ("catalog", "notifications", "ai", "search", "content"),
    "control.flag.changed": (
        "listener",
        "source_parser",
        "pre_ai",
        "ai",
        "catalog",
        "search",
        "notifications",
        "identity",
        "taxonomy",
        "content",
        "analytics",
    ),
}.items():
    for owner in owners:
        BINDINGS[owner].append(key + ".v1")


def consumers(event):
    return [
        o for o in OWNERS if event["event_type"] + ".v" + str(event["event_version"]) in BINDINGS[o]
    ]
