"""Resolve the table arguments of tool calls."""

WORK_SCHEMA = "stg_ops"
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")


def qualify(name, schema=WORK_SCHEMA):
    """Return ``schema.table`` for a table name from a tool call.

    Names which already contain a schema are left alone.  In particular, an
    unqualified name must not be resolved against PostgreSQL's default
    ``public`` schema: this agent's working schema is ``stg_ops``.
    """
    name = name.strip()
    if "." in name:
        return name
    return "%s.%s" % (schema, name)


def schema_of(name):
    """Return the schema part of a qualified table name."""
    return name.split(".", 1)[0]


def resolve_args(tool, args):
    """Return the arguments of a tool call with its table argument qualified.

    Build a new argument dictionary rather than changing the caller's copy.
    """
    if tool in TABLE_TOOLS and isinstance(args.get("table"), str):
        args = dict(args, table=qualify(args["table"]))
    return args
