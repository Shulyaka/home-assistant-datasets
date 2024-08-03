"""Evaluate data collected from the home assistant conversation agent.

See `assist_collect` for the step to collect data. You can then run an offline
analysis of the model using the following command:

```bash
$ OUTPUT_DIR="output/$(date +"%Y-%m-%d")/"
$ home-assistant-datasets assist eval --model_output_dir=${OUTPUT_DIR}
```

Usage:
```
usage: home-assistant-datasets assist eval [-h] [--model_output_dir MODEL_OUTPUT_DIR] [--output_type {csv,yaml,report}]

options:
  -h, --help            show this help message and exit
  --model_output_dir MODEL_OUTPUT_DIR
                        Specifies the model output directory from `collect`.
  --output_type {csv,yaml,report}
                        Specifies the output type.
```

"""

import argparse
import logging
import pathlib
from typing import Any

import yaml
from mashumaro.mixins.yaml import EncodedData
from mashumaro.exceptions import MissingField

from homeassistant.components.conversation import trace

from .data_model import ModelOutput
from .eval_output import GOOD_LABEL, BAD_LABEL, create_writer, OutputType

__all__ = []

_LOGGER = logging.getLogger(__name__)


def find_llm_call(trace_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Gets the llm call from the conversation trace."""
    tool_call = next(
        iter(
            event
            for event in trace_events
            if event["event_type"]
            in (trace.ConversationTraceEventType.TOOL_CALL, "llm_tool_call")  # type: ignore[attr-defined]
        ),
        None,
    )
    if tool_call is None:
        return None

    data = tool_call["data"]
    return {
        "tool_name": data.get("tool_name"),
        "tool_args": data.get("tool_args"),
    }


def yaml_decoder(data: EncodedData) -> Any:
    return yaml.load(data, yaml.UnsafeLoader)


def create_arguments(args: argparse.ArgumentParser) -> None:
    """Get parsed passed in arguments."""
    args.add_argument(
        "--model_output_dir",
        type=str,
        help="Specifies the model output directory from `collect`.",
    )
    args.add_argument(
        "--output_type",
        type=OutputType,
        choices=OutputType,
        help="Specifies the output type.",
    )


def run(args: argparse.Namespace) -> int:
    """Run the command line action."""
    model_outputs = pathlib.Path(args.model_output_dir)
    writer = create_writer(args.output_type)
    writer.start()
    for model_output_file in model_outputs.glob("*/**/*.yaml"):
        stem = model_output_file.relative_to(model_outputs)
        filename = model_output_file.name[:-5]  # strip .yaml
        model_id = str(list(stem.parents)[0])
        task_prefix = filename.split("-")[0]

        try:
            output = ModelOutput.from_yaml(
                model_output_file.read_text(), decoder=yaml_decoder
            )
        except (yaml.error.YAMLError, ValueError, MissingField) as err:
            raise ValueError(
                f"Unable to parse model output file: {model_output_file}: {str(err)}"
            ) from err

        label = BAD_LABEL
        unexpected_states = output.context["unexpected_states"]
        if len(unexpected_states) == 0:
            # Success!
            label = GOOD_LABEL
            if "Sorry" in output.response:
                raise ValueError(
                    f"Incorrect expected states logic in {model_output_file}? Response said Sorry but no unexpected states: {output.task_id}"
                )
        writer.row(
            {
                "model_id": model_id,
                "category": output.category,
                "task_prefix": task_prefix,
                "label": label,
                "text": output.task["input_text"],
                "response": output.response,
                "tool_call": find_llm_call(
                    output.context.get("conversation_trace", {})
                ),
                "entity_diff": writer.diff(unexpected_states),
            }
        )

    writer.finish()

    return 0
