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

def render_to_console(
    default_renderer: structlog.dev.ConsoleRenderer,
    pool_renderer: structlog.dev.ConsoleRenderer,
    host_renderer: structlog.dev.ConsoleRenderer,
    vm_renderer: structlog.dev.ConsoleRenderer,
    snapshot_renderer: structlog.dev.ConsoleRenderer,
    vdi_renderer: structlog.dev.ConsoleRenderer,
    ssh_output_renderer: structlog.dev.ConsoleRenderer,
) -> structlog.typing.Processor:
    dispatch = {
        "Pool": pool_renderer,
        "Host": host_renderer,
        "VM": vm_renderer,
        "Snapshot": snapshot_renderer,
        "VDI": vdi_renderer,
    }
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
        # SSH output renderer
        if event_dict.pop("ssh_output", False):
            event_dict["ssh_prefix"] = ">"
            return ssh_output_renderer(logger, method_name, event_dict)

        # Default renderer
        logger_name = event_dict.get("logger", None)
        if logger_name not in dispatch:
            return default_renderer(logger, method_name, event_dict)
        event_dict.pop("logger")

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

        # Remove noisy key-value pairs
        for key in noise_keys[logger_name]:
            event_dict.pop(key, None)
        if event_dict.get("level") == "info":
            for key in info_noise_keys[logger_name]:
                event_dict.pop(key, None)

        # Render
        renderer = dispatch[logger_name]
        return renderer(logger, method_name, event_dict)

    return processor


def configure_logging():
    styles = structlog.dev.ConsoleRenderer.get_default_column_styles(colors=True)

    default_renderer = structlog.dev.ConsoleRenderer(colors=True, pad_event_to=40, sort_keys=False)
    col_extras = default_renderer.columns[0]
    col_timestamp = default_renderer.columns[1]
    col_level = default_renderer.columns[2]
    col_event = default_renderer.columns[3]
    col_logger = default_renderer.columns[4]
    col_level.formatter.width = 5  # type: ignore
    col_logger.formatter.width = 17  # type: ignore

    def make_renderer(identifier_column: structlog.dev.Column) -> structlog.dev.ConsoleRenderer:
        return structlog.dev.ConsoleRenderer(
            sort_keys=False,
            columns=[col_timestamp, col_level, col_event, identifier_column, col_extras],
        )

    pool_renderer = make_renderer(make_kv_identifier_column("pool", styles))
    host_renderer = make_renderer(make_kv_identifier_column("host", styles))
    vm_renderer = make_renderer(make_kv_identifier_column("vm", styles))
    snapshot_renderer = make_renderer(make_kv_identifier_column("snapshot", styles))
    vdi_renderer = make_renderer(make_kv_identifier_column("vdi", styles))

    ssh_output_renderer = structlog.dev.ConsoleRenderer(
        sort_keys=False,
        columns=[
            col_timestamp,
            col_level,
            structlog.dev.Column("", drop_column),
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

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            millisecond_timestamper,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            render_to_console(
                default_renderer,
                pool_renderer,
                host_renderer,
                vm_renderer,
                snapshot_renderer,
                vdi_renderer,
                ssh_output_renderer,
            ),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
