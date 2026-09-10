"""Run the Pocket Entertainment Catalog server."""

from .config import load_settings
from .server import run


if __name__ == "__main__":
    run(load_settings())

