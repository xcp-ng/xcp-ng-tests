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


def render_console_or_ssh_output(
    console_renderer: structlog.dev.ConsoleRenderer,
    ssh_output_renderer: structlog.dev.ConsoleRenderer,
) -> structlog.typing.Processor:
    def processor(
        logger: structlog.typing.WrappedLogger,
        method_name: str,
        event_dict: structlog.typing.EventDict,
    ) -> str:
        if event_dict.pop("ssh_output", False):
            event_dict["ssh_prefix"] = ">"
            return ssh_output_renderer(logger, method_name, event_dict)
        return console_renderer(logger, method_name, event_dict)

    return processor


def configure_logging():
    console_renderer = structlog.dev.ConsoleRenderer(colors=True, pad_event_to=40, sort_keys=False)
    logger_name_column = console_renderer.columns[-1]
    assert logger_name_column.key == "logger_name"
    console_renderer.columns[2].formatter.width = 5 # type: ignore
    logger_name_column.formatter.width = 8 # type: ignore
    styles = structlog.dev.ConsoleRenderer.get_default_column_styles(colors=True)
    ssh_output_renderer = structlog.dev.ConsoleRenderer(
        sort_keys=False,
        columns=[
            console_renderer.columns[1],
            console_renderer.columns[2],
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
            render_console_or_ssh_output(console_renderer, ssh_output_renderer),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
