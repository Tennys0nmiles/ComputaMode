"""YAML config loader."""

import os
import yaml

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config.yaml"
)


def load_config(path=None):
    """Load and return the config dict from YAML."""
    path = path or DEFAULT_CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)
