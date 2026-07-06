from datetime import datetime

import structlog

def drop_column(key: str, value: object) -> str:
    return ""


def millisecond_timestamper(
    logger: structlog.typing.WrappedLogger,
    method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    now = datetime.now()
    base_time = now.strftime("%b %d %H:%M:%S")
    milliseconds = f"{now.microsecond // 1000:03d}"
    event_dict["timestamp"] = f"{base_time}.{milliseconds}"
    return event_dict


def make_kv_identifier_column(key: str, styles: structlog.dev.ColumnStyles) -> structlog.dev.Column:
    width = 16 - len(key)
    value_format = f"{{:>{width}}}"
    col = structlog.dev.Column(
        key,
        structlog.dev.KeyValueColumnFormatter(
            key_style=styles.kv_key,
            value_style=styles.kv_value,
            reset_style=styles.reset,
            value_repr=value_format.format,
            prefix="[",
            postfix="]",
            width=16 - len(key),
        ),
    )
    return col


def make_ssh_output_renderer(
    default_renderer: structlog.dev.ConsoleRenderer,
) -> structlog.dev.ConsoleRenderer:
    styles = default_renderer.get_default_column_styles(colors=True)
    col_timestamp = default_renderer.columns[1]
    col_level = default_renderer.columns[2]
    return structlog.dev.ConsoleRenderer(
        sort_keys=False,
        columns=[
            structlog.dev.Column("", drop_column),
            col_timestamp,
            col_level,
            structlog.dev.Column(
                "ssh_prefix",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=styles.bright,
                    reset_style=styles.reset,
                    value_repr=str,
                ),
            ),
            structlog.dev.Column(
                "stdout",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=styles.timestamp,
                    reset_style=styles.reset,
                    value_repr=str,
                ),
            ),
        ],
    )

def make_ssh_result_renderer(
    default_renderer: structlog.dev.ConsoleRenderer,
) -> structlog.dev.ConsoleRenderer:
    styles = default_renderer.get_default_column_styles(colors=True)
    col_timestamp = default_renderer.columns[1]
    col_level = default_renderer.columns[2]
    return structlog.dev.ConsoleRenderer(
        sort_keys=False,
        columns=[
            structlog.dev.Column("", drop_column),
            col_timestamp,
            col_level,
            structlog.dev.Column(
                "ssh_prefix",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=styles.bright,
                    reset_style=styles.reset,
                    value_repr=str,
                ),
            ),
            structlog.dev.Column(
                "returncode",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=None,
                    value_style=styles.level_warn,
                    reset_style=styles.reset,
                    value_repr=str,
                ),
            ),
            structlog.dev.Column(
                "ssh_error",
                structlog.dev.KeyValueColumnFormatter(
                    key_style=styles.kv_key,
                    value_style=styles.kv_value,
                    reset_style=styles.reset,
                    value_repr=str,
                ),
            ),
        ],
    )


def make_pretty_renderer_processor(left_identifier: bool = True) -> structlog.typing.Processor:
    pad_event_to = 0 if left_identifier else 40

    # Default renderer
    default_renderer = structlog.dev.ConsoleRenderer(colors=True, pad_event_to=pad_event_to, sort_keys=False)
    col_extras = default_renderer.columns[0]
    col_timestamp = default_renderer.columns[1]
    col_level = default_renderer.columns[2]
    col_event = default_renderer.columns[3]
    col_logger = default_renderer.columns[4]
    col_level.formatter.width = 5  # type: ignore
    col_logger.formatter.width = 17  # type: ignore

    # Make identified renderer
    def make_identified_renderer(identifier_column: structlog.dev.Column) -> structlog.dev.ConsoleRenderer:
        columns = (
            [col_timestamp, col_level, identifier_column, col_event, col_extras]
            if left_identifier
            else [col_timestamp, col_level, col_event, identifier_column, col_extras]
        )
        return structlog.dev.ConsoleRenderer(sort_keys=False, columns=columns)

    styles = default_renderer.get_default_column_styles(colors=True)
    identified_renderers = {
        "Pool": make_identified_renderer(make_kv_identifier_column("pool", styles)),
        "Host": make_identified_renderer(make_kv_identifier_column("host", styles)),
        "VM": make_identified_renderer(make_kv_identifier_column("vm", styles)),
        "Snapshot": make_identified_renderer(make_kv_identifier_column("snapshot", styles)),
        "VDI": make_identified_renderer(make_kv_identifier_column("vdi", styles)),
    }

    # Make ssh command renderer
    def make_identified_ssh_command_renderer(identifier_column: structlog.dev.Column) -> structlog.dev.ConsoleRenderer:
        catchall = structlog.dev.Column("", drop_column)
        command_column = structlog.dev.Column(
            "command",
            structlog.dev.KeyValueColumnFormatter(
                key_style=None,
                value_style=styles.level_warn,
                reset_style=styles.reset,
                value_repr=str,
            ),
        )
        return structlog.dev.ConsoleRenderer(
            sort_keys=False,
            columns=[catchall, col_timestamp, col_level, identifier_column, command_column],
        )

    ssh_command_identified_renderers = {
        "Pool": make_identified_ssh_command_renderer(make_kv_identifier_column("pool", styles)),
        "Host": make_identified_ssh_command_renderer(make_kv_identifier_column("host", styles)),
        "VM": make_identified_ssh_command_renderer(make_kv_identifier_column("vm", styles)),
    }
    default_ssh_command_identified_renderer = make_identified_ssh_command_renderer(
        make_kv_identifier_column("host", styles)
    )

    ssh_output_renderer = make_ssh_output_renderer(default_renderer)
    ssh_result_renderer = make_ssh_result_renderer(default_renderer)

    noise_keys = {
        "Pool": [],
        "Host": ["pool"],
        "VM": ["pool", "ip"],
        "Snapshot": ["pool"],
        "VDI": [],
    }
    info_noise_keys = {
        "Pool": [],
        "Host": [],
        "VM": ["host", "vm_uuid", "ip"],
        "Snapshot": ["host", "vm_uuid", "snapshot_uuid"],
        "VDI": ["vdi_uuid", "sr_uuid"],
    }

    def processor(
        logger: structlog.typing.WrappedLogger,
        method_name: str,
        event_dict: structlog.typing.EventDict,
    ) -> str:
        logger_name = event_dict.get("logger", None)

        # Prepare identifier
        if logger_name == "VM":
            ip = event_dict.get("ip")
            vm_uuid = event_dict.get("vm_uuid", "")
            event_dict["vm"] = ip if ip else vm_uuid[:8]
        elif logger_name == "Snapshot":
            snapshot_uuid = event_dict.get("snapshot_uuid", "")
            event_dict["snapshot"] = snapshot_uuid[:8]
        elif logger_name == "VDI":
            vdi_uuid = event_dict.get("vdi_uuid", "")
            event_dict["vdi"] = vdi_uuid[:8]

        # SSH command renderer
        if event_dict.pop("ssh_command", False):
            renderer = ssh_command_identified_renderers.get(
                logger_name,
                default_ssh_command_identified_renderer,
            )
            return renderer(logger, method_name, event_dict)

        # SSH output renderer
        if event_dict.pop("ssh_output", False):
            event_dict["ssh_prefix"] = ">"
            return ssh_output_renderer(logger, method_name, event_dict)

        # SSH output renderer
        if event_dict.pop("ssh_result", False):
            returncode = event_dict.get("returncode")
            if returncode is None:
                raise structlog.DropEvent
            event_dict["ssh_prefix"] = "$?"
            return ssh_result_renderer(logger, method_name, event_dict)

        # Default renderer
        logger_name = event_dict.get("logger", None)
        if logger_name not in identified_renderers:
            return default_renderer(logger, method_name, event_dict)

        # Remove noisy key-value pairs
        event_dict.pop("logger")
        for key in noise_keys[logger_name]:
            event_dict.pop(key, None)
        if event_dict.get("level") == "info":
            for key in info_noise_keys[logger_name]:
                event_dict.pop(key, None)

        # Pool / host / VM / snapshot renderer
        renderer = identified_renderers[logger_name]
        return renderer(logger, method_name, event_dict)

    return processor


def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            millisecond_timestamper,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            make_pretty_renderer_processor(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
