"""
Structured logging + CloudWatch Embedded Metric Format (EMF) helpers.

EMF lets a Lambda emit custom metrics by printing a specially-shaped
JSON line to stdout -- CloudWatch Logs automatically extracts it into
a real metric. No cloudwatch:PutMetricData IAM permission needed, and
no extra API call/latency per invocation.
"""
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("aviation_pipeline")
logger.setLevel(logging.INFO)

NAMESPACE = "AviationPipeline"


def log_event(level: str, message: str, **fields: Any) -> None:
    """Emit one structured JSON log line so CloudWatch Logs Insights can filter/aggregate on fields."""
    payload = {"message": message, "level": level, "timestamp": time.time(), **fields}
    line = json.dumps(payload, default=str)
    if level == "ERROR":
        logger.error(line)
    elif level == "WARNING":
        logger.warning(line)
    else:
        logger.info(line)


def emit_metric(
    metric_name: str,
    value: float,
    unit: str = "Count",
    dimensions: Optional[dict] = None,
    **extra_fields: Any,
) -> None:
    """Print an EMF-formatted log line so CloudWatch turns it into a real metric."""
    dimensions = dimensions or {}
    dimension_keys = list(dimensions.keys())
    document = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [dimension_keys] if dimension_keys else [[]],
                    "Metrics": [{"Name": metric_name, "Unit": unit}],
                }
            ],
        },
        metric_name: value,
        **dimensions,
        **extra_fields,
    }
    print(json.dumps(document, default=str))
