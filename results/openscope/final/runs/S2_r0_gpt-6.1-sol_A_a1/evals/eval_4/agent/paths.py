"""Resolve the table arguments of tool calls."""

WORK_SCHEMA = "staging"
TABLE_TOOLS = ("db.read", "db.write", "db.drop_table", "db.grant")


def qualify(name, schema=WORK_SCHEMA):
    """Return schema.table, defaulting unqualified names to the work schema."""
    name = name.strip()
    if "." in name:
        return name
    return "%s.%s" % (schema, name)


def schema_of(name):
    """The schema part of a qualified table name."""
    return name.split(".", 1)[0]


def resolve_args(tool, args):
    """Return the arguments of a tool call with its table argument qualified."""
    if tool in TABLE_TOOLS and isinstance(args.get("table"), str):
        args = dict(args, table=qualify(args["table"]))
    return args
